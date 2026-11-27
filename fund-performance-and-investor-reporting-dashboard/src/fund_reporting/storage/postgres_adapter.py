"""PostgreSQL adapter stub isolating optional psycopg dependency from core domain logic."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from fund_reporting.domain.errors import DomainError
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

from .repository import FundRepository


class PostgresRepository(FundRepository):
    """PostgreSQL implementation contract backed by config/schema_postgres.sql.

    Isolated behind an optional import so the local prototype and test suite run cleanly
    without a PostgreSQL server or psycopg installed.
    """

    def __init__(self, dsn: str, schema_path: Optional[Path] = None) -> None:
        self.dsn = dsn
        self.schema_path = schema_path or (
            Path(__file__).resolve().parents[3] / "config" / "schema_postgres.sql"
        )
        try:
            import psycopg  # type: ignore
        except ImportError as exc:
            raise DomainError(
                "PostgreSQL driver 'psycopg' is not installed. Install optional extra `[postgres]` "
                "or use SqliteRepository for local execution.",
                code="OPTIONAL_DEPENDENCY_MISSING",
                http_status=503,
            ) from exc
        self._psycopg = psycopg

    def get_schema_sql(self) -> str:
        return self.schema_path.read_text(encoding="utf-8")

    def _not_connected(self) -> None:
        raise DomainError(
            "PostgresRepository requires an approved PostgreSQL environment and active connection pool.",
            code="POSTGRES_NOT_CONFIGURED",
            http_status=503,
        )

    def initialize(self) -> None:
        with self._psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(self.get_schema_sql())
            conn.commit()

    def upsert_fund(self, fund: FundDefinition) -> None:
        self._not_connected()

    def get_fund(self, fund_id: str) -> Optional[FundDefinition]:
        self._not_connected()
        return None

    def list_funds(self) -> List[FundDefinition]:
        self._not_connected()
        return []

    def save_nav_snapshot(self, snapshot: NavSnapshot) -> None:
        self._not_connected()

    def save_cash_flow(self, flow: CashFlowRecord) -> None:
        self._not_connected()

    def get_nav_snapshots(
        self,
        fund_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        as_of_utc: Optional[str] = None,
    ) -> List[NavSnapshot]:
        self._not_connected()
        return []

    def get_cash_flows(
        self,
        fund_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[CashFlowRecord]:
        self._not_connected()
        return []

    def save_fx_rates(self, rates: Sequence[FxRateRecord]) -> None:
        self._not_connected()

    def get_fx_rates(
        self,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[FxRateRecord]:
        self._not_connected()
        return []

    def save_benchmark_points(self, points: Sequence[BenchmarkPoint]) -> None:
        self._not_connected()

    def get_benchmark_points(
        self,
        benchmark_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[BenchmarkPoint]:
        self._not_connected()
        return []

    def save_strategy_snapshots(self, snapshots: Sequence[StrategySnapshot]) -> None:
        self._not_connected()

    def get_strategy_snapshots(
        self,
        fund_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[StrategySnapshot]:
        self._not_connected()
        return []

    def save_report(self, report: ReportRecord) -> None:
        self._not_connected()

    def update_report(self, report: ReportRecord) -> None:
        self._not_connected()

    def get_report(self, version_id: str) -> Optional[ReportRecord]:
        self._not_connected()
        return None

    def list_reports(
        self,
        *,
        fund_id: Optional[str] = None,
        report_series_id: Optional[str] = None,
    ) -> List[ReportRecord]:
        self._not_connected()
        return []

    def append_audit_event(self, event: AuditEvent) -> None:
        self._not_connected()

    def list_audit_events(self, *, limit: int = 200) -> List[AuditEvent]:
        self._not_connected()
        return []
