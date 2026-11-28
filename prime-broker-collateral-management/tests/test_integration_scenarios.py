"""Integration scenarios: ineligible collateral, haircut increase, concentration caps, and full multi-broker demo."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from pb_collateral.cli import run_bundle
from pb_collateral.fx import FxTable
from pb_collateral.models import AssetClass, MarginCallAction
from pb_collateral.service import CollateralManagementService
from pb_collateral.validation import (
    normalize_broker_statement,
    parse_financing_schedules,
    parse_fx_rates,
    parse_internal_snapshot,
    parse_policy_config,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = PROJECT_ROOT / "config"
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"


class TestIntegrationScenarios(unittest.TestCase):
    def setUp(self) -> None:
        self.policies, self.rounding = parse_policy_config(
            json.loads((CONFIG_DIR / "default_policy.json").read_text())
        )
        self.schedules = parse_financing_schedules(
            json.loads((CONFIG_DIR / "financing_rates.json").read_text())
        )
        self.demo = json.loads((FIXTURES_DIR / "demo_multi_broker_bundle.json").read_text())
        self.fx_table = FxTable(parse_fx_rates(self.demo["fx_rates"]), rounding_mode=self.rounding)

    def test_ineligible_collateral_gets_100pct_haircut_and_reconciliation_break(self) -> None:
        # In demo bundle PB-ALPHA has LOT-A-HY-INELIG-01 (HY_CORPORATE_BOND, $190,000 gross)
        # Broker optimistically reported 1,790,000 collateral value, whereas policy excludes HY bond:
        # LOT-A-UST-01: 1,000,000 * 0.98 = 980,000.00
        # LOT-A-SOV-EUR-01: 500,000 EUR * 1.10 = 550,000 USD * (1 - 0.03 - 0.08) = 489,500.00
        # LOT-A-HY-INELIG-01: 190,000 -> 0.00 (ineligible)
        # Margin cash: 250,000.00
        # Total posted eligible = 980,000 + 489,500 + 250,000 = 1,719,500.00
        item = self.demo["broker_runs"][0]
        svc = CollateralManagementService(policies=self.policies, financing_schedules=self.schedules)
        res = svc.process_statement(
            statement=normalize_broker_statement(item["statement"]),
            internal_snapshot=parse_internal_snapshot(item["internal_snapshot"]),
            fx_table=self.fx_table,
            accrual_start_date=date(2026, 3, 16),
            accrual_end_date=date(2026, 3, 17),
        )

        self.assertEqual(res.valuation.ineligible_lots, ("LOT-A-HY-INELIG-01",))
        self.assertEqual(res.valuation.ineligible_securities_gross_base, Decimal("190000.00"))
        self.assertEqual(res.valuation.total_posted_collateral_base, Decimal("1719500.00"))
        self.assertEqual(svc.counters.ineligible_assets_excluded, 1)
        # Broker reported 1,790,000 vs calculated 1,719,500 -> ELIGIBILITY_OR_HAIRCUT_BREAK of 70,500.00
        elig_breaks = [b for b in res.breaks if b.category == "ELIGIBILITY_OR_HAIRCUT_BREAK"]
        self.assertEqual(len(elig_breaks), 1)
        self.assertEqual(elig_breaks[0].difference, Decimal("70500.00"))

    def test_haircut_increase_turns_surplus_into_margin_call(self) -> None:
        # Start with reference oracle: PB-ALPHA has +20,000 surplus under v1 policy (UST 2%, EQ_LC 15%)
        ref = json.loads((FIXTURES_DIR / "reference_oracle.json").read_text())
        item = ref["broker_runs"][0]
        stmt = normalize_broker_statement(item["statement"])
        snap = parse_internal_snapshot(item["internal_snapshot"])
        fx = FxTable(parse_fx_rates(ref["fx_rates"]))

        # Policy v2 increases haircut on US_TREASURY (0_5Y) from 2% -> 5% and EQUITY_LARGE_CAP from 15% -> 35%
        base_policy = self.policies["PB-ALPHA"]
        tighter_rules = []
        for rule in base_policy.rules:
            if rule.asset_class == AssetClass.US_TREASURY and rule.maturity_bucket == "0_5Y":
                tighter_rules.append(replace(rule, haircut_pct=Decimal("0.05")))
            elif rule.asset_class == AssetClass.EQUITY_LARGE_CAP:
                tighter_rules.append(replace(rule, haircut_pct=Decimal("0.35")))
            else:
                tighter_rules.append(rule)
        policy_v2 = replace(base_policy, version=2, rules=tuple(tighter_rules))

        svc_v2 = CollateralManagementService(
            policies={"PB-ALPHA": policy_v2},
            financing_schedules=self.schedules,
        )
        res = svc_v2.process_statement(
            statement=stmt,
            internal_snapshot=snap,
            fx_table=fx,
            accrual_start_date=date(2026, 3, 13),
            accrual_end_date=date(2026, 3, 16),
        )
        # New posted collateral: UST 1,000,000*0.95 (950,000) + EQ 400,000*0.65 (260,000) + Cash 200,000 = 1,410,000
        # Requirement = 1,500,000 -> deficit = -90,000 (>= MTA 50,000) -> MARGIN_CALL of 90,000.00!
        self.assertEqual(res.valuation.total_posted_collateral_base, Decimal("1410000.00"))
        self.assertEqual(res.margin_call.raw_surplus_or_deficit_base, Decimal("-90000.00"))
        self.assertEqual(res.margin_call.action, MarginCallAction.MARGIN_CALL)
        self.assertEqual(res.margin_call.call_or_return_amount_base, Decimal("90000.00"))
        self.assertTrue(res.margin_call.flagged_before_settlement)
        self.assertIn("LOT-UST-FREE-002", res.margin_call.recommended_funding_lots)

    def test_end_to_end_demo_bundle_reproducibility(self) -> None:
        report1 = run_bundle(
            policy_path=CONFIG_DIR / "default_policy.json",
            financing_path=CONFIG_DIR / "financing_rates.json",
            fixture_path=FIXTURES_DIR / "demo_multi_broker_bundle.json",
        )
        report2 = run_bundle(
            policy_path=CONFIG_DIR / "default_policy.json",
            financing_path=CONFIG_DIR / "financing_rates.json",
            fixture_path=FIXTURES_DIR / "demo_multi_broker_bundle.json",
        )
        self.assertEqual(report1, report2)
        self.assertEqual(report1["counters"]["statements_processed"], 2)
        self.assertEqual(report1["counters"]["failed_transfers_quarantined"], 1)
        self.assertEqual(report1["counters"]["ineligible_assets_excluded"], 1)
        self.assertEqual(report1["counters"]["substitutions_approved"], 1)
        self.assertEqual(report1["counters"]["substitutions_blocked"], 1)


if __name__ == "__main__":
    unittest.main()
