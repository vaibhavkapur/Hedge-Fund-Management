"""Zero-dependency SQLite implementation of FundRepository for reproducible local prototypes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional, Sequence

from fund_reporting.domain.enums import (
    CashFlowTiming,
    CashFlowType,
    DataStatus,
    EnvironmentMode,
    ReportAudience,
    ReportStatus,
    Role,
)
from fund_reporting.domain.errors import ImmutableReportError
from fund_reporting.domain.models import (
    AuditEvent,
    BenchmarkPoint,
    CashFlowRecord,
    FeeSchedule,
    FundDefinition,
    FxRateRecord,
    NavSnapshot,
    ReportRecord,
    StrategySnapshot,
)

from .repository import FundRepository

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS funds (
    fund_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    environment TEXT NOT NULL,
    data_status TEXT NOT NULL,
    inception_date TEXT NOT NULL,
    benchmark_id TEXT NOT NULL,
    management_fee_bps REAL NOT NULL,
    performance_fee_pct REAL NOT NULL,
    hurdle_rate_annual REAL NOT NULL DEFAULT 0.0,
    high_water_mark_enabled INTEGER NOT NULL DEFAULT 1,
    accrual_basis_days INTEGER NOT NULL DEFAULT 252,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nav_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    as_of_utc TEXT NOT NULL,
    availability_utc TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    currency TEXT NOT NULL,
    environment TEXT NOT NULL,
    data_status TEXT NOT NULL,
    opening_nav REAL NOT NULL,
    gross_pnl REAL NOT NULL,
    management_fee REAL NOT NULL,
    performance_fee REAL NOT NULL,
    other_expenses REAL NOT NULL DEFAULT 0.0,
    subscriptions REAL NOT NULL DEFAULT 0.0,
    redemptions REAL NOT NULL DEFAULT 0.0,
    closing_nav REAL NOT NULL,
    shares_outstanding REAL NOT NULL,
    cash_balance REAL NOT NULL,
    gross_exposure REAL NOT NULL,
    net_exposure REAL NOT NULL,
    ledger_reconciled INTEGER NOT NULL DEFAULT 1,
    ledger_hash TEXT NOT NULL,
    provenance_source TEXT NOT NULL,
    cash_flow_timing TEXT NOT NULL DEFAULT 'START_OF_DAY',
    UNIQUE (fund_id, valuation_date, revision)
);

CREATE TABLE IF NOT EXISTS cash_flows (
    flow_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    as_of_utc TEXT NOT NULL,
    flow_type TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    timing TEXT NOT NULL,
    investor_class TEXT NOT NULL DEFAULT 'CLASS_A',
    environment TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fx_rates (
    rate_id TEXT PRIMARY KEY,
    valuation_date TEXT NOT NULL,
    as_of_utc TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    rate REAL NOT NULL,
    source TEXT NOT NULL,
    UNIQUE (valuation_date, base_currency, quote_currency)
);

CREATE TABLE IF NOT EXISTS benchmark_returns (
    benchmark_id TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    daily_return REAL NOT NULL,
    level REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (benchmark_id, valuation_date)
);

CREATE TABLE IF NOT EXISTS strategy_snapshots (
    strategy_snapshot_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    fund_id TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    currency TEXT NOT NULL,
    environment TEXT NOT NULL,
    weight REAL NOT NULL,
    gross_pnl REAL NOT NULL,
    transaction_costs REAL NOT NULL DEFAULT 0.0,
    long_exposure REAL NOT NULL,
    short_exposure REAL NOT NULL,
    cash_allocation REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS reports (
    version_id TEXT PRIMARY KEY,
    report_series_id TEXT NOT NULL,
    fund_id TEXT NOT NULL,
    revision_number INTEGER NOT NULL,
    supersedes_version_id TEXT,
    revision_reason TEXT,
    audience TEXT NOT NULL,
    reporting_currency TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    as_of_utc TEXT NOT NULL,
    environment TEXT NOT NULL,
    data_status TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    reviewed_by TEXT,
    approved_by TEXT,
    published_at_utc TEXT,
    content_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (report_series_id, revision_number)
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id TEXT PRIMARY KEY,
    timestamp_utc TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    details_json TEXT NOT NULL
);
"""


class SqliteRepository(FundRepository):
    """Thread-safe SQLite repository supporting both file-backed and :memory: databases."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.initialize()

    def close(self) -> None:
        self._conn.close()

    def initialize(self) -> None:
        with self._conn:
            self._conn.executescript(_SQLITE_SCHEMA)

    def upsert_fund(self, fund: FundDefinition) -> None:
        fs = fund.fee_schedule
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO funds (
                    fund_id, name, base_currency, environment, data_status, inception_date,
                    benchmark_id, management_fee_bps, performance_fee_pct, hurdle_rate_annual,
                    high_water_mark_enabled, accrual_basis_days, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fund_id) DO UPDATE SET
                    name=excluded.name,
                    base_currency=excluded.base_currency,
                    environment=excluded.environment,
                    data_status=excluded.data_status,
                    benchmark_id=excluded.benchmark_id,
                    management_fee_bps=excluded.management_fee_bps,
                    performance_fee_pct=excluded.performance_fee_pct,
                    hurdle_rate_annual=excluded.hurdle_rate_annual,
                    high_water_mark_enabled=excluded.high_water_mark_enabled,
                    accrual_basis_days=excluded.accrual_basis_days
                """,
                (
                    fund.fund_id,
                    fund.name,
                    fund.base_currency,
                    fund.environment.value,
                    fund.data_status.value,
                    fund.inception_date,
                    fund.benchmark_id,
                    fs.management_fee_annual_bps,
                    fs.performance_fee_pct,
                    fs.hurdle_rate_annual,
                    1 if fs.high_water_mark_enabled else 0,
                    fs.accrual_basis_days,
                    fund.created_at_utc,
                ),
            )

    def _row_to_fund(self, row: sqlite3.Row) -> FundDefinition:
        return FundDefinition(
            fund_id=row["fund_id"],
            name=row["name"],
            base_currency=row["base_currency"],
            environment=EnvironmentMode(row["environment"]),
            data_status=DataStatus(row["data_status"]),
            inception_date=row["inception_date"],
            benchmark_id=row["benchmark_id"],
            fee_schedule=FeeSchedule(
                management_fee_annual_bps=row["management_fee_bps"],
                performance_fee_pct=row["performance_fee_pct"],
                hurdle_rate_annual=row["hurdle_rate_annual"],
                high_water_mark_enabled=bool(row["high_water_mark_enabled"]),
                accrual_basis_days=row["accrual_basis_days"],
            ),
            created_at_utc=row["created_at_utc"],
        )

    def get_fund(self, fund_id: str) -> Optional[FundDefinition]:
        row = self._conn.execute("SELECT * FROM funds WHERE fund_id = ?", (fund_id,)).fetchone()
        return self._row_to_fund(row) if row else None

    def list_funds(self) -> List[FundDefinition]:
        rows = self._conn.execute("SELECT * FROM funds ORDER BY fund_id").fetchall()
        return [self._row_to_fund(r) for r in rows]

    def save_nav_snapshot(self, snapshot: NavSnapshot) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO nav_snapshots (
                    snapshot_id, fund_id, valuation_date, as_of_utc, availability_utc,
                    revision, currency, environment, data_status, opening_nav, gross_pnl,
                    management_fee, performance_fee, other_expenses, subscriptions,
                    redemptions, closing_nav, shares_outstanding, cash_balance,
                    gross_exposure, net_exposure, ledger_reconciled, ledger_hash,
                    provenance_source, cash_flow_timing
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.fund_id,
                    snapshot.valuation_date,
                    snapshot.as_of_utc,
                    snapshot.availability_utc,
                    snapshot.revision,
                    snapshot.currency,
                    snapshot.environment.value,
                    snapshot.data_status.value,
                    snapshot.opening_nav,
                    snapshot.gross_pnl,
                    snapshot.management_fee,
                    snapshot.performance_fee,
                    snapshot.other_expenses,
                    snapshot.subscriptions,
                    snapshot.redemptions,
                    snapshot.closing_nav,
                    snapshot.shares_outstanding,
                    snapshot.cash_balance,
                    snapshot.gross_exposure,
                    snapshot.net_exposure,
                    1 if snapshot.ledger_reconciled else 0,
                    snapshot.ledger_hash,
                    snapshot.provenance_source,
                    snapshot.cash_flow_timing.value,
                ),
            )

    def _row_to_snapshot(self, row: sqlite3.Row) -> NavSnapshot:
        return NavSnapshot(
            snapshot_id=row["snapshot_id"],
            fund_id=row["fund_id"],
            valuation_date=row["valuation_date"],
            as_of_utc=row["as_of_utc"],
            availability_utc=row["availability_utc"],
            revision=row["revision"],
            currency=row["currency"],
            environment=EnvironmentMode(row["environment"]),
            data_status=DataStatus(row["data_status"]),
            opening_nav=row["opening_nav"],
            gross_pnl=row["gross_pnl"],
            management_fee=row["management_fee"],
            performance_fee=row["performance_fee"],
            other_expenses=row["other_expenses"],
            subscriptions=row["subscriptions"],
            redemptions=row["redemptions"],
            closing_nav=row["closing_nav"],
            shares_outstanding=row["shares_outstanding"],
            cash_balance=row["cash_balance"],
            gross_exposure=row["gross_exposure"],
            net_exposure=row["net_exposure"],
            ledger_reconciled=bool(row["ledger_reconciled"]),
            ledger_hash=row["ledger_hash"],
            provenance_source=row["provenance_source"],
            cash_flow_timing=CashFlowTiming(row["cash_flow_timing"]),
        )

    def get_nav_snapshots(
        self,
        fund_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        as_of_utc: Optional[str] = None,
    ) -> List[NavSnapshot]:
        clauses = ["fund_id = ?"]
        params: List[object] = [fund_id]
        if start_date:
            clauses.append("valuation_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("valuation_date <= ?")
            params.append(end_date)
        if as_of_utc:
            clauses.append("availability_utc <= ?")
            params.append(as_of_utc)

        query = f"SELECT * FROM nav_snapshots WHERE {' AND '.join(clauses)} ORDER BY valuation_date ASC, revision ASC"
        rows = self._conn.execute(query, params).fetchall()
        latest_by_date = {}
        for r in rows:
            snap = self._row_to_snapshot(r)
            latest_by_date[snap.valuation_date] = snap
        return [latest_by_date[d] for d in sorted(latest_by_date.keys())]

    def save_cash_flow(self, flow: CashFlowRecord) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO cash_flows (
                    flow_id, fund_id, snapshot_id, valuation_date, as_of_utc,
                    flow_type, amount, currency, timing, investor_class, environment
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    flow.flow_id,
                    flow.fund_id,
                    flow.snapshot_id,
                    flow.valuation_date,
                    flow.as_of_utc,
                    flow.flow_type.value,
                    flow.amount,
                    flow.currency,
                    flow.timing.value,
                    flow.investor_class,
                    flow.environment.value,
                ),
            )

    def get_cash_flows(
        self,
        fund_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[CashFlowRecord]:
        clauses = ["fund_id = ?"]
        params: List[object] = [fund_id]
        if start_date:
            clauses.append("valuation_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("valuation_date <= ?")
            params.append(end_date)
        rows = self._conn.execute(
            f"SELECT * FROM cash_flows WHERE {' AND '.join(clauses)} ORDER BY valuation_date ASC",
            params,
        ).fetchall()
        return [
            CashFlowRecord(
                flow_id=r["flow_id"],
                fund_id=r["fund_id"],
                snapshot_id=r["snapshot_id"],
                valuation_date=r["valuation_date"],
                as_of_utc=r["as_of_utc"],
                flow_type=CashFlowType(r["flow_type"]),
                amount=r["amount"],
                currency=r["currency"],
                timing=CashFlowTiming(r["timing"]),
                investor_class=r["investor_class"],
                environment=EnvironmentMode(r["environment"]),
            )
            for r in rows
        ]

    def save_fx_rates(self, rates: Sequence[FxRateRecord]) -> None:
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO fx_rates (
                    rate_id, valuation_date, as_of_utc, base_currency, quote_currency, rate, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (r.rate_id, r.valuation_date, r.as_of_utc, r.base_currency, r.quote_currency, r.rate, r.source)
                    for r in rates
                ],
            )

    def get_fx_rates(
        self,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[FxRateRecord]:
        clauses: List[str] = []
        params: List[object] = []
        if start_date:
            clauses.append("valuation_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("valuation_date <= ?")
            params.append(end_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM fx_rates {where} ORDER BY valuation_date ASC", params
        ).fetchall()
        return [
            FxRateRecord(
                rate_id=r["rate_id"],
                valuation_date=r["valuation_date"],
                as_of_utc=r["as_of_utc"],
                base_currency=r["base_currency"],
                quote_currency=r["quote_currency"],
                rate=r["rate"],
                source=r["source"],
            )
            for r in rows
        ]

    def save_benchmark_points(self, points: Sequence[BenchmarkPoint]) -> None:
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO benchmark_returns (
                    benchmark_id, valuation_date, currency, daily_return, level, source
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (p.benchmark_id, p.valuation_date, p.currency, p.daily_return, p.level, p.source)
                    for p in points
                ],
            )

    def get_benchmark_points(
        self,
        benchmark_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[BenchmarkPoint]:
        clauses = ["benchmark_id = ?"]
        params: List[object] = [benchmark_id]
        if start_date:
            clauses.append("valuation_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("valuation_date <= ?")
            params.append(end_date)
        rows = self._conn.execute(
            f"SELECT * FROM benchmark_returns WHERE {' AND '.join(clauses)} ORDER BY valuation_date ASC",
            params,
        ).fetchall()
        return [
            BenchmarkPoint(
                benchmark_id=r["benchmark_id"],
                valuation_date=r["valuation_date"],
                currency=r["currency"],
                daily_return=r["daily_return"],
                level=r["level"],
                source=r["source"],
            )
            for r in rows
        ]

    def save_strategy_snapshots(self, snapshots: Sequence[StrategySnapshot]) -> None:
        with self._conn:
            self._conn.executemany(
                """
                INSERT OR REPLACE INTO strategy_snapshots (
                    strategy_snapshot_id, snapshot_id, fund_id, valuation_date,
                    strategy_id, strategy_name, asset_class, currency, environment,
                    weight, gross_pnl, transaction_costs, long_exposure, short_exposure, cash_allocation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        s.strategy_snapshot_id,
                        s.snapshot_id,
                        s.fund_id,
                        s.valuation_date,
                        s.strategy_id,
                        s.strategy_name,
                        s.asset_class,
                        s.currency,
                        s.environment.value,
                        s.weight,
                        s.gross_pnl,
                        s.transaction_costs,
                        s.long_exposure,
                        s.short_exposure,
                        s.cash_allocation,
                    )
                    for s in snapshots
                ],
            )

    def get_strategy_snapshots(
        self,
        fund_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[StrategySnapshot]:
        clauses = ["fund_id = ?"]
        params: List[object] = [fund_id]
        if start_date:
            clauses.append("valuation_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("valuation_date <= ?")
            params.append(end_date)
        rows = self._conn.execute(
            f"SELECT * FROM strategy_snapshots WHERE {' AND '.join(clauses)} ORDER BY valuation_date ASC, strategy_id ASC",
            params,
        ).fetchall()
        return [
            StrategySnapshot(
                strategy_snapshot_id=r["strategy_snapshot_id"],
                snapshot_id=r["snapshot_id"],
                fund_id=r["fund_id"],
                valuation_date=r["valuation_date"],
                strategy_id=r["strategy_id"],
                strategy_name=r["strategy_name"],
                asset_class=r["asset_class"],
                currency=r["currency"],
                environment=EnvironmentMode(r["environment"]),
                weight=r["weight"],
                gross_pnl=r["gross_pnl"],
                transaction_costs=r["transaction_costs"],
                long_exposure=r["long_exposure"],
                short_exposure=r["short_exposure"],
                cash_allocation=r["cash_allocation"],
            )
            for r in rows
        ]

    def _row_to_report(self, r: sqlite3.Row) -> ReportRecord:
        return ReportRecord(
            version_id=r["version_id"],
            report_series_id=r["report_series_id"],
            fund_id=r["fund_id"],
            revision_number=r["revision_number"],
            supersedes_version_id=r["supersedes_version_id"],
            revision_reason=r["revision_reason"],
            audience=ReportAudience(r["audience"]),
            reporting_currency=r["reporting_currency"],
            period_start=r["period_start"],
            period_end=r["period_end"],
            as_of_utc=r["as_of_utc"],
            environment=EnvironmentMode(r["environment"]),
            data_status=DataStatus(r["data_status"]),
            methodology_version=r["methodology_version"],
            status=ReportStatus(r["status"]),
            created_by=r["created_by"],
            reviewed_by=r["reviewed_by"],
            approved_by=r["approved_by"],
            published_at_utc=r["published_at_utc"],
            content_sha256=r["content_sha256"],
            payload=json.loads(r["payload_json"]),
        )

    def save_report(self, report: ReportRecord) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO reports (
                    version_id, report_series_id, fund_id, revision_number,
                    supersedes_version_id, revision_reason, audience, reporting_currency,
                    period_start, period_end, as_of_utc, environment, data_status,
                    methodology_version, status, created_by, reviewed_by, approved_by,
                    published_at_utc, content_sha256, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.version_id,
                    report.report_series_id,
                    report.fund_id,
                    report.revision_number,
                    report.supersedes_version_id,
                    report.revision_reason,
                    report.audience.value,
                    report.reporting_currency,
                    report.period_start,
                    report.period_end,
                    report.as_of_utc,
                    report.environment.value,
                    report.data_status.value,
                    report.methodology_version,
                    report.status.value,
                    report.created_by,
                    report.reviewed_by,
                    report.approved_by,
                    report.published_at_utc,
                    report.content_sha256,
                    json.dumps(report.payload, sort_keys=True),
                ),
            )

    def update_report(self, report: ReportRecord) -> None:
        existing = self.get_report(report.version_id)
        if existing is not None and existing.status == ReportStatus.PUBLISHED:
            # Published reports are strictly immutable; only status -> SUPERSEDED is permitted without payload mutation
            if report.status != ReportStatus.SUPERSEDED or report.content_sha256 != existing.content_sha256:
                raise ImmutableReportError(
                    f"Published report {report.version_id} is immutable; create a new revision instead.",
                    details={"version_id": report.version_id, "existing_status": existing.status.value},
                )
        with self._conn:
            self._conn.execute(
                """
                UPDATE reports SET
                    status = ?,
                    reviewed_by = ?,
                    approved_by = ?,
                    published_at_utc = ?,
                    content_sha256 = ?,
                    payload_json = ?
                WHERE version_id = ?
                """,
                (
                    report.status.value,
                    report.reviewed_by,
                    report.approved_by,
                    report.published_at_utc,
                    report.content_sha256,
                    json.dumps(report.payload, sort_keys=True),
                    report.version_id,
                ),
            )

    def get_report(self, version_id: str) -> Optional[ReportRecord]:
        row = self._conn.execute("SELECT * FROM reports WHERE version_id = ?", (version_id,)).fetchone()
        return self._row_to_report(row) if row else None

    def list_reports(
        self,
        *,
        fund_id: Optional[str] = None,
        report_series_id: Optional[str] = None,
    ) -> List[ReportRecord]:
        clauses: List[str] = []
        params: List[object] = []
        if fund_id:
            clauses.append("fund_id = ?")
            params.append(fund_id)
        if report_series_id:
            clauses.append("report_series_id = ?")
            params.append(report_series_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM reports {where} ORDER BY as_of_utc DESC, revision_number DESC",
            params,
        ).fetchall()
        return [self._row_to_report(r) for r in rows]

    def append_audit_event(self, event: AuditEvent) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO audit_log (
                    audit_id, timestamp_utc, actor_id, actor_role, action,
                    resource_type, resource_id, outcome, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.audit_id,
                    event.timestamp_utc,
                    event.actor_id,
                    event.actor_role.value,
                    event.action,
                    event.resource_type,
                    event.resource_id,
                    event.outcome,
                    json.dumps(event.details, sort_keys=True),
                ),
            )

    def list_audit_events(self, *, limit: int = 200) -> List[AuditEvent]:
        rows = self._conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp_utc DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            AuditEvent(
                audit_id=r["audit_id"],
                timestamp_utc=r["timestamp_utc"],
                actor_id=r["actor_id"],
                actor_role=Role(r["actor_role"]),
                action=r["action"],
                resource_type=r["resource_type"],
                resource_id=r["resource_id"],
                outcome=r["outcome"],
                details=json.loads(r["details_json"]),
            )
            for r in rows
        ]
