"""Scenario 7: External provider outage halts close safely in DEGRADED_OUTAGE state and recovers cleanly."""

from __future__ import annotations

from datetime import date

from hf_platform.domain import ApprovalAction, BreakType, CloseStatus, EventType
from hf_platform.services.platform import HedgeFundPlatform


def test_scenario_service_outage_safe_fallback_and_recovery(platform: HedgeFundPlatform) -> None:
    d1 = date(2026, 11, 25)

    # Inject Market Data outage
    platform.fault_state.market_data_outage = True
    health_down = platform.health_and_metrics()
    assert health_down["status"] == "DEGRADED"

    run_md_down = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    assert run_md_down.status == CloseStatus.DEGRADED_OUTAGE
    assert run_md_down.snapshot_id is None
    assert len(run_md_down.blocking_exception_ids) == 1

    # Restore Market Data, inject Prime Broker feed outage
    platform.fault_state.market_data_outage = False
    platform.fault_state.broker_feed_outage = True
    run_pb_down = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    assert run_pb_down.status == CloseStatus.DEGRADED_OUTAGE
    assert run_pb_down.snapshot_id is None

    # Confirm outage events & exceptions recorded
    outage_excs = [
        e for e in platform.exceptions.list_exceptions("FUND_SAAT_MS", d1) if e.category == BreakType.SERVICE_OUTAGE
    ]
    assert len(outage_excs) == 2
    assert any(e.event_type == EventType.SERVICE_OUTAGE_DETECTED for e in platform.audit.list_events())

    # Restore all external services and close out outage incident tickets with maker-checker
    platform.fault_state.broker_feed_outage = False
    assert platform.health_and_metrics()["status"] == "HEALTHY"
    for exc in outage_excs:
        platform.exceptions.propose_resolution(
            exception_id=exc.exception_id,
            resolution_notes="Confirmed external vendor feed restored and verified",
            actor_id="usr_ops_01",
        )
        platform.exceptions.decide_exception(
            exception_id=exc.exception_id,
            action=ApprovalAction.APPROVE,
            decision_notes="Verified health checks green",
            actor_id="usr_ctrl_01",
        )

    # Retry close -> clean idempotent recovery & signoff
    recovered = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    assert recovered.status == CloseStatus.PENDING_SIGNOFF
    closed = platform.close.sign_off_close(close_run_id=recovered.close_run_id, actor_id="usr_ctrl_01")
    assert closed.status == CloseStatus.CLOSED
    assert platform.audit.verify_chain()["verified"] is True
