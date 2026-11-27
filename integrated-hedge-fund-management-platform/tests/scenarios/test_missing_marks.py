"""Scenario 2: Missing and stale marks block daily close until dual-control approved overrides are applied."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from hf_platform.domain import BreakType, CloseStatus, MarketMark
from hf_platform.services.platform import HedgeFundPlatform


def test_scenario_missing_and_stale_marks_block_then_recover(
    platform: HedgeFundPlatform, project_root: Path
) -> None:
    fixture = json.loads(
        (project_root / "data" / "fixtures" / "scenario_missing_marks.json").read_text(encoding="utf-8")
    )
    d1 = date(2026, 11, 25)

    platform.fault_state.omitted_mark_instruments.update(fixture["omitted_instruments"])
    for sm in fixture["stale_marks"]:
        mk = MarketMark.model_validate(sm)
        platform.fault_state.override_marks[mk.instrument_id] = mk

    # Attempt 1: Close must block at market data validation with 2 CRITICAL exceptions
    blocked = platform.close.run_daily_close(
        fund_id="FUND_SAAT_MS",
        business_date=d1,
        actor_id="usr_acct_01",
    )
    assert blocked.status == CloseStatus.BLOCKED
    assert blocked.snapshot_id is None
    assert len(blocked.blocking_exception_ids) == 2

    excs = platform.exceptions.list_exceptions(fund_id="FUND_SAAT_MS", business_date=d1)
    cats = {e.category for e in excs}
    assert BreakType.MISSING_MARK in cats
    assert BreakType.STALE_MARK in cats

    # Apply dual-control approved manual mark overrides for NVDA and SAP
    platform.close.apply_manual_mark_override(
        fund_id="FUND_SAAT_MS",
        business_date=d1,
        instrument_id="INST_NVDA",
        price=Decimal("138.00"),
        currency_code="USD",
        proposer_id="usr_ops_01",
        approver_id="usr_risk_01",
        justification="Verified NASDAQ close via secondary market data terminal",
    )
    platform.close.apply_manual_mark_override(
        fund_id="FUND_SAAT_MS",
        business_date=d1,
        instrument_id="INST_SAP",
        price=Decimal("204.00"),
        currency_code="EUR",
        proposer_id="usr_ops_01",
        approver_id="usr_ctrl_01",
        justification="Verified XETRA close print",
    )

    # Attempt 2: Re-run close -> succeeds, freezes snapshot, and signs off
    ready = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    assert ready.status == CloseStatus.PENDING_SIGNOFF
    closed = platform.close.sign_off_close(close_run_id=ready.close_run_id, actor_id="usr_ctrl_01")
    assert closed.status == CloseStatus.CLOSED

    snap = platform.store.get_snapshot(closed.snapshot_id)
    assert snap is not None
    assert snap.nav_reconciliation_diff_usd == Decimal("0.00")
    assert platform.audit.verify_chain()["verified"] is True
