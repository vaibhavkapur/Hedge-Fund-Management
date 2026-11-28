"""Typed domain, validation, workflow, and reconciliation exceptions."""

from __future__ import annotations

from typing import Any, Dict, Optional


class ISRPBaseError(Exception):
    """Base error with machine-readable code and structured details."""

    code: str = "ISRP_ERROR"
    http_status: int = 400

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_code": self.code,
            "message": self.message,
            "details": self.details,
        }


class ConfigurationError(ISRPBaseError):
    code = "CONFIGURATION_ERROR"
    http_status = 500


class ValidationError(ISRPBaseError):
    code = "VALIDATION_ERROR"
    http_status = 422


class NotFoundError(ISRPBaseError):
    code = "NOT_FOUND"
    http_status = 404


class InvalidStateTransitionError(ISRPBaseError):
    code = "INVALID_STATE_TRANSITION"
    http_status = 409


class DuplicateRequestError(ISRPBaseError):
    code = "DUPLICATE_REQUEST"
    http_status = 409


class CutoffViolationError(ISRPBaseError):
    code = "CUTOFF_VIOLATION"
    http_status = 422


class InsufficientSharesError(ISRPBaseError):
    code = "INSUFFICIENT_SHARES"
    http_status = 422


class ReviewerSeparationError(ISRPBaseError):
    code = "REVIEWER_SEPARATION_VIOLATION"
    http_status = 403


class UnapprovedRequestError(ISRPBaseError):
    code = "UNAPPROVED_REQUEST"
    http_status = 409


class ReconciliationError(ISRPBaseError):
    code = "RECONCILIATION_ERROR"
    http_status = 409
