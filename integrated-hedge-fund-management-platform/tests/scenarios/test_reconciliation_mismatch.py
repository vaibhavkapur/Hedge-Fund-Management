"""Scenario 5: Reconciliation mismatches (position qty, cash, fill mismatch, unbooked PB fill) are classified and block close."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hf_platform.domain import BreakType, CloseStatus


def test_scenario_reconciliation_mismatch_classifies_all_breaks(day1_closed_platform) -> None:
    platform, _ = day1_closed_platform
    d2 = date(2026, 11, 26)

    run = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d2, actor_id="usr_ops_01")
    assert run.status == CloseStatus.BLOCKED
    assert run.reconciliation_id is not None

    recon = platform.store.get_reconciliation(run.reconciliation_id)
    assert recon is not None
    assert recon.is_clean is False
    assert recon.total_break_impact_usd > Decimal("0.00")

    break_types = {b.break_type for b in recon.breaks}
    assert BreakType.POSITION_QUANTITY in break_types
    assert BreakType.CASH_BALANCE in break_types
    assert BreakType.FILL_MISMATCH in break_types
    assert BreakType.MISSING_INTERNAL_FILL in break_types

    # Every material break creates a tracked operational exception blocking snapshot freeze
    assert len(run.blocking_exception_ids) >= 4
    snap = platform.store.get_snapshot(run.snapshot_id)
    assert snap is not None
    assert snap.is_frozen is False
    assert platform.audit.verify_chain()["verified"] is True
