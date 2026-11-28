"""Integration tests: cross-report reconciliation, cross-report mismatch detection, reproducibility, and replay."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from regulatory_reporting_engine.config import load_config
from regulatory_reporting_engine.engine import RegulatoryReportingEngine
from regulatory_reporting_engine.errors import CrossReportReconciliationError
from regulatory_reporting_engine.models import GovernedSnapshot
from regulatory_reporting_engine.store import SqliteJournalStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"


class TestReconciliationAndIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)
        self.engine = RegulatoryReportingEngine(self.config, store=SqliteJournalStore(":memory:"))
        self.ref_raw = json.loads((FIXTURES_DIR / "reference_snapshot_q1_2026.json").read_text(encoding="utf-8"))
        self.ref_snap = GovernedSnapshot.from_dict(self.ref_raw)

    def tearDown(self) -> None:
        self.engine.close()

    def test_cross_report_reconciliation_passes_for_consistent_snapshot(self) -> None:
        us_rpt = self.engine.generate_report(
            self.ref_snap, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
        )
        eu_rpt = self.engine.generate_report(
            self.ref_snap, profile_id="EU_AIFMD_ANNEX4_ILLUSTRATIVE", schema_version="1.0.0"
        )
        rec = self.engine.reconcile_reports(us_rpt, eu_rpt)
        self.assertTrue(rec.passed)
        self.assertEqual(len(rec.checks), 6)
        self.assertEqual(self.engine.counters["reconciliations_passed"], 1)

    def test_cross_report_totals_mismatch_raises_error(self) -> None:
        """Required scenario: Cross-report totals mismatch detected with structured diff details."""
        us_rpt = self.engine.generate_report(
            self.ref_snap, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
        )
        # Generate EU report from a drifted snapshot (different NAV & gross exposure)
        drifted_raw = json.loads(
            (FIXTURES_DIR / "reference_snapshot_q1_2026_corrected.json").read_text(encoding="utf-8")
        )
        drifted_snap = GovernedSnapshot.from_dict(drifted_raw)
        eu_drifted = self.engine.generate_report(
            drifted_snap, profile_id="EU_AIFMD_ANNEX4_ILLUSTRATIVE", schema_version="1.0.0"
        )

        with self.assertRaises(CrossReportReconciliationError) as ctx:
            self.engine.reconcile_reports(us_rpt, eu_drifted)
        self.assertEqual(ctx.exception.code, "REG-3001")
        mismatched_names = {m["check_name"] for m in ctx.exception.details["mismatches"]}
        self.assertIn("cross_report_nav_total", mismatched_names)
        self.assertIn("cross_report_gross_exposure_total", mismatched_names)
        self.assertEqual(self.engine.counters["reconciliations_failed"], 1)

        # Also verify diagnostic non-raising mode returns structured report
        diag = self.engine.reconcile_reports(us_rpt, eu_drifted, fail_on_mismatch=False)
        self.assertFalse(diag.passed)

    def test_internal_asset_class_additivity_mismatch_detected(self) -> None:
        """If an exposure uses FX asset_class not covered by EU 3-bucket schedule, additivity check catches it."""
        raw = copy.deepcopy(self.ref_raw)
        raw["snapshot_id"] = "SNAP-SYNTH-FX-UNMAPPED-001"
        raw["exposures"].append(
            {
                "exposure_id": "EXP-004",
                "asset_class": "FX",
                "counterparty_lei": "000000SYNTHCP0000287",
                "currency": "USD",
                "long_exposure_base": "5000000.00",
                "short_exposure_base": "0.00",
                "gross_exposure_base": "5000000.00",
                "net_exposure_base": "5000000.00",
            }
        )
        snap = GovernedSnapshot.from_dict(raw)
        us_rpt = self.engine.build_draft_report_pure(
            snap, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
        )
        eu_rpt = self.engine.build_draft_report_pure(
            snap, profile_id="EU_AIFMD_ANNEX4_ILLUSTRATIVE", schema_version="1.0.0"
        )
        with self.assertRaises(CrossReportReconciliationError) as ctx:
            self.engine.reconcile_reports(us_rpt, eu_rpt)
        names = {m["check_name"] for m in ctx.exception.details["mismatches"]}
        self.assertIn("internal_asset_class_sum_EU_AIFMD_ANNEX4_ILLUSTRATIVE", names)

    def test_deterministic_reproducibility_and_replay(self) -> None:
        rpt_a = self.engine.generate_report(
            self.ref_snap, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
        )
        replay = self.engine.replay_report(rpt_a.report_id)
        self.assertTrue(replay["exact_match"])
        self.assertEqual(replay["stored_sha256"], replay["replayed_sha256"])

        # Fresh engine instance produces byte-identical report ID, hash, XML, and CSV
        engine2 = RegulatoryReportingEngine(self.config, store=SqliteJournalStore(":memory:"))
        try:
            rpt_b = engine2.generate_report(
                self.ref_snap, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
            )
            self.assertEqual(rpt_a.report_id, rpt_b.report_id)
            self.assertEqual(rpt_a.content_sha256, rpt_b.content_sha256)
            self.assertEqual(rpt_a.xml_payload, rpt_b.xml_payload)
            self.assertEqual(rpt_a.csv_payload, rpt_b.csv_payload)
        finally:
            engine2.close()


if __name__ == "__main__":
    unittest.main()
