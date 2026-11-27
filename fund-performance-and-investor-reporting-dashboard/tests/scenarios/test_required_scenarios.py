"""Scenario tests covering all 7 mandatory failure and control cases in plan.md Section 5.

Required scenarios verified:
  1. Cash subscription/redemption
  2. Missing nav
  3. Zero volatility
  4. Currency conversion
  5. Report revision
  6. Paper/live mixing
  7. Unauthorized access
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from fund_reporting.api.service import FundReportingService
from fund_reporting.domain.enums import (
    CashFlowTiming,
    CashFlowType,
    DataStatus,
    EnvironmentMode,
    MetricFlagCode,
    ReportAudience,
    ReportStatus,
    Role,
)
from fund_reporting.domain.errors import (
    AuthorizationError,
    CurrencyConversionError,
    ImmutableReportError,
    MissingNavError,
    MixedEnvironmentError,
)
from fund_reporting.domain.models import (
    CashFlowRecord,
    FeeSchedule,
    FundDefinition,
    FxRateRecord,
    NavSnapshot,
    StrategySnapshot,
)
from fund_reporting.domain.validation import compute_ledger_hash
from fund_reporting.security.rbac import Actor
from fund_reporting.storage.sqlite_repo import SqliteRepository

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "default_config.json"
ALPHA_FIXTURE = ROOT / "data" / "fixtures" / "fund_alpha_synthetic.json"


def _build_service() -> FundReportingService:
    repo = SqliteRepository(":memory:")
    return FundReportingService.from_config_path(repo, CONFIG_PATH)


def _snap(
    fund_id: str,
    dt: str,
    opening: float,
    gross_pnl: float,
    *,
    fees: float = 0.0,
    subs: float = 0.0,
    reds: float = 0.0,
    rev: int = 1,
    env: EnvironmentMode = EnvironmentMode.SIMULATED,
    currency: str = "USD",
) -> NavSnapshot:
    closing = opening + subs - reds + gross_pnl - fees
    s = NavSnapshot(
        snapshot_id=f"SNAP-{fund_id}-{dt}-r{rev}",
        fund_id=fund_id,
        valuation_date=dt,
        as_of_utc=f"{dt}T21:00:00Z",
        availability_utc=f"{dt}T22:00:00Z",
        revision=rev,
        currency=currency,
        environment=env,
        data_status=DataStatus.SYNTHETIC if env != EnvironmentMode.LIVE else DataStatus.UNAUDITED,
        opening_nav=opening,
        gross_pnl=gross_pnl,
        management_fee=fees,
        performance_fee=0.0,
        other_expenses=0.0,
        subscriptions=subs,
        redemptions=reds,
        closing_nav=closing,
        shares_outstanding=max(1.0, closing / 100.0),
        cash_balance=closing * 0.15,
        gross_exposure=closing * 0.85,
        net_exposure=closing * 0.50,
        ledger_reconciled=True,
        ledger_hash="pending",
        provenance_source="SCENARIO_TEST",
        cash_flow_timing=CashFlowTiming.START_OF_DAY,
    )
    return replace(s, ledger_hash=compute_ledger_hash(s))


class TestRequiredPlanScenarios(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _build_service()
        self.admin = Actor("admin_01", Role.ADMIN)
        self.analyst = Actor("analyst_01", Role.ANALYST)
        self.reviewer = Actor("reviewer_01", Role.REVIEWER)
        self.approver = Actor("approver_01", Role.APPROVER)
        self.investor = Actor("investor_01", Role.VIEWER_INVESTOR, entitled_funds={"FUND-SAAT-SIM-01"})

    def test_scenario_01_cash_subscription_redemption(self) -> None:
        """Large subscription (+50%) and redemption (-40%) must not distort Time-Weighted Return."""
        fund = FundDefinition(
            fund_id="FUND-CF-01",
            name="Cash Flow Scenario Fund",
            base_currency="USD",
            environment=EnvironmentMode.SIMULATED,
            data_status=DataStatus.SYNTHETIC,
            inception_date="2026-04-01",
            benchmark_id="BM-NONE",
            fee_schedule=FeeSchedule(0.0, 0.0, 0.0, False, 252),
            created_at_utc="2026-04-01T00:00:00Z",
        )
        self.service.register_fund(self.analyst, fund)

        # Day 1: 1,000,000 -> +2% (+20,000) -> 1,020,000
        s1 = _snap("FUND-CF-01", "2026-04-01", 1_000_000.0, 20_000.0)
        # Day 2: +500,000 subscription (eff=1,520,000) -> +2% (+30,400) -> 1,550,400
        s2 = _snap("FUND-CF-01", "2026-04-02", 1_020_000.0, 30_400.0, subs=500_000.0)
        # Day 3: -600,000 redemption (eff=950,400) -> +2% (+19,008) -> 969,408
        s3 = _snap("FUND-CF-01", "2026-04-03", 1_550_400.0, 19_008.0, reds=600_000.0)

        for s in (s1, s2, s3):
            self.service.ingest_nav_snapshot(self.analyst, s)

        analytics = self.service.get_dashboard_analytics(self.analyst, "FUND-CF-01")
        expected_cum = (1.02 ** 3) - 1.0
        self.assertAlmostEqual(analytics["metrics"]["net"]["cumulative_return"], expected_cum, places=10)
        self.assertAlmostEqual(analytics["reconciliation_summary"]["nav_bridge_residual"], 0.0, places=6)
        self.assertEqual(analytics["reconciliation_summary"]["net_cash_flows"], -100_000.0)

    def test_scenario_02_missing_nav(self) -> None:
        """Missing NAV date gap must fail closed with MissingNavError (never silently forward-filled)."""
        fund = FundDefinition(
            fund_id="FUND-GAP-01",
            name="Missing NAV Scenario Fund",
            base_currency="USD",
            environment=EnvironmentMode.SIMULATED,
            data_status=DataStatus.SYNTHETIC,
            inception_date="2026-04-01",
            benchmark_id="BM-NONE",
            fee_schedule=FeeSchedule(),
            created_at_utc="2026-04-01T00:00:00Z",
        )
        self.service.register_fund(self.analyst, fund)
        self.service.ingest_nav_snapshot(self.analyst, _snap("FUND-GAP-01", "2026-04-01", 1_000_000.0, 5_000.0))
        # Inject 10-day missing NAV gap
        self.service.ingest_nav_snapshot(self.analyst, _snap("FUND-GAP-01", "2026-04-15", 1_005_000.0, 4_000.0))

        with self.assertRaises(MissingNavError) as ctx:
            self.service.get_dashboard_analytics(self.analyst, "FUND-GAP-01")
        self.assertEqual(ctx.exception.code, "MISSING_NAV")

    def test_scenario_03_zero_volatility(self) -> None:
        """Zero volatility series must omit Sharpe/Sortino/Calmar with ZERO_VOLATILITY flags."""
        fund = FundDefinition(
            fund_id="FUND-FLAT-01",
            name="Zero Volatility Scenario Fund",
            base_currency="USD",
            environment=EnvironmentMode.SIMULATED,
            data_status=DataStatus.SYNTHETIC,
            inception_date="2026-04-01",
            benchmark_id="BM-NONE",
            fee_schedule=FeeSchedule(0.0, 0.0, 0.0, False, 252),
            created_at_utc="2026-04-01T00:00:00Z",
        )
        self.service.register_fund(self.analyst, fund)
        nav = 1_000_000.0
        for d in ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-06", "2026-04-07"]:
            pnl = nav * 0.001
            snap = _snap("FUND-FLAT-01", d, nav, pnl)
            self.service.ingest_nav_snapshot(self.analyst, snap)
            nav = snap.closing_nav

        analytics = self.service.get_dashboard_analytics(self.analyst, "FUND-FLAT-01")
        net = analytics["metrics"]["net"]
        self.assertEqual(net["annualized_volatility"], 0.0)
        self.assertIsNone(net["sharpe_ratio"])
        self.assertIsNone(net["sortino_ratio"])
        self.assertIsNone(net["calmar_ratio"])
        self.assertTrue(any(f["code"] == MetricFlagCode.ZERO_VOLATILITY.value for f in analytics["flags"]))

    def test_scenario_04_currency_conversion(self) -> None:
        """Point-in-time FX conversion works with direct/inverse rates and fails closed on missing rate."""
        fund = FundDefinition(
            fund_id="FUND-FX-01",
            name="Currency Conversion Scenario Fund",
            base_currency="USD",
            environment=EnvironmentMode.SIMULATED,
            data_status=DataStatus.SYNTHETIC,
            inception_date="2026-04-01",
            benchmark_id="BM-NONE",
            fee_schedule=FeeSchedule(0.0, 0.0, 0.0, False, 252),
            created_at_utc="2026-04-01T00:00:00Z",
        )
        self.service.register_fund(self.analyst, fund)
        self.service.ingest_nav_snapshot(self.analyst, _snap("FUND-FX-01", "2026-04-01", 1_000.0, 100.0))
        self.service.ingest_nav_snapshot(self.analyst, _snap("FUND-FX-01", "2026-04-02", 1_100.0, 0.0))

        # Register FX for prior day + day 1 + day 2 (USD->EUR rises 0.80 -> 0.88 = +10%)
        self.service.ingest_fx_rates(
            self.analyst,
            [
                FxRateRecord("FX0", "2026-03-31", "2026-03-31T21:00:00Z", "USD", "EUR", 0.80, "TEST"),
                FxRateRecord("FX1", "2026-04-01", "2026-04-01T21:00:00Z", "USD", "EUR", 0.80, "TEST"),
                FxRateRecord("FX2", "2026-04-02", "2026-04-02T21:00:00Z", "USD", "EUR", 0.88, "TEST"),
            ],
        )
        eur = self.service.get_dashboard_analytics(self.analyst, "FUND-FX-01", reporting_currency="EUR")
        # Local +10%, FX +10% -> (1.10 * 1.10) - 1 = +21%
        self.assertAlmostEqual(eur["metrics"]["net"]["cumulative_return"], 0.21, places=10)
        self.assertAlmostEqual(eur["reconciliation_summary"]["closing_nav"], 1100.0 * 0.88, places=4)

        # Missing JPY rate must fail closed
        with self.assertRaises(CurrencyConversionError) as ctx:
            self.service.get_dashboard_analytics(self.analyst, "FUND-FX-01", reporting_currency="JPY")
        self.assertEqual(ctx.exception.code, "MISSING_FX_RATE")

    def test_scenario_05_report_revision(self) -> None:
        """Published reports are immutable; revisions create new version IDs and supersede v1 without mutating v1."""
        bundle = json.loads(ALPHA_FIXTURE.read_text(encoding="utf-8"))
        self.service.load_fixture_bundle(self.admin, bundle)

        # Create, submit, review, publish v1
        v1 = self.service.reports.create_draft_report(
            self.analyst, fund_id="FUND-SAAT-SIM-01", report_series_id="RPT-REV-SCENARIO", audience=ReportAudience.INVESTOR
        )
        self.service.reports.submit_report(self.analyst, v1.version_id)
        self.service.reports.review_report(self.reviewer, v1.version_id, approve=True)
        v1_pub = self.service.reports.approve_and_publish_report(self.approver, v1.version_id)
        v1_sha = v1_pub.content_sha256

        # In-place mutation of published report must be blocked
        with self.assertRaises(ImmutableReportError):
            self.service.reports.attempt_in_place_edit(v1_pub.version_id)
        with self.assertRaises(ImmutableReportError):
            self.service.repo.update_report(replace(v1_pub, content_sha256="tampered"))

        # Ingest a revised snapshot for the final date (restated fee adjustment)
        snaps = self.service.get_snapshots(self.analyst, "FUND-SAAT-SIM-01")
        last = snaps[-1]
        restated_mgmt = round(last.management_fee + 250.0, 2)
        restated_close = round(last.closing_nav - 250.0, 2)
        revised_snap = replace(
            last,
            snapshot_id=f"{last.snapshot_id}-restated",
            revision=2,
            availability_utc="2026-05-10T10:00:00Z",
            management_fee=restated_mgmt,
            closing_nav=restated_close,
        )
        revised_snap = replace(revised_snap, ledger_hash=compute_ledger_hash(revised_snap))
        self.service.ingest_nav_snapshot(self.analyst, revised_snap)

        # Create revision v2 and publish through dual control
        v2 = self.service.reports.create_revision(
            self.analyst,
            supersedes_version_id=v1_pub.version_id,
            revision_reason="Administrator fee accrual restatement on final valuation date",
        )
        self.assertEqual(v2.revision_number, 2)
        self.assertEqual(v2.supersedes_version_id, v1_pub.version_id)
        self.assertNotEqual(v2.version_id, v1_pub.version_id)
        self.assertNotEqual(v2.content_sha256, v1_sha)

        self.service.reports.submit_report(self.analyst, v2.version_id)
        self.service.reports.review_report(self.reviewer, v2.version_id, approve=True)
        v2_pub = self.service.reports.approve_and_publish_report(self.approver, v2.version_id)
        self.assertEqual(v2_pub.status, ReportStatus.PUBLISHED)

        # Verify v1 is SUPERSEDED with identical content SHA-256 preserved
        v1_after = self.service.reports.get_report(self.investor, v1_pub.version_id)
        self.assertEqual(v1_after.status, ReportStatus.SUPERSEDED)
        self.assertEqual(v1_after.content_sha256, v1_sha)

    def test_scenario_06_paper_live_mixing(self) -> None:
        """Mixing PAPER/LIVE/SIMULATED snapshots or strategies is strictly rejected."""
        fund = FundDefinition(
            fund_id="FUND-PAPER-01",
            name="Paper Sandbox Fund",
            base_currency="USD",
            environment=EnvironmentMode.PAPER,
            data_status=DataStatus.RECONCILED_SANDBOX,
            inception_date="2026-04-01",
            benchmark_id="BM-NONE",
            fee_schedule=FeeSchedule(),
            created_at_utc="2026-04-01T00:00:00Z",
        )
        self.service.register_fund(self.analyst, fund)
        valid_paper = _snap("FUND-PAPER-01", "2026-04-01", 1_000_000.0, 5_000.0, env=EnvironmentMode.PAPER)
        self.service.ingest_nav_snapshot(self.analyst, valid_paper)

        # Attempt to ingest LIVE snapshot into PAPER fund
        live_snap = _snap("FUND-PAPER-01", "2026-04-02", 1_005_000.0, 2_000.0, env=EnvironmentMode.LIVE)
        with self.assertRaises(MixedEnvironmentError) as ctx:
            self.service.ingest_nav_snapshot(self.analyst, live_snap)
        self.assertEqual(ctx.exception.code, "MIXED_ENVIRONMENT_PROHIBITED")

        # Attempt to attach LIVE strategy snapshot to PAPER NAV snapshot
        paper_day2 = _snap("FUND-PAPER-01", "2026-04-02", 1_005_000.0, 2_000.0, env=EnvironmentMode.PAPER)
        live_strat = StrategySnapshot(
            strategy_snapshot_id="SS-MIX",
            snapshot_id=paper_day2.snapshot_id,
            fund_id="FUND-PAPER-01",
            valuation_date="2026-04-02",
            strategy_id="S1",
            strategy_name="Live Strat",
            asset_class="Equities",
            currency="USD",
            environment=EnvironmentMode.LIVE,
            weight=1.0,
            gross_pnl=2_000.0,
            transaction_costs=10.0,
            long_exposure=500_000.0,
            short_exposure=0.0,
        )
        with self.assertRaises(MixedEnvironmentError):
            self.service.ingest_nav_snapshot(self.analyst, paper_day2, strategy_snapshots=[live_strat])

    def test_scenario_07_unauthorized_access(self) -> None:
        """RBAC blocks unauthorized roles, unpublished investor reads, self-approval, and logs DENY events."""
        bundle = json.loads(ALPHA_FIXTURE.read_text(encoding="utf-8"))
        self.service.load_fixture_bundle(self.admin, bundle)

        # Investor blocked from internal analytics
        with self.assertRaises(AuthorizationError):
            self.service.get_dashboard_analytics(self.investor, "FUND-SAAT-SIM-01")

        # Investor blocked from creating a report
        with self.assertRaises(AuthorizationError):
            self.service.reports.create_draft_report(
                self.investor, fund_id="FUND-SAAT-SIM-01", report_series_id="RPT-UNAUTH"
            )

        # Analyst creates INTERNAL and INVESTOR drafts
        draft_internal = self.service.reports.create_draft_report(
            self.analyst, fund_id="FUND-SAAT-SIM-01", report_series_id="RPT-INT-01", audience=ReportAudience.INTERNAL
        )
        draft_inv = self.service.reports.create_draft_report(
            self.analyst, fund_id="FUND-SAAT-SIM-01", report_series_id="RPT-INV-01", audience=ReportAudience.INVESTOR
        )

        # Investor cannot read DRAFT investor report
        with self.assertRaises(AuthorizationError):
            self.service.reports.get_report(self.investor, draft_inv.version_id)

        # Publish INTERNAL report -> Investor STILL cannot read INTERNAL audience report
        self.service.reports.submit_report(self.analyst, draft_internal.version_id)
        self.service.reports.review_report(self.reviewer, draft_internal.version_id, approve=True)
        self.service.reports.approve_and_publish_report(self.approver, draft_internal.version_id)
        with self.assertRaises(AuthorizationError):
            self.service.reports.get_report(self.investor, draft_internal.version_id)

        # Separation of duties: Admin who authors a report cannot review or approve their own report
        self_authored = self.service.reports.create_draft_report(
            self.admin, fund_id="FUND-SAAT-SIM-01", report_series_id="RPT-SOD-01", audience=ReportAudience.INVESTOR
        )
        self.service.reports.submit_report(self.admin, self_authored.version_id)
        with self.assertRaises(AuthorizationError) as ctx:
            self.service.reports.review_report(self.admin, self_authored.version_id, approve=True)
        self.assertEqual(ctx.exception.code, "SEPARATION_OF_DUTIES_VIOLATION")

        # Investor blocked from reading audit log
        with self.assertRaises(AuthorizationError):
            self.service.list_audit_events(self.investor)

        # Reviewer can read audit log and verify DENY events were recorded
        events = self.service.list_audit_events(self.reviewer, limit=200)
        denies = [e for e in events if e.outcome == "DENY"]
        self.assertGreaterEqual(len(denies), 5)


if __name__ == "__main__":
    unittest.main()
