"""Command-line interface for the Hedge Fund Fee Accounting Engine."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import List, Optional

from .errors import FeeEngineError
from .reporting import collect_runtime_metadata, run_scenario_fixture, write_json_report
from .service import FeeAccountingService


DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "default_engine_config.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fee-engine",
        description="Hedge Fund Fee Accounting Engine CLI (educational/research prototype)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to engine configuration JSON file",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Optional SQLite ledger database path (uses in-memory DB if omitted)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run-fixture", help="Run a JSON scenario fixture and verify expected oracle outputs")
    run_cmd.add_argument("fixture", type=Path, help="Path to scenario fixture JSON file")
    run_cmd.add_argument("--out", type=Path, default=None, help="Optional path to write JSON report")

    replay_cmd = sub.add_parser("replay", help="Replay a stored run from SQLite and verify output hash")
    replay_cmd.add_argument("run_id", type=str, help="Run ID to replay")

    list_cmd = sub.add_parser("list-runs", help="List persisted calculation runs in SQLite")
    list_cmd.add_argument("--json", action="store_true", help="Print JSON output")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = args.db if args.db is not None else Path(":memory:")

    try:
        service = FeeAccountingService.from_config_path(args.config, db_path=db_path)
        if args.command == "run-fixture":
            report = run_scenario_fixture(args.fixture, service, persist=True)
            report["runtime_metadata"] = collect_runtime_metadata(service.config)
            if args.out is not None:
                write_json_report(args.out, report)
            print(
                json.dumps(
                    {
                        "scenario_id": report["scenario_id"],
                        "all_passed": report["all_passed"],
                        "run_count": report["run_count"],
                        "comparisons_checked": sum(c["checks_evaluated"] for c in report["comparisons"]),
                        "discrepancies": sum(c["discrepancy_count"] for c in report["comparisons"]),
                    },
                    indent=2,
                )
            )
            return 0 if report["all_passed"] else 2

        if args.command == "replay":
            if args.db is None:
                print("Error: --db is required for replay", file=sys.stderr)
                return 1
            res = service.replay_run(args.run_id)
            print(json.dumps(res, indent=2))
            return 0 if res["matches"] else 2

        if args.command == "list-runs":
            if args.db is None:
                print("Error: --db is required for list-runs", file=sys.stderr)
                return 1
            runs = service.store.list_runs()  # type: ignore[union-attr]
            print(json.dumps(runs, indent=2))
            return 0

        return 1
    except FeeEngineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
