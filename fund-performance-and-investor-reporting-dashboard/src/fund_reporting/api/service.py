"""Application service facade enforcing RBAC, ledger validation, analytics, and versioned reporting."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fund_reporting.adapters.synthetic_adapters import (
    SyntheticPortfolioOptimizerAdapter,
    SyntheticRiskEngineAdapter,
    SyntheticTransactionCostAdapter,
)
from fund_reporting.domain.enums import ReportAudience, Role
from fund_reporting.domain.errors import NotFoundError, ValidationError
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
from fund_reporting.domain.validation import (
    validate_environment_homogeneity,
    validate_nav_snapshot,
)
from fund_reporting.metrics.engine import AnalyticsConfig, AnalyticsEngine
from fund_reporting.reporting.exporters import (
    export_report_csv,
    export_report_html,
    export_report_json,
    export_report_markdown,
)
from fund_reporting.reporting.workflow import ReportWorkflowService
from fund_reporting.security.rbac import AccessController, Action, Actor
from fund_reporting.storage.repository import FundRepository


class FundReportingService:
    """High-level orchestration boundary used by the HTTP API, CLI scripts, and tests."""

    def __init__(
        self,
        repository: FundRepository,
        *,
        config: Optional[AnalyticsConfig] = None,
        require_dual_control: bool = True,
        require_reconciled_for_publish: bool = True,
    ) -> None:
        self.repo = repository
        self.config = config or AnalyticsConfig()
        self.engine = AnalyticsEngine(self.config)
        self.rbac = AccessController(repository, require_dual_control=require_dual_control)
        self.risk_adapter = SyntheticRiskEngineAdapter()
        self.optimizer_adapter = SyntheticPortfolioOptimizerAdapter()
        self.tca_adapter = SyntheticTransactionCostAdapter()
        self.reports = ReportWorkflowService(
            repository,
            self.engine,
            self.rbac,
            risk_adapter=self.risk_adapter,
            optimizer_adapter=self.optimizer_adapter,
            tca_adapter=self.tca_adapter,
            require_reconciled_for_publish=require_reconciled_for_publish,
        )

    @classmethod
    def from_config_path(cls, repository: FundRepository, config_path: Path) -> "FundReportingService":
        cfg = AnalyticsConfig.from_file(config_path)
        return cls(repository, config=cfg)

    def get_health(self) -> Dict[str, Any]:
        funds = self.repo.list_funds()
        return {
            "status": "ok",
            "methodology_version": self.config.methodology_version,
            "order_execution_enabled": False,
            "fund_count": len(funds),
            "require_dual_control": self.rbac.require_dual_control,
        }

    def register_fund(self, actor: Actor, fund: FundDefinition) -> FundDefinition:
        self.rbac.authorize(
            actor,
            Action.INGEST_DATA,
            resource_type="fund",
            resource_id=fund.fund_id,
            fund_id=fund.fund_id,
        )
        self.repo.upsert_fund(fund)
        return fund

    def list_funds(self, actor: Actor) -> List[FundDefinition]:
        funds = self.repo.list_funds()
        return [f for f in funds if actor.can_access_fund(f.fund_id)]

    def ingest_nav_snapshot(
        self,
        actor: Actor,
        snapshot: NavSnapshot,
        *,
        cash_flows: Sequence[CashFlowRecord] = (),
        strategy_snapshots: Sequence[StrategySnapshot] = (),
    ) -> NavSnapshot:
        self.rbac.authorize(
            actor,
            Action.INGEST_DATA,
            resource_type="nav_snapshot",
            resource_id=snapshot.snapshot_id,
            fund_id=snapshot.fund_id,
        )
        fund = self.repo.get_fund(snapshot.fund_id)
        if fund is None:
            raise NotFoundError(f"Fund not registered: {snapshot.fund_id}", details={"fund_id": snapshot.fund_id})

        validate_nav_snapshot(
            snapshot,
            tolerance_abs=self.config.reconciliation_tolerance_abs,
            tolerance_rel=self.config.reconciliation_tolerance_rel,
            expected_environment=fund.environment,
        )

        # Ensure cash flows and strategy snapshots match fund environment (no paper/live mixing)
        if cash_flows:
            validate_environment_homogeneity(
                [fund.environment] + [cf.environment for cf in cash_flows],
                context=f"cash_flows:{snapshot.snapshot_id}",
            )
        if strategy_snapshots:
            validate_environment_homogeneity(
                [fund.environment] + [st.environment for st in strategy_snapshots],
                context=f"strategy_snapshots:{snapshot.snapshot_id}",
            )

        self.repo.save_nav_snapshot(snapshot)
        for cf in cash_flows:
            self.repo.save_cash_flow(cf)
        if strategy_snapshots:
            self.repo.save_strategy_snapshots(strategy_snapshots)
        return snapshot

    def ingest_fx_rates(self, actor: Actor, rates: Sequence[FxRateRecord]) -> int:
        self.rbac.authorize(
            actor,
            Action.INGEST_DATA,
            resource_type="fx_rates",
            resource_id=f"batch:{len(rates)}",
        )
        self.repo.save_fx_rates(rates)
        return len(rates)

    def ingest_benchmark(self, actor: Actor, points: Sequence[BenchmarkPoint]) -> int:
        bm_id = points[0].benchmark_id if points else "empty"
        self.rbac.authorize(
            actor,
            Action.INGEST_DATA,
            resource_type="benchmark",
            resource_id=bm_id,
        )
        self.repo.save_benchmark_points(points)
        return len(points)

    def get_snapshots(
        self,
        actor: Actor,
        fund_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        as_of_utc: Optional[str] = None,
    ) -> List[NavSnapshot]:
        self.rbac.authorize(
            actor,
            Action.READ_INTERNAL_ANALYTICS,
            resource_type="nav_snapshot_series",
            resource_id=fund_id,
            fund_id=fund_id,
        )
        return self.repo.get_nav_snapshots(
            fund_id, start_date=start_date, end_date=end_date, as_of_utc=as_of_utc
        )

    def get_dashboard_analytics(
        self,
        actor: Actor,
        fund_id: str,
        *,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        reporting_currency: Optional[str] = None,
        as_of_utc: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.rbac.authorize(
            actor,
            Action.READ_INTERNAL_ANALYTICS,
            resource_type="analytics",
            resource_id=fund_id,
            fund_id=fund_id,
        )
        fund = self.repo.get_fund(fund_id)
        if fund is None:
            raise NotFoundError(f"Fund not found: {fund_id}", details={"fund_id": fund_id})

        snapshots = self.repo.get_nav_snapshots(
            fund_id, start_date=start_date, end_date=end_date, as_of_utc=as_of_utc
        )
        bm_points = self.repo.get_benchmark_points(
            fund.benchmark_id, start_date=start_date, end_date=end_date
        )
        strat_snaps = self.repo.get_strategy_snapshots(
            fund_id, start_date=start_date, end_date=end_date
        )
        fx_rates = self.repo.get_fx_rates()

        result = self.engine.analyze(
            fund,
            snapshots,
            benchmark_points=bm_points,
            strategy_snapshots=strat_snaps,
            fx_rates=fx_rates,
            reporting_currency=reporting_currency,
        )
        net_rets = [d["net_return"] for d in result["daily_series"]]
        latest_date = result["period_end"]
        latest_strats = [s for s in strat_snaps if s.valuation_date == latest_date]
        avg_nav = (
            sum(d["closing_nav"] for d in result["daily_series"]) / len(result["daily_series"])
            if result["daily_series"]
            else 0.0
        )
        result["foundation_diagnostics"] = {
            "risk_engine": self.risk_adapter.get_risk_snapshot(
                fund_id, latest_date, fund.environment, net_rets
            ).to_dict(),
            "portfolio_optimizer": self.optimizer_adapter.get_allocation_drift(
                fund_id, latest_date, fund.environment, latest_strats
            ).to_dict(),
            "transaction_cost_analysis": self.tca_adapter.get_tca_summary(
                fund_id,
                result["period_start"],
                latest_date,
                fund.environment,
                result["reporting_currency"],
                strat_snaps,
                avg_nav,
            ).to_dict(),
        }
        return result

    def export_report(self, actor: Actor, version_id: str, fmt: str) -> Tuple[str, str]:
        """Return (content_type, serialized_body) for a permitted report version."""
        report = self.reports.get_report(actor, version_id)
        self.rbac.authorize_report_read(actor, report, Action.EXPORT_REPORT)
        fmt_lower = fmt.strip().lower()
        if fmt_lower == "json":
            return "application/json; charset=utf-8", export_report_json(report)
        if fmt_lower in ("md", "markdown"):
            return "text/markdown; charset=utf-8", export_report_markdown(report)
        if fmt_lower == "csv":
            return "text/csv; charset=utf-8", export_report_csv(report)
        if fmt_lower == "html":
            return "text/html; charset=utf-8", export_report_html(report)
        raise ValidationError(
            f"Unsupported export format: {fmt!r} (supported: json, markdown, csv, html)",
            details={"format": fmt},
        )

    def list_audit_events(self, actor: Actor, *, limit: int = 100) -> List[AuditEvent]:
        self.rbac.authorize(
            actor,
            Action.READ_AUDIT_LOG,
            resource_type="audit_log",
            resource_id="global",
        )
        return self.repo.list_audit_events(limit=limit)

    def load_fixture_bundle(self, actor: Actor, bundle: Dict[str, Any]) -> Dict[str, int]:
        """Load a canonical synthetic fixture JSON bundle through validated ingestion paths."""
        fund = FundDefinition.from_dict(bundle["fund"])
        self.register_fund(actor, fund)

        fx_records = [FxRateRecord.from_dict(r) for r in bundle.get("fx_rates", [])]
        if fx_records:
            self.ingest_fx_rates(actor, fx_records)

        bm_records = [BenchmarkPoint.from_dict(b) for b in bundle.get("benchmark_points", [])]
        if bm_records:
            self.ingest_benchmark(actor, bm_records)

        cf_by_snap: Dict[str, List[CashFlowRecord]] = {}
        for raw_cf in bundle.get("cash_flows", []):
            cf = CashFlowRecord.from_dict(raw_cf)
            cf_by_snap.setdefault(cf.snapshot_id, []).append(cf)

        st_by_snap: Dict[str, List[StrategySnapshot]] = {}
        for raw_st in bundle.get("strategy_snapshots", []):
            st = StrategySnapshot.from_dict(raw_st)
            st_by_snap.setdefault(st.snapshot_id, []).append(st)

        snap_count = 0
        for raw_snap in bundle.get("nav_snapshots", []):
            snap = NavSnapshot.from_dict(raw_snap)
            self.ingest_nav_snapshot(
                actor,
                snap,
                cash_flows=cf_by_snap.get(snap.snapshot_id, ()),
                strategy_snapshots=st_by_snap.get(snap.snapshot_id, ()),
            )
            snap_count += 1

        return {
            "nav_snapshots": snap_count,
            "fx_rates": len(fx_records),
            "benchmark_points": len(bm_records),
            "strategy_snapshots": len(bundle.get("strategy_snapshots", [])),
        }
