"""Integration tests: mid-period capital flows, versioned corrections, SQLite immutability, replay, and approvals."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from hedge_fund_fee_engine.errors import ApprovalError, StorageError
from hedge_fund_fee_engine.models import (
    ApprovalStatus,
    CapitalFlow,
    CapitalFlowType,
    EngineConfig,
    FeeAgreement,
    InvestorLot,
    JournalEntryType,
    NavSnapshot,
)
from hedge_fund_fee_engine.reporting import run_scenario_fixture
from hedge_fund_fee_engine.service import FeeAccountingService
from hedge_fund_fee_engine.storage import SqliteFeeStore


FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "default_engine_config.json"


class TestFlowsCorrectionsIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.config = EngineConfig.from_json_file(DEFAULT_CONFIG)

    def test_mid_period_flows_and_versioned_correction_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ledger.sqlite"
            store = SqliteFeeStore(db_path)
            service = FeeAccountingService(config=self.config, store=store)

            report = run_scenario_fixture(
                FIXTURES_DIR / "mid_period_flows_and_correction.json", service
            )
            self.assertTrue(report["all_passed"], msg=report["comparisons"])
            self.assertEqual(report["run_count"], 2)

            v1_run = report["runs"][0]
            v2_run = report["runs"][1]
            self.assertEqual(v1_run["version"], 1)
            self.assertEqual(v2_run["version"], 2)
            self.assertEqual(v2_run["supersedes_run_id"], v1_run["run_id"])

            # Prior run is marked SUPERSEDED in SQLite while preserving all prior journal entries
            v1_stored = store.get_run_record(v1_run["run_id"])
            self.assertEqual(v1_stored["status"], ApprovalStatus.SUPERSEDED.value)
            v1_entries = store.get_journal_entries_for_run(v1_run["run_id"])
            self.assertGreater(len(v1_entries), 0)

            # v2 contains exact reversal entries for every v1 entry plus corrected entries
            v2_entries = store.get_journal_entries_for_run(v2_run["run_id"])
            reversals = [e for e in v2_entries if e.entry_type == JournalEntryType.CORRECTION_REVERSAL]
            self.assertEqual(len(reversals), len(v1_entries))
            self.assertEqual(
                {r.supersedes_entry_id for r in reversals},
                {e.entry_id for e in v1_entries},
            )

            # Replay determinism for both v1 and v2 after reopening DB (simulated restart)
            store.close()
            reopened_store = SqliteFeeStore(db_path)
            reopened_service = FeeAccountingService(config=self.config, store=reopened_store)
            for rid in (v1_run["run_id"], v2_run["run_id"]):
                replay = reopened_service.replay_run(rid)
                self.assertTrue(replay["matches"], msg=replay)

            # Immutability triggers block UPDATE and DELETE on journal_entries and journal_lines
            with self.assertRaises(sqlite3.IntegrityError):
                reopened_store._conn.execute("DELETE FROM journal_lines")
            with self.assertRaises(sqlite3.IntegrityError):
                reopened_store._conn.execute("UPDATE journal_entries SET description = 'tampered'")

            # Approval lifecycle on v2: DRAFT -> APPROVED; double-approve rejected
            apr = reopened_service.approve_run(v2_run["run_id"], actor="controller@fund", comment="Audit verified")
            self.assertEqual(apr.status, ApprovalStatus.APPROVED)
            with self.assertRaises(ApprovalError) as ctx:
                reopened_service.approve_run(v2_run["run_id"], actor="controller@fund")
            self.assertEqual(ctx.exception.code, "INVALID_APPROVAL_TRANSITION")
            reopened_store.close()

    def test_duplicate_accrual_run_blocked(self) -> None:
        ref_data = json.loads((FIXTURES_DIR / "reference_basic_2_and_20.json").read_text(encoding="utf-8"))
        agr = FeeAgreement.from_dict(ref_data["agreement"])
        snap = NavSnapshot.from_dict(ref_data["periods"][0]["snapshot"])
        lots = [InvestorLot.from_dict(d) for d in ref_data["initial_lots"]]

        store = SqliteFeeStore(Path(":memory:"))
        service = FeeAccountingService(config=self.config, store=store)
        service.run_period(agreement=agr, snapshot=snap, lots=lots, version=1, persist=True)

        with self.assertRaises(StorageError) as ctx:
            service.run_period(agreement=agr, snapshot=snap, lots=lots, version=1, persist=True)
        self.assertEqual(ctx.exception.code, "DUPLICATE_RUN")
        store.close()

    def test_underwater_partial_redemption_reduces_loss_carryforward_proportionally(self) -> None:
        ref_data = json.loads((FIXTURES_DIR / "reference_basic_2_and_20.json").read_text(encoding="utf-8"))
        agr = FeeAgreement.from_dict(ref_data["agreement"])
        lot = InvestorLot(
            lot_id="LOT-UW",
            investor_id="INV-UW",
            share_class="CLASS_A",
            series_id="S1",
            subscription_date=date(2024, 12, 31),
            units=Decimal("10000.000000"),
            opening_nav_per_unit=Decimal("90.000000"),
            high_water_mark=Decimal("100.000000"),
            hurdle_base_nav=Decimal("100.000000"),
            last_crystallization_date=date(2024, 12, 31),
        )
        snap = NavSnapshot(
            snapshot_id="SNAP-UW",
            period_start_date=date(2024, 12, 31),
            valuation_date=date(2025, 3, 31),
            share_class="CLASS_A",
            currency="USD",
            gross_nav_per_unit=Decimal("90.000000"),
        )
        # Redeem 50% (5,000 units) mid-period while underwater
        flow = CapitalFlow(
            flow_id="FLOW-UW-RED",
            investor_id="INV-UW",
            lot_id="LOT-UW",
            share_class="CLASS_A",
            flow_date=date(2025, 2, 14),
            flow_type=CapitalFlowType.REDEMPTION,
            nav_per_unit=Decimal("90.000000"),
            currency="USD",
            units=Decimal("5000.000000"),
        )
        service = FeeAccountingService(config=self.config)
        res = service.run_period(agreement=agr, snapshot=snap, lots=[lot], capital_flows=[flow], persist=False)

        rem_lot = res.updated_lots[0]
        self.assertEqual(rem_lot.units, Decimal("5000.000000"))
        # Per-unit HWM remains 100.00 while dollar loss carryforward reflects only remaining 5,000 units
        self.assertEqual(rem_lot.high_water_mark, Decimal("100.000000"))
        # Remaining lot: gross=450,000, mgmt fee=450,000*0.02*0.25=2,250 -> net=447,750 (89.55/u)
        # Loss carryforward = (100 - 89.55) * 5000 = 52,250.00 (half of full-lot carryforward!)
        self.assertEqual(rem_lot.loss_carryforward_amount, Decimal("52250.00"))


if __name__ == "__main__":
    unittest.main()
