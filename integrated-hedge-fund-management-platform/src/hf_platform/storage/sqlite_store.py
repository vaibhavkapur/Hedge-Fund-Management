"""SQLite persistent storage implementation matching docs/schema.sql."""

from __future__ import annotations

import sqlite3
import threading
from datetime import date
from pathlib import Path

from hf_platform.domain import (
    Account,
    AuditLogEntry,
    Counterparty,
    DailyCloseRun,
    DomainEvent,
    Fund,
    Instrument,
    JournalEntry,
    MarketMark,
    OperationalException,
    PortfolioSnapshot,
    ReconciliationResult,
    ReportArtifact,
    Strategy,
    TradeFill,
    UserPrincipal,
)
from hf_platform.errors import AuditIntegrityError, SnapshotImmutableError
from hf_platform.storage.repository import StorageRepository

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS funds (
    fund_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counterparties (
    counterparty_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    counterparty_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategies (
    strategy_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    currency TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    business_date TEXT NOT NULL,
    is_late INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fills_fund_date ON fills (fund_id, business_date);

CREATE TABLE IF NOT EXISTS manual_marks (
    business_date TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (business_date, instrument_id)
);

CREATE TABLE IF NOT EXISTS journal_entries (
    entry_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    business_date TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_journal_fund_date ON journal_entries (fund_id, business_date);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    business_date TEXT NOT NULL,
    version INTEGER NOT NULL,
    is_frozen INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_fund_date ON portfolio_snapshots (fund_id, business_date);

CREATE TABLE IF NOT EXISTS reconciliations (
    reconciliation_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    business_date TEXT NOT NULL,
    is_clean INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS close_runs (
    close_run_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    business_date TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exceptions (
    exception_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    business_date TEXT NOT NULL,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    business_date TEXT NOT NULL,
    report_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    sequence_number INTEGER PRIMARY KEY,
    audit_id TEXT UNIQUE NOT NULL,
    timestamp_utc TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS domain_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    occurred_at_utc TEXT NOT NULL,
    fund_id TEXT,
    payload_json TEXT NOT NULL
);
"""


class SQLiteStore(StorageRepository):
    """Thread-safe SQLite repository with frozen-snapshot immutability and append-only audit log."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.executescript(SCHEMA_SQL)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # Master Data
    def save_fund(self, fund: Fund) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO funds (fund_id, name, status, version, payload_json) VALUES (?, ?, ?, ?, ?)",
                (fund.fund_id, fund.name, fund.status.value, fund.version, fund.model_dump_json()),
            )
            self._conn.commit()

    def get_fund(self, fund_id: str) -> Fund | None:
        with self._lock:
            row = self._conn.execute("SELECT payload_json FROM funds WHERE fund_id = ?", (fund_id,)).fetchone()
            return Fund.model_validate_json(row["payload_json"]) if row else None

    def list_funds(self) -> list[Fund]:
        with self._lock:
            rows = self._conn.execute("SELECT payload_json FROM funds ORDER BY fund_id").fetchall()
            return [Fund.model_validate_json(r["payload_json"]) for r in rows]

    def save_counterparty(self, cp: Counterparty) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO counterparties (counterparty_id, name, payload_json) VALUES (?, ?, ?)",
                (cp.counterparty_id, cp.name, cp.model_dump_json()),
            )
            self._conn.commit()

    def list_counterparties(self) -> list[Counterparty]:
        with self._lock:
            rows = self._conn.execute("SELECT payload_json FROM counterparties ORDER BY counterparty_id").fetchall()
            return [Counterparty.model_validate_json(r["payload_json"]) for r in rows]

    def save_account(self, account: Account) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO accounts (account_id, fund_id, counterparty_id, status, payload_json) VALUES (?, ?, ?, ?, ?)",
                (account.account_id, account.fund_id, account.counterparty_id, account.status.value, account.model_dump_json()),
            )
            self._conn.commit()

    def list_accounts(self, fund_id: str | None = None) -> list[Account]:
        with self._lock:
            if fund_id:
                rows = self._conn.execute(
                    "SELECT payload_json FROM accounts WHERE fund_id = ? ORDER BY account_id", (fund_id,)
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT payload_json FROM accounts ORDER BY account_id").fetchall()
            return [Account.model_validate_json(r["payload_json"]) for r in rows]

    def save_strategy(self, strategy: Strategy) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO strategies (strategy_id, fund_id, status, payload_json) VALUES (?, ?, ?, ?)",
                (strategy.strategy_id, strategy.fund_id, strategy.status.value, strategy.model_dump_json()),
            )
            self._conn.commit()

    def list_strategies(self, fund_id: str | None = None) -> list[Strategy]:
        with self._lock:
            if fund_id:
                rows = self._conn.execute(
                    "SELECT payload_json FROM strategies WHERE fund_id = ? ORDER BY strategy_id", (fund_id,)
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT payload_json FROM strategies ORDER BY strategy_id").fetchall()
            return [Strategy.model_validate_json(r["payload_json"]) for r in rows]

    def save_instrument(self, instrument: Instrument) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO instruments (instrument_id, symbol, asset_class, currency, payload_json) VALUES (?, ?, ?, ?, ?)",
                (instrument.instrument_id, instrument.symbol, instrument.asset_class.value, instrument.currency.value, instrument.model_dump_json()),
            )
            self._conn.commit()

    def list_instruments(self) -> list[Instrument]:
        with self._lock:
            rows = self._conn.execute("SELECT payload_json FROM instruments ORDER BY instrument_id").fetchall()
            return [Instrument.model_validate_json(r["payload_json"]) for r in rows]

    def save_user(self, user: UserPrincipal) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO users (user_id, name, role, payload_json) VALUES (?, ?, ?, ?)",
                (user.user_id, user.name, user.role.value, user.model_dump_json()),
            )
            self._conn.commit()

    def get_user(self, user_id: str) -> UserPrincipal | None:
        with self._lock:
            row = self._conn.execute("SELECT payload_json FROM users WHERE user_id = ?", (user_id,)).fetchone()
            return UserPrincipal.model_validate_json(row["payload_json"]) if row else None

    def list_users(self) -> list[UserPrincipal]:
        with self._lock:
            rows = self._conn.execute("SELECT payload_json FROM users ORDER BY user_id").fetchall()
            return [UserPrincipal.model_validate_json(r["payload_json"]) for r in rows]

    # Fills & Manual Marks
    def save_fill(self, fill: TradeFill) -> bool:
        bdate = (fill.business_date or fill.executed_at_utc.date()).isoformat()
        with self._lock:
            existing = self._conn.execute("SELECT 1 FROM fills WHERE fill_id = ?", (fill.fill_id,)).fetchone()
            if existing:
                return False
            self._conn.execute(
                "INSERT INTO fills (fill_id, fund_id, account_id, business_date, is_late, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                (fill.fill_id, fill.fund_id, fill.account_id, bdate, 1 if fill.is_late else 0, fill.model_dump_json()),
            )
            self._conn.commit()
            return True

    def list_fills(
        self,
        fund_id: str,
        up_to_date: date | None = None,
        business_date: date | None = None,
    ) -> list[TradeFill]:
        with self._lock:
            if business_date is not None:
                rows = self._conn.execute(
                    "SELECT payload_json FROM fills WHERE fund_id = ? AND business_date = ? ORDER BY fill_id",
                    (fund_id, business_date.isoformat()),
                ).fetchall()
            elif up_to_date is not None:
                rows = self._conn.execute(
                    "SELECT payload_json FROM fills WHERE fund_id = ? AND business_date <= ? ORDER BY business_date, fill_id",
                    (fund_id, up_to_date.isoformat()),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT payload_json FROM fills WHERE fund_id = ? ORDER BY business_date, fill_id",
                    (fund_id,),
                ).fetchall()
            return [TradeFill.model_validate_json(r["payload_json"]) for r in rows]

    def save_manual_mark(self, business_date: date, mark: MarketMark) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO manual_marks (business_date, instrument_id, payload_json) VALUES (?, ?, ?)",
                (business_date.isoformat(), mark.instrument_id, mark.model_dump_json()),
            )
            self._conn.commit()

    def list_manual_marks(self, business_date: date) -> list[MarketMark]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM manual_marks WHERE business_date = ? ORDER BY instrument_id",
                (business_date.isoformat(),),
            ).fetchall()
            return [MarketMark.model_validate_json(r["payload_json"]) for r in rows]

    # Ledger
    def save_journal_entry(self, entry: JournalEntry) -> bool:
        with self._lock:
            existing = self._conn.execute(
                "SELECT 1 FROM journal_entries WHERE entry_id = ?", (entry.entry_id,)
            ).fetchone()
            if existing:
                return False
            self._conn.execute(
                "INSERT INTO journal_entries (entry_id, fund_id, business_date, source_ref, version, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                (entry.entry_id, entry.fund_id, entry.business_date.isoformat(), entry.source_ref, entry.version, entry.model_dump_json()),
            )
            self._conn.commit()
            return True

    def list_journal_entries(self, fund_id: str, up_to_date: date | None = None) -> list[JournalEntry]:
        with self._lock:
            if up_to_date is not None:
                rows = self._conn.execute(
                    "SELECT payload_json FROM journal_entries WHERE fund_id = ? AND business_date <= ? ORDER BY business_date, entry_id",
                    (fund_id, up_to_date.isoformat()),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT payload_json FROM journal_entries WHERE fund_id = ? ORDER BY business_date, entry_id",
                    (fund_id,),
                ).fetchall()
            return [JournalEntry.model_validate_json(r["payload_json"]) for r in rows]

    # Portfolio Snapshots
    def save_snapshot(self, snapshot: PortfolioSnapshot) -> None:
        with self._lock:
            existing = self._conn.execute(
                "SELECT is_frozen FROM portfolio_snapshots WHERE snapshot_id = ?", (snapshot.snapshot_id,)
            ).fetchone()
            if existing and existing["is_frozen"] == 1:
                raise SnapshotImmutableError(
                    f"Cannot overwrite frozen snapshot {snapshot.snapshot_id}",
                    details={"snapshot_id": snapshot.snapshot_id},
                )
            self._conn.execute(
                "INSERT OR REPLACE INTO portfolio_snapshots (snapshot_id, fund_id, business_date, version, is_frozen, content_hash, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot.snapshot_id,
                    snapshot.fund_id,
                    snapshot.business_date.isoformat(),
                    snapshot.version,
                    1 if snapshot.is_frozen else 0,
                    snapshot.content_hash,
                    snapshot.model_dump_json(),
                ),
            )
            self._conn.commit()

    def get_snapshot(self, snapshot_id: str) -> PortfolioSnapshot | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM portfolio_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
            return PortfolioSnapshot.model_validate_json(row["payload_json"]) if row else None

    def list_snapshots(self, fund_id: str, business_date: date | None = None) -> list[PortfolioSnapshot]:
        with self._lock:
            if business_date:
                rows = self._conn.execute(
                    "SELECT payload_json FROM portfolio_snapshots WHERE fund_id = ? AND business_date = ? ORDER BY version",
                    (fund_id, business_date.isoformat()),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT payload_json FROM portfolio_snapshots WHERE fund_id = ? ORDER BY business_date, version",
                    (fund_id,),
                ).fetchall()
            return [PortfolioSnapshot.model_validate_json(r["payload_json"]) for r in rows]

    # Reconciliation
    def save_reconciliation(self, recon: ReconciliationResult) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO reconciliations (reconciliation_id, fund_id, business_date, is_clean, payload_json) VALUES (?, ?, ?, ?, ?)",
                (recon.reconciliation_id, recon.fund_id, recon.business_date.isoformat(), 1 if recon.is_clean else 0, recon.model_dump_json()),
            )
            self._conn.commit()

    def get_reconciliation(self, reconciliation_id: str) -> ReconciliationResult | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM reconciliations WHERE reconciliation_id = ?", (reconciliation_id,)
            ).fetchone()
            return ReconciliationResult.model_validate_json(row["payload_json"]) if row else None

    def list_reconciliations(self, fund_id: str, business_date: date | None = None) -> list[ReconciliationResult]:
        with self._lock:
            if business_date:
                rows = self._conn.execute(
                    "SELECT payload_json FROM reconciliations WHERE fund_id = ? AND business_date = ? ORDER BY reconciliation_id",
                    (fund_id, business_date.isoformat()),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT payload_json FROM reconciliations WHERE fund_id = ? ORDER BY business_date, reconciliation_id",
                    (fund_id,),
                ).fetchall()
            return [ReconciliationResult.model_validate_json(r["payload_json"]) for r in rows]

    # Daily Close
    def save_close_run(self, close_run: DailyCloseRun) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO close_runs (close_run_id, fund_id, business_date, attempt, status, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                (close_run.close_run_id, close_run.fund_id, close_run.business_date.isoformat(), close_run.attempt, close_run.status.value, close_run.model_dump_json()),
            )
            self._conn.commit()

    def get_close_run(self, close_run_id: str) -> DailyCloseRun | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM close_runs WHERE close_run_id = ?", (close_run_id,)
            ).fetchone()
            return DailyCloseRun.model_validate_json(row["payload_json"]) if row else None

    def list_close_runs(self, fund_id: str, business_date: date | None = None) -> list[DailyCloseRun]:
        with self._lock:
            if business_date:
                rows = self._conn.execute(
                    "SELECT payload_json FROM close_runs WHERE fund_id = ? AND business_date = ? ORDER BY attempt",
                    (fund_id, business_date.isoformat()),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT payload_json FROM close_runs WHERE fund_id = ? ORDER BY business_date, attempt",
                    (fund_id,),
                ).fetchall()
            return [DailyCloseRun.model_validate_json(r["payload_json"]) for r in rows]

    # Exceptions
    def save_exception(self, exc: OperationalException) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO exceptions (exception_id, fund_id, business_date, status, severity, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
                (exc.exception_id, exc.fund_id, exc.business_date.isoformat(), exc.status.value, exc.severity.value, exc.model_dump_json()),
            )
            self._conn.commit()

    def get_exception(self, exception_id: str) -> OperationalException | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM exceptions WHERE exception_id = ?", (exception_id,)
            ).fetchone()
            return OperationalException.model_validate_json(row["payload_json"]) if row else None

    def list_exceptions(self, fund_id: str | None = None, business_date: date | None = None) -> list[OperationalException]:
        with self._lock:
            clauses: list[str] = []
            params: list[str] = []
            if fund_id:
                clauses.append("fund_id = ?")
                params.append(fund_id)
            if business_date:
                clauses.append("business_date = ?")
                params.append(business_date.isoformat())
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = self._conn.execute(
                f"SELECT payload_json FROM exceptions {where} ORDER BY business_date, exception_id",
                tuple(params),
            ).fetchall()
            return [OperationalException.model_validate_json(r["payload_json"]) for r in rows]

    # Reports
    def save_report(self, report: ReportArtifact) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO reports (report_id, fund_id, business_date, report_type, version, status, snapshot_id, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    report.report_id,
                    report.fund_id,
                    report.business_date.isoformat(),
                    report.report_type.value,
                    report.version,
                    report.status.value,
                    report.snapshot_id,
                    report.model_dump_json(),
                ),
            )
            self._conn.commit()

    def get_report(self, report_id: str) -> ReportArtifact | None:
        with self._lock:
            row = self._conn.execute("SELECT payload_json FROM reports WHERE report_id = ?", (report_id,)).fetchone()
            return ReportArtifact.model_validate_json(row["payload_json"]) if row else None

    def list_reports(self, fund_id: str, business_date: date | None = None) -> list[ReportArtifact]:
        with self._lock:
            if business_date:
                rows = self._conn.execute(
                    "SELECT payload_json FROM reports WHERE fund_id = ? AND business_date = ? ORDER BY report_type, version",
                    (fund_id, business_date.isoformat()),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT payload_json FROM reports WHERE fund_id = ? ORDER BY business_date, report_type, version",
                    (fund_id,),
                ).fetchall()
            return [ReportArtifact.model_validate_json(r["payload_json"]) for r in rows]

    # Append-only Audit & Events
    def append_audit_entry(self, entry: AuditLogEntry) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit_log (sequence_number, audit_id, timestamp_utc, actor_id, action, entity_type, entity_id, outcome, prev_hash, entry_hash, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.sequence_number,
                    entry.audit_id,
                    entry.timestamp_utc.isoformat(),
                    entry.actor_id,
                    entry.action,
                    entry.entity_type,
                    entry.entity_id,
                    entry.outcome,
                    entry.prev_hash,
                    entry.entry_hash,
                    entry.model_dump_json(),
                ),
            )
            self._conn.commit()

    def get_last_audit_entry(self) -> AuditLogEntry | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM audit_log ORDER BY sequence_number DESC LIMIT 1"
            ).fetchone()
            return AuditLogEntry.model_validate_json(row["payload_json"]) if row else None

    def list_audit_entries(self, limit: int | None = None) -> list[AuditLogEntry]:
        with self._lock:
            if limit is not None:
                rows = self._conn.execute(
                    "SELECT sequence_number, action, actor_id, outcome, prev_hash, entry_hash, payload_json "
                    "FROM audit_log ORDER BY sequence_number DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                rows = list(reversed(rows))
            else:
                rows = self._conn.execute(
                    "SELECT sequence_number, action, actor_id, outcome, prev_hash, entry_hash, payload_json "
                    "FROM audit_log ORDER BY sequence_number ASC"
                ).fetchall()
            entries: list[AuditLogEntry] = []
            for r in rows:
                entry = AuditLogEntry.model_validate_json(r["payload_json"])
                if (
                    entry.sequence_number != r["sequence_number"]
                    or entry.action != r["action"]
                    or entry.actor_id != r["actor_id"]
                    or entry.outcome != r["outcome"]
                    or entry.prev_hash != r["prev_hash"]
                    or entry.entry_hash != r["entry_hash"]
                ):
                    raise AuditIntegrityError(
                        f"Audit column/payload divergence detected at sequence {r['sequence_number']}",
                        details={"sequence_number": r["sequence_number"]},
                    )
                entries.append(entry)
            return entries

    def append_event(self, event: DomainEvent) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO domain_events (event_id, event_type, occurred_at_utc, fund_id, payload_json) VALUES (?, ?, ?, ?, ?)",
                (event.event_id, event.event_type.value, event.occurred_at_utc.isoformat(), event.fund_id, event.model_dump_json()),
            )
            self._conn.commit()

    def list_events(self, limit: int | None = None) -> list[DomainEvent]:
        with self._lock:
            if limit is not None:
                rows = self._conn.execute(
                    "SELECT payload_json FROM domain_events ORDER BY rowid DESC LIMIT ?", (limit,)
                ).fetchall()
                rows = list(reversed(rows))
            else:
                rows = self._conn.execute("SELECT payload_json FROM domain_events ORDER BY rowid ASC").fetchall()
            return [DomainEvent.model_validate_json(r["payload_json"]) for r in rows]
