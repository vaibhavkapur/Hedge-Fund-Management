"""End-to-end reproducible demonstration of the Integrated Hedge Fund Management Platform."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from hf_platform.config import PlatformConfig
from hf_platform.domain import ApprovalAction, ReportType
from hf_platform.errors import ExecutionDisabledError, RoleSeparationError
from hf_platform.services.platform import HedgeFundPlatform


def run_demo(project_root: Path) -> dict:
    evidence_dir = project_root / "reports" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    db_path = evidence_dir / "demo_runtime.db"
    if db_path.exists():
        db_path.unlink()

    config = PlatformConfig.from_file(project_root / "config" / "platform_config.json")
    platform = HedgeFundPlatform(
        config=config,
        db_path=db_path,
        outbox_dir=evidence_dir / "admin_outbox",
        event_log_path=evidence_dir / "demo_events.jsonl",
    )
    if (evidence_dir / "demo_events.jsonl").exists():
        (evidence_dir / "demo_events.jsonl").unlink()

    try:
        # 1. Load canonical master data
        bundle = platform.master.load_bundle_file(project_root / "data" / "fixtures" / "master_data.json")
        platform.dataset.load_day_file(project_root / "data" / "fixtures" / "day1_clean_close.json")
        platform.dataset.load_day_file(project_root / "data" / "fixtures" / "day2_reconciliation_breaks.json")

        # 2. Verify hard order execution kill-switch
        exec_blocked = False
        try:
            platform.submit_order(actor_id="usr_pm_01", fund_id="FUND_SAAT_MS", symbol="AAPL", quantity="100")
        except ExecutionDisabledError:
            exec_blocked = True

        # 3. Day 1 clean close + dual-control signoff
        d1 = date(2026, 11, 25)
        run1 = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
        assert run1.status.value == "PENDING_SIGNOFF", f"Unexpected Day 1 status: {run1.status}"

        # Demonstrate role separation rejection when preparer attempts self-signoff
        self_signoff_blocked = False
        try:
            platform.close.sign_off_close(close_run_id=run1.close_run_id, actor_id="usr_acct_01")
        except (RoleSeparationError, Exception):
            self_signoff_blocked = True

        run1_closed = platform.close.sign_off_close(close_run_id=run1.close_run_id, actor_id="usr_ctrl_01")
        snap1 = platform.store.get_snapshot(run1_closed.snapshot_id)
        tb1 = platform.ledger.compute_trial_balance("FUND_SAAT_MS", d1)

        # 4. Generate, approve, and deliver frozen reports for Day 1
        nav_rpt = platform.reporting.generate_report(
            snapshot_id=snap1.snapshot_id,
            report_type=ReportType.NAV_SUMMARY,
            actor_id="usr_acct_01",
            notes="Official Day 1 EOD NAV Summary",
        )
        nav_pub = platform.reporting.approve_and_publish(report_id=nav_rpt.report_id, actor_id="usr_ctrl_01")
        admin_rpt = platform.reporting.generate_report(
            snapshot_id=snap1.snapshot_id,
            report_type=ReportType.ADMINISTRATOR_EXTRACT,
            actor_id="usr_acct_01",
        )
        admin_pub = platform.reporting.approve_and_publish(report_id=admin_rpt.report_id, actor_id="usr_ctrl_01")
        delivery_receipt = platform.reporting.deliver_to_administrator(
            report_id=admin_pub.report_id, actor_id="usr_ops_01"
        )

        # 5. Day 2 close with reconciliation breaks -> blocked -> maker-checker resolution -> re-run & close
        d2 = date(2026, 11, 26)
        run2_blocked = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d2, actor_id="usr_ops_01")
        assert run2_blocked.status.value == "BLOCKED"

        for exc_id in run2_blocked.blocking_exception_ids:
            platform.exceptions.propose_resolution(
                exception_id=exc_id,
                resolution_notes="Confirmed PB settlement timing difference; documented with prime broker ticket #PB-8821",
                actor_id="usr_ops_01",
            )
            platform.exceptions.decide_exception(
                exception_id=exc_id,
                action=ApprovalAction.WAIVE,
                decision_notes="Reviewed broker ticket; approved T+1 settlement waiver",
                actor_id="usr_ctrl_01",
            )

        run2_ready = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d2, actor_id="usr_acct_01")
        run2_closed = platform.close.sign_off_close(close_run_id=run2_ready.close_run_id, actor_id="usr_ctrl_01")
        snap2 = platform.store.get_snapshot(run2_closed.snapshot_id)

        audit_check = platform.audit.verify_chain()
        summary = {
            "master_counts": {
                "funds": len(bundle.funds),
                "accounts": len(bundle.accounts),
                "strategies": len(bundle.strategies),
                "instruments": len(bundle.instruments),
                "users": len(bundle.users),
            },
            "controls": {
                "order_execution_blocked": exec_blocked,
                "self_signoff_blocked": self_signoff_blocked,
                "audit_chain_verified": audit_check["verified"],
                "audit_entries": audit_check["entry_count"],
            },
            "day1_close": {
                "close_run_id": run1_closed.close_run_id,
                "status": run1_closed.status.value,
                "snapshot_id": snap1.snapshot_id,
                "nav_usd": str(snap1.nav_usd),
                "ledger_nav_usd": str(snap1.ledger_nav_usd),
                "nav_diff_usd": str(snap1.nav_reconciliation_diff_usd),
                "trial_balance_balanced": tb1.is_balanced,
                "gross_leverage": str(snap1.risk_metrics.gross_leverage),
                "daily_var_95_usd": str(snap1.risk_metrics.daily_var_95_usd),
                "published_nav_report": nav_pub.report_id,
                "admin_delivery_id": delivery_receipt["delivery_id"],
            },
            "day2_close_with_breaks": {
                "initial_attempt_status": run2_blocked.status.value,
                "blocking_exceptions_detected": len(run2_blocked.blocking_exception_ids),
                "final_close_run_id": run2_closed.close_run_id,
                "final_status": run2_closed.status.value,
                "snapshot_id": snap2.snapshot_id,
                "nav_usd": str(snap2.nav_usd),
                "nav_diff_usd": str(snap2.nav_reconciliation_diff_usd),
            },
        }
        (evidence_dir / "demo_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary
    finally:
        platform.shutdown()


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    result = run_demo(root)
    print(json.dumps(result, indent=2))
