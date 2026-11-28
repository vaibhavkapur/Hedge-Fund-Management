"""Immutable SQLite ledger and versioned snapshot persistence."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .errors import ApprovalError, StorageError
from .models import (
    ApprovalRecord,
    ApprovalStatus,
    CalculationRunResult,
    CapitalFlow,
    FeeAgreement,
    InvestorLot,
    JournalEntry,
    JournalEntryType,
    JournalLine,
    NavSnapshot,
    canonical_json,
    stable_hash,
)


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS fee_agreements (
    agreement_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    share_class TEXT NOT NULL,
    currency TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (agreement_id, version)
);

CREATE TABLE IF NOT EXISTS calculation_runs (
    run_id TEXT PRIMARY KEY,
    agreement_id TEXT NOT NULL,
    share_class TEXT NOT NULL,
    currency TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    period_start_date TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_run_id TEXT,
    config_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (agreement_id, valuation_date, version)
);

CREATE TABLE IF NOT EXISTS journal_entries (
    entry_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES calculation_runs(run_id),
    version INTEGER NOT NULL,
    posting_date TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    description TEXT NOT NULL,
    supersedes_entry_id TEXT,
    total_debits TEXT NOT NULL,
    total_credits TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journal_lines (
    line_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT NOT NULL REFERENCES journal_entries(entry_id),
    account_code TEXT NOT NULL,
    account_name TEXT NOT NULL,
    debit TEXT NOT NULL,
    credit TEXT NOT NULL,
    currency TEXT NOT NULL,
    share_class TEXT NOT NULL,
    lot_id TEXT NOT NULL,
    memo TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES calculation_runs(run_id),
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    comment TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS prevent_journal_entry_update
BEFORE UPDATE ON journal_entries
BEGIN
    SELECT RAISE(ABORT, 'Immutable ledger: journal_entries cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS prevent_journal_entry_delete
BEFORE DELETE ON journal_entries
BEGIN
    SELECT RAISE(ABORT, 'Immutable ledger: journal_entries cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS prevent_journal_line_update
BEFORE UPDATE ON journal_lines
BEGIN
    SELECT RAISE(ABORT, 'Immutable ledger: journal_lines cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS prevent_journal_line_delete
BEFORE DELETE ON journal_lines
BEGIN
    SELECT RAISE(ABORT, 'Immutable ledger: journal_lines cannot be deleted');
END;
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SqliteFeeStore:
    """Append-only SQLite persistence for agreements, runs, immutable journals, and approvals."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteFeeStore":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def save_agreement(self, agreement: FeeAgreement) -> None:
        payload = canonical_json(agreement.to_dict())
        cur = self._conn.execute(
            "SELECT payload_json FROM fee_agreements WHERE agreement_id = ? AND version = ?",
            (agreement.agreement_id, agreement.version),
        )
        existing = cur.fetchone()
        if existing is not None:
            if existing["payload_json"] == payload:
                return
            raise StorageError(
                f"Agreement ({agreement.agreement_id}, v{agreement.version}) already exists with different content",
                code="AGREEMENT_VERSION_CONFLICT",
                field="agreement_id",
            )
        self._conn.execute(
            """
            INSERT INTO fee_agreements (agreement_id, version, share_class, currency, effective_date, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agreement.agreement_id,
                agreement.version,
                agreement.share_class,
                agreement.currency,
                agreement.effective_date.isoformat(),
                payload,
                _utc_now_iso(),
            ),
        )
        self._conn.commit()

    def get_agreement(self, agreement_id: str, version: Optional[int] = None) -> FeeAgreement:
        if version is None:
            cur = self._conn.execute(
                "SELECT payload_json FROM fee_agreements WHERE agreement_id = ? ORDER BY version DESC LIMIT 1",
                (agreement_id,),
            )
        else:
            cur = self._conn.execute(
                "SELECT payload_json FROM fee_agreements WHERE agreement_id = ? AND version = ?",
                (agreement_id, version),
            )
        row = cur.fetchone()
        if row is None:
            raise StorageError(
                f"FeeAgreement '{agreement_id}' (version={version}) not found",
                code="AGREEMENT_NOT_FOUND",
                field="agreement_id",
            )
        return FeeAgreement.from_dict(json.loads(row["payload_json"]))

    def has_active_run(self, agreement_id: str, valuation_date: date, version: int) -> bool:
        cur = self._conn.execute(
            """
            SELECT 1 FROM calculation_runs
            WHERE agreement_id = ? AND valuation_date = ? AND version = ?
            """,
            (agreement_id, valuation_date.isoformat(), version),
        )
        return cur.fetchone() is not None

    def save_run(
        self,
        *,
        result: CalculationRunResult,
        agreement: FeeAgreement,
        snapshot: NavSnapshot,
        opening_lots: Sequence[InvestorLot],
        capital_flows: Sequence[CapitalFlow],
    ) -> None:
        inputs_payload = canonical_json(
            {
                "agreement": agreement.to_dict(),
                "snapshot": snapshot.to_dict(),
                "opening_lots": [lot.to_dict() for lot in opening_lots],
                "capital_flows": [flow.to_dict() for flow in capital_flows],
            }
        )
        result_payload = canonical_json(result.to_dict())

        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO calculation_runs (
                        run_id, agreement_id, share_class, currency,
                        valuation_date, period_start_date, version, supersedes_run_id,
                        config_version, input_hash, output_hash, status,
                        inputs_json, result_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.run_id,
                        result.agreement_id,
                        result.share_class,
                        result.currency,
                        result.valuation_date.isoformat(),
                        result.period_start_date.isoformat(),
                        result.version,
                        result.supersedes_run_id,
                        result.config_version,
                        result.input_hash,
                        result.output_hash,
                        result.status.value,
                        inputs_payload,
                        result_payload,
                        _utc_now_iso(),
                    ),
                )

                for entry in result.journal_entries:
                    self._conn.execute(
                        """
                        INSERT INTO journal_entries (
                            entry_id, run_id, version, posting_date, valuation_date,
                            currency, entry_type, description, supersedes_entry_id,
                            total_debits, total_credits
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            entry.entry_id,
                            entry.run_id,
                            entry.version,
                            entry.posting_date.isoformat(),
                            entry.valuation_date.isoformat(),
                            entry.currency,
                            entry.entry_type.value,
                            entry.description,
                            entry.supersedes_entry_id,
                            format(entry.total_debits, "f"),
                            format(entry.total_credits, "f"),
                        ),
                    )
                    for line in entry.lines:
                        self._conn.execute(
                            """
                            INSERT INTO journal_lines (
                                entry_id, account_code, account_name,
                                debit, credit, currency, share_class, lot_id, memo
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                entry.entry_id,
                                line.account_code,
                                line.account_name,
                                format(line.debit, "f"),
                                format(line.credit, "f"),
                                line.currency,
                                line.share_class,
                                line.lot_id,
                                line.memo,
                            ),
                        )

                if result.supersedes_run_id:
                    self._conn.execute(
                        "UPDATE calculation_runs SET status = ? WHERE run_id = ?",
                        (ApprovalStatus.SUPERSEDED.value, result.supersedes_run_id),
                    )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Duplicate or conflicting run ({result.agreement_id}, {result.valuation_date}, v{result.version}): {exc}",
                code="DUPLICATE_RUN",
                field="run_id",
            ) from exc

    def get_run_record(self, run_id: str) -> Dict[str, Any]:
        cur = self._conn.execute("SELECT * FROM calculation_runs WHERE run_id = ?", (run_id,))
        row = cur.fetchone()
        if row is None:
            raise StorageError(f"Calculation run '{run_id}' not found", code="RUN_NOT_FOUND", field="run_id")
        return {
            "run_id": row["run_id"],
            "agreement_id": row["agreement_id"],
            "share_class": row["share_class"],
            "currency": row["currency"],
            "valuation_date": row["valuation_date"],
            "period_start_date": row["period_start_date"],
            "version": row["version"],
            "supersedes_run_id": row["supersedes_run_id"],
            "config_version": row["config_version"],
            "input_hash": row["input_hash"],
            "output_hash": row["output_hash"],
            "status": row["status"],
            "inputs": json.loads(row["inputs_json"]),
            "result": json.loads(row["result_json"]),
            "created_at": row["created_at"],
        }

    def get_journal_entries_for_run(self, run_id: str) -> Tuple[JournalEntry, ...]:
        cur = self._conn.execute(
            "SELECT * FROM journal_entries WHERE run_id = ? ORDER BY rowid ASC",
            (run_id,),
        )
        entries: List[JournalEntry] = []
        for row in cur.fetchall():
            lcur = self._conn.execute(
                "SELECT * FROM journal_lines WHERE entry_id = ? ORDER BY line_id ASC",
                (row["entry_id"],),
            )
            lines = tuple(
                JournalLine(
                    account_code=lr["account_code"],
                    account_name=lr["account_name"],
                    debit=Decimal(lr["debit"]),
                    credit=Decimal(lr["credit"]),
                    currency=lr["currency"],
                    share_class=lr["share_class"],
                    lot_id=lr["lot_id"],
                    memo=lr["memo"],
                )
                for lr in lcur.fetchall()
            )
            entries.append(
                JournalEntry(
                    entry_id=row["entry_id"],
                    run_id=row["run_id"],
                    version=row["version"],
                    posting_date=date.fromisoformat(row["posting_date"]),
                    valuation_date=date.fromisoformat(row["valuation_date"]),
                    currency=row["currency"],
                    entry_type=JournalEntryType(row["entry_type"]),
                    description=row["description"],
                    lines=lines,
                    supersedes_entry_id=row["supersedes_entry_id"],
                )
            )
        return tuple(entries)

    def record_approval(
        self,
        *,
        run_id: str,
        target_status: ApprovalStatus,
        actor: str,
        comment: str = "",
    ) -> ApprovalRecord:
        record = self.get_run_record(run_id)
        current_status = ApprovalStatus(record["status"])

        valid_transitions = {
            ApprovalStatus.DRAFT: {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED},
            ApprovalStatus.APPROVED: {ApprovalStatus.SUPERSEDED},
            ApprovalStatus.REJECTED: set(),
            ApprovalStatus.SUPERSEDED: set(),
        }
        if target_status not in valid_transitions[current_status]:
            raise ApprovalError(
                f"Invalid approval transition for run '{run_id}': {current_status.value} -> {target_status.value}",
                code="INVALID_APPROVAL_TRANSITION",
                field="status",
            )

        decided_at = _utc_now_iso()
        approval_id = f"APR-{stable_hash({'run_id': run_id, 'status': target_status.value, 'actor': actor, 'at': decided_at}, length=12)}"
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO approvals (approval_id, run_id, status, actor, decided_at, comment)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (approval_id, run_id, target_status.value, actor, decided_at, comment),
            )
            self._conn.execute(
                "UPDATE calculation_runs SET status = ? WHERE run_id = ?",
                (target_status.value, run_id),
            )
        return ApprovalRecord(
            approval_id=approval_id,
            run_id=run_id,
            status=target_status,
            actor=actor,
            decided_at=decided_at,
            comment=comment,
        )

    def list_runs(self) -> List[Dict[str, Any]]:
        cur = self._conn.execute(
            """
            SELECT run_id, agreement_id, share_class, valuation_date, version,
                   supersedes_run_id, status, input_hash, output_hash, created_at
            FROM calculation_runs
            ORDER BY valuation_date ASC, version ASC, rowid ASC
            """
        )
        return [dict(row) for row in cur.fetchall()]

    def list_approvals(self, run_id: str) -> List[ApprovalRecord]:
        cur = self._conn.execute(
            "SELECT * FROM approvals WHERE run_id = ? ORDER BY rowid ASC",
            (run_id,),
        )
        return [
            ApprovalRecord(
                approval_id=r["approval_id"],
                run_id=r["run_id"],
                status=ApprovalStatus(r["status"]),
                actor=r["actor"],
                decided_at=r["decided_at"],
                comment=r["comment"],
            )
            for r in cur.fetchall()
        ]
