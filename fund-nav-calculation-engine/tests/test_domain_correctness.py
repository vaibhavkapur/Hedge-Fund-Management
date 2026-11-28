"""Domain correctness tests: reference oracle, missing valuation, FX mismatch, stale prices, late corrections."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fund_nav_engine.calculator import calculate_nav
from fund_nav_engine.errors import (
    FxMismatchError,
    MissingValuationError,
    StalePriceError,
)
from fund_nav_engine.io_loader import load_bundle, load_config
from fund_nav_engine.models import (
    FxRateRecord,
    PriceRecord,
    StalePricePolicy,
)
from fund_nav_engine.service import NavCalculationService
from fund_nav_engine.store import SqliteNavStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "data" / "fixtures"
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"


class TestDomainCorrectness(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)
        self.expected = json.loads((FIXTURES / "reference_expected.json").read_text(encoding="utf-8"))

    def test_reference_scenario_matches_hand_calculated_oracle(self) -> None:
        bundle = load_bundle(FIXTURES / "reference_scenario.json")
        res = calculate_nav(bundle, self.config)
        exp = self.expected["v1"]

        self.assertEqual(str(res.pre_flow_gross_assets), exp["pre_flow_gross_assets"])
        self.assertEqual(str(res.pre_flow_liabilities), exp["pre_flow_liabilities"])
        self.assertEqual(str(res.pre_flow_net_assets), exp["pre_flow_net_assets"])
        self.assertEqual(str(res.total_subscriptions), exp["total_subscriptions"])
        self.assertEqual(str(res.total_redemptions), exp["total_redemptions"])
        self.assertEqual(str(res.net_capital_flows), exp["net_capital_flows"])
        self.assertEqual(str(res.gross_assets), exp["gross_assets"])
        self.assertEqual(str(res.total_liabilities), exp["total_liabilities"])
        self.assertEqual(str(res.net_assets), exp["net_assets"])

        # Strict accounting identity: NAV == Gross Assets - Total Liabilities
        self.assertEqual(res.net_assets, res.gross_assets - res.total_liabilities)
        self.assertTrue(res.reconciliation.reconciled)
        self.assertEqual(res.diagnostics["flows_deferred_post_cutoff"], exp["flows_deferred_post_cutoff"])

        by_class = {sc.share_class_id: sc for sc in res.share_class_navs}
        for cid, exp_sc in exp["share_classes"].items():
            actual_sc = by_class[cid]
            self.assertEqual(str(actual_sc.allocation_weight), exp_sc["allocation_weight"])
            self.assertEqual(str(actual_sc.pre_flow_net_assets_base), exp_sc["pre_flow_net_assets_base"])
            self.assertEqual(str(actual_sc.dealing_nav_per_share), exp_sc["dealing_nav_per_share"])
            self.assertEqual(str(actual_sc.closing_shares), exp_sc["closing_shares"])
            self.assertEqual(str(actual_sc.closing_net_assets_base), exp_sc["closing_net_assets_base"])
            self.assertEqual(str(actual_sc.closing_net_assets_class_ccy), exp_sc["closing_net_assets_class_ccy"])
            self.assertEqual(str(actual_sc.nav_per_share), exp_sc["nav_per_share"])
            self.assertFalse(actual_sc.zero_shares_flag)

        # Sum of class net assets equals fund net assets exactly
        self.assertEqual(
            sum((sc.closing_net_assets_base for sc in res.share_class_navs), Decimal("0")),
            res.net_assets,
        )

    def test_missing_valuation_raises_structured_error(self) -> None:
        bundle = load_bundle(FIXTURES / "missing_valuation.json")
        with self.assertRaises(MissingValuationError) as ctx:
            calculate_nav(bundle, self.config)
        self.assertEqual(ctx.exception.code, "NAV-2001")
        self.assertEqual(ctx.exception.details["instrument_id"], "EQ-US-MISSING")

    def test_fx_mismatch_missing_rate_raises_error(self) -> None:
        bundle = load_bundle(FIXTURES / "fx_mismatch.json")
        with self.assertRaises(FxMismatchError) as ctx:
            calculate_nav(bundle, self.config)
        self.assertEqual(ctx.exception.code, "NAV-2003")
        self.assertEqual(ctx.exception.details["from_currency"], "EUR")

    def test_fx_mismatch_position_vs_price_currency_raises_error(self) -> None:
        bundle = load_bundle(FIXTURES / "fx_mismatch.json")
        wrong_ccy_price = replace(bundle.prices[0], currency="USD")
        mutated = replace(bundle, prices=(wrong_ccy_price,))
        with self.assertRaises(FxMismatchError) as ctx:
            calculate_nav(mutated, self.config)
        self.assertEqual(ctx.exception.code, "NAV-2003")
        self.assertEqual(ctx.exception.details["position_currency"], "EUR")
        self.assertEqual(ctx.exception.details["price_currency"], "USD")

    def test_fx_stale_and_conflicting_rates_raise_error(self) -> None:
        bundle = load_bundle(FIXTURES / "fx_mismatch.json")
        stale_fx = FxRateRecord(
            fx_id="FX-STALE",
            base_currency="EUR",
            quote_currency="USD",
            rate=Decimal("1.20"),
            rate_date=date(2026, 3, 20),
            as_of_timestamp=datetime(2026, 3, 20, 16, 0, tzinfo=timezone.utc),
        )
        with self.assertRaises(FxMismatchError) as ctx:
            calculate_nav(replace(bundle, fx_rates=(stale_fx,)), self.config)
        self.assertEqual(ctx.exception.code, "NAV-2004")

        fx_a = FxRateRecord(
            fx_id="FX-A",
            base_currency="EUR",
            quote_currency="USD",
            rate=Decimal("1.20"),
            rate_date=date(2026, 3, 31),
            as_of_timestamp=datetime(2026, 3, 31, 16, 0, tzinfo=timezone.utc),
        )
        fx_b = FxRateRecord(
            fx_id="FX-B",
            base_currency="EUR",
            quote_currency="USD",
            rate=Decimal("1.25"),
            rate_date=date(2026, 3, 31),
            as_of_timestamp=datetime(2026, 3, 31, 16, 0, tzinfo=timezone.utc),
        )
        with self.assertRaises(FxMismatchError) as ctx2:
            calculate_nav(replace(bundle, fx_rates=(fx_a, fx_b)), self.config)
        self.assertEqual(ctx2.exception.code, "NAV-2005")

    def test_stale_price_policies(self) -> None:
        bundle = load_bundle(FIXTURES / "stale_price_exception.json")

        # Default policy = error -> raises StalePriceError
        with self.assertRaises(StalePriceError) as ctx:
            calculate_nav(bundle, self.config)
        self.assertEqual(ctx.exception.code, "NAV-2002")

        # Policy = require_override -> passes when override flag & reason present
        cfg_override = replace(self.config, stale_price_policy=StalePricePolicy.REQUIRE_OVERRIDE)
        res_override = calculate_nav(bundle, cfg_override)
        self.assertEqual(res_override.diagnostics["stale_prices_detected"], 1)
        self.assertEqual(res_override.diagnostics["stale_price_overrides_used"], 1)
        self.assertTrue(res_override.position_valuations[0].stale_override_used)

        # Policy = require_override -> fails if price is NOT an override
        non_override_px = PriceRecord(
            price_id="PX-STALE-NO-OVR",
            instrument_id="BOND-CORP-ILLIQUID",
            price=Decimal("96.50"),
            currency="USD",
            price_date=date(2026, 3, 25),
            as_of_timestamp=datetime(2026, 3, 25, 20, 0, tzinfo=timezone.utc),
            is_override=False,
        )
        with self.assertRaises(StalePriceError):
            calculate_nav(replace(bundle, prices=(non_override_px,)), cfg_override)

        # Policy = warn_and_use_last -> succeeds with warning
        cfg_warn = replace(self.config, stale_price_policy=StalePricePolicy.WARN_AND_USE_LAST)
        res_warn = calculate_nav(replace(bundle, prices=(non_override_px,)), cfg_warn)
        self.assertEqual(str(res_warn.net_assets), "193000.00")
        self.assertTrue(any("warn_and_use_last" in w for w in res_warn.warnings))

    def test_late_correction_restatement_matches_oracle(self) -> None:
        v1_bundle = load_bundle(FIXTURES / "reference_scenario.json")
        v2_bundle = load_bundle(FIXTURES / "late_correction_bundle.json")
        exp_v2 = self.expected["v2"]

        with SqliteNavStore(":memory:") as store:
            svc = NavCalculationService(store)
            v1_draft = svc.calculate_and_store_draft(v1_bundle, self.config, actor="acct")
            v1_frozen = svc.approve_and_freeze(v1_draft.snapshot_id, approver="ctrl")

            outcome = svc.publish_restatement(
                prior_snapshot_id=v1_frozen.snapshot_id,
                corrected_bundle=v2_bundle,
                config=self.config,
                reason="Late pricing vendor correction for EQ-US-ACME",
                actor="ctrl",
                approver="vp_val",
                auto_freeze=True,
            )
            v2 = outcome["snapshot"]
            diff = outcome["diff"]

            self.assertEqual(v2.version, 2)
            self.assertEqual(v2.prior_snapshot_id, v1_frozen.snapshot_id)
            self.assertEqual(str(v2.gross_assets), exp_v2["gross_assets"])
            self.assertEqual(str(v2.total_liabilities), exp_v2["total_liabilities"])
            self.assertEqual(str(v2.net_assets), exp_v2["net_assets"])
            self.assertEqual(diff["net_assets_delta"], exp_v2["net_assets_delta_vs_v1"])

            by_cls = {sc.share_class_id: sc for sc in v2.share_class_navs}
            self.assertEqual(
                str(by_cls["CLS-A-USD"].dealing_nav_per_share),
                exp_v2["share_classes"]["CLS-A-USD"]["dealing_nav_per_share"],
            )
            self.assertEqual(
                str(by_cls["CLS-A-USD"].subscription_shares),
                exp_v2["share_classes"]["CLS-A-USD"]["subscription_shares"],
            )
            self.assertEqual(
                str(by_cls["CLS-B-EUR"].nav_per_share),
                exp_v2["share_classes"]["CLS-B-EUR"]["nav_per_share"],
            )

            # Original v1 remains completely intact and retrievable
            reloaded_v1 = store.get_snapshot(v1_frozen.snapshot_id)
            self.assertEqual(reloaded_v1.output_hash, v1_frozen.output_hash)
            self.assertEqual(str(reloaded_v1.net_assets), self.expected["v1"]["net_assets"])


if __name__ == "__main__":
    unittest.main()
