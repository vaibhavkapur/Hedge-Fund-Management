"""Scenario 3: Unauthorized role approvals and maker self-approvals are rejected and audited."""

from __future__ import annotations

from datetime import date

import pytest

from hf_platform.domain import ApprovalAction, CloseStatus, EventType
from hf_platform.errors import AuthorizationError, RoleSeparationError
from hf_platform.services.platform import HedgeFundPlatform


def test_scenario_unauthorized_role_and_self_approval_blocked(platform: HedgeFundPlatform) -> None:
    d1 = date(2026, 11, 25)
    d2 = date(2026, 11, 26)

    # 1. Close sign-off by unauthorized roles (PM, SYSTEM_ADMIN, AUDITOR, OPERATIONS_ANALYST)
    r1 = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_acct_01")
    assert r1.status == CloseStatus.PENDING_SIGNOFF

    for actor in ("usr_pm_01", "usr_sys_01", "usr_audit_01", "usr_ops_01"):
        with pytest.raises(AuthorizationError):
            platform.close.sign_off_close(close_run_id=r1.close_run_id, actor_id=actor)

    # 2. Self-signoff by Controller who prepared the close run
    r1_ctrl = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d1, actor_id="usr_ctrl_01")
    with pytest.raises(RoleSeparationError):
        platform.close.sign_off_close(close_run_id=r1_ctrl.close_run_id, actor_id="usr_ctrl_01")

    # Independent Compliance Officer sign-off succeeds
    closed_d1 = platform.close.sign_off_close(close_run_id=r1_ctrl.close_run_id, actor_id="usr_comp_01")
    assert closed_d1.status == CloseStatus.CLOSED

    # 3. Exception self-approval by Risk Manager who proposed resolution
    r2 = platform.close.run_daily_close(fund_id="FUND_SAAT_MS", business_date=d2, actor_id="usr_ops_01")
    assert r2.status == CloseStatus.BLOCKED
    exc_id = r2.blocking_exception_ids[0]
    platform.exceptions.propose_resolution(
        exception_id=exc_id,
        resolution_notes="Risk manager investigation",
        actor_id="usr_risk_01",
    )
    with pytest.raises(RoleSeparationError):
        platform.exceptions.decide_exception(
            exception_id=exc_id,
            action=ApprovalAction.APPROVE,
            decision_notes="Attempting self approval",
            actor_id="usr_risk_01",
        )

    # Verify all denied attempts appear in immutable audit log & events
    denied_audits = [a for a in platform.audit.list_entries() if a.outcome == "DENIED"]
    assert len(denied_audits) >= 6
    assert any(e.event_type == EventType.AUTHORIZATION_DENIED for e in platform.audit.list_events())
    assert platform.audit.verify_chain()["verified"] is True
