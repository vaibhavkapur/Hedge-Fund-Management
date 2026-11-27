"""Portfolio valuation, market data, risk, attribution, and frozen snapshot contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hf_platform.domain.enums import AssetClass, Currency, TradeSide
from hf_platform.domain.master import ensure_utc


class TradeFill(BaseModel):
    model_config = ConfigDict(frozen=True)

    fill_id: str
    order_id: str
    fund_id: str
    account_id: str
    strategy_id: str
    instrument_id: str
    side: TradeSide
    quantity: Decimal = Field(gt=Decimal("0"))
    price: Decimal = Field(gt=Decimal("0"))
    currency: Currency
    commission: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))
    executed_at_utc: datetime
    received_at_utc: datetime
    broker_ref: str
    business_date: date | None = None
    is_late: bool = False
    deferred_to_date: date | None = None

    @field_validator("executed_at_utc", "received_at_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @property
    def signed_quantity(self) -> Decimal:
        return self.quantity if self.side == TradeSide.BUY else -self.quantity

    @property
    def gross_notional_local(self) -> Decimal:
        return self.quantity * self.price


class MarketMark(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument_id: str
    price: Decimal = Field(gt=Decimal("0"))
    currency: Currency
    source: str
    as_of_utc: datetime
    available_at_utc: datetime
    is_manual_override: bool = False
    override_approved_by: str | None = None

    @field_validator("as_of_utc", "available_at_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)


class FxRate(BaseModel):
    model_config = ConfigDict(frozen=True)

    base_currency: Currency
    quote_currency: Currency = Currency.USD
    rate_to_usd: Decimal = Field(gt=Decimal("0"))
    source: str
    as_of_utc: datetime
    available_at_utc: datetime

    @field_validator("as_of_utc", "available_at_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)


class Position(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    strategy_id: str
    instrument_id: str
    symbol: str
    asset_class: AssetClass
    sector: str
    currency: Currency
    quantity: Decimal
    avg_cost_local: Decimal
    mark_price_local: Decimal
    fx_rate_to_usd: Decimal
    cost_basis_local: Decimal
    market_value_local: Decimal
    market_value_usd: Decimal
    unrealized_pnl_local: Decimal
    unrealized_pnl_usd: Decimal
    realized_pnl_usd: Decimal = Decimal("0.00")
    weight_pct_nav: Decimal = Decimal("0.00")


class CashBalance(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    currency: Currency
    balance_local: Decimal
    fx_rate_to_usd: Decimal
    balance_usd: Decimal


class RiskLimitBreach(BaseModel):
    model_config = ConfigDict(frozen=True)

    limit_name: str
    observed_value: Decimal
    limit_value: Decimal
    dimension: str
    severity: str


class RiskMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    gross_exposure_usd: Decimal
    net_exposure_usd: Decimal
    long_exposure_usd: Decimal
    short_exposure_usd: Decimal
    gross_leverage: Decimal
    net_leverage: Decimal
    beta_adjusted_net_usd: Decimal
    daily_var_95_usd: Decimal
    daily_var_95_pct_nav: Decimal
    stress_equity_down_10pct_usd: Decimal
    stress_rates_shock_usd: Decimal
    top_single_name_pct: Decimal
    top_single_name_symbol: str
    exposure_by_sector_usd: dict[str, Decimal]
    exposure_by_asset_class_usd: dict[str, Decimal]
    exposure_by_currency_usd: dict[str, Decimal]
    exposure_by_counterparty_usd: dict[str, Decimal]
    limit_breaches: list[RiskLimitBreach] = Field(default_factory=list)


class StrategyAttribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str
    strategy_name: str
    capital_allocation_usd: Decimal
    gross_exposure_usd: Decimal
    net_exposure_usd: Decimal
    unrealized_pnl_usd: Decimal
    realized_pnl_usd: Decimal
    commissions_usd: Decimal
    net_pnl_usd: Decimal
    return_on_allocated_pct: Decimal


class PortfolioSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    fund_id: str
    business_date: date
    version: int = 1
    is_frozen: bool = False
    frozen_at_utc: datetime | None = None
    frozen_by: str | None = None
    created_at_utc: datetime
    prior_nav_usd: Decimal
    total_cash_usd: Decimal
    total_long_mv_usd: Decimal
    total_short_mv_usd: Decimal
    net_market_value_usd: Decimal
    accrued_fees_usd: Decimal
    nav_usd: Decimal
    nav_per_share: Decimal
    shares_outstanding: Decimal
    daily_pnl_usd: Decimal
    daily_return_pct: Decimal
    positions: list[Position]
    cash_balances: list[CashBalance]
    risk_metrics: RiskMetrics
    strategy_attribution: list[StrategyAttribution]
    ledger_nav_usd: Decimal
    nav_reconciliation_diff_usd: Decimal
    content_hash: str = ""

    @field_validator("created_at_utc", mode="after")
    @classmethod
    def _utc_created(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @field_validator("frozen_at_utc", mode="after")
    @classmethod
    def _utc_frozen(cls, v: datetime | None) -> datetime | None:
        return ensure_utc(v) if v is not None else None

    def compute_content_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude={"content_hash", "is_frozen", "frozen_at_utc", "frozen_by"})
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
