"""Versioned reporting service anchored strictly to immutable frozen portfolio snapshots."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from hf_platform.adapters.interfaces import FundAdminAdapter
from hf_platform.domain import (
    EventType,
    PortfolioSnapshot,
    ReportArtifact,
    ReportRevisionDiff,
    ReportStatus,
    ReportType,
)
from hf_platform.errors import CloseGateError, DomainValidationError, NotFoundError
from hf_platform.services.audit_service import AuditService
from hf_platform.services.auth_service import AuthService
from hf_platform.storage.repository import StorageRepository


class ReportingService:
    """Generates, approves, revises (with version diffs), and delivers reports tied to frozen snapshots."""

    def __init__(
        self,
        store: StorageRepository,
        auth: AuthService,
        audit: AuditService,
        admin_adapter: FundAdminAdapter,
    ) -> None:
        self._store = store
        self._auth = auth
        self._audit = audit
        self._admin_adapter = admin_adapter

    def generate_report(
        self,
        *,
        snapshot_id: str,
        report_type: ReportType,
        actor_id: str,
        notes: str = "",
    ) -> ReportArtifact:
        """Generate a report strictly from a FROZEN snapshot. Rejects unfrozen/draft snapshots."""
        snap = self._require_frozen_snapshot(snapshot_id)
        user = self._auth.authorize(
            actor_id=actor_id,
            action="report.generate",
            entity_type="report",
            entity_id=f"{snap.fund_id}:{snap.business_date.isoformat()}:{report_type.value}",
            fund_id=snap.fund_id,
        )
        existing = [
            r
            for r in self._store.list_reports(snap.fund_id, snap.business_date)
            if r.report_type == report_type
        ]
        version = len(existing) + 1
        report_id = f"RPT_{snap.fund_id}_{snap.business_date.strftime('%Y%m%d')}_{report_type.value}_v{version}"
        payload = self._build_payload(snap, report_type, notes=notes)

        draft = ReportArtifact(
            report_id=report_id,
            fund_id=snap.fund_id,
            business_date=snap.business_date,
            report_type=report_type,
            version=version,
            status=ReportStatus.PENDING_APPROVAL,
            snapshot_id=snap.snapshot_id,
            snapshot_content_hash=snap.content_hash,
            generated_by=user.user_id,
            generated_at_utc=datetime.now(UTC),
            payload=payload,
            content_hash="",
        )
        report = draft.model_copy(update={"content_hash": draft.compute_content_hash()})
        self._store.save_report(report)
        self._audit.record(
            actor_id=user.user_id,
            actor_role=user.role,
            action="report.generate",
            entity_type="report",
            entity_id=report.report_id,
            details={
                "report_type": report_type.value,
                "version": version,
                "snapshot_id": snap.snapshot_id,
                "snapshot_hash": snap.content_hash,
            },
        )
        self._audit.emit_event(
            event_type=EventType.REPORT_GENERATED,
            fund_id=snap.fund_id,
            payload={"report_id": report.report_id, "version": version, "snapshot_id": snap.snapshot_id},
        )
        return report

    def approve_and_publish(self, *, report_id: str, actor_id: str) -> ReportArtifact:
        """Dual-control approval and publication of a pending report."""
        report = self._require_report(report_id)
        if report.status != ReportStatus.PENDING_APPROVAL:
            raise DomainValidationError(f"Report {report_id} is in status {report.status.value}, not PENDING_APPROVAL")
        approver = self._auth.authorize(
            actor_id=actor_id,
            action="report.approve",
            entity_type="report",
            entity_id=report_id,
            maker_id=report.generated_by,
            fund_id=report.fund_id,
        )
        # If this report supersedes an earlier published version, mark the prior version SUPERSEDED
        if report.supersedes_report_id:
            prior = self._store.get_report(report.supersedes_report_id)
            if prior and prior.status == ReportStatus.PUBLISHED:
                self._store.save_report(prior.model_copy(update={"status": ReportStatus.SUPERSEDED}))

        published = report.model_copy(
            update={
                "status": ReportStatus.PUBLISHED,
                "approved_by": approver.user_id,
                "approved_at_utc": datetime.now(UTC),
            }
        )
        self._store.save_report(published)
        self._audit.record(
            actor_id=approver.user_id,
            actor_role=approver.role,
            action="report.publish",
            entity_type="report",
            entity_id=published.report_id,
            details={
                "version": published.version,
                "maker_id": report.generated_by,
                "checker_id": approver.user_id,
                "supersedes_report_id": published.supersedes_report_id,
            },
        )
        self._audit.emit_event(
            event_type=EventType.REPORT_PUBLISHED,
            fund_id=published.fund_id,
            payload={"report_id": published.report_id, "version": published.version, "snapshot_id": published.snapshot_id},
        )
        return published

    def revise_report(
        self,
        *,
        prior_report_id: str,
        new_snapshot_id: str,
        reason: str,
        actor_id: str,
        notes: str = "",
    ) -> ReportArtifact:
        """Create a versioned, diff-tracked report revision referencing a new frozen snapshot."""
        if not reason.strip():
            raise DomainValidationError("Revision reason is mandatory for report revisions")
        prior = self._require_report(prior_report_id)
        snap = self._require_frozen_snapshot(new_snapshot_id)
        user = self._auth.authorize(
            actor_id=actor_id,
            action="report.revise",
            entity_type="report",
            entity_id=prior_report_id,
            fund_id=prior.fund_id,
        )
        same_type = [
            r
            for r in self._store.list_reports(snap.fund_id, snap.business_date)
            if r.report_type == prior.report_type
        ]
        new_version = max((r.version for r in same_type), default=prior.version) + 1
        new_report_id = (
            f"RPT_{snap.fund_id}_{snap.business_date.strftime('%Y%m%d')}_{prior.report_type.value}_v{new_version}"
        )
        new_payload = self._build_payload(snap, prior.report_type, notes=notes or f"Revision v{new_version}: {reason}")
        diff = self._compute_diff(prior, new_version, reason, new_payload)

        draft = ReportArtifact(
            report_id=new_report_id,
            fund_id=snap.fund_id,
            business_date=snap.business_date,
            report_type=prior.report_type,
            version=new_version,
            status=ReportStatus.PENDING_APPROVAL,
            snapshot_id=snap.snapshot_id,
            snapshot_content_hash=snap.content_hash,
            generated_by=user.user_id,
            generated_at_utc=datetime.now(UTC),
            supersedes_report_id=prior.report_id,
            revision_reason=reason,
            revision_diff=diff,
            payload=new_payload,
            content_hash="",
        )
        revised = draft.model_copy(update={"content_hash": draft.compute_content_hash()})
        self._store.save_report(revised)
        self._audit.record(
            actor_id=user.user_id,
            actor_role=user.role,
            action="report.revise",
            entity_type="report",
            entity_id=revised.report_id,
            details={
                "prior_report_id": prior.report_id,
                "new_version": new_version,
                "reason": reason,
                "changed_keys": sorted(diff.changed_fields.keys()),
            },
        )
        self._audit.emit_event(
            event_type=EventType.REPORT_REVISED,
            fund_id=snap.fund_id,
            payload={
                "report_id": revised.report_id,
                "prior_report_id": prior.report_id,
                "new_version": new_version,
                "reason": reason,
            },
        )
        return revised

    def deliver_to_administrator(self, *, report_id: str, actor_id: str, export_format: str = "json") -> dict[str, Any]:
        """Deliver a PUBLISHED report package to the external fund administrator interface."""
        report = self._require_report(report_id)
        if report.status != ReportStatus.PUBLISHED:
            raise CloseGateError(
                f"Cannot deliver report {report_id} in status {report.status.value}; report must be PUBLISHED",
                details={"report_id": report_id, "status": report.status.value},
            )
        user = self._auth.authorize(
            actor_id=actor_id,
            action="report.deliver",
            entity_type="report",
            entity_id=report_id,
            fund_id=report.fund_id,
        )
        receipt = self._admin_adapter.deliver_package(report, export_format=export_format)
        self._audit.record(
            actor_id=user.user_id,
            actor_role=user.role,
            action="report.deliver_admin",
            entity_type="report",
            entity_id=report_id,
            details=receipt,
        )
        return receipt

    def _require_frozen_snapshot(self, snapshot_id: str) -> PortfolioSnapshot:
        snap = self._store.get_snapshot(snapshot_id)
        if snap is None:
            raise NotFoundError(f"Snapshot not found: {snapshot_id}")
        if not snap.is_frozen:
            raise CloseGateError(
                f"Reports may only reference FROZEN snapshots; snapshot {snapshot_id} is not frozen",
                details={"snapshot_id": snapshot_id, "is_frozen": False},
            )
        return snap

    def _require_report(self, report_id: str) -> ReportArtifact:
        report = self._store.get_report(report_id)
        if report is None:
            raise NotFoundError(f"Report not found: {report_id}")
        return report

    def _build_payload(self, snap: PortfolioSnapshot, report_type: ReportType, notes: str = "") -> dict[str, Any]:
        base = {
            "fund_id": snap.fund_id,
            "business_date": snap.business_date.isoformat(),
            "snapshot_id": snap.snapshot_id,
            "snapshot_content_hash": snap.content_hash,
            "notes": notes,
            "disclaimer": "Prototype operational report — not an audited NAV or regulatory filing.",
        }
        if report_type == ReportType.NAV_SUMMARY:
            base.update(
                {
                    "prior_nav_usd": str(snap.prior_nav_usd),
                    "total_cash_usd": str(snap.total_cash_usd),
                    "total_long_mv_usd": str(snap.total_long_mv_usd),
                    "total_short_mv_usd": str(snap.total_short_mv_usd),
                    "net_market_value_usd": str(snap.net_market_value_usd),
                    "accrued_fees_usd": str(snap.accrued_fees_usd),
                    "nav_usd": str(snap.nav_usd),
                    "ledger_nav_usd": str(snap.ledger_nav_usd),
                    "nav_reconciliation_diff_usd": str(snap.nav_reconciliation_diff_usd),
                    "nav_per_share": str(snap.nav_per_share),
                    "daily_pnl_usd": str(snap.daily_pnl_usd),
                    "daily_return_pct": str(snap.daily_return_pct),
                }
            )
        elif report_type == ReportType.RISK_EXPOSURE:
            base.update(snap.risk_metrics.model_dump(mode="json"))
        elif report_type == ReportType.PERFORMANCE_ATTRIBUTION:
            base["strategies"] = [a.model_dump(mode="json") for a in snap.strategy_attribution]
            base["daily_pnl_usd"] = str(snap.daily_pnl_usd)
            base["daily_return_pct"] = str(snap.daily_return_pct)
        elif report_type == ReportType.RECONCILIATION_SUMMARY:
            recons = self._store.list_reconciliations(snap.fund_id, snap.business_date)
            latest = recons[-1] if recons else None
            base["reconciliation"] = latest.model_dump(mode="json") if latest else None
            excs = self._store.list_exceptions(snap.fund_id, snap.business_date)
            base["exception_count"] = len(excs)
            base["exceptions"] = [e.model_dump(mode="json") for e in excs]
        else:  # ADMINISTRATOR_EXTRACT
            base.update(
                {
                    "nav_usd": str(snap.nav_usd),
                    "nav_per_share": str(snap.nav_per_share),
                    "shares_outstanding": str(snap.shares_outstanding),
                    "total_cash_usd": str(snap.total_cash_usd),
                    "accrued_fees_usd": str(snap.accrued_fees_usd),
                    "position_count": len(snap.positions),
                    "positions": [p.model_dump(mode="json") for p in snap.positions],
                    "cash_balances": [c.model_dump(mode="json") for c in snap.cash_balances],
                }
            )
        return base

    def _compute_diff(
        self,
        prior: ReportArtifact,
        new_version: int,
        reason: str,
        new_payload: dict[str, Any],
    ) -> ReportRevisionDiff:
        changed: dict[str, dict[str, Any]] = {}
        all_keys = sorted(set(prior.payload.keys()) | set(new_payload.keys()))
        for key in all_keys:
            old_val = prior.payload.get(key)
            new_val = new_payload.get(key)
            if old_val != new_val:
                changed[key] = {"before": old_val, "after": new_val}
        return ReportRevisionDiff(
            prior_report_id=prior.report_id,
            prior_version=prior.version,
            new_version=new_version,
            reason=reason,
            changed_fields=changed,
        )
