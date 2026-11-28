"""Command-line interface for Prime Broker Collateral Management."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pb_collateral.errors import CollateralDomainError
from pb_collateral.fx import FxTable
from pb_collateral.journal import SQLiteJournalStore
from pb_collateral.reporting import runtime_metadata, serialize_reconciliation_report, write_json_report
from pb_collateral.service import CollateralManagementService
from pb_collateral.validation import (
    normalize_broker_statement,
    parse_financing_schedules,
    parse_fx_rates,
    parse_internal_snapshot,
    parse_policy_config,
    parse_substitution_request,
)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_bundle(
    *,
    policy_path: Path,
    financing_path: Path,
    fixture_path: Path,
    journal_db: str = ":memory:",
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    policy_raw = load_json(policy_path)
    fin_raw = load_json(financing_path)
    fixture_raw = load_json(fixture_path)

    policies, rounding_mode = parse_policy_config(policy_raw)
    schedules = parse_financing_schedules(fin_raw)
    fx_table = FxTable(parse_fx_rates(fixture_raw.get("fx_rates", [])), rounding_mode=rounding_mode)

    journal = SQLiteJournalStore(journal_db)
    svc = CollateralManagementService(
        policies=policies,
        financing_schedules=schedules,
        journal=journal,
        rounding_mode=rounding_mode,
    )

    accrual_start = date.fromisoformat(fixture_raw["accrual_start_date"]) if "accrual_start_date" in fixture_raw else None
    accrual_end = date.fromisoformat(fixture_raw["accrual_end_date"]) if "accrual_end_date" in fixture_raw else None

    pairs = []
    for item in fixture_raw.get("broker_runs", []):
        stmt = normalize_broker_statement(item["statement"])
        snap = parse_internal_snapshot(item["internal_snapshot"])
        pairs.append((stmt, snap))

    results = svc.process_multi_broker_batch(
        pairs=pairs,
        fx_table=fx_table,
        accrual_start_date=accrual_start,
        accrual_end_date=accrual_end,
    )

    sub_results = []
    for sub_item in fixture_raw.get("substitutions", []):
        req = parse_substitution_request(sub_item["request"])
        matching_stmt = next((s for s, _ in pairs if s.broker_id == req.broker_id and s.account_id == req.account_id), None)
        if matching_stmt is None:
            raise CollateralDomainError(f"No matching statement for substitution broker '{req.broker_id}'")
        matching_snap = next(sn for s, sn in pairs if s.broker_id == req.broker_id and s.account_id == req.account_id)
        sub_res = svc.review_substitution(
            request=req,
            current_positions=matching_stmt.positions,
            cash_balances=matching_stmt.cash_balances,
            margin_requirement=matching_stmt.margin_requirement,
            fx_table=fx_table,
            transfers=matching_snap.transfers,
        )
        sub_results.append(sub_res)

    meta = runtime_metadata(
        {
            policy_path.name: _file_sha256(policy_path),
            financing_path.name: _file_sha256(financing_path),
            fixture_path.name: _file_sha256(fixture_path),
        }
    )
    report = serialize_reconciliation_report(
        scenario_name=fixture_raw.get("scenario_name", fixture_path.stem),
        results=results,
        substitutions=sub_results,
        counters=svc.counters,
        metadata=meta,
    )

    if output_path is not None:
        write_json_report(output_path, report)
    journal.close()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pb-collateral",
        description="Prime Broker Collateral Management CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run", help="Run collateral reconciliation and substitution scenario bundle")
    run_cmd.add_argument("--policy", type=Path, required=True, help="Path to collateral policy JSON")
    run_cmd.add_argument("--financing", type=Path, required=True, help="Path to financing schedules JSON")
    run_cmd.add_argument("--fixture", type=Path, required=True, help="Path to scenario bundle JSON")
    run_cmd.add_argument("--journal-db", type=str, default=":memory:", help="SQLite journal path (default :memory:)")
    run_cmd.add_argument("--output", type=Path, default=None, help="Optional JSON report output path")

    replay_cmd = sub.add_parser("replay", help="Replay a stored reconciliation run from SQLite journal")
    replay_cmd.add_argument("--journal-db", type=str, required=True, help="SQLite journal file path")
    replay_cmd.add_argument("--run-id", type=str, required=True, help="Deterministic run_id to replay")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            report = run_bundle(
                policy_path=args.policy,
                financing_path=args.financing,
                fixture_path=args.fixture,
                journal_db=args.journal_db,
                output_path=args.output,
            )
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        if args.command == "replay":
            store = SQLiteJournalStore(args.journal_db)
            data = store.get_reconciliation_run(args.run_id)
            store.close()
            if data is None:
                print(json.dumps({"error": "RUN_NOT_FOUND", "run_id": args.run_id}), file=sys.stderr)
                return 2
            print(json.dumps(data, indent=2, sort_keys=True))
            return 0
    except CollateralDomainError as exc:
        print(json.dumps(exc.to_dict(), indent=2, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
