"""Command-line interface for the Regulatory Reporting Engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from regulatory_reporting_engine.config import load_config
from regulatory_reporting_engine.engine import RegulatoryReportingEngine
from regulatory_reporting_engine.errors import RegulatoryEngineError
from regulatory_reporting_engine.models import GovernedSnapshot, ReportStatus
from regulatory_reporting_engine.renderer import render_lineage_bundle_json


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "default_config.json"


def _load_snapshot(path: str) -> GovernedSnapshot:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return GovernedSnapshot.from_dict(raw)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="regulatory-reporting-engine",
        description="Illustrative regulatory reporting engine (offline draft generation only; no automated filing).",
    )
    parser.add_argument(
        "--config",
        default=str(_default_config_path()),
        help="Path to engine JSON configuration file.",
    )
    parser.add_argument(
        "--base-dir",
        default=None,
        help="Optional base directory for resolving relative paths in config.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-profiles", help="List available jurisdiction/profile schemas.")

    p_val = sub.add_parser("validate-snapshot", help="Validate a governed snapshot JSON fixture.")
    p_val.add_argument("snapshot_path", help="Path to snapshot JSON file.")

    p_gen = sub.add_parser("generate", help="Generate a draft regulatory report with XML, CSV, and lineage.")
    p_gen.add_argument("snapshot_path", help="Path to snapshot JSON file.")
    p_gen.add_argument("--profile", required=True, help="Profile ID (e.g. US_SEC_PF_ILLUSTRATIVE).")
    p_gen.add_argument("--schema-version", required=True, help="Schema version (e.g. 1.0.0).")
    p_gen.add_argument("--actor", default="cli_operator", help="Audit actor identifier.")
    p_gen.add_argument("--out-dir", help="Optional directory to write XML, CSV, and lineage JSON.")

    p_diff = sub.add_parser("schema-diff", help="Inspect schema evolution changes between two versions.")
    p_diff.add_argument("--profile", required=True)
    p_diff.add_argument("--from-version", required=True)
    p_diff.add_argument("--to-version", required=True)

    p_replay = sub.add_parser("replay", help="Replay a stored report from its immutable snapshot and verify hash.")
    p_replay.add_argument("report_id", help="Stored report_id to replay.")

    p_status = sub.add_parser("transition", help="Advance a report approval status (REVIEWED, APPROVED, REJECTED).")
    p_status.add_argument("report_id")
    p_status.add_argument("--to-status", required=True, choices=["REVIEWED", "APPROVED", "REJECTED"])
    p_status.add_argument("--actor", required=True)
    p_status.add_argument("--rationale", required=True)

    args = parser.parse_args(argv)

    try:
        config = load_config(args.config, base_dir=args.base_dir)
        engine = RegulatoryReportingEngine(config)
        try:
            if args.command == "list-profiles":
                print(json.dumps({"profiles": engine.registry.list_profiles()}, indent=2))
                return 0

            if args.command == "validate-snapshot":
                snap = _load_snapshot(args.snapshot_path)
                print(
                    json.dumps(
                        {
                            "valid": True,
                            "snapshot_id": snap.snapshot_id,
                            "fund_id": snap.fund.fund_id,
                            "payload_sha256": snap.payload_sha256,
                            "positions": len(snap.positions),
                            "exposures": len(snap.exposures),
                        },
                        indent=2,
                    )
                )
                return 0

            if args.command == "generate":
                snap = _load_snapshot(args.snapshot_path)
                rpt = engine.generate_report(
                    snap,
                    profile_id=args.profile,
                    schema_version=args.schema_version,
                    actor=args.actor,
                )
                if args.out_dir:
                    out = Path(args.out_dir)
                    out.mkdir(parents=True, exist_ok=True)
                    schema = engine.registry.get_schema(args.profile, args.schema_version)
                    (out / f"{rpt.report_id}.xml").write_text(rpt.xml_payload, encoding="utf-8")
                    (out / f"{rpt.report_id}.csv").write_text(rpt.csv_payload, encoding="utf-8")
                    (out / f"{rpt.report_id}.lineage.json").write_text(
                        render_lineage_bundle_json(
                            report_id=rpt.report_id,
                            snapshot_id=rpt.snapshot_id,
                            schema=schema,
                            lineage=rpt.lineage,
                        ),
                        encoding="utf-8",
                    )
                print(json.dumps(rpt.to_dict(), indent=2))
                return 0

            if args.command == "schema-diff":
                diff = engine.compare_schemas(args.profile, args.from_version, args.to_version)
                print(json.dumps(diff.to_dict(), indent=2))
                return 0

            if args.command == "replay":
                res = engine.replay_report(args.report_id)
                print(json.dumps(res, indent=2))
                return 0 if res["exact_match"] else 2

            if args.command == "transition":
                entry = engine.transition_status(
                    args.report_id,
                    ReportStatus(args.to_status),
                    actor=args.actor,
                    rationale=args.rationale,
                )
                print(json.dumps(entry.to_dict(), indent=2))
                return 0
        finally:
            engine.close()
    except RegulatoryEngineError as exc:
        print(json.dumps(exc.to_dict(), indent=2), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
