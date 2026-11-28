#!/usr/bin/env python3
"""Run reproducible end-to-end Prime Broker Collateral Management demo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pb_collateral.cli import run_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Run reproducible multi-broker collateral management demo")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "demo_run_output.json",
        help="Destination JSON report path",
    )
    parser.add_argument(
        "--journal-db",
        type=str,
        default=":memory:",
        help="SQLite journal database path (default :memory:)",
    )
    args = parser.parse_args()

    report = run_bundle(
        policy_path=PROJECT_ROOT / "config" / "default_policy.json",
        financing_path=PROJECT_ROOT / "config" / "financing_rates.json",
        fixture_path=PROJECT_ROOT / "data" / "fixtures" / "demo_multi_broker_bundle.json",
        journal_db=args.journal_db,
        output_path=args.output,
    )

    print("=== Prime Broker Collateral Management Demo Summary ===")
    print(f"Scenario: {report['scenario_name']} (schema {report['schema_version']})")
    print(f"Counters: {json.dumps(report['counters'], sort_keys=True)}")
    for rec in report["reconciliations"]:
        mc = rec["margin_call"]
        val = rec["valuation"]
        fin = rec["financing"]
        print(
            f"\n[{rec['broker_id']} / {rec['account_id']} | {rec['business_date']}] run_id={rec['run_id']}\n"
            f"  Posted Collateral : {val['total_posted_collateral_base']} {rec['base_currency']}\n"
            f"  Margin Requirement: {mc['margin_requirement_base']} {rec['base_currency']}\n"
            f"  Surplus / Deficit : {mc['raw_surplus_or_deficit_base']} {rec['base_currency']}\n"
            f"  Margin Action     : {mc['action']} ({mc['call_or_return_amount_base']} {rec['base_currency']}) "
            f"[flagged_pre_settle={mc['flagged_before_settlement']}]\n"
            f"  Financing Accrual : {fin['net_accrual_base']} {rec['base_currency']}\n"
            f"  Reconcil. Breaks  : {len(rec['breaks'])} ({', '.join(b['category'] for b in rec['breaks']) or 'CLEAN'})"
        )
    for sub in report["substitutions"]:
        print(
            f"\n[Substitution {sub['request_id']} @ {sub['broker_id']}] review_id={sub['review_id']}\n"
            f"  Pre -> Post Posted: {sub['pre_posted_collateral_base']} -> {sub['post_posted_collateral_base']} {sub['base_currency']}\n"
            f"  Settlement Allowed: {sub['settlement_permitted']} | Status: {sub['approval_status']}\n"
            f"  Required Top-Up   : {sub['required_top_up_base']} {sub['base_currency']}"
        )
    print(f"\nWrote machine-readable report to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
