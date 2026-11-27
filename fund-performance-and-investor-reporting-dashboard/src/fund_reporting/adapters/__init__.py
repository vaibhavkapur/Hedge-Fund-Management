"""Clean adapter contracts for upstream risk, portfolio optimization, and TCA foundations."""

from .contracts import (
    OptimizerDriftReport,
    PortfolioOptimizerAdapter,
    RiskEngineAdapter,
    RiskEngineSnapshot,
    TcaSummaryReport,
    TransactionCostAdapter,
)
from .synthetic_adapters import (
    SyntheticPortfolioOptimizerAdapter,
    SyntheticRiskEngineAdapter,
    SyntheticTransactionCostAdapter,
)

__all__ = [
    "OptimizerDriftReport",
    "PortfolioOptimizerAdapter",
    "RiskEngineAdapter",
    "RiskEngineSnapshot",
    "SyntheticPortfolioOptimizerAdapter",
    "SyntheticRiskEngineAdapter",
    "SyntheticTransactionCostAdapter",
    "TcaSummaryReport",
    "TransactionCostAdapter",
]
