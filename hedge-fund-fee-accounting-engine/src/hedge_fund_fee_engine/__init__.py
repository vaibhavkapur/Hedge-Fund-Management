"""Hedge Fund Fee Accounting Engine package.

Educational and research prototype for deterministic management and performance
fee accrual, crystallization, investor lot tracking, and immutable double-entry
journaling using exact Decimal arithmetic and SQLite.
"""

from .errors import (
    ApprovalError,
    CalendarBoundaryError,
    FeeEngineError,
    ReconciliationError,
    StorageError,
    UnsupportedPolicyError,
    ValidationError,
)
from .models import (
    ApprovalRecord,
    ApprovalStatus,
    CalculationRunResult,
    CapitalFlow,
    CapitalFlowType,
    CrystallizationFrequency,
    DayCountConvention,
    EngineConfig,
    FeeAgreement,
    FeeCorrection,
    HurdleType,
    HwmResetPolicy,
    InvestorLot,
    JournalEntry,
    JournalEntryType,
    JournalLine,
    LotFeeCalculation,
    ManagementFeeBasis,
    NavSnapshot,
    ReconciliationReport,
)
from .service import FeeAccountingService

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "ApprovalError",
    "ApprovalRecord",
    "ApprovalStatus",
    "CalculationRunResult",
    "CalendarBoundaryError",
    "CapitalFlow",
    "CapitalFlowType",
    "CrystallizationFrequency",
    "DayCountConvention",
    "EngineConfig",
    "FeeAccountingService",
    "FeeAgreement",
    "FeeCorrection",
    "FeeEngineError",
    "HurdleType",
    "HwmResetPolicy",
    "InvestorLot",
    "JournalEntry",
    "JournalEntryType",
    "JournalLine",
    "LotFeeCalculation",
    "ManagementFeeBasis",
    "NavSnapshot",
    "ReconciliationError",
    "ReconciliationReport",
    "StorageError",
    "UnsupportedPolicyError",
    "ValidationError",
]
