"""Structured domain errors for Prime Broker Collateral Management."""

from __future__ import annotations

from typing import Any, Dict, Optional


class CollateralDomainError(Exception):
    """Base error with machine-readable code and structured context."""

    code: str = "COLLATERAL_DOMAIN_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.message = message
        self.context: Dict[str, Any] = dict(context or {})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_code": self.code,
            "message": self.message,
            "context": self.context,
        }


class ValidationError(CollateralDomainError):
    """Raised when input payloads violate schema, unit, or domain constraints."""

    code = "VALIDATION_ERROR"


class UnsupportedInputError(CollateralDomainError):
    """Raised when an unsupported asset class, currency, day-count, or schema version is provided."""

    code = "UNSUPPORTED_INPUT"


class MissingConfigurationError(CollateralDomainError):
    """Raised when required policy, FX rate, or financing schedule configuration is missing."""

    code = "MISSING_CONFIGURATION"


class DuplicateStatementError(CollateralDomainError):
    """Raised when a conflicting statement ID or invalid version sequence is submitted."""

    code = "DUPLICATE_STATEMENT"


class DoubleCountingError(CollateralDomainError):
    """Raised when the same collateral lot is simultaneously counted across multiple brokers or pledges."""

    code = "COLLATERAL_DOUBLE_COUNTING"


class DayCountBoundaryError(CollateralDomainError):
    """Raised on invalid financing accrual date ranges or day-count boundary violations."""

    code = "DAY_COUNT_BOUNDARY_ERROR"


class PersistenceError(CollateralDomainError):
    """Raised when journal storage operations fail or immutability invariants are violated."""

    code = "PERSISTENCE_ERROR"
