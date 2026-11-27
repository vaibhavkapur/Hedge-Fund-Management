"""Report generation, immutable versioning, dual-control review/approval, and revisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Dict, List, Optional

from fund_reporting.adapters.contracts import (
    PortfolioOptimizerAdapter,
    RiskEngineAdapter,
    TransactionCostAdapter,
)
from fund_reporting.adapters.synthetic_adapters import (
    SyntheticPortfolioOptimizerAdapter,
    SyntheticRiskEngineAdapter,
    SyntheticTransactionCostAdapter,
)
from fund_reporting.domain.enums import (
    DataStatus,
    EnvironmentMode,
    ReportAudience,
    ReportStatus,
)
from fund_reporting.domain.errors import (
    ImmutableReportError,
    NotFoundError,
    ReconciliationError,
    ValidationError,
    WorkflowStateError,
)
from fund_reporting.domain.models import ReportRecord
from fund_reporting.domain.validation import utc_now_iso
from fund_reporting.metrics.engine import AnalyticsEngine
from fund_reporting.security.rbac import AccessController, Action, Actor
from fund_reporting.storage.repository import FundRepository


def compute_report_sha256(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class ReportWorkflowService:
    """Manages immutable versioned investor/internal reports with review & approval gates."""

    def __init__(
        self,
        repository: FundRepository,
        analytics_engine: AnalyticsEngine,
        access_controller: AccessController,
        *,
        risk_adapter: Optional[RiskEngineAdapter] = None,
        optimizer_adapter: Optional[PortfolioOptimizerAdapter] = None,
        tca_adapter: Optional[TransactionCostAdapter] = None,
        require_reconciled_for_publish: bool = True,
    ) -> None:
        self._repo = repository
        self._engine = analytics_engine
        self._rbac = access_controller
        self._risk_adapter = risk_adapter or SyntheticRiskEngineAdapter()
        self._optimizer_adapter = optimizer_adapter or SyntheticPortfolioOptimizerAdapter()
        self._tca_adapter = tca_adapter or SyntheticTransactionCostAdapter()
        self.require_reconciled_for_publish = require_reconciled_for_publish

    def _build_payload(
        self,
        fund_id: str,
        *,
        start_date: Optional[str],
        end_date: Optional[str],
        reporting_currency: Optional[str],
        as_of_utc: Optional[str],
        audience: ReportAudience,
        revision_number: int,
        supersedes_version_id: Optional[str],
        revision_reason: Optional[str],
    ) -> Dict[str, Any]:
        fund = self._repo.get_fund(fund_id)
        if fund is None:
            raise NotFoundError(f"Fund not found: {fund_id}", details={"fund_id": fund_id})

        # Enforce non-goal: simulated/paper data cannot claim AUDITED status
        if fund.environment in (EnvironmentMode.SIMULATED, EnvironmentMode.PAPER) and fund.data_status == DataStatus.AUDITED:
            raise ValidationError(
                "Simulated or paper fund data cannot be labeled AUDITED.",
                details={"fund_id": fund_id, "environment": fund.environment.value},
            )

        snapshots = self._repo.get_nav_snapshots(
            fund_id, start_date=start_date, end_date=end_date, as_of_utc=as_of_utc
        )
        bm_points = self._repo.get_benchmark_points(
            fund.benchmark_id, start_date=start_date, end_date=end_date
        )
        strat_snaps = self._repo.get_strategy_snapshots(
            fund_id, start_date=start_date, end_date=end_date
        )
        fx_rates = self._repo.get_fx_rates()

        analytics = self._engine.analyze(
            fund,
            snapshots,
            benchmark_points=bm_points,
            strategy_snapshots=strat_snaps,
            fx_rates=fx_rates,
            reporting_currency=reporting_currency,
        )

        # Enrich with upstream foundation adapter diagnostics
        net_returns = [pt["net_return"] for pt in analytics["daily_series"]]
        latest_date = analytics["period_end"]
        latest_strats = [s for s in strat_snaps if s.valuation_date == latest_date]
        avg_nav = (
            sum(pt["closing_nav"] for pt in analytics["daily_series"]) / len(analytics["daily_series"])
            if analytics["daily_series"]
            else 0.0
        )

        risk_diag = self._risk_adapter.get_risk_snapshot(
            fund.fund_id, latest_date, fund.environment, net_returns
        )
        opt_drift = self._optimizer_adapter.get_allocation_drift(
            fund.fund_id, latest_date, fund.environment, latest_strats
        )
        tca_summary = self._tca_adapter.get_tca_summary(
            fund.fund_id,
            analytics["period_start"],
            latest_date,
            fund.environment,
            analytics["reporting_currency"],
            strat_snaps,
            avg_nav,
        )

        analytics["audience"] = audience.value
        analytics["revision_metadata"] = {
            "revision_number": revision_number,
            "supersedes_version_id": supersedes_version_id,
            "revision_reason": revision_reason,
        }
        analytics["foundation_diagnostics"] = {
            "risk_engine": risk_diag.to_dict(),
            "portfolio_optimizer": opt_drift.to_dict(),
            "transaction_cost_analysis": tca_summary.to_dict(),
        }
        return analytics

    def create_draft_report(
        self,
        actor: Actor,
        *,
        fund_id: str,
        report_series_id: str,
        audience: ReportAudience = ReportAudience.INVESTOR,
        reporting_currency: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        as_of_utc: Optional[str] = None,
    ) -> ReportRecord:
        self._rbac.authorize(
            actor,
            Action.CREATE_REPORT,
            resource_type="report_series",
            resource_id=report_series_id,
            fund_id=fund_id,
        )

        existing = self._repo.list_reports(report_series_id=report_series_id)
        if existing:
            raise WorkflowStateError(
                f"Report series {report_series_id} already exists; use create_revision() instead.",
                details={"report_series_id": report_series_id},
            )

        payload = self._build_payload(
            fund_id,
            start_date=start_date,
            end_date=end_date,
            reporting_currency=reporting_currency,
            as_of_utc=as_of_utc,
            audience=audience,
            revision_number=1,
            supersedes_version_id=None,
            revision_reason=None,
        )
        digest = compute_report_sha256(payload)
        version_id = f"{report_series_id}-v1-{digest[:8]}"

        record = ReportRecord(
            version_id=version_id,
            report_series_id=report_series_id,
            fund_id=fund_id,
            revision_number=1,
            supersedes_version_id=None,
            revision_reason=None,
            audience=audience,
            reporting_currency=payload["reporting_currency"],
            period_start=payload["period_start"],
            period_end=payload["period_end"],
            as_of_utc=as_of_utc or payload["as_of_utc"],
            environment=EnvironmentMode(payload["fund"]["environment"]),
            data_status=DataStatus(payload["fund"]["data_status"]),
            methodology_version=payload["methodology_version"],
            status=ReportStatus.DRAFT,
            created_by=actor.actor_id,
            reviewed_by=None,
            approved_by=None,
            published_at_utc=None,
            content_sha256=digest,
            payload=payload,
        )
        self._repo.save_report(record)
        return record

    def submit_report(self, actor: Actor, version_id: str) -> ReportRecord:
        report = self._require_report(version_id)
        self._rbac.authorize(
            actor, Action.SUBMIT_REPORT, resource_type="report", resource_id=version_id, fund_id=report.fund_id
        )
        if report.status not in (ReportStatus.DRAFT, ReportStatus.REJECTED):
            raise WorkflowStateError(
                f"Cannot submit report from status {report.status.value}",
                details={"version_id": version_id, "status": report.status.value},
            )
        updated = replace(report, status=ReportStatus.SUBMITTED)
        self._repo.update_report(updated)
        return updated

    def review_report(
        self,
        actor: Actor,
        version_id: str,
        *,
        approve: bool = True,
        review_notes: str = "",
    ) -> ReportRecord:
        report = self._require_report(version_id)
        self._rbac.authorize(
            actor, Action.REVIEW_REPORT, resource_type="report", resource_id=version_id, fund_id=report.fund_id
        )
        self._rbac.enforce_separation_of_duties(
            actor,
            action=Action.REVIEW_REPORT,
            resource_id=version_id,
            prohibited_actor_ids={report.created_by},
            reason="Dual control violation: report author cannot review their own report.",
        )
        if report.status != ReportStatus.SUBMITTED:
            raise WorkflowStateError(
                f"Report must be SUBMITTED before review (current: {report.status.value})",
                details={"version_id": version_id, "status": report.status.value},
            )
        new_status = ReportStatus.REVIEWED if approve else ReportStatus.REJECTED
        new_payload = dict(report.payload)
        new_payload["review_notes"] = review_notes
        updated = replace(
            report,
            status=new_status,
            reviewed_by=actor.actor_id,
            payload=new_payload,
            content_sha256=compute_report_sha256(new_payload),
        )
        self._repo.update_report(updated)
        return updated

    def approve_and_publish_report(self, actor: Actor, version_id: str) -> ReportRecord:
        report = self._require_report(version_id)
        self._rbac.authorize(
            actor,
            Action.APPROVE_PUBLISH_REPORT,
            resource_type="report",
            resource_id=version_id,
            fund_id=report.fund_id,
        )
        self._rbac.enforce_separation_of_duties(
            actor,
            action=Action.APPROVE_PUBLISH_REPORT,
            resource_id=version_id,
            prohibited_actor_ids={report.created_by},
            reason="Dual control violation: report author cannot approve/publish their own report.",
        )
        if report.status != ReportStatus.REVIEWED:
            raise WorkflowStateError(
                f"Report must be REVIEWED before approval/publication (current: {report.status.value})",
                details={"version_id": version_id, "status": report.status.value},
            )
        if self.require_reconciled_for_publish:
            reconciled = bool(report.payload.get("reconciliation_summary", {}).get("all_snapshots_reconciled", False))
            if not reconciled:
                raise ReconciliationError(
                    "Cannot publish report containing unreconciled ledger snapshots.",
                    details={"version_id": version_id},
                )

        published = replace(
            report,
            status=ReportStatus.PUBLISHED,
            approved_by=actor.actor_id,
            published_at_utc=utc_now_iso(),
        )
        self._repo.update_report(published)

        # If this revision supersedes a prior published version, mark the prior version SUPERSEDED
        if published.supersedes_version_id:
            prior = self._repo.get_report(published.supersedes_version_id)
            if prior is not None and prior.status == ReportStatus.PUBLISHED:
                self._repo.update_report(replace(prior, status=ReportStatus.SUPERSEDED))

        return published

    def create_revision(
        self,
        actor: Actor,
        *,
        supersedes_version_id: str,
        revision_reason: str,
        reporting_currency: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        as_of_utc: Optional[str] = None,
    ) -> ReportRecord:
        prior = self._require_report(supersedes_version_id)
        self._rbac.authorize(
            actor,
            Action.REVISE_REPORT,
            resource_type="report",
            resource_id=supersedes_version_id,
            fund_id=prior.fund_id,
        )
        if not revision_reason.strip():
            raise ValidationError("revision_reason is required when creating a report revision.")

        series_reports = self._repo.list_reports(report_series_id=prior.report_series_id)
        next_rev = max(r.revision_number for r in series_reports) + 1

        payload = self._build_payload(
            prior.fund_id,
            start_date=start_date or prior.period_start,
            end_date=end_date or prior.period_end,
            reporting_currency=reporting_currency or prior.reporting_currency,
            as_of_utc=as_of_utc,
            audience=prior.audience,
            revision_number=next_rev,
            supersedes_version_id=prior.version_id,
            revision_reason=revision_reason.strip(),
        )
        digest = compute_report_sha256(payload)
        version_id = f"{prior.report_series_id}-v{next_rev}-{digest[:8]}"

        revised = ReportRecord(
            version_id=version_id,
            report_series_id=prior.report_series_id,
            fund_id=prior.fund_id,
            revision_number=next_rev,
            supersedes_version_id=prior.version_id,
            revision_reason=revision_reason.strip(),
            audience=prior.audience,
            reporting_currency=payload["reporting_currency"],
            period_start=payload["period_start"],
            period_end=payload["period_end"],
            as_of_utc=as_of_utc or payload["as_of_utc"],
            environment=EnvironmentMode(payload["fund"]["environment"]),
            data_status=DataStatus(payload["fund"]["data_status"]),
            methodology_version=payload["methodology_version"],
            status=ReportStatus.DRAFT,
            created_by=actor.actor_id,
            reviewed_by=None,
            approved_by=None,
            published_at_utc=None,
            content_sha256=digest,
            payload=payload,
        )
        self._repo.save_report(revised)
        return revised

    def attempt_in_place_edit(self, version_id: str) -> None:
        report = self._require_report(version_id)
        if report.status in (ReportStatus.PUBLISHED, ReportStatus.SUPERSEDED):
            raise ImmutableReportError(
                f"Report {version_id} is immutable ({report.status.value}); create a new revision instead.",
                details={"version_id": version_id, "status": report.status.value},
            )

    def get_report(self, actor: Actor, version_id: str) -> ReportRecord:
        report = self._require_report(version_id)
        self._rbac.authorize_report_read(actor, report, Action.READ_REPORT)
        return report

    def list_reports(
        self,
        actor: Actor,
        *,
        fund_id: Optional[str] = None,
        report_series_id: Optional[str] = None,
    ) -> List[ReportRecord]:
        reports = self._repo.list_reports(fund_id=fund_id, report_series_id=report_series_id)
        visible: List[ReportRecord] = []
        for r in reports:
            if not actor.can_access_fund(r.fund_id):
                continue
            if actor.role.value == "VIEWER_INVESTOR":
                if r.audience != ReportAudience.INVESTOR:
                    continue
                if r.status not in (ReportStatus.PUBLISHED, ReportStatus.SUPERSEDED):
                    continue
            visible.append(r)
        return visible

    def _require_report(self, version_id: str) -> ReportRecord:
        report = self._repo.get_report(version_id)
        if report is None:
            raise NotFoundError(f"Report version not found: {version_id}", details={"version_id": version_id})
        return report
