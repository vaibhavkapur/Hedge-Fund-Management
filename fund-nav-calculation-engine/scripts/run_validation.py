#!/usr/bin/env python3
"""Run full validation suite: unit/integration tests, reproducible demo, and synthetic benchmark."""

from __future__ import annotations

import json
import platform
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from fund_nav_engine.cli import run_benchmark, run_demo  # noqa: E402


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(str(PROJECT_ROOT / "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    test_result = runner.run(suite)

    demo_report = run_demo(project_root=PROJECT_ROOT, output_dir=PROJECT_ROOT / "reports")
    bench_report = run_benchmark(
        num_positions=500, num_classes=10, iterations=5, output_dir=PROJECT_ROOT / "reports"
    )

    summary = {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "tests_run": test_result.testsRun,
        "failures": len(test_result.failures),
        "errors": len(test_result.errors),
        "all_tests_passed": test_result.wasSuccessful(),
        "demo_oracle_checks": demo_report["oracle_comparison"],
        "demo_v1_snapshot_id": demo_report["v1_snapshot"]["snapshot_id"],
        "demo_v2_snapshot_id": demo_report["v2_restated_snapshot"]["snapshot_id"],
        "benchmark_mean_ms": bench_report["measurements"]["mean_ms"],
        "benchmark_positions_per_second": bench_report["measurements"]["positions_per_second_mean"],
        "benchmark_hashes_identical": bench_report["measurements"]["all_output_hashes_identical"],
    }
    (PROJECT_ROOT / "reports" / "validation_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("\n=== VALIDATION SUMMARY ===")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if test_result.wasSuccessful() and all(demo_report["oracle_comparison"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
