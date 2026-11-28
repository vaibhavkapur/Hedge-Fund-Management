#!/usr/bin/env python3
"""Reproducible end-to-end demo and raw report generator for hedge-fund-fee-accounting-engine."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sys
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from hedge_fund_fee_engine.models import (
    CalculationRunResult,
    EngineConfig,
    FeeAgreement,
    InvestorLot,
    NavSnapshot,
)
from hedge_fund_fee_engine.reporting import (
    collect_runtime_metadata,
    compare_calculation_against_expected,
    export_journal_csv,
    run_scenario_fixture,
    write_json_report,
)
from hedge_fund_fee_engine.service import FeeAccountingService
from hedge_fund_fee_engine.storage import SqliteFeeStore


def main() -> int:
    config_path = PROJECT_ROOT / "config" / "default_engine_config.json"
    fixtures_dir = PROJECT_ROOT / "data" / "fixtures"
    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    ledger_path = reports_dir / "demo_ledger.sqlite"
    if ledger_path.exists():
        ledger_path.unlink()

    config = EngineConfig.from_json_file(config_path)
    store = SqliteFeeStore(ledger_path)
    service = FeeAccountingService(config=config, store=store)

    scenario_files = [
        ("reference_basic_2_and_20.json", "scenario_reference_basic.json"),
        ("loss_and_recovery_hwm.json", "scenario_loss_recovery.json"),
        ("mid_period_flows_and_correction.json", "scenario_flows_corrections.json"),
        ("hurdle_and_crystallization_boundaries.json", "scenario_hurdle_boundaries.json"),
    ]

    scenario_summaries: List[Dict[str, Any]] = []
    all_runs_for_csv: List[CalculationRunResult] = []
    all_passed = True

    for fixture_name, report_name in scenario_files:
        report = run_scenario_fixture(fixtures_dir / fixture_name, service, persist=True)
        write_json_report(reports_dir / report_name, report)
        if not report["all_passed"]:
            all_passed = False
        scenario_summaries.append(
            {
                "scenario_id": report["scenario_id"],
                "fixture_file": fixture_name,
                "report_file": report_name,
                "run_count": report["run_count"],
                "all_passed": report["all_passed"],
                "checks_evaluated": sum(c["checks_evaluated"] for c in report["comparisons"]),
                "discrepancy_count": sum(c["discrepancy_count"] for c in report["comparisons"]),
            }
        )

    # Run independent worked examples suite
    we_raw = json.loads((fixtures_dir / "independent_worked_examples.json").read_text(encoding="utf-8"))
    we_results: List[Dict[str, Any]] = []
    for ex in we_raw["examples"]:
        agr = FeeAgreement.from_dict(ex["agreement"])
        lots = tuple(InvestorLot.from_dict(d) for d in ex["initial_lots"])
        ex_comps = []
        for period in ex["periods"]:
            snap = NavSnapshot.from_dict(period["snapshot"])
            run_res = service.run_period(agreement=agr, snapshot=snap, lots=lots, persist=True)
            all_runs_for_csv.append(run_res)
            lots = run_res.updated_lots
            comp = compare_calculation_against_expected(
                actual=run_res,
                expected_lots=period["expected"],
                tolerance=Decimal("0.00"),
            )
            ex_comps.append(comp)
            if not comp["passed"]:
                all_passed = False
        we_results.append(
            {
                "scenario_id": ex["scenario_id"],
                "description": ex["description"],
                "passed": all(c["passed"] for c in ex_comps),
                "checks_evaluated": sum(c["checks_evaluated"] for c in ex_comps),
                "discrepancy_count": sum(c["discrepancy_count"] for c in ex_comps),
                "comparisons": ex_comps,
            }
        )

    worked_examples_report = {
        "suite_id": we_raw["suite_id"],
        "provenance": we_raw["provenance"],
        "tolerance": "0.00",
        "all_passed": all(r["passed"] for r in we_results),
        "example_count": len(we_results),
        "examples": we_results,
    }
    write_json_report(reports_dir / "worked_examples_comparison.json", worked_examples_report)

    # Verify deterministic replay on EVERY persisted run in SQLite
    persisted_runs = store.list_runs()
    replay_checks = [service.replay_run(r["run_id"]) for r in persisted_runs]
    all_replays_matched = all(rc["matches"] for rc in replay_checks)
    if not all_replays_matched:
        all_passed = False

    # Export worked-example journals CSV
    export_journal_csv(reports_dir / "worked_examples_journals.csv", all_runs_for_csv)

    runtime_metadata = collect_runtime_metadata(config)
    write_json_report(reports_dir / "runtime_metadata.json", runtime_metadata)

    demo_summary = {
        "all_passed": all_passed,
        "runtime_metadata": runtime_metadata,
        "persisted_run_count": len(persisted_runs),
        "all_replays_matched": all_replays_matched,
        "scenarios": scenario_summaries,
        "worked_examples": {
            "example_count": len(we_results),
            "all_passed": worked_examples_report["all_passed"],
            "total_checks": sum(r["checks_evaluated"] for r in we_results),
            "total_discrepancies": sum(r["discrepancy_count"] for r in we_results),
        },
        "replay_verification": replay_checks,
    }
    write_json_report(reports_dir / "demo_summary.json", demo_summary)
    store.close()

    print(json.dumps(
        {
            "all_passed": demo_summary["all_passed"],
            "persisted_run_count": demo_summary["persisted_run_count"],
            "all_replays_matched": demo_summary["all_replays_matched"],
            "scenarios_passed": sum(1 for s in scenario_summaries if s["all_passed"]),
            "scenarios_total": len(scenario_summaries),
            "worked_examples_passed": sum(1 for r in we_results if r["passed"]),
            "worked_examples_total": len(we_results),
        },
        indent=2,
    ))
    return 0 if all_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
