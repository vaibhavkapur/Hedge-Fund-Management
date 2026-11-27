"""Structured domain error hierarchy with stable codes and HTTP statuses."""

from __future__ import annotations

from typing import Any, Dict, Optional


class DomainError(Exception):
    """Base structured error for all domain, validation, and operational failures."""

    code: str = "DOMAIN_ERROR"
    http_status: int = 400

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        http_status: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status
        self.details: Dict[str, Any] = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class ValidationError(DomainError):
    code = "VALIDATION_ERROR"
    http_status = 422


class MissingNavError(DomainError):
    code = "MISSING_NAV"
    http_status = 422


class ReconciliationError(DomainError):
    code = "LEDGER_RECONCILIATION_FAILED"
    http_status = 422


class MixedEnvironmentError(DomainError):
    code = "MIXED_ENVIRONMENT_PROHIBITED"
    http_status = 422


class CurrencyConversionError(DomainError):
    code = "MISSING_FX_RATE"
    http_status = 422


class AuthorizationError(DomainError):
    code = "UNAUTHORIZED_ACCESS"
    http_status = 403


class WorkflowStateError(DomainError):
    code = "INVALID_WORKFLOW_TRANSITION"
    http_status = 409


class ImmutableReportError(DomainError):
    code = "IMMUTABLE_PUBLISHED_REPORT"
    http_status = 409


class NotFoundError(DomainError):
    code = "RESOURCE_NOT_FOUND"
    http_status = 404
