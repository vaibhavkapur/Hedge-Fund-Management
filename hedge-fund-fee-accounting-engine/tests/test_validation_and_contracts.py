"""Validation, schema contract, invalid-input, and adapter tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from hedge_fund_fee_engine.adapters import (
    CapitalFlowFeedAdapter,
    NavSnapshotFeedAdapter,
)
from hedge_fund_fee_engine.errors import (
    FeeEngineError,
    UnsupportedPolicyError,
    ValidationError,
)
from hedge_fund_fee_engine.models import (
    CapitalFlow,
    CapitalFlowType,
    EngineConfig,
    FeeAgreement,
    InvestorLot,
    NavSnapshot,
    to_decimal,
)
from hedge_fund_fee_engine.service import FeeAccountingService


FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "default_engine_config.json"


class TestValidationAndContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.config = EngineConfig.from_json_file(DEFAULT_CONFIG)
        self.service = FeeAccountingService(config=self.config)
        self.ref_data = json.loads((FIXTURES_DIR / "reference_basic_2_and_20.json").read_text(encoding="utf-8"))

    def test_invalid_fixture_cases_all_raise_expected_error_codes(self) -> None:
        invalid_suite = json.loads((FIXTURES_DIR / "invalid_cases.json").read_text(encoding="utf-8"))
        base_agreement = dict(self.ref_data["agreement"])
        base_snapshot = dict(self.ref_data["periods"][0]["snapshot"])
        base_lots = [InvestorLot.from_dict(d) for d in self.ref_data["initial_lots"]]

        for case in invalid_suite["cases"]:
            with self.subTest(case_id=case["case_id"]):
                agr_dict = {**base_agreement, **case.get("agreement_override", {})}
                snap_dict = {**base_snapshot, **case.get("snapshot_override", {})}
                with self.assertRaises(FeeEngineError) as ctx:
                    agr = FeeAgreement.from_dict(agr_dict)
                    snap = NavSnapshot.from_dict(snap_dict)
                    self.service.run_period(agreement=agr, snapshot=snap, lots=base_lots, persist=False)
                self.assertEqual(ctx.exception.code, case["expected_error_code"])

    def test_float_inputs_rejected_by_decimal_parser(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            to_decimal(0.02, field_name="management_fee_rate")
        self.assertEqual(ctx.exception.code, "FLOAT_NOT_ALLOWED")

    def test_missing_config_file_raises_clear_error(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            EngineConfig.from_json_file(PROJECT_ROOT / "config" / "nonexistent_config.json")
        self.assertEqual(ctx.exception.code, "CONFIG_NOT_FOUND")

    def test_unsupported_hurdle_and_day_count_raise_unsupported_policy_error(self) -> None:
        bad_agr = dict(self.ref_data["agreement"], hurdle_type="WATERFALL_TIERED_EXOTIC")
        with self.assertRaises(UnsupportedPolicyError) as ctx:
            FeeAgreement.from_dict(bad_agr)
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_HURDLE_TYPE")

        bad_dc = dict(self.ref_data["agreement"], day_count="ACT_ACT_ISDA")
        with self.assertRaises(UnsupportedPolicyError) as ctx:
            FeeAgreement.from_dict(bad_dc)
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_DAY_COUNT")

    def test_duplicate_lot_ids_and_excessive_redemption_rejected(self) -> None:
        agr = FeeAgreement.from_dict(self.ref_data["agreement"])
        snap = NavSnapshot.from_dict(self.ref_data["periods"][0]["snapshot"])
        lot = InvestorLot.from_dict(self.ref_data["initial_lots"][0])

        with self.assertRaises(ValidationError) as ctx:
            self.service.run_period(agreement=agr, snapshot=snap, lots=[lot, lot], persist=False)
        self.assertEqual(ctx.exception.code, "DUPLICATE_LOT_ID")

        excessive_redemption = CapitalFlow(
            flow_id="FLOW-TOO-BIG",
            investor_id=lot.investor_id,
            lot_id=lot.lot_id,
            share_class=lot.share_class,
            flow_date=date(2025, 2, 15),
            flow_type=CapitalFlowType.REDEMPTION,
            nav_per_unit=Decimal("105.00"),
            currency="USD",
            units=Decimal("99999.000000"),
        )
        with self.assertRaises(ValidationError) as ctx:
            self.service.run_period(
                agreement=agr,
                snapshot=snap,
                lots=[lot],
                capital_flows=[excessive_redemption],
                persist=False,
            )
        self.assertEqual(ctx.exception.code, "REDEMPTION_EXCEEDS_UNITS")

    def test_integration_adapters_translate_neighbor_payloads(self) -> None:
        nav_snap = NavSnapshotFeedAdapter.from_nav_engine_payload(
            {
                "valuation_id": "VAL-2025Q1",
                "period_start_date": "2024-12-31",
                "valuation_date": "2025-03-31",
                "share_class": "CLASS_A",
                "currency": "USD",
                "pre_fee_nav_per_share": "110.000000",
            }
        )
        self.assertEqual(nav_snap.snapshot_id, "VAL-2025Q1")
        self.assertEqual(nav_snap.gross_nav_per_unit, Decimal("110.000000"))

        flow = CapitalFlowFeedAdapter.from_investor_platform_payload(
            {
                "order_id": "ORD-001",
                "investor_id": "INV-99",
                "lot_id": "LOT-99",
                "share_class": "CLASS_A",
                "trade_date": "2025-02-14",
                "flow_type": "SUBSCRIPTION",
                "dealing_nav_per_unit": "105.000000",
                "currency": "USD",
                "amount": "105000.00",
            }
        )
        self.assertEqual(flow.flow_id, "ORD-001")
        self.assertEqual(flow.amount, Decimal("105000.00"))


if __name__ == "__main__":
    unittest.main()
