"""Command-line interface (`hfp`) for the Integrated Hedge Fund Management Platform."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from hf_platform.api.server import create_server
from hf_platform.config import PlatformConfig
from hf_platform.domain import ReportType
from hf_platform.services.platform import HedgeFundPlatform


def build_platform_from_root(root: Path, db_path: str | Path = ":memory:") -> HedgeFundPlatform:
    config = PlatformConfig.from_file(root / "config" / "platform_config.json")
    platform = HedgeFundPlatform(config=config, db_path=db_path)
    platform.master.load_bundle_file(root / "data" / "fixtures" / "master_data.json")
    platform.dataset.load_day_file(root / "data" / "fixtures" / "day1_clean_close.json")
    platform.dataset.load_day_file(root / "data" / "fixtures" / "day2_reconciliation_breaks.json")
    return platform


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hfp",
        description="Integrated Hedge Fund Management Platform Control Plane CLI",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Path to project root containing config/ and data/fixtures/",
    )
    parser.add_argument(
        "--db",
        default=":memory:",
        help="SQLite database path (default: in-memory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Print platform health, adapter status, and audit chain integrity")

    close_p = sub.add_parser("close", help="Run daily close for a fund and business date")
    close_p.add_argument("--fund-id", default="FUND_SAAT_MS")
    close_p.add_argument("--date", required=True, help="Business date (YYYY-MM-DD)")
    close_p.add_argument("--actor", default="usr_acct_01")
    close_p.add_argument("--approver", default="usr_ctrl_01")
    close_p.add_argument("--signoff", action="store_true", help="Also execute dual-control sign-off")

    serve_p = sub.add_parser("serve", help="Seed fixtures, run Day 1 close, and launch Control Plane HTTP server")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8080)

    sub.add_parser("audit-verify", help="Verify cryptographic hash chain integrity of audit log")

    args = parser.parse_args(argv)
    platform = build_platform_from_root(args.project_root, db_path=args.db)

    try:
        if args.command == "health":
            print(json.dumps(platform.health_and_metrics(), indent=2))
            return 0

        if args.command == "audit-verify":
            print(json.dumps(platform.audit.verify_chain(), indent=2))
            return 0

        if args.command == "close":
            bdate = date.fromisoformat(args.date)
            run = platform.close.run_daily_close(
                fund_id=args.fund_id,
                business_date=bdate,
                actor_id=args.actor,
            )
            if args.signoff and run.status.value == "PENDING_SIGNOFF":
                run = platform.close.sign_off_close(close_run_id=run.close_run_id, actor_id=args.approver)
            print(run.model_dump_json(indent=2))
            return 0

        if args.command == "serve":
            run = platform.close.run_daily_close(
                fund_id="FUND_SAAT_MS",
                business_date=date(2026, 11, 25),
                actor_id="usr_acct_01",
            )
            platform.close.sign_off_close(close_run_id=run.close_run_id, actor_id="usr_ctrl_01")
            if run.snapshot_id:
                rpt = platform.reporting.generate_report(
                    snapshot_id=run.snapshot_id,
                    report_type=ReportType.NAV_SUMMARY,
                    actor_id="usr_acct_01",
                )
                platform.reporting.approve_and_publish(report_id=rpt.report_id, actor_id="usr_ctrl_01")
            server = create_server(platform, host=args.host, port=args.port)
            print(f"Control Plane listening at http://{args.host}:{args.port} (Ctrl+C to stop)")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                server.shutdown()
            return 0

        return 1
    finally:
        platform.shutdown()


if __name__ == "__main__":
    sys.exit(main())
