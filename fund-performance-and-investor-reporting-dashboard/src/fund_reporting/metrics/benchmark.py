"""Benchmark comparison metrics: active return, tracking error, IR, beta, alpha, capture."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fund_reporting.domain.enums import MetricFlagCode
from fund_reporting.domain.models import BenchmarkPoint, FxRateRecord, MetricFlag

from .currency import resolve_fx_rate
from .returns import compound_returns

_ZERO_EPS = 1e-12


def compute_benchmark_comparison(
    fund_dates: Sequence[str],
    fund_returns: Sequence[float],
    benchmark_points: Sequence[BenchmarkPoint],
    *,
    reporting_currency: str,
    fx_rates: Sequence[FxRateRecord] = (),
    trading_days_per_year: int = 252,
    risk_free_rate_annual: float = 0.04,
    min_observations: int = 30,
) -> Tuple[Dict[str, Any], List[MetricFlag]]:
    """Align fund returns against benchmark points and compute relative analytics."""
    flags: List[MetricFlag] = []
    if not benchmark_points or not fund_dates:
        flags.append(
            MetricFlag(
                code=MetricFlagCode.BENCHMARK_GAP,
                metric="benchmark.alignment",
                message="Benchmark data unavailable for requested period.",
            )
        )
        return {"available": False, "aligned_observations": 0}, flags

    bm_by_date = {bp.valuation_date: bp for bp in benchmark_points}
    aligned_dates: List[str] = []
    p_rets: List[float] = []
    b_rets: List[float] = []
    missing_dates: List[str] = []

    prev_fx: Optional[float] = None
    for dt, p_ret in zip(fund_dates, fund_returns):
        bp = bm_by_date.get(dt)
        if bp is None:
            missing_dates.append(dt)
            continue
        b_ret = bp.daily_return
        if bp.currency != reporting_currency:
            curr_fx = resolve_fx_rate(fx_rates, dt, bp.currency, reporting_currency)
            if prev_fx is not None:
                b_ret = (1.0 + b_ret) * (curr_fx / prev_fx) - 1.0
            prev_fx = curr_fx
        aligned_dates.append(dt)
        p_rets.append(p_ret)
        b_rets.append(b_ret)

    if missing_dates:
        flags.append(
            MetricFlag(
                code=MetricFlagCode.BENCHMARK_GAP,
                metric="benchmark.alignment",
                message=f"Benchmark missing {len(missing_dates)} valuation date(s).",
            )
        )

    n = len(aligned_dates)
    if n == 0:
        return {"available": False, "aligned_observations": 0}, flags

    bm_id = benchmark_points[0].benchmark_id
    bm_cum = compound_returns(b_rets)
    p_cum = compound_returns(p_rets)
    active_cum = p_cum - bm_cum

    active_daily = [p - b for p, b in zip(p_rets, b_rets)]
    mean_active = sum(active_daily) / n
    mean_p = sum(p_rets) / n
    mean_b = sum(b_rets) / n

    tracking_error_ann: Optional[float]
    info_ratio: Optional[float]
    if n < 2:
        tracking_error_ann = 0.0
        info_ratio = None
        flags.append(
            MetricFlag(
                code=MetricFlagCode.ZERO_VOLATILITY,
                metric="benchmark.information_ratio",
                message="Insufficient observations for tracking error and information ratio.",
            )
        )
    else:
        te_var = sum((a - mean_active) ** 2 for a in active_daily) / (n - 1)
        te_daily = math.sqrt(max(0.0, te_var))
        tracking_error_ann = te_daily * math.sqrt(trading_days_per_year)
        if tracking_error_ann <= _ZERO_EPS:
            info_ratio = None
            flags.append(
                MetricFlag(
                    code=MetricFlagCode.ZERO_VOLATILITY,
                    metric="benchmark.information_ratio",
                    message="Zero tracking error; information ratio is omitted.",
                )
            )
        else:
            info_ratio = (mean_active * trading_days_per_year) / tracking_error_ann

    beta: Optional[float]
    alpha_ann: Optional[float]
    correlation: Optional[float]
    if n < 2:
        beta = None
        alpha_ann = None
        correlation = None
    else:
        cov = sum((p - mean_p) * (b - mean_b) for p, b in zip(p_rets, b_rets)) / (n - 1)
        var_b = sum((b - mean_b) ** 2 for b in b_rets) / (n - 1)
        var_p = sum((p - mean_p) ** 2 for p in p_rets) / (n - 1)
        if var_b <= _ZERO_EPS:
            beta = None
            alpha_ann = None
            correlation = None
            flags.append(
                MetricFlag(
                    code=MetricFlagCode.ZERO_VOLATILITY,
                    metric="benchmark.beta",
                    message="Zero benchmark variance; beta and alpha are omitted.",
                )
            )
        else:
            beta = cov / var_b
            rf_daily = risk_free_rate_annual / float(trading_days_per_year)
            alpha_ann = ((mean_p - rf_daily) - beta * (mean_b - rf_daily)) * trading_days_per_year
            correlation = cov / math.sqrt(var_p * var_b) if var_p > _ZERO_EPS else None

    if n < min_observations:
        flags.append(
            MetricFlag(
                code=MetricFlagCode.INADEQUATE_HISTORY,
                metric="benchmark.beta_and_alpha",
                message=f"Benchmark regression computed on {n} observations (< {min_observations} minimum).",
            )
        )

    up_p = [p for p, b in zip(p_rets, b_rets) if b > 0.0]
    up_b = [b for b in b_rets if b > 0.0]
    down_p = [p for p, b in zip(p_rets, b_rets) if b < 0.0]
    down_b = [b for b in b_rets if b < 0.0]

    up_capture: Optional[float] = None
    if up_b and abs(compound_returns(up_b)) > _ZERO_EPS:
        up_capture = compound_returns(up_p) / compound_returns(up_b)

    down_capture: Optional[float] = None
    if down_b and abs(compound_returns(down_b)) > _ZERO_EPS:
        down_capture = compound_returns(down_p) / compound_returns(down_b)

    bm_wealth = 1.0
    bm_series: List[Dict[str, Any]] = []
    for dt, b_ret in zip(aligned_dates, b_rets):
        bm_wealth *= 1.0 + b_ret
        bm_series.append(
            {
                "valuation_date": dt,
                "daily_return": b_ret,
                "cumulative_return": bm_wealth - 1.0,
                "wealth_index": bm_wealth,
            }
        )

    return {
        "available": True,
        "benchmark_id": bm_id,
        "reporting_currency": reporting_currency,
        "aligned_observations": n,
        "benchmark_cumulative_return": bm_cum,
        "fund_cumulative_return": p_cum,
        "active_cumulative_return": active_cum,
        "tracking_error_annualized": tracking_error_ann,
        "information_ratio": info_ratio,
        "beta": beta,
        "alpha_annualized": alpha_ann,
        "correlation": correlation,
        "up_capture_ratio": up_capture,
        "down_capture_ratio": down_capture,
        "series": bm_series,
    }, flags
