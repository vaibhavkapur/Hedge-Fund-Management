"""High-level typed PlatformService orchestrating domain rules, persistence, audit, and replay."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .audit import DiagnosticTracker, verify_audit_chain
from .config import PlatformConfig
from .errors import (
    CutoffViolationError,
    DuplicateRequestError,
    InsufficientSharesError,
    InvalidStateTransitionError,
    NotFoundError,
    ReconciliationError,
    ValidationError,
)
from .models import (
    ApprovalDecision,
    ApprovalRecord,
    AuditEvent,
    CapitalFlowRequest,
    CashConfirmation,
    CashDirection,
    CashMatchStatus,
    DealingWindow,
    ExceptionCase,
    ExceptionStatus,
    ExceptionType,
    InvestorRecord,
    InvestorStatus,
    JournalEntryType,
    NAVRecord,
    NAVStatus,
    PricingCalculation,
    RequestStatus,
    RequestType,
    ShareJournalEntry,
    SharePosition,
    parse_decimal,
    parse_iso_date,
    parse_iso_datetime,
)
from .onboarding import (
    create_prospect_investor,
    evaluate_investor_onboarding,
    transition_investor_status,
)
from .pricing import (
    RestatementDelta,
    calculate_nav_restatement_delta,
    calculate_redemption,
    calculate_subscription,
    quantize_cash,
    quantize_nav,
    quantize_shares,
)
from .reconciliation import (
    ReconciliationReport,
    build_share_register,
    evaluate_cash_confirmation,
    verify_platform_reconciliation,
)
from .repository import Repository, SQLiteRepository
from .workflow import (
    check_dealing_window_cutoff,
    ensure_cancellable,
    ensure_request_approved,
    ensure_reviewer_separation,
    validate_exception_resolution,
    validate_request_transition,
)


def _utc_now(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now.astimezone(timezone.utc)


class PlatformService:
    """Typed service boundary isolating domain workflows from HTTP/CLI transport."""

    def __init__(
        self,
        config: PlatformConfig,
        repository: Optional[Repository] = None,
        diagnostics: Optional[DiagnosticTracker] = None,
    ) -> None:
        self.config = config
        self.repo: Repository = repository if repository is not None else SQLiteRepository(":memory:")
        self.diagnostics = diagnostics if diagnostics is not None else DiagnosticTracker()

    # -- Deterministic ID generators --

    def _next_audit_event_id(self) -> str:
        count = len(self.repo.list_audit_events()) + 1
        return f"EVT-{count:06d}"

    def _next_approval_id(self) -> str:
        count = len(self.repo.list_approvals()) + 1
        return f"APR-{count:06d}"

    def _next_exception_id(self) -> str:
        count = len(self.repo.list_exceptions()) + 1
        return f"EXC-{count:06d}"

    def _next_journal_id(self) -> str:
        count = len(self.repo.list_journal_entries()) + 1
        return f"JRN-{count:06d}"

    def _audit(
        self,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload: Dict[str, Any],
        now: datetime,
    ) -> AuditEvent:
        evt = self.repo.append_audit_event(
            event_id=self._next_audit_event_id(),
            timestamp=now,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
        )
        self.diagnostics.increment("audit_events_recorded")
        self.diagnostics.record_log(
            "INFO",
            action,
            actor_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            sequence=evt.sequence,
        )
        return evt

    # -- Investor & Mock Onboarding --

    def register_investor(
        self,
        *,
        investor_id: str,
        legal_name: str,
        investor_type: str,
        jurisdiction: str,
        tax_form_status: str,
        accredited: bool,
        bank_account_ref: str,
        identity_doc_ref: str,
        actor_id: str = "system",
        now: Optional[datetime] = None,
    ) -> InvestorRecord:
        ts = _utc_now(now)
        if self.repo.get_investor(investor_id) is not None:
            raise DuplicateRequestError(
                f"Investor '{investor_id}' already exists.",
                {"investor_id": investor_id},
            )
        inv = create_prospect_investor(
            investor_id=investor_id,
            legal_name=legal_name,
            investor_type=investor_type,
            jurisdiction=jurisdiction,
            tax_form_status=tax_form_status,
            accredited=accredited,
            bank_account_ref=bank_account_ref,
            identity_doc_ref=identity_doc_ref,
            created_at=ts,
        )
        self.repo.save_investor(inv)
        self.diagnostics.increment("investors_registered")
        self._audit(actor_id, "INVESTOR_REGISTERED", "INVESTOR", inv.investor_id, inv.to_dict(), ts)
        return inv

    def run_onboarding(
        self,
        investor_id: str,
        actor_id: str = "compliance_mock_engine",
        now: Optional[datetime] = None,
    ) -> InvestorRecord:
        ts = _utc_now(now)
        inv = self.repo.get_investor(investor_id)
        if inv is None:
            raise NotFoundError(f"Investor '{investor_id}' not found.", {"investor_id": investor_id})
        updated = evaluate_investor_onboarding(inv, self.config.workflow, ts)
        self.repo.save_investor(updated)
        self.diagnostics.increment(
            "onboarding_approved" if updated.status == InvestorStatus.KYC_APPROVED else "onboarding_rejected"
        )
        self._audit(
            actor_id,
            "INVESTOR_ONBOARDING_EVALUATED",
            "INVESTOR",
            updated.investor_id,
            {"status": updated.status.value, "checks": [c.to_dict() for c in updated.onboarding_checks]},
            ts,
        )
        return updated

    def activate_investor(
        self,
        investor_id: str,
        actor_id: str = "ops_admin",
        now: Optional[datetime] = None,
    ) -> InvestorRecord:
        ts = _utc_now(now)
        inv = self.repo.get_investor(investor_id)
        if inv is None:
            raise NotFoundError(f"Investor '{investor_id}' not found.", {"investor_id": investor_id})
        updated = transition_investor_status(inv, InvestorStatus.ACTIVE, ts)
        self.repo.save_investor(updated)
        self.diagnostics.increment("investors_activated")
        self._audit(
            actor_id,
            "INVESTOR_ACTIVATED",
            "INVESTOR",
            updated.investor_id,
            {"previous_status": inv.status.value, "status": updated.status.value, "version": updated.version},
            ts,
        )
        return updated

    def suspend_investor(
        self,
        investor_id: str,
        actor_id: str,
        reason: str,
        now: Optional[datetime] = None,
    ) -> InvestorRecord:
        ts = _utc_now(now)
        inv = self.repo.get_investor(investor_id)
        if inv is None:
            raise NotFoundError(f"Investor '{investor_id}' not found.", {"investor_id": investor_id})
        updated = transition_investor_status(inv, InvestorStatus.SUSPENDED, ts)
        self.repo.save_investor(updated)
        self._audit(
            actor_id,
            "INVESTOR_SUSPENDED",
            "INVESTOR",
            updated.investor_id,
            {"reason": reason, "status": updated.status.value, "version": updated.version},
            ts,
        )
        return updated

    # -- Dealing Windows & Approved NAV --

    def register_dealing_window(
        self,
        *,
        window_id: str,
        fund_id: str,
        share_class: str,
        dealing_date: Any,
        open_at: Any,
        cutoff_at: Any,
        cash_due_date: Any,
        settlement_date: Any,
        actor_id: str = "fund_admin",
        now: Optional[datetime] = None,
    ) -> DealingWindow:
        ts = _utc_now(now)
        self.config.get_share_class(fund_id, share_class)
        d_date = parse_iso_date(dealing_date, "dealing_date")
        o_dt = parse_iso_datetime(open_at, "open_at")
        c_dt = parse_iso_datetime(cutoff_at, "cutoff_at")
        due_d = parse_iso_date(cash_due_date, "cash_due_date")
        settle_d = parse_iso_date(settlement_date, "settlement_date")
        if o_dt >= c_dt:
            raise ValidationError("Dealing window open_at must be strictly before cutoff_at.")
        if due_d < d_date or settle_d < d_date:
            raise ValidationError("cash_due_date and settlement_date cannot precede dealing_date.")

        window = DealingWindow(
            window_id=window_id,
            fund_id=fund_id,
            share_class=share_class,
            dealing_date=d_date,
            open_at=o_dt,
            cutoff_at=c_dt,
            cash_due_date=due_d,
            settlement_date=settle_d,
        )
        self.repo.save_dealing_window(window)
        self._audit(actor_id, "DEALING_WINDOW_REGISTERED", "DEALING_WINDOW", window.window_id, window.to_dict(), ts)
        return window

    def publish_nav(
        self,
        *,
        nav_id: str,
        fund_id: str,
        share_class: str,
        dealing_date: Any,
        currency: str,
        nav_per_share: Any,
        approved_by: str,
        approved_at: Any,
        version: int = 1,
    ) -> NAVRecord:
        sc_cfg = self.config.get_share_class(fund_id, share_class)
        ccy = str(currency).upper()
        if ccy not in self.config.supported_currencies or ccy != sc_cfg.currency:
            raise ValidationError(
                f"NAV currency '{ccy}' is unsupported or mismatches share class currency '{sc_cfg.currency}'.",
                {"currency": ccy, "expected": sc_cfg.currency},
            )
        d_date = parse_iso_date(dealing_date, "dealing_date")
        nav_val = parse_decimal(nav_per_share, "nav_per_share")
        assert nav_val is not None
        if nav_val <= Decimal("0"):
            raise ValidationError("nav_per_share must be strictly positive.")
        nav_q = quantize_nav(nav_val, self.config.rounding)
        app_dt = parse_iso_datetime(approved_at, "approved_at")

        existing = self.repo.get_active_nav(fund_id, share_class, d_date)
        if existing is not None:
            raise DuplicateRequestError(
                f"An approved NAV ('{existing.nav_id}') already exists for {fund_id}/{share_class} on {d_date.isoformat()}; "
                "use restate_nav() to record a new version.",
                {"existing_nav_id": existing.nav_id},
            )

        nav = NAVRecord(
            nav_id=nav_id,
            fund_id=fund_id,
            share_class=share_class,
            dealing_date=d_date,
            currency=ccy,
            nav_per_share=nav_q,
            version=version,
            status=NAVStatus.APPROVED,
            approved_by=approved_by,
            approved_at=app_dt,
        )
        self.repo.save_nav(nav)
        self.diagnostics.increment("navs_published")
        self._audit(approved_by, "NAV_PUBLISHED", "NAV", nav.nav_id, nav.to_dict(), app_dt)
        return nav

    def restate_nav(
        self,
        *,
        new_nav_id: str,
        fund_id: str,
        share_class: str,
        dealing_date: Any,
        restated_nav_per_share: Any,
        reason: str,
        maker_id: str,
        checker_id: str,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Versioned NAV restatement with maker-checker enforcement and immutable journal adjustments."""
        ts = _utc_now(now)
        if not reason or not reason.strip():
            raise ValidationError("Restatement reason is required.")
        ensure_reviewer_separation(maker_id, checker_id, context="nav_restatement")

        sc_cfg = self.config.get_share_class(fund_id, share_class)
        d_date = parse_iso_date(dealing_date, "dealing_date")
        prior_nav = self.repo.get_active_nav(fund_id, share_class, d_date)
        if prior_nav is None:
            raise NotFoundError(
                f"No existing approved NAV found for {fund_id}/{share_class} on {d_date.isoformat()}."
            )

        new_val = parse_decimal(restated_nav_per_share, "restated_nav_per_share")
        assert new_val is not None
        if new_val <= Decimal("0"):
            raise ValidationError("restated_nav_per_share must be strictly positive.")
        new_nav_q = quantize_nav(new_val, self.config.rounding)
        if new_nav_q == prior_nav.nav_per_share:
            raise ValidationError("Restated NAV per share equals existing NAV per share.")

        superseded = replace(prior_nav, status=NAVStatus.SUPERSEDED, restatement_reason=reason)
        self.repo.save_nav(superseded)

        new_nav = NAVRecord(
            nav_id=new_nav_id,
            fund_id=fund_id,
            share_class=share_class,
            dealing_date=d_date,
            currency=prior_nav.currency,
            nav_per_share=new_nav_q,
            version=prior_nav.version + 1,
            status=NAVStatus.APPROVED,
            approved_by=checker_id,
            approved_at=ts,
            supersedes_nav_id=prior_nav.nav_id,
            restatement_reason=reason,
        )
        self.repo.save_nav(new_nav)

        approval = ApprovalRecord(
            approval_id=self._next_approval_id(),
            entity_type="NAV_RESTATEMENT",
            entity_id=new_nav.nav_id,
            maker_id=maker_id,
            checker_id=checker_id,
            decision=ApprovalDecision.APPROVED,
            reason=reason,
            decided_at=ts,
        )
        self.repo.save_approval(approval)
        self._audit(
            checker_id,
            "NAV_RESTATED",
            "NAV",
            new_nav.nav_id,
            {
                "prior_nav_id": prior_nav.nav_id,
                "prior_nav_per_share": str(prior_nav.nav_per_share),
                "new_nav_per_share": str(new_nav.nav_per_share),
                "version": new_nav.version,
                "maker_id": maker_id,
                "checker_id": checker_id,
                "reason": reason,
            },
            ts,
        )

        # Find impacted settled/restated requests referencing the prior NAV
        impacted_deltas: List[RestatementDelta] = []
        adjustment_entries: List[ShareJournalEntry] = []
        created_exceptions: List[ExceptionCase] = []

        for req in self.repo.list_requests():
            if (
                req.fund_id == fund_id
                and req.share_class == share_class
                and req.dealing_date == d_date
                and req.status in (RequestStatus.SETTLED, RequestStatus.RESTATED)
                and req.nav_id == prior_nav.nav_id
            ):
                assert req.gross_amount is not None
                assert req.fee_amount is not None
                assert req.net_cash is not None
                assert req.calculated_shares is not None
                delta = calculate_nav_restatement_delta(
                    request_id=req.request_id,
                    request_type=req.request_type,
                    original_nav_id=prior_nav.nav_id,
                    original_gross_cash=req.gross_amount,
                    original_fee=req.fee_amount,
                    original_net_cash=req.net_cash,
                    original_shares=req.calculated_shares,
                    restated_nav=new_nav,
                    share_class_cfg=sc_cfg,
                    rounding=self.config.rounding,
                )
                impacted_deltas.append(delta)

                prior_entries = self.repo.list_journal_entries(req.request_id)
                last_entry_id = prior_entries[-1].entry_id if prior_entries else None
                next_ver = (prior_entries[-1].version + 1) if prior_entries else 2

                # Record immutable adjustment journal entry (never mutate existing entries)
                adj_entry = ShareJournalEntry(
                    entry_id=self._next_journal_id(),
                    request_id=req.request_id,
                    investor_id=req.investor_id,
                    fund_id=req.fund_id,
                    share_class=req.share_class,
                    currency=req.currency,
                    dealing_date=req.dealing_date,
                    entry_type=JournalEntryType.NAV_RESTATEMENT_ADJUSTMENT,
                    share_delta=delta.share_delta,
                    gross_cash_delta=Decimal("0.00"),
                    fee_delta=delta.fee_delta,
                    net_cash_delta=delta.net_cash_delta,
                    nav_id=new_nav.nav_id,
                    nav_version=new_nav.version,
                    posted_at=ts,
                    posted_by=checker_id,
                    version=next_ver,
                    supersedes_entry_id=last_entry_id,
                )
                self.repo.append_journal_entry(adj_entry)
                adjustment_entries.append(adj_entry)

                updated_req = replace(
                    req,
                    status=RequestStatus.RESTATED,
                    version=req.version + 1,
                    nav_id=new_nav.nav_id,
                    nav_version=new_nav.version,
                    calculated_shares=delta.restated_shares,
                    fee_amount=delta.restated_fee,
                    net_cash=delta.restated_net_cash,
                )
                self.repo.save_request(updated_req)

                exc_case = ExceptionCase(
                    exception_id=self._next_exception_id(),
                    request_id=req.request_id,
                    exception_type=ExceptionType.NAV_RESTATEMENT,
                    status=ExceptionStatus.RESOLVED,
                    summary=f"NAV restated from {prior_nav.nav_per_share} to {new_nav.nav_per_share}; adjustment posted.",
                    created_by=maker_id,
                    created_at=ts,
                    details=delta.to_dict(),
                    reviewer_id=checker_id,
                    resolution_note=f"Approved restatement: {reason}",
                    resolved_at=ts,
                )
                self.repo.save_exception(exc_case)
                created_exceptions.append(exc_case)

                self._audit(
                    checker_id,
                    "REQUEST_RESTATED",
                    "REQUEST",
                    req.request_id,
                    {
                        "adjustment_entry_id": adj_entry.entry_id,
                        "delta": delta.to_dict(),
                        "new_request_version": updated_req.version,
                    },
                    ts,
                )

        self.diagnostics.increment("nav_restatements")
        return {
            "superseded_nav": superseded.to_dict(),
            "restated_nav": new_nav.to_dict(),
            "approval": approval.to_dict(),
            "impacted_count": len(impacted_deltas),
            "deltas": [d.to_dict() for d in impacted_deltas],
            "adjustment_entries": [e.to_dict() for e in adjustment_entries],
            "exceptions": [x.to_dict() for x in created_exceptions],
        }

    # -- Capital Flow Requests (Subscriptions & Redemptions) --

    def submit_request(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        investor_id: str,
        fund_id: str,
        share_class: str,
        request_type: str | RequestType,
        dealing_date: Any,
        currency: str,
        gross_amount: Any = None,
        requested_shares: Any = None,
        submitted_at: Any = None,
        maker_id: str = "ops_maker_01",
    ) -> CapitalFlowRequest:
        ts = parse_iso_datetime(submitted_at, "submitted_at") if submitted_at is not None else _utc_now(None)

        if not request_id or not request_id.strip():
            raise ValidationError("request_id is required.", {"field": "request_id"})
        if not idempotency_key or not idempotency_key.strip():
            raise ValidationError("idempotency_key is required.", {"field": "idempotency_key"})
        if not maker_id or not maker_id.strip():
            raise ValidationError("maker_id is required.", {"field": "maker_id"})

        # 1. Duplicate / Idempotency check
        existing_by_id = self.repo.get_request(request_id)
        existing_by_key = self.repo.get_request_by_idempotency_key(idempotency_key)
        if existing_by_id is not None or existing_by_key is not None:
            existing = existing_by_id or existing_by_key
            assert existing is not None
            self.diagnostics.increment("duplicate_requests_blocked")
            exc_case = ExceptionCase(
                exception_id=self._next_exception_id(),
                request_id=request_id,
                exception_type=ExceptionType.DUPLICATE_REQUEST,
                status=ExceptionStatus.OPEN,
                summary=f"Duplicate request blocked (idempotency_key={idempotency_key}, existing={existing.request_id}).",
                created_by=maker_id,
                created_at=ts,
                details={"existing_request_id": existing.request_id, "idempotency_key": idempotency_key},
            )
            self.repo.save_exception(exc_case)
            self._audit(
                maker_id,
                "DUPLICATE_REQUEST_REJECTED",
                "REQUEST",
                request_id,
                {"existing_request_id": existing.request_id, "idempotency_key": idempotency_key, "exception_id": exc_case.exception_id},
                ts,
            )
            raise DuplicateRequestError(
                f"Duplicate request detected for request_id='{request_id}' or idempotency_key='{idempotency_key}'.",
                {"existing_request_id": existing.request_id, "exception_id": exc_case.exception_id},
            )

        # 2. Validate fund/share class & currency
        sc_cfg = self.config.get_share_class(fund_id, share_class)
        ccy = str(currency).upper()
        if ccy not in self.config.supported_currencies:
            raise ValidationError(f"Unsupported currency '{ccy}'.", {"currency": ccy})
        if ccy != sc_cfg.currency:
            raise ValidationError(
                f"Request currency '{ccy}' does not match share class currency '{sc_cfg.currency}'.",
                {"currency": ccy, "share_class_currency": sc_cfg.currency},
            )

        try:
            req_type = request_type if isinstance(request_type, RequestType) else RequestType(str(request_type).upper())
        except ValueError as exc:
            raise ValidationError(
                f"Unsupported request_type '{request_type}'. Expected SUBSCRIPTION or REDEMPTION.",
                {"request_type": request_type},
            ) from exc

        d_date = parse_iso_date(dealing_date, "dealing_date")

        # 3. Validate investor exists and is ACTIVE
        investor = self.repo.get_investor(investor_id)
        if investor is None:
            raise NotFoundError(f"Investor '{investor_id}' not found.", {"investor_id": investor_id})
        if investor.status != InvestorStatus.ACTIVE:
            exc_case = ExceptionCase(
                exception_id=self._next_exception_id(),
                request_id=request_id,
                exception_type=ExceptionType.KYC_NOT_ACTIVE,
                status=ExceptionStatus.OPEN,
                summary=f"Investor '{investor_id}' is in status {investor.status.value}, not ACTIVE.",
                created_by=maker_id,
                created_at=ts,
                details={"investor_id": investor_id, "investor_status": investor.status.value},
            )
            self.repo.save_exception(exc_case)
            self._audit(maker_id, "REQUEST_BLOCKED_KYC", "REQUEST", request_id, exc_case.to_dict(), ts)
            raise ValidationError(
                f"Investor '{investor_id}' is not ACTIVE (current status: {investor.status.value}).",
                {"investor_status": investor.status.value, "exception_id": exc_case.exception_id},
            )

        # 4. Validate amounts / shares
        parsed_gross = parse_decimal(gross_amount, "gross_amount", allow_none=True)
        parsed_shares = parse_decimal(requested_shares, "requested_shares", allow_none=True)

        if req_type == RequestType.SUBSCRIPTION:
            if parsed_gross is None or parsed_shares is not None:
                raise ValidationError("Subscription requires gross_amount and must not specify requested_shares.")
            if parsed_gross <= Decimal("0"):
                raise ValidationError("Subscription gross_amount must be strictly positive.")
            parsed_gross = quantize_cash(parsed_gross, self.config.rounding)
            if parsed_gross < sc_cfg.min_subscription_amount:
                raise ValidationError(
                    f"Subscription amount {parsed_gross} is below minimum {sc_cfg.min_subscription_amount} {ccy}.",
                    {"gross_amount": str(parsed_gross), "min_subscription_amount": str(sc_cfg.min_subscription_amount)},
                )
        else:
            if (parsed_shares is None) == (parsed_gross is None):
                raise ValidationError("Redemption must specify exactly one of requested_shares or gross_amount.")
            if parsed_shares is not None:
                if parsed_shares <= Decimal("0"):
                    raise ValidationError("Redemption requested_shares must be strictly positive.")
                parsed_shares = quantize_shares(parsed_shares, self.config.rounding)
                if parsed_shares < sc_cfg.min_redemption_shares:
                    raise ValidationError(
                        f"Redemption shares {parsed_shares} below minimum {sc_cfg.min_redemption_shares}.",
                        {"requested_shares": str(parsed_shares), "min_redemption_shares": str(sc_cfg.min_redemption_shares)},
                    )
            else:
                assert parsed_gross is not None
                if parsed_gross <= Decimal("0"):
                    raise ValidationError("Redemption gross_amount must be strictly positive.")
                parsed_gross = quantize_cash(parsed_gross, self.config.rounding)

        # 5. Validate dealing window & cutoff
        window = self.repo.get_dealing_window(fund_id, share_class, d_date)
        if window is None:
            raise NotFoundError(
                f"No dealing window configured for {fund_id}/{share_class} on {d_date.isoformat()}.",
                {"fund_id": fund_id, "share_class": share_class, "dealing_date": d_date.isoformat()},
            )
        try:
            check_dealing_window_cutoff(ts, window)
        except CutoffViolationError as err:
            self.diagnostics.increment("cutoff_violations")
            exc_case = ExceptionCase(
                exception_id=self._next_exception_id(),
                request_id=request_id,
                exception_type=ExceptionType.CUTOFF_VIOLATION,
                status=ExceptionStatus.OPEN,
                summary=err.message,
                created_by=maker_id,
                created_at=ts,
                details=err.details,
            )
            self.repo.save_exception(exc_case)
            self._audit(
                maker_id,
                "CUTOFF_VIOLATION_RECORDED",
                "REQUEST",
                request_id,
                {"exception_id": exc_case.exception_id, **err.details},
                ts,
            )
            raise CutoffViolationError(
                err.message,
                {**err.details, "exception_id": exc_case.exception_id},
            ) from err

        # 6. For share-based redemptions, verify sufficient available shares
        if req_type == RequestType.REDEMPTION and parsed_shares is not None:
            register = self.get_share_register()
            pos = register.get((investor_id, fund_id, share_class, ccy))
            available = pos.available_shares if pos is not None else quantize_shares(Decimal("0"), self.config.rounding)
            if parsed_shares > available:
                self.diagnostics.increment("insufficient_shares_rejections")
                details = {
                    "investor_id": investor_id,
                    "requested_shares": str(parsed_shares),
                    "available_shares": str(available),
                }
                exc_case = ExceptionCase(
                    exception_id=self._next_exception_id(),
                    request_id=request_id,
                    exception_type=ExceptionType.INSUFFICIENT_SHARES,
                    status=ExceptionStatus.OPEN,
                    summary=f"Insufficient available shares: requested {parsed_shares}, available {available}.",
                    created_by=maker_id,
                    created_at=ts,
                    details=details,
                )
                self.repo.save_exception(exc_case)
                self._audit(
                    maker_id,
                    "INSUFFICIENT_SHARES_REJECTED",
                    "REQUEST",
                    request_id,
                    {"exception_id": exc_case.exception_id, **details},
                    ts,
                )
                raise InsufficientSharesError(
                    f"Investor '{investor_id}' has only {available} available shares; cannot redeem {parsed_shares}.",
                    {**details, "exception_id": exc_case.exception_id},
                )

        req = CapitalFlowRequest(
            request_id=request_id.strip(),
            idempotency_key=idempotency_key.strip(),
            investor_id=investor_id,
            fund_id=fund_id,
            share_class=share_class,
            request_type=req_type,
            dealing_date=d_date,
            currency=ccy,
            gross_amount=parsed_gross,
            requested_shares=parsed_shares,
            submitted_at=ts,
            maker_id=maker_id.strip(),
            status=RequestStatus.PENDING_APPROVAL,
            version=1,
        )
        self.repo.save_request(req)
        self.diagnostics.increment("requests_submitted")
        self._audit(maker_id, "REQUEST_SUBMITTED", "REQUEST", req.request_id, req.to_dict(), ts)
        return req

    def review_request(
        self,
        *,
        request_id: str,
        checker_id: str,
        decision: str | ApprovalDecision,
        reason: str = "Verified investor instruction and dealing terms.",
        now: Optional[datetime] = None,
    ) -> Tuple[CapitalFlowRequest, ApprovalRecord]:
        ts = _utc_now(now)
        req = self.repo.get_request(request_id)
        if req is None:
            raise NotFoundError(f"Request '{request_id}' not found.", {"request_id": request_id})

        dec = decision if isinstance(decision, ApprovalDecision) else ApprovalDecision(str(decision).upper())
        if self.config.workflow.require_maker_checker:
            ensure_reviewer_separation(req.maker_id, checker_id, context="request_review")

        target_status = RequestStatus.APPROVED if dec == ApprovalDecision.APPROVED else RequestStatus.REJECTED
        validate_request_transition(req.status, target_status)

        approval = ApprovalRecord(
            approval_id=self._next_approval_id(),
            entity_type="REQUEST",
            entity_id=req.request_id,
            maker_id=req.maker_id,
            checker_id=checker_id.strip(),
            decision=dec,
            reason=reason,
            decided_at=ts,
        )
        self.repo.save_approval(approval)

        updated = replace(
            req,
            status=target_status,
            version=req.version + 1,
            checker_id=checker_id.strip(),
            approved_at=ts if dec == ApprovalDecision.APPROVED else None,
        )
        self.repo.save_request(updated)
        self.diagnostics.increment("requests_approved" if dec == ApprovalDecision.APPROVED else "requests_rejected")
        self._audit(
            checker_id,
            f"REQUEST_{dec.value}",
            "REQUEST",
            req.request_id,
            {"approval_id": approval.approval_id, "maker_id": req.maker_id, "checker_id": checker_id, "reason": reason},
            ts,
        )
        return updated, approval

    def cancel_request(
        self,
        *,
        request_id: str,
        actor_id: str,
        reason: str,
        now: Optional[datetime] = None,
    ) -> CapitalFlowRequest:
        ts = _utc_now(now)
        if not reason or not reason.strip():
            raise ValidationError("Cancellation reason is required.")
        req = self.repo.get_request(request_id)
        if req is None:
            raise NotFoundError(f"Request '{request_id}' not found.", {"request_id": request_id})
        ensure_cancellable(req)
        validate_request_transition(req.status, RequestStatus.CANCELLED)

        updated = replace(
            req,
            status=RequestStatus.CANCELLED,
            version=req.version + 1,
            cancelled_by=actor_id,
            cancellation_reason=reason.strip(),
        )
        self.repo.save_request(updated)
        self.diagnostics.increment("requests_cancelled")
        self._audit(
            actor_id,
            "REQUEST_CANCELLED",
            "REQUEST",
            req.request_id,
            {"previous_status": req.status.value, "reason": reason.strip(), "version": updated.version},
            ts,
        )
        return updated

    def price_request(
        self,
        *,
        request_id: str,
        actor_id: str = "pricing_engine",
        now: Optional[datetime] = None,
    ) -> Tuple[CapitalFlowRequest, PricingCalculation]:
        ts = _utc_now(now)
        req = self.repo.get_request(request_id)
        if req is None:
            raise NotFoundError(f"Request '{request_id}' not found.", {"request_id": request_id})

        ensure_request_approved(req, require_maker_checker=self.config.workflow.require_maker_checker)
        validate_request_transition(req.status, RequestStatus.PRICED)

        nav = self.repo.get_active_nav(req.fund_id, req.share_class, req.dealing_date)
        if nav is None:
            raise NotFoundError(
                f"No approved NAV available for {req.fund_id}/{req.share_class} on {req.dealing_date.isoformat()}."
            )

        sc_cfg = self.config.get_share_class(req.fund_id, req.share_class)
        if req.request_type == RequestType.SUBSCRIPTION:
            assert req.gross_amount is not None
            calc = calculate_subscription(
                request_id=req.request_id,
                gross_cash=req.gross_amount,
                nav=nav,
                share_class_cfg=sc_cfg,
                rounding=self.config.rounding,
            )
        else:
            calc = calculate_redemption(
                request_id=req.request_id,
                requested_shares=req.requested_shares,
                requested_gross_cash=req.gross_amount,
                nav=nav,
                share_class_cfg=sc_cfg,
                rounding=self.config.rounding,
            )
            # Re-verify available shares for cash-based redemptions
            register = self.get_share_register()
            pos = register.get((req.investor_id, req.fund_id, req.share_class, req.currency))
            settled = pos.settled_shares if pos else Decimal("0")
            # Pending excludes this request if it was already locked
            other_pending = Decimal("0")
            for other in self.repo.list_requests():
                if (
                    other.request_id != req.request_id
                    and other.investor_id == req.investor_id
                    and other.fund_id == req.fund_id
                    and other.share_class == req.share_class
                    and other.request_type == RequestType.REDEMPTION
                    and other.status not in (RequestStatus.SETTLED, RequestStatus.RESTATED, RequestStatus.REJECTED, RequestStatus.CANCELLED)
                ):
                    amt = other.calculated_shares if other.calculated_shares is not None else other.requested_shares
                    if amt is not None:
                        other_pending += amt
            if calc.shares > (settled - other_pending):
                raise InsufficientSharesError(
                    f"Redemption requires {calc.shares} shares, but only {settled - other_pending} are available.",
                    {"required_shares": str(calc.shares), "available_shares": str(settled - other_pending)},
                )

        updated = replace(
            req,
            status=RequestStatus.PRICED,
            version=req.version + 1,
            nav_id=calc.nav_id,
            nav_version=calc.nav_version,
            gross_amount=calc.gross_cash,
            fee_amount=calc.fee_amount,
            net_cash=calc.net_cash,
            calculated_shares=calc.shares,
            rounding_residual=calc.rounding_residual,
        )
        self.repo.save_request(updated)
        self.diagnostics.increment("requests_priced")
        self._audit(actor_id, "REQUEST_PRICED", "REQUEST", req.request_id, calc.to_dict(), ts)
        return updated, calc

    def record_cash_confirmation(
        self,
        *,
        confirmation_id: str,
        request_id: str,
        direction: str | CashDirection,
        currency: str,
        amount: Any,
        value_date: Any,
        bank_reference: str,
        received_at: Any = None,
        actor_id: str = "treasury_feed",
    ) -> Tuple[CashConfirmation, Optional[ExceptionCase]]:
        ts = parse_iso_datetime(received_at, "received_at") if received_at is not None else _utc_now(None)
        if self.repo.get_cash_confirmation(confirmation_id) is not None:
            raise DuplicateRequestError(
                f"Cash confirmation '{confirmation_id}' has already been recorded.",
                {"confirmation_id": confirmation_id},
            )

        req = self.repo.get_request(request_id)
        if req is None:
            raise NotFoundError(f"Request '{request_id}' not found.", {"request_id": request_id})

        window = self.repo.get_dealing_window(req.fund_id, req.share_class, req.dealing_date)
        if window is None:
            raise NotFoundError(f"Dealing window not found for request '{request_id}'.")

        dir_enum = direction if isinstance(direction, CashDirection) else CashDirection(str(direction).upper())
        amt = parse_decimal(amount, "amount")
        assert amt is not None
        if amt <= Decimal("0"):
            raise ValidationError("Cash confirmation amount must be strictly positive.")
        amt_q = quantize_cash(amt, self.config.rounding)
        v_date = parse_iso_date(value_date, "value_date")

        raw_conf = CashConfirmation(
            confirmation_id=confirmation_id.strip(),
            request_id=request_id,
            direction=dir_enum,
            currency=str(currency).upper(),
            amount=amt_q,
            value_date=v_date,
            bank_reference=bank_reference.strip(),
            received_at=ts,
        )
        match = evaluate_cash_confirmation(req, raw_conf, window, self.config.rounding)
        final_conf = replace(raw_conf, match_status=match.status, discrepancy_amount=match.discrepancy_amount)
        self.repo.save_cash_confirmation(final_conf)
        self.diagnostics.increment(f"cash_{match.status.value.lower()}")

        exc_case: Optional[ExceptionCase] = None
        if match.status == CashMatchStatus.MATCHED:
            if req.status in (RequestStatus.APPROVED, RequestStatus.CASH_PENDING, RequestStatus.PRICED):
                # Keep PRICED if already priced (redemption or pre-priced subscription), else move to CASH_CONFIRMED
                if req.status != RequestStatus.PRICED:
                    validate_request_transition(req.status, RequestStatus.CASH_CONFIRMED)
                    self.repo.save_request(replace(req, status=RequestStatus.CASH_CONFIRMED, version=req.version + 1))
            self._audit(actor_id, "CASH_MATCHED", "CASH_CONFIRMATION", final_conf.confirmation_id, final_conf.to_dict(), ts)
        else:
            assert match.exception_type is not None
            exc_case = ExceptionCase(
                exception_id=self._next_exception_id(),
                request_id=req.request_id,
                exception_type=match.exception_type,
                status=ExceptionStatus.OPEN,
                summary=match.reason,
                created_by=actor_id,
                created_at=ts,
                details={
                    "confirmation_id": final_conf.confirmation_id,
                    "match_status": match.status.value,
                    "expected_amount": str(match.expected_amount),
                    "received_amount": str(final_conf.amount),
                    "discrepancy_amount": str(match.discrepancy_amount),
                    "value_date": final_conf.value_date.isoformat(),
                },
            )
            self.repo.save_exception(exc_case)
            self.diagnostics.increment("exceptions_opened")
            if req.status in (
                RequestStatus.APPROVED,
                RequestStatus.CASH_PENDING,
                RequestStatus.CASH_CONFIRMED,
                RequestStatus.PRICED,
            ):
                validate_request_transition(req.status, RequestStatus.EXCEPTION_REVIEW)
                self.repo.save_request(replace(req, status=RequestStatus.EXCEPTION_REVIEW, version=req.version + 1))
            self._audit(
                actor_id,
                f"CASH_EXCEPTION_{match.status.value}",
                "CASH_CONFIRMATION",
                final_conf.confirmation_id,
                {"confirmation": final_conf.to_dict(), "exception_id": exc_case.exception_id},
                ts,
            )

        return final_conf, exc_case

    def resolve_exception(
        self,
        *,
        exception_id: str,
        reviewer_id: str,
        target_status: str | ExceptionStatus,
        resolution_note: str,
        now: Optional[datetime] = None,
    ) -> ExceptionCase:
        ts = _utc_now(now)
        exc_case = self.repo.get_exception(exception_id)
        if exc_case is None:
            raise NotFoundError(f"Exception '{exception_id}' not found.", {"exception_id": exception_id})

        t_status = (
            target_status if isinstance(target_status, ExceptionStatus) else ExceptionStatus(str(target_status).upper())
        )
        validate_exception_resolution(exc_case, reviewer_id, t_status, resolution_note)

        updated = replace(
            exc_case,
            status=t_status,
            reviewer_id=reviewer_id.strip(),
            resolution_note=resolution_note.strip(),
            resolved_at=ts if t_status in (ExceptionStatus.RESOLVED, ExceptionStatus.REJECTED) else None,
        )
        self.repo.save_exception(updated)

        req = self.repo.get_request(exc_case.request_id)
        if req is not None and req.status == RequestStatus.EXCEPTION_REVIEW:
            if t_status == ExceptionStatus.RESOLVED and exc_case.exception_type == ExceptionType.DELAYED_CASH:
                next_st = RequestStatus.CASH_CONFIRMED
                validate_request_transition(req.status, next_st)
                self.repo.save_request(replace(req, status=next_st, version=req.version + 1))
            elif t_status == ExceptionStatus.REJECTED:
                validate_request_transition(req.status, RequestStatus.REJECTED)
                self.repo.save_request(replace(req, status=RequestStatus.REJECTED, version=req.version + 1))

        self.diagnostics.increment(f"exceptions_{t_status.value.lower()}")
        self._audit(
            reviewer_id,
            f"EXCEPTION_{t_status.value}",
            "EXCEPTION",
            updated.exception_id,
            {"request_id": updated.request_id, "reviewer_id": reviewer_id, "resolution_note": resolution_note},
            ts,
        )
        return updated

    def settle_request(
        self,
        *,
        request_id: str,
        actor_id: str = "settlement_engine",
        now: Optional[datetime] = None,
    ) -> Tuple[CapitalFlowRequest, ShareJournalEntry]:
        ts = _utc_now(now)
        req = self.repo.get_request(request_id)
        if req is None:
            raise NotFoundError(f"Request '{request_id}' not found.", {"request_id": request_id})

        # Gate 1: Strict maker-checker approval check (unapproved requests CANNOT settle)
        ensure_request_approved(req, require_maker_checker=self.config.workflow.require_maker_checker)

        # Gate 2: No unresolved exceptions on this request
        open_excs = [
            e
            for e in self.repo.list_exceptions(req.request_id)
            if e.status in (ExceptionStatus.OPEN, ExceptionStatus.UNDER_REVIEW)
        ]
        if open_excs:
            raise ReconciliationError(
                f"Request '{req.request_id}' has {len(open_excs)} unresolved exception(s) and cannot settle.",
                {"open_exception_ids": [e.exception_id for e in open_excs]},
            )

        # Gate 3: Must be priced
        if req.nav_id is None or req.calculated_shares is None or req.net_cash is None or req.fee_amount is None or req.gross_amount is None:
            raise InvalidStateTransitionError(
                f"Request '{req.request_id}' must be priced before settlement.",
                {"request_id": req.request_id, "status": req.status.value},
            )

        # Gate 4: Must have reconciled cash confirmation (either MATCHED, or DELAYED_CASH with resolved exception)
        expected_dir = CashDirection.INBOUND if req.request_type == RequestType.SUBSCRIPTION else CashDirection.OUTBOUND
        expected_cash = req.gross_amount if req.request_type == RequestType.SUBSCRIPTION else req.net_cash
        confs = [c for c in self.repo.list_cash_confirmations(req.request_id) if c.direction == expected_dir]
        eligible_confs = [
            c
            for c in confs
            if c.match_status == CashMatchStatus.MATCHED
            or (c.match_status == CashMatchStatus.DELAYED_CASH and not open_excs)
        ]
        confirmed_total = quantize_cash(sum((c.amount for c in eligible_confs), Decimal("0.00")), self.config.rounding)
        if confirmed_total != expected_cash:
            raise ReconciliationError(
                f"Cannot settle request '{req.request_id}': confirmed {expected_dir.value} cash {confirmed_total} != expected {expected_cash}.",
                {"confirmed_cash": str(confirmed_total), "expected_cash": str(expected_cash)},
            )

        validate_request_transition(req.status, RequestStatus.SETTLED)

        # Gate 5: For redemptions, double check settled share balance cannot go negative
        if req.request_type == RequestType.REDEMPTION:
            register = self.get_share_register()
            pos = register.get((req.investor_id, req.fund_id, req.share_class, req.currency))
            settled_shares = pos.settled_shares if pos else Decimal("0")
            if req.calculated_shares > settled_shares:
                raise InsufficientSharesError(
                    f"Settlement aborted: investor '{req.investor_id}' settled shares {settled_shares} < {req.calculated_shares}.",
                    {"settled_shares": str(settled_shares), "required_shares": str(req.calculated_shares)},
                )
            entry_type = JournalEntryType.REDEMPTION_BURN
            share_delta = -req.calculated_shares
            gross_delta = -req.gross_amount
            net_delta = -req.net_cash
        else:
            entry_type = JournalEntryType.SUBSCRIPTION_ISSUE
            share_delta = req.calculated_shares
            gross_delta = req.gross_amount
            net_delta = req.net_cash

        entry = ShareJournalEntry(
            entry_id=self._next_journal_id(),
            request_id=req.request_id,
            investor_id=req.investor_id,
            fund_id=req.fund_id,
            share_class=req.share_class,
            currency=req.currency,
            dealing_date=req.dealing_date,
            entry_type=entry_type,
            share_delta=share_delta,
            gross_cash_delta=gross_delta,
            fee_delta=req.fee_amount,
            net_cash_delta=net_delta,
            nav_id=req.nav_id,
            nav_version=req.nav_version or 1,
            posted_at=ts,
            posted_by=actor_id,
            version=1,
        )
        self.repo.append_journal_entry(entry)

        settled_req = replace(req, status=RequestStatus.SETTLED, version=req.version + 1)
        self.repo.save_request(settled_req)
        self.diagnostics.increment("requests_settled")
        self._audit(
            actor_id,
            "REQUEST_SETTLED",
            "REQUEST",
            req.request_id,
            {"journal_entry_id": entry.entry_id, "share_delta": str(entry.share_delta), "net_cash_delta": str(entry.net_cash_delta)},
            ts,
        )
        return settled_req, entry

    # -- Reporting, Share Register, Reconciliation & Reproducibility --

    def get_share_register(self) -> Dict[Tuple[str, str, str, str], SharePosition]:
        return build_share_register(
            self.repo.list_journal_entries(),
            self.repo.list_requests(),
            self.config.rounding,
        )

    def verify_reconciliation(self) -> ReconciliationReport:
        self.diagnostics.increment("reconciliation_runs")
        positions = self.get_share_register()
        return verify_platform_reconciliation(
            requests=self.repo.list_requests(),
            journal_entries=self.repo.list_journal_entries(),
            cash_confirmations=self.repo.list_cash_confirmations(),
            positions=positions,
            rounding=self.config.rounding,
        )

    def verify_audit_trail(self) -> Tuple[bool, List[str]]:
        return verify_audit_chain(self.repo.list_audit_events())

    def export_state_snapshot(self) -> Dict[str, Any]:
        """Export deterministic logical snapshot and SHA-256 digest for replay verification."""
        positions = [p.to_dict() for p in self.get_share_register().values()]
        reconciliation = self.verify_reconciliation().to_dict()
        audit_ok, audit_errors = self.verify_audit_trail()
        snapshot = {
            "config_hash": self.config.config_hash,
            "investors": [i.to_dict() for i in self.repo.list_investors()],
            "dealing_windows": [w.to_dict() for w in self.repo.list_dealing_windows()],
            "nav_records": [n.to_dict() for n in self.repo.list_navs()],
            "requests": [r.to_dict() for r in self.repo.list_requests()],
            "approvals": [a.to_dict() for a in self.repo.list_approvals()],
            "cash_confirmations": [c.to_dict() for c in self.repo.list_cash_confirmations()],
            "exceptions": [e.to_dict() for e in self.repo.list_exceptions()],
            "share_journal": [j.to_dict() for j in self.repo.list_journal_entries()],
            "share_register": positions,
            "reconciliation": reconciliation,
            "audit_chain_valid": audit_ok,
            "audit_chain_errors": audit_errors,
            "audit_event_count": len(self.repo.list_audit_events()),
        }
        canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
        snapshot["logical_state_hash"] = hashlib.sha256(canonical).hexdigest()
        return snapshot
