"""Unit tests for risk metrics, zero volatility handling, and benchmark comparison."""

from __future__ import annotations

import math
import unittest

from fund_reporting.domain.enums import MetricFlagCode
from fund_reporting.domain.models import BenchmarkPoint
from fund_reporting.metrics.benchmark import compute_benchmark_comparison
from fund_reporting.metrics.risk import compute_risk_metrics


class TestRiskAndBenchmark(unittest.TestCase):
    def test_zero_volatility_returns_none_and_flags(self) -> None:
        dates = [f"2026-01-{day:02d}" for day in range(1, 26)]
        flat_returns = [0.001] * len(dates)
        metrics, flags = compute_risk_metrics(dates, flat_returns, basis_label="NET")

        self.assertEqual(metrics["annualized_volatility"], 0.0)
        self.assertIsNone(metrics["sharpe_ratio"])
        self.assertIsNone(metrics["sortino_ratio"])
        self.assertIsNone(metrics["calmar_ratio"])
        self.assertEqual(metrics["max_drawdown"], 0.0)
        flag_codes = {f.code for f in flags}
        self.assertIn(MetricFlagCode.ZERO_VOLATILITY, flag_codes)

    def test_benchmark_identical_series_zero_tracking_error(self) -> None:
        dates = [f"2026-01-{day:02d}" for day in range(1, 35)]
        rets = [0.01 if i % 2 == 0 else -0.005 for i in range(len(dates))]
        bm_pts = [
            BenchmarkPoint(
                benchmark_id="BM-1",
                valuation_date=dt,
                currency="USD",
                daily_return=r,
                level=100.0,
                source="TEST",
            )
            for dt, r in zip(dates, rets)
        ]
        res, flags = compute_benchmark_comparison(dates, rets, bm_pts, reporting_currency="USD")

        self.assertTrue(res["available"])
        self.assertAlmostEqual(res["active_cumulative_return"], 0.0, places=10)
        self.assertAlmostEqual(res["tracking_error_annualized"], 0.0, places=10)
        self.assertIsNone(res["information_ratio"])
        self.assertAlmostEqual(res["beta"], 1.0, places=10)
        self.assertAlmostEqual(res["correlation"], 1.0, places=10)
        self.assertTrue(any(f.code == MetricFlagCode.ZERO_VOLATILITY for f in flags))

    def test_benchmark_gap_is_flagged(self) -> None:
        dates = ["2026-01-02", "2026-01-03", "2026-01-04"]
        rets = [0.01, -0.005, 0.007]
        bm_pts = [
            BenchmarkPoint("BM-1", "2026-01-02", "USD", 0.008, 100.8, "TEST"),
            BenchmarkPoint("BM-1", "2026-01-04", "USD", 0.004, 101.2, "TEST"),
        ]
        res, flags = compute_benchmark_comparison(dates, rets, bm_pts, reporting_currency="USD")
        self.assertEqual(res["aligned_observations"], 2)
        self.assertTrue(any(f.code == MetricFlagCode.BENCHMARK_GAP for f in flags))

    def test_var_cvar_ordering(self) -> None:
        dates = [f"2026-02-{d:02d}" for d in range(1, 41)]
        rets = [((i % 7) - 3) * 0.008 for i in range(40)]
        metrics, _ = compute_risk_metrics(dates, rets, basis_label="NET")
        self.assertIsNotNone(metrics["var_95_daily"])
        self.assertIsNotNone(metrics["cvar_95_daily"])
        self.assertLessEqual(metrics["cvar_95_daily"], metrics["var_95_daily"])
        self.assertTrue(math.isfinite(metrics["sharpe_ratio"]))


if __name__ == "__main__":
    unittest.main()
