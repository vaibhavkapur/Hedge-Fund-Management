"""Fund NAV Calculation Engine.

Educational and research prototype for deterministic daily fund NAV calculation,
ledger reconciliation, immutable valuation snapshots, and auditable restatements.
"""

from fund_nav_engine.calculator import calculate_nav
from fund_nav_engine.errors import (
    ConfigurationError,
    FlowTimingError,
    FxMismatchError,
    ImmutabilityViolationError,
    LedgerReconciliationError,
    MissingValuationError,
    NavEngineError,
    RestatementError,
    StalePriceError,
    ValidationError,
)
from fund_nav_engine.service import NavCalculationService
from fund_nav_engine.store import SqliteNavStore

__all__ = [
    "ConfigurationError",
    "FlowTimingError",
    "FxMismatchError",
    "ImmutabilityViolationError",
    "LedgerReconciliationError",
    "MissingValuationError",
    "NavCalculationService",
    "NavEngineError",
    "RestatementError",
    "SqliteNavStore",
    "StalePriceError",
    "ValidationError",
    "calculate_nav",
]
__version__ = "0.1.0"
