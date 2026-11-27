"""End-to-end multi-day daily close integration tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hf_platform.domain import ApprovalAction, CloseStatus, ReportType
from hf_platform.services.platform import HedgeFundPlatform


def test_two_day_close_lifecycle_with_break_resolution_and_reporting(platform: HedgeFundPlatform) -> None:
    d1 = date(2026, 11, 25)
    d2 = date(2026, 11, 26)

    # Day 1 clean close
    r1 = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    assert r1.status == CloseStatus.PENDING_SIGNOFF
    r1_done = platform.close.sign_off_close(close_run_id=r1.close_run_id, actor_id="usr_ctrl_01")
    assert r1_done.status == CloseStatus.CLOSED

    snap1 = platform.store.get_snapshot(r1_done.snapshot_id)
    assert snap1 is not None
    assert snap1.nav_reconciliation_diff_usd == Decimal("0.00")

    # Day 2 has intentional reconciliation mismatches
    r2_blocked = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d2, actor_id="usr_ops_01")
    assert r2_blocked.status == CloseStatus.BLOCKED
    assert len(r2_blocked.blocking_exception_ids) >= 3

    # Resolve/waive exceptions with strict maker-checker
    for exc_id in r2_blocked.blocking_exception_ids:
        platform.exceptions.propose_resolution(
            exception_id=exc_id,
            resolution_notes="Verified PB allocation delay; ticket #PB-104",
            actor_id="usr_ops_01",
        )
        platform.exceptions.decide_exception(
            exception_id=exc_id,
            action=ApprovalAction.WAIVE,
            decision_notes="Approved temporary waiver for T+1 settlement",
            actor_id="usr_ctrl_01",
        )

    # Re-run Day 2 close -> succeeds and closes
    r2_ready = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d2, actor_id="usr_acct_01")
    assert r2_ready.status == CloseStatus.PENDING_SIGNOFF
    r2_done = platform.close.sign_off_close(close_run_id=r2_ready.close_run_id, actor_id="usr_ctrl_01")
    assert r2_done.status == CloseStatus.CLOSED

    snap2 = platform.store.get_snapshot(r2_done.snapshot_id)
    assert snap2 is not None
    assert snap2.prior_nav_usd == snap1.nav_usd
    assert snap2.nav_reconciliation_diff_usd == Decimal("0.00")

    # Generate all 5 report types from frozen Day 2 snapshot
    for rtype in ReportType:
        rpt = platform.reporting.generate_report(snapshot_id=snap2.snapshot_id, report_type=rtype, actor_id="usr_acct_01")
        pub = platform.reporting.approve_and_publish(report_id=rpt.report_id, actor_id="usr_ctrl_01")
        assert pub.snapshot_id == snap2.snapshot_id
        assert pub.snapshot_content_hash == snap2.content_hash

    assert platform.audit.verify_chain()["verified"] is True
