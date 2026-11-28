#!/usr/bin/env python3
"""Generate versioned baseline, demo, scenario-matrix, and micro-benchmark reports in reports/."""

from __future__ import annotations

import hashlib
import json
import sys
import time
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pb_collateral.cli import run_bundle
from pb_collateral.fx import FxTable
from pb_collateral.models import AssetClass, CashBalanceRecord, CashBalanceType, EncumbranceStatus, FxRate, PositionRecord
from pb_collateral.reporting import runtime_metadata, write_json_report
from pb_collateral.validation import parse_policy_config
from pb_collateral.valuation import evaluate_collateral


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_benchmark(iterations: int = 50, positions_per_broker: int = 200) -> Dict[str, Any]:
    """Deterministic synthetic micro-benchmark for collateral valuation throughput."""
    policy_path = PROJECT_ROOT / "config" / "default_policy.json"
    policies, rounding = parse_policy_config(json.loads(policy_path.read_text(encoding="utf-8")))
    policy = policies["PB-ALPHA"]
    fx = FxTable(
        [
            FxRate("EUR", "USD", Decimal("1.1000"), date(2026, 3, 17)),
            FxRate("GBP", "USD", Decimal("1.2600"), date(2026, 3, 17)),
        ],
        rounding_mode=rounding,
    )
    positions: List[PositionRecord] = []
    for i in range(positions_per_broker):
        ac = AssetClass.US_TREASURY if i % 2 == 0 else AssetClass.EQUITY_LARGE_CAP
        rating = "AAA" if ac == AssetClass.US_TREASURY else "UNRATED"
        mat = "0_5Y" if ac == AssetClass.US_TREASURY else "PERPETUAL"
        ccy = "USD" if i % 3 != 0 else "EUR"
        positions.append(
            PositionRecord(
                position_id=f"BENCH-P-{i:04d}",
                lot_id=f"BENCH-LOT-{i:04d}",
                broker_id="PB-ALPHA",
                account_id="ACC-BENCH",
                security_id=f"SEC-{i:04d}",
                asset_class=ac,
                rating_tier=rating,
                maturity_bucket=mat,
                quantity=Decimal("1000"),
                market_price=Decimal("100.00"),
                currency=ccy,
                encumbrance_status=EncumbranceStatus.PLEDGED_MARGIN if i % 4 != 0 else EncumbranceStatus.UNENCUMBERED,
            )
        )
    cash = [
        CashBalanceRecord(
            balance_id="BENCH-CASH",
            broker_id="PB-ALPHA",
            account_id="ACC-BENCH",
            currency="USD",
            amount=Decimal("5000000.00"),
            balance_type=CashBalanceType.MARGIN_CASH,
        )
    ]

    latencies_ms: List[float] = []
    last_total = Decimal("0.00")
    for _ in range(iterations):
        t0 = time.perf_counter()
        summary = evaluate_collateral(
            broker_id="PB-ALPHA",
            account_id="ACC-BENCH",
            business_date=date(2026, 3, 17),
            positions=positions,
            cash_balances=cash,
            policy=policy,
            fx_table=fx,
            rounding_mode=rounding,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(elapsed_ms)
        last_total = summary.total_posted_collateral_base

    latencies_ms.sort()
    p50 = latencies_ms[len(latencies_ms) // 2]
    p95 = latencies_ms[int(len(latencies_ms) * 0.95)]
    mean_ms = sum(latencies_ms) / len(latencies_ms)
    pos_per_sec = positions_per_broker / (mean_ms / 1000.0) if mean_ms > 0 else 0.0

    return {
        "benchmark_kind": "synthetic_in_memory_valuation_microbenchmark",
        "iterations": iterations,
        "positions_per_iteration": positions_per_broker,
        "mean_latency_ms": round(mean_ms, 3),
        "p50_latency_ms": round(p50, 3),
        "p95_latency_ms": round(p95, 3),
        "throughput_positions_per_sec": round(pos_per_sec, 1),
        "deterministic_posted_collateral_usd": str(last_total),
    }


def run_unittest_matrix() -> Dict[str, Any]:
    loader = unittest.TestLoader()
    suite = loader.discover(str(PROJECT_ROOT / "tests"))
    result = unittest.TestResult()
    t0 = time.perf_counter()
    suite.run(result)
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    return {
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "all_passed": result.wasSuccessful(),
        "elapsed_ms": elapsed_ms,
    }


def main() -> int:
    reports_dir = PROJECT_ROOT / "reports"
    policy_path = PROJECT_ROOT / "config" / "default_policy.json"
    fin_path = PROJECT_ROOT / "config" / "financing_rates.json"
    ref_path = PROJECT_ROOT / "data" / "fixtures" / "reference_oracle.json"
    demo_path = PROJECT_ROOT / "data" / "fixtures" / "demo_multi_broker_bundle.json"

    baseline = run_bundle(
        policy_path=policy_path,
        financing_path=fin_path,
        fixture_path=ref_path,
        output_path=reports_dir / "baseline_reference_output.json",
    )
    demo = run_bundle(
        policy_path=policy_path,
        financing_path=fin_path,
        fixture_path=demo_path,
        output_path=reports_dir / "demo_run_output.json",
    )

    test_summary = run_unittest_matrix()
    bench = run_benchmark()
    meta = runtime_metadata(
        {
            "default_policy.json": _sha256(policy_path),
            "financing_rates.json": _sha256(fin_path),
            "reference_oracle.json": _sha256(ref_path),
            "demo_multi_broker_bundle.json": _sha256(demo_path),
        }
    )

    write_json_report(
        reports_dir / "benchmark_and_validation.json",
        {
            "metadata": meta,
            "unittest_summary": test_summary,
            "microbenchmark": bench,
            "baseline_run_ids": [r["run_id"] for r in baseline["reconciliations"]],
            "demo_run_ids": [r["run_id"] for r in demo["reconciliations"]],
            "not_run_dependencies": [
                {
                    "capability": "Live PostgreSQL persistence via PostgresJournalAdapter",
                    "status": "NOT_RUN",
                    "reason": "PostgreSQL server and optional psycopg driver are not installed in offline local environment; SQLiteJournalStore and DDL schema validation ran instead.",
                }
            ],
        },
    )
    print(json.dumps({"unittest_summary": test_summary, "microbenchmark": bench}, indent=2))
    return 0 if test_summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
