"""Integration tests for persistent SQLite storage, event bus JSONL, and administrator outbox."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from hf_platform.config import PlatformConfig
from hf_platform.domain import ReportType
from hf_platform.services.platform import HedgeFundPlatform


def test_sqlite_persistence_across_restart_and_outbox_delivery(project_root: Path, tmp_path: Path) -> None:
    db_file = tmp_path / "persisted.db"
    outbox = tmp_path / "outbox"
    events_file = tmp_path / "events.jsonl"
    cfg = PlatformConfig.from_file(project_root / "config" / "platform_config.json")

    p1 = HedgeFundPlatform(config=cfg, db_path=db_file, outbox_dir=outbox, event_log_path=events_file)
    p1.master.load_bundle_file(project_root / "data" / "fixtures" / "master_data.json")
    p1.dataset.load_day_file(project_root / "data" / "fixtures" / "day1_clean_close.json")
    run = p1.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=date(2026, 11, 25), actor_id="usr_acct_01")
    closed = p1.close.sign_off_close(close_run_id=run.close_run_id, actor_id="usr_ctrl_01")
    rpt = p1.reporting.generate_report(
        snapshot_id=closed.snapshot_id,
        report_type=ReportType.ADMINISTRATOR_EXTRACT,
        actor_id="usr_acct_01",
    )
    pub = p1.reporting.approve_and_publish(report_id=rpt.report_id, actor_id="usr_ctrl_01")
    receipt = p1.reporting.deliver_to_administrator(report_id=pub.report_id, actor_id="usr_ops_01")
    p1.shutdown()

    assert Path(receipt["artifact_path"]).exists()
    assert events_file.exists()
    event_lines = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    assert any(e["event_type"] == "SNAPSHOT_FROZEN" for e in event_lines)
    assert any(e["event_type"] == "CLOSE_SIGNED_OFF" for e in event_lines)

    # Re-open from disk and verify full state + audit chain intact
    p2 = HedgeFundPlatform(config=cfg, db_path=db_file)
    try:
        reloaded_snap = p2.store.get_snapshot(closed.snapshot_id)
        assert reloaded_snap is not None
        assert reloaded_snap.is_frozen is True
        assert p2.audit.verify_chain()["verified"] is True
        assert len(p2.store.list_reports("FUND_SAAT_MS")) == 1
    finally:
        p2.shutdown()
