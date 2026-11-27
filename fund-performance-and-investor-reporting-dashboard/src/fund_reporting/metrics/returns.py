"""Time-weighted return (TWR) and Modified Dietz return calculations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from fund_reporting.domain.enums import CashFlowTiming
from fund_reporting.domain.errors import MissingNavError
from fund_reporting.domain.models import NavSnapshot


@dataclass(frozen=True)
class DailyReturnPoint:
    valuation_date: str
    opening_nav: float
    net_cash_flow: float
    effective_capital: float
    gross_pnl: float
    total_fees: float
    net_pnl: float
    closing_nav: float
    gross_return: float
    net_return: float
    cumulative_gross_return: float
    cumulative_net_return: float
    gross_wealth_index: float
    net_wealth_index: float
    currency: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "valuation_date": self.valuation_date,
            "opening_nav": round(self.opening_nav, 6),
            "net_cash_flow": round(self.net_cash_flow, 6),
            "effective_capital": round(self.effective_capital, 6),
            "gross_pnl": round(self.gross_pnl, 6),
            "total_fees": round(self.total_fees, 6),
            "net_pnl": round(self.net_pnl, 6),
            "closing_nav": round(self.closing_nav, 6),
            "gross_return": self.gross_return,
            "net_return": self.net_return,
            "cumulative_gross_return": self.cumulative_gross_return,
            "cumulative_net_return": self.cumulative_net_return,
            "gross_wealth_index": self.gross_wealth_index,
            "net_wealth_index": self.net_wealth_index,
            "currency": self.currency,
        }


def compound_returns(returns: Sequence[float]) -> float:
    """Geometric compounding of a sequence of periodic decimal returns."""
    wealth = 1.0
    for r in returns:
        wealth *= 1.0 + r
    return wealth - 1.0


def compute_daily_twr_series(snapshots: Sequence[NavSnapshot]) -> List[DailyReturnPoint]:
    """Compute gross and net daily Time-Weighted Returns across external cash flows.

    For START_OF_DAY cash flows:
        effective_capital = opening_nav + net_cash_flow
        gross_return = gross_pnl / effective_capital
        net_return = (closing_nav - effective_capital) / effective_capital = net_pnl / effective_capital

    For END_OF_DAY cash flows:
        effective_capital = opening_nav
        gross_return = gross_pnl / opening_nav
        net_return = (closing_nav - net_cash_flow - opening_nav) / opening_nav = net_pnl / opening_nav
    """
    if not snapshots:
        raise MissingNavError("Cannot compute returns on empty NAV series")

    ordered = sorted(snapshots, key=lambda s: s.valuation_date)
    points: List[DailyReturnPoint] = []
    gross_wealth = 1.0
    net_wealth = 1.0

    for snap in ordered:
        net_cf = snap.net_cash_flow
        if snap.cash_flow_timing == CashFlowTiming.START_OF_DAY:
            effective_capital = snap.opening_nav + net_cf
        else:
            effective_capital = snap.opening_nav

        if effective_capital <= 0.0:
            raise MissingNavError(
                f"Non-positive effective capital ({effective_capital:.4f}) on {snap.valuation_date} after cash flow",
                details={
                    "valuation_date": snap.valuation_date,
                    "opening_nav": snap.opening_nav,
                    "net_cash_flow": net_cf,
                },
            )

        gross_ret = snap.gross_pnl / effective_capital
        net_ret = snap.net_pnl / effective_capital
        gross_wealth *= 1.0 + gross_ret
        net_wealth *= 1.0 + net_ret

        points.append(
            DailyReturnPoint(
                valuation_date=snap.valuation_date,
                opening_nav=snap.opening_nav,
                net_cash_flow=net_cf,
                effective_capital=effective_capital,
                gross_pnl=snap.gross_pnl,
                total_fees=snap.total_fees,
                net_pnl=snap.net_pnl,
                closing_nav=snap.closing_nav,
                gross_return=gross_ret,
                net_return=net_ret,
                cumulative_gross_return=gross_wealth - 1.0,
                cumulative_net_return=net_wealth - 1.0,
                gross_wealth_index=gross_wealth,
                net_wealth_index=net_wealth,
                currency=snap.currency,
            )
        )

    return points


def compute_modified_dietz(snapshots: Sequence[NavSnapshot]) -> Tuple[float, float]:
    """Compute period-level Modified Dietz (gross_return, net_return) for cash-flow cross-checking."""
    if not snapshots:
        raise MissingNavError("Cannot compute Modified Dietz on empty NAV series")

    ordered = sorted(snapshots, key=lambda s: s.valuation_date)
    n = len(ordered)
    v_start = ordered[0].opening_nav
    v_end = ordered[-1].closing_nav

    total_cf = 0.0
    weighted_cf = 0.0
    total_fees = 0.0

    for idx, snap in enumerate(ordered):
        cf = snap.net_cash_flow
        total_cf += cf
        total_fees += snap.total_fees
        if snap.cash_flow_timing == CashFlowTiming.START_OF_DAY:
            weight = (n - idx) / float(n)
        else:
            weight = (n - idx - 1) / float(n)
        weighted_cf += weight * cf

    denom = v_start + weighted_cf
    if denom <= 0.0:
        raise MissingNavError(
            "Non-positive Modified Dietz denominator",
            details={"v_start": v_start, "weighted_cf": weighted_cf},
        )

    net_gain = v_end - v_start - total_cf
    gross_gain = net_gain + total_fees
    return gross_gain / denom, net_gain / denom
