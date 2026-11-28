#!/usr/bin/env python3
"""Reproducible end-to-end demo for the Investor Subscription and Redemption Platform.

Runs the synthetic baseline fixture, compares against expected reference outputs,
demonstrates versioned NAV restatement with maker-checker enforcement, and saves
machine-readable outputs to reports/demo_baseline_output.json.
"""

from __future__ import annotations

import json
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from isrp.adapters import (
    export_fee_accounting_feed,
    export_regulatory_flow_report,
    ingest_approved_nav_payload,
)
from isrp.config import load_config
from isrp.repository import SQLiteRepository
from isrp.service import PlatformService


def execute_baseline_scenario(config_path: Path, fixture_path: Path) -> Dict[str, Any]:
    config = load_config(config_path)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    service = PlatformService(config, SQLiteRepository(":memory:"))

    t0 = datetime(2026, 3, 1, 8, 0, 0, tzinfo=timezone.utc)
    for inv in fixture["investors"]:
        service.register_investor(**inv, now=t0)
        service.run_onboarding(inv["investor_id"], now=t0)
        service.activate_investor(inv["investor_id"], now=t0)

    for win in fixture["dealing_windows"]:
        service.register_dealing_window(**win, now=t0)

    for nav in fixture["nav_records"]:
        ingest_approved_nav_payload(service, nav)

    cash_by_req = {c["request_id"]: c for c in fixture["cash_confirmations"]}
    for r in fixture["requests"]:
        service.submit_request(
            request_id=r["request_id"],
            idempotency_key=r["idempotency_key"],
            investor_id=r["investor_id"],
            fund_id=r["fund_id"],
            share_class=r["share_class"],
            request_type=r["request_type"],
            dealing_date=r["dealing_date"],
            currency=r["currency"],
            gross_amount=r["gross_amount"],
            requested_shares=r["requested_shares"],
            submitted_at=r["submitted_at"],
            maker_id=r["maker_id"],
        )
        t_rev = (
            datetime(2026, 4, 1, 11, 0, 0, tzinfo=timezone.utc)
            if r["dealing_date"] == "2026-03-31"
            else datetime(2026, 5, 1, 11, 0, 0, tzinfo=timezone.utc)
        )
        service.review_request(
            request_id=r["request_id"],
            checker_id=r["checker_id"],
            decision="APPROVED",
            now=t_rev,
        )
        if r["request_type"] == "SUBSCRIPTION":
            service.record_cash_confirmation(**cash_by_req[r["request_id"]])
            service.price_request(request_id=r["request_id"], now=t_rev)
            service.settle_request(request_id=r["request_id"], now=t_rev)
        else:
            service.price_request(request_id=r["request_id"], now=t_rev)
            service.record_cash_confirmation(**cash_by_req[r["request_id"]])
            service.settle_request(request_id=r["request_id"], now=t_rev)

    baseline_snapshot = service.export_state_snapshot()
    exp = fixture["expected_summary"]
    recon = baseline_snapshot["reconciliation"]
    baseline_matches = (
        recon["reconciled"] is True
        and recon["total_register_shares"] == exp["total_register_shares"]
        and recon["total_inbound_cash_journal"] == exp["total_inbound_cash"]
        and recon["total_outbound_cash_journal"] == exp["total_outbound_cash"]
        and recon["total_fees_collected"] == exp["total_fees_collected"]
    )

    fee_feed = export_fee_accounting_feed(service)
    reg_report = export_regulatory_flow_report(service)

    # Demonstrate versioned NAV restatement on 2026-03-31 (125.000000 -> 123.750000)
    restatement = service.restate_nav(
        new_nav_id="NAV-ALPHA-A-20260331-V2",
        fund_id="FUND-ALPHA",
        share_class="CLASS-A",
        dealing_date="2026-03-31",
        restated_nav_per_share="123.750000",
        reason="Demo accrued expense adjustment",
        maker_id="nav_controller_01",
        checker_id="nav_director_02",
        now=datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc),
    )
    post_restatement_snapshot = service.export_state_snapshot()

    return {
        "report_schema_version": "1.0.0",
        "fixture_id": fixture["fixture_id"],
        "synthetic_data_notice": fixture["synthetic_data_notice"],
        "runtime": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "sqlite_version": sqlite3.sqlite_version,
            "config_hash": config.config_hash,
            "deterministic_seed": config.deterministic_seed,
        },
        "baseline_verification": {
            "matches_expected_summary": baseline_matches,
            "expected_summary": exp,
            "actual_reconciliation": recon,
            "baseline_logical_state_hash": baseline_snapshot["logical_state_hash"],
            "share_register": baseline_snapshot["share_register"],
        },
        "adapters": {
            "fee_accounting_feed_items": fee_feed["item_count"],
            "regulatory_report_reconciled": reg_report["reconciled"],
        },
        "nav_restatement_demo": {
            "restatement_result": restatement,
            "post_restatement_register": post_restatement_snapshot["share_register"],
            "post_restatement_reconciliation": post_restatement_snapshot["reconciliation"],
            "post_restatement_logical_state_hash": post_restatement_snapshot["logical_state_hash"],
            "audit_chain_valid": post_restatement_snapshot["audit_chain_valid"],
            "audit_event_count": post_restatement_snapshot["audit_event_count"],
        },
        "diagnostic_counters": service.diagnostics.snapshot_counters(),
    }


def main() -> int:
    config_path = PROJECT_ROOT / "config" / "default_config.json"
    fixture_path = PROJECT_ROOT / "data" / "fixtures" / "valid_scenario.json"
    output_path = PROJECT_ROOT / "reports" / "demo_baseline_output.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = execute_baseline_scenario(config_path, fixture_path)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    b = result["baseline_verification"]
    r = result["nav_restatement_demo"]
    print("=== Investor Subscription & Redemption Platform Demo ===")
    print(f"Config SHA-256:            {result['runtime']['config_hash']}")
    print(f"Baseline Matches Expected: {b['matches_expected_summary']}")
    print(f"Baseline Total Shares:     {b['actual_reconciliation']['total_register_shares']}")
    print(f"Baseline Inbound Cash:     {b['actual_reconciliation']['total_inbound_cash_journal']} USD")
    print(f"Baseline Outbound Cash:    {b['actual_reconciliation']['total_outbound_cash_journal']} USD")
    print(f"Baseline Fees Collected:   {b['actual_reconciliation']['total_fees_collected']} USD")
    print(f"Baseline Logical Hash:     {b['baseline_logical_state_hash']}")
    print(f"Post-Restatement Shares:   {r['post_restatement_reconciliation']['total_register_shares']}")
    print(f"Audit Chain Valid:         {r['audit_chain_valid']} ({r['audit_event_count']} events)")
    print(f"Saved report:              {output_path}")
    return 0 if (b["matches_expected_summary"] and r["audit_chain_valid"] and r["post_restatement_reconciliation"]["reconciled"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
