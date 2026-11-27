"""Strategy attribution (Carino multi-period linking) and cash/exposure drill-down."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Sequence

from fund_reporting.domain.models import FxRateRecord, NavSnapshot, StrategySnapshot
from fund_reporting.domain.validation import validate_environment_homogeneity

from .currency import resolve_fx_rate
from .returns import DailyReturnPoint


def _carino_factor(ret: float) -> float:
    if abs(ret) < 1e-12:
        return 1.0
    if ret <= -1.0:
        return 1.0
    return math.log(1.0 + ret) / ret


def compute_strategy_attribution(
    return_points: Sequence[DailyReturnPoint],
    strategy_snapshots: Sequence[StrategySnapshot],
    *,
    reporting_currency: str,
    fx_rates: Sequence[FxRateRecord] = (),
) -> Dict[str, Any]:
    """Compute Carino-linked multi-period return contribution and TCA drag per strategy."""
    if not return_points or not strategy_snapshots:
        return {
            "strategies": [],
            "total_linked_gross_contribution": 0.0,
            "fund_cumulative_gross_return": return_points[-1].cumulative_gross_return if return_points else 0.0,
            "residual_unattributed_return": return_points[-1].cumulative_gross_return if return_points else 0.0,
            "total_transaction_costs": 0.0,
        }

    validate_environment_homogeneity(
        (s.environment for s in strategy_snapshots), context="strategy_snapshots"
    )

    ret_by_date = {pt.valuation_date: pt for pt in return_points}
    cum_gross = return_points[-1].cumulative_gross_return
    total_k = _carino_factor(cum_gross)

    grouped_by_date: Dict[str, List[StrategySnapshot]] = defaultdict(list)
    for st in strategy_snapshots:
        if st.valuation_date in ret_by_date:
            grouped_by_date[st.valuation_date].append(st)

    agg: Dict[str, Dict[str, Any]] = {}
    for dt, pt in ret_by_date.items():
        day_k = _carino_factor(pt.gross_return)
        link_mult = day_k / total_k if abs(total_k) > 1e-12 else 1.0

        for st in grouped_by_date.get(dt, []):
            fx = resolve_fx_rate(fx_rates, dt, st.currency, reporting_currency)
            pnl_rc = st.gross_pnl * fx
            tca_rc = st.transaction_costs * fx
            long_rc = st.long_exposure * fx
            short_rc = st.short_exposure * fx
            cash_rc = st.cash_allocation * fx

            daily_contrib = pnl_rc / pt.effective_capital
            linked_contrib = daily_contrib * link_mult
            tca_drag = tca_rc / pt.effective_capital

            entry = agg.setdefault(
                st.strategy_id,
                {
                    "strategy_id": st.strategy_id,
                    "strategy_name": st.strategy_name,
                    "asset_class": st.asset_class,
                    "observations": 0,
                    "weight_sum": 0.0,
                    "total_gross_pnl": 0.0,
                    "total_transaction_costs": 0.0,
                    "linked_gross_contribution": 0.0,
                    "tca_return_drag": 0.0,
                    "latest_weight": 0.0,
                    "latest_long_exposure": 0.0,
                    "latest_short_exposure": 0.0,
                    "latest_cash_allocation": 0.0,
                    "latest_date": "",
                },
            )
            entry["observations"] += 1
            entry["weight_sum"] += st.weight
            entry["total_gross_pnl"] += pnl_rc
            entry["total_transaction_costs"] += tca_rc
            entry["linked_gross_contribution"] += linked_contrib
            entry["tca_return_drag"] += tca_drag
            if dt >= entry["latest_date"]:
                entry["latest_date"] = dt
                entry["latest_weight"] = st.weight
                entry["latest_long_exposure"] = long_rc
                entry["latest_short_exposure"] = short_rc
                entry["latest_cash_allocation"] = cash_rc

    strategies_out: List[Dict[str, Any]] = []
    for entry in agg.values():
        obs = max(1, entry["observations"])
        latest_gross = abs(entry["latest_long_exposure"]) + abs(entry["latest_short_exposure"])
        latest_net = entry["latest_long_exposure"] - abs(entry["latest_short_exposure"])
        strategies_out.append(
            {
                "strategy_id": entry["strategy_id"],
                "strategy_name": entry["strategy_name"],
                "asset_class": entry["asset_class"],
                "average_weight": entry["weight_sum"] / obs,
                "latest_weight": entry["latest_weight"],
                "total_gross_pnl": round(entry["total_gross_pnl"], 4),
                "total_transaction_costs": round(entry["total_transaction_costs"], 4),
                "linked_gross_contribution": entry["linked_gross_contribution"],
                "tca_return_drag": entry["tca_return_drag"],
                "latest_long_exposure": round(entry["latest_long_exposure"], 4),
                "latest_short_exposure": round(entry["latest_short_exposure"], 4),
                "latest_gross_exposure": round(latest_gross, 4),
                "latest_net_exposure": round(latest_net, 4),
                "latest_cash_allocation": round(entry["latest_cash_allocation"], 4),
                "currency": reporting_currency,
            }
        )

    strategies_out.sort(key=lambda x: x["linked_gross_contribution"], reverse=True)
    total_linked = sum(s["linked_gross_contribution"] for s in strategies_out)
    total_tca = sum(s["total_transaction_costs"] for s in strategies_out)

    return {
        "currency": reporting_currency,
        "linking_methodology": "CARINO_LOGARITHMIC",
        "strategies": strategies_out,
        "total_linked_gross_contribution": total_linked,
        "fund_cumulative_gross_return": cum_gross,
        "residual_unattributed_return": cum_gross - total_linked,
        "total_transaction_costs": round(total_tca, 4),
    }


def compute_exposure_drilldown(
    snapshots: Sequence[NavSnapshot],
    strategy_snapshots: Sequence[StrategySnapshot],
    *,
    reporting_currency: str,
    fx_rates: Sequence[FxRateRecord] = (),
) -> Dict[str, Any]:
    """Build time-series cash/exposure profile and latest asset-class/strategy drill-down."""
    if not snapshots:
        return {"currency": reporting_currency, "latest": {}, "time_series": [], "by_asset_class": []}

    ordered = sorted(snapshots, key=lambda s: s.valuation_date)
    latest_snap = ordered[-1]
    latest_date = latest_snap.valuation_date

    time_series: List[Dict[str, Any]] = []
    for snap in ordered:
        nav = snap.closing_nav
        time_series.append(
            {
                "valuation_date": snap.valuation_date,
                "closing_nav": round(nav, 4),
                "cash_balance": round(snap.cash_balance, 4),
                "cash_pct_nav": snap.cash_balance / nav if nav else 0.0,
                "gross_exposure": round(snap.gross_exposure, 4),
                "gross_exposure_pct_nav": snap.gross_exposure / nav if nav else 0.0,
                "net_exposure": round(snap.net_exposure, 4),
                "net_exposure_pct_nav": snap.net_exposure / nav if nav else 0.0,
            }
        )

    asset_buckets: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"long_exposure": 0.0, "short_exposure": 0.0, "cash_allocation": 0.0, "weight": 0.0}
    )
    for st in strategy_snapshots:
        if st.valuation_date != latest_date:
            continue
        fx = resolve_fx_rate(fx_rates, latest_date, st.currency, reporting_currency)
        b = asset_buckets[st.asset_class]
        b["long_exposure"] += st.long_exposure * fx
        b["short_exposure"] += abs(st.short_exposure) * fx
        b["cash_allocation"] += st.cash_allocation * fx
        b["weight"] += st.weight

    nav = latest_snap.closing_nav
    by_asset_class = []
    for ac, vals in sorted(asset_buckets.items()):
        gross = vals["long_exposure"] + vals["short_exposure"]
        net = vals["long_exposure"] - vals["short_exposure"]
        by_asset_class.append(
            {
                "asset_class": ac,
                "weight": vals["weight"],
                "long_exposure": round(vals["long_exposure"], 4),
                "short_exposure": round(vals["short_exposure"], 4),
                "gross_exposure": round(gross, 4),
                "net_exposure": round(net, 4),
                "gross_pct_nav": gross / nav if nav else 0.0,
                "net_pct_nav": net / nav if nav else 0.0,
                "cash_allocation": round(vals["cash_allocation"], 4),
            }
        )

    return {
        "currency": reporting_currency,
        "as_of_date": latest_date,
        "latest": time_series[-1],
        "by_asset_class": by_asset_class,
        "time_series": time_series,
    }
