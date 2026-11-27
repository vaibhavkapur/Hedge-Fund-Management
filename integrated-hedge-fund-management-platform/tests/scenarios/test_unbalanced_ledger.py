"""Scenario 1: Unbalanced ledger entry is rejected, not persisted, and recorded in audit trail."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from hf_platform.domain import EventType
from hf_platform.errors import LedgerBalanceError
from hf_platform.services.platform import HedgeFundPlatform


def test_scenario_unbalanced_ledger_rejected_and_audited(
    platform: HedgeFundPlatform, project_root: Path
) -> None:
    fixture = json.loads(
        (project_root / "data" / "fixtures" / "scenario_unbalanced_ledger.json").read_text(encoding="utf-8")
    )
    entry_data = fixture["entry"]
    bdate = date.fromisoformat(fixture["business_date"])

    platform.ledger.ensure_opening_capital(fixture["fund_id"], actor_id="usr_acct_01")
    before_count = len(platform.store.list_journal_entries(fixture["fund_id"]))

    with pytest.raises(LedgerBalanceError) as exc_info:
        platform.ledger.post_manual_entry(
            entry_id=entry_data["entry_id"],
            fund_id=fixture["fund_id"],
            business_date=bdate,
            description=entry_data["description"],
            source_ref=entry_data["source_ref"],
            lines=entry_data["lines"],
            actor_id="usr_acct_01",
        )

    assert exc_info.value.code == "LEDGER_UNBALANCED"
    assert "USD" in exc_info.value.details["imbalances"]
    assert exc_info.value.details["imbalances"]["USD"]["difference"] == "500.00"

    # Verify entry was NOT persisted and trial balance remains intact
    after_count = len(platform.store.list_journal_entries(fixture["fund_id"]))
    assert after_count == before_count
    tb = platform.ledger.compute_trial_balance(fixture["fund_id"], bdate)
    assert tb.is_balanced is True

    # Verify rejection audited and event emitted
    assert any(
        a.action == "ledger.post_manual" and a.outcome == "FAILED" and a.entity_id == entry_data["entry_id"]
        for a in platform.audit.list_entries()
    )
    assert any(e.event_type == EventType.JOURNAL_REJECTED for e in platform.audit.list_events())
    assert platform.audit.verify_chain()["verified"] is True
