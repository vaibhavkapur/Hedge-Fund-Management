"""Canonical immutable domain models using exact Decimal arithmetic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP
from enum import Enum
from typing import Dict, Optional, Tuple


SUPPORTED_CURRENCIES: Dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CHF": 2,
    "JPY": 0,
}

ROUNDING_MAP: Dict[str, str] = {
    "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
    "ROUND_HALF_UP": ROUND_HALF_UP,
    "ROUND_DOWN": ROUND_DOWN,
    "ROUND_UP": ROUND_UP,
}


def quantize_money(
    amount: Decimal,
    currency: str,
    rounding_mode: str = "ROUND_HALF_EVEN",
    minor_units: Optional[Dict[str, int]] = None,
) -> Decimal:
    """Round a Decimal amount to exact currency minor units."""
    unit_map = minor_units or SUPPORTED_CURRENCIES
    scale = unit_map.get(currency, 2)
    quantum = Decimal(1) if scale == 0 else Decimal(10) ** (-scale)
    return amount.quantize(quantum, rounding=ROUNDING_MAP[rounding_mode])


class DayCountConvention(str, Enum):
    ACT_360 = "ACT_360"
    ACT_365F = "ACT_365F"
    ACT_ACT_ISDA = "ACT_ACT_ISDA"
    THIRTY_360 = "THIRTY_360"


class AssetClass(str, Enum):
    CASH = "CASH"
    US_TREASURY = "US_TREASURY"
    SOVEREIGN_BOND = "SOVEREIGN_BOND"
    IG_CORPORATE_BOND = "IG_CORPORATE_BOND"
    HY_CORPORATE_BOND = "HY_CORPORATE_BOND"
    EQUITY_LARGE_CAP = "EQUITY_LARGE_CAP"
    EQUITY_SMALL_CAP = "EQUITY_SMALL_CAP"
    ETF = "ETF"
    MONEY_MARKET_FUND = "MONEY_MARKET_FUND"


class EncumbranceStatus(str, Enum):
    UNENCUMBERED = "UNENCUMBERED"
    PLEDGED_MARGIN = "PLEDGED_MARGIN"
    REHYPOTHECATED = "REHYPOTHECATED"
    PENDING_TRANSFER_OUT = "PENDING_TRANSFER_OUT"
    PENDING_TRANSFER_IN = "PENDING_TRANSFER_IN"
    FAILED_TRANSFER = "FAILED_TRANSFER"
    LOCKED_SUBSTITUTION = "LOCKED_SUBSTITUTION"


class CashBalanceType(str, Enum):
    FREE_CASH = "FREE_CASH"
    MARGIN_CASH = "MARGIN_CASH"
    DEBIT_FINANCING = "DEBIT_FINANCING"
    SHORT_PROCEEDS = "SHORT_PROCEEDS"


class TransferStatus(str, Enum):
    SETTLED = "SETTLED"
    PENDING = "PENDING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class MarginCallAction(str, Enum):
    MARGIN_CALL = "MARGIN_CALL"
    EXCESS_RETURN = "EXCESS_RETURN"
    NO_ACTION = "NO_ACTION"


class ApprovalStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AUTO_FLAGGED_SHORTFALL = "AUTO_FLAGGED_SHORTFALL"


@dataclass(frozen=True)
class FxRate:
    base_currency: str
    quote_currency: str
    rate: Decimal
    as_of_date: date
    source: str = "SIMULATED_FIXING"


@dataclass(frozen=True)
class HaircutRule:
    asset_class: AssetClass
    rating_tier: str
    maturity_bucket: str
    eligible: bool
    haircut_pct: Decimal
    concentration_limit_pct: Optional[Decimal] = None
    currency_mismatch_addon_pct: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class CollateralPolicy:
    policy_id: str
    version: int
    broker_id: str
    base_currency: str
    effective_date: date
    default_day_count: DayCountConvention
    minimum_transfer_amount: Decimal
    rounding_increment: Decimal
    reconciliation_tolerance: Decimal
    rules: Tuple[HaircutRule, ...]


@dataclass(frozen=True)
class PositionRecord:
    position_id: str
    lot_id: str
    broker_id: str
    account_id: str
    security_id: str
    asset_class: AssetClass
    rating_tier: str
    maturity_bucket: str
    quantity: Decimal
    market_price: Decimal
    currency: str
    encumbrance_status: EncumbranceStatus
    isin: Optional[str] = None
    pledge_ref: Optional[str] = None

    @property
    def gross_market_value_local(self) -> Decimal:
        return self.quantity * self.market_price


@dataclass(frozen=True)
class CashBalanceRecord:
    balance_id: str
    broker_id: str
    account_id: str
    currency: str
    amount: Decimal
    balance_type: CashBalanceType


@dataclass(frozen=True)
class MarginRequirement:
    broker_id: str
    account_id: str
    business_date: date
    initial_margin: Decimal
    variation_margin: Decimal
    house_addon: Decimal
    currency: str

    @property
    def total_requirement(self) -> Decimal:
        return self.initial_margin + self.variation_margin + self.house_addon


@dataclass(frozen=True)
class FinancingRateSchedule:
    schedule_id: str
    broker_id: str
    currency: str
    benchmark_name: str
    benchmark_rate: Decimal
    debit_spread_bps: Decimal
    credit_spread_bps: Decimal
    short_rebate_spread_bps: Decimal
    day_count: DayCountConvention
    effective_date: date


@dataclass(frozen=True)
class CollateralTransfer:
    transfer_id: str
    from_broker_id: str
    to_broker_id: str
    lot_id: str
    security_id: str
    quantity: Decimal
    currency: str
    market_value: Decimal
    status: TransferStatus
    trade_date: date
    settlement_date: date
    failure_reason: Optional[str] = None


@dataclass(frozen=True)
class BrokerStatement:
    statement_id: str
    version: int
    broker_id: str
    account_id: str
    business_date: date
    received_at: str
    base_currency: str
    positions: Tuple[PositionRecord, ...]
    cash_balances: Tuple[CashBalanceRecord, ...]
    margin_requirement: MarginRequirement
    content_hash: str
    supersedes_statement_id: Optional[str] = None
    reported_collateral_value: Optional[Decimal] = None
    reported_financing_accrual: Optional[Decimal] = None


@dataclass(frozen=True)
class InternalLedgerSnapshot:
    snapshot_id: str
    version: int
    broker_id: str
    account_id: str
    business_date: date
    positions: Tuple[PositionRecord, ...]
    cash_balances: Tuple[CashBalanceRecord, ...]
    transfers: Tuple[CollateralTransfer, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class EvaluatedPosition:
    position_id: str
    lot_id: str
    broker_id: str
    security_id: str
    asset_class: AssetClass
    currency: str
    encumbrance_status: EncumbranceStatus
    gross_value_local: Decimal
    fx_rate_to_base: Decimal
    gross_value_base: Decimal
    eligible: bool
    base_haircut_pct: Decimal
    fx_addon_haircut_pct: Decimal
    effective_haircut_pct: Decimal
    concentration_trim_base: Decimal
    net_collateral_value_base: Decimal
    counted_for_margin: bool
    exclusion_reason: Optional[str] = None


@dataclass(frozen=True)
class CollateralValuationSummary:
    broker_id: str
    account_id: str
    business_date: date
    base_currency: str
    policy_id: str
    policy_version: int
    positions: Tuple[EvaluatedPosition, ...]
    total_gross_securities_base: Decimal
    eligible_securities_value_base: Decimal
    ineligible_securities_gross_base: Decimal
    encumbered_collateral_base: Decimal
    pledged_margin_collateral_base: Decimal
    rehypothecated_collateral_base: Decimal
    available_unencumbered_collateral_base: Decimal
    margin_cash_base: Decimal
    free_cash_base: Decimal
    total_posted_collateral_base: Decimal
    total_available_buffer_base: Decimal
    quarantined_failed_transfer_lots: Tuple[str, ...] = field(default_factory=tuple)
    ineligible_lots: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FinancingAccrualLine:
    balance_id: str
    broker_id: str
    account_id: str
    currency: str
    balance_type: CashBalanceType
    principal_local: Decimal
    start_date: date
    end_date: date
    day_count: DayCountConvention
    year_fraction: Decimal
    effective_rate: Decimal
    accrual_local: Decimal
    fx_rate_to_base: Decimal
    accrual_base: Decimal
    notes: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FinancingAccrualSummary:
    broker_id: str
    account_id: str
    start_date: date
    end_date: date
    base_currency: str
    lines: Tuple[FinancingAccrualLine, ...]
    net_accrual_base: Decimal


@dataclass(frozen=True)
class ReconciliationBreak:
    break_id: str
    broker_id: str
    account_id: str
    business_date: date
    category: str
    severity: str
    reference_id: str
    currency: str
    broker_value: Decimal
    internal_value: Decimal
    difference: Decimal
    description: str


@dataclass(frozen=True)
class MarginCallNotice:
    call_id: str
    broker_id: str
    account_id: str
    business_date: date
    base_currency: str
    margin_requirement_base: Decimal
    posted_eligible_collateral_base: Decimal
    raw_surplus_or_deficit_base: Decimal
    minimum_transfer_amount_base: Decimal
    rounding_increment_base: Decimal
    action: MarginCallAction
    call_or_return_amount_base: Decimal
    unencumbered_buffer_base: Decimal
    flagged_before_settlement: bool
    approval_status: ApprovalStatus
    recommended_funding_lots: Tuple[str, ...] = field(default_factory=tuple)
    notes: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ReconciliationResult:
    run_id: str
    statement_id: str
    statement_version: int
    broker_id: str
    account_id: str
    business_date: date
    base_currency: str
    duplicate_skipped: bool
    valuation: CollateralValuationSummary
    financing: FinancingAccrualSummary
    breaks: Tuple[ReconciliationBreak, ...]
    margin_call: MarginCallNotice
    reconciled_clean: bool


@dataclass(frozen=True)
class SubstitutionRequest:
    request_id: str
    broker_id: str
    account_id: str
    business_date: date
    release_lot_ids: Tuple[str, ...]
    pledge_positions: Tuple[PositionRecord, ...]
    requested_by: str


@dataclass(frozen=True)
class SubstitutionReviewResult:
    review_id: str
    request_id: str
    broker_id: str
    account_id: str
    business_date: date
    base_currency: str
    pre_posted_collateral_base: Decimal
    released_collateral_value_base: Decimal
    proposed_pledge_value_base: Decimal
    post_posted_collateral_base: Decimal
    margin_requirement_base: Decimal
    post_surplus_or_deficit_base: Decimal
    shortfall_flagged_before_settlement: bool
    settlement_permitted: bool
    approval_status: ApprovalStatus
    required_top_up_base: Decimal
    rejected_lots: Tuple[str, ...]
    warnings: Tuple[str, ...]
    post_settlement_positions: Tuple[PositionRecord, ...]
