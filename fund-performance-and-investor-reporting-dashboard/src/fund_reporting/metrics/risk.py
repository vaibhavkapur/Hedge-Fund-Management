"""Deterministic risk, drawdown, and tail-risk metrics with explicit edge-case flags."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fund_reporting.domain.enums import MetricFlagCode
from fund_reporting.domain.models import MetricFlag

_ZERO_EPS = 1e-12


def compute_drawdown_series(
    dates: Sequence[str],
    returns: Sequence[float],
) -> Dict[str, Any]:
    """Compute wealth curve, underwater drawdown series, max drawdown, and peak/trough/recovery."""
    if not dates or len(dates) != len(returns):
        return {
            "series": [],
            "max_drawdown": 0.0,
            "current_drawdown": 0.0,
            "peak_date": None,
            "trough_date": None,
            "recovery_date": None,
            "ulcer_index": 0.0,
        }

    wealth = 1.0
    running_peak = 1.0
    running_peak_date = dates[0]

    max_dd = 0.0
    max_dd_peak_date: Optional[str] = dates[0]
    max_dd_trough_date: Optional[str] = dates[0]
    max_dd_peak_level = 1.0
    trough_idx = 0

    series: List[Dict[str, Any]] = []
    sq_dd_sum = 0.0

    for idx, (dt, ret) in enumerate(zip(dates, returns)):
        wealth *= 1.0 + ret
        if wealth >= running_peak:
            running_peak = wealth
            running_peak_date = dt
        dd = (wealth / running_peak) - 1.0
        sq_dd_sum += (dd * 100.0) ** 2

        if dd < max_dd:
            max_dd = dd
            max_dd_peak_date = running_peak_date
            max_dd_trough_date = dt
            max_dd_peak_level = running_peak
            trough_idx = idx

        series.append(
            {
                "valuation_date": dt,
                "wealth_index": wealth,
                "running_peak": running_peak,
                "drawdown": dd,
            }
        )

    recovery_date: Optional[str] = None
    if max_dd < -_ZERO_EPS:
        for point in series[trough_idx + 1 :]:
            if point["wealth_index"] >= max_dd_peak_level - 1e-12:
                recovery_date = str(point["valuation_date"])
                break
    else:
        max_dd_peak_date = None
        max_dd_trough_date = None

    ulcer_index = math.sqrt(sq_dd_sum / len(series)) / 100.0

    return {
        "series": series,
        "max_drawdown": max_dd,
        "current_drawdown": series[-1]["drawdown"],
        "peak_date": max_dd_peak_date,
        "trough_date": max_dd_trough_date,
        "recovery_date": recovery_date,
        "ulcer_index": ulcer_index,
    }


def _sample_std(values: Sequence[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    std = math.sqrt(max(0.0, var))
    return 0.0 if std <= _ZERO_EPS else std


def _historical_var_cvar(returns: Sequence[float], confidence: float = 0.95) -> Tuple[Optional[float], Optional[float]]:
    if not returns:
        return None, None
    sorted_rets = sorted(returns)
    alpha = 1.0 - confidence
    # Lower-tail index (0-based)
    idx = max(0, min(len(sorted_rets) - 1, int(math.floor(alpha * len(sorted_rets)))))
    var_val = sorted_rets[idx]
    tail = sorted_rets[: idx + 1]
    cvar_val = sum(tail) / len(tail)
    return var_val, cvar_val


def compute_risk_metrics(
    dates: Sequence[str],
    returns: Sequence[float],
    *,
    trading_days_per_year: int = 252,
    risk_free_rate_annual: float = 0.04,
    min_history: Optional[Dict[str, int]] = None,
    basis_label: str = "NET",
) -> Tuple[Dict[str, Any], List[MetricFlag]]:
    """Compute annualized return, volatility, Sharpe, Sortino, Calmar, VaR/CVaR, and flags."""
    thresholds = {
        "annualized_return": 20,
        "annualized_volatility": 20,
        "sharpe_ratio": 60,
        "sortino_ratio": 60,
        "calmar_ratio": 60,
        "var_cvar": 30,
    }
    if min_history:
        thresholds.update(min_history)

    flags: List[MetricFlag] = []
    n = len(returns)

    if n == 0:
        flags.append(
            MetricFlag(
                code=MetricFlagCode.INADEQUATE_HISTORY,
                metric=f"{basis_label}.all",
                message="Zero observations available.",
                severity="ERROR",
            )
        )
        return {"observation_count": 0}, flags

    wealth = 1.0
    for r in returns:
        wealth *= 1.0 + r
    cumulative_return = wealth - 1.0

    ann_return: Optional[float] = None
    if wealth > 0.0:
        ann_return = (wealth ** (trading_days_per_year / float(n))) - 1.0
    if n < thresholds["annualized_return"]:
        flags.append(
            MetricFlag(
                code=MetricFlagCode.INADEQUATE_HISTORY,
                metric=f"{basis_label}.annualized_return",
                message=(
                    f"Only {n} observation(s) available (minimum {thresholds['annualized_return']} "
                    "recommended before relying on annualized returns)."
                ),
            )
        )

    mean_daily = sum(returns) / n
    daily_vol = _sample_std(returns)
    ann_vol = daily_vol * math.sqrt(trading_days_per_year)

    if n < thresholds["annualized_volatility"]:
        flags.append(
            MetricFlag(
                code=MetricFlagCode.INADEQUATE_HISTORY,
                metric=f"{basis_label}.annualized_volatility",
                message=f"Only {n} observation(s) available (minimum {thresholds['annualized_volatility']}).",
            )
        )

    rf_daily = risk_free_rate_annual / float(trading_days_per_year)
    excess_daily = [r - rf_daily for r in returns]
    mean_excess = sum(excess_daily) / n
    excess_vol = _sample_std(excess_daily)

    sharpe: Optional[float]
    if n < 2 or excess_vol <= _ZERO_EPS:
        sharpe = None
        flags.append(
            MetricFlag(
                code=MetricFlagCode.ZERO_VOLATILITY,
                metric=f"{basis_label}.sharpe_ratio",
                message="Zero or undefined return volatility; Sharpe ratio is omitted rather than infinite.",
            )
        )
    else:
        sharpe = (mean_excess / excess_vol) * math.sqrt(trading_days_per_year)
        if n < thresholds["sharpe_ratio"]:
            flags.append(
                MetricFlag(
                    code=MetricFlagCode.INADEQUATE_HISTORY,
                    metric=f"{basis_label}.sharpe_ratio",
                    message=f"Sharpe ratio computed on {n} observations (< {thresholds['sharpe_ratio']} minimum).",
                )
            )

    downside_sq = [min(0.0, r - rf_daily) ** 2 for r in returns]
    downside_dev_daily = math.sqrt(sum(downside_sq) / n)
    downside_dev_ann = downside_dev_daily * math.sqrt(trading_days_per_year)

    sortino: Optional[float]
    if downside_dev_daily <= _ZERO_EPS:
        sortino = None
        flags.append(
            MetricFlag(
                code=MetricFlagCode.ZERO_VOLATILITY,
                metric=f"{basis_label}.sortino_ratio",
                message="Zero downside deviation; Sortino ratio is omitted rather than infinite.",
            )
        )
    else:
        sortino = (mean_excess * trading_days_per_year) / downside_dev_ann
        if n < thresholds["sortino_ratio"]:
            flags.append(
                MetricFlag(
                    code=MetricFlagCode.INADEQUATE_HISTORY,
                    metric=f"{basis_label}.sortino_ratio",
                    message=f"Sortino ratio computed on {n} observations (< {thresholds['sortino_ratio']} minimum).",
                )
            )

    dd_info = compute_drawdown_series(dates, returns)
    max_dd = float(dd_info["max_drawdown"])

    calmar: Optional[float]
    if abs(max_dd) <= _ZERO_EPS or ann_return is None:
        calmar = None
        flags.append(
            MetricFlag(
                code=MetricFlagCode.ZERO_VOLATILITY,
                metric=f"{basis_label}.calmar_ratio",
                message="Zero maximum drawdown; Calmar ratio is omitted.",
            )
        )
    else:
        calmar = ann_return / abs(max_dd)
        if n < thresholds["calmar_ratio"]:
            flags.append(
                MetricFlag(
                    code=MetricFlagCode.INADEQUATE_HISTORY,
                    metric=f"{basis_label}.calmar_ratio",
                    message=f"Calmar ratio computed on {n} observations (< {thresholds['calmar_ratio']} minimum).",
                )
            )

    var_95, cvar_95 = _historical_var_cvar(returns, confidence=0.95)
    if n < thresholds["var_cvar"]:
        flags.append(
            MetricFlag(
                code=MetricFlagCode.INADEQUATE_HISTORY,
                metric=f"{basis_label}.var_95_daily",
                message=f"Historical VaR/CVaR computed on {n} observations (< {thresholds['var_cvar']} minimum).",
            )
        )

    positive_days = sum(1 for r in returns if r > 0.0)
    win_rate = positive_days / float(n)

    metrics = {
        "observation_count": n,
        "cumulative_return": cumulative_return,
        "annualized_return": ann_return,
        "mean_daily_return": mean_daily,
        "daily_volatility": daily_vol,
        "annualized_volatility": ann_vol,
        "downside_deviation_annualized": downside_dev_ann,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "max_drawdown": max_dd,
        "current_drawdown": dd_info["current_drawdown"],
        "max_drawdown_peak_date": dd_info["peak_date"],
        "max_drawdown_trough_date": dd_info["trough_date"],
        "max_drawdown_recovery_date": dd_info["recovery_date"],
        "ulcer_index": dd_info["ulcer_index"],
        "var_95_daily": var_95,
        "cvar_95_daily": cvar_95,
        "best_day_return": max(returns),
        "worst_day_return": min(returns),
        "win_rate": win_rate,
        "drawdown_series": dd_info["series"],
    }
    return metrics, flags
