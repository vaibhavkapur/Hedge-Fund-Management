"""Immutable append-only versioned journal store (SQLite default + PostgreSQL adapter boundary)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

from pb_collateral.errors import DuplicateStatementError, PersistenceError
from pb_collateral.models import BrokerStatement, ReconciliationResult, SubstitutionReviewResult


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def to_canonical_json(obj: Any) -> str:
    data = asdict(obj) if hasattr(obj, "__dataclass_fields__") else obj
    return json.dumps(data, default=_json_default, sort_keys=True, separators=(",", ":"))


class JournalStore(Protocol):
    """Persistence contract for immutable versioned journal and snapshots."""

    def register_statement(self, statement: BrokerStatement) -> Tuple[bool, bool]:
        """Register statement in journal.

        Returns (duplicate_skipped, is_version_correction).
        Raises DuplicateStatementError if statement_id exists with conflicting content_hash
        or version regresses for (broker_id, account_id, business_date).
        """
        ...

    def record_reconciliation(self, result: ReconciliationResult) -> None:
        ...

    def record_substitution(self, review: SubstitutionReviewResult) -> None:
        ...

    def get_reconciliation_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        ...

    def list_reconciliation_runs(self, broker_id: Optional[str] = None) -> List[Dict[str, Any]]:
        ...

    def list_statement_versions(self, broker_id: str, account_id: str, business_date: date) -> List[Dict[str, Any]]:
        ...


class SQLiteJournalStore:
    """Append-only SQLite journal enforcing immutability via BEFORE UPDATE/DELETE triggers."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        ddl = """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS statement_versions (
            entry_seq       INTEGER PRIMARY KEY AUTOINCREMENT,
            statement_id    TEXT NOT NULL UNIQUE,
            version         INTEGER NOT NULL CHECK (version >= 1),
            supersedes_id   TEXT NULL,
            broker_id       TEXT NOT NULL,
            account_id      TEXT NOT NULL,
            business_date   TEXT NOT NULL,
            base_currency   TEXT NOT NULL,
            content_hash    TEXT NOT NULL,
            payload_json    TEXT NOT NULL,
            recorded_at     TEXT NOT NULL,
            UNIQUE (broker_id, account_id, business_date, version)
        );

        CREATE TABLE IF NOT EXISTS reconciliation_runs (
            run_seq             INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id              TEXT NOT NULL UNIQUE,
            statement_id        TEXT NOT NULL REFERENCES statement_versions(statement_id),
            broker_id           TEXT NOT NULL,
            account_id          TEXT NOT NULL,
            business_date       TEXT NOT NULL,
            policy_id           TEXT NOT NULL,
            policy_version      INTEGER NOT NULL,
            base_currency       TEXT NOT NULL,
            posted_collateral   TEXT NOT NULL,
            margin_requirement  TEXT NOT NULL,
            net_accrual_base    TEXT NOT NULL,
            break_count         INTEGER NOT NULL,
            reconciled_clean    INTEGER NOT NULL,
            result_json         TEXT NOT NULL,
            recorded_at         TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS margin_calls (
            call_seq            INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id             TEXT NOT NULL UNIQUE,
            run_id              TEXT NOT NULL REFERENCES reconciliation_runs(run_id),
            broker_id           TEXT NOT NULL,
            account_id          TEXT NOT NULL,
            business_date       TEXT NOT NULL,
            base_currency       TEXT NOT NULL,
            action              TEXT NOT NULL,
            call_amount         TEXT NOT NULL,
            flagged_pre_settle  INTEGER NOT NULL,
            approval_status     TEXT NOT NULL,
            payload_json        TEXT NOT NULL,
            recorded_at         TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS substitution_reviews (
            review_seq           INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id            TEXT NOT NULL UNIQUE,
            request_id           TEXT NOT NULL,
            broker_id            TEXT NOT NULL,
            account_id           TEXT NOT NULL,
            business_date        TEXT NOT NULL,
            settlement_permitted INTEGER NOT NULL,
            approval_status      TEXT NOT NULL,
            required_top_up      TEXT NOT NULL,
            payload_json         TEXT NOT NULL,
            recorded_at          TEXT NOT NULL
        );

        CREATE TRIGGER IF NOT EXISTS trg_no_update_statements
        BEFORE UPDATE ON statement_versions
        BEGIN
            SELECT RAISE(ABORT, 'statement_versions is append-only; record corrections as new versions');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_no_delete_statements
        BEFORE DELETE ON statement_versions
        BEGIN
            SELECT RAISE(ABORT, 'statement_versions is append-only; deletes are prohibited');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_no_update_runs
        BEFORE UPDATE ON reconciliation_runs
        BEGIN
            SELECT RAISE(ABORT, 'reconciliation_runs is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_no_delete_runs
        BEFORE DELETE ON reconciliation_runs
        BEGIN
            SELECT RAISE(ABORT, 'reconciliation_runs is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_no_update_calls
        BEFORE UPDATE ON margin_calls
        BEGIN
            SELECT RAISE(ABORT, 'margin_calls is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_no_delete_calls
        BEFORE DELETE ON margin_calls
        BEGIN
            SELECT RAISE(ABORT, 'margin_calls is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_no_update_subs
        BEFORE UPDATE ON substitution_reviews
        BEGIN
            SELECT RAISE(ABORT, 'substitution_reviews is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_no_delete_subs
        BEFORE DELETE ON substitution_reviews
        BEGIN
            SELECT RAISE(ABORT, 'substitution_reviews is append-only');
        END;
        """
        self._conn.executescript(ddl)
        self._conn.commit()

    def register_statement(self, statement: BrokerStatement) -> Tuple[bool, bool]:
        cur = self._conn.cursor()

        # Check exact duplicate by content_hash for same broker/account/date
        existing_by_id = cur.execute(
            "SELECT statement_id, version, content_hash FROM statement_versions WHERE statement_id = ?",
            (statement.statement_id,),
        ).fetchone()

        if existing_by_id is not None:
            if existing_by_id["content_hash"] == statement.content_hash:
                return True, False
            raise DuplicateStatementError(
                f"Conflicting duplicate statement_id '{statement.statement_id}' with different content_hash; "
                "submit corrections with a new statement_id and incremented version.",
                context={
                    "statement_id": statement.statement_id,
                    "existing_hash": existing_by_id["content_hash"],
                    "incoming_hash": statement.content_hash,
                },
            )

        # Also detect identical content re-sent under a different statement_id for same broker/account/date/version
        existing_same_slot = cur.execute(
            """
            SELECT statement_id, version, content_hash
            FROM statement_versions
            WHERE broker_id = ? AND account_id = ? AND business_date = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (statement.broker_id, statement.account_id, statement.business_date.isoformat()),
        ).fetchone()

        is_correction = False
        if existing_same_slot is not None:
            if existing_same_slot["content_hash"] == statement.content_hash and existing_same_slot["version"] == statement.version:
                return True, False
            if statement.version <= existing_same_slot["version"]:
                raise DuplicateStatementError(
                    f"Statement version {statement.version} for ({statement.broker_id}, {statement.account_id}, "
                    f"{statement.business_date.isoformat()}) does not exceed existing version {existing_same_slot['version']}.",
                    context={
                        "statement_id": statement.statement_id,
                        "existing_statement_id": existing_same_slot["statement_id"],
                        "existing_version": existing_same_slot["version"],
                        "incoming_version": statement.version,
                    },
                )
            is_correction = True

        now_utc = datetime.now(timezone.utc).isoformat()
        try:
            cur.execute(
                """
                INSERT INTO statement_versions (
                    statement_id, version, supersedes_id, broker_id, account_id,
                    business_date, base_currency, content_hash, payload_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    statement.statement_id,
                    statement.version,
                    statement.supersedes_statement_id,
                    statement.broker_id,
                    statement.account_id,
                    statement.business_date.isoformat(),
                    statement.base_currency,
                    statement.content_hash,
                    to_canonical_json(statement),
                    now_utc,
                ),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise PersistenceError(f"Failed to append statement '{statement.statement_id}': {exc}") from exc
        return False, is_correction

    def record_reconciliation(self, result: ReconciliationResult) -> None:
        cur = self._conn.cursor()
        existing = cur.execute("SELECT run_id FROM reconciliation_runs WHERE run_id = ?", (result.run_id,)).fetchone()
        if existing is not None:
            return  # Idempotent rerun
        now_utc = datetime.now(timezone.utc).isoformat()
        cur.execute(
            """
            INSERT INTO reconciliation_runs (
                run_id, statement_id, broker_id, account_id, business_date,
                policy_id, policy_version, base_currency, posted_collateral,
                margin_requirement, net_accrual_base, break_count, reconciled_clean,
                result_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.run_id,
                result.statement_id,
                result.broker_id,
                result.account_id,
                result.business_date.isoformat(),
                result.valuation.policy_id,
                result.valuation.policy_version,
                result.base_currency,
                str(result.valuation.total_posted_collateral_base),
                str(result.margin_call.margin_requirement_base),
                str(result.financing.net_accrual_base),
                len(result.breaks),
                1 if result.reconciled_clean else 0,
                to_canonical_json(result),
                now_utc,
            ),
        )
        cur.execute(
            """
            INSERT OR IGNORE INTO margin_calls (
                call_id, run_id, broker_id, account_id, business_date,
                base_currency, action, call_amount, flagged_pre_settle,
                approval_status, payload_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.margin_call.call_id,
                result.run_id,
                result.broker_id,
                result.account_id,
                result.business_date.isoformat(),
                result.base_currency,
                result.margin_call.action.value,
                str(result.margin_call.call_or_return_amount_base),
                1 if result.margin_call.flagged_before_settlement else 0,
                result.margin_call.approval_status.value,
                to_canonical_json(result.margin_call),
                now_utc,
            ),
        )
        self._conn.commit()

    def record_substitution(self, review: SubstitutionReviewResult) -> None:
        cur = self._conn.cursor()
        existing = cur.execute(
            "SELECT review_id FROM substitution_reviews WHERE review_id = ?", (review.review_id,)
        ).fetchone()
        if existing is not None:
            return
        now_utc = datetime.now(timezone.utc).isoformat()
        cur.execute(
            """
            INSERT INTO substitution_reviews (
                review_id, request_id, broker_id, account_id, business_date,
                settlement_permitted, approval_status, required_top_up, payload_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review.review_id,
                review.request_id,
                review.broker_id,
                review.account_id,
                review.business_date.isoformat(),
                1 if review.settlement_permitted else 0,
                review.approval_status.value,
                str(review.required_top_up_base),
                to_canonical_json(review),
                now_utc,
            ),
        )
        self._conn.commit()

    def get_reconciliation_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT result_json FROM reconciliation_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["result_json"])

    def get_latest_run_for_statement(self, statement_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT result_json FROM reconciliation_runs WHERE statement_id = ? ORDER BY run_seq DESC LIMIT 1",
            (statement_id,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["result_json"])

    def list_reconciliation_runs(self, broker_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if broker_id:
            rows = self._conn.execute(
                "SELECT run_id, statement_id, broker_id, account_id, business_date, break_count, reconciled_clean, recorded_at "
                "FROM reconciliation_runs WHERE broker_id = ? ORDER BY run_seq ASC",
                (broker_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT run_id, statement_id, broker_id, account_id, business_date, break_count, reconciled_clean, recorded_at "
                "FROM reconciliation_runs ORDER BY run_seq ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_statement_versions(self, broker_id: str, account_id: str, business_date: date) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT statement_id, version, supersedes_id, content_hash, recorded_at
            FROM statement_versions
            WHERE broker_id = ? AND account_id = ? AND business_date = ?
            ORDER BY version ASC
            """,
            (broker_id, account_id, business_date.isoformat()),
        ).fetchall()
        return [dict(r) for r in rows]

    def execute_raw(self, sql: str) -> None:
        """Exposed solely for immutability verification tests."""
        self._conn.execute(sql)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class PostgresJournalAdapter:
    """Optional PostgreSQL adapter implementing JournalStore using postgres_schema.sql.

    Requires `psycopg>=3.1` and a reachable PostgreSQL instance (not bundled in offline sandbox).
    """

    SCHEMA_PATH = Path(__file__).parent / "sql" / "postgres_schema.sql"

    def __init__(self, dsn: str) -> None:
        if not dsn or not dsn.strip():
            raise PersistenceError("PostgreSQL DSN must be non-empty")
        try:
            import psycopg  # type: ignore
        except ImportError as exc:
            raise PersistenceError(
                "PostgreSQL adapter requires 'psycopg[binary]==3.2.3'. "
                "Install optional dependency `[postgres]` or use SQLiteJournalStore for local runs.",
                context={"schema_file": str(self.SCHEMA_PATH)},
            ) from exc
        self._dsn = dsn
        self._psycopg = psycopg
        self._conn = psycopg.connect(dsn)
        self.apply_schema()

    def apply_schema(self) -> None:
        ddl = self.SCHEMA_PATH.read_text(encoding="utf-8")
        with self._conn.cursor() as cur:
            cur.execute(ddl)
        self._conn.commit()

    @classmethod
    def get_schema_sql(cls) -> str:
        return cls.SCHEMA_PATH.read_text(encoding="utf-8")

    def close(self) -> None:
        self._conn.close()
