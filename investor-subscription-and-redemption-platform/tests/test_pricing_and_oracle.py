"""Tests comparing pure Decimal pricing calculations against the independent reference oracle."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from isrp.config import load_config
from isrp.errors import ValidationError
from isrp.models import NAVRecord, NAVStatus, RequestType
from isrp.pricing import (
    calculate_nav_restatement_delta,
    calculate_redemption,
    calculate_subscription,
)


class TestPricingAgainstReferenceOracle(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(PROJECT_ROOT / "config" / "default_config.json")
        self.oracle = json.loads(
            (PROJECT_ROOT / "data" / "fixtures" / "reference_oracle.json").read_text(encoding="utf-8")
        )
        self.ts = datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)

    def test_all_reference_oracle_cases_match_exact_decimal(self) -> None:
        for case in self.oracle["cases"]:
            with self.subTest(case_id=case["case_id"]):
                sc_cfg = self.config.get_share_class(case["fund_id"], case["share_class"])
                op = case["operation"]

                if op == "SUBSCRIPTION":
                    nav = NAVRecord(
                        nav_id=f"NAV-{case['case_id']}",
                        fund_id=case["fund_id"],
                        share_class=case["share_class"],
                        dealing_date=date(2026, 3, 31),
                        currency=case["currency"],
                        nav_per_share=Decimal(case["nav_per_share"]),
                        version=1,
                        status=NAVStatus.APPROVED,
                        approved_by="nav_controller",
                        approved_at=self.ts,
                    )
                    calc = calculate_subscription(
                        request_id=case["case_id"],
                        gross_cash=Decimal(case["gross_cash"]),
                        nav=nav,
                        share_class_cfg=sc_cfg,
                        rounding=self.config.rounding,
                    )
                    exp = case["expected"]
                    self.assertEqual(calc.fee_amount, Decimal(exp["fee_amount"]))
                    self.assertEqual(calc.net_cash, Decimal(exp["net_cash"]))
                    self.assertEqual(calc.shares, Decimal(exp["shares"]))
                    self.assertEqual(calc.valuation_at_nav, Decimal(exp["valuation_at_nav"]))
                    if "rounding_residual" in exp:
                        self.assertEqual(calc.rounding_residual, Decimal(exp["rounding_residual"]))
                    if "max_abs_residual" in exp:
                        self.assertLessEqual(abs(calc.rounding_residual), Decimal(exp["max_abs_residual"]))

                elif op == "REDEMPTION":
                    nav = NAVRecord(
                        nav_id=f"NAV-{case['case_id']}",
                        fund_id=case["fund_id"],
                        share_class=case["share_class"],
                        dealing_date=date(2026, 4, 30),
                        currency=case["currency"],
                        nav_per_share=Decimal(case["nav_per_share"]),
                        version=1,
                        status=NAVStatus.APPROVED,
                        approved_by="nav_controller",
                        approved_at=self.ts,
                    )
                    calc = calculate_redemption(
                        request_id=case["case_id"],
                        requested_shares=Decimal(case["requested_shares"]),
                        requested_gross_cash=None,
                        nav=nav,
                        share_class_cfg=sc_cfg,
                        rounding=self.config.rounding,
                    )
                    exp = case["expected"]
                    self.assertEqual(calc.gross_cash, Decimal(exp["gross_cash"]))
                    self.assertEqual(calc.fee_amount, Decimal(exp["fee_amount"]))
                    self.assertEqual(calc.net_cash, Decimal(exp["net_cash"]))
                    self.assertEqual(calc.shares, Decimal(exp["shares"]))

                elif op == "NAV_RESTATEMENT_SUBSCRIPTION":
                    restated_nav = NAVRecord(
                        nav_id="NAV-RESTATED-V2",
                        fund_id=case["fund_id"],
                        share_class=case["share_class"],
                        dealing_date=date(2026, 3, 31),
                        currency=case["currency"],
                        nav_per_share=Decimal(case["restated_nav_per_share"]),
                        version=2,
                        status=NAVStatus.APPROVED,
                        approved_by="nav_controller_2",
                        approved_at=self.ts,
                    )
                    delta = calculate_nav_restatement_delta(
                        request_id=case["case_id"],
                        request_type=RequestType.SUBSCRIPTION,
                        original_nav_id="NAV-V1",
                        original_gross_cash=Decimal("100000.00"),
                        original_fee=Decimal("1000.00"),
                        original_net_cash=Decimal(case["net_cash"]),
                        original_shares=Decimal(case["original_shares"]),
                        restated_nav=restated_nav,
                        share_class_cfg=sc_cfg,
                        rounding=self.config.rounding,
                    )
                    exp = case["expected"]
                    self.assertEqual(delta.restated_shares, Decimal(exp["restated_shares"]))
                    self.assertEqual(delta.share_delta, Decimal(exp["share_delta"]))
                    self.assertEqual(delta.net_cash_delta, Decimal(exp["cash_delta"]))

    def test_pricing_rejects_superseded_nav_and_ambiguous_redemption(self) -> None:
        sc_cfg = self.config.get_share_class("FUND-ALPHA", "CLASS-A")
        bad_nav = NAVRecord(
            nav_id="NAV-OLD",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            dealing_date=date(2026, 3, 31),
            currency="USD",
            nav_per_share=Decimal("125.000000"),
            version=1,
            status=NAVStatus.SUPERSEDED,
            approved_by="nav_controller",
            approved_at=self.ts,
        )
        with self.assertRaises(ValidationError):
            calculate_subscription(
                request_id="REQ-BAD-NAV",
                gross_cash=Decimal("10000.00"),
                nav=bad_nav,
                share_class_cfg=sc_cfg,
                rounding=self.config.rounding,
            )

        good_nav = NAVRecord(
            nav_id="NAV-GOOD",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            dealing_date=date(2026, 3, 31),
            currency="USD",
            nav_per_share=Decimal("125.000000"),
            version=1,
            status=NAVStatus.APPROVED,
            approved_by="nav_controller",
            approved_at=self.ts,
        )
        with self.assertRaises(ValidationError):
            calculate_redemption(
                request_id="REQ-AMB",
                requested_shares=Decimal("10.000000"),
                requested_gross_cash=Decimal("1250.00"),
                nav=good_nav,
                share_class_cfg=sc_cfg,
                rounding=self.config.rounding,
            )


if __name__ == "__main__":
    unittest.main()
