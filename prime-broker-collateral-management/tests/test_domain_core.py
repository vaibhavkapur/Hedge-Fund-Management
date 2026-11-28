"""Core domain tests: reference oracle, duplicate statement, FX change, failed transfer, and no double-counting."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from pb_collateral.errors import DoubleCountingError, DuplicateStatementError, MissingConfigurationError
from pb_collateral.fx import FxTable
from pb_collateral.journal import SQLiteJournalStore
from pb_collateral.models import (
    AssetClass,
    CashBalanceRecord,
    CashBalanceType,
    CollateralTransfer,
    EncumbranceStatus,
    FxRate,
    MarginCallAction,
    PositionRecord,
    TransferStatus,
)
from pb_collateral.service import CollateralManagementService
from pb_collateral.validation import (
    normalize_broker_statement,
    parse_financing_schedules,
    parse_fx_rates,
    parse_internal_snapshot,
    parse_policy_config,
)
from pb_collateral.valuation import evaluate_collateral, verify_no_global_double_counting

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"


class TestDomainCore(unittest.TestCase):
    def setUp(self) -> None:
        self.policies, self.rounding = parse_policy_config(
            json.loads((CONFIG_DIR / "default_policy.json").read_text())
        )
        self.schedules = parse_financing_schedules(
            json.loads((CONFIG_DIR / "financing_rates.json").read_text())
        )
        self.ref_fixture = json.loads((FIXTURES_DIR / "reference_oracle.json").read_text())
        self.fx_table = FxTable(parse_fx_rates(self.ref_fixture["fx_rates"]), rounding_mode=self.rounding)

    def test_reference_oracle_matches_hand_calculation_exactly(self) -> None:
        item = self.ref_fixture["broker_runs"][0]
        stmt = normalize_broker_statement(item["statement"])
        snap = parse_internal_snapshot(item["internal_snapshot"])
        expected = self.ref_fixture["expected_oracle"]["PB-ALPHA"]

        svc = CollateralManagementService(
            policies=self.policies,
            financing_schedules=self.schedules,
            rounding_mode=self.rounding,
        )
        res = svc.process_statement(
            statement=stmt,
            internal_snapshot=snap,
            fx_table=self.fx_table,
            accrual_start_date=date(2026, 3, 13),
            accrual_end_date=date(2026, 3, 16),
        )

        self.assertTrue(res.reconciled_clean)
        self.assertEqual(len(res.breaks), expected["break_count"])
        self.assertEqual(str(res.valuation.total_gross_securities_base), expected["total_gross_securities_base"])
        self.assertEqual(str(res.valuation.eligible_securities_value_base), expected["eligible_securities_value_base"])
        self.assertEqual(str(res.valuation.pledged_margin_collateral_base), expected["pledged_margin_collateral_base"])
        self.assertEqual(
            str(res.valuation.available_unencumbered_collateral_base),
            expected["available_unencumbered_collateral_base"],
        )
        self.assertEqual(str(res.valuation.margin_cash_base), expected["margin_cash_base"])
        self.assertEqual(str(res.valuation.total_posted_collateral_base), expected["total_posted_collateral_base"])
        self.assertEqual(str(res.valuation.total_available_buffer_base), expected["total_available_buffer_base"])
        self.assertEqual(str(res.financing.net_accrual_base), expected["net_accrual_base"])
        self.assertEqual(res.margin_call.action.value, expected["margin_action"])
        self.assertEqual(str(res.margin_call.raw_surplus_or_deficit_base), expected["raw_surplus_or_deficit_base"])

    def test_duplicate_statement_idempotent_skip_and_conflict_rejection(self) -> None:
        item = self.ref_fixture["broker_runs"][0]
        stmt = normalize_broker_statement(item["statement"])
        snap = parse_internal_snapshot(item["internal_snapshot"])

        svc = CollateralManagementService(
            policies=self.policies,
            financing_schedules=self.schedules,
            journal=SQLiteJournalStore(":memory:"),
        )

        res1 = svc.process_statement(
            statement=stmt,
            internal_snapshot=snap,
            fx_table=self.fx_table,
            accrual_start_date=date(2026, 3, 13),
            accrual_end_date=date(2026, 3, 16),
        )
        self.assertFalse(res1.duplicate_skipped)
        self.assertEqual(svc.counters.statements_processed, 1)

        # Exact duplicate statement -> idempotent skip, no double-counting in counters or journal
        res2 = svc.process_statement(
            statement=stmt,
            internal_snapshot=snap,
            fx_table=self.fx_table,
            accrual_start_date=date(2026, 3, 13),
            accrual_end_date=date(2026, 3, 16),
        )
        self.assertTrue(res2.duplicate_skipped)
        self.assertEqual(res2.run_id, res1.run_id)
        self.assertEqual(svc.counters.statements_processed, 1)
        self.assertEqual(svc.counters.duplicate_statements_skipped, 1)

        # Conflicting statement with same statement_id but mutated cash -> DuplicateStatementError
        mutated_raw = json.loads(json.dumps(item["statement"]))
        mutated_raw["cash_balances"][0]["amount"] = "210000.00"
        mutated_stmt = normalize_broker_statement(mutated_raw)
        with self.assertRaises(DuplicateStatementError):
            svc.process_statement(
                statement=mutated_stmt,
                internal_snapshot=snap,
                fx_table=self.fx_table,
                accrual_start_date=date(2026, 3, 13),
                accrual_end_date=date(2026, 3, 16),
            )

        # Proper versioned correction (new statement_id + version=2 + supersedes) -> accepted & tracked
        corrected_raw = json.loads(json.dumps(mutated_raw))
        corrected_raw["statement_id"] = "STMT-PB-ALPHA-20260316-V2"
        corrected_raw["version"] = 2
        corrected_raw["supersedes_statement_id"] = stmt.statement_id
        corrected_stmt = normalize_broker_statement(corrected_raw)
        res3 = svc.process_statement(
            statement=corrected_stmt,
            internal_snapshot=snap,
            fx_table=self.fx_table,
            accrual_start_date=date(2026, 3, 13),
            accrual_end_date=date(2026, 3, 16),
        )
        self.assertFalse(res3.duplicate_skipped)
        self.assertEqual(svc.counters.corrections_versioned, 1)
        versions = svc.journal.list_statement_versions("PB-ALPHA", "ACC-MAIN-01", date(2026, 3, 16))
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[1]["version"], 2)

    def test_fx_change_revalues_collateral_and_triggers_margin_call(self) -> None:
        # EUR sovereign bond pledged at PB-ALPHA (USD base): base haircut 3% + FX mismatch addon 8% = 11%
        pos_eur = PositionRecord(
            position_id="P-EUR-1",
            lot_id="LOT-EUR-SOV-1",
            broker_id="PB-ALPHA",
            account_id="ACC-FX",
            security_id="DE-BUND-5Y",
            asset_class=AssetClass.SOVEREIGN_BOND,
            rating_tier="AA",
            maturity_bucket="0_5Y",
            quantity=Decimal("10000"),
            market_price=Decimal("100.00"),
            currency="EUR",
            encumbrance_status=EncumbranceStatus.PLEDGED_MARGIN,
        )
        policy = self.policies["PB-ALPHA"]

        # Include $1,000,000 USD margin cash so SOVEREIGN_BOND remains within its 60% concentration cap
        cash_usd = CashBalanceRecord(
            balance_id="CB-M-USD",
            broker_id="PB-ALPHA",
            account_id="ACC-FX",
            currency="USD",
            amount=Decimal("1000000.00"),
            balance_type=CashBalanceType.MARGIN_CASH,
        )

        # Day 1: EUR/USD = 1.1500 -> gross USD = 1,150,000; effective haircut 11% -> net = 1,023,500.00 (+1M cash = 2,023,500)
        fx_strong_eur = FxTable([FxRate("EUR", "USD", Decimal("1.1500"), date(2026, 3, 16))])
        val1 = evaluate_collateral(
            broker_id="PB-ALPHA",
            account_id="ACC-FX",
            business_date=date(2026, 3, 16),
            positions=[pos_eur],
            cash_balances=[cash_usd],
            policy=policy,
            fx_table=fx_strong_eur,
        )
        self.assertEqual(val1.pledged_margin_collateral_base, Decimal("1023500.00"))
        self.assertEqual(val1.total_posted_collateral_base, Decimal("2023500.00"))
        self.assertEqual(val1.positions[0].effective_haircut_pct, Decimal("0.11"))

        # Day 2: EUR weakens sharply to EUR/USD = 1.0200 -> gross USD = 1,020,000; net = 907,800.00 (-115,700 USD drop!)
        fx_weak_eur = FxTable([FxRate("EUR", "USD", Decimal("1.0200"), date(2026, 3, 17))])
        val2 = evaluate_collateral(
            broker_id="PB-ALPHA",
            account_id="ACC-FX",
            business_date=date(2026, 3, 17),
            positions=[pos_eur],
            cash_balances=[cash_usd],
            policy=policy,
            fx_table=fx_weak_eur,
        )
        self.assertEqual(val2.pledged_margin_collateral_base, Decimal("907800.00"))
        self.assertEqual(val2.total_posted_collateral_base, Decimal("1907800.00"))
        self.assertEqual(
            val1.total_posted_collateral_base - val2.total_posted_collateral_base,
            Decimal("115700.00"),
        )

    def test_concentration_limit_trim_applied_deterministically(self) -> None:
        # Standalone EUR Sovereign bond (60% cap in PB-ALPHA policy) with no other collateral is trimmed to 60%
        pos_eur = PositionRecord(
            position_id="P-EUR-CONC",
            lot_id="LOT-EUR-CONC-1",
            broker_id="PB-ALPHA",
            account_id="ACC-CONC",
            security_id="DE-BUND-5Y",
            asset_class=AssetClass.SOVEREIGN_BOND,
            rating_tier="AA",
            maturity_bucket="0_5Y",
            quantity=Decimal("10000"),
            market_price=Decimal("100.00"),
            currency="EUR",
            encumbrance_status=EncumbranceStatus.PLEDGED_MARGIN,
        )
        fx = FxTable([FxRate("EUR", "USD", Decimal("1.1500"), date(2026, 3, 16))])
        val = evaluate_collateral(
            broker_id="PB-ALPHA",
            account_id="ACC-CONC",
            business_date=date(2026, 3, 16),
            positions=[pos_eur],
            cash_balances=[],
            policy=self.policies["PB-ALPHA"],
            fx_table=fx,
        )
        # Pre-trim net = 1,023,500.00; 60% cap = 614,100.00; trim = 409,400.00
        self.assertEqual(val.positions[0].concentration_trim_base, Decimal("409400.00"))
        self.assertEqual(val.total_posted_collateral_base, Decimal("614100.00"))

    def test_failed_transfer_quarantined_and_prevents_double_counting(self) -> None:
        raw_stmt = {
            "schema_version": "1.0.0",
            "statement_id": "STMT-FAIL-XFER-V1",
            "version": 1,
            "broker_id": "PB-BETA",
            "account_id": "ACC-EUR-1",
            "business_date": "2026-03-17",
            "received_at": "2026-03-17T21:00:00Z",
            "base_currency": "EUR",
            "positions": [
                {
                    "position_id": "P1",
                    "lot_id": "LOT-SOV-OK",
                    "security_id": "FR-OAT",
                    "asset_class": "SOVEREIGN_BOND",
                    "rating_tier": "AA",
                    "maturity_bucket": "0_5Y",
                    "quantity": "5000",
                    "market_price": "100.00",
                    "currency": "EUR",
                    "encumbrance_status": "PLEDGED_MARGIN",
                },
                {
                    "position_id": "P2",
                    "lot_id": "LOT-SOV-FAIL",
                    "security_id": "DE-BUND",
                    "asset_class": "SOVEREIGN_BOND",
                    "rating_tier": "AA",
                    "maturity_bucket": "0_5Y",
                    "quantity": "3000",
                    "market_price": "100.00",
                    "currency": "EUR",
                    "encumbrance_status": "PENDING_TRANSFER_IN",
                },
            ],
            "cash_balances": [
                {"balance_id": "C1", "currency": "EUR", "amount": "50000.00", "balance_type": "MARGIN_CASH"}
            ],
            "margin_requirement": {
                "initial_margin": "600000.00",
                "variation_margin": "50000.00",
                "house_addon": "0.00",
                "currency": "EUR",
            },
        }
        raw_snap = {
            "schema_version": "1.0.0",
            "snapshot_id": "SNAP-FAIL-XFER-V1",
            "version": 1,
            "broker_id": "PB-BETA",
            "account_id": "ACC-EUR-1",
            "business_date": "2026-03-17",
            "positions": [raw_stmt["positions"][0]],
            "cash_balances": raw_stmt["cash_balances"],
            "transfers": [
                {
                    "transfer_id": "XFER-999",
                    "from_broker_id": "PB-ALPHA",
                    "to_broker_id": "PB-BETA",
                    "lot_id": "LOT-SOV-FAIL",
                    "security_id": "DE-BUND",
                    "quantity": "3000",
                    "currency": "EUR",
                    "market_value": "300000.00",
                    "status": "FAILED",
                    "trade_date": "2026-03-16",
                    "settlement_date": "2026-03-17",
                    "failure_reason": "COUNTERPARTY_DK",
                }
            ],
        }
        svc = CollateralManagementService(policies=self.policies, financing_schedules=self.schedules)
        res = svc.process_statement(
            statement=normalize_broker_statement(raw_stmt),
            internal_snapshot=parse_internal_snapshot(raw_snap),
            fx_table=self.fx_table,
            accrual_start_date=date(2026, 3, 16),
            accrual_end_date=date(2026, 3, 17),
        )

        # LOT-SOV-FAIL is quarantined (0 collateral value); only LOT-SOV-OK (500,000 * 0.975 = 487,500) + 50,000 cash = 537,500
        self.assertEqual(res.valuation.quarantined_failed_transfer_lots, ("LOT-SOV-FAIL",))
        self.assertEqual(res.valuation.total_posted_collateral_base, Decimal("537500.00"))
        # Margin requirement is 650,000 -> shortfall of 112,500 -> rounded up to 113,000 EUR call!
        self.assertEqual(res.margin_call.action, MarginCallAction.MARGIN_CALL)
        self.assertTrue(res.margin_call.flagged_before_settlement)
        self.assertEqual(res.margin_call.call_or_return_amount_base, Decimal("113000.00"))
        self.assertTrue(any(b.category == "FAILED_TRANSFER_BREAK" for b in res.breaks))

    def test_global_double_counting_across_brokers_raises(self) -> None:
        shared_lot = PositionRecord(
            position_id="P-SHARED",
            lot_id="LOT-SHARED-001",
            broker_id="PB-ALPHA",
            account_id="ACC-1",
            security_id="US912828YV68",
            asset_class=AssetClass.US_TREASURY,
            rating_tier="AAA",
            maturity_bucket="0_5Y",
            quantity=Decimal("1000"),
            market_price=Decimal("100.00"),
            currency="USD",
            encumbrance_status=EncumbranceStatus.PLEDGED_MARGIN,
        )
        duplicate_at_beta = replace(shared_lot, broker_id="PB-BETA", account_id="ACC-2")
        with self.assertRaises(DoubleCountingError):
            verify_no_global_double_counting(
                {"PB-ALPHA": [shared_lot], "PB-BETA": [duplicate_at_beta]}
            )

    def test_missing_configuration_raises_explicit_error(self) -> None:
        item = self.ref_fixture["broker_runs"][0]
        stmt = normalize_broker_statement(item["statement"])
        snap = parse_internal_snapshot(item["internal_snapshot"])
        # Service configured without PB-ALPHA policy
        svc = CollateralManagementService(
            policies={"PB-BETA": self.policies["PB-BETA"]},
            financing_schedules=self.schedules,
        )
        with self.assertRaises(MissingConfigurationError):
            svc.process_statement(statement=stmt, internal_snapshot=snap, fx_table=self.fx_table)

        # Missing FX rate for CHF/USD
        fx_empty = FxTable([])
        with self.assertRaises(MissingConfigurationError):
            fx_empty.get_rate("CHF", "USD")


if __name__ == "__main__":
    unittest.main()
