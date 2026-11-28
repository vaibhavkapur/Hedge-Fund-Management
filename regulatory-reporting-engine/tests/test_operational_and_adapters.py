"""Operational resilience tests: missing config, unsafe config, unsupported inputs, restart/rerun, CLI, NAV adapter."""

from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from regulatory_reporting_engine.adapters import adapt_fund_nav_engine_snapshot
from regulatory_reporting_engine.cli import main as cli_main
from regulatory_reporting_engine.config import load_config
from regulatory_reporting_engine.engine import RegulatoryReportingEngine
from regulatory_reporting_engine.errors import (
    AutomatedSubmissionProhibitedError,
    ConfigurationError,
    UnsupportedInputError,
)
from regulatory_reporting_engine.models import GovernedSnapshot, ReportStatus
from regulatory_reporting_engine.store import PostgresJournalStoreUnavailableError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"


class TestOperationalAndAdapters(unittest.TestCase):
    def test_missing_or_malformed_configuration_fails_loudly(self) -> None:
        with self.assertRaises(ConfigurationError) as ctx:
            load_config(PROJECT_ROOT / "config" / "does_not_exist.json")
        self.assertEqual(ctx.exception.code, "REG-1001")

        with tempfile.TemporaryDirectory() as tmp:
            bad_json = Path(tmp) / "bad.json"
            bad_json.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_config(bad_json)

            # Attempting to enable automated submission in config is structurally blocked
            unsafe_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            unsafe_cfg["submission_policy"]["automated_submission_enabled"] = True
            unsafe_path = Path(tmp) / "unsafe.json"
            unsafe_path.write_text(json.dumps(unsafe_cfg), encoding="utf-8")
            with self.assertRaises(AutomatedSubmissionProhibitedError):
                load_config(unsafe_path, base_dir=PROJECT_ROOT)

    def test_unsupported_profile_and_unavailable_postgres_backend(self) -> None:
        config = load_config(CONFIG_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            cfg_raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg_raw["storage"]["backend"] = "memory"
            mem_cfg_path = Path(tmp) / "mem.json"
            mem_cfg_path.write_text(json.dumps(cfg_raw), encoding="utf-8")
            mem_cfg = load_config(mem_cfg_path, base_dir=PROJECT_ROOT)

            engine = RegulatoryReportingEngine(mem_cfg)
            try:
                snap = GovernedSnapshot.from_dict(
                    json.loads((FIXTURES_DIR / "reference_snapshot_q1_2026.json").read_text(encoding="utf-8"))
                )
                with self.assertRaises(UnsupportedInputError):
                    engine.build_draft_report_pure(
                        snap, profile_id="UK_FCA_UNKNOWN", schema_version="1.0.0"
                    )
            finally:
                engine.close()

            cfg_raw["storage"]["backend"] = "postgres"
            pg_cfg_path = Path(tmp) / "pg.json"
            pg_cfg_path.write_text(json.dumps(cfg_raw), encoding="utf-8")
            pg_cfg = load_config(pg_cfg_path, base_dir=PROJECT_ROOT)
            with self.assertRaises(PostgresJournalStoreUnavailableError):
                RegulatoryReportingEngine(pg_cfg)

    def test_restart_and_rerun_persistence_with_sqlite_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg_raw["storage"]["backend"] = "sqlite"
            cfg_raw["storage"]["sqlite_path"] = str(Path(tmp) / "journal.db")
            cfg_path = Path(tmp) / "cfg.json"
            cfg_path.write_text(json.dumps(cfg_raw), encoding="utf-8")
            cfg = load_config(cfg_path, base_dir=PROJECT_ROOT)

            snap = GovernedSnapshot.from_dict(
                json.loads((FIXTURES_DIR / "reference_snapshot_q1_2026.json").read_text(encoding="utf-8"))
            )
            # Process 1: generate and review
            eng1 = RegulatoryReportingEngine(cfg)
            rpt = eng1.generate_report(snap, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0")
            eng1.transition_status(
                rpt.report_id,
                ReportStatus.REVIEWED,
                actor="ops_reviewer",
                rationale="Pre-restart review",
                timestamp_utc="2026-04-02T08:00:00Z",
            )
            eng1.close()

            # Process 2 (simulated restart): reload from SQLite and replay
            eng2 = RegulatoryReportingEngine(cfg)
            try:
                loaded = eng2.store.get_report(rpt.report_id)
                assert loaded is not None
                self.assertEqual(loaded.status, ReportStatus.REVIEWED)
                replay = eng2.replay_report(rpt.report_id)
                self.assertTrue(replay["exact_match"])
                self.assertTrue(eng2.store.verify_audit_chain(rpt.report_id))
            finally:
                eng2.close()

    def test_independent_nav_engine_adapter(self) -> None:
        nav_sample = json.loads((FIXTURES_DIR / "nav_engine_sample_output.json").read_text(encoding="utf-8"))
        adapted = adapt_fund_nav_engine_snapshot(
            nav_sample,
            fund_lei="000000SYNTHFUND00181",
            manager_lei="000000SYNTHMGR000140",
            domicile_country="US",
            period_id="2026-Q1",
            period_start="2026-01-01",
        )
        cfg = load_config(CONFIG_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            cfg_raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg_raw["storage"]["backend"] = "memory"
            p = Path(tmp) / "m.json"
            p.write_text(json.dumps(cfg_raw), encoding="utf-8")
            eng = RegulatoryReportingEngine(load_config(p, base_dir=PROJECT_ROOT))
            try:
                rpt = eng.build_draft_report_pure(
                    adapted, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="2.0.0"
                )
                self.assertEqual(rpt.fields["nav_total_usd"], Decimal("100000000.00"))
                self.assertEqual(rpt.fields["long_market_value_usd"], Decimal("105000000.00"))
                self.assertEqual(rpt.fields["short_market_value_usd"], Decimal("20000000.00"))
                self.assertEqual(rpt.fields["gross_notional_exposure_usd"], Decimal("125000000.00"))
                self.assertEqual(rpt.fields["position_count"], 3)
            finally:
                eng.close()

    def test_cli_list_validate_generate_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg_raw["storage"]["sqlite_path"] = str(Path(tmp) / "cli.db")
            cfg_path = Path(tmp) / "cli_cfg.json"
            cfg_path.write_text(json.dumps(cfg_raw), encoding="utf-8")
            out_dir = Path(tmp) / "out"

            self.assertEqual(
                cli_main(["--config", str(cfg_path), "--base-dir", str(PROJECT_ROOT), "list-profiles"]),
                0,
            )
            self.assertEqual(
                cli_main(
                    [
                        "--config",
                        str(cfg_path),
                        "--base-dir",
                        str(PROJECT_ROOT),
                        "validate-snapshot",
                        str(FIXTURES_DIR / "reference_snapshot_q1_2026.json"),
                    ]
                ),
                0,
            )
            self.assertEqual(
                cli_main(
                    [
                        "--config",
                        str(cfg_path),
                        "--base-dir",
                        str(PROJECT_ROOT),
                        "validate-snapshot",
                        str(FIXTURES_DIR / "invalid_missing_required.json"),
                    ]
                ),
                1,
            )
            rc = cli_main(
                [
                    "--config",
                    str(cfg_path),
                    "--base-dir",
                    str(PROJECT_ROOT),
                    "generate",
                    str(FIXTURES_DIR / "reference_snapshot_q1_2026.json"),
                    "--profile",
                    "US_SEC_PF_ILLUSTRATIVE",
                    "--schema-version",
                    "1.0.0",
                    "--out-dir",
                    str(out_dir),
                ]
            )
            self.assertEqual(rc, 0)
            xml_files = list(out_dir.glob("*.xml"))
            self.assertEqual(len(xml_files), 1)
            self.assertEqual(len(list(out_dir.glob("*.csv"))), 1)
            self.assertEqual(len(list(out_dir.glob("*.lineage.json"))), 1)
            report_id = xml_files[0].stem
            self.assertEqual(
                cli_main(["--config", str(cfg_path), "--base-dir", str(PROJECT_ROOT), "replay", report_id]),
                0,
            )
            self.assertEqual(
                cli_main(
                    [
                        "--config",
                        str(cfg_path),
                        "--base-dir",
                        str(PROJECT_ROOT),
                        "schema-diff",
                        "--profile",
                        "US_SEC_PF_ILLUSTRATIVE",
                        "--from-version",
                        "1.0.0",
                        "--to-version",
                        "2.0.0",
                    ]
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
