"""Regulatory Reporting Engine — Illustrative configurable regulatory reporting prototype."""

from regulatory_reporting_engine.config import EngineConfig, load_config
from regulatory_reporting_engine.engine import RegulatoryReportingEngine
from regulatory_reporting_engine.errors import (
    ApprovalWorkflowError,
    AutomatedSubmissionProhibitedError,
    ConfigurationError,
    CrossReportReconciliationError,
    DuplicateFilingError,
    ImmutabilityViolationError,
    InvalidIdentifierError,
    MappingError,
    MissingRequiredDataError,
    RegulatoryEngineError,
    SchemaEvolutionError,
    SchemaValidationError,
    UnitMismatchError,
    UnsupportedInputError,
)
from regulatory_reporting_engine.models import (
    ApprovalAuditEntry,
    ExposureRecord,
    FieldLineage,
    FundMetadata,
    GovernedSnapshot,
    NavRecord,
    PositionRecord,
    ReportArtifact,
    ReportingPeriod,
    ReportStatus,
)

__all__ = [
    "ApprovalAuditEntry",
    "ApprovalWorkflowError",
    "AutomatedSubmissionProhibitedError",
    "ConfigurationError",
    "CrossReportReconciliationError",
    "DuplicateFilingError",
    "EngineConfig",
    "ExposureRecord",
    "FieldLineage",
    "FundMetadata",
    "GovernedSnapshot",
    "ImmutabilityViolationError",
    "InvalidIdentifierError",
    "MappingError",
    "MissingRequiredDataError",
    "NavRecord",
    "PositionRecord",
    "RegulatoryEngineError",
    "RegulatoryReportingEngine",
    "ReportArtifact",
    "ReportStatus",
    "ReportingPeriod",
    "SchemaEvolutionError",
    "SchemaValidationError",
    "UnitMismatchError",
    "UnsupportedInputError",
    "load_config",
]

__version__ = "0.1.0"
