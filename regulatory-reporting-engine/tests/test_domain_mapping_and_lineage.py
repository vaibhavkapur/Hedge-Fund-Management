"""Domain correctness tests: reference oracle comparison, 100% lineage, missing data, invalid ID, changed schema."""

from __future__ import annotations

import copy
import json
import unittest
from decimal import Decimal
from pathlib import Path

from regulatory_reporting_engine.config import load_config
from regulatory_reporting_engine.engine import RegulatoryReportingEngine
from regulatory_reporting_engine.errors import (
    InvalidIdentifierError,
    MissingRequiredDataError,
    SchemaEvolutionError,
    UnitMismatchError,
)
from regulatory_reporting_engine.mapper import map_snapshot_to_profile
from regulatory_reporting_engine.models import GovernedSnapshot
from regulatory_reporting_engine.schemas import SchemaRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"


class TestDomainMappingAndLineage(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)
        self.registry = SchemaRegistry(self.config.paths.profiles_dir, self.config.paths.mappings_dir)
        self.engine = RegulatoryReportingEngine(self.config, store=None, registry=self.registry)
        self.ref_snapshot = GovernedSnapshot.from_dict(
            json.loads((FIXTURES_DIR / "reference_snapshot_q1_2026.json").read_text(encoding="utf-8"))
        )
        self.expected = json.loads((FIXTURES_DIR / "reference_expected.json").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.engine.close()

    def test_reference_oracle_exact_match_all_profiles(self) -> None:
        for profile_key, oracle_fields in self.expected["profiles"].items():
            profile_id, schema_version = profile_key.split(":")
            report = self.engine.build_draft_report_pure(
                self.ref_snapshot,
                profile_id=profile_id,
                schema_version=schema_version,
            )
            serialized_actual = {
                k: (str(v) if isinstance(v, Decimal) else v) for k, v in report.fields.items()
            }
            self.assertEqual(serialized_actual, oracle_fields, f"Mismatch for {profile_key}")

            # Acceptance criterion: every output field traces to source or declared derivation
            self.assertEqual(set(report.lineage.keys()), set(report.fields.keys()))
            for fid, lin in report.lineage.items():
                self.assertEqual(lin.snapshot_id, self.ref_snapshot.snapshot_id)
                self.assertTrue(len(lin.derivation_expression) > 0)
                if lin.source_type == "derived":
                    self.assertTrue(len(lin.upstream_fields) >= 2)
                    self.assertTrue(len(lin.source_record_ids) >= 1)
                else:
                    self.assertTrue(len(lin.source_record_ids) >= 1, f"Empty source records for {fid}")

    def test_boundary_zero_nav_and_empty_positions(self) -> None:
        snap = GovernedSnapshot.from_dict(
            json.loads((FIXTURES_DIR / "boundary_zero_nav_empty_positions.json").read_text(encoding="utf-8"))
        )
        report = self.engine.build_draft_report_pure(
            snap,
            profile_id="US_SEC_PF_ILLUSTRATIVE",
            schema_version="1.0.0",
        )
        self.assertEqual(report.fields["nav_total_usd"], Decimal("0.00"))
        self.assertEqual(report.fields["gross_leverage_ratio"], Decimal("0.000000"))
        self.assertEqual(report.fields["position_count"], 0)
        self.assertEqual(report.fields["top_counterparty_exposure_usd"], Decimal("0.00"))

    def test_changed_schema_detected_explicitly(self) -> None:
        """Applying v1.0.0 mapping against evolved v2.0.0 schema must fail with SchemaEvolutionError."""
        schema_v2 = self.registry.get_schema("US_SEC_PF_ILLUSTRATIVE", "2.0.0")
        mapping_v1 = self.registry.get_mapping("US_SEC_PF_ILLUSTRATIVE", "1.0.0")

        with self.assertRaises(SchemaEvolutionError) as ctx:
            map_snapshot_to_profile(self.ref_snapshot, schema_v2, mapping_v1)
        self.assertEqual(ctx.exception.code, "REG-2001")

        # Also test a mapping claiming v2.0.0 version tag but still missing new required fields
        forged_mapping = copy.deepcopy(mapping_v1)
        forged_mapping["target_schema_version"] = "2.0.0"
        with self.assertRaises(SchemaEvolutionError) as ctx2:
            map_snapshot_to_profile(self.ref_snapshot, schema_v2, forged_mapping)
        self.assertIn("cash_and_equivalents_usd", ctx2.exception.details["missing_required_schema_fields"])
        self.assertIn("gross_exposure_usd", ctx2.exception.details["unknown_mapping_target_fields"])

    def test_missing_required_v2_source_field_raises_missing_data(self) -> None:
        raw = json.loads((FIXTURES_DIR / "reference_snapshot_q1_2026.json").read_text(encoding="utf-8"))
        del raw["nav"]["cash_and_equivalents"]
        snap = GovernedSnapshot.from_dict(raw)

        # v1 still succeeds because v1 does not require cash_and_equivalents
        rpt_v1 = self.engine.build_draft_report_pure(
            snap, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
        )
        self.assertEqual(rpt_v1.fields["nav_total_usd"], Decimal("100000000.00"))

        # v2 requires cash_and_equivalents -> explicit MissingRequiredDataError
        with self.assertRaises(MissingRequiredDataError) as ctx:
            self.engine.build_draft_report_pure(
                snap, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="2.0.0"
            )
        self.assertEqual(ctx.exception.code, "REG-1002")

    def test_currency_unit_mismatch_against_profile(self) -> None:
        raw = json.loads((FIXTURES_DIR / "reference_snapshot_q1_2026.json").read_text(encoding="utf-8"))
        raw["fund"]["base_currency"] = "EUR"
        raw["nav"]["currency"] = "EUR"
        for exp in raw["exposures"]:
            exp["currency"] = "EUR"
        snap = GovernedSnapshot.from_dict(raw)

        with self.assertRaises(UnitMismatchError) as ctx:
            self.engine.build_draft_report_pure(
                snap, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
            )
        self.assertEqual(ctx.exception.code, "REG-1004")

    def test_invalid_identifier_blocks_report_generation(self) -> None:
        raw = json.loads((FIXTURES_DIR / "invalid_identifier.json").read_text(encoding="utf-8"))
        with self.assertRaises(InvalidIdentifierError):
            GovernedSnapshot.from_dict(raw)

    def test_schema_diff_identifies_breaking_v1_to_v2_changes(self) -> None:
        diff = self.engine.compare_schemas("US_SEC_PF_ILLUSTRATIVE", "1.0.0", "2.0.0")
        self.assertTrue(diff.is_breaking)
        self.assertIn("cash_and_equivalents_usd", diff.added_fields)
        self.assertIn("gross_notional_exposure_usd", diff.added_fields)
        self.assertIn("liquidity_bucket_primary", diff.added_fields)
        self.assertIn("gross_exposure_usd", diff.removed_fields)


if __name__ == "__main__":
    unittest.main()
