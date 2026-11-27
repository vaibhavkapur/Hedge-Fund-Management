"""RBAC policy engine, separation-of-duties checks, and append-only audit trail."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Set

from fund_reporting.domain.enums import ReportAudience, ReportStatus, Role
from fund_reporting.domain.errors import AuthorizationError
from fund_reporting.domain.models import AuditEvent, ReportRecord
from fund_reporting.domain.validation import utc_now_iso
from fund_reporting.storage.repository import FundRepository


class Action(str, Enum):
    INGEST_DATA = "INGEST_DATA"
    READ_INTERNAL_ANALYTICS = "READ_INTERNAL_ANALYTICS"
    CREATE_REPORT = "CREATE_REPORT"
    SUBMIT_REPORT = "SUBMIT_REPORT"
    REVIEW_REPORT = "REVIEW_REPORT"
    APPROVE_PUBLISH_REPORT = "APPROVE_PUBLISH_REPORT"
    REVISE_REPORT = "REVISE_REPORT"
    READ_REPORT = "READ_REPORT"
    EXPORT_REPORT = "EXPORT_REPORT"
    READ_AUDIT_LOG = "READ_AUDIT_LOG"


_ROLE_PERMISSIONS: Dict[Role, Set[Action]] = {
    Role.VIEWER_INVESTOR: {
        Action.READ_REPORT,
        Action.EXPORT_REPORT,
    },
    Role.ANALYST: {
        Action.INGEST_DATA,
        Action.READ_INTERNAL_ANALYTICS,
        Action.CREATE_REPORT,
        Action.SUBMIT_REPORT,
        Action.REVISE_REPORT,
        Action.READ_REPORT,
        Action.EXPORT_REPORT,
    },
    Role.REVIEWER: {
        Action.READ_INTERNAL_ANALYTICS,
        Action.REVIEW_REPORT,
        Action.REVISE_REPORT,
        Action.READ_REPORT,
        Action.EXPORT_REPORT,
        Action.READ_AUDIT_LOG,
    },
    Role.APPROVER: {
        Action.READ_INTERNAL_ANALYTICS,
        Action.APPROVE_PUBLISH_REPORT,
        Action.REVISE_REPORT,
        Action.READ_REPORT,
        Action.EXPORT_REPORT,
        Action.READ_AUDIT_LOG,
    },
    Role.ADMIN: set(Action),
}


@dataclass(frozen=True)
class Actor:
    actor_id: str
    role: Role
    entitled_funds: Optional[Set[str]] = field(default=None)

    def can_access_fund(self, fund_id: str) -> bool:
        if self.entitled_funds is None:
            return True
        return fund_id in self.entitled_funds


class TokenAuthenticator:
    """Bearer-token authenticator mapping secret tokens (loaded outside source code) to Actors."""

    def __init__(self, token_map: Optional[Dict[str, Actor]] = None) -> None:
        self._tokens: Dict[str, Actor] = dict(token_map or {})

    def register_token(self, token: str, actor: Actor) -> None:
        self._tokens[token] = actor

    def authenticate(self, authorization_header: Optional[str]) -> Actor:
        if not authorization_header:
            raise AuthorizationError(
                "Missing Authorization header (expected 'Bearer <token>')",
                code="UNAUTHENTICATED",
                http_status=401,
            )
        parts = authorization_header.strip().split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise AuthorizationError(
                "Malformed Authorization header (expected 'Bearer <token>')",
                code="UNAUTHENTICATED",
                http_status=401,
            )
        presented = parts[1].strip()
        for stored_token, actor in self._tokens.items():
            if hmac.compare_digest(presented, stored_token):
                return actor
        raise AuthorizationError(
            "Invalid or expired API token",
            code="UNAUTHENTICATED",
            http_status=401,
        )


class AccessController:
    """Enforces RBAC, investor visibility boundaries, dual control, and audit logging."""

    def __init__(
        self,
        repository: FundRepository,
        *,
        require_dual_control: bool = True,
    ) -> None:
        self._repo = repository
        self.require_dual_control = require_dual_control

    def _record_audit(
        self,
        actor: Actor,
        action: Action,
        resource_type: str,
        resource_id: str,
        outcome: str,
        details: Optional[Dict[str, object]] = None,
    ) -> None:
        event = AuditEvent(
            audit_id=f"AUD-{uuid.uuid4().hex[:12]}",
            timestamp_utc=utc_now_iso(),
            actor_id=actor.actor_id,
            actor_role=actor.role,
            action=action.value,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details or {},
        )
        self._repo.append_audit_event(event)

    def authorize(
        self,
        actor: Actor,
        action: Action,
        *,
        resource_type: str,
        resource_id: str,
        fund_id: Optional[str] = None,
    ) -> None:
        allowed_actions = _ROLE_PERMISSIONS.get(actor.role, set())
        if action not in allowed_actions:
            self._record_audit(
                actor,
                action,
                resource_type,
                resource_id,
                "DENY",
                {"reason": f"Role {actor.role.value} is not permitted to perform {action.value}"},
            )
            raise AuthorizationError(
                f"Role {actor.role.value} is not authorized for {action.value}",
                details={
                    "actor_id": actor.actor_id,
                    "role": actor.role.value,
                    "action": action.value,
                    "resource_id": resource_id,
                },
            )

        if fund_id is not None and not actor.can_access_fund(fund_id):
            self._record_audit(
                actor,
                action,
                resource_type,
                resource_id,
                "DENY",
                {"reason": f"Actor not entitled to fund {fund_id}", "fund_id": fund_id},
            )
            raise AuthorizationError(
                f"Actor {actor.actor_id} is not entitled to access fund {fund_id}",
                details={"actor_id": actor.actor_id, "fund_id": fund_id},
            )

        self._record_audit(actor, action, resource_type, resource_id, "ALLOW", {"fund_id": fund_id})

    def authorize_report_read(self, actor: Actor, report: ReportRecord, action: Action = Action.READ_REPORT) -> None:
        """Restrict VIEWER_INVESTOR to PUBLISHED/SUPERSEDED INVESTOR-audience reports only."""
        self.authorize(
            actor,
            action,
            resource_type="report",
            resource_id=report.version_id,
            fund_id=report.fund_id,
        )
        if actor.role == Role.VIEWER_INVESTOR:
            if report.audience != ReportAudience.INVESTOR:
                self._record_audit(
                    actor,
                    action,
                    "report",
                    report.version_id,
                    "DENY",
                    {"reason": "Investor role cannot access INTERNAL reports"},
                )
                raise AuthorizationError(
                    "Investor role cannot access INTERNAL audience reports",
                    details={"version_id": report.version_id, "audience": report.audience.value},
                )
            if report.status not in (ReportStatus.PUBLISHED, ReportStatus.SUPERSEDED):
                self._record_audit(
                    actor,
                    action,
                    "report",
                    report.version_id,
                    "DENY",
                    {"reason": f"Investor role cannot access unpublished report ({report.status.value})"},
                )
                raise AuthorizationError(
                    f"Investor role cannot access report in status {report.status.value}",
                    details={"version_id": report.version_id, "status": report.status.value},
                )

    def enforce_separation_of_duties(
        self,
        actor: Actor,
        *,
        action: Action,
        resource_id: str,
        prohibited_actor_ids: Set[str],
        reason: str,
    ) -> None:
        if not self.require_dual_control:
            return
        if actor.actor_id in prohibited_actor_ids:
            self._record_audit(
                actor,
                action,
                "report",
                resource_id,
                "DENY",
                {"reason": reason, "prohibited_actor_ids": sorted(prohibited_actor_ids)},
            )
            raise AuthorizationError(
                reason,
                code="SEPARATION_OF_DUTIES_VIOLATION",
                details={"actor_id": actor.actor_id, "resource_id": resource_id},
            )
