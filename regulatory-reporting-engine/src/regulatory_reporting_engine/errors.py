"""Structured domain exception hierarchy for the Regulatory Reporting Engine."""

from __future__ import annotations

from typing import Any, Dict, Optional


class RegulatoryEngineError(Exception):
    """Base exception for all regulatory reporting errors with machine-readable code."""

    default_code: str = "REG-0000"

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


class ConfigurationError(RegulatoryEngineError):
    """Raised when configuration is missing, malformed, or violates safety invariants."""

    default_code = "REG-1001"


class MissingRequiredDataError(RegulatoryEngineError):
    """Raised when required snapshot, NAV, position, exposure, or mapped fields are absent."""

    default_code = "REG-1002"


class InvalidIdentifierError(RegulatoryEngineError):
    """Raised when an LEI, ISIN, ISO currency, country code, or entity identifier fails validation."""

    default_code = "REG-1003"


class UnitMismatchError(RegulatoryEngineError):
    """Raised when currencies, units, or decimal scales conflict during mapping or validation."""

    default_code = "REG-1004"


class UnsupportedInputError(RegulatoryEngineError):
    """Raised when an unsupported profile, format, aggregation, or contract version is requested."""

    default_code = "REG-1005"


class SchemaEvolutionError(RegulatoryEngineError):
    """Raised when a schema version changes incompatibly relative to a mapping or prior version."""

    default_code = "REG-2001"


class SchemaValidationError(RegulatoryEngineError):
    """Raised when generated report fields, XML, or CSV payloads fail profile schema validation."""

    default_code = "REG-2002"


class MappingError(RegulatoryEngineError):
    """Raised when a field mapping rule cannot be resolved or fails lineage completeness."""

    default_code = "REG-2003"


class CrossReportReconciliationError(RegulatoryEngineError):
    """Raised when corresponding totals across reports or schedules do not reconcile."""

    default_code = "REG-3001"


class DuplicateFilingError(RegulatoryEngineError):
    """Raised when attempting to create a duplicate filing without an explicit version correction."""

    default_code = "REG-4001"


class ApprovalWorkflowError(RegulatoryEngineError):
    """Raised when a report status transition or correction violates lifecycle rules."""

    default_code = "REG-4002"


class ImmutabilityViolationError(RegulatoryEngineError):
    """Raised when attempting to mutate or delete an immutable snapshot, report, or audit entry."""

    default_code = "REG-4003"


class AutomatedSubmissionProhibitedError(RegulatoryEngineError):
    """Raised whenever automated external regulatory submission is requested or enabled."""

    default_code = "REG-9001"
