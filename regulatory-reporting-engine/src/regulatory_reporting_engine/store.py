"""Immutable append-only journal, snapshot, versioned filing, and approval audit store."""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from regulatory_reporting_engine.errors import (
    ApprovalWorkflowError,
    ConfigurationError,
    DuplicateFilingError,
    ImmutabilityViolationError,
)
from regulatory_reporting_engine.models import (
    ApprovalAuditEntry,
    FieldLineage,
    GovernedSnapshot,
    ReportArtifact,
    ReportStatus,
    canonical_json_sha256,
)

ALLOWED_STATUS_TRANSITIONS = {
    ReportStatus.DRAFT: {ReportStatus.REVIEWED, ReportStatus.REJECTED},
    ReportStatus.REVIEWED: {ReportStatus.APPROVED, ReportStatus.REJECTED},
    ReportStatus.APPROVED: {ReportStatus.SUPERSEDED},
    ReportStatus.REJECTED: set(),
    ReportStatus.SUPERSEDED: set(),
}


class JournalStore(Protocol):
    """Storage interface satisfied by SQLite offline store and optional PostgreSQL adapter."""

    def save_snapshot(self, snapshot: GovernedSnapshot) -> None: ...
    def get_snapshot(self, snapshot_id: str) -> Optional[GovernedSnapshot]: ...
    def save_report(self, report: ReportArtifact, *, actor: str, rationale: str) -> None: ...
    def get_report(self, report_id: str) -> Optional[ReportArtifact]: ...
    def list_versions(self, filing_key: str) -> List[ReportArtifact]: ...
    def transition_status(
        self, report_id: str, to_status: ReportStatus, *, actor: str, rationale: str, timestamp_utc: str
    ) -> ApprovalAuditEntry: ...
    def get_audit_trail(self, report_id: str) -> List[ApprovalAuditEntry]: ...
    def verify_audit_chain(self, report_id: str) -> bool: ...
    def close(self) -> None: ...


class SqliteJournalStore:
    """
    Append-only SQLite store mirroring sql/schema_postgres.sql with database-level
    immutability triggers on governed_snapshots and approval_audit_journal.
    """

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS governed_snapshots (
                snapshot_id         TEXT PRIMARY KEY,
                fund_id             TEXT NOT NULL,
                as_of_date          TEXT NOT NULL,
                period_start        TEXT NOT NULL,
                period_end          TEXT NOT NULL,
                base_currency       TEXT NOT NULL,
                payload_sha256      TEXT NOT NULL,
                payload_json        TEXT NOT NULL,
                ingested_at_utc     TEXT NOT NULL
            );

            CREATE TRIGGER IF NOT EXISTS trg_snapshots_no_update
            BEFORE UPDATE ON governed_snapshots
            BEGIN
                SELECT RAISE(ABORT, 'Immutability violation: governed_snapshots is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_snapshots_no_delete
            BEFORE DELETE ON governed_snapshots
            BEGIN
                SELECT RAISE(ABORT, 'Immutability violation: governed_snapshots is append-only');
            END;

            CREATE TABLE IF NOT EXISTS regulatory_reports (
                report_id               TEXT PRIMARY KEY,
                filing_key              TEXT NOT NULL,
                version_number          INTEGER NOT NULL CHECK (version_number >= 1),
                supersedes_report_id    TEXT REFERENCES regulatory_reports(report_id),
                correction_reason       TEXT,
                snapshot_id             TEXT NOT NULL REFERENCES governed_snapshots(snapshot_id),
                snapshot_sha256         TEXT NOT NULL,
                profile_id              TEXT NOT NULL,
                schema_version          TEXT NOT NULL,
                mapping_id              TEXT NOT NULL,
                mapping_version         TEXT NOT NULL,
                status                  TEXT NOT NULL,
                watermark               TEXT NOT NULL,
                content_sha256          TEXT NOT NULL,
                fields_json             TEXT NOT NULL,
                lineage_json            TEXT NOT NULL,
                xml_payload             TEXT NOT NULL,
                csv_payload             TEXT NOT NULL,
                created_at_utc          TEXT NOT NULL,
                UNIQUE (filing_key, version_number)
            );

            CREATE TRIGGER IF NOT EXISTS trg_reports_no_delete
            BEFORE DELETE ON regulatory_reports
            BEGIN
                SELECT RAISE(ABORT, 'Immutability violation: regulatory_reports cannot be deleted');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_reports_immutable_payload
            BEFORE UPDATE OF fields_json, lineage_json, xml_payload, csv_payload, content_sha256, snapshot_id
            ON regulatory_reports
            BEGIN
                SELECT RAISE(ABORT, 'Immutability violation: report content is immutable; create a new version');
            END;

            CREATE TABLE IF NOT EXISTS approval_audit_journal (
                audit_id            INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id           TEXT NOT NULL REFERENCES regulatory_reports(report_id),
                from_status         TEXT NOT NULL,
                to_status           TEXT NOT NULL,
                actor               TEXT NOT NULL,
                rationale           TEXT NOT NULL,
                event_sha256        TEXT NOT NULL,
                prev_event_sha256   TEXT,
                recorded_at_utc     TEXT NOT NULL
            );

            CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
            BEFORE UPDATE ON approval_audit_journal
            BEGIN
                SELECT RAISE(ABORT, 'Immutability violation: approval_audit_journal is append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
            BEFORE DELETE ON approval_audit_journal
            BEGIN
                SELECT RAISE(ABORT, 'Immutability violation: approval_audit_journal is append-only');
            END;
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def save_snapshot(self, snapshot: GovernedSnapshot) -> None:
        existing = self._conn.execute(
            "SELECT payload_sha256 FROM governed_snapshots WHERE snapshot_id = ?",
            (snapshot.snapshot_id,),
        ).fetchone()
        if existing is not None:
            if existing["payload_sha256"] == snapshot.payload_sha256:
                return  # Idempotent re-ingestion of identical snapshot
            raise ImmutabilityViolationError(
                f"Snapshot '{snapshot.snapshot_id}' already exists with different payload hash.",
                details={
                    "snapshot_id": snapshot.snapshot_id,
                    "existing_sha256": existing["payload_sha256"],
                    "incoming_sha256": snapshot.payload_sha256,
                },
            )
        self._conn.execute(
            """
            INSERT INTO governed_snapshots (
                snapshot_id, fund_id, as_of_date, period_start, period_end,
                base_currency, payload_sha256, payload_json, ingested_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.fund.fund_id,
                snapshot.nav.as_of_date.isoformat(),
                snapshot.reporting_period.start_date.isoformat(),
                snapshot.reporting_period.end_date.isoformat(),
                snapshot.fund.base_currency,
                snapshot.payload_sha256,
                json.dumps(snapshot.to_dict(), sort_keys=True),
                snapshot.captured_at_utc,
            ),
        )
        self._conn.commit()

    def get_snapshot(self, snapshot_id: str) -> Optional[GovernedSnapshot]:
        row = self._conn.execute(
            "SELECT payload_json FROM governed_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        return GovernedSnapshot.from_dict(json.loads(row["payload_json"]))

    def _append_audit(
        self,
        *,
        report_id: str,
        from_status: str,
        to_status: str,
        actor: str,
        rationale: str,
        timestamp_utc: str,
    ) -> ApprovalAuditEntry:
        prev_row = self._conn.execute(
            "SELECT event_sha256 FROM approval_audit_journal WHERE report_id = ? ORDER BY audit_id DESC LIMIT 1",
            (report_id,),
        ).fetchone()
        prev_hash = prev_row["event_sha256"] if prev_row is not None else None
        event_body = {
            "report_id": report_id,
            "from_status": from_status,
            "to_status": to_status,
            "actor": actor,
            "rationale": rationale,
            "prev_event_sha256": prev_hash,
            "recorded_at_utc": timestamp_utc,
        }
        event_hash = canonical_json_sha256(event_body)
        cur = self._conn.execute(
            """
            INSERT INTO approval_audit_journal (
                report_id, from_status, to_status, actor, rationale,
                event_sha256, prev_event_sha256, recorded_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                from_status,
                to_status,
                actor,
                rationale,
                event_hash,
                prev_hash,
                timestamp_utc,
            ),
        )
        return ApprovalAuditEntry(
            audit_id=int(cur.lastrowid),
            report_id=report_id,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            rationale=rationale,
            event_sha256=event_hash,
            prev_event_sha256=prev_hash,
            recorded_at_utc=timestamp_utc,
        )

    def save_report(self, report: ReportArtifact, *, actor: str, rationale: str) -> None:
        existing_versions = self._conn.execute(
            "SELECT report_id, version_number, status FROM regulatory_reports WHERE filing_key = ? ORDER BY version_number DESC",
            (report.filing_key,),
        ).fetchall()

        if report.version_number == 1 and existing_versions:
            raise DuplicateFilingError(
                f"Duplicate filing detected for filing_key '{report.filing_key}'. "
                f"Existing version v{existing_versions[0]['version_number']} ({existing_versions[0]['report_id']}) "
                f"must be corrected via create_correction() rather than re-filing v1.",
                details={
                    "filing_key": report.filing_key,
                    "existing_report_id": existing_versions[0]["report_id"],
                    "existing_version": existing_versions[0]["version_number"],
                },
            )

        if report.version_number > 1:
            if not existing_versions:
                raise ApprovalWorkflowError(
                    f"Cannot save correction v{report.version_number} for '{report.filing_key}' without a prior version."
                )
            expected_next = int(existing_versions[0]["version_number"]) + 1
            if report.version_number != expected_next:
                raise DuplicateFilingError(
                    f"Version conflict for '{report.filing_key}': expected v{expected_next}, got v{report.version_number}.",
                    details={"filing_key": report.filing_key, "expected_version": expected_next, "got": report.version_number},
                )
            if report.supersedes_report_id != existing_versions[0]["report_id"]:
                raise ApprovalWorkflowError(
                    f"Correction must supersede latest version '{existing_versions[0]['report_id']}', "
                    f"got '{report.supersedes_report_id}'."
                )

        fields_serialized = {
            k: (str(v) if isinstance(v, Decimal) else v) for k, v in report.fields.items()
        }
        lineage_serialized = {k: v.to_dict() for k, v in report.lineage.items()}

        try:
            self._conn.execute(
                """
                INSERT INTO regulatory_reports (
                    report_id, filing_key, version_number, supersedes_report_id, correction_reason,
                    snapshot_id, snapshot_sha256, profile_id, schema_version, mapping_id, mapping_version,
                    status, watermark, content_sha256, fields_json, lineage_json,
                    xml_payload, csv_payload, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.filing_key,
                    report.version_number,
                    report.supersedes_report_id,
                    report.correction_reason,
                    report.snapshot_id,
                    report.snapshot_sha256,
                    report.profile_id,
                    report.schema_version,
                    report.mapping_id,
                    report.mapping_version,
                    report.status.value,
                    report.watermark,
                    report.content_sha256,
                    json.dumps(fields_serialized, sort_keys=True),
                    json.dumps(lineage_serialized, sort_keys=True),
                    report.xml_payload,
                    report.csv_payload,
                    report.created_at_utc,
                ),
            )
            self._append_audit(
                report_id=report.report_id,
                from_status="NONE",
                to_status=report.status.value,
                actor=actor,
                rationale=rationale,
                timestamp_utc=report.created_at_utc,
            )
            # Mark prior version as SUPERSEDED if it was APPROVED or REVIEWED or DRAFT
            if report.supersedes_report_id is not None:
                prior_status = existing_versions[0]["status"]
                if prior_status != ReportStatus.SUPERSEDED.value:
                    self._conn.execute(
                        "UPDATE regulatory_reports SET status = ? WHERE report_id = ?",
                        (ReportStatus.SUPERSEDED.value, report.supersedes_report_id),
                    )
                    self._append_audit(
                        report_id=report.supersedes_report_id,
                        from_status=prior_status,
                        to_status=ReportStatus.SUPERSEDED.value,
                        actor=actor,
                        rationale=f"Superseded by {report.report_id} (v{report.version_number}): {report.correction_reason}",
                        timestamp_utc=report.created_at_utc,
                    )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            raise DuplicateFilingError(
                f"Database uniqueness violation saving report '{report.report_id}': {exc}",
                details={"report_id": report.report_id, "filing_key": report.filing_key},
            ) from exc

    def _row_to_report(self, row: sqlite3.Row) -> ReportArtifact:
        fields = json.loads(row["fields_json"])
        lineage_raw = json.loads(row["lineage_json"])
        lineage = {
            k: FieldLineage(
                target_field=v["target_field"],
                value=v["value"],
                unit=v["unit"],
                source_type=v["source_type"],
                mapping_id=v["mapping_id"],
                mapping_version=v["mapping_version"],
                snapshot_id=v["snapshot_id"],
                source_record_ids=tuple(v["source_record_ids"]),
                derivation_expression=v["derivation_expression"],
                upstream_fields=tuple(v.get("upstream_fields", [])),
                rounding_mode=v.get("rounding_mode"),
                decimal_places=v.get("decimal_places"),
            )
            for k, v in lineage_raw.items()
        }
        return ReportArtifact(
            report_id=row["report_id"],
            filing_key=row["filing_key"],
            version_number=int(row["version_number"]),
            supersedes_report_id=row["supersedes_report_id"],
            correction_reason=row["correction_reason"],
            snapshot_id=row["snapshot_id"],
            snapshot_sha256=row["snapshot_sha256"],
            profile_id=row["profile_id"],
            schema_version=row["schema_version"],
            mapping_id=row["mapping_id"],
            mapping_version=row["mapping_version"],
            status=ReportStatus(row["status"]),
            watermark=row["watermark"],
            fields=fields,
            lineage=lineage,
            xml_payload=row["xml_payload"],
            csv_payload=row["csv_payload"],
            content_sha256=row["content_sha256"],
            created_at_utc=row["created_at_utc"],
        )

    def get_report(self, report_id: str) -> Optional[ReportArtifact]:
        row = self._conn.execute(
            "SELECT * FROM regulatory_reports WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        return self._row_to_report(row) if row is not None else None

    def list_versions(self, filing_key: str) -> List[ReportArtifact]:
        rows = self._conn.execute(
            "SELECT * FROM regulatory_reports WHERE filing_key = ? ORDER BY version_number ASC",
            (filing_key,),
        ).fetchall()
        return [self._row_to_report(r) for r in rows]

    def transition_status(
        self,
        report_id: str,
        to_status: ReportStatus,
        *,
        actor: str,
        rationale: str,
        timestamp_utc: str,
    ) -> ApprovalAuditEntry:
        if not actor.strip() or not rationale.strip():
            raise ApprovalWorkflowError("Status transition requires non-empty actor and rationale.")
        row = self._conn.execute(
            "SELECT status FROM regulatory_reports WHERE report_id = ?",
            (report_id,),
        ).fetchone()
        if row is None:
            raise ApprovalWorkflowError(f"Report '{report_id}' not found.")
        current = ReportStatus(row["status"])
        if to_status not in ALLOWED_STATUS_TRANSITIONS[current]:
            raise ApprovalWorkflowError(
                f"Illegal status transition for '{report_id}': {current.value} -> {to_status.value}.",
                details={
                    "report_id": report_id,
                    "from_status": current.value,
                    "to_status": to_status.value,
                    "allowed": sorted(s.value for s in ALLOWED_STATUS_TRANSITIONS[current]),
                },
            )
        self._conn.execute(
            "UPDATE regulatory_reports SET status = ? WHERE report_id = ?",
            (to_status.value, report_id),
        )
        entry = self._append_audit(
            report_id=report_id,
            from_status=current.value,
            to_status=to_status.value,
            actor=actor,
            rationale=rationale,
            timestamp_utc=timestamp_utc,
        )
        self._conn.commit()
        return entry

    def get_audit_trail(self, report_id: str) -> List[ApprovalAuditEntry]:
        rows = self._conn.execute(
            "SELECT * FROM approval_audit_journal WHERE report_id = ? ORDER BY audit_id ASC",
            (report_id,),
        ).fetchall()
        return [
            ApprovalAuditEntry(
                audit_id=int(r["audit_id"]),
                report_id=r["report_id"],
                from_status=r["from_status"],
                to_status=r["to_status"],
                actor=r["actor"],
                rationale=r["rationale"],
                event_sha256=r["event_sha256"],
                prev_event_sha256=r["prev_event_sha256"],
                recorded_at_utc=r["recorded_at_utc"],
            )
            for r in rows
        ]

    def verify_audit_chain(self, report_id: str) -> bool:
        """Cryptographically verify the append-only hash chain for a report's approval audit trail."""
        entries = self.get_audit_trail(report_id)
        expected_prev: Optional[str] = None
        for entry in entries:
            if entry.prev_event_sha256 != expected_prev:
                return False
            recomputed = canonical_json_sha256(
                {
                    "report_id": entry.report_id,
                    "from_status": entry.from_status,
                    "to_status": entry.to_status,
                    "actor": entry.actor,
                    "rationale": entry.rationale,
                    "prev_event_sha256": entry.prev_event_sha256,
                    "recorded_at_utc": entry.recorded_at_utc,
                }
            )
            if recomputed != entry.event_sha256:
                return False
            expected_prev = entry.event_sha256
        return True

    def attempt_raw_mutation_check(self, sql: str) -> None:
        """Execute a raw SQL statement; translates SQLite immutability trigger aborts into ImmutabilityViolationError."""
        try:
            self._conn.execute(sql)
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            self._conn.rollback()
            if "Immutability violation" in str(exc):
                raise ImmutabilityViolationError(str(exc)) from exc
            raise


class PostgresJournalStoreUnavailableError(ConfigurationError):
    """Raised when Postgres backend is configured without psycopg or live database connection."""

    default_code = "REG-1006"
