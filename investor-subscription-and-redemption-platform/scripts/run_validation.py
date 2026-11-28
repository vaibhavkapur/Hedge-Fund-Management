#!/usr/bin/env python3
"""Run full automated validation suite, reproducibility check, and write machine-readable evidence."""

from __future__ import annotations

import importlib.util
import json
import platform
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from isrp.config import load_config
from run_demo import execute_baseline_scenario


def _is_module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def main() -> int:
    start = time.perf_counter()
    config_path = PROJECT_ROOT / "config" / "default_config.json"
    fixture_path = PROJECT_ROOT / "data" / "fixtures" / "valid_scenario.json"
    config = load_config(config_path)

    # 1. Discover and run full unittest suite
    loader = unittest.TestLoader()
    suite = loader.discover(str(PROJECT_ROOT / "tests"), pattern="test_*.py")
    test_details: List[Dict[str, Any]] = []

    class RecordingResult(unittest.TextTestResult):
        def addSuccess(self, test: unittest.TestCase) -> None:
            super().addSuccess(test)
            test_details.append({"test": test.id(), "outcome": "PASS"})

        def addFailure(self, test: unittest.TestCase, err: Any) -> None:
            super().addFailure(test, err)
            test_details.append({"test": test.id(), "outcome": "FAIL", "detail": self._exc_info_to_string(err, test)})

        def addError(self, test: unittest.TestCase, err: Any) -> None:
            super().addError(test, err)
            test_details.append({"test": test.id(), "outcome": "ERROR", "detail": self._exc_info_to_string(err, test)})

    runner = unittest.TextTestRunner(verbosity=2, resultclass=RecordingResult)
    result = runner.run(suite)

    # 2. Reproducibility check: run baseline demo twice and assert identical hashes
    run1 = execute_baseline_scenario(config_path, fixture_path)
    run2 = execute_baseline_scenario(config_path, fixture_path)
    repro_match = (
        run1["baseline_verification"]["baseline_logical_state_hash"]
        == run2["baseline_verification"]["baseline_logical_state_hash"]
        and run1["nav_restatement_demo"]["post_restatement_logical_state_hash"]
        == run2["nav_restatement_demo"]["post_restatement_logical_state_hash"]
    )

    elapsed_ms = round((time.perf_counter() - start) * 1000.0, 2)

    report = {
        "report_schema_version": "1.0.0",
        "runtime": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "sqlite_version": sqlite3.sqlite_version,
            "config_hash": config.config_hash,
            "deterministic_seed": config.deterministic_seed,
            "optional_dependencies": {
                "fastapi_installed": _is_module_available("fastapi"),
                "pydantic_installed": _is_module_available("pydantic"),
                "psycopg_installed": _is_module_available("psycopg"),
                "pytest_installed": _is_module_available("pytest"),
            },
        },
        "test_summary": {
            "tests_run": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
            "all_passed": result.wasSuccessful(),
            "elapsed_ms": elapsed_ms,
        },
        "reproducibility": {
            "deterministic_replay_match": repro_match,
            "run1_baseline_hash": run1["baseline_verification"]["baseline_logical_state_hash"],
            "run2_baseline_hash": run2["baseline_verification"]["baseline_logical_state_hash"],
            "run1_post_restatement_hash": run1["nav_restatement_demo"]["post_restatement_logical_state_hash"],
            "run2_post_restatement_hash": run2["nav_restatement_demo"]["post_restatement_logical_state_hash"],
        },
        "untested_capabilities": [
            {
                "capability": "Live PostgreSQL persistence via PostgresRepository",
                "status": "NOT_RUN",
                "reason": "psycopg and a running PostgreSQL server are not installed in this offline environment; SQLiteRepository executed end-to-end and PostgreSQL DDL verified structurally.",
            },
            {
                "capability": "Live FastAPI/Uvicorn ASGI runtime",
                "status": "NOT_RUN",
                "reason": "fastapi/uvicorn packages are not installed in this offline environment; identical route semantics were executed and verified via isrp.api.http_server.dispatch_http_request.",
            },
        ],
        "tests": sorted(test_details, key=lambda x: x["test"]),
    }

    out_path = PROJECT_ROOT / "reports" / "validation_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"\nValidation report saved to: {out_path}")
    print(f"Tests run: {result.testsRun}, Passed: {result.wasSuccessful()}, Reproducible: {repro_match}, Elapsed: {elapsed_ms} ms")
    return 0 if (result.wasSuccessful() and repro_match) else 1


if __name__ == "__main__":
    raise SystemExit(main())
