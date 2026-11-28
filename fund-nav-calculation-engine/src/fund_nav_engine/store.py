"""SQLite-backed immutable valuation snapshot store and append-only audit journal."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from fund_nav_engine.errors import ImmutabilityViolationError, RestatementError, ValidationError
from fund_nav_engine.io_loader import bundle_from_dict, config_from_dict, result_from_dict
from fund_nav_engine.models import (
    AuditJournalEntry,
    NavCalculationResult,
    SnapshotStatus,
    ValuationConfig,
    ValuationInputBundle,
    canonical_json,
    format_utc_datetime,
    parse_utc_datetime,
    sha256_canonical,
)

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS input_bundles (
    input_hash TEXT PRIMARY KEY,
    bundle_id TEXT NOT NULL,
    fund_id TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    input_version TEXT NOT NULL,
    bundle_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
    stored_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS valuation_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft', 'approved', 'frozen')),
    prior_snapshot_id TEXT NULL REFERENCES valuation_snapshots(snapshot_id),
    restatement_reason TEXT NULL,
    input_hash TEXT NOT NULL REFERENCES input_bundles(input_hash),
    output_hash TEXT NOT NULL,
    config_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_by TEXT NULL,
    approved_at TEXT NULL,
    frozen_by TEXT NULL,
    frozen_at TEXT NULL,
    UNIQUE(fund_id, valuation_date, version)
);

CREATE TABLE IF NOT EXISTS audit_journal (
    entry_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    details_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_prevent_frozen_snapshot_update
BEFORE UPDATE ON valuation_snapshots
WHEN OLD.status = 'frozen'
BEGIN
    SELECT RAISE(ABORT, 'FROZEN_SNAPSHOT_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_frozen_snapshot_delete
BEFORE DELETE ON valuation_snapshots
WHEN OLD.status = 'frozen'
BEGIN
    SELECT RAISE(ABORT, 'FROZEN_SNAPSHOT_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_audit_update
BEFORE UPDATE ON audit_journal
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_JOURNAL_IMMUTABLE');
END;

CREATE TRIGGER IF NOT EXISTS trg_prevent_audit_delete
BEFORE DELETE ON audit_journal
BEGIN
    SELECT RAISE(ABORT, 'AUDIT_JOURNAL_IMMUTABLE');
END;
"""


class SqliteNavStore:
    """Persistent SQLite store enforcing database-level immutability on frozen snapshots."""

    def __init__(self, db_path: Union[str, Path] = ":memory:") -> None:
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA_SQL)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteNavStore":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def _append_audit(
        self,
        snapshot_id: str,
        event_type: str,
        actor: str,
        timestamp: datetime,
        details: Dict[str, Any],
    ) -> AuditJournalEntry:
        ts_str = format_utc_datetime(timestamp)
        count_row = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM audit_journal WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        seq = int(count_row["cnt"]) + 1
        entry_id = f"AUD-{snapshot_id}-{seq:04d}-{event_type}"
        payload = {
            "entry_id": entry_id,
            "snapshot_id": snapshot_id,
            "event_type": event_type,
            "actor": actor,
            "timestamp": ts_str,
            "details": details,
        }
        payload_hash = sha256_canonical(payload)
        self._conn.execute(
            """
            INSERT INTO audit_journal (
                entry_id, snapshot_id, event_type, actor, timestamp, details_json, payload_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                snapshot_id,
                event_type,
                actor,
                ts_str,
                canonical_json(details),
                payload_hash,
            ),
        )
        return AuditJournalEntry(
            entry_id=entry_id,
            snapshot_id=snapshot_id,
            event_type=event_type,
            actor=actor,
            timestamp=timestamp,
            details=details,
            payload_hash=payload_hash,
        )

    def save_draft(
        self,
        result: NavCalculationResult,
        bundle: ValuationInputBundle,
        config: ValuationConfig,
        *,
        actor: str = "system",
        recorded_at: Optional[datetime] = None,
    ) -> NavCalculationResult:
        """Persist a draft snapshot and its versioned inputs (idempotent for identical rerun)."""
        ts = (recorded_at or result.calculated_at).astimezone(timezone.utc)
        ts_str = format_utc_datetime(ts)

        existing = self._conn.execute(
            "SELECT status, output_hash, payload_json FROM valuation_snapshots WHERE snapshot_id = ?",
            (result.snapshot_id,),
        ).fetchone()
        if existing is not None:
            if existing["output_hash"] == result.output_hash:
                return result_from_dict(json.loads(existing["payload_json"]))
            raise ImmutabilityViolationError(
                f"Snapshot '{result.snapshot_id}' already exists with different output hash.",
                details={"snapshot_id": result.snapshot_id, "existing_status": existing["status"]},
            )

        with self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO input_bundles (
                    input_hash, bundle_id, fund_id, valuation_date, input_version,
                    bundle_json, config_json, stored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.input_hash,
                    bundle.bundle_id,
                    bundle.fund_id,
                    bundle.valuation_date.isoformat(),
                    bundle.input_version,
                    canonical_json(bundle.to_dict()),
                    canonical_json(config.to_dict()),
                    ts_str,
                ),
            )
            try:
                self._conn.execute(
                    """
                    INSERT INTO valuation_snapshots (
                        snapshot_id, fund_id, valuation_date, version, status,
                        prior_snapshot_id, restatement_reason, input_hash, output_hash,
                        config_version, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.snapshot_id,
                        result.fund_id,
                        result.valuation_date.isoformat(),
                        result.version,
                        result.status.value,
                        result.prior_snapshot_id,
                        result.restatement_reason,
                        result.input_hash,
                        result.output_hash,
                        result.config_version,
                        canonical_json(result.to_dict()),
                        ts_str,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ImmutabilityViolationError(
                    f"Cannot insert snapshot '{result.snapshot_id}' (version conflict or invalid prior_snapshot_id): {exc}",
                    details={"snapshot_id": result.snapshot_id, "version": result.version},
                ) from exc

            self._append_audit(
                snapshot_id=result.snapshot_id,
                event_type="SNAPSHOT_CALCULATED",
                actor=actor,
                timestamp=ts,
                details={
                    "version": result.version,
                    "input_hash": result.input_hash,
                    "output_hash": result.output_hash,
                    "prior_snapshot_id": result.prior_snapshot_id,
                    "restatement_reason": result.restatement_reason,
                },
            )
        return result

    def approve_snapshot(
        self,
        snapshot_id: str,
        *,
        actor: str,
        approved_at: Optional[datetime] = None,
    ) -> NavCalculationResult:
        """Transition a draft snapshot to approved status."""
        ts = (approved_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        row = self._conn.execute(
            "SELECT status, payload_json FROM valuation_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise ValidationError(f"Snapshot '{snapshot_id}' not found.")
        if row["status"] == SnapshotStatus.FROZEN.value:
            raise ImmutabilityViolationError(
                f"Snapshot '{snapshot_id}' is already frozen and cannot be modified.",
                details={"snapshot_id": snapshot_id},
            )
        if row["status"] == SnapshotStatus.APPROVED.value:
            return result_from_dict(json.loads(row["payload_json"]))

        current = result_from_dict(json.loads(row["payload_json"]))
        updated = replace(current, status=SnapshotStatus.APPROVED)
        with self._conn:
            try:
                self._conn.execute(
                    """
                    UPDATE valuation_snapshots
                    SET status = ?, payload_json = ?, approved_by = ?, approved_at = ?
                    WHERE snapshot_id = ?
                    """,
                    (
                        SnapshotStatus.APPROVED.value,
                        canonical_json(updated.to_dict()),
                        actor,
                        format_utc_datetime(ts),
                        snapshot_id,
                    ),
                )
            except sqlite3.DatabaseError as exc:
                if "FROZEN_SNAPSHOT_IMMUTABLE" in str(exc):
                    raise ImmutabilityViolationError(
                        f"Snapshot '{snapshot_id}' is frozen and immutable."
                    ) from exc
                raise
            self._append_audit(
                snapshot_id=snapshot_id,
                event_type="SNAPSHOT_APPROVED",
                actor=actor,
                timestamp=ts,
                details={"output_hash": updated.output_hash},
            )
        return updated

    def freeze_snapshot(
        self,
        snapshot_id: str,
        *,
        actor: str,
        frozen_at: Optional[datetime] = None,
    ) -> NavCalculationResult:
        """Freeze an approved snapshot so it becomes permanently immutable."""
        ts = (frozen_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        row = self._conn.execute(
            "SELECT status, payload_json FROM valuation_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise ValidationError(f"Snapshot '{snapshot_id}' not found.")
        if row["status"] == SnapshotStatus.FROZEN.value:
            raise ImmutabilityViolationError(
                f"Snapshot '{snapshot_id}' is already frozen.",
                details={"snapshot_id": snapshot_id},
            )
        if row["status"] != SnapshotStatus.APPROVED.value:
            raise ValidationError(
                f"Snapshot '{snapshot_id}' must be approved before freezing (current status: {row['status']}).",
                details={"snapshot_id": snapshot_id, "status": row["status"]},
            )

        current = result_from_dict(json.loads(row["payload_json"]))
        frozen = replace(current, status=SnapshotStatus.FROZEN)
        with self._conn:
            self._conn.execute(
                """
                UPDATE valuation_snapshots
                SET status = ?, payload_json = ?, frozen_by = ?, frozen_at = ?
                WHERE snapshot_id = ?
                """,
                (
                    SnapshotStatus.FROZEN.value,
                    canonical_json(frozen.to_dict()),
                    actor,
                    format_utc_datetime(ts),
                    snapshot_id,
                ),
            )
            self._append_audit(
                snapshot_id=snapshot_id,
                event_type="SNAPSHOT_FROZEN",
                actor=actor,
                timestamp=ts,
                details={"output_hash": frozen.output_hash},
            )
        return frozen

    def record_audit_event(
        self,
        snapshot_id: str,
        event_type: str,
        actor: str,
        details: Dict[str, Any],
        timestamp: Optional[datetime] = None,
    ) -> AuditJournalEntry:
        ts = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
        with self._conn:
            return self._append_audit(snapshot_id, event_type, actor, ts, details)

    def attempt_raw_mutation(self, snapshot_id: str, new_payload: str) -> None:
        """Attempt direct UPDATE on valuation_snapshots (used to verify DB trigger enforcement)."""
        try:
            with self._conn:
                self._conn.execute(
                    "UPDATE valuation_snapshots SET payload_json = ? WHERE snapshot_id = ?",
                    (new_payload, snapshot_id),
                )
        except sqlite3.DatabaseError as exc:
            if "FROZEN_SNAPSHOT_IMMUTABLE" in str(exc):
                raise ImmutabilityViolationError(
                    f"Database trigger blocked update of frozen snapshot '{snapshot_id}'.",
                    details={"snapshot_id": snapshot_id},
                ) from exc
            raise

    def attempt_raw_delete(self, snapshot_id: str) -> None:
        """Attempt direct DELETE on valuation_snapshots (used to verify DB trigger enforcement)."""
        try:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM valuation_snapshots WHERE snapshot_id = ?",
                    (snapshot_id,),
                )
        except sqlite3.DatabaseError as exc:
            if "FROZEN_SNAPSHOT_IMMUTABLE" in str(exc):
                raise ImmutabilityViolationError(
                    f"Database trigger blocked deletion of frozen snapshot '{snapshot_id}'.",
                    details={"snapshot_id": snapshot_id},
                ) from exc
            raise

    def get_snapshot(self, snapshot_id: str) -> NavCalculationResult:
        row = self._conn.execute(
            "SELECT payload_json, output_hash FROM valuation_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            raise ValidationError(f"Snapshot '{snapshot_id}' not found.")
        res = result_from_dict(json.loads(row["payload_json"]))
        recomputed = sha256_canonical(res.core_payload_dict())
        if recomputed != row["output_hash"] or recomputed != res.output_hash:
            raise ImmutabilityViolationError(
                f"Snapshot '{snapshot_id}' integrity hash mismatch.",
                details={"expected": row["output_hash"], "actual": recomputed},
            )
        return res

    def get_input_bundle_and_config(
        self, input_hash: str
    ) -> Tuple[ValuationInputBundle, ValuationConfig]:
        row = self._conn.execute(
            "SELECT bundle_json, config_json FROM input_bundles WHERE input_hash = ?",
            (input_hash,),
        ).fetchone()
        if row is None:
            raise ValidationError(f"Input bundle for hash '{input_hash}' not found.")
        bundle = bundle_from_dict(json.loads(row["bundle_json"]))
        config = config_from_dict(json.loads(row["config_json"]))
        return bundle, config

    def get_latest_frozen(
        self, fund_id: str, valuation_date: date
    ) -> Optional[NavCalculationResult]:
        row = self._conn.execute(
            """
            SELECT payload_json FROM valuation_snapshots
            WHERE fund_id = ? AND valuation_date = ? AND status = 'frozen'
            ORDER BY version DESC
            LIMIT 1
            """,
            (fund_id, valuation_date.isoformat()),
        ).fetchone()
        if row is None:
            return None
        return result_from_dict(json.loads(row["payload_json"]))

    def list_snapshots(
        self, fund_id: str, valuation_date: Optional[date] = None
    ) -> List[NavCalculationResult]:
        if valuation_date is not None:
            rows = self._conn.execute(
                """
                SELECT payload_json FROM valuation_snapshots
                WHERE fund_id = ? AND valuation_date = ?
                ORDER BY version ASC
                """,
                (fund_id, valuation_date.isoformat()),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT payload_json FROM valuation_snapshots
                WHERE fund_id = ?
                ORDER BY valuation_date ASC, version ASC
                """,
                (fund_id,),
            ).fetchall()
        return [result_from_dict(json.loads(r["payload_json"])) for r in rows]

    def get_restatement_chain(self, snapshot_id: str) -> List[NavCalculationResult]:
        """Return ordered lineage [v1, v2, ..., snapshot_id] following prior_snapshot_id."""
        chain: List[NavCalculationResult] = []
        visited = set()
        current_id: Optional[str] = snapshot_id
        while current_id is not None:
            if current_id in visited:
                raise RestatementError(
                    f"Cycle detected in restatement chain at '{current_id}'."
                )
            visited.add(current_id)
            snap = self.get_snapshot(current_id)
            chain.append(snap)
            current_id = snap.prior_snapshot_id
        chain.reverse()
        return chain

    def get_audit_journal(self, snapshot_id: Optional[str] = None) -> List[AuditJournalEntry]:
        if snapshot_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM audit_journal WHERE snapshot_id = ? ORDER BY rowid ASC",
                (snapshot_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM audit_journal ORDER BY rowid ASC"
            ).fetchall()
        return [
            AuditJournalEntry(
                entry_id=str(r["entry_id"]),
                snapshot_id=str(r["snapshot_id"]),
                event_type=str(r["event_type"]),
                actor=str(r["actor"]),
                timestamp=parse_utc_datetime(r["timestamp"], "timestamp"),
                details=json.loads(r["details_json"]),
                payload_hash=str(r["payload_hash"]),
            )
            for r in rows
        ]
