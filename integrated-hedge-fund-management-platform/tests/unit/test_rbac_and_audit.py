"""Unit tests for RBAC matrix, maker-checker separation, and hash-chained audit integrity."""

from __future__ import annotations

import pytest

from hf_platform.errors import AuditIntegrityError, AuthorizationError, RoleSeparationError
from hf_platform.services.platform import HedgeFundPlatform


def test_rbac_denies_auditor_and_sysadmin_financial_actions(platform: HedgeFundPlatform) -> None:
    with pytest.raises(AuthorizationError):
        platform.auth.authorize(
            actor_id="usr_audit_01",
            action="close.signoff",
            entity_type="daily_close",
            entity_id="CLOSE_1",
        )
    with pytest.raises(AuthorizationError):
        platform.auth.authorize(
            actor_id="usr_sys_01",
            action="exception.approve",
            entity_type="exception",
            entity_id="EXC_1",
        )


def test_maker_checker_blocks_self_approval(platform: HedgeFundPlatform) -> None:
    with pytest.raises(RoleSeparationError):
        platform.auth.authorize(
            actor_id="usr_ctrl_01",
            action="close.signoff",
            entity_type="daily_close",
            entity_id="CLOSE_1",
            maker_id="usr_ctrl_01",
        )


def test_audit_chain_detects_tampering(platform: HedgeFundPlatform) -> None:
    check = platform.audit.verify_chain()
    assert check["verified"] is True
    assert check["entry_count"] >= 1

    # Tamper directly with SQLite row to prove hash chain catches modification
    platform.store._conn.execute("UPDATE audit_log SET action = 'tampered.action' WHERE sequence_number = 1")
    platform.store._conn.commit()
    with pytest.raises(AuditIntegrityError):
        platform.audit.verify_chain()
