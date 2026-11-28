#!/usr/bin/env python3
"""Run test suite, reference oracle comparison, micro-benchmark, and emit machine-readable evidence in reports/."""

from __future__ import annotations

import json
import platform
import sqlite3
import sys
import time
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from regulatory_reporting_engine.config import load_config
from regulatory_reporting_engine.engine import RegulatoryReportingEngine
from regulatory_reporting_engine.models import GovernedSnapshot
from regulatory_reporting_engine.store import SqliteJournalStore
from run_demo import run_demo


def run_benchmark(iterations: int = 200) -> dict:
    config = load_config(PROJECT_ROOT / "config" / "default_config.json")
    engine = RegulatoryReportingEngine(config, store=SqliteJournalStore(":memory:"))
    snap = GovernedSnapshot.from_dict(
        json.loads((config.paths.fixtures_dir / "reference_snapshot_q1_2026.json").read_text(encoding="utf-8"))
    )
    try:
        latencies_ms = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            us = engine.build_draft_report_pure(
                snap, profile_id="US_SEC_PF_ILLUSTRATIVE", schema_version="1.0.0"
            )
            eu = engine.build_draft_report_pure(
                snap, profile_id="EU_AIFMD_ANNEX4_ILLUSTRATIVE", schema_version="1.0.0"
            )
            engine.reconcile_reports(us, eu)
            latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        latencies_ms.sort()
        total_ms = sum(latencies_ms)
        return {
            "workload": "dual_profile_mapping_xml_csv_validation_and_reconciliation",
            "iterations": iterations,
            "mean_ms": round(total_ms / iterations, 4),
            "p50_ms": round(latencies_ms[iterations // 2], 4),
            "p95_ms": round(latencies_ms[int(iterations * 0.95)], 4),
            "p99_ms": round(latencies_ms[int(iterations * 0.99)], 4),
            "throughput_bundles_per_sec": round((iterations / (total_ms / 1000.0)), 2),
        }
    finally:
        engine.close()


def run_oracle_comparison() -> dict:
    config = load_config(PROJECT_ROOT / "config" / "default_config.json")
    engine = RegulatoryReportingEngine(config, store=SqliteJournalStore(":memory:"))
    snap = GovernedSnapshot.from_dict(
        json.loads((config.paths.fixtures_dir / "reference_snapshot_q1_2026.json").read_text(encoding="utf-8"))
    )
    expected = json.loads((config.paths.fixtures_dir / "reference_expected.json").read_text(encoding="utf-8"))
    comparisons = {}
    try:
        for profile_key, oracle in expected["profiles"].items():
            pid, ver = profile_key.split(":")
            rpt = engine.build_draft_report_pure(snap, profile_id=pid, schema_version=ver)
            actual = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in rpt.fields.items()}
            mismatches = {
                k: {"expected": oracle[k], "actual": actual.get(k)}
                for k in oracle
                if oracle[k] != actual.get(k)
            }
            comparisons[profile_key] = {
                "report_id": rpt.report_id,
                "content_sha256": rpt.content_sha256,
                "field_count": len(actual),
                "lineage_traced_count": len(rpt.lineage),
                "exact_oracle_match": len(mismatches) == 0,
                "mismatches": mismatches,
            }
        return comparisons
    finally:
        engine.close()


def main() -> int:
    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # 1. Execute unittest suite
    loader = unittest.TestLoader()
    suite = loader.discover(str(PROJECT_ROOT / "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    t_test_start = time.perf_counter()
    test_result = runner.run(suite)
    test_duration_s = round(time.perf_counter() - t_test_start, 4)

    # 2. Execute end-to-end demo
    demo_summary = run_demo(reports_dir / "demo_outputs")

    # 3. Execute reference oracle comparison
    oracle_results = run_oracle_comparison()

    # 4. Execute micro-benchmark
    benchmark = run_benchmark(iterations=200)

    # 5. Record runtime/configuration metadata
    config = load_config(PROJECT_ROOT / "config" / "default_config.json")
    metadata = {
        "recorded_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "sqlite_version": sqlite3.sqlite_version,
            "external_dependencies_installed": [],
            "postgres_live_tests_run": False,
            "postgres_live_tests_note": "No live PostgreSQL server or psycopg installed in offline environment; DDL provided in sql/schema_postgres.sql and SQLite parity verified.",
        },
        "config": config.to_metadata_dict(),
        "test_suite": {
            "tests_run": test_result.testsRun,
            "failures": len(test_result.failures),
            "errors": len(test_result.errors),
            "skipped": len(test_result.skipped),
            "passed": test_result.wasSuccessful(),
            "duration_seconds": test_duration_s,
        },
        "oracle_comparison": oracle_results,
        "benchmark": benchmark,
        "demo_summary": demo_summary,
    }
    (reports_dir / "run_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    (reports_dir / "benchmark_results.json").write_text(json.dumps(benchmark, indent=2) + "\n", encoding="utf-8")
    (reports_dir / "oracle_comparison.json").write_text(json.dumps(oracle_results, indent=2) + "\n", encoding="utf-8")

    print("\n=== Acceptance Run Summary ===")
    print(json.dumps(metadata["test_suite"], indent=2))
    print(json.dumps(benchmark, indent=2))
    return 0 if test_result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
