"""Lifecycle tests: duplicate filing, version correction, schema evolution, approval audit, immutability, no auto-submit."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from regulatory_reporting_engine.config import load_config
from regulatory_reporting_engine.engine import RegulatoryReportingEngine
from regulatory_reporting_engine.errors import (
    ApprovalWorkflowError,
    AutomatedSubmissionProhibitedError,
    DuplicateFilingError,
    ImmutabilityViolationError,
)
from regulatory_reporting_engine.models import GovernedSnapshot, ReportStatus
from regulatory_reporting_engine.store import SqliteJournalStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"


class TestLifecycleCorrectionsAndAudit(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)
        self.store = SqliteJournalStore(":memory:")
        self.engine = RegulatoryReportingEngine(self.config, store=self.store)
        self.snap_v1 = GovernedSnapshot.from_dict(
            json.loads((FIXTURES_DIR / "reference_snapshot_q1_2026.json").read_text(encoding="utf-8"))
        )
        self.snap_v2 = GovernedSnapshot.from_dict(
            json.loads((FIXTURES_DIR / "reference_snapshot_q1_2026_corrected.json").read_text(encoding="utf-8"))
        )

    def tearDown(self) -> None:
        self.engine.close()

    def test_duplicate_filing_blocked_and_version_correction_succeeds(self) -> None:
        """Required scenario: Duplicate filing/version correction."""
        rpt_v1 = self.engine.generate_report(
            self.snap_v1,
            profile_id="US_SEC_PF_ILLUSTRATIVE",
            schema_version="1.0.0",
            actor="analyst_alice",
        )
        # Re-filing v1 for the same fund + period + profile MUST raise DuplicateFilingError
        with self.assertRaises(DuplicateFilingError) as ctx:
            self.engine.generate_report(
                self.snap_v1,
                profile_id="US_SEC_PF_ILLUSTRATIVE",
                schema_version="1.0.0",
                actor="analyst_bob",
            )
        self.assertEqual(ctx.exception.code, "REG-4001")

        # Advance v1 through DRAFT -> REVIEWED -> APPROVED
        self.engine.transition_status(
            rpt_v1.report_id,
            ReportStatus.REVIEWED,
            actor="reviewer_carol",
            rationale="Checked lineage and cross-report reconciliation.",
            timestamp_utc="2026-04-02T10:00:00Z",
        )
        self.engine.transition_status(
            rpt_v1.report_id,
            ReportStatus.APPROVED,
            actor="cco_dave",
            rationale="Approved illustrative draft for offline compliance package.",
            timestamp_utc="2026-04-02T14:00:00Z",
        )

        # Create version correction v2 superseding v1
        rpt_v2 = self.engine.create_correction(
            rpt_v1.report_id,
            self.snap_v2,
            correction_reason="Late corporate-action accrual (+1.5M USD NAV restatement).",
            actor="analyst_alice",
        )
        self.assertEqual(rpt_v2.version_number, 2)
        self.assertEqual(rpt_v2.supersedes_report_id, rpt_v1.report_id)
        self.assertEqual(rpt_v2.fields["nav_total_usd"], Decimal("101500000.00"))
        self.assertEqual(rpt_v2.status, ReportStatus.DRAFT)

        # Prior version v1 is preserved immutably and marked SUPERSEDED
        stored_v1 = self.store.get_report(rpt_v1.report_id)
        assert stored_v1 is not None
        self.assertEqual(stored_v1.status, ReportStatus.SUPERSEDED)
        self.assertEqual(stored_v1.fields["nav_total_usd"], "100000000.00")

        # Attempting to correct already-superseded v1 fails explicitly
        with self.assertRaises(ApprovalWorkflowError):
            self.engine.create_correction(
                rpt_v1.report_id,
                self.snap_v2,
                correction_reason="Duplicate branch attempt",
                actor="analyst_bob",
            )

        # Schema evolution correction v2 -> v3 upgrading schema 1.0.0 -> 2.0.0
        rpt_v3 = self.engine.create_correction(
            rpt_v2.report_id,
            self.snap_v2,
            correction_reason="Schema evolution upgrade to US_SEC_PF_ILLUSTRATIVE 2.0.0.",
            actor="compliance_lead",
            schema_version="2.0.0",
        )
        self.assertEqual(rpt_v3.version_number, 3)
        self.assertEqual(rpt_v3.schema_version, "2.0.0")
        self.assertIn("cash_and_equivalents_usd", rpt_v3.fields)

        versions = self.engine.list_filing_versions(rpt_v1.filing_key)
        self.assertEqual([v.version_number for v in versions], [1, 2, 3])
        self.assertTrue(self.store.verify_audit_chain(rpt_v1.report_id))
        self.assertTrue(self.store.verify_audit_chain(rpt_v2.report_id))
        self.assertTrue(self.store.verify_audit_chain(rpt_v3.report_id))

    def test_illegal_approval_transitions_blocked(self) -> None:
        rpt = self.engine.generate_report(
            self.snap_v1, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
        )
        # Cannot jump directly DRAFT -> APPROVED without REVIEWED
        with self.assertRaises(ApprovalWorkflowError) as ctx:
            self.engine.transition_status(
                rpt.report_id,
                ReportStatus.APPROVED,
                actor="cco_dave",
                rationale="Attempted shortcut",
            )
        self.assertEqual(ctx.exception.code, "REG-4002")

    def test_database_level_immutability_enforced(self) -> None:
        rpt = self.engine.generate_report(
            self.snap_v1, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
        )
        with self.assertRaises(ImmutabilityViolationError):
            self.store.attempt_raw_mutation_check(
                f"UPDATE governed_snapshots SET base_currency = 'EUR' WHERE snapshot_id = '{self.snap_v1.snapshot_id}'"
            )
        with self.assertRaises(ImmutabilityViolationError):
            self.store.attempt_raw_mutation_check(
                f"DELETE FROM approval_audit_journal WHERE report_id = '{rpt.report_id}'"
            )
        with self.assertRaises(ImmutabilityViolationError):
            self.store.attempt_raw_mutation_check(
                f"UPDATE regulatory_reports SET xml_payload = '<tampered/>' WHERE report_id = '{rpt.report_id}'"
            )

    def test_automated_submission_structurally_prohibited(self) -> None:
        """Acceptance criterion: No automated submission."""
        rpt = self.engine.generate_report(
            self.snap_v1, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
        )
        with self.assertRaises(AutomatedSubmissionProhibitedError) as ctx:
            self.engine.submit_to_regulator(rpt.report_id, destination="SEC_EDGAR_MOCK")
        self.assertEqual(ctx.exception.code, "REG-9001")
        self.assertEqual(self.engine.counters["submission_attempts_blocked"], 1)


if __name__ == "__main__":
    unittest.main()
