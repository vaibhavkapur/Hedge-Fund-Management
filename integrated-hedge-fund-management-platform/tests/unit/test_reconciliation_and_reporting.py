"""Unit tests for reconciliation tolerances and frozen-snapshot reporting gates."""

from __future__ import annotations

from datetime import date

import pytest

from hf_platform.domain import ReportType
from hf_platform.errors import CloseGateError
from hf_platform.services.platform import HedgeFundPlatform


def test_report_generation_rejects_unfrozen_snapshot(platform: HedgeFundPlatform) -> None:
    d1 = date(2026, 11, 25)
    platform.ledger.ensure_opening_capital("FUND_SAAT_MS", actor_id="usr_acct_01")
    for fill in platform.execution_adapter.fetch_fills("FUND_SAAT_MS", d1):
        platform.store.save_fill(fill)
        platform.ledger.post_fill(fill, d1, actor_id="usr_acct_01")
    marks = platform.market_data_adapter.fetch_marks(d1)
    fx = platform.market_data_adapter.fetch_fx_rates(d1)
    draft_snap = platform.portfolio.build_snapshot(
        fund_id="FUND_SAAT_MS",
        business_date=d1,
        marks=marks,
        fx_rates=fx,
        actor_id="usr_acct_01",
    )
    assert draft_snap.is_frozen is False
    with pytest.raises(CloseGateError, match="FROZEN"):
        platform.reporting.generate_report(
            snapshot_id=draft_snap.snapshot_id,
            report_type=ReportType.NAV_SUMMARY,
            actor_id="usr_acct_01",
        )


def test_report_delivery_requires_published_status(day1_closed_platform) -> None:
    platform, snap_id = day1_closed_platform
    pending_rpt = platform.reporting.generate_report(
        snapshot_id=snap_id,
        report_type=ReportType.ADMINISTRATOR_EXTRACT,
        actor_id="usr_acct_01",
    )
    with pytest.raises(CloseGateError, match="PUBLISHED"):
        platform.reporting.deliver_to_administrator(report_id=pending_rpt.report_id, actor_id="usr_ops_01")

    pub_rpt = platform.reporting.approve_and_publish(report_id=pending_rpt.report_id, actor_id="usr_ctrl_01")
    receipt = platform.reporting.deliver_to_administrator(report_id=pub_rpt.report_id, actor_id="usr_ops_01")
    assert receipt["status"] == "DELIVERED"
    assert receipt["snapshot_id"] == snap_id
