"""Tests for collateral substitution review, pre-settlement shortfall gating, immutable journal, and replay."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from pb_collateral.errors import DoubleCountingError, PersistenceError
from pb_collateral.fx import FxTable
from pb_collateral.journal import PostgresJournalAdapter, SQLiteJournalStore
from pb_collateral.models import (
    ApprovalStatus,
    AssetClass,
    EncumbranceStatus,
    PositionRecord,
    SubstitutionRequest,
)
from pb_collateral.service import CollateralManagementService
from pb_collateral.validation import (
    normalize_broker_statement,
    parse_financing_schedules,
    parse_fx_rates,
    parse_internal_snapshot,
    parse_policy_config,
    parse_substitution_request,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"


class TestSubstitutionAndJournal(unittest.TestCase):
    def setUp(self) -> None:
        self.policies, self.rounding = parse_policy_config(
            json.loads((CONFIG_DIR / "default_policy.json").read_text())
        )
        self.schedules = parse_financing_schedules(
            json.loads((CONFIG_DIR / "financing_rates.json").read_text())
        )
        self.demo = json.loads((FIXTURES_DIR / "demo_multi_broker_bundle.json").read_text())
        self.fx_table = FxTable(parse_fx_rates(self.demo["fx_rates"]), rounding_mode=self.rounding)

    def test_substitution_approved_and_shortfall_blocked_before_settlement(self) -> None:
        pb_alpha_stmt = normalize_broker_statement(self.demo["broker_runs"][0]["statement"])
        svc = CollateralManagementService(policies=self.policies, financing_schedules=self.schedules)

        # 1. Valid substitution: release EUR Sovereign ($489,500 net) and pledge $550k UST ($539,000 net)
        req_ok = parse_substitution_request(self.demo["substitutions"][0]["request"])
        rev_ok = svc.review_substitution(
            request=req_ok,
            current_positions=pb_alpha_stmt.positions,
            cash_balances=pb_alpha_stmt.cash_balances,
            margin_requirement=pb_alpha_stmt.margin_requirement,
            fx_table=self.fx_table,
        )
        self.assertTrue(rev_ok.settlement_permitted)
        self.assertFalse(rev_ok.shortfall_flagged_before_settlement)
        self.assertEqual(rev_ok.approval_status, ApprovalStatus.APPROVED)
        self.assertEqual(rev_ok.required_top_up_base, Decimal("0.00"))
        self.assertEqual(rev_ok.post_posted_collateral_base, Decimal("1769000.00"))
        # Confirm released lot is now UNENCUMBERED and new lot is PLEDGED_MARGIN
        post_map = {p.lot_id: p.encumbrance_status for p in rev_ok.post_settlement_positions}
        self.assertEqual(post_map["LOT-A-SOV-EUR-01"], EncumbranceStatus.UNENCUMBERED)
        self.assertEqual(post_map["LOT-A-UST-NEW-01"], EncumbranceStatus.PLEDGED_MARGIN)

        # 2. Bad substitution: release $1M UST ($980k net) and offer ineligible HY bond ($0 net).
        # Remaining pre-cap pool = $489,500 SOV + $250,000 cash = $739,500.
        # Because SOV exceeds its 60% concentration cap ($443,700) by $45,800, post posted collateral = $693,700.00!
        # Against $1,650,000 requirement -> shortfall / required top-up = $956,300.00.
        req_bad = parse_substitution_request(self.demo["substitutions"][1]["request"])
        rev_bad = svc.review_substitution(
            request=req_bad,
            current_positions=pb_alpha_stmt.positions,
            cash_balances=pb_alpha_stmt.cash_balances,
            margin_requirement=pb_alpha_stmt.margin_requirement,
            fx_table=self.fx_table,
        )
        self.assertFalse(rev_bad.settlement_permitted)
        self.assertTrue(rev_bad.shortfall_flagged_before_settlement)
        self.assertEqual(rev_bad.approval_status, ApprovalStatus.AUTO_FLAGGED_SHORTFALL)
        self.assertEqual(rev_bad.post_posted_collateral_base, Decimal("693700.00"))
        self.assertEqual(rev_bad.required_top_up_base, Decimal("956300.00"))
        self.assertEqual(rev_bad.rejected_lots, ("LOT-A-HY-REPLACE-01",))
        # Original positions remain untouched (settlement did not occur)
        self.assertEqual(rev_bad.post_settlement_positions, pb_alpha_stmt.positions)

    def test_substitution_rejects_externally_encumbered_lot_double_counting(self) -> None:
        pb_alpha_stmt = normalize_broker_statement(self.demo["broker_runs"][0]["statement"])
        svc = CollateralManagementService(policies=self.policies, financing_schedules=self.schedules)
        req = SubstitutionRequest(
            request_id="SUB-DBL-COUNT",
            broker_id="PB-ALPHA",
            account_id="ACC-FUND-USD",
            business_date=date(2026, 3, 17),
            release_lot_ids=("LOT-A-SOV-EUR-01",),
            pledge_positions=(
                PositionRecord(
                    position_id="P-EXT",
                    lot_id="LOT-B-SOV-01",  # Already pledged at PB-BETA!
                    broker_id="PB-ALPHA",
                    account_id="ACC-FUND-USD",
                    security_id="FR0013407236",
                    asset_class=AssetClass.SOVEREIGN_BOND,
                    rating_tier="AA",
                    maturity_bucket="0_5Y",
                    quantity=Decimal("6000"),
                    market_price=Decimal("100.00"),
                    currency="EUR",
                    encumbrance_status=EncumbranceStatus.UNENCUMBERED,
                ),
            ),
            requested_by="ops",
        )
        with self.assertRaises(DoubleCountingError):
            svc.review_substitution(
                request=req,
                current_positions=pb_alpha_stmt.positions,
                cash_balances=pb_alpha_stmt.cash_balances,
                margin_requirement=pb_alpha_stmt.margin_requirement,
                fx_table=self.fx_table,
                externally_encumbered_lots={"LOT-B-SOV-01"},
            )

    def test_sqlite_journal_immutability_and_persistent_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "journal.db")
            store = SQLiteJournalStore(db_path)
            svc = CollateralManagementService(
                policies=self.policies,
                financing_schedules=self.schedules,
                journal=store,
            )
            item = self.demo["broker_runs"][0]
            res = svc.process_statement(
                statement=normalize_broker_statement(item["statement"]),
                internal_snapshot=parse_internal_snapshot(item["internal_snapshot"]),
                fx_table=self.fx_table,
                accrual_start_date=date(2026, 3, 16),
                accrual_end_date=date(2026, 3, 17),
            )

            # Verify append-only triggers reject UPDATE and DELETE
            with self.assertRaises(sqlite3.IntegrityError):
                store.execute_raw("UPDATE statement_versions SET base_currency = 'EUR'")
            with self.assertRaises(sqlite3.IntegrityError):
                store.execute_raw("DELETE FROM reconciliation_runs")
            store.close()

            # Simulate restart/rerun by reopening the persisted SQLite journal
            reopened = SQLiteJournalStore(db_path)
            replayed = reopened.get_reconciliation_run(res.run_id)
            reopened.close()
            self.assertIsNotNone(replayed)
            self.assertEqual(replayed["run_id"], res.run_id)
            self.assertEqual(replayed["valuation"]["total_posted_collateral_base"], "1719500.00")

    def test_postgres_adapter_schema_available_and_missing_driver_errors_cleanly(self) -> None:
        sql = PostgresJournalAdapter.get_schema_sql()
        self.assertIn("CREATE TABLE IF NOT EXISTS statement_versions", sql)
        self.assertIn("reject_journal_mutation", sql)
        with self.assertRaises(PersistenceError):
            PostgresJournalAdapter("postgresql://localhost/nonexistent")


if __name__ == "__main__":
    unittest.main()
