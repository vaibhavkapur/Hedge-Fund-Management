"""Fund, account, strategy, instrument, counterparty, and principal master contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hf_platform.domain.enums import AccountType, AssetClass, Currency, EntityStatus, Role


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class Fund(BaseModel):
    model_config = ConfigDict(frozen=True)

    fund_id: str = Field(min_length=3)
    name: str = Field(min_length=2)
    base_currency: Currency = Currency.USD
    inception_date: date
    initial_nav_usd: Decimal = Field(gt=Decimal("0"))
    shares_outstanding: Decimal = Field(gt=Decimal("0"))
    administrator_name: str
    status: EntityStatus = EntityStatus.ACTIVE
    version: int = 1


class Counterparty(BaseModel):
    model_config = ConfigDict(frozen=True)

    counterparty_id: str = Field(min_length=3)
    name: str
    lei: str
    credit_rating: str = "NR"


class Account(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str = Field(min_length=3)
    fund_id: str
    counterparty_id: str
    name: str
    account_type: AccountType
    base_currency: Currency = Currency.USD
    initial_cash: dict[Currency, Decimal] = Field(default_factory=dict)
    status: EntityStatus = EntityStatus.ACTIVE
    version: int = 1


class Strategy(BaseModel):
    model_config = ConfigDict(frozen=True)

    strategy_id: str = Field(min_length=3)
    fund_id: str
    name: str
    asset_focus: str
    pm_user_id: str
    capital_allocation_usd: Decimal = Field(ge=Decimal("0"))
    status: EntityStatus = EntityStatus.ACTIVE
    version: int = 1


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument_id: str = Field(min_length=3)
    symbol: str
    name: str
    asset_class: AssetClass
    sector: str
    currency: Currency
    multiplier: Decimal = Field(default=Decimal("1"), gt=Decimal("0"))
    beta: Decimal = Decimal("1.0")
    annualized_vol: Decimal = Field(default=Decimal("0.20"), ge=Decimal("0"))


class UserPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=3)
    name: str
    role: Role


class MasterDataBundle(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "1.0.0"
    as_of_utc: datetime
    funds: list[Fund]
    counterparties: list[Counterparty]
    accounts: list[Account]
    strategies: list[Strategy]
    instruments: list[Instrument]
    users: list[UserPrincipal]

    @field_validator("as_of_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)
