"""Tests for schema validation, normalization, error propagation, and invalid fixtures."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pb_collateral.errors import UnsupportedInputError, ValidationError
from pb_collateral.models import AssetClass, EncumbranceStatus
from pb_collateral.validation import (
    normalize_broker_statement,
    parse_financing_schedules,
    parse_policy_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"


class TestValidationAndNormalization(unittest.TestCase):
    def test_default_configs_parse_cleanly(self) -> None:
        policies, rounding_mode = parse_policy_config(json.loads((CONFIG_DIR / "default_policy.json").read_text()))
        schedules = parse_financing_schedules(json.loads((CONFIG_DIR / "financing_rates.json").read_text()))
        self.assertIn("PB-ALPHA", policies)
        self.assertIn("PB-BETA", policies)
        self.assertEqual(rounding_mode, "ROUND_HALF_EVEN")
        self.assertIn(("PB-ALPHA", "USD"), schedules)
        self.assertIn(("PB-BETA", "EUR"), schedules)

    def test_broker_aliases_normalize_and_hash_is_stable(self) -> None:
        ref = json.loads((FIXTURES_DIR / "reference_oracle.json").read_text())
        raw_stmt = ref["broker_runs"][0]["statement"]
        stmt1 = normalize_broker_statement(raw_stmt)
        # Reorder positions and use canonical enum names; content hash must remain identical
        raw_reordered = json.loads(json.dumps(raw_stmt))
        raw_reordered["positions"] = list(reversed(raw_reordered["positions"]))
        raw_reordered["positions"][-1]["asset_class"] = "US_TREASURY"
        raw_reordered["positions"][-1]["encumbrance_status"] = "PLEDGED_MARGIN"
        stmt2 = normalize_broker_statement(raw_reordered)

        self.assertEqual(stmt1.positions[0].asset_class, AssetClass.US_TREASURY)
        self.assertEqual(stmt1.positions[0].encumbrance_status, EncumbranceStatus.PLEDGED_MARGIN)
        self.assertEqual(stmt1.positions[2].encumbrance_status, EncumbranceStatus.UNENCUMBERED)
        self.assertEqual(stmt1.content_hash, stmt2.content_hash)

    def test_invalid_fixtures_raise_explicit_errors(self) -> None:
        cases = json.loads((FIXTURES_DIR / "invalid_payloads.json").read_text())["cases"]

        with self.assertRaises(ValidationError):
            normalize_broker_statement(cases["float_price_rejected"])

        with self.assertRaises(UnsupportedInputError):
            normalize_broker_statement(cases["unsupported_currency"])

        with self.assertRaises(UnsupportedInputError):
            normalize_broker_statement(cases["unsupported_asset_class"])

        with self.assertRaises(ValidationError):
            normalize_broker_statement(cases["missing_timezone"])

    def test_duplicate_lot_inside_single_statement_rejected(self) -> None:
        ref = json.loads((FIXTURES_DIR / "reference_oracle.json").read_text())
        raw_stmt = json.loads(json.dumps(ref["broker_runs"][0]["statement"]))
        raw_stmt["positions"].append(dict(raw_stmt["positions"][0], position_id="POS-DUP"))
        with self.assertRaises(ValidationError) as ctx:
            normalize_broker_statement(raw_stmt)
        self.assertIn("Duplicate lot_id", str(ctx.exception))

    def test_positive_debit_balance_sign_rejected(self) -> None:
        ref = json.loads((FIXTURES_DIR / "reference_oracle.json").read_text())
        raw_stmt = json.loads(json.dumps(ref["broker_runs"][0]["statement"]))
        raw_stmt["cash_balances"][1]["amount"] = "600000.00"
        with self.assertRaises(ValidationError):
            normalize_broker_statement(raw_stmt)


if __name__ == "__main__":
    unittest.main()
