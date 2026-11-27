"""Deterministic return, risk, benchmark, currency, and attribution metrics library."""

from .attribution import compute_exposure_drilldown, compute_strategy_attribution
from .benchmark import compute_benchmark_comparison
from .currency import convert_nav_series, resolve_fx_rate
from .engine import AnalyticsConfig, AnalyticsEngine
from .returns import (
    DailyReturnPoint,
    compute_daily_twr_series,
    compute_modified_dietz,
    compound_returns,
)
from .risk import compute_drawdown_series, compute_risk_metrics

__all__ = [
    "AnalyticsConfig",
    "AnalyticsEngine",
    "DailyReturnPoint",
    "compound_returns",
    "compute_benchmark_comparison",
    "compute_daily_twr_series",
    "compute_drawdown_series",
    "compute_exposure_drilldown",
    "compute_modified_dietz",
    "compute_risk_metrics",
    "compute_strategy_attribution",
    "convert_nav_series",
    "resolve_fx_rate",
]
