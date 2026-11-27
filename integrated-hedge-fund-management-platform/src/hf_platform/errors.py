"""Structured domain and operational error hierarchy."""

from __future__ import annotations

from typing import Any


class PlatformError(Exception):
    """Base structured error with stable machine-readable code and HTTP status."""

    code: str = "PLATFORM_ERROR"
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.code,
            "message": self.message,
            "details": self.details,
        }


class DomainValidationError(PlatformError):
    code = "DOMAIN_VALIDATION_ERROR"
    http_status = 400


class NotFoundError(PlatformError):
    code = "NOT_FOUND"
    http_status = 404


class AuthorizationError(PlatformError):
    code = "AUTHORIZATION_ERROR"
    http_status = 403


class RoleSeparationError(AuthorizationError):
    code = "ROLE_SEPARATION_VIOLATION"
    http_status = 403


class LedgerBalanceError(PlatformError):
    code = "LEDGER_UNBALANCED"
    http_status = 422


class MissingMarketDataError(PlatformError):
    code = "MISSING_MARKET_DATA"
    http_status = 422


class CloseGateError(PlatformError):
    code = "CLOSE_GATE_BLOCKED"
    http_status = 409


class SnapshotImmutableError(PlatformError):
    code = "SNAPSHOT_IMMUTABLE"
    http_status = 409


class LateFillCutoffError(PlatformError):
    code = "LATE_FILL_AFTER_CUTOFF"
    http_status = 409


class ExternalServiceError(PlatformError):
    code = "EXTERNAL_SERVICE_UNAVAILABLE"
    http_status = 503


class ExecutionDisabledError(PlatformError):
    code = "ORDER_EXECUTION_DISABLED"
    http_status = 403


class AuditIntegrityError(PlatformError):
    code = "AUDIT_INTEGRITY_VIOLATION"
    http_status = 500
