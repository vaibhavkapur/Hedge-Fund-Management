"""Unit tests verifying exact hand-calculated returns, drawdowns, FX, and Carino attribution."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from fund_reporting.domain.enums import CashFlowTiming, DataStatus, EnvironmentMode, MetricFlagCode
from fund_reporting.domain.models import (
    FundDefinition,
    FxRateRecord,
    NavSnapshot,
    StrategySnapshot,
)
from fund_reporting.domain.validation import compute_ledger_hash
from fund_reporting.metrics.attribution import compute_strategy_attribution
from fund_reporting.metrics.engine import AnalyticsEngine
from fund_reporting.metrics.returns import compute_daily_twr_series

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "data" / "fixtures"


class TestHandCalculatedMetrics(unittest.TestCase):
    def test_hand_calculated_fixture_exact_match(self) -> None:
        bundle = json.loads((FIXTURES_DIR / "hand_calculated_case.json").read_text(encoding="utf-8"))
        expected = bundle["expected"]
        fund = FundDefinition.from_dict(bundle["fund"])
        snaps = [NavSnapshot.from_dict(s) for s in bundle["nav_snapshots"]]
        fx = [FxRateRecord.from_dict(r) for r in bundle["fx_rates"]]

        engine = AnalyticsEngine()
        res_usd = engine.analyze(fund, snaps, fx_rates=fx, reporting_currency="USD")

        self.assertAlmostEqual(
            res_usd["metrics"]["gross"]["cumulative_return"],
            expected["cumulative_gross_twr_usd"],
            places=10,
        )
        self.assertAlmostEqual(
            res_usd["metrics"]["net"]["cumulative_return"],
            expected["cumulative_net_twr_usd"],
            places=10,
        )
        self.assertAlmostEqual(
            res_usd["metrics"]["net"]["max_drawdown"],
            expected["max_net_drawdown_usd"],
            places=10,
        )
        self.assertEqual(
            res_usd["metrics"]["net"]["max_drawdown_peak_date"],
            expected["max_net_drawdown_peak_date"],
        )
        self.assertEqual(
            res_usd["metrics"]["net"]["max_drawdown_trough_date"],
            expected["max_net_drawdown_trough_date"],
        )
        self.assertEqual(
            res_usd["metrics"]["net"]["max_drawdown_recovery_date"],
            expected["max_net_drawdown_recovery_date"],
        )
        # 4-day history MUST be flagged for inadequate history
        flag_codes = {f["code"] for f in res_usd["flags"]}
        self.assertIn(MetricFlagCode.INADEQUATE_HISTORY.value, flag_codes)
        self.assertIn(MetricFlagCode.SIMULATED_DATA.value, flag_codes)

        # Exact EUR conversion check
        res_eur = engine.analyze(fund, snaps, fx_rates=fx, reporting_currency="EUR")
        self.assertAlmostEqual(
            res_eur["metrics"]["net"]["cumulative_return"],
            expected["cumulative_net_twr_eur"],
            places=10,
        )
        eur_flag_codes = {f["code"] for f in res_eur["flags"]}
        self.assertIn(MetricFlagCode.CURRENCY_CONVERTED.value, eur_flag_codes)
        self.assertAlmostEqual(res_eur["reconciliation_summary"]["nav_bridge_residual"], 0.0, places=4)

    def test_end_of_day_cash_flow_timing(self) -> None:
        # Open=100, GrossPnL=+10, Sub=+50 at EOD -> Close=160 -> TWR must be exactly +10%
        snap = NavSnapshot(
            snapshot_id="EOD-1",
            fund_id="F1",
            valuation_date="2026-03-02",
            as_of_utc="2026-03-02T21:00:00Z",
            availability_utc="2026-03-02T21:30:00Z",
            revision=1,
            currency="USD",
            environment=EnvironmentMode.SIMULATED,
            data_status=DataStatus.SYNTHETIC,
            opening_nav=100.0,
            gross_pnl=10.0,
            management_fee=0.0,
            performance_fee=0.0,
            other_expenses=0.0,
            subscriptions=50.0,
            redemptions=0.0,
            closing_nav=160.0,
            shares_outstanding=150.0,
            cash_balance=60.0,
            gross_exposure=100.0,
            net_exposure=100.0,
            ledger_reconciled=True,
            ledger_hash="h",
            provenance_source="TEST",
            cash_flow_timing=CashFlowTiming.END_OF_DAY,
        )
        pts = compute_daily_twr_series([snap])
        self.assertAlmostEqual(pts[0].gross_return, 0.10, places=10)
        self.assertAlmostEqual(pts[0].net_return, 0.10, places=10)

    def test_carino_attribution_sums_identically_to_fund_gross_twr(self) -> None:
        bundle = json.loads((FIXTURES_DIR / "fund_alpha_synthetic.json").read_text(encoding="utf-8"))
        snaps = [NavSnapshot.from_dict(s) for s in bundle["nav_snapshots"]]
        strats = [StrategySnapshot.from_dict(s) for s in bundle["strategy_snapshots"]]
        pts = compute_daily_twr_series(snaps)
        attr = compute_strategy_attribution(pts, strats, reporting_currency="USD")

        self.assertEqual(len(attr["strategies"]), 4)
        self.assertAlmostEqual(
            attr["total_linked_gross_contribution"],
            attr["fund_cumulative_gross_return"],
            places=8,
        )
        self.assertAlmostEqual(attr["residual_unattributed_return"], 0.0, places=8)


if __name__ == "__main__":
    unittest.main()
