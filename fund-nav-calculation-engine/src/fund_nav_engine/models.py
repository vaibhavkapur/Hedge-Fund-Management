"""Canonical immutable domain models and serialization helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from fund_nav_engine.errors import ValidationError


class InstrumentType(str, Enum):
    EQUITY = "equity"
    BOND = "bond"
    CASH = "cash"
    ETF = "etf"
    FUND_UNIT = "fund_unit"
    DERIVATIVE_MTM = "derivative_mtm"


class AccrualDirection(str, Enum):
    ASSET = "asset"
    LIABILITY = "liability"


class AccrualType(str, Enum):
    MANAGEMENT_FEE = "management_fee"
    PERFORMANCE_FEE = "performance_fee"
    INTEREST_RECEIVABLE = "interest_receivable"
    DIVIDEND_RECEIVABLE = "dividend_receivable"
    EXPENSE_ACCRUAL = "expense_accrual"
    TAX_ACCRUAL = "tax_accrual"


class FlowType(str, Enum):
    SUBSCRIPTION = "subscription"
    REDEMPTION = "redemption"


class FlowStatus(str, Enum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    CANCELLED = "cancelled"


class FlowTimingRule(str, Enum):
    PRE_NAV = "pre_nav"
    POST_NAV = "post_nav"


class StalePricePolicy(str, Enum):
    ERROR = "error"
    WARN_AND_USE_LAST = "warn_and_use_last"
    REQUIRE_OVERRIDE = "require_override"


class PostCutoffPolicy(str, Enum):
    DEFER = "defer"
    REJECT = "reject"


class SnapshotStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    FROZEN = "frozen"


class LedgerAccountType(str, Enum):
    CASH = "cash"
    SECURITIES_COST = "securities_cost"
    RECEIVABLES = "receivables"
    PAYABLES = "payables"
    ACCRUED_EXPENSES = "accrued_expenses"
    CAPITAL_SUBSCRIPTIONS = "capital_subscriptions"
    CAPITAL_REDEMPTIONS = "capital_redemptions"


def parse_decimal(value: Any, field_name: str) -> Decimal:
    """Parse exact Decimal rejecting bool, float, NaN, and Infinity."""
    if isinstance(value, bool):
        raise ValidationError(
            f"Field '{field_name}' must be a decimal string, int, or Decimal, not bool.",
            details={"field": field_name, "value": value},
        )
    if isinstance(value, float):
        raise ValidationError(
            f"Field '{field_name}' cannot be a binary float; pass exact string or Decimal.",
            details={"field": field_name, "value": value},
        )
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError(
            f"Field '{field_name}' is not a valid Decimal: {value!r}",
            details={"field": field_name, "value": repr(value)},
        ) from exc
    if not dec.is_finite():
        raise ValidationError(
            f"Field '{field_name}' must be finite, got {value!r}",
            details={"field": field_name, "value": repr(value)},
        )
    return dec


def parse_date(value: Any, field_name: str) -> date:
    """Parse ISO-8601 YYYY-MM-DD date."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError(
                f"Field '{field_name}' must be ISO date YYYY-MM-DD, got {value!r}",
                details={"field": field_name, "value": value},
            ) from exc
    raise ValidationError(
        f"Field '{field_name}' must be a date or ISO string, got {type(value).__name__}",
        details={"field": field_name},
    )


def parse_utc_datetime(value: Any, field_name: str) -> datetime:
    """Parse ISO-8601 timestamp and normalize to UTC."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationError(
                f"Field '{field_name}' must be ISO-8601 datetime, got {value!r}",
                details={"field": field_name, "value": value},
            ) from exc
    else:
        raise ValidationError(
            f"Field '{field_name}' must be datetime or ISO string, got {type(value).__name__}",
            details={"field": field_name},
        )
    if dt.tzinfo is None:
        raise ValidationError(
            f"Field '{field_name}' must include explicit timezone (UTC or offset).",
            details={"field": field_name, "value": str(value)},
        )
    return dt.astimezone(timezone.utc)


def format_decimal(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    # Avoid scientific notation while preserving exact scale
    return format(value, "f")


def format_utc_datetime(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(data: Any) -> str:
    """Produce deterministic JSON string suitable for cryptographic hashing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_canonical(data: Any) -> str:
    """Compute SHA-256 hex digest of canonical JSON representation."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def quantize_decimal(value: Decimal, precision: int, rounding_mode: str) -> Decimal:
    """Quantize a Decimal to the specified number of fractional digits."""
    quantum = Decimal(1) if precision == 0 else Decimal(1).scaleb(-precision)
    return value.quantize(quantum, rounding=rounding_mode)


@dataclass(frozen=True)
class ValuationConfig:
    config_version: str
    fund_id: str
    base_currency: str
    currency_precisions: Dict[str, int]
    nav_per_share_precision: int = 6
    share_precision: int = 6
    fx_rate_precision: int = 8
    rounding_mode: str = "ROUND_HALF_EVEN"
    max_price_age_days: int = 2
    stale_price_policy: StalePricePolicy = StalePricePolicy.ERROR
    allow_inverse_fx: bool = True
    flow_cutoff_utc: str = "16:00:00"
    post_cutoff_policy: PostCutoffPolicy = PostCutoffPolicy.DEFER
    ledger_tolerance: Decimal = Decimal("0.01")
    require_reconciliation: bool = True
    seed: int = 42

    def currency_precision(self, currency: str) -> int:
        if currency not in self.currency_precisions:
            raise ValidationError(
                f"Unsupported currency '{currency}' not present in currency_precisions.",
                details={"currency": currency},
            )
        return self.currency_precisions[currency]

    def cutoff_time(self) -> time:
        return time.fromisoformat(self.flow_cutoff_utc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "config_version": self.config_version,
            "fund_id": self.fund_id,
            "base_currency": self.base_currency,
            "currency_precisions": dict(sorted(self.currency_precisions.items())),
            "nav_per_share_precision": self.nav_per_share_precision,
            "share_precision": self.share_precision,
            "fx_rate_precision": self.fx_rate_precision,
            "rounding_mode": self.rounding_mode,
            "max_price_age_days": self.max_price_age_days,
            "stale_price_policy": self.stale_price_policy.value,
            "allow_inverse_fx": self.allow_inverse_fx,
            "flow_cutoff_utc": self.flow_cutoff_utc,
            "post_cutoff_policy": self.post_cutoff_policy.value,
            "ledger_tolerance": format_decimal(self.ledger_tolerance),
            "require_reconciliation": self.require_reconciliation,
            "seed": self.seed,
        }


@dataclass(frozen=True)
class PositionRecord:
    position_id: str
    fund_id: str
    instrument_id: str
    instrument_type: InstrumentType
    quantity: Decimal
    cost_basis: Decimal
    currency: str
    valuation_date: date
    source_system: str = "portfolio_ledger"
    record_version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "fund_id": self.fund_id,
            "instrument_id": self.instrument_id,
            "instrument_type": self.instrument_type.value,
            "quantity": format_decimal(self.quantity),
            "cost_basis": format_decimal(self.cost_basis),
            "currency": self.currency,
            "valuation_date": self.valuation_date.isoformat(),
            "source_system": self.source_system,
            "record_version": self.record_version,
        }


@dataclass(frozen=True)
class PriceRecord:
    price_id: str
    instrument_id: str
    price: Decimal
    currency: str
    price_date: date
    as_of_timestamp: datetime
    pricing_source: str = "composite_close"
    is_override: bool = False
    override_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "price_id": self.price_id,
            "instrument_id": self.instrument_id,
            "price": format_decimal(self.price),
            "currency": self.currency,
            "price_date": self.price_date.isoformat(),
            "as_of_timestamp": format_utc_datetime(self.as_of_timestamp),
            "pricing_source": self.pricing_source,
            "is_override": self.is_override,
            "override_reason": self.override_reason,
        }


@dataclass(frozen=True)
class FxRateRecord:
    fx_id: str
    base_currency: str
    quote_currency: str
    rate: Decimal
    rate_date: date
    as_of_timestamp: datetime
    source: str = "wm_reuters_mock"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fx_id": self.fx_id,
            "base_currency": self.base_currency,
            "quote_currency": self.quote_currency,
            "rate": format_decimal(self.rate),
            "rate_date": self.rate_date.isoformat(),
            "as_of_timestamp": format_utc_datetime(self.as_of_timestamp),
            "source": self.source,
        }


@dataclass(frozen=True)
class LiabilityRecord:
    liability_id: str
    fund_id: str
    category: str
    amount: Decimal
    currency: str
    valuation_date: date
    share_class_id: Optional[str] = None
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "liability_id": self.liability_id,
            "fund_id": self.fund_id,
            "category": self.category,
            "amount": format_decimal(self.amount),
            "currency": self.currency,
            "valuation_date": self.valuation_date.isoformat(),
            "share_class_id": self.share_class_id,
            "description": self.description,
        }


@dataclass(frozen=True)
class AccrualRecord:
    accrual_id: str
    fund_id: str
    accrual_type: AccrualType
    direction: AccrualDirection
    amount: Decimal
    currency: str
    valuation_date: date
    period_start: date
    period_end: date
    share_class_id: Optional[str] = None
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accrual_id": self.accrual_id,
            "fund_id": self.fund_id,
            "accrual_type": self.accrual_type.value,
            "direction": self.direction.value,
            "amount": format_decimal(self.amount),
            "currency": self.currency,
            "valuation_date": self.valuation_date.isoformat(),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "share_class_id": self.share_class_id,
            "description": self.description,
        }


@dataclass(frozen=True)
class ShareClassRecord:
    share_class_id: str
    fund_id: str
    name: str
    currency: str
    opening_shares: Decimal
    management_fee_bps: Decimal
    launch_nav_per_share: Decimal
    prior_nav_per_share: Optional[Decimal] = None
    prior_net_assets_base: Optional[Decimal] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "share_class_id": self.share_class_id,
            "fund_id": self.fund_id,
            "name": self.name,
            "currency": self.currency,
            "opening_shares": format_decimal(self.opening_shares),
            "management_fee_bps": format_decimal(self.management_fee_bps),
            "launch_nav_per_share": format_decimal(self.launch_nav_per_share),
            "prior_nav_per_share": format_decimal(self.prior_nav_per_share),
            "prior_net_assets_base": format_decimal(self.prior_net_assets_base),
        }


@dataclass(frozen=True)
class CapitalFlowRecord:
    flow_id: str
    fund_id: str
    share_class_id: str
    flow_type: FlowType
    currency: str
    trade_date: date
    settlement_date: date
    submitted_at: datetime
    amount: Optional[Decimal] = None
    shares: Optional[Decimal] = None
    status: FlowStatus = FlowStatus.CONFIRMED
    timing_rule: FlowTimingRule = FlowTimingRule.POST_NAV

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "fund_id": self.fund_id,
            "share_class_id": self.share_class_id,
            "flow_type": self.flow_type.value,
            "currency": self.currency,
            "trade_date": self.trade_date.isoformat(),
            "settlement_date": self.settlement_date.isoformat(),
            "submitted_at": format_utc_datetime(self.submitted_at),
            "amount": format_decimal(self.amount),
            "shares": format_decimal(self.shares),
            "status": self.status.value,
            "timing_rule": self.timing_rule.value,
        }


@dataclass(frozen=True)
class LedgerBalanceRecord:
    ledger_account_id: str
    fund_id: str
    account_type: LedgerAccountType
    balance: Decimal
    currency: str
    as_of_date: date
    share_class_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ledger_account_id": self.ledger_account_id,
            "fund_id": self.fund_id,
            "account_type": self.account_type.value,
            "balance": format_decimal(self.balance),
            "currency": self.currency,
            "as_of_date": self.as_of_date.isoformat(),
            "share_class_id": self.share_class_id,
        }


@dataclass(frozen=True)
class ValuationInputBundle:
    bundle_id: str
    fund_id: str
    valuation_date: date
    input_version: str
    positions: Tuple[PositionRecord, ...] = ()
    prices: Tuple[PriceRecord, ...] = ()
    fx_rates: Tuple[FxRateRecord, ...] = ()
    liabilities: Tuple[LiabilityRecord, ...] = ()
    accruals: Tuple[AccrualRecord, ...] = ()
    share_classes: Tuple[ShareClassRecord, ...] = ()
    capital_flows: Tuple[CapitalFlowRecord, ...] = ()
    ledger_balances: Tuple[LedgerBalanceRecord, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "fund_id": self.fund_id,
            "valuation_date": self.valuation_date.isoformat(),
            "input_version": self.input_version,
            "positions": [p.to_dict() for p in sorted(self.positions, key=lambda x: x.position_id)],
            "prices": [p.to_dict() for p in sorted(self.prices, key=lambda x: x.price_id)],
            "fx_rates": [f.to_dict() for f in sorted(self.fx_rates, key=lambda x: x.fx_id)],
            "liabilities": [l.to_dict() for l in sorted(self.liabilities, key=lambda x: x.liability_id)],
            "accruals": [a.to_dict() for a in sorted(self.accruals, key=lambda x: x.accrual_id)],
            "share_classes": [
                s.to_dict() for s in sorted(self.share_classes, key=lambda x: x.share_class_id)
            ],
            "capital_flows": [
                c.to_dict() for c in sorted(self.capital_flows, key=lambda x: x.flow_id)
            ],
            "ledger_balances": [
                b.to_dict() for b in sorted(self.ledger_balances, key=lambda x: x.ledger_account_id)
            ],
        }

    def content_hash(self) -> str:
        return sha256_canonical(self.to_dict())


@dataclass(frozen=True)
class PositionValuation:
    position_id: str
    instrument_id: str
    instrument_type: InstrumentType
    quantity: Decimal
    unit_price: Decimal
    price_currency: str
    local_market_value: Decimal
    fx_rate: Decimal
    base_market_value: Decimal
    price_date: date
    price_age_days: int
    is_stale: bool
    stale_override_used: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "instrument_id": self.instrument_id,
            "instrument_type": self.instrument_type.value,
            "quantity": format_decimal(self.quantity),
            "unit_price": format_decimal(self.unit_price),
            "price_currency": self.price_currency,
            "local_market_value": format_decimal(self.local_market_value),
            "fx_rate": format_decimal(self.fx_rate),
            "base_market_value": format_decimal(self.base_market_value),
            "price_date": self.price_date.isoformat(),
            "price_age_days": self.price_age_days,
            "is_stale": self.is_stale,
            "stale_override_used": self.stale_override_used,
        }


@dataclass(frozen=True)
class ReconciliationBreak:
    account_type: LedgerAccountType
    currency: str
    expected_amount: Decimal
    ledger_amount: Decimal
    difference: Decimal
    tolerance: Decimal
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_type": self.account_type.value,
            "currency": self.currency,
            "expected_amount": format_decimal(self.expected_amount),
            "ledger_amount": format_decimal(self.ledger_amount),
            "difference": format_decimal(self.difference),
            "tolerance": format_decimal(self.tolerance),
            "passed": self.passed,
        }


@dataclass(frozen=True)
class ReconciliationReport:
    reconciled: bool
    total_checks: int
    passed_checks: int
    breaks: Tuple[ReconciliationBreak, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reconciled": self.reconciled,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "breaks": [b.to_dict() for b in self.breaks],
        }


@dataclass(frozen=True)
class ShareClassNav:
    share_class_id: str
    currency: str
    opening_shares: Decimal
    allocation_weight: Decimal
    allocated_gross_assets_base: Decimal
    allocated_liabilities_base: Decimal
    class_specific_asset_accruals_base: Decimal
    class_specific_liabilities_base: Decimal
    pre_flow_net_assets_base: Decimal
    dealing_nav_per_share: Optional[Decimal]
    subscriptions_amount_base: Decimal
    redemptions_amount_base: Decimal
    subscription_shares: Decimal
    redemption_shares: Decimal
    closing_shares: Decimal
    closing_net_assets_base: Decimal
    closing_net_assets_class_ccy: Decimal
    fx_rate_to_class_ccy: Decimal
    nav_per_share: Optional[Decimal]
    zero_shares_flag: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "share_class_id": self.share_class_id,
            "currency": self.currency,
            "opening_shares": format_decimal(self.opening_shares),
            "allocation_weight": format_decimal(self.allocation_weight),
            "allocated_gross_assets_base": format_decimal(self.allocated_gross_assets_base),
            "allocated_liabilities_base": format_decimal(self.allocated_liabilities_base),
            "class_specific_asset_accruals_base": format_decimal(
                self.class_specific_asset_accruals_base
            ),
            "class_specific_liabilities_base": format_decimal(
                self.class_specific_liabilities_base
            ),
            "pre_flow_net_assets_base": format_decimal(self.pre_flow_net_assets_base),
            "dealing_nav_per_share": format_decimal(self.dealing_nav_per_share),
            "subscriptions_amount_base": format_decimal(self.subscriptions_amount_base),
            "redemptions_amount_base": format_decimal(self.redemptions_amount_base),
            "subscription_shares": format_decimal(self.subscription_shares),
            "redemption_shares": format_decimal(self.redemption_shares),
            "closing_shares": format_decimal(self.closing_shares),
            "closing_net_assets_base": format_decimal(self.closing_net_assets_base),
            "closing_net_assets_class_ccy": format_decimal(self.closing_net_assets_class_ccy),
            "fx_rate_to_class_ccy": format_decimal(self.fx_rate_to_class_ccy),
            "nav_per_share": format_decimal(self.nav_per_share),
            "zero_shares_flag": self.zero_shares_flag,
        }


@dataclass(frozen=True)
class NavCalculationResult:
    snapshot_id: str
    fund_id: str
    valuation_date: date
    version: int
    status: SnapshotStatus
    prior_snapshot_id: Optional[str]
    restatement_reason: Optional[str]
    base_currency: str
    pre_flow_gross_assets: Decimal
    pre_flow_liabilities: Decimal
    pre_flow_net_assets: Decimal
    gross_assets: Decimal
    total_liabilities: Decimal
    net_assets: Decimal
    total_subscriptions: Decimal
    total_redemptions: Decimal
    net_capital_flows: Decimal
    position_valuations: Tuple[PositionValuation, ...]
    share_class_navs: Tuple[ShareClassNav, ...]
    reconciliation: ReconciliationReport
    warnings: Tuple[str, ...]
    diagnostics: Dict[str, Any]
    input_hash: str
    config_version: str
    calculated_at: datetime
    output_hash: str = ""

    def core_payload_dict(self) -> Dict[str, Any]:
        """Deterministic payload excluding mutable workflow status and wall-clock timestamps."""
        return {
            "snapshot_id": self.snapshot_id,
            "fund_id": self.fund_id,
            "valuation_date": self.valuation_date.isoformat(),
            "version": self.version,
            "prior_snapshot_id": self.prior_snapshot_id,
            "restatement_reason": self.restatement_reason,
            "base_currency": self.base_currency,
            "pre_flow_gross_assets": format_decimal(self.pre_flow_gross_assets),
            "pre_flow_liabilities": format_decimal(self.pre_flow_liabilities),
            "pre_flow_net_assets": format_decimal(self.pre_flow_net_assets),
            "gross_assets": format_decimal(self.gross_assets),
            "total_liabilities": format_decimal(self.total_liabilities),
            "net_assets": format_decimal(self.net_assets),
            "total_subscriptions": format_decimal(self.total_subscriptions),
            "total_redemptions": format_decimal(self.total_redemptions),
            "net_capital_flows": format_decimal(self.net_capital_flows),
            "position_valuations": [p.to_dict() for p in self.position_valuations],
            "share_class_navs": [s.to_dict() for s in self.share_class_navs],
            "reconciliation": self.reconciliation.to_dict(),
            "warnings": list(self.warnings),
            "diagnostics": self.diagnostics,
            "input_hash": self.input_hash,
            "config_version": self.config_version,
        }

    def to_dict(self) -> Dict[str, Any]:
        payload = self.core_payload_dict()
        payload["status"] = self.status.value
        payload["calculated_at"] = format_utc_datetime(self.calculated_at)
        payload["output_hash"] = self.output_hash
        return payload


@dataclass(frozen=True)
class AuditJournalEntry:
    entry_id: str
    snapshot_id: str
    event_type: str
    actor: str
    timestamp: datetime
    details: Dict[str, Any] = field(default_factory=dict)
    payload_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "snapshot_id": self.snapshot_id,
            "event_type": self.event_type,
            "actor": self.actor,
            "timestamp": format_utc_datetime(self.timestamp),
            "details": self.details,
            "payload_hash": self.payload_hash,
        }
