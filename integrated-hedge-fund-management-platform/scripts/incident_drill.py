"""M4 Simulated Daily Close, 7-Scenario Incident Drill, and Baseline Performance Benchmark."""

from __future__ import annotations

import json
import platform as sys_platform
import statistics
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from hf_platform.config import PlatformConfig
from hf_platform.domain import ApprovalAction, MarketMark, ReportType, TradeFill
from hf_platform.errors import AuthorizationError, LedgerBalanceError, RoleSeparationError
from hf_platform.services.platform import HedgeFundPlatform


def _fresh_platform(root: Path) -> HedgeFundPlatform:
    cfg = PlatformConfig.from_file(root / "config" / "platform_config.json")
    p = HedgeFundPlatform(config=cfg, db_path=":memory:")
    p.master.load_bundle_file(root / "data" / "fixtures" / "master_data.json")
    p.dataset.load_day_file(root / "data" / "fixtures" / "day1_clean_close.json")
    p.dataset.load_day_file(root / "data" / "fixtures" / "day2_reconciliation_breaks.json")
    return p


def run_incident_drill(project_root: Path) -> dict[str, Any]:
    evidence_dir = project_root / "reports" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    d1 = date(2026, 11, 25)
    d2 = date(2026, 11, 26)
    scenarios: dict[str, Any] = {}

    # 1. Unbalanced ledger
    p = _fresh_platform(project_root)
    raw_unbal = json.loads((project_root / "data" / "fixtures" / "scenario_unbalanced_ledger.json").read_text())
    try:
        p.ledger.post_manual_entry(
            entry_id=raw_unbal["entry"]["entry_id"],
            fund_id=raw_unbal["fund_id"],
            business_date=d1,
            description=raw_unbal["entry"]["description"],
            source_ref=raw_unbal["entry"]["source_ref"],
            lines=raw_unbal["entry"]["lines"],
            actor_id="usr_acct_01",
        )
        scenarios["unbalanced_ledger"] = {"passed": False}
    except LedgerBalanceError as exc:
        scenarios["unbalanced_ledger"] = {
            "passed": True,
            "error_code": exc.code,
            "imbalances": exc.details.get("imbalances"),
            "audit_verified": p.audit.verify_chain()["verified"],
        }
    p.shutdown()

    # 2. Missing and stale marks -> blocked -> dual-control manual override -> closed
    p = _fresh_platform(project_root)
    raw_mm = json.loads((project_root / "data" / "fixtures" / "scenario_missing_marks.json").read_text())
    p.fault_state.omitted_mark_instruments.update(raw_mm["omitted_instruments"])
    for sm in raw_mm["stale_marks"]:
        mk = MarketMark.model_validate(sm)
        p.fault_state.override_marks[mk.instrument_id] = mk
    blocked = p.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    p.close.apply_manual_mark_override(
        fund_id="FUND_SAAT_MS",
        business_date=d1,
        instrument_id="INST_NVDA",
        price=Decimal("138.00"),
        currency_code="USD",
        proposer_id="usr_ops_01",
        approver_id="usr_risk_01",
        justification="Verified NASDAQ closing print via secondary terminal",
    )
    p.close.apply_manual_mark_override(
        fund_id="FUND_SAAT_MS",
        business_date=d1,
        instrument_id="INST_SAP",
        price=Decimal("204.00"),
        currency_code="EUR",
        proposer_id="usr_ops_01",
        approver_id="usr_ctrl_01",
        justification="Verified XETRA close print",
    )
    recovered = p.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    closed = p.close.sign_off_close(close_run_id=recovered.close_run_id, actor_id="usr_ctrl_01")
    scenarios["missing_marks"] = {
        "passed": blocked.status.value == "BLOCKED" and closed.status.value == "CLOSED",
        "initial_status": blocked.status.value,
        "blocking_exceptions": len(blocked.blocking_exception_ids),
        "recovered_status": closed.status.value,
        "snapshot_id": closed.snapshot_id,
    }
    p.shutdown()

    # 3. Unauthorized approval (wrong role + self-approval)
    p = _fresh_platform(project_root)
    run_ok = p.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    denied_reasons: list[str] = []
    for bad_actor in ("usr_pm_01", "usr_sys_01", "usr_audit_01"):
        try:
            p.close.sign_off_close(close_run_id=run_ok.close_run_id, actor_id=bad_actor)
        except AuthorizationError as exc:
            denied_reasons.append(f"{bad_actor}:{exc.code}")
    run_ctrl = p.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_ctrl_01")
    try:
        p.close.sign_off_close(close_run_id=run_ctrl.close_run_id, actor_id="usr_ctrl_01")
    except RoleSeparationError as exc:
        denied_reasons.append(f"self_ctrl:{exc.code}")
    scenarios["unauthorized_approval"] = {
        "passed": len(denied_reasons) == 4,
        "denied_attempts": denied_reasons,
        "audit_verified": p.audit.verify_chain()["verified"],
    }
    p.shutdown()

    # 4. Late fills after 21:00 UTC cutoff
    p = _fresh_platform(project_root)
    raw_lf = json.loads((project_root / "data" / "fixtures" / "scenario_late_fills.json").read_text())
    for lf in raw_lf["late_fills"]:
        p.fault_state.extra_fills.append(TradeFill.model_validate({**lf, "business_date": d1}))
    run_lf = p.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    scenarios["late_fills"] = {
        "passed": run_lf.status.value == "BLOCKED" and len(run_lf.late_fill_ids) == 1,
        "status": run_lf.status.value,
        "late_fill_ids": run_lf.late_fill_ids,
        "blocking_exception_ids": run_lf.blocking_exception_ids,
    }
    p.shutdown()

    # 5. Reconciliation mismatch
    p = _fresh_platform(project_root)
    r1 = p.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    p.close.sign_off_close(close_run_id=r1.close_run_id, actor_id="usr_ctrl_01")
    r2 = p.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d2, actor_id="usr_ops_01")
    recon = p.store.get_reconciliation(r2.reconciliation_id)
    scenarios["reconciliation_mismatch"] = {
        "passed": r2.status.value == "BLOCKED" and not recon.is_clean and len(recon.breaks) >= 3,
        "status": r2.status.value,
        "break_count": len(recon.breaks),
        "break_types": sorted({b.break_type.value for b in recon.breaks}),
        "total_break_impact_usd": str(recon.total_break_impact_usd),
    }
    p.shutdown()

    # 6. Report revision with version diff & dual-control approval
    p = _fresh_platform(project_root)
    r1 = p.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    p.close.sign_off_close(close_run_id=r1.close_run_id, actor_id="usr_ctrl_01")
    rpt_v1 = p.reporting.generate_report(snapshot_id=r1.snapshot_id, report_type=ReportType.NAV_SUMMARY, actor_id="usr_acct_01")
    pub_v1 = p.reporting.approve_and_publish(report_id=rpt_v1.report_id, actor_id="usr_ctrl_01")
    # Simulate restatement after approved mark correction
    p.close.apply_manual_mark_override(
        fund_id="FUND_SAAT_MS",
        business_date=d1,
        instrument_id="INST_AAPL",
        price=Decimal("199.00"),
        currency_code="USD",
        proposer_id="usr_ops_01",
        approver_id="usr_risk_01",
        justification="Corrected official exchange closing auction print",
    )
    marks = p.market_data_adapter.fetch_marks(d1)
    fx = p.market_data_adapter.fetch_fx_rates(d1)
    snap_v2 = p.portfolio.build_snapshot(fund_id="FUND_SAAT_MS", business_date=d1, marks=marks, fx_rates=fx, actor_id="usr_acct_01")
    frozen_v2 = p.portfolio.freeze_snapshot(snap_v2.snapshot_id, actor_id="usr_acct_01")
    rpt_v2 = p.reporting.revise_report(
        prior_report_id=pub_v1.report_id,
        new_snapshot_id=frozen_v2.snapshot_id,
        reason="Restate AAPL EOD mark from 198.50 to 199.00 per exchange correction notice",
        actor_id="usr_acct_01",
    )
    pub_v2 = p.reporting.approve_and_publish(report_id=rpt_v2.report_id, actor_id="usr_ctrl_01")
    v1_after = p.store.get_report(pub_v1.report_id)
    scenarios["report_revision"] = {
        "passed": v1_after.status.value == "SUPERSEDED" and pub_v2.status.value == "PUBLISHED" and pub_v2.version == 2,
        "v1_status": v1_after.status.value,
        "v2_report_id": pub_v2.report_id,
        "changed_fields": sorted(pub_v2.revision_diff.changed_fields.keys()) if pub_v2.revision_diff else [],
        "nav_before": pub_v2.revision_diff.changed_fields.get("nav_usd", {}).get("before") if pub_v2.revision_diff else None,
        "nav_after": pub_v2.revision_diff.changed_fields.get("nav_usd", {}).get("after") if pub_v2.revision_diff else None,
    }
    p.shutdown()

    # 7. Service outage -> safe degraded halt -> service recovery -> clean close
    p = _fresh_platform(project_root)
    p.fault_state.broker_feed_outage = True
    degraded = p.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    # Resolve outage exception and restore service
    p.fault_state.broker_feed_outage = False
    for exc_id in degraded.blocking_exception_ids:
        p.exceptions.propose_resolution(exception_id=exc_id, resolution_notes="Prime broker feed restored", actor_id="usr_ops_01")
        p.exceptions.decide_exception(exception_id=exc_id, action=ApprovalAction.APPROVE, decision_notes="Verified feed health", actor_id="usr_ctrl_01")
    recovered = p.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    closed = p.close.sign_off_close(close_run_id=recovered.close_run_id, actor_id="usr_ctrl_01")
    scenarios["service_outage"] = {
        "passed": degraded.status.value == "DEGRADED_OUTAGE" and closed.status.value == "CLOSED",
        "degraded_status": degraded.status.value,
        "recovered_status": closed.status.value,
        "snapshot_id": closed.snapshot_id,
    }
    p.shutdown()

    # Baseline latency benchmark (25 clean Day-1 close runs in-memory SQLite)
    timings_ms: list[float] = []
    for _ in range(25):
        bp = _fresh_platform(project_root)
        t0 = time.perf_counter()
        run = bp.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
        bp.close.sign_off_close(close_run_id=run.close_run_id, actor_id="usr_ctrl_01")
        timings_ms.append((time.perf_counter() - t0) * 1000.0)
        bp.shutdown()

    timings_sorted = sorted(timings_ms)
    benchmark = {
        "sample_size": len(timings_ms),
        "environment": {
            "python": sys_platform.python_version(),
            "os": sys_platform.platform(),
            "storage": "sqlite_in_memory",
        },
        "mean_ms": round(statistics.mean(timings_ms), 2),
        "median_ms": round(statistics.median(timings_ms), 2),
        "stdev_ms": round(statistics.stdev(timings_ms), 2),
        "p95_ms": round(timings_sorted[int(0.95 * (len(timings_sorted) - 1))], 2),
        "min_ms": round(min(timings_ms), 2),
        "max_ms": round(max(timings_ms), 2),
        "note": "Baseline synthetic prototype measurement (7 fills, 7 positions, 2 accounts). Not a production SLA claim.",
    }

    report = {
        "all_scenarios_passed": all(s["passed"] for s in scenarios.values()),
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "baseline_benchmark": benchmark,
    }
    (evidence_dir / "incident_drill_evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    out = run_incident_drill(root)
    print(json.dumps(out, indent=2))
