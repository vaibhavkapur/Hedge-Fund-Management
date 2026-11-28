"""Structured domain exception hierarchy for the Fund NAV Calculation Engine."""

from __future__ import annotations

from typing import Any, Dict, Optional


class NavEngineError(Exception):
    """Base exception for all NAV engine errors with machine-readable code."""

    default_code: str = "NAV-0000"

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code or self.default_code
        self.message = message
        self.details: Dict[str, Any] = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": type(self).__name__,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class ConfigurationError(NavEngineError):
    """Raised when configuration is missing, malformed, or unsupported."""

    default_code = "NAV-1001"


class ValidationError(NavEngineError):
    """Raised when canonical input contracts fail schema or domain validation."""

    default_code = "NAV-1002"


class MissingValuationError(NavEngineError):
    """Raised when a held position has no matching valuation price."""

    default_code = "NAV-2001"


class StalePriceError(NavEngineError):
    """Raised when a valuation price exceeds the configured staleness limit."""

    default_code = "NAV-2002"


class FxMismatchError(NavEngineError):
    """Raised on currency mismatches or missing FX conversion rates."""

    default_code = "NAV-2003"


class FlowTimingError(NavEngineError):
    """Raised when subscription/redemption timing or share availability rules fail."""

    default_code = "NAV-3001"


class LedgerReconciliationError(NavEngineError):
    """Raised when calculated sub-ledger balances do not reconcile within tolerance."""

    default_code = "NAV-4001"


class ImmutabilityViolationError(NavEngineError):
    """Raised when attempting to modify or delete a frozen snapshot or audit record."""

    default_code = "NAV-5001"


class RestatementError(NavEngineError):
    """Raised when a restatement violates lineage or lifecycle preconditions."""

    default_code = "NAV-5002"
