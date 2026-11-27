"""Integration tests for REST API router, Control Plane UI HTML, and CLI."""

from __future__ import annotations

import json
from pathlib import Path

from hf_platform.api.server import ControlPlaneApp
from hf_platform.cli import main as cli_main
from hf_platform.services.platform import HedgeFundPlatform


def test_rest_api_and_ui_workflow(platform: HedgeFundPlatform) -> None:
    app = ControlPlaneApp(platform)

    # UI index served
    code, ctype, html = app.dispatch("GET", "/")
    assert code == 200
    assert ctype == "text/html"
    assert "SAAT Integrated Hedge Fund Control Plane" in html

    code, _, health = app.dispatch("GET", "/api/health")
    assert code == 200
    assert health["status"] == "HEALTHY"
    assert health["order_execution_enabled"] is False

    code, _, master = app.dispatch("GET", "/api/master")
    assert code == 200
    assert len(master["funds"]) == 1

    # Trigger Day 1 close via API
    code, _, run = app.dispatch(
        "POST",
        "/api/funds/FUND_SAAT_MS/close-runs",
        {"business_date": "2026-11-25", "actor_id": "usr_acct_01"},
    )
    assert code == 200
    assert run["status"] == "PENDING_SIGNOFF"

    # Unauthorized signoff rejected with 403
    code, _, err = app.dispatch(
        "POST",
        f"/api/close-runs/{run['close_run_id']}/signoff",
        {"actor_id": "usr_acct_01"},
    )
    assert code == 403
    assert err["error_code"] == "AUTHORIZATION_ERROR"

    # Controller signoff succeeds
    code, _, closed = app.dispatch(
        "POST",
        f"/api/close-runs/{run['close_run_id']}/signoff",
        {"actor_id": "usr_ctrl_01"},
    )
    assert code == 200
    assert closed["status"] == "CLOSED"

    # Trial balance & snapshots endpoints
    code, _, tb = app.dispatch("GET", "/api/funds/FUND_SAAT_MS/trial-balance?date=2026-11-25")
    assert code == 200
    assert tb["is_balanced"] is True

    code, _, snaps = app.dispatch("GET", "/api/funds/FUND_SAAT_MS/snapshots?date=2026-11-25")
    assert code == 200
    assert len(snaps["snapshots"]) == 1

    # Generate and approve report
    code, _, rpt = app.dispatch(
        "POST",
        "/api/reports/generate",
        {
            "snapshot_id": closed["snapshot_id"],
            "report_type": "NAV_SUMMARY",
            "actor_id": "usr_acct_01",
        },
    )
    assert code == 201
    code, _, pub = app.dispatch(
        "POST",
        f"/api/reports/{rpt['report_id']}/approve",
        {"actor_id": "usr_ctrl_01"},
    )
    assert code == 200
    assert pub["status"] == "PUBLISHED"

    # Audit and events endpoints
    code, _, audit = app.dispatch("GET", "/api/audit")
    assert code == 200
    assert audit["verification"]["verified"] is True

    code, _, events = app.dispatch("GET", "/api/events")
    assert code == 200
    assert len(events["events"]) > 0


def test_cli_commands(project_root: Path, capsys) -> None:
    rc = cli_main(["--project-root", str(project_root), "health"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "HEALTHY"

    rc = cli_main(
        ["--project-root", str(project_root), "close", "--date", "2026-11-25", "--signoff"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "CLOSED"

    rc = cli_main(["--project-root", str(project_root), "audit-verify"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verified"] is True
