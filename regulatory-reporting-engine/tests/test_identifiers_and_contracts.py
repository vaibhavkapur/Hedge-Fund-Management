"""Unit tests for ISO identifiers and canonical input data contracts."""

from __future__ import annotations

import copy
import json
import unittest
from decimal import Decimal
from pathlib import Path

from regulatory_reporting_engine.errors import (
    InvalidIdentifierError,
    MissingRequiredDataError,
    UnitMismatchError,
    UnsupportedInputError,
)
from regulatory_reporting_engine.identifiers import (
    is_valid_isin,
    is_valid_lei,
    validate_isin,
    validate_iso_country,
    validate_iso_currency,
    validate_lei,
)
from regulatory_reporting_engine.models import GovernedSnapshot, to_minor_units

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "data" / "fixtures"


class TestIdentifiersAndContracts(unittest.TestCase):
    def _load_fixture(self, name: str) -> dict:
        return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))

    def test_valid_and_invalid_lei_and_isin(self) -> None:
        self.assertTrue(is_valid_lei("000000SYNTHFUND00181"))
        self.assertFalse(is_valid_lei("000000SYNTHFUND00199"))
        self.assertFalse(is_valid_lei("SHORT"))

        self.assertTrue(is_valid_isin("US0000000010"))
        self.assertTrue(is_valid_isin("DE0000000017"))
        self.assertFalse(is_valid_isin("US0000000019"))
        self.assertFalse(is_valid_isin("BADISIN"))

        with self.assertRaises(InvalidIdentifierError) as ctx:
            validate_lei("000000SYNTHFUND00199", field_name="fund.fund_lei")
        self.assertEqual(ctx.exception.code, "REG-1003")

        with self.assertRaises(InvalidIdentifierError):
            validate_isin("US0000000019")

        self.assertEqual(validate_iso_currency("USD"), "USD")
        with self.assertRaises(InvalidIdentifierError):
            validate_iso_currency("ZZZ")

        self.assertEqual(validate_iso_country("US"), "US")
        with self.assertRaises(InvalidIdentifierError):
            validate_iso_country("XX")

    def test_valid_reference_snapshot_and_minor_units(self) -> None:
        raw = self._load_fixture("reference_snapshot_q1_2026.json")
        snap = GovernedSnapshot.from_dict(raw)
        self.assertEqual(snap.snapshot_id, "SNAP-SYNTH-2026Q1-0001")
        self.assertEqual(snap.nav.net_asset_value, Decimal("100000000.00"))
        self.assertEqual(to_minor_units(snap.nav.net_asset_value), 10000000000)
        self.assertEqual(len(snap.positions), 4)
        self.assertEqual(len(snap.exposures), 3)
        self.assertEqual(len(snap.payload_sha256), 64)

    def test_missing_required_data_fixture_rejected(self) -> None:
        raw = self._load_fixture("invalid_missing_required.json")
        with self.assertRaises(MissingRequiredDataError) as ctx:
            GovernedSnapshot.from_dict(raw)
        self.assertEqual(ctx.exception.code, "REG-1002")

    def test_invalid_identifier_fixture_rejected(self) -> None:
        raw = self._load_fixture("invalid_identifier.json")
        with self.assertRaises(InvalidIdentifierError) as ctx:
            GovernedSnapshot.from_dict(raw)
        self.assertEqual(ctx.exception.code, "REG-1003")

    def test_float_input_rejected_for_exact_decimal(self) -> None:
        raw = self._load_fixture("reference_snapshot_q1_2026.json")
        raw["nav"]["net_asset_value"] = 100000000.0
        with self.assertRaises(UnitMismatchError) as ctx:
            GovernedSnapshot.from_dict(raw)
        self.assertEqual(ctx.exception.code, "REG-1004")

    def test_nav_identity_and_currency_mismatch_rejected(self) -> None:
        raw = self._load_fixture("reference_snapshot_q1_2026.json")
        bad_nav = copy.deepcopy(raw)
        bad_nav["nav"]["net_asset_value"] = "99999999.00"
        with self.assertRaises(UnitMismatchError):
            GovernedSnapshot.from_dict(bad_nav)

        bad_ccy = copy.deepcopy(raw)
        bad_ccy["nav"]["currency"] = "EUR"
        with self.assertRaises(UnitMismatchError):
            GovernedSnapshot.from_dict(bad_ccy)

    def test_position_fx_consistency_and_exposure_identities(self) -> None:
        raw = self._load_fixture("reference_snapshot_q1_2026.json")
        bad_pos = copy.deepcopy(raw)
        bad_pos["positions"][0]["market_value_base"] = "61000000.00"
        with self.assertRaises(UnitMismatchError):
            GovernedSnapshot.from_dict(bad_pos)

        bad_exp = copy.deepcopy(raw)
        bad_exp["exposures"][0]["gross_exposure_base"] = "90000000.00"
        with self.assertRaises(UnitMismatchError):
            GovernedSnapshot.from_dict(bad_exp)

    def test_unsupported_contract_version_rejected(self) -> None:
        raw = self._load_fixture("reference_snapshot_q1_2026.json")
        raw["schema_contract_version"] = "9.9.9"
        with self.assertRaises(UnsupportedInputError) as ctx:
            GovernedSnapshot.from_dict(raw)
        self.assertEqual(ctx.exception.code, "REG-1005")


if __name__ == "__main__":
    unittest.main()
