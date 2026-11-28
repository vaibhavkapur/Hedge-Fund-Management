"""Tests for deterministic XML and CSV rendering and strict schema validation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from regulatory_reporting_engine.config import load_config
from regulatory_reporting_engine.engine import RegulatoryReportingEngine
from regulatory_reporting_engine.errors import SchemaValidationError
from regulatory_reporting_engine.models import GovernedSnapshot
from regulatory_reporting_engine.schemas import (
    validate_csv_against_schema,
    validate_xml_against_schema,
)
from regulatory_reporting_engine.store import SqliteJournalStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"


class TestSchemaValidationXmlCsv(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)
        self.engine = RegulatoryReportingEngine(self.config, store=SqliteJournalStore(":memory:"))
        self.snapshot = GovernedSnapshot.from_dict(
            json.loads((FIXTURES_DIR / "reference_snapshot_q1_2026.json").read_text(encoding="utf-8"))
        )

    def tearDown(self) -> None:
        self.engine.close()

    def test_generated_xml_and_csv_pass_schema_validation_for_all_profiles(self) -> None:
        for profile_key in self.config.supported_profiles:
            pid, ver = profile_key.split(":")
            schema = self.engine.registry.get_schema(pid, ver)
            report = self.engine.build_draft_report_pure(
                self.snapshot, profile_id=pid, schema_version=ver
            )
            validate_xml_against_schema(report.xml_payload, schema)
            validate_csv_against_schema(report.csv_payload, schema)
            self.assertIn('certifiedForFiling="false"', report.xml_payload)
            self.assertIn("ILLUSTRATIVE_DRAFT_NOT_FOR_REGULATORY_FILING", report.csv_payload)

    def test_corrupted_xml_fails_schema_validation(self) -> None:
        schema = self.engine.registry.get_schema("US_SEC_PF_ILLUSTRATIVE", "1.0.0")
        report = self.engine.build_draft_report_pure(
            self.snapshot, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
        )

        # 1. Tamper certifiedForFiling attribute
        bad_cert = report.xml_payload.replace('certifiedForFiling="false"', 'certifiedForFiling="true"')
        with self.assertRaises(SchemaValidationError):
            validate_xml_against_schema(bad_cert, schema)

        # 2. Tamper decimal precision
        bad_scale = report.xml_payload.replace(">100000000.00<", ">100000000.0<")
        with self.assertRaises(SchemaValidationError):
            validate_xml_against_schema(bad_scale, schema)

        # 3. Tamper LEI inside XML
        bad_lei = report.xml_payload.replace("000000SYNTHFUND00181", "000000SYNTHFUND00199")
        with self.assertRaises(SchemaValidationError):
            validate_xml_against_schema(bad_lei, schema)

        # 4. Malformed XML
        with self.assertRaises(SchemaValidationError):
            validate_xml_against_schema("<broken><xml>", schema)

    def test_corrupted_csv_fails_schema_validation(self) -> None:
        schema = self.engine.registry.get_schema("US_SEC_PF_ILLUSTRATIVE", "1.0.0")
        report = self.engine.build_draft_report_pure(
            self.snapshot, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
        )

        # Header tampering
        bad_header = report.csv_payload.replace("nav_total_usd", "nav_wrong_col")
        with self.assertRaises(SchemaValidationError):
            validate_csv_against_schema(bad_header, schema)

        # Negative value on non-negative field
        bad_val = report.csv_payload.replace(",100000000.00,", ",-500.00,")
        with self.assertRaises(SchemaValidationError):
            validate_csv_against_schema(bad_val, schema)


if __name__ == "__main__":
    unittest.main()
