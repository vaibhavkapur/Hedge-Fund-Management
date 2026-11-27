"""Role-Based Access Control (RBAC) and strict Maker-Checker role separation enforcement."""

from __future__ import annotations

from hf_platform.domain import EventType, Role, UserPrincipal
from hf_platform.errors import AuthorizationError, RoleSeparationError
from hf_platform.services.audit_service import AuditService
from hf_platform.storage.repository import StorageRepository

# Explicit permission matrix: action -> set of permitted roles.
# Note: SYSTEM_ADMIN is intentionally excluded from financial approvals, ledger posting, and close sign-off.
# AUDITOR is strictly read-only.
ROLE_PERMISSIONS: dict[str, set[Role]] = {
    "master.write": {Role.CONTROLLER, Role.OPERATIONS_ANALYST, Role.SYSTEM_ADMIN},
    "ledger.post": {Role.FUND_ACCOUNTANT, Role.CONTROLLER},
    "close.run": {Role.OPERATIONS_ANALYST, Role.FUND_ACCOUNTANT, Role.CONTROLLER},
    "close.signoff": {Role.CONTROLLER, Role.COMPLIANCE_OFFICER},
    "mark.override_propose": {Role.OPERATIONS_ANALYST, Role.FUND_ACCOUNTANT, Role.RISK_MANAGER},
    "mark.override_approve": {Role.CONTROLLER, Role.RISK_MANAGER, Role.COMPLIANCE_OFFICER},
    "exception.create": {
        Role.OPERATIONS_ANALYST,
        Role.FUND_ACCOUNTANT,
        Role.RISK_MANAGER,
        Role.CONTROLLER,
        Role.COMPLIANCE_OFFICER,
    },
    "exception.propose_resolution": {
        Role.OPERATIONS_ANALYST,
        Role.FUND_ACCOUNTANT,
        Role.RISK_MANAGER,
    },
    "exception.approve": {
        Role.CONTROLLER,
        Role.RISK_MANAGER,
        Role.COMPLIANCE_OFFICER,
    },
    "report.generate": {Role.FUND_ACCOUNTANT, Role.OPERATIONS_ANALYST, Role.CONTROLLER},
    "report.approve": {Role.CONTROLLER, Role.COMPLIANCE_OFFICER},
    "report.revise": {Role.FUND_ACCOUNTANT, Role.CONTROLLER},
    "report.deliver": {Role.OPERATIONS_ANALYST, Role.FUND_ACCOUNTANT, Role.CONTROLLER},
    "read.all": set(Role),
}


class AuthService:
    """Enforces RBAC and four-eyes (maker-checker) separation; audits every denial."""

    def __init__(self, store: StorageRepository, audit: AuditService) -> None:
        self._store = store
        self._audit = audit

    def require_user(self, user_id: str) -> UserPrincipal:
        user = self._store.get_user(user_id)
        if user is None:
            self._audit.record(
                actor_id=user_id,
                actor_role=None,
                action="auth.authenticate",
                entity_type="user",
                entity_id=user_id,
                outcome="DENIED",
                details={"reason": "unknown_user"},
            )
            self._audit.emit_event(
                event_type=EventType.AUTHORIZATION_DENIED,
                payload={"actor_id": user_id, "reason": "unknown_user"},
            )
            raise AuthorizationError(
                f"Unknown user principal: {user_id}",
                details={"user_id": user_id},
            )
        return user

    def authorize(
        self,
        *,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        maker_id: str | None = None,
        fund_id: str | None = None,
    ) -> UserPrincipal:
        user = self.require_user(actor_id)
        allowed_roles = ROLE_PERMISSIONS.get(action, set())
        if user.role not in allowed_roles:
            self._audit.record(
                actor_id=user.user_id,
                actor_role=user.role,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                outcome="DENIED",
                details={
                    "reason": "role_not_permitted",
                    "role": user.role.value,
                    "allowed_roles": sorted(r.value for r in allowed_roles),
                },
            )
            self._audit.emit_event(
                event_type=EventType.AUTHORIZATION_DENIED,
                fund_id=fund_id,
                payload={
                    "actor_id": user.user_id,
                    "actor_role": user.role.value,
                    "action": action,
                    "entity_id": entity_id,
                    "reason": "role_not_permitted",
                },
            )
            raise AuthorizationError(
                f"User {user.user_id} with role {user.role.value} is not authorized for action '{action}'",
                details={
                    "actor_id": user.user_id,
                    "actor_role": user.role.value,
                    "action": action,
                    "allowed_roles": sorted(r.value for r in allowed_roles),
                },
            )

        if maker_id is not None and maker_id == user.user_id:
            self._audit.record(
                actor_id=user.user_id,
                actor_role=user.role,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                outcome="DENIED",
                details={"reason": "maker_checker_self_approval", "maker_id": maker_id},
            )
            self._audit.emit_event(
                event_type=EventType.AUTHORIZATION_DENIED,
                fund_id=fund_id,
                payload={
                    "actor_id": user.user_id,
                    "action": action,
                    "entity_id": entity_id,
                    "reason": "maker_checker_self_approval",
                },
            )
            raise RoleSeparationError(
                f"Role separation violation: maker '{maker_id}' cannot self-approve action '{action}' on {entity_type} '{entity_id}'",
                details={"actor_id": user.user_id, "maker_id": maker_id, "action": action, "entity_id": entity_id},
            )

        return user
