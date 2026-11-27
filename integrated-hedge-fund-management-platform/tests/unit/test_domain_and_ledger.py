"""Unit tests for master data contracts, double-entry ledger invariants, and execution controls."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from hf_platform.domain import (
    Currency,
    JournalEntry,
    JournalLine,
    LedgerAccountType,
    MasterDataBundle,
)
from hf_platform.errors import DomainValidationError, ExecutionDisabledError, LedgerBalanceError
from hf_platform.services.platform import HedgeFundPlatform


def test_master_data_loaded_and_referentially_valid(platform: HedgeFundPlatform) -> None:
    assert len(platform.master.list_funds()) == 1
    assert len(platform.master.list_accounts("FUND_SAAT_MS")) == 2
    assert len(platform.master.list_strategies("FUND_SAAT_MS")) == 2
    assert len(platform.master.list_instruments()) == 7
    assert len(platform.master.list_users()) == 9


def test_master_data_rejects_broken_foreign_key(platform: HedgeFundPlatform, project_root) -> None:
    import json

    raw = json.loads((project_root / "data" / "fixtures" / "master_data.json").read_text())
    raw["accounts"][0]["fund_id"] = "FUND_DOES_NOT_EXIST"
    bundle = MasterDataBundle.model_validate(raw)
    with pytest.raises(DomainValidationError, match="unknown fund"):
        platform.master.register_bundle(bundle)


def test_journal_entry_enforces_debit_equals_credit_per_currency() -> None:
    with pytest.raises(LedgerBalanceError):
        JournalEntry(
            entry_id="JE_BAD_1",
            fund_id="FUND_SAAT_MS",
            business_date=date(2026, 11, 25),
            description="Unbalanced test",
            source_ref="TEST",
            created_by="usr_acct_01",
            lines=[
                JournalLine(
                    account_id="ACCT_PB_ALPHA_EQ",
                    ledger_account=LedgerAccountType.CASH,
                    currency=Currency.USD,
                    debit=Decimal("100.00"),
                ),
                JournalLine(
                    account_id="ACCT_PB_ALPHA_EQ",
                    ledger_account=LedgerAccountType.DIVIDEND_INCOME,
                    currency=Currency.USD,
                    credit=Decimal("99.99"),
                ),
            ],
        )


def test_journal_line_cannot_have_both_debit_and_credit() -> None:
    with pytest.raises(LedgerBalanceError):
        JournalLine(
            account_id="ACCT_PB_ALPHA_EQ",
            ledger_account=LedgerAccountType.CASH,
            currency=Currency.USD,
            debit=Decimal("10.00"),
            credit=Decimal("10.00"),
        )


def test_opening_capital_and_trial_balance_idempotent(platform: HedgeFundPlatform) -> None:
    first = platform.ledger.ensure_opening_capital("FUND_SAAT_MS", actor_id="usr_acct_01")
    second = platform.ledger.ensure_opening_capital("FUND_SAAT_MS", actor_id="usr_acct_01")
    assert len(first) == 3  # Alpha USD, Alpha EUR, Beta USD
    assert len(second) == 0
    tb = platform.ledger.compute_trial_balance("FUND_SAAT_MS", date(2026, 11, 25))
    assert tb.is_balanced is True
    assert tb.totals_by_currency["USD"]["total_debit"] == Decimal("9460000.00")
    assert tb.totals_by_currency["EUR"]["total_debit"] == Decimal("500000.00")


def test_order_execution_disabled_by_default(platform: HedgeFundPlatform) -> None:
    assert platform.config.execution_controls.order_execution_enabled is False
    with pytest.raises(ExecutionDisabledError) as exc_info:
        platform.submit_order(actor_id="usr_pm_01", fund_id="FUND_SAAT_MS", symbol="AAPL", quantity="100")
    assert exc_info.value.code == "ORDER_EXECUTION_DISABLED"
    entries = platform.audit.list_entries()
    assert any(e.action == "execution.submit_order" and e.outcome == "DENIED" for e in entries)
