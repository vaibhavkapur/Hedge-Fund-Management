"""High-level Regulatory Reporting Engine orchestrating pure domain logic and immutable persistence."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from regulatory_reporting_engine.config import EngineConfig
from regulatory_reporting_engine.errors import (
    ApprovalWorkflowError,
    AutomatedSubmissionProhibitedError,
    UnsupportedInputError,
)
from regulatory_reporting_engine.mapper import map_snapshot_to_profile
from regulatory_reporting_engine.models import (
    ApprovalAuditEntry,
    GovernedSnapshot,
    ReportArtifact,
    ReportStatus,
    canonical_json_sha256,
)
from regulatory_reporting_engine.reconciliation import ReconciliationReport, reconcile_reports
from regulatory_reporting_engine.renderer import render_csv_report, render_xml_report
from regulatory_reporting_engine.schemas import ProfileSchema, SchemaDiff, SchemaRegistry
from regulatory_reporting_engine.store import (
    JournalStore,
    PostgresJournalStoreUnavailableError,
    SqliteJournalStore,
)

logger = logging.getLogger("regulatory_reporting_engine")


def build_filing_key(fund_id: str, period_id: str, profile_id: str) -> str:
    """Stable deterministic business key for a fund + period + jurisdiction profile."""
    return f"{fund_id}::{period_id}::{profile_id}"


def build_report_id(filing_key: str, version_number: int, content_sha256: str) -> str:
    """Stable deterministic report identifier derived from filing key, version, and content hash."""
    slug = filing_key.replace("::", "-")
    return f"RPT-{slug}-V{version_number}-{content_sha256[:12].upper()}"


class RegulatoryReportingEngine:
    """
    Service interface separating pure report generation/reconciliation from immutable storage,
    approval auditing, versioned corrections, and structured diagnostics.
    """

    def __init__(
        self,
        config: EngineConfig,
        *,
        store: Optional[JournalStore] = None,
        registry: Optional[SchemaRegistry] = None,
    ) -> None:
        self.config = config
        self.registry = registry or SchemaRegistry(config.paths.profiles_dir, config.paths.mappings_dir)
        if store is not None:
            self.store: JournalStore = store
        elif config.storage.backend == "memory":
            self.store = SqliteJournalStore(":memory:")
        elif config.storage.backend == "sqlite":
            self.store = SqliteJournalStore(config.storage.sqlite_path)
        elif config.storage.backend == "postgres":
            raise PostgresJournalStoreUnavailableError(
                "PostgreSQL backend requires psycopg and a configured database DSN; "
                "use 'sqlite' or 'memory' for offline execution (DDL provided in sql/schema_postgres.sql)."
            )
        else:
            raise UnsupportedInputError(f"Unsupported storage backend '{config.storage.backend}'.")

        self.counters: Dict[str, int] = {
            "snapshots_ingested": 0,
            "reports_generated": 0,
            "corrections_created": 0,
            "reconciliations_passed": 0,
            "reconciliations_failed": 0,
            "status_transitions": 0,
            "replays_verified": 0,
            "submission_attempts_blocked": 0,
        }

    def close(self) -> None:
        self.store.close()

    def build_draft_report_pure(
        self,
        snapshot: GovernedSnapshot,
        *,
        profile_id: str,
        schema_version: str,
        version_number: int = 1,
        supersedes_report_id: Optional[str] = None,
        correction_reason: Optional[str] = None,
        mapping_override: Optional[Dict[str, Any]] = None,
        created_at_utc: Optional[str] = None,
    ) -> ReportArtifact:
        """
        Pure domain function (no I/O or DB mutation):
        maps a GovernedSnapshot into a schema-validated ReportArtifact with XML, CSV, and full lineage.
        """
        profile_key = f"{profile_id}:{schema_version}"
        if profile_key not in self.config.supported_profiles:
            raise UnsupportedInputError(
                f"Profile '{profile_key}' is not enabled in EngineConfig.supported_profiles.",
                details={"profile": profile_key, "supported": list(self.config.supported_profiles)},
            )

        schema: ProfileSchema = self.registry.get_schema(profile_id, schema_version)
        mapping = mapping_override if mapping_override is not None else self.registry.get_mapping(profile_id, schema_version)

        fields, lineage = map_snapshot_to_profile(
            snapshot,
            schema,
            mapping,
            default_rounding_mode=self.config.numeric_policy.default_rounding_mode,
        )

        filing_key = build_filing_key(snapshot.fund.fund_id, snapshot.reporting_period.period_id, profile_id)
        content_hash = canonical_json_sha256(
            {
                "filing_key": filing_key,
                "version_number": version_number,
                "snapshot_sha256": snapshot.payload_sha256,
                "profile_id": profile_id,
                "schema_version": schema_version,
                "mapping_id": str(mapping["mapping_id"]),
                "mapping_version": str(mapping["mapping_version"]),
                "fields": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in fields.items()},
            }
        )
        report_id = build_report_id(filing_key, version_number, content_hash)
        timestamp = created_at_utc or snapshot.captured_at_utc

        xml_payload = render_xml_report(
            report_id=report_id,
            filing_key=filing_key,
            version_number=version_number,
            snapshot_id=snapshot.snapshot_id,
            schema=schema,
            mapping_id=str(mapping["mapping_id"]),
            mapping_version=str(mapping["mapping_version"]),
            watermark=self.config.submission_policy.watermark,
            fields=fields,
        )
        csv_payload = render_csv_report(
            report_id=report_id,
            version_number=version_number,
            schema=schema,
            watermark=self.config.submission_policy.watermark,
            fields=fields,
        )

        return ReportArtifact(
            report_id=report_id,
            filing_key=filing_key,
            version_number=version_number,
            supersedes_report_id=supersedes_report_id,
            correction_reason=correction_reason,
            snapshot_id=snapshot.snapshot_id,
            snapshot_sha256=snapshot.payload_sha256,
            profile_id=profile_id,
            schema_version=schema_version,
            mapping_id=str(mapping["mapping_id"]),
            mapping_version=str(mapping["mapping_version"]),
            status=ReportStatus.DRAFT,
            watermark=self.config.submission_policy.watermark,
            fields=fields,
            lineage=lineage,
            xml_payload=xml_payload,
            csv_payload=csv_payload,
            content_sha256=content_hash,
            created_at_utc=timestamp,
        )

    def ingest_snapshot(self, snapshot: GovernedSnapshot) -> None:
        self.store.save_snapshot(snapshot)
        self.counters["snapshots_ingested"] += 1
        logger.info(
            "snapshot_ingested",
            extra={"snapshot_id": snapshot.snapshot_id, "fund_id": snapshot.fund.fund_id, "sha256": snapshot.payload_sha256},
        )

    def generate_report(
        self,
        snapshot: GovernedSnapshot,
        *,
        profile_id: str,
        schema_version: str,
        actor: str = "system_reporting_batch",
        rationale: str = "Initial draft generation from governed snapshot",
        mapping_override: Optional[Dict[str, Any]] = None,
        created_at_utc: Optional[str] = None,
    ) -> ReportArtifact:
        """Persist snapshot (idempotently), generate v1 draft report, and record immutable audit entry."""
        self.ingest_snapshot(snapshot)
        report = self.build_draft_report_pure(
            snapshot,
            profile_id=profile_id,
            schema_version=schema_version,
            version_number=1,
            mapping_override=mapping_override,
            created_at_utc=created_at_utc,
        )
        self.store.save_report(report, actor=actor, rationale=rationale)
        self.counters["reports_generated"] += 1
        logger.info(
            "report_generated",
            extra={"report_id": report.report_id, "filing_key": report.filing_key, "version": report.version_number},
        )
        return report

    def create_correction(
        self,
        prior_report_id: str,
        corrected_snapshot: GovernedSnapshot,
        *,
        correction_reason: str,
        actor: str,
        schema_version: Optional[str] = None,
        created_at_utc: Optional[str] = None,
    ) -> ReportArtifact:
        """
        Create an immutable versioned correction superseding prior_report_id.
        Supports both restated snapshot corrections and schema-evolution upgrades (e.g., v1.0.0 -> v2.0.0).
        """
        if not correction_reason.strip() or not actor.strip():
            raise ApprovalWorkflowError("Correction requires non-empty correction_reason and actor.")
        prior = self.store.get_report(prior_report_id)
        if prior is None:
            raise ApprovalWorkflowError(f"Prior report '{prior_report_id}' not found for correction.")

        versions = self.store.list_versions(prior.filing_key)
        latest = versions[-1]
        if latest.report_id != prior.report_id:
            raise ApprovalWorkflowError(
                f"Cannot correct superseded version '{prior.report_id}'; latest version is '{latest.report_id}'."
            )

        target_schema_ver = schema_version or prior.schema_version
        self.ingest_snapshot(corrected_snapshot)
        next_version = latest.version_number + 1

        corrected = self.build_draft_report_pure(
            corrected_snapshot,
            profile_id=prior.profile_id,
            schema_version=target_schema_ver,
            version_number=next_version,
            supersedes_report_id=latest.report_id,
            correction_reason=correction_reason,
            created_at_utc=created_at_utc,
        )
        self.store.save_report(corrected, actor=actor, rationale=f"Version correction v{next_version}: {correction_reason}")
        self.counters["corrections_created"] += 1
        return corrected

    def transition_status(
        self,
        report_id: str,
        to_status: ReportStatus,
        *,
        actor: str,
        rationale: str,
        timestamp_utc: Optional[str] = None,
    ) -> ApprovalAuditEntry:
        ts = timestamp_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = self.store.transition_status(
            report_id, to_status, actor=actor, rationale=rationale, timestamp_utc=ts
        )
        self.counters["status_transitions"] += 1
        return entry

    def reconcile_reports(
        self,
        left: ReportArtifact,
        right: ReportArtifact,
        *,
        fail_on_mismatch: Optional[bool] = None,
    ) -> ReconciliationReport:
        policy = self.config.reconciliation_policy
        should_fail = policy.fail_on_mismatch if fail_on_mismatch is None else fail_on_mismatch
        try:
            rec = reconcile_reports(
                left,
                right,
                monetary_tolerance=policy.monetary_tolerance,
                ratio_tolerance=policy.ratio_tolerance,
                fail_on_mismatch=should_fail,
            )
        except Exception:
            self.counters["reconciliations_failed"] += 1
            raise
        if rec.passed:
            self.counters["reconciliations_passed"] += 1
        else:
            self.counters["reconciliations_failed"] += 1
        return rec

    def replay_report(self, report_id: str) -> Dict[str, Any]:
        """
        Deterministically re-run report generation from the persisted immutable snapshot and verify
        exact hash, XML, CSV, and field equality against the stored ReportArtifact.
        """
        stored = self.store.get_report(report_id)
        if stored is None:
            raise ApprovalWorkflowError(f"Report '{report_id}' not found for replay.")
        snapshot = self.store.get_snapshot(stored.snapshot_id)
        if snapshot is None:
            raise ApprovalWorkflowError(f"Snapshot '{stored.snapshot_id}' missing for replay.")

        replayed = self.build_draft_report_pure(
            snapshot,
            profile_id=stored.profile_id,
            schema_version=stored.schema_version,
            version_number=stored.version_number,
            supersedes_report_id=stored.supersedes_report_id,
            correction_reason=stored.correction_reason,
            created_at_utc=stored.created_at_utc,
        )
        matches = (
            replayed.report_id == stored.report_id
            and replayed.content_sha256 == stored.content_sha256
            and replayed.xml_payload == stored.xml_payload
            and replayed.csv_payload == stored.csv_payload
        )
        self.counters["replays_verified"] += 1
        return {
            "report_id": report_id,
            "replayed_report_id": replayed.report_id,
            "stored_sha256": stored.content_sha256,
            "replayed_sha256": replayed.content_sha256,
            "exact_match": matches,
        }

    def compare_schemas(self, profile_id: str, from_version: str, to_version: str) -> SchemaDiff:
        return self.registry.compare_schema_versions(profile_id, from_version, to_version)

    def submit_to_regulator(self, report_id: str, *, destination: str = "external_portal") -> None:
        """
        Structural safety guard: automated regulatory filing submission is strictly prohibited.
        Always raises AutomatedSubmissionProhibitedError.
        """
        self.counters["submission_attempts_blocked"] += 1
        raise AutomatedSubmissionProhibitedError(
            f"Automated submission of report '{report_id}' to '{destination}' is prohibited. "
            "This engine generates reviewed illustrative drafts for offline human compliance review only.",
            details={"report_id": report_id, "destination": destination},
        )

    def list_filing_versions(self, filing_key: str) -> List[ReportArtifact]:
        return self.store.list_versions(filing_key)
