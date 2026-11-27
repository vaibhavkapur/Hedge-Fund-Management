"""Scenario 4: Late fills arriving after the 21:00 UTC cutoff are quarantined, deferred to T+1, and block close until reviewed."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from hf_platform.domain import ApprovalAction, BreakType, CloseStatus, EventType, TradeFill
from hf_platform.services.platform import HedgeFundPlatform


def test_scenario_late_fills_quarantined_and_controlled(
    platform: HedgeFundPlatform, project_root: Path
) -> None:
    fixture = json.loads(
        (project_root / "data" / "fixtures" / "scenario_late_fills.json").read_text(encoding="utf-8")
    )
    d1 = date.fromisoformat(fixture["business_date"])
    for raw_fill in fixture["late_fills"]:
        platform.fault_state.extra_fills.append(TradeFill.model_validate({**raw_fill, "business_date": d1}))

    # Attempt 1: Late fill is quarantined (not posted into T+0 ledger) and opens HIGH exception blocking close
    run1 = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    assert run1.status == CloseStatus.BLOCKED
    assert run1.late_fill_ids == ["FILL_20261125_LATE_001"]

    # Confirm the late fill was NOT posted into T+0 positions/ledger
    stored_fills = {f.fill_id: f for f in platform.store.list_fills("FUND_SAAT_MS", business_date=d1)}
    late = stored_fills["FILL_20261125_LATE_001"]
    assert late.is_late is True
    assert late.deferred_to_date == date(2026, 11, 26)
    journal_refs = {je.source_ref for je in platform.store.list_journal_entries("FUND_SAAT_MS", up_to_date=d1)}
    assert "FILL_20261125_LATE_001" not in journal_refs

    # Confirm LATE_FILL exception & event emitted
    excs = [e for e in platform.exceptions.list_exceptions("FUND_SAAT_MS", d1) if e.category == BreakType.LATE_FILL]
    assert len(excs) == 1
    assert any(e.event_type == EventType.LATE_FILL_DETECTED for e in platform.audit.list_events())

    # Maker-checker confirms T+1 deferral, allowing T+0 close to proceed cleanly
    platform.exceptions.propose_resolution(
        exception_id=excs[0].exception_id,
        resolution_notes="Confirmed late broker drop; booked for T+1 trade-date adjustment per cutoff policy",
        actor_id="usr_ops_01",
    )
    platform.exceptions.decide_exception(
        exception_id=excs[0].exception_id,
        action=ApprovalAction.APPROVE,
        decision_notes="Approved deferral to 2026-11-26",
        actor_id="usr_ctrl_01",
    )

    run2 = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    assert run2.status == CloseStatus.PENDING_SIGNOFF
    closed = platform.close.sign_off_close(close_run_id=run2.close_run_id, actor_id="usr_ctrl_01")
    assert closed.status == CloseStatus.CLOSED
    assert platform.audit.verify_chain()["verified"] is True
