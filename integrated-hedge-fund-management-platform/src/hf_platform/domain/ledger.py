"""Double-entry general ledger domain contracts with strict balance invariants."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hf_platform.domain.enums import Currency, LedgerAccountType
from hf_platform.domain.master import ensure_utc
from hf_platform.errors import LedgerBalanceError


class JournalLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    ledger_account: LedgerAccountType
    currency: Currency
    debit: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))
    credit: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0"))
    instrument_id: str | None = None
    strategy_id: str | None = None
    memo: str = ""

    @model_validator(mode="after")
    def _one_sided_nonzero(self) -> JournalLine:
        if self.debit == Decimal("0") and self.credit == Decimal("0"):
            raise LedgerBalanceError(
                "Journal line must have a positive debit or credit amount",
                details={"account_id": self.account_id, "ledger_account": self.ledger_account.value},
            )
        if self.debit > Decimal("0") and self.credit > Decimal("0"):
            raise LedgerBalanceError(
                "Journal line cannot have both debit and credit populated simultaneously",
                details={"account_id": self.account_id, "debit": str(self.debit), "credit": str(self.credit)},
            )
        return self


class JournalEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry_id: str
    fund_id: str
    business_date: date
    description: str
    source_ref: str
    lines: list[JournalLine]
    created_by: str
    created_at_utc: datetime = Field(default_factory=lambda: datetime.now(UTC))
    version: int = 1
    supersedes_entry_id: str | None = None

    @field_validator("created_at_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @model_validator(mode="after")
    def _verify_balanced_by_currency(self) -> JournalEntry:
        if len(self.lines) < 2:
            raise LedgerBalanceError(
                f"Journal entry {self.entry_id} must contain at least 2 lines",
                details={"entry_id": self.entry_id, "line_count": len(self.lines)},
            )
        debits: dict[Currency, Decimal] = defaultdict(lambda: Decimal("0"))
        credits: dict[Currency, Decimal] = defaultdict(lambda: Decimal("0"))
        for line in self.lines:
            debits[line.currency] += line.debit
            credits[line.currency] += line.credit
        imbalances: dict[str, dict[str, str]] = {}
        for ccy in sorted(set(debits) | set(credits), key=lambda c: c.value):
            diff = debits[ccy] - credits[ccy]
            if diff != Decimal("0"):
                imbalances[ccy.value] = {
                    "total_debit": str(debits[ccy]),
                    "total_credit": str(credits[ccy]),
                    "difference": str(diff),
                }
        if imbalances:
            raise LedgerBalanceError(
                f"Journal entry {self.entry_id} is unbalanced across currencies: {list(imbalances.keys())}",
                details={"entry_id": self.entry_id, "imbalances": imbalances},
            )
        return self


class TrialBalanceLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    ledger_account: LedgerAccountType
    currency: Currency
    total_debit: Decimal
    total_credit: Decimal
    net_balance: Decimal


class TrialBalance(BaseModel):
    model_config = ConfigDict(frozen=True)

    fund_id: str
    business_date: date
    generated_at_utc: datetime
    lines: list[TrialBalanceLine]
    totals_by_currency: dict[str, dict[str, Decimal]]
    is_balanced: bool
    entry_count: int
