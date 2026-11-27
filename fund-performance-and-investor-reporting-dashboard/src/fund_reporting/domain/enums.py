"""Domain enumerations for fund reporting."""

from __future__ import annotations

from enum import Enum


class EnvironmentMode(str, Enum):
    """Execution/data environment. Mixing environments is strictly prohibited."""

    SIMULATED = "SIMULATED"
    PAPER = "PAPER"
    LIVE = "LIVE"


class DataStatus(str, Enum):
    """Verification and provenance status of underlying fund data."""

    SYNTHETIC = "SYNTHETIC"
    RECONCILED_SANDBOX = "RECONCILED_SANDBOX"
    UNAUDITED = "UNAUDITED"
    AUDITED = "AUDITED"


class MetricBasis(str, Enum):
    """Whether return/risk figures are calculated before or after fees."""

    GROSS = "GROSS"
    NET = "NET"


class CashFlowType(str, Enum):
    """Canonical external or settlement cash flow categories."""

    SUBSCRIPTION = "SUBSCRIPTION"
    REDEMPTION = "REDEMPTION"
    FEE_SETTLEMENT = "FEE_SETTLEMENT"
    DISTRIBUTION = "DISTRIBUTION"


class CashFlowTiming(str, Enum):
    """When an external cash flow enters/exits capital for TWR sub-period math."""

    START_OF_DAY = "START_OF_DAY"
    END_OF_DAY = "END_OF_DAY"


class ReportAudience(str, Enum):
    """Target audience controls visibility and mandatory disclosures."""

    INTERNAL = "INTERNAL"
    INVESTOR = "INVESTOR"


class ReportStatus(str, Enum):
    """Lifecycle state machine for versioned reports."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


class Role(str, Enum):
    """RBAC roles enforcing separation of duties."""

    VIEWER_INVESTOR = "VIEWER_INVESTOR"
    ANALYST = "ANALYST"
    REVIEWER = "REVIEWER"
    APPROVER = "APPROVER"
    ADMIN = "ADMIN"


class MetricFlagCode(str, Enum):
    """Standardized quality and disclosure flags attached to metrics and reports."""

    ZERO_VOLATILITY = "ZERO_VOLATILITY"
    INADEQUATE_HISTORY = "INADEQUATE_HISTORY"
    SIMULATED_DATA = "SIMULATED_DATA"
    PAPER_DATA = "PAPER_DATA"
    UNAUDITED_DATA = "UNAUDITED_DATA"
    CURRENCY_CONVERTED = "CURRENCY_CONVERTED"
    UNRECONCILED_LEDGER = "UNRECONCILED_LEDGER"
    BENCHMARK_GAP = "BENCHMARK_GAP"
