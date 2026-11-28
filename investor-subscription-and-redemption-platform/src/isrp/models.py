"""Domain entities, value types, enums, and serialization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .errors import ValidationError


def parse_decimal(value: Any, field_name: str, *, allow_none: bool = False) -> Optional[Decimal]:
    """Parse strict Decimal from string, int, or Decimal (rejecting floats and booleans)."""
    if value is None:
        if allow_none:
            return None
        raise ValidationError(f"Field '{field_name}' is required.", {"field": field_name})
    if isinstance(value, bool) or isinstance(value, float):
        raise ValidationError(
            f"Field '{field_name}' must be a decimal string, int, or Decimal (floats are disallowed).",
            {"field": field_name, "type": type(value).__name__},
        )
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except Exception as exc:
        raise ValidationError(
            f"Invalid decimal format for '{field_name}': {value!r}.",
            {"field": field_name, "value": value},
        ) from exc
    if not dec.is_finite():
        raise ValidationError(f"Non-finite decimal value for '{field_name}'.", {"field": field_name})
    return dec


def parse_iso_date(value: Any, field_name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValidationError(f"Field '{field_name}' must be an ISO YYYY-MM-DD date string.", {"field": field_name})
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"Invalid ISO date for '{field_name}': {value!r}.", {"field": field_name}) from exc


def parse_iso_datetime(value: Any, field_name: str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValidationError(
                f"Invalid ISO-8601 timestamp for '{field_name}': {value!r}.",
                {"field": field_name},
            ) from exc
    else:
        raise ValidationError(f"Field '{field_name}' must be an ISO-8601 UTC timestamp string.", {"field": field_name})
    if dt.tzinfo is None:
        raise ValidationError(
            f"Timestamp '{field_name}' must include explicit timezone offset (e.g. 'Z' or '+00:00').",
            {"field": field_name},
        )
    return dt.astimezone(timezone.utc)


def format_iso_datetime(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class InvestorStatus(str, Enum):
    PROSPECT = "PROSPECT"
    ONBOARDING_PENDING = "ONBOARDING_PENDING"
    KYC_APPROVED = "KYC_APPROVED"
    KYC_REJECTED = "KYC_REJECTED"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class RequestType(str, Enum):
    SUBSCRIPTION = "SUBSCRIPTION"
    REDEMPTION = "REDEMPTION"


class RequestStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    VALIDATED = "VALIDATED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXCEPTION_REVIEW = "EXCEPTION_REVIEW"
    CASH_PENDING = "CASH_PENDING"
    CASH_CONFIRMED = "CASH_CONFIRMED"
    PRICED = "PRICED"
    SETTLED = "SETTLED"
    RESTATED = "RESTATED"


class NAVStatus(str, Enum):
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


class CashDirection(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class CashMatchStatus(str, Enum):
    MATCHED = "MATCHED"
    DELAYED_CASH = "DELAYED_CASH"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    UNMATCHED = "UNMATCHED"


class ApprovalDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ExceptionType(str, Enum):
    CUTOFF_VIOLATION = "CUTOFF_VIOLATION"
    DELAYED_CASH = "DELAYED_CASH"
    CASH_MISMATCH = "CASH_MISMATCH"
    INSUFFICIENT_SHARES = "INSUFFICIENT_SHARES"
    KYC_NOT_ACTIVE = "KYC_NOT_ACTIVE"
    NAV_RESTATEMENT = "NAV_RESTATEMENT"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"


class ExceptionStatus(str, Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


class JournalEntryType(str, Enum):
    SUBSCRIPTION_ISSUE = "SUBSCRIPTION_ISSUE"
    REDEMPTION_BURN = "REDEMPTION_BURN"
    NAV_RESTATEMENT_ADJUSTMENT = "NAV_RESTATEMENT_ADJUSTMENT"


@dataclass(frozen=True)
class OnboardingCheckResult:
    check_name: str
    passed: bool
    reason: str
    mock_disclaimer: str = "SYNTHETIC_MOCK_CHECK_NOT_LEGAL_COMPLIANCE"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_name": self.check_name,
            "passed": self.passed,
            "reason": self.reason,
            "mock_disclaimer": self.mock_disclaimer,
        }


@dataclass(frozen=True)
class InvestorRecord:
    investor_id: str
    legal_name: str
    investor_type: str
    jurisdiction: str
    tax_form_status: str
    accredited: bool
    bank_account_ref: str
    identity_doc_ref: str
    status: InvestorStatus
    version: int
    onboarding_checks: Tuple[OnboardingCheckResult, ...] = ()
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "investor_id": self.investor_id,
            "legal_name": self.legal_name,
            "investor_type": self.investor_type,
            "jurisdiction": self.jurisdiction,
            "tax_form_status": self.tax_form_status,
            "accredited": self.accredited,
            "bank_account_ref": self.bank_account_ref,
            "identity_doc_ref": self.identity_doc_ref,
            "status": self.status.value,
            "version": self.version,
            "onboarding_checks": [c.to_dict() for c in self.onboarding_checks],
            "updated_at": format_iso_datetime(self.updated_at) if self.updated_at else None,
        }


@dataclass(frozen=True)
class DealingWindow:
    window_id: str
    fund_id: str
    share_class: str
    dealing_date: date
    open_at: datetime
    cutoff_at: datetime
    cash_due_date: date
    settlement_date: date

    def to_dict(self) -> Dict[str, Any]:
        return {
            "window_id": self.window_id,
            "fund_id": self.fund_id,
            "share_class": self.share_class,
            "dealing_date": self.dealing_date.isoformat(),
            "open_at": format_iso_datetime(self.open_at),
            "cutoff_at": format_iso_datetime(self.cutoff_at),
            "cash_due_date": self.cash_due_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
        }


@dataclass(frozen=True)
class NAVRecord:
    nav_id: str
    fund_id: str
    share_class: str
    dealing_date: date
    currency: str
    nav_per_share: Decimal
    version: int
    status: NAVStatus
    approved_by: str
    approved_at: datetime
    supersedes_nav_id: Optional[str] = None
    restatement_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nav_id": self.nav_id,
            "fund_id": self.fund_id,
            "share_class": self.share_class,
            "dealing_date": self.dealing_date.isoformat(),
            "currency": self.currency,
            "nav_per_share": str(self.nav_per_share),
            "version": self.version,
            "status": self.status.value,
            "approved_by": self.approved_by,
            "approved_at": format_iso_datetime(self.approved_at),
            "supersedes_nav_id": self.supersedes_nav_id,
            "restatement_reason": self.restatement_reason,
        }


@dataclass(frozen=True)
class PricingCalculation:
    request_id: str
    request_type: RequestType
    nav_id: str
    nav_version: int
    nav_per_share: Decimal
    currency: str
    fee_bps: int
    gross_cash: Decimal
    fee_amount: Decimal
    net_cash: Decimal
    shares: Decimal
    valuation_at_nav: Decimal
    rounding_residual: Decimal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "request_type": self.request_type.value,
            "nav_id": self.nav_id,
            "nav_version": self.nav_version,
            "nav_per_share": str(self.nav_per_share),
            "currency": self.currency,
            "fee_bps": self.fee_bps,
            "gross_cash": str(self.gross_cash),
            "fee_amount": str(self.fee_amount),
            "net_cash": str(self.net_cash),
            "shares": str(self.shares),
            "valuation_at_nav": str(self.valuation_at_nav),
            "rounding_residual": str(self.rounding_residual),
        }


@dataclass(frozen=True)
class CapitalFlowRequest:
    request_id: str
    idempotency_key: str
    investor_id: str
    fund_id: str
    share_class: str
    request_type: RequestType
    dealing_date: date
    currency: str
    gross_amount: Optional[Decimal]
    requested_shares: Optional[Decimal]
    submitted_at: datetime
    maker_id: str
    status: RequestStatus
    version: int = 1
    checker_id: Optional[str] = None
    approved_at: Optional[datetime] = None
    nav_id: Optional[str] = None
    nav_version: Optional[int] = None
    fee_amount: Optional[Decimal] = None
    net_cash: Optional[Decimal] = None
    calculated_shares: Optional[Decimal] = None
    rounding_residual: Optional[Decimal] = None
    cancellation_reason: Optional[str] = None
    cancelled_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "idempotency_key": self.idempotency_key,
            "investor_id": self.investor_id,
            "fund_id": self.fund_id,
            "share_class": self.share_class,
            "request_type": self.request_type.value,
            "dealing_date": self.dealing_date.isoformat(),
            "currency": self.currency,
            "gross_amount": str(self.gross_amount) if self.gross_amount is not None else None,
            "requested_shares": str(self.requested_shares) if self.requested_shares is not None else None,
            "submitted_at": format_iso_datetime(self.submitted_at),
            "maker_id": self.maker_id,
            "status": self.status.value,
            "version": self.version,
            "checker_id": self.checker_id,
            "approved_at": format_iso_datetime(self.approved_at) if self.approved_at else None,
            "nav_id": self.nav_id,
            "nav_version": self.nav_version,
            "fee_amount": str(self.fee_amount) if self.fee_amount is not None else None,
            "net_cash": str(self.net_cash) if self.net_cash is not None else None,
            "calculated_shares": str(self.calculated_shares) if self.calculated_shares is not None else None,
            "rounding_residual": str(self.rounding_residual) if self.rounding_residual is not None else None,
            "cancellation_reason": self.cancellation_reason,
            "cancelled_by": self.cancelled_by,
        }


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    entity_type: str
    entity_id: str
    maker_id: str
    checker_id: str
    decision: ApprovalDecision
    reason: str
    decided_at: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "maker_id": self.maker_id,
            "checker_id": self.checker_id,
            "decision": self.decision.value,
            "reason": self.reason,
            "decided_at": format_iso_datetime(self.decided_at),
        }


@dataclass(frozen=True)
class CashConfirmation:
    confirmation_id: str
    request_id: str
    direction: CashDirection
    currency: str
    amount: Decimal
    value_date: date
    bank_reference: str
    received_at: datetime
    match_status: CashMatchStatus = CashMatchStatus.UNMATCHED
    discrepancy_amount: Decimal = Decimal("0.00")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "request_id": self.request_id,
            "direction": self.direction.value,
            "currency": self.currency,
            "amount": str(self.amount),
            "value_date": self.value_date.isoformat(),
            "bank_reference": self.bank_reference,
            "received_at": format_iso_datetime(self.received_at),
            "match_status": self.match_status.value,
            "discrepancy_amount": str(self.discrepancy_amount),
        }


@dataclass(frozen=True)
class ExceptionCase:
    exception_id: str
    request_id: str
    exception_type: ExceptionType
    status: ExceptionStatus
    summary: str
    created_by: str
    created_at: datetime
    details: Dict[str, Any] = field(default_factory=dict)
    reviewer_id: Optional[str] = None
    resolution_note: Optional[str] = None
    resolved_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exception_id": self.exception_id,
            "request_id": self.request_id,
            "exception_type": self.exception_type.value,
            "status": self.status.value,
            "summary": self.summary,
            "created_by": self.created_by,
            "created_at": format_iso_datetime(self.created_at),
            "details": self.details,
            "reviewer_id": self.reviewer_id,
            "resolution_note": self.resolution_note,
            "resolved_at": format_iso_datetime(self.resolved_at) if self.resolved_at else None,
        }


@dataclass(frozen=True)
class ShareJournalEntry:
    entry_id: str
    request_id: str
    investor_id: str
    fund_id: str
    share_class: str
    currency: str
    dealing_date: date
    entry_type: JournalEntryType
    share_delta: Decimal
    gross_cash_delta: Decimal
    fee_delta: Decimal
    net_cash_delta: Decimal
    nav_id: str
    nav_version: int
    posted_at: datetime
    posted_by: str
    version: int = 1
    supersedes_entry_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "request_id": self.request_id,
            "investor_id": self.investor_id,
            "fund_id": self.fund_id,
            "share_class": self.share_class,
            "currency": self.currency,
            "dealing_date": self.dealing_date.isoformat(),
            "entry_type": self.entry_type.value,
            "share_delta": str(self.share_delta),
            "gross_cash_delta": str(self.gross_cash_delta),
            "fee_delta": str(self.fee_delta),
            "net_cash_delta": str(self.net_cash_delta),
            "nav_id": self.nav_id,
            "nav_version": self.nav_version,
            "posted_at": format_iso_datetime(self.posted_at),
            "posted_by": self.posted_by,
            "version": self.version,
            "supersedes_entry_id": self.supersedes_entry_id,
        }


@dataclass(frozen=True)
class SharePosition:
    investor_id: str
    fund_id: str
    share_class: str
    currency: str
    settled_shares: Decimal
    pending_redemption_shares: Decimal
    total_subscribed_gross: Decimal
    total_redeemed_net: Decimal
    total_fees_paid: Decimal

    @property
    def available_shares(self) -> Decimal:
        return self.settled_shares - self.pending_redemption_shares

    def to_dict(self) -> Dict[str, Any]:
        return {
            "investor_id": self.investor_id,
            "fund_id": self.fund_id,
            "share_class": self.share_class,
            "currency": self.currency,
            "settled_shares": str(self.settled_shares),
            "pending_redemption_shares": str(self.pending_redemption_shares),
            "available_shares": str(self.available_shares),
            "total_subscribed_gross": str(self.total_subscribed_gross),
            "total_redeemed_net": str(self.total_redeemed_net),
            "total_fees_paid": str(self.total_fees_paid),
        }


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    event_id: str
    timestamp: datetime
    actor_id: str
    action: str
    entity_type: str
    entity_id: str
    payload: Dict[str, Any]
    prev_hash: str
    entry_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sequence": self.sequence,
            "event_id": self.event_id,
            "timestamp": format_iso_datetime(self.timestamp),
            "actor_id": self.actor_id,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "payload": self.payload,
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
        }
