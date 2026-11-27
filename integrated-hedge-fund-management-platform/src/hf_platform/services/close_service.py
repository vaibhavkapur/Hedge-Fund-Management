"""Eight-stage deterministic daily close orchestrator with explicit failure gates and sign-off."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hf_platform.adapters.interfaces import (
    BrokerFeedAdapter,
    ExecutionFeedAdapter,
    MarketDataAdapter,
)
from hf_platform.config import PlatformConfig
from hf_platform.domain import (
    BreakType,
    CloseStatus,
    CloseStepResult,
    CloseStepStatus,
    EventType,
    MarketMark,
    Severity,
)
from hf_platform.domain.workflow import DailyCloseRun
from hf_platform.errors import CloseGateError, ExternalServiceError, NotFoundError
from hf_platform.services.audit_service import AuditService
from hf_platform.services.auth_service import AuthService
from hf_platform.services.exception_service import ExceptionService
from hf_platform.services.ledger_service import LedgerService
from hf_platform.services.master_service import MasterDataService
from hf_platform.services.portfolio_risk_service import PortfolioRiskService
from hf_platform.services.reconciliation_service import ReconciliationService
from hf_platform.storage.repository import StorageRepository


class CloseService:
    """Orchestrates the end-of-day close checklist with hard gates on missing marks, breaks, outages, and sign-off."""

    def __init__(
        self,
        *,
        store: StorageRepository,
        master: MasterDataService,
        ledger: LedgerService,
        portfolio: PortfolioRiskService,
        recon: ReconciliationService,
        exceptions: ExceptionService,
        auth: AuthService,
        audit: AuditService,
        broker_adapter: BrokerFeedAdapter,
        execution_adapter: ExecutionFeedAdapter,
        market_data_adapter: MarketDataAdapter,
        config: PlatformConfig,
    ) -> None:
        self._store = store
        self._master = master
        self._ledger = ledger
        self._portfolio = portfolio
        self._recon = recon
        self._exceptions = exceptions
        self._auth = auth
        self._audit = audit
        self._broker = broker_adapter
        self._execution = execution_adapter
        self._market_data = market_data_adapter
        self._config = config

    def run_daily_close(self, *, fund_id: str, business_date: date, actor_id: str) -> DailyCloseRun:
        """Execute ordered daily close checklist. Never silently closes on missing data, breaks, or outage."""
        user = self._auth.authorize(
            actor_id=actor_id,
            action="close.run",
            entity_type="daily_close",
            entity_id=f"{fund_id}:{business_date.isoformat()}",
            fund_id=fund_id,
        )
        self._master.get_fund(fund_id)
        prior_runs = self._store.list_close_runs(fund_id, business_date)
        attempt = len(prior_runs) + 1
        close_run_id = f"CLOSE_{fund_id}_{business_date.strftime('%Y%m%d')}_A{attempt}"
        started_at = datetime.now(UTC)

        self._audit.emit_event(
            event_type=EventType.CLOSE_STARTED,
            fund_id=fund_id,
            correlation_id=close_run_id,
            payload={"close_run_id": close_run_id, "business_date": business_date.isoformat(), "attempt": attempt},
        )

        steps: list[CloseStepResult] = []
        late_fill_ids: list[str] = []
        blocking_exc_ids: list[str] = []
        reconciliation_id: str | None = None
        snapshot_id: str | None = None

        cutoff_utc = datetime(
            business_date.year,
            business_date.month,
            business_date.day,
            self._config.close_controls.cutoff_utc_hour,
            self._config.close_controls.cutoff_utc_minute,
            tzinfo=UTC,
        )

        # Ensure opening capital exists in ledger
        self._ledger.ensure_opening_capital(fund_id, actor_id=user.user_id)

        # STEP 1: Trade & Fill Ingestion + Late Fill Cutoff Control
        s1_start = datetime.now(UTC)
        try:
            raw_fills = self._execution.fetch_fills(fund_id, business_date)
        except ExternalServiceError as exc:
            return self._abort_on_outage(
                close_run_id=close_run_id,
                fund_id=fund_id,
                business_date=business_date,
                attempt=attempt,
                actor_id=user.user_id,
                initiated_at=started_at,
                steps=steps,
                step_order=1,
                step_name="ingest_fills",
                step_started=s1_start,
                error=exc,
            )

        on_time_count = 0
        for fill in raw_fills:
            is_late = fill.received_at_utc > cutoff_utc
            if is_late:
                deferred_date = business_date + timedelta(days=1)
                late_fill = fill.model_copy(
                    update={"business_date": business_date, "is_late": True, "deferred_to_date": deferred_date}
                )
                self._store.save_fill(late_fill)
                late_fill_ids.append(late_fill.fill_id)
                exc_obj = self._exceptions.open_exception(
                    fund_id=fund_id,
                    business_date=business_date,
                    category=BreakType.LATE_FILL,
                    severity=Severity.HIGH,
                    title=f"Late fill after EOD cutoff: {late_fill.fill_id}",
                    description=(
                        f"Fill {late_fill.fill_id} received at {late_fill.received_at_utc.isoformat()} "
                        f"after cutoff {cutoff_utc.isoformat()}; deferred to {deferred_date.isoformat()} pending review."
                    ),
                    source_ref=f"LATE_FILL:{late_fill.fill_id}",
                    actor_id=user.user_id,
                    financial_impact_usd=late_fill.gross_notional_local,
                )
                self._audit.emit_event(
                    event_type=EventType.LATE_FILL_DETECTED,
                    fund_id=fund_id,
                    correlation_id=close_run_id,
                    payload={
                        "fill_id": late_fill.fill_id,
                        "received_at_utc": late_fill.received_at_utc.isoformat(),
                        "cutoff_utc": cutoff_utc.isoformat(),
                        "deferred_to_date": deferred_date.isoformat(),
                        "exception_id": exc_obj.exception_id,
                    },
                )
            else:
                clean_fill = fill.model_copy(update={"business_date": business_date, "is_late": False})
                self._store.save_fill(clean_fill)
                self._ledger.post_fill(clean_fill, business_date, actor_id=user.user_id)
                on_time_count += 1

        steps.append(
            CloseStepResult(
                step_name="ingest_fills",
                order=1,
                status=CloseStepStatus.PASSED,
                message=f"Ingested {on_time_count} on-time fills; quarantined {len(late_fill_ids)} late fill(s) after {cutoff_utc.strftime('%H:%M')}Z cutoff",
                started_at_utc=s1_start,
                completed_at_utc=datetime.now(UTC),
                details={"on_time_fills": on_time_count, "late_fill_ids": late_fill_ids},
            )
        )

        # STEP 2: Market Data (EOD Marks & FX) Validation
        s2_start = datetime.now(UTC)
        try:
            marks = self._market_data.fetch_marks(business_date)
            fx_rates = self._market_data.fetch_fx_rates(business_date)
        except ExternalServiceError as exc:
            return self._abort_on_outage(
                close_run_id=close_run_id,
                fund_id=fund_id,
                business_date=business_date,
                attempt=attempt,
                actor_id=user.user_id,
                initiated_at=started_at,
                steps=steps,
                step_order=2,
                step_name="validate_market_data",
                step_started=s2_start,
                error=exc,
            )

        mark_map, fx_map, md_issues = self._portfolio.validate_marks_and_fx(
            fund_id=fund_id,
            business_date=business_date,
            marks=marks,
            fx_rates=fx_rates,
        )
        if md_issues and self._config.close_controls.require_zero_missing_marks:
            for issue in md_issues:
                cat = BreakType.STALE_MARK if issue["type"] == "STALE_MARK" else BreakType.MISSING_MARK
                exc_obj = self._exceptions.open_exception(
                    fund_id=fund_id,
                    business_date=business_date,
                    category=cat,
                    severity=Severity.CRITICAL,
                    title=f"{issue['type']}: {issue['symbol']}",
                    description=issue["reason"],
                    source_ref=f"MD:{business_date.isoformat()}:{issue['instrument_id']}",
                    actor_id=user.user_id,
                )
                blocking_exc_ids.append(exc_obj.exception_id)
            steps.append(
                CloseStepResult(
                    step_name="validate_market_data",
                    order=2,
                    status=CloseStepStatus.BLOCKED,
                    message=f"Blocked: {len(md_issues)} missing or stale market data input(s)",
                    started_at_utc=s2_start,
                    completed_at_utc=datetime.now(UTC),
                    details={"issues": md_issues, "exception_ids": blocking_exc_ids},
                )
            )
            return self._finalize_blocked(
                close_run_id=close_run_id,
                fund_id=fund_id,
                business_date=business_date,
                attempt=attempt,
                actor_id=user.user_id,
                initiated_at=started_at,
                steps=steps,
                blocking_exc_ids=blocking_exc_ids,
                late_fill_ids=late_fill_ids,
                reason=f"Missing or stale market marks ({len(md_issues)} issue(s))",
            )

        steps.append(
            CloseStepResult(
                step_name="validate_market_data",
                order=2,
                status=CloseStepStatus.PASSED,
                message=f"Validated {len(mark_map)} marks and {len(fx_map)} FX rates",
                started_at_utc=s2_start,
                completed_at_utc=datetime.now(UTC),
                details={"mark_count": len(mark_map), "fx_currencies": sorted(c.value for c in fx_map)},
            )
        )

        # STEP 3: Trial Balance Verification
        s3_start = datetime.now(UTC)
        tb = self._ledger.compute_trial_balance(fund_id, business_date)
        if not tb.is_balanced:
            steps.append(
                CloseStepResult(
                    step_name="verify_trial_balance",
                    order=3,
                    status=CloseStepStatus.FAILED,
                    message="Trial balance is out of balance",
                    started_at_utc=s3_start,
                    completed_at_utc=datetime.now(UTC),
                    details={"totals_by_currency": {k: {ik: str(iv) for ik, iv in v.items()} for k, v in tb.totals_by_currency.items()}},
                )
            )
            return self._finalize_blocked(
                close_run_id=close_run_id,
                fund_id=fund_id,
                business_date=business_date,
                attempt=attempt,
                actor_id=user.user_id,
                initiated_at=started_at,
                steps=steps,
                blocking_exc_ids=blocking_exc_ids,
                late_fill_ids=late_fill_ids,
                reason="General ledger trial balance failed integrity check",
            )
        steps.append(
            CloseStepResult(
                step_name="verify_trial_balance",
                order=3,
                status=CloseStepStatus.PASSED,
                message=f"Trial balance verified across {tb.entry_count} journal entries",
                started_at_utc=s3_start,
                completed_at_utc=datetime.now(UTC),
                details={"entry_count": tb.entry_count, "currencies": sorted(tb.totals_by_currency.keys())},
            )
        )

        # STEP 4: Multi-Way Prime Broker / Custodian Reconciliation
        s4_start = datetime.now(UTC)
        try:
            ext_positions = self._broker.fetch_external_positions(fund_id, business_date)
            ext_cash = self._broker.fetch_external_cash(fund_id, business_date)
            ext_fills = self._broker.fetch_external_fills(fund_id, business_date)
        except ExternalServiceError as exc:
            return self._abort_on_outage(
                close_run_id=close_run_id,
                fund_id=fund_id,
                business_date=business_date,
                attempt=attempt,
                actor_id=user.user_id,
                initiated_at=started_at,
                steps=steps,
                step_order=4,
                step_name="reconcile_external_feeds",
                step_started=s4_start,
                error=exc,
            )

        recon_res = self._recon.reconcile(
            fund_id=fund_id,
            business_date=business_date,
            external_positions=ext_positions,
            external_cash=ext_cash,
            external_fills=ext_fills,
            marks=mark_map,
            fx_map=fx_map,
            actor_id=user.user_id,
        )
        reconciliation_id = recon_res.reconciliation_id
        for brk in recon_res.breaks:
            self._exceptions.open_exception(
                fund_id=fund_id,
                business_date=business_date,
                category=brk.break_type,
                severity=brk.severity,
                title=f"{brk.break_type.value}: {brk.account_id}",
                description=brk.description,
                source_ref=f"RECON:{business_date.isoformat()}:{brk.account_id}:{brk.break_type.value}:{brk.instrument_id or brk.currency or brk.broker_ref}",
                actor_id=user.user_id,
                financial_impact_usd=brk.financial_impact_usd,
            )

        steps.append(
            CloseStepResult(
                step_name="reconcile_external_feeds",
                order=4,
                status=CloseStepStatus.PASSED if recon_res.is_clean else CloseStepStatus.BLOCKED,
                message=(
                    f"Clean reconciliation ({recon_res.positions_checked} pos, {recon_res.cash_balances_checked} cash, {recon_res.fills_checked} fills)"
                    if recon_res.is_clean
                    else f"Detected {len(recon_res.breaks)} break(s) totaling ${recon_res.total_break_impact_usd} USD"
                ),
                started_at_utc=s4_start,
                completed_at_utc=datetime.now(UTC),
                details={
                    "reconciliation_id": recon_res.reconciliation_id,
                    "break_count": len(recon_res.breaks),
                    "total_break_impact_usd": str(recon_res.total_break_impact_usd),
                },
            )
        )

        # STEP 5: Consolidated Portfolio Valuation, Risk, & Ledger NAV Reconciliation
        s5_start = datetime.now(UTC)
        snapshot = self._portfolio.build_snapshot(
            fund_id=fund_id,
            business_date=business_date,
            marks=marks,
            fx_rates=fx_rates,
            actor_id=user.user_id,
            post_ledger_eod=True,
        )
        snapshot_id = snapshot.snapshot_id

        # Re-verify trial balance after EOD MTM and fee accrual postings
        post_tb = self._ledger.compute_trial_balance(fund_id, business_date)
        nav_reconciled = snapshot.nav_reconciliation_diff_usd == Decimal("0.00") and post_tb.is_balanced

        for breach in snapshot.risk_metrics.limit_breaches:
            self._exceptions.open_exception(
                fund_id=fund_id,
                business_date=business_date,
                category=BreakType.RISK_LIMIT_BREACH,
                severity=Severity(breach.severity),
                title=f"Risk Limit Breach: {breach.limit_name} ({breach.dimension})",
                description=f"Observed {breach.observed_value} exceeds limit {breach.limit_value}",
                source_ref=f"RISK:{business_date.isoformat()}:{breach.limit_name}:{breach.dimension}",
                actor_id=user.user_id,
            )

        steps.append(
            CloseStepResult(
                step_name="compute_portfolio_and_risk",
                order=5,
                status=CloseStepStatus.PASSED if nav_reconciled else CloseStepStatus.FAILED,
                message=(
                    f"NAV=${snapshot.nav_usd} (diff=${snapshot.nav_reconciliation_diff_usd}); "
                    f"Gross Lev={snapshot.risk_metrics.gross_leverage}x; VaR95={snapshot.risk_metrics.daily_var_95_pct_nav}%"
                ),
                started_at_utc=s5_start,
                completed_at_utc=datetime.now(UTC),
                details={
                    "snapshot_id": snapshot.snapshot_id,
                    "nav_usd": str(snapshot.nav_usd),
                    "ledger_nav_usd": str(snapshot.ledger_nav_usd),
                    "nav_diff_usd": str(snapshot.nav_reconciliation_diff_usd),
                    "risk_breaches": len(snapshot.risk_metrics.limit_breaches),
                },
            )
        )

        # STEP 6: Exception & Break Gate Check
        s6_start = datetime.now(UTC)
        unresolved = self._exceptions.list_blocking_exceptions(fund_id, business_date)
        blocking_exc_ids = [e.exception_id for e in unresolved]
        if unresolved and self._config.close_controls.require_zero_unresolved_critical_breaks:
            steps.append(
                CloseStepResult(
                    step_name="check_exception_gates",
                    order=6,
                    status=CloseStepStatus.BLOCKED,
                    message=f"Close blocked by {len(unresolved)} unresolved HIGH/CRITICAL exception(s)",
                    started_at_utc=s6_start,
                    completed_at_utc=datetime.now(UTC),
                    details={"blocking_exception_ids": blocking_exc_ids},
                )
            )
            return self._finalize_blocked(
                close_run_id=close_run_id,
                fund_id=fund_id,
                business_date=business_date,
                attempt=attempt,
                actor_id=user.user_id,
                initiated_at=started_at,
                steps=steps,
                blocking_exc_ids=blocking_exc_ids,
                late_fill_ids=late_fill_ids,
                reconciliation_id=reconciliation_id,
                snapshot_id=snapshot_id,
                reason=f"{len(unresolved)} unresolved material exception(s) block snapshot freeze",
            )

        steps.append(
            CloseStepResult(
                step_name="check_exception_gates",
                order=6,
                status=CloseStepStatus.PASSED,
                message="Zero unresolved HIGH/CRITICAL exceptions",
                started_at_utc=s6_start,
                completed_at_utc=datetime.now(UTC),
                details={"blocking_exception_ids": []},
            )
        )

        # STEP 7: Freeze Portfolio Snapshot
        s7_start = datetime.now(UTC)
        frozen_snap = self._portfolio.freeze_snapshot(snapshot.snapshot_id, actor_id=user.user_id)
        steps.append(
            CloseStepResult(
                step_name="freeze_snapshot",
                order=7,
                status=CloseStepStatus.PASSED,
                message=f"Frozen immutable snapshot {frozen_snap.snapshot_id} (sha256={frozen_snap.content_hash[:12]}...)",
                started_at_utc=s7_start,
                completed_at_utc=datetime.now(UTC),
                details={"snapshot_id": frozen_snap.snapshot_id, "content_hash": frozen_snap.content_hash},
            )
        )

        # STEP 8: Pending Dual-Control Sign-Off
        s8_start = datetime.now(UTC)
        steps.append(
            CloseStepResult(
                step_name="dual_control_signoff",
                order=8,
                status=CloseStepStatus.PENDING,
                message="Awaiting independent Controller / Compliance Officer sign-off",
                started_at_utc=s8_start,
                completed_at_utc=datetime.now(UTC),
                details={"preparer_id": user.user_id},
            )
        )

        run = DailyCloseRun(
            close_run_id=close_run_id,
            fund_id=fund_id,
            business_date=business_date,
            attempt=attempt,
            status=CloseStatus.PENDING_SIGNOFF,
            initiated_by=user.user_id,
            initiated_at_utc=started_at,
            completed_at_utc=datetime.now(UTC),
            steps=steps,
            reconciliation_id=reconciliation_id,
            snapshot_id=frozen_snap.snapshot_id,
            blocking_exception_ids=[],
            late_fill_ids=late_fill_ids,
            signoff_preparer_id=user.user_id,
        )
        self._store.save_close_run(run)
        self._audit.record(
            actor_id=user.user_id,
            actor_role=user.role,
            action="close.run_ready_for_signoff",
            entity_type="daily_close",
            entity_id=close_run_id,
            details={"snapshot_id": frozen_snap.snapshot_id, "nav_usd": str(frozen_snap.nav_usd)},
        )
        return run

    def sign_off_close(self, *, close_run_id: str, actor_id: str) -> DailyCloseRun:
        """Apply independent dual-control sign-off to complete the daily close."""
        run = self._store.get_close_run(close_run_id)
        if run is None:
            raise NotFoundError(f"Close run not found: {close_run_id}")
        if run.status != CloseStatus.PENDING_SIGNOFF:
            raise CloseGateError(
                f"Close run {close_run_id} is in status {run.status.value} and cannot be signed off",
                details={"status": run.status.value, "blocking_exception_ids": run.blocking_exception_ids},
            )
        approver = self._auth.authorize(
            actor_id=actor_id,
            action="close.signoff",
            entity_type="daily_close",
            entity_id=close_run_id,
            maker_id=run.signoff_preparer_id,
            fund_id=run.fund_id,
        )
        now = datetime.now(UTC)
        updated_steps = [
            s.model_copy(
                update={
                    "status": CloseStepStatus.PASSED,
                    "message": f"Signed off by {approver.user_id} ({approver.role.value})",
                    "completed_at_utc": now,
                    "details": {"preparer_id": run.signoff_preparer_id, "approver_id": approver.user_id},
                }
            )
            if s.step_name == "dual_control_signoff"
            else s
            for s in run.steps
        ]
        completed = run.model_copy(
            update={
                "status": CloseStatus.CLOSED,
                "steps": updated_steps,
                "signoff_approver_id": approver.user_id,
                "signoff_at_utc": now,
                "completed_at_utc": now,
            }
        )
        self._store.save_close_run(completed)
        self._audit.record(
            actor_id=approver.user_id,
            actor_role=approver.role,
            action="close.signoff",
            entity_type="daily_close",
            entity_id=close_run_id,
            details={"preparer_id": run.signoff_preparer_id, "snapshot_id": run.snapshot_id},
        )
        self._audit.emit_event(
            event_type=EventType.CLOSE_SIGNED_OFF,
            fund_id=run.fund_id,
            correlation_id=close_run_id,
            payload={"close_run_id": close_run_id, "snapshot_id": run.snapshot_id, "approver_id": approver.user_id},
        )
        return completed

    def apply_manual_mark_override(
        self,
        *,
        fund_id: str,
        business_date: date,
        instrument_id: str,
        price: Decimal,
        currency_code: str,
        proposer_id: str,
        approver_id: str,
        justification: str,
    ) -> MarketMark:
        """Apply a dual-control approved manual price override when market data is missing or stale."""
        proposer = self._auth.authorize(
            actor_id=proposer_id,
            action="mark.override_propose",
            entity_type="market_mark",
            entity_id=f"{business_date.isoformat()}:{instrument_id}",
            fund_id=fund_id,
        )
        approver = self._auth.authorize(
            actor_id=approver_id,
            action="mark.override_approve",
            entity_type="market_mark",
            entity_id=f"{business_date.isoformat()}:{instrument_id}",
            maker_id=proposer.user_id,
            fund_id=fund_id,
        )
        now = datetime.now(UTC)
        mark = MarketMark(
            instrument_id=instrument_id,
            price=price,
            currency=currency_code,
            source=f"MANUAL_OVERRIDE:{proposer.user_id}",
            as_of_utc=datetime(
                business_date.year,
                business_date.month,
                business_date.day,
                self._config.close_controls.cutoff_utc_hour,
                self._config.close_controls.cutoff_utc_minute,
                tzinfo=UTC,
            ),
            available_at_utc=now,
            is_manual_override=True,
            override_approved_by=approver.user_id,
        )
        self._store.save_manual_mark(business_date, mark)
        self._audit.record(
            actor_id=approver.user_id,
            actor_role=approver.role,
            action="mark.override_approve",
            entity_type="market_mark",
            entity_id=f"{business_date.isoformat()}:{instrument_id}",
            details={
                "proposer_id": proposer.user_id,
                "approver_id": approver.user_id,
                "price": str(price),
                "justification": justification,
            },
        )
        # Auto-resolve any open missing/stale mark exception for this instrument with maker-checker trace
        source_ref = f"MD:{business_date.isoformat()}:{instrument_id}"
        for exc in self._store.list_exceptions(fund_id=fund_id, business_date=business_date):
            if exc.source_ref == source_ref and exc.status.value not in ("RESOLVED", "WAIVED"):
                self._exceptions.propose_resolution(
                    exception_id=exc.exception_id,
                    resolution_notes=f"Manual mark override @ {price}: {justification}",
                    actor_id=proposer.user_id,
                )
                from hf_platform.domain import ApprovalAction

                self._exceptions.decide_exception(
                    exception_id=exc.exception_id,
                    action=ApprovalAction.APPROVE,
                    decision_notes=f"Approved manual mark override @ {price}",
                    actor_id=approver.user_id,
                )
        return mark

    def _finalize_blocked(
        self,
        *,
        close_run_id: str,
        fund_id: str,
        business_date: date,
        attempt: int,
        actor_id: str,
        initiated_at: datetime,
        steps: list[CloseStepResult],
        blocking_exc_ids: list[str],
        late_fill_ids: list[str],
        reason: str,
        reconciliation_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> DailyCloseRun:
        run = DailyCloseRun(
            close_run_id=close_run_id,
            fund_id=fund_id,
            business_date=business_date,
            attempt=attempt,
            status=CloseStatus.BLOCKED,
            initiated_by=actor_id,
            initiated_at_utc=initiated_at,
            completed_at_utc=datetime.now(UTC),
            steps=steps,
            reconciliation_id=reconciliation_id,
            snapshot_id=snapshot_id,
            blocking_exception_ids=blocking_exc_ids,
            late_fill_ids=late_fill_ids,
            failure_reason=reason,
        )
        self._store.save_close_run(run)
        user = self._store.get_user(actor_id)
        self._audit.record(
            actor_id=actor_id,
            actor_role=user.role if user else None,
            action="close.blocked",
            entity_type="daily_close",
            entity_id=close_run_id,
            outcome="FAILED",
            details={"reason": reason, "blocking_exception_ids": blocking_exc_ids},
        )
        self._audit.emit_event(
            event_type=EventType.CLOSE_BLOCKED,
            fund_id=fund_id,
            correlation_id=close_run_id,
            payload={"close_run_id": close_run_id, "reason": reason, "blocking_exception_ids": blocking_exc_ids},
        )
        return run

    def _abort_on_outage(
        self,
        *,
        close_run_id: str,
        fund_id: str,
        business_date: date,
        attempt: int,
        actor_id: str,
        initiated_at: datetime,
        steps: list[CloseStepResult],
        step_order: int,
        step_name: str,
        step_started: datetime,
        error: ExternalServiceError,
    ) -> DailyCloseRun:
        exc_obj = self._exceptions.open_exception(
            fund_id=fund_id,
            business_date=business_date,
            category=BreakType.SERVICE_OUTAGE,
            severity=Severity.CRITICAL,
            title=f"External Service Outage: {error.code}",
            description=error.message,
            source_ref=f"OUTAGE:{business_date.isoformat()}:{error.code}:{uuid.uuid4().hex[:4]}",
            actor_id=actor_id,
        )
        steps.append(
            CloseStepResult(
                step_name=step_name,
                order=step_order,
                status=CloseStepStatus.DEGRADED,
                message=f"Safe halt on service outage ({error.code}): {error.message}",
                started_at_utc=step_started,
                completed_at_utc=datetime.now(UTC),
                details={"error_code": error.code, "exception_id": exc_obj.exception_id},
            )
        )
        run = DailyCloseRun(
            close_run_id=close_run_id,
            fund_id=fund_id,
            business_date=business_date,
            attempt=attempt,
            status=CloseStatus.DEGRADED_OUTAGE,
            initiated_by=actor_id,
            initiated_at_utc=initiated_at,
            completed_at_utc=datetime.now(UTC),
            steps=steps,
            blocking_exception_ids=[exc_obj.exception_id],
            failure_reason=f"Safe fallback triggered due to external outage: {error.code} - {error.message}",
        )
        self._store.save_close_run(run)
        user = self._store.get_user(actor_id)
        self._audit.record(
            actor_id=actor_id,
            actor_role=user.role if user else None,
            action="close.degraded_outage",
            entity_type="daily_close",
            entity_id=close_run_id,
            outcome="FAILED",
            details={"error_code": error.code, "message": error.message, "exception_id": exc_obj.exception_id},
        )
        self._audit.emit_event(
            event_type=EventType.SERVICE_OUTAGE_DETECTED,
            fund_id=fund_id,
            correlation_id=close_run_id,
            payload={"error_code": error.code, "message": error.message, "exception_id": exc_obj.exception_id},
        )
        self._audit.emit_event(
            event_type=EventType.CLOSE_DEGRADED,
            fund_id=fund_id,
            correlation_id=close_run_id,
            payload={"close_run_id": close_run_id, "error_code": error.code},
        )
        return run
