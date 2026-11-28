"""Explicit domain exception hierarchy for the fee accounting engine."""

from __future__ import annotations

from typing import Optional


class FeeEngineError(Exception):
    """Base exception for all hedge fund fee accounting engine errors."""

    def __init__(self, message: str, *, code: str = "FEE_ENGINE_ERROR", field: Optional[str] = None) -> None:
        self.code = code
        self.field = field
        self.message = message
        prefix = f"[{code}]"
        if field:
            prefix = f"{prefix} ({field})"
        super().__init__(f"{prefix} {message}")


class ValidationError(FeeEngineError):
    """Raised when input records violate schema or domain invariants."""

    def __init__(self, message: str, *, code: str = "VALIDATION_ERROR", field: Optional[str] = None) -> None:
        super().__init__(message, code=code, field=field)


class UnsupportedPolicyError(FeeEngineError):
    """Raised when a fee policy, currency, hurdle, or convention is unsupported."""

    def __init__(self, message: str, *, code: str = "UNSUPPORTED_POLICY", field: Optional[str] = None) -> None:
        super().__init__(message, code=code, field=field)


class CalendarBoundaryError(FeeEngineError):
    """Raised when valuation or crystallization dates violate calendar rules."""

    def __init__(self, message: str, *, code: str = "CALENDAR_BOUNDARY_ERROR", field: Optional[str] = None) -> None:
        super().__init__(message, code=code, field=field)


class ReconciliationError(FeeEngineError):
    """Raised when journal entries, fee totals, or NAV bridges fail to reconcile."""

    def __init__(self, message: str, *, code: str = "RECONCILIATION_ERROR", field: Optional[str] = None) -> None:
        super().__init__(message, code=code, field=field)


class StorageError(FeeEngineError):
    """Raised on persistence, immutability, or version conflict violations."""

    def __init__(self, message: str, *, code: str = "STORAGE_ERROR", field: Optional[str] = None) -> None:
        super().__init__(message, code=code, field=field)


class ApprovalError(FeeEngineError):
    """Raised on invalid approval state transitions."""

    def __init__(self, message: str, *, code: str = "APPROVAL_ERROR", field: Optional[str] = None) -> None:
        super().__init__(message, code=code, field=field)
