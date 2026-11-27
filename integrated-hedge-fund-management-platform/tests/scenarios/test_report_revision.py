"""Scenario 6: Report revisions are versioned, diff-tracked, anchored to frozen snapshots, and require dual-control approval."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from hf_platform.domain import EventType, ReportStatus, ReportType
from hf_platform.errors import AuthorizationError, DomainValidationError, RoleSeparationError


def test_scenario_report_revision_versioning_diff_and_supersession(day1_closed_platform) -> None:
    platform, snap1_id = day1_closed_platform
    d1 = date(2026, 11, 25)

    # Publish v1 NAV Summary anchored to frozen snapshot v1
    v1_draft = platform.reporting.generate_report(
        snapshot_id=snap1_id,
        report_type=ReportType.NAV_SUMMARY,
        actor_id="usr_acct_01",
    )
    v1_pub = platform.reporting.approve_and_publish(report_id=v1_draft.report_id, actor_id="usr_ctrl_01")
    assert v1_pub.version == 1
    assert v1_pub.status == ReportStatus.PUBLISHED

    # Restatement: apply approved AAPL mark correction (+0.50 on 10,000 shares = +$5,000 NAV)
    platform.close.apply_manual_mark_override(
        fund_id="FUND_SAAT_MS",
        business_date=d1,
        instrument_id="INST_AAPL",
        price=Decimal("199.00"),
        currency_code="USD",
        proposer_id="usr_ops_01",
        approver_id="usr_risk_01",
        justification="Exchange official closing auction restatement",
    )
    marks = platform.market_data_adapter.fetch_marks(d1)
    fx = platform.market_data_adapter.fetch_fx_rates(d1)
    snap2 = platform.portfolio.build_snapshot(
        fund_id="FUND_SAAT_MS",
        business_date=d1,
        marks=marks,
        fx_rates=fx,
        actor_id="usr_acct_01",
    )
    assert snap2.nav_reconciliation_diff_usd == Decimal("0.00")
    frozen_snap2 = platform.portfolio.freeze_snapshot(snap2.snapshot_id, actor_id="usr_acct_01")

    # Empty revision reason must be rejected
    with pytest.raises(DomainValidationError, match="reason"):
        platform.reporting.revise_report(
            prior_report_id=v1_pub.report_id,
            new_snapshot_id=frozen_snap2.snapshot_id,
            reason="   ",
            actor_id="usr_acct_01",
        )

    # Create v2 revision
    v2_draft = platform.reporting.revise_report(
        prior_report_id=v1_pub.report_id,
        new_snapshot_id=frozen_snap2.snapshot_id,
        reason="Restate AAPL EOD mark from 198.50 to 199.00 per exchange correction",
        actor_id="usr_acct_01",
    )
    assert v2_draft.version == 2
    assert v2_draft.supersedes_report_id == v1_pub.report_id
    assert v2_draft.revision_diff is not None
    assert "nav_usd" in v2_draft.revision_diff.changed_fields
    diff_nav = v2_draft.revision_diff.changed_fields["nav_usd"]
    assert Decimal(diff_nav["after"]) - Decimal(diff_nav["before"]) == Decimal("5000.00")

    # Maker (FUND_ACCOUNTANT) cannot approve revision
    with pytest.raises(AuthorizationError):
        platform.reporting.approve_and_publish(report_id=v2_draft.report_id, actor_id="usr_acct_01")

    # Controller who prepares a revision also cannot self-approve (four-eyes separation)
    v3_ctrl_draft = platform.reporting.revise_report(
        prior_report_id=v1_pub.report_id,
        new_snapshot_id=frozen_snap2.snapshot_id,
        reason="Controller-drafted restatement check",
        actor_id="usr_ctrl_01",
    )
    with pytest.raises(RoleSeparationError):
        platform.reporting.approve_and_publish(report_id=v3_ctrl_draft.report_id, actor_id="usr_ctrl_01")

    # Controller approves accountant-prepared v2 -> v1 becomes SUPERSEDED, v2 becomes PUBLISHED
    v2_pub = platform.reporting.approve_and_publish(report_id=v2_draft.report_id, actor_id="usr_ctrl_01")
    v1_reloaded = platform.store.get_report(v1_pub.report_id)
    assert v2_pub.status == ReportStatus.PUBLISHED
    assert v1_reloaded is not None
    assert v1_reloaded.status == ReportStatus.SUPERSEDED
    assert any(e.event_type == EventType.REPORT_REVISED for e in platform.audit.list_events())
    assert platform.audit.verify_chain()["verified"] is True
