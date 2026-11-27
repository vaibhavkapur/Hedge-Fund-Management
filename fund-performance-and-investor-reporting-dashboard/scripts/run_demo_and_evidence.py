"""Reproducible validation runner and end-to-end demo script producing retained evidence."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fund_reporting.api.http_server import bootstrap_service
from fund_reporting.domain.enums import ReportAudience, Role
from fund_reporting.domain.errors import AuthorizationError
from fund_reporting.domain.models import FundDefinition, FxRateRecord, NavSnapshot
from fund_reporting.domain.validation import compute_ledger_hash, utc_now_iso
from fund_reporting.metrics.engine import AnalyticsEngine
from fund_reporting.security.rbac import Actor


def run_validation() -> int:
    """Execute all unit, integration, and scenario tests and write validation reports."""
    start_ts = time.perf_counter()
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT))

    results_list: List[Dict[str, Any]] = []

    class RecordingResult(unittest.TextTestResult):
        def addSuccess(self, test: unittest.TestCase) -> None:
            super().addSuccess(test)
            results_list.append({"test": test.id(), "status": "PASS"})

        def addFailure(self, test: unittest.TestCase, err: Any) -> None:
            super().addFailure(test, err)
            results_list.append({"test": test.id(), "status": "FAIL", "detail": str(err[1])})

        def addError(self, test: unittest.TestCase, err: Any) -> None:
            super().addError(test, err)
            results_list.append({"test": test.id(), "status": "ERROR", "detail": str(err[1])})

    runner = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult)
    res = runner.run(suite)
    elapsed_ms = round((time.perf_counter() - start_ts) * 1000.0, 2)

    # Also record exact hand-calculated verification evidence
    hc_bundle = json.loads((ROOT / "data" / "fixtures" / "hand_calculated_case.json").read_text(encoding="utf-8"))
    engine = AnalyticsEngine()
    hc_fund = FundDefinition.from_dict(hc_bundle["fund"])
    hc_snaps = [NavSnapshot.from_dict(s) for s in hc_bundle["nav_snapshots"]]
    hc_fx = [FxRateRecord.from_dict(r) for r in hc_bundle["fx_rates"]]
    hc_usd = engine.analyze(hc_fund, hc_snaps, fx_rates=hc_fx, reporting_currency="USD")
    hc_eur = engine.analyze(hc_fund, hc_snaps, fx_rates=hc_fx, reporting_currency="EUR")

    # Baseline analytics latency benchmark on 90-day synthetic fund
    alpha_bundle = json.loads((ROOT / "data" / "fixtures" / "fund_alpha_synthetic.json").read_text(encoding="utf-8"))
    service, _ = bootstrap_service(":memory:", ROOT / "data" / "fixtures" / "fund_alpha_synthetic.json")
    analyst = Actor("bench_analyst", Role.ANALYST)
    t0 = time.perf_counter()
    for _ in range(10):
        service.get_dashboard_analytics(analyst, "FUND-SAAT-SIM-01", reporting_currency="EUR")
    avg_analytics_ms = round(((time.perf_counter() - t0) / 10.0) * 1000.0, 3)

    report_json = {
        "generated_at_utc": utc_now_iso(),
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "data_classification": "SYNTHETIC_FIXTURES_ONLY",
        },
        "summary": {
            "total_tests": res.testsRun,
            "passed": sum(1 for r in results_list if r["status"] == "PASS"),
            "failures": len(res.failures),
            "errors": len(res.errors),
            "success": res.wasSuccessful(),
            "suite_duration_ms": elapsed_ms,
        },
        "baseline_benchmark": {
            "sample_size_runs": 10,
            "dataset": "FUND-SAAT-SIM-01 (90 trading days, 4 strategies, EUR FX conversion + adapters)",
            "mean_analytics_latency_ms": avg_analytics_ms,
        },
        "hand_calculated_verification": {
            "expected": hc_bundle["expected"],
            "computed": {
                "cumulative_gross_twr_usd": round(hc_usd["metrics"]["gross"]["cumulative_return"], 10),
                "cumulative_net_twr_usd": round(hc_usd["metrics"]["net"]["cumulative_return"], 10),
                "cumulative_net_twr_eur": round(hc_eur["metrics"]["net"]["cumulative_return"], 10),
                "max_net_drawdown_usd": round(hc_usd["metrics"]["net"]["max_drawdown"], 10),
                "max_net_drawdown_peak_date": hc_usd["metrics"]["net"]["max_drawdown_peak_date"],
                "max_net_drawdown_trough_date": hc_usd["metrics"]["net"]["max_drawdown_trough_date"],
                "max_net_drawdown_recovery_date": hc_usd["metrics"]["net"]["max_drawdown_recovery_date"],
            },
        },
        "test_cases": results_list,
    }

    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "validation_report.json").write_text(
        json.dumps(report_json, indent=2, sort_keys=True), encoding="utf-8"
    )

    md_lines = [
        "# Validation & Test Evidence Report",
        "",
        f"- **Generated (UTC):** `{report_json['generated_at_utc']}`",
        f"- **Python / Platform:** `{report_json['environment']['python_version']}` / `{report_json['environment']['platform']}`",
        f"- **Data Classification:** `SIMULATED / SYNTHETIC FIXTURES ONLY`",
        f"- **Result:** `{'PASS' if res.wasSuccessful() else 'FAIL'}` ({report_json['summary']['passed']}/{report_json['summary']['total_tests']} passed in {elapsed_ms} ms)",
        f"- **Baseline Benchmark:** Mean full 90-day multi-currency analytics latency = `{avg_analytics_ms} ms` (n=10 runs)",
        "",
        "## Hand-Calculated Verification",
        "",
        "| Metric | Expected | Computed | Match |",
        "|---|---:|---:|---|",
    ]
    for k, exp_v in hc_bundle["expected"].items():
        comp_v = report_json["hand_calculated_verification"]["computed"][k]
        ok = abs(exp_v - comp_v) < 1e-9 if isinstance(exp_v, float) else exp_v == comp_v
        md_lines.append(f"| `{k}` | `{exp_v}` | `{comp_v}` | `{'PASS' if ok else 'FAIL'}` |")

    md_lines.extend(["", "## Test Cases Executed", "", "| Test ID | Status |", "|---|---|"])
    for item in results_list:
        md_lines.append(f"| `{item['test']}` | `{item['status']}` |")

    (reports_dir / "validation_report.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"Wrote validation evidence to {reports_dir / 'validation_report.md'}")
    return 0 if res.wasSuccessful() else 1


def run_demo() -> int:
    """Run an end-to-end reproducible demo and write exported reports to reports/demo_exports/."""
    fixture = ROOT / "data" / "fixtures" / "fund_alpha_synthetic.json"
    service, _ = bootstrap_service(":memory:", fixture)

    analyst = Actor("analyst_maya", Role.ANALYST)
    reviewer = Actor("reviewer_alex", Role.REVIEWER)
    approver = Actor("approver_cto", Role.APPROVER)
    investor = Actor("investor_lp_01", Role.VIEWER_INVESTOR, entitled_funds={"FUND-SAAT-SIM-01"})

    usd = service.get_dashboard_analytics(analyst, "FUND-SAAT-SIM-01", reporting_currency="USD")
    eur = service.get_dashboard_analytics(analyst, "FUND-SAAT-SIM-01", reporting_currency="EUR")

    # Publish v1 investor report
    v1 = service.reports.create_draft_report(
        analyst,
        fund_id="FUND-SAAT-SIM-01",
        report_series_id="RPT-2026Q1-ALPHA",
        audience=ReportAudience.INVESTOR,
        reporting_currency="USD",
    )
    service.reports.submit_report(analyst, v1.version_id)
    service.reports.review_report(reviewer, v1.version_id, approve=True, review_notes="NAV bridge reconciled")
    v1_pub = service.reports.approve_and_publish_report(approver, v1.version_id)

    # Demonstrate post-close fee restatement -> revision v2
    snaps = service.get_snapshots(analyst, "FUND-SAAT-SIM-01")
    last = snaps[-1]
    restated = replace(
        last,
        snapshot_id=f"{last.snapshot_id}-r2",
        revision=2,
        availability_utc="2026-05-12T10:00:00Z",
        management_fee=round(last.management_fee + 125.0, 2),
        closing_nav=round(last.closing_nav - 125.0, 2),
    )
    restated = replace(restated, ledger_hash=compute_ledger_hash(restated))
    service.ingest_nav_snapshot(analyst, restated)

    v2 = service.reports.create_revision(
        analyst,
        supersedes_version_id=v1_pub.version_id,
        revision_reason="Post-close management fee accrual restatement (+$125.00)",
    )
    service.reports.submit_report(analyst, v2.version_id)
    service.reports.review_report(reviewer, v2.version_id, approve=True, review_notes="Verified restatement")
    v2_pub = service.reports.approve_and_publish_report(approver, v2.version_id)

    # Verify investor unauthorized internal analytics access is blocked & logged
    rbac_blocked = False
    try:
        service.get_dashboard_analytics(investor, "FUND-SAAT-SIM-01")
    except AuthorizationError:
        rbac_blocked = True

    export_dir = ROOT / "reports" / "demo_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    for fmt, ext in [("json", "json"), ("markdown", "md"), ("csv", "csv"), ("html", "html")]:
        _, content = service.export_report(investor, v2_pub.version_id, fmt)
        (export_dir / f"{v2_pub.version_id}.{ext}").write_text(content, encoding="utf-8")

    print("=== Fund Performance & Investor Reporting Demo ===")
    print(f"Banner        : {usd['disclosure']['banner']}")
    print(f"Period        : {usd['period_start']} to {usd['period_end']} ({usd['reconciliation_summary']['snapshot_count']} snapshots)")
    print(f"USD Net TWR   : {usd['metrics']['net']['cumulative_return']*100:.2f}% | Gross TWR: {usd['metrics']['gross']['cumulative_return']*100:.2f}%")
    print(f"EUR Net TWR   : {eur['metrics']['net']['cumulative_return']*100:.2f}% (FX translated)")
    print(f"Net Sharpe    : {usd['metrics']['net']['sharpe_ratio']:.2f} | Max DD: {usd['metrics']['net']['max_drawdown']*100:.2f}%")
    print(f"NAV Residual  : {usd['reconciliation_summary']['nav_bridge_residual']}")
    print(f"Attr Residual : {usd['attribution']['residual_unattributed_return']:.10f}")
    print(f"Report v1     : {v1_pub.version_id} (now SUPERSEDED, SHA256={v1_pub.content_sha256[:12]}...)")
    print(f"Report v2     : {v2_pub.version_id} (PUBLISHED, supersedes={v2_pub.supersedes_version_id})")
    print(f"RBAC Check    : Investor blocked from internal analytics = {rbac_blocked}")
    print(f"Exports Saved : {export_dir}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["validate", "demo"], required=True)
    args = parser.parse_args()
    code = run_validation() if args.mode == "validate" else run_demo()
    sys.exit(code)


if __name__ == "__main__":
    main()
