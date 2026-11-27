"""Deterministic local adapters implementing upstream contracts on synthetic/reconciled data."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, Sequence

from fund_reporting.domain.enums import EnvironmentMode
from fund_reporting.domain.models import StrategySnapshot

from .contracts import (
    OptimizerDriftReport,
    PortfolioOptimizerAdapter,
    RiskEngineAdapter,
    RiskEngineSnapshot,
    TcaSummaryReport,
    TransactionCostAdapter,
)


class SyntheticRiskEngineAdapter(RiskEngineAdapter):
    """Local deterministic adapter standing in for 08-quantum-risk-engine."""

    def get_risk_snapshot(
        self,
        fund_id: str,
        as_of_date: str,
        environment: EnvironmentMode,
        returns: Sequence[float],
    ) -> RiskEngineSnapshot:
        if len(returns) >= 2:
            mean = sum(returns) / len(returns)
            std = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
            sorted_r = sorted(returns)
            idx = max(0, int(math.floor(0.05 * len(sorted_r))))
            tail = sorted_r[: idx + 1]
            cvar = sum(tail) / len(tail)
        else:
            mean = 0.0
            std = 0.0
            cvar = 0.0

        parametric_var = mean - 1.64485 * std
        return RiskEngineSnapshot(
            fund_id=fund_id,
            as_of_date=as_of_date,
            environment=environment,
            engine_name="LocalDeterministicRiskAdapter (contract: 08-quantum-risk-engine)",
            parametric_var_95=parametric_var,
            cvar_95=cvar,
            stress_loss_2008=min(-0.02, -4.5 * std) if std > 0 else 0.0,
            stress_loss_rate_shock=min(-0.01, -2.2 * std) if std > 0 else 0.0,
            factor_exposures={
                "market_beta_proxy": 0.42,
                "momentum_factor": 0.28,
                "volatility_carry": -0.15,
                "rates_duration": 0.12,
            },
            provenance="LOCAL_CONTRACT_ADAPTER_SYNTHETIC",
        )


class SyntheticPortfolioOptimizerAdapter(PortfolioOptimizerAdapter):
    """Local deterministic adapter standing in for 02-hybrid-quantum-portfolio-optimizer."""

    def __init__(self, target_weights: Dict[str, float] | None = None) -> None:
        self._target_weights = target_weights or {}

    def get_allocation_drift(
        self,
        fund_id: str,
        as_of_date: str,
        environment: EnvironmentMode,
        latest_strategies: Sequence[StrategySnapshot],
    ) -> OptimizerDriftReport:
        actual = {s.strategy_id: s.weight for s in latest_strategies}
        if self._target_weights:
            target = dict(self._target_weights)
        elif actual:
            equal_w = 1.0 / len(actual)
            target = {sid: round(equal_w, 4) for sid in sorted(actual)}
        else:
            target = {}

        all_ids = sorted(set(target.keys()) | set(actual.keys()))
        drift = {sid: round(actual.get(sid, 0.0) - target.get(sid, 0.0), 6) for sid in all_ids}
        max_abs = max((abs(v) for v in drift.values()), default=0.0)

        return OptimizerDriftReport(
            fund_id=fund_id,
            as_of_date=as_of_date,
            environment=environment,
            optimizer_name="LocalTargetWeightAdapter (contract: 02-hybrid-quantum-portfolio-optimizer)",
            target_weights=target,
            actual_weights=actual,
            weight_drift=drift,
            max_abs_drift=max_abs,
            provenance="LOCAL_CONTRACT_ADAPTER_SYNTHETIC",
        )


class SyntheticTransactionCostAdapter(TransactionCostAdapter):
    """Local deterministic adapter standing in for 09-transaction-cost-analysis."""

    def get_tca_summary(
        self,
        fund_id: str,
        period_start: str,
        period_end: str,
        environment: EnvironmentMode,
        currency: str,
        strategy_snapshots: Sequence[StrategySnapshot],
        avg_nav: float,
    ) -> TcaSummaryReport:
        by_strat: Dict[str, Dict[str, float]] = defaultdict(lambda: {"cost": 0.0, "gross_pnl": 0.0})
        names: Dict[str, str] = {}
        for st in strategy_snapshots:
            by_strat[st.strategy_id]["cost"] += st.transaction_costs
            by_strat[st.strategy_id]["gross_pnl"] += st.gross_pnl
            names[st.strategy_id] = st.strategy_name

        total_cost = sum(v["cost"] for v in by_strat.values())
        total_bps = (total_cost / avg_nav) * 10000.0 if avg_nav > 0 else 0.0
        slippage_bps = round(total_bps * 0.68, 3)
        commission_bps = round(total_bps * 0.32, 3)

        breakdown = []
        for sid, vals in sorted(by_strat.items()):
            cost_bps = (vals["cost"] / avg_nav) * 10000.0 if avg_nav > 0 else 0.0
            breakdown.append(
                {
                    "strategy_id": sid,
                    "strategy_name": names[sid],
                    "transaction_costs": round(vals["cost"], 4),
                    "cost_bps_of_nav": round(cost_bps, 3),
                }
            )

        return TcaSummaryReport(
            fund_id=fund_id,
            period_start=period_start,
            period_end=period_end,
            environment=environment,
            currency=currency,
            total_transaction_costs=round(total_cost, 4),
            estimated_slippage_bps=slippage_bps,
            estimated_commission_bps=commission_bps,
            cost_by_strategy=breakdown,
            provenance="LOCAL_CONTRACT_ADAPTER_SYNTHETIC",
        )
