"""Multi-way reconciliation domain contracts (IBOR vs Prime Broker / Custodian)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hf_platform.domain.enums import BreakType, Currency, Severity, TradeSide
from hf_platform.domain.master import ensure_utc


class ExternalPositionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    instrument_id: str
    quantity: Decimal
    market_price: Decimal
    currency: Currency
    source: str
    as_of_utc: datetime

    @field_validator("as_of_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)


class ExternalCashRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    currency: Currency
    balance: Decimal
    source: str
    as_of_utc: datetime

    @field_validator("as_of_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)


class ExternalFillRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    broker_ref: str
    account_id: str
    instrument_id: str
    side: TradeSide
    quantity: Decimal
    price: Decimal
    currency: Currency
    commission: Decimal = Decimal("0.00")
    executed_at_utc: datetime

    @field_validator("executed_at_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)


class ReconciliationBreak(BaseModel):
    model_config = ConfigDict(frozen=True)

    break_id: str
    reconciliation_id: str
    fund_id: str
    business_date: date
    account_id: str
    break_type: BreakType
    severity: Severity
    instrument_id: str | None = None
    currency: Currency | None = None
    broker_ref: str | None = None
    internal_value: str
    external_value: str
    difference: str
    financial_impact_usd: Decimal
    description: str
    detected_at_utc: datetime

    @field_validator("detected_at_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)


class ReconciliationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    reconciliation_id: str
    fund_id: str
    business_date: date
    run_at_utc: datetime
    positions_checked: int
    cash_balances_checked: int
    fills_checked: int
    breaks: list[ReconciliationBreak] = Field(default_factory=list)
    total_break_impact_usd: Decimal = Decimal("0.00")
    critical_break_count: int = 0
    is_clean: bool = True

    @field_validator("run_at_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)
