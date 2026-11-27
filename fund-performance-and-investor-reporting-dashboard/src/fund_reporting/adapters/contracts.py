"""Clean port interfaces for linked foundation repositories.

Per plan.md Section 2, external personal repositories (08-quantum-risk-engine,
02-hybrid-quantum-portfolio-optimizer, 09-transaction-cost-analysis) are integrated
exclusively behind documented adapter contracts until IP/licensing and production
readiness are formally cleared.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Sequence

from fund_reporting.domain.enums import EnvironmentMode
from fund_reporting.domain.models import StrategySnapshot


@dataclass(frozen=True)
class RiskEngineSnapshot:
    """Contract aligned with Quantum-Trading / 08-quantum-risk-engine."""

    fund_id: str
    as_of_date: str
    environment: EnvironmentMode
    engine_name: str
    parametric_var_95: float
    cvar_95: float
    stress_loss_2008: float
    stress_loss_rate_shock: float
    factor_exposures: Dict[str, float]
    provenance: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["environment"] = self.environment.value
        return d


@dataclass(frozen=True)
class OptimizerDriftReport:
    """Contract aligned with Quantum-Trading / 02-hybrid-quantum-portfolio-optimizer."""

    fund_id: str
    as_of_date: str
    environment: EnvironmentMode
    optimizer_name: str
    target_weights: Dict[str, float]
    actual_weights: Dict[str, float]
    weight_drift: Dict[str, float]
    max_abs_drift: float
    provenance: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["environment"] = self.environment.value
        return d


@dataclass(frozen=True)
class TcaSummaryReport:
    """Contract aligned with Trading-Software / 09-transaction-cost-analysis."""

    fund_id: str
    period_start: str
    period_end: str
    environment: EnvironmentMode
    currency: str
    total_transaction_costs: float
    estimated_slippage_bps: float
    estimated_commission_bps: float
    cost_by_strategy: List[Dict[str, Any]]
    provenance: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["environment"] = self.environment.value
        return d


class RiskEngineAdapter(ABC):
    @abstractmethod
    def get_risk_snapshot(
        self,
        fund_id: str,
        as_of_date: str,
        environment: EnvironmentMode,
        returns: Sequence[float],
    ) -> RiskEngineSnapshot:
        """Produce factor & tail risk diagnostics."""


class PortfolioOptimizerAdapter(ABC):
    @abstractmethod
    def get_allocation_drift(
        self,
        fund_id: str,
        as_of_date: str,
        environment: EnvironmentMode,
        latest_strategies: Sequence[StrategySnapshot],
    ) -> OptimizerDriftReport:
        """Compare actual strategy weights against target optimizer allocation."""


class TransactionCostAdapter(ABC):
    @abstractmethod
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
        """Summarize execution transaction costs and slippage drag."""
