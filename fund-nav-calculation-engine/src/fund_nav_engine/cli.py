"""Command-line interface for the Fund NAV Calculation Engine."""

from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from fund_nav_engine.calculator import calculate_nav
from fund_nav_engine.errors import NavEngineError
from fund_nav_engine.io_loader import load_bundle, load_config
from fund_nav_engine.models import (
    AccrualDirection,
    AccrualRecord,
    AccrualType,
    FxRateRecord,
    InstrumentType,
    LedgerAccountType,
    LedgerBalanceRecord,
    PositionRecord,
    PriceRecord,
    ShareClassRecord,
    ValuationConfig,
    ValuationInputBundle,
)
from fund_nav_engine.service import NavCalculationService
from fund_nav_engine.store import SqliteNavStore


def _project_root() -> Path:
    # src/fund_nav_engine/cli.py -> project root is two levels above src
    return Path(__file__).resolve().parents[2]


def _configure_logging(verbose: bool) -> None:
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def _cmd_calculate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    bundle = load_bundle(args.bundle)
    if args.db:
        with SqliteNavStore(args.db) as store:
            svc = NavCalculationService(store)
            result = svc.calculate_and_store_draft(bundle, config, actor=args.actor)
            if args.freeze:
                result = svc.approve_and_freeze(
                    result.snapshot_id, approver=args.actor, publisher=args.actor
                )
    else:
        result = calculate_nav(bundle, config)

    out_text = json.dumps(result.to_dict(), indent=2, sort_keys=True)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(out_text + "\n", encoding="utf-8")
    else:
        print(out_text)
    return 0


def _cmd_freeze(args: argparse.Namespace) -> int:
    with SqliteNavStore(args.db) as store:
        svc = NavCalculationService(store)
        frozen = svc.approve_and_freeze(
            args.snapshot_id,
            approver=args.approver,
            publisher=args.publisher or args.approver,
        )
    print(json.dumps(frozen.to_dict(), indent=2, sort_keys=True))
    return 0


def _cmd_restate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    bundle = load_bundle(args.bundle)
    with SqliteNavStore(args.db) as store:
        svc = NavCalculationService(store)
        outcome = svc.publish_restatement(
            prior_snapshot_id=args.prior_snapshot_id,
            corrected_bundle=bundle,
            config=config,
            reason=args.reason,
            actor=args.actor,
            approver=args.approver or args.actor,
            auto_freeze=not args.draft_only,
        )
    payload = {
        "snapshot": outcome["snapshot"].to_dict(),
        "diff": outcome["diff"],
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    with SqliteNavStore(args.db) as store:
        svc = NavCalculationService(store)
        report = svc.replay_and_verify(args.snapshot_id)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def run_demo(
    project_root: Optional[Path] = None,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute reproducible end-to-end demo: calculate v1, freeze, restate v2, verify lineage."""
    root = project_root or _project_root()
    reports_dir = output_dir or (root / "reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(root / "config" / "default_config.json")
    v1_bundle = load_bundle(root / "data" / "fixtures" / "reference_scenario.json")
    v2_bundle = load_bundle(root / "data" / "fixtures" / "late_correction_bundle.json")
    expected_oracle = json.loads(
        (root / "data" / "fixtures" / "reference_expected.json").read_text(encoding="utf-8")
    )

    fixed_ts_v1 = datetime(2026, 3, 31, 17, 0, 0, tzinfo=timezone.utc)
    fixed_ts_v2 = datetime(2026, 4, 1, 9, 30, 0, tzinfo=timezone.utc)

    db_path = reports_dir / "demo_nav_store.sqlite"
    if db_path.exists():
        db_path.unlink()

    with SqliteNavStore(db_path) as store:
        svc = NavCalculationService(store)
        draft_v1 = svc.calculate_and_store_draft(
            v1_bundle, config, actor="fund_accountant", calculated_at=fixed_ts_v1
        )
        frozen_v1 = svc.approve_and_freeze(
            draft_v1.snapshot_id,
            approver="nav_controller",
            publisher="nav_controller",
            timestamp=fixed_ts_v1,
        )
        restatement = svc.publish_restatement(
            prior_snapshot_id=frozen_v1.snapshot_id,
            corrected_bundle=v2_bundle,
            config=config,
            reason="Late pricing vendor correction for EQ-US-ACME (102.50 -> 104.50 USD)",
            actor="nav_controller",
            approver="head_of_valuation",
            auto_freeze=True,
            calculated_at=fixed_ts_v2,
        )
        frozen_v2 = restatement["snapshot"]
        verify_v1 = svc.replay_and_verify(frozen_v1.snapshot_id)
        verify_v2 = svc.replay_and_verify(frozen_v2.snapshot_id)
        audit_entries = svc.list_audit_trail()

    oracle_checks = {
        "v1_gross_assets_matches_oracle": str(frozen_v1.gross_assets)
        == expected_oracle["v1"]["gross_assets"],
        "v1_total_liabilities_matches_oracle": str(frozen_v1.total_liabilities)
        == expected_oracle["v1"]["total_liabilities"],
        "v1_net_assets_matches_oracle": str(frozen_v1.net_assets)
        == expected_oracle["v1"]["net_assets"],
        "v1_nav_identity_exact": frozen_v1.net_assets
        == (frozen_v1.gross_assets - frozen_v1.total_liabilities),
        "v2_gross_assets_matches_oracle": str(frozen_v2.gross_assets)
        == expected_oracle["v2"]["gross_assets"],
        "v2_total_liabilities_matches_oracle": str(frozen_v2.total_liabilities)
        == expected_oracle["v2"]["total_liabilities"],
        "v2_net_assets_matches_oracle": str(frozen_v2.net_assets)
        == expected_oracle["v2"]["net_assets"],
        "v2_nav_identity_exact": frozen_v2.net_assets
        == (frozen_v2.gross_assets - frozen_v2.total_liabilities),
        "v2_links_to_v1": frozen_v2.prior_snapshot_id == frozen_v1.snapshot_id,
    }

    report = {
        "report_type": "reproducible_demo_run",
        "runtime_metadata": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "config_version": config.config_version,
            "seed": config.seed,
        },
        "v1_snapshot": frozen_v1.to_dict(),
        "v2_restated_snapshot": frozen_v2.to_dict(),
        "restatement_diff": restatement["diff"],
        "verification": {
            "v1": verify_v1,
            "v2": verify_v2,
        },
        "oracle_comparison": oracle_checks,
        "audit_journal_count": len(audit_entries),
        "audit_journal": audit_entries,
    }

    (reports_dir / "demo_run_output.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def run_benchmark(
    num_positions: int = 500,
    num_classes: int = 10,
    iterations: int = 5,
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run deterministic synthetic benchmark measuring NAV calculation latency and throughput."""
    root = _project_root()
    reports_dir = output_dir or (root / "reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    val_date = date(2026, 3, 31)
    ts = datetime(2026, 3, 31, 16, 0, 0, tzinfo=timezone.utc)
    config = ValuationConfig(
        config_version="1.0.0-bench",
        fund_id="FUND-BENCH-01",
        base_currency="USD",
        currency_precisions={"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0},
        require_reconciliation=True,
    )

    currencies = ["USD", "EUR", "GBP", "JPY"]
    fx_rates = (
        FxRateRecord("FX-EUR", "EUR", "USD", Decimal("1.25"), val_date, ts),
        FxRateRecord("FX-GBP", "GBP", "USD", Decimal("1.30"), val_date, ts),
        FxRateRecord("FX-JPY", "JPY", "USD", Decimal("0.0065"), val_date, ts),
    )

    positions: List[PositionRecord] = []
    prices: List[PriceRecord] = []
    cost_by_ccy: Dict[str, Decimal] = {c: Decimal("0") for c in currencies}

    for i in range(num_positions):
        ccy = currencies[i % len(currencies)]
        qty = Decimal(str(100 + (i % 50) * 10))
        px = Decimal("1000") if ccy == "JPY" else Decimal(f"{50 + (i % 100)}.25")
        cost = qty * (px - (Decimal("10") if ccy == "JPY" else Decimal("1.00")))
        inst_id = f"INST-{i:04d}"
        positions.append(
            PositionRecord(
                position_id=f"POS-{i:04d}",
                fund_id="FUND-BENCH-01",
                instrument_id=inst_id,
                instrument_type=InstrumentType.EQUITY,
                quantity=qty,
                cost_basis=cost,
                currency=ccy,
                valuation_date=val_date,
            )
        )
        prices.append(
            PriceRecord(
                price_id=f"PX-{i:04d}",
                instrument_id=inst_id,
                price=px,
                currency=ccy,
                price_date=val_date,
                as_of_timestamp=ts,
            )
        )
        cost_by_ccy[ccy] += cost

    share_classes = tuple(
        ShareClassRecord(
            share_class_id=f"CLS-{j:02d}",
            fund_id="FUND-BENCH-01",
            name=f"Synthetic Class {j}",
            currency="USD" if j % 2 == 0 else "EUR",
            opening_shares=Decimal("10000"),
            management_fee_bps=Decimal("100"),
            launch_nav_per_share=Decimal("100.00"),
            prior_net_assets_base=Decimal("1000000.00"),
        )
        for j in range(num_classes)
    )

    accruals = tuple(
        AccrualRecord(
            accrual_id=f"ACC-{j:02d}",
            fund_id="FUND-BENCH-01",
            accrual_type=AccrualType.MANAGEMENT_FEE,
            direction=AccrualDirection.LIABILITY,
            amount=Decimal("250.00"),
            currency="USD",
            valuation_date=val_date,
            period_start=val_date,
            period_end=val_date,
            share_class_id=f"CLS-{j:02d}",
        )
        for j in range(num_classes)
    )

    ledger_balances = [
        LedgerBalanceRecord(
            ledger_account_id=f"GL-SEC-{ccy}",
            fund_id="FUND-BENCH-01",
            account_type=LedgerAccountType.SECURITIES_COST,
            balance=bal,
            currency=ccy,
            as_of_date=val_date,
        )
        for ccy, bal in cost_by_ccy.items()
        if bal > Decimal("0")
    ]
    ledger_balances.append(
        LedgerBalanceRecord(
            ledger_account_id="GL-ACC-USD",
            fund_id="FUND-BENCH-01",
            account_type=LedgerAccountType.ACCRUED_EXPENSES,
            balance=Decimal("250.00") * num_classes,
            currency="USD",
            as_of_date=val_date,
        )
    )

    bundle = ValuationInputBundle(
        bundle_id=f"BENCH-{num_positions}P-{num_classes}C",
        fund_id="FUND-BENCH-01",
        valuation_date=val_date,
        input_version="1.0.0",
        positions=tuple(positions),
        prices=tuple(prices),
        fx_rates=fx_rates,
        accruals=accruals,
        share_classes=share_classes,
        ledger_balances=tuple(ledger_balances),
    )

    timings_ms: List[float] = []
    output_hashes: List[str] = []
    last_result = None
    for _ in range(iterations):
        start = time.perf_counter()
        last_result = calculate_nav(bundle, config, calculated_at=ts)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        timings_ms.append(round(elapsed_ms, 3))
        output_hashes.append(last_result.output_hash)

    assert last_result is not None
    bench_report = {
        "report_type": "synthetic_benchmark",
        "data_provenance": "synthetic_deterministic_fixture",
        "runtime_metadata": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "config_version": config.config_version,
            "seed": config.seed,
        },
        "parameters": {
            "num_positions": num_positions,
            "num_share_classes": num_classes,
            "iterations": iterations,
        },
        "measurements": {
            "run_durations_ms": timings_ms,
            "min_ms": min(timings_ms),
            "max_ms": max(timings_ms),
            "mean_ms": round(sum(timings_ms) / len(timings_ms), 3),
            "positions_per_second_mean": round(
                num_positions / ((sum(timings_ms) / len(timings_ms)) / 1000.0), 1
            ),
            "all_output_hashes_identical": len(set(output_hashes)) == 1,
            "output_hash": output_hashes[0],
            "reconciled": last_result.reconciliation.reconciled,
            "net_assets": str(last_result.net_assets),
        },
    }
    (reports_dir / "benchmark_results.json").write_text(
        json.dumps(bench_report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return bench_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fund-nav-engine",
        description="Deterministic Fund NAV Calculation, Reconciliation, and Restatement Engine.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable structured INFO logs.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_calc = sub.add_parser("calculate", help="Calculate NAV for an input bundle.")
    p_calc.add_argument("--config", required=True, help="Path to ValuationConfig JSON.")
    p_calc.add_argument("--bundle", required=True, help="Path to ValuationInputBundle JSON.")
    p_calc.add_argument("--db", help="Optional SQLite DB path to persist draft snapshot.")
    p_calc.add_argument("--actor", default="cli_operator", help="Operator identity for audit trail.")
    p_calc.add_argument("--freeze", action="store_true", help="Approve and freeze immediately (requires --db).")
    p_calc.add_argument("--output", help="Optional path to write JSON result.")
    p_calc.set_defaults(func=_cmd_calculate)

    p_freeze = sub.add_parser("freeze", help="Approve and freeze an existing draft snapshot.")
    p_freeze.add_argument("--db", required=True, help="SQLite DB path.")
    p_freeze.add_argument("--snapshot-id", required=True, help="Snapshot ID to freeze.")
    p_freeze.add_argument("--approver", required=True, help="Approver actor name.")
    p_freeze.add_argument("--publisher", help="Publisher actor name (defaults to approver).")
    p_freeze.set_defaults(func=_cmd_freeze)

    p_restate = sub.add_parser("restate", help="Publish a restatement linked to a prior frozen snapshot.")
    p_restate.add_argument("--db", required=True, help="SQLite DB path.")
    p_restate.add_argument("--prior-snapshot-id", required=True, help="Prior frozen snapshot ID.")
    p_restate.add_argument("--config", required=True, help="Path to ValuationConfig JSON.")
    p_restate.add_argument("--bundle", required=True, help="Path to corrected input bundle JSON.")
    p_restate.add_argument("--reason", required=True, help="Audit reason for restatement.")
    p_restate.add_argument("--actor", default="nav_controller", help="Actor initiating restatement.")
    p_restate.add_argument("--approver", help="Approver actor name.")
    p_restate.add_argument("--draft-only", action="store_true", help="Leave restatement as draft.")
    p_restate.set_defaults(func=_cmd_restate)

    p_verify = sub.add_parser("verify", help="Replay stored inputs and verify snapshot hash & lineage.")
    p_verify.add_argument("--db", required=True, help="SQLite DB path.")
    p_verify.add_argument("--snapshot-id", required=True, help="Snapshot ID to verify.")
    p_verify.set_defaults(func=_cmd_verify)

    p_demo = sub.add_parser("demo", help="Run reproducible end-to-end demo and write reports.")
    p_demo.add_argument("--output-dir", help="Optional output directory (defaults to reports/).")
    p_demo.set_defaults(
        func=lambda a: (
            print(
                json.dumps(
                    run_demo(output_dir=Path(a.output_dir) if a.output_dir else None),
                    indent=2,
                    sort_keys=True,
                )
            )
            or 0
        )
    )

    p_bench = sub.add_parser("benchmark", help="Run deterministic synthetic performance benchmark.")
    p_bench.add_argument("--positions", type=int, default=500)
    p_bench.add_argument("--classes", type=int, default=10)
    p_bench.add_argument("--iterations", type=int, default=5)
    p_bench.add_argument("--output-dir", help="Optional output directory (defaults to reports/).")
    p_bench.set_defaults(
        func=lambda a: (
            print(
                json.dumps(
                    run_benchmark(
                        num_positions=a.positions,
                        num_classes=a.classes,
                        iterations=a.iterations,
                        output_dir=Path(a.output_dir) if a.output_dir else None,
                    ),
                    indent=2,
                    sort_keys=True,
                )
            )
            or 0
        )
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return int(args.func(args))
    except NavEngineError as exc:
        print(json.dumps(exc.to_dict(), indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
