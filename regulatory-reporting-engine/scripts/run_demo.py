#!/usr/bin/env python3
"""Reproducible end-to-end demo for the Regulatory Reporting Engine."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from regulatory_reporting_engine.config import load_config
from regulatory_reporting_engine.engine import RegulatoryReportingEngine
from regulatory_reporting_engine.errors import (
    AutomatedSubmissionProhibitedError,
    DuplicateFilingError,
)
from regulatory_reporting_engine.models import GovernedSnapshot, ReportStatus
from regulatory_reporting_engine.renderer import render_lineage_bundle_json
from regulatory_reporting_engine.store import SqliteJournalStore


def run_demo(out_dir: Path | None = None) -> dict:
    config = load_config(PROJECT_ROOT / "config" / "default_config.json")
    reports_dir = out_dir or (PROJECT_ROOT / "reports" / "demo_outputs")
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Fresh SQLite database in demo_outputs for deterministic clean reruns
    db_path = reports_dir / "demo_journal.db"
    if db_path.exists():
        db_path.unlink()

    engine = RegulatoryReportingEngine(config, store=SqliteJournalStore(db_path))
    try:
        snap_v1 = GovernedSnapshot.from_dict(
            json.loads((config.paths.fixtures_dir / "reference_snapshot_q1_2026.json").read_text(encoding="utf-8"))
        )
        snap_v2 = GovernedSnapshot.from_dict(
            json.loads((config.paths.fixtures_dir / "reference_snapshot_q1_2026_corrected.json").read_text(encoding="utf-8"))
        )

        # 1. Generate US Form PF-style v1 and EU AIFMD-style v1 draft reports
        us_v1 = engine.generate_report(
            snap_v1,
            profile_id="US_SEC_PF_ILLUSTRATIVE",
            schema_version="1.0.0",
            actor="demo_analyst",
            rationale="Q1 2026 initial US illustrative draft",
        )
        eu_v1 = engine.generate_report(
            snap_v1,
            profile_id="EU_AIFMD_ANNEX4_ILLUSTRATIVE",
            schema_version="1.0.0",
            actor="demo_analyst",
            rationale="Q1 2026 initial EU illustrative draft",
        )

        # 2. Cross-report reconciliation
        reconciliation = engine.reconcile_reports(us_v1, eu_v1)

        # 3. Review and approve v1
        engine.transition_status(
            us_v1.report_id,
            ReportStatus.REVIEWED,
            actor="demo_reviewer",
            rationale="Verified 100% lineage and cross-report reconciliation",
            timestamp_utc="2026-04-02T09:00:00Z",
        )
        engine.transition_status(
            us_v1.report_id,
            ReportStatus.APPROVED,
            actor="demo_cco",
            rationale="Approved illustrative draft for offline compliance review",
            timestamp_utc="2026-04-02T11:30:00Z",
        )

        # 4. Verify duplicate filing is blocked
        duplicate_blocked = False
        try:
            engine.generate_report(
                snap_v1,
                profile_id="US_SEC_PF_ILLUSTRATIVE",
                schema_version="1.0.0",
                actor="demo_analyst",
            )
        except DuplicateFilingError:
            duplicate_blocked = True

        # 5. Version correction (v2 restated NAV) and schema evolution correction (v3 -> schema 2.0.0)
        us_v2 = engine.create_correction(
            us_v1.report_id,
            snap_v2,
            correction_reason="Late corporate-action accrual restatement (+1,500,000.00 USD).",
            actor="demo_analyst",
        )
        us_v3 = engine.create_correction(
            us_v2.report_id,
            snap_v2,
            correction_reason="Schema evolution migration to US_SEC_PF_ILLUSTRATIVE 2.0.0.",
            actor="demo_compliance",
            schema_version="2.0.0",
        )

        # 6. Replay reproducibility check
        replay_v1 = engine.replay_report(us_v1.report_id)
        replay_v3 = engine.replay_report(us_v3.report_id)

        # 7. Verify automated submission is structurally blocked
        auto_submit_blocked = False
        try:
            engine.submit_to_regulator(us_v3.report_id, destination="MOCK_REGULATOR_PORTAL")
        except AutomatedSubmissionProhibitedError:
            auto_submit_blocked = True

        # Persist raw artifacts for inspection
        us_schema_v1 = engine.registry.get_schema("US_SEC_PF_ILLUSTRATIVE", "1.0.0")
        us_schema_v2 = engine.registry.get_schema("US_SEC_PF_ILLUSTRATIVE", "2.0.0")
        eu_schema_v1 = engine.registry.get_schema("EU_AIFMD_ANNEX4_ILLUSTRATIVE", "1.0.0")

        (reports_dir / "us_pf_v1_draft.xml").write_text(us_v1.xml_payload, encoding="utf-8")
        (reports_dir / "us_pf_v1_draft.csv").write_text(us_v1.csv_payload, encoding="utf-8")
        (reports_dir / "us_pf_v1_lineage.json").write_text(
            render_lineage_bundle_json(
                report_id=us_v1.report_id, snapshot_id=us_v1.snapshot_id, schema=us_schema_v1, lineage=us_v1.lineage
            ),
            encoding="utf-8",
        )

        (reports_dir / "us_pf_v3_evolved_v2schema.xml").write_text(us_v3.xml_payload, encoding="utf-8")
        (reports_dir / "us_pf_v3_evolved_v2schema.csv").write_text(us_v3.csv_payload, encoding="utf-8")
        (reports_dir / "us_pf_v3_lineage.json").write_text(
            render_lineage_bundle_json(
                report_id=us_v3.report_id, snapshot_id=us_v3.snapshot_id, schema=us_schema_v2, lineage=us_v3.lineage
            ),
            encoding="utf-8",
        )

        (reports_dir / "eu_aifmd_v1_draft.xml").write_text(eu_v1.xml_payload, encoding="utf-8")
        (reports_dir / "eu_aifmd_v1_draft.csv").write_text(eu_v1.csv_payload, encoding="utf-8")
        (reports_dir / "eu_aifmd_v1_lineage.json").write_text(
            render_lineage_bundle_json(
                report_id=eu_v1.report_id, snapshot_id=eu_v1.snapshot_id, schema=eu_schema_v1, lineage=eu_v1.lineage
            ),
            encoding="utf-8",
        )

        summary = {
            "us_v1_report_id": us_v1.report_id,
            "us_v1_sha256": us_v1.content_sha256,
            "eu_v1_report_id": eu_v1.report_id,
            "eu_v1_sha256": eu_v1.content_sha256,
            "us_v2_correction_report_id": us_v2.report_id,
            "us_v3_schema_evolution_report_id": us_v3.report_id,
            "reconciliation": reconciliation.to_dict(),
            "duplicate_filing_blocked": duplicate_blocked,
            "automated_submission_blocked": auto_submit_blocked,
            "replay_v1_exact_match": replay_v1["exact_match"],
            "replay_v3_exact_match": replay_v3["exact_match"],
            "audit_chain_valid_v1": engine.store.verify_audit_chain(us_v1.report_id),
            "audit_chain_valid_v2": engine.store.verify_audit_chain(us_v2.report_id),
            "audit_chain_valid_v3": engine.store.verify_audit_chain(us_v3.report_id),
            "counters": engine.counters,
        }
        (reports_dir / "demo_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary
    finally:
        engine.close()


if __name__ == "__main__":
    result = run_demo()
    print(json.dumps(result, indent=2))
