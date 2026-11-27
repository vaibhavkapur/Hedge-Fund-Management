"""Abstract storage repository contract separating business logic from DB drivers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Sequence

from fund_reporting.domain.models import (
    AuditEvent,
    BenchmarkPoint,
    CashFlowRecord,
    FundDefinition,
    FxRateRecord,
    NavSnapshot,
    ReportRecord,
    StrategySnapshot,
)


class FundRepository(ABC):
    """Canonical persistence boundary for funds, NAV snapshots, reports, and audit logs."""

    @abstractmethod
    def initialize(self) -> None:
        """Create schema tables if they do not exist."""

    @abstractmethod
    def upsert_fund(self, fund: FundDefinition) -> None:
        """Persist or update a fund definition."""

    @abstractmethod
    def get_fund(self, fund_id: str) -> Optional[FundDefinition]:
        """Retrieve fund definition by ID."""

    @abstractmethod
    def list_funds(self) -> List[FundDefinition]:
        """List all registered funds."""

    @abstractmethod
    def save_nav_snapshot(self, snapshot: NavSnapshot) -> None:
        """Persist a validated versioned NAV snapshot."""

    @abstractmethod
    def save_cash_flow(self, flow: CashFlowRecord) -> None:
        """Persist an external or settlement cash flow record."""

    @abstractmethod
    def get_nav_snapshots(
        self,
        fund_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        as_of_utc: Optional[str] = None,
    ) -> List[NavSnapshot]:
        """Retrieve latest-revision-per-date NAV snapshots, optionally point-in-time as_of_utc."""

    @abstractmethod
    def get_cash_flows(
        self,
        fund_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[CashFlowRecord]:
        """Retrieve cash flows for a fund."""

    @abstractmethod
    def save_fx_rates(self, rates: Sequence[FxRateRecord]) -> None:
        """Persist point-in-time FX rates."""

    @abstractmethod
    def get_fx_rates(
        self,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[FxRateRecord]:
        """Retrieve FX rates over an optional date range."""

    @abstractmethod
    def save_benchmark_points(self, points: Sequence[BenchmarkPoint]) -> None:
        """Persist benchmark daily return points."""

    @abstractmethod
    def get_benchmark_points(
        self,
        benchmark_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[BenchmarkPoint]:
        """Retrieve benchmark series."""

    @abstractmethod
    def save_strategy_snapshots(self, snapshots: Sequence[StrategySnapshot]) -> None:
        """Persist strategy attribution and exposure snapshots."""

    @abstractmethod
    def get_strategy_snapshots(
        self,
        fund_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[StrategySnapshot]:
        """Retrieve strategy snapshots."""

    @abstractmethod
    def save_report(self, report: ReportRecord) -> None:
        """Insert a new report record (immutable once PUBLISHED)."""

    @abstractmethod
    def update_report(self, report: ReportRecord) -> None:
        """Update report workflow metadata (only allowed for non-published mutations or superseding status)."""

    @abstractmethod
    def get_report(self, version_id: str) -> Optional[ReportRecord]:
        """Fetch a specific immutable report version."""

    @abstractmethod
    def list_reports(
        self,
        *,
        fund_id: Optional[str] = None,
        report_series_id: Optional[str] = None,
    ) -> List[ReportRecord]:
        """List reports, optionally filtered by fund or report series."""

    @abstractmethod
    def append_audit_event(self, event: AuditEvent) -> None:
        """Append an immutable audit log entry."""

    @abstractmethod
    def list_audit_events(self, *, limit: int = 200) -> List[AuditEvent]:
        """Return recent audit events ordered newest-first."""
