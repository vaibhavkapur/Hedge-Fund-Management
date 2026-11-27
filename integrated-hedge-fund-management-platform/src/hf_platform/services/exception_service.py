"""Operational exception management and maker-checker approval workflow service."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from hf_platform.config import PlatformConfig
from hf_platform.domain import (
    ApprovalAction,
    BreakType,
    EventType,
    ExceptionStatus,
    OperationalException,
    Severity,
)
from hf_platform.errors import DomainValidationError, NotFoundError
from hf_platform.services.audit_service import AuditService
from hf_platform.services.auth_service import AuthService
from hf_platform.storage.repository import StorageRepository


class ExceptionService:
    """Manages exception lifecycle with SLA tracking and strict role-separated approvals."""

    def __init__(
        self,
        store: StorageRepository,
        auth: AuthService,
        audit: AuditService,
        config: PlatformConfig,
    ) -> None:
        self._store = store
        self._auth = auth
        self._audit = audit
        self._config = config

    def open_exception(
        self,
        *,
        fund_id: str,
        business_date: date,
        category: BreakType,
        severity: Severity,
        title: str,
        description: str,
        source_ref: str,
        actor_id: str,
        financial_impact_usd: Decimal = Decimal("0.00"),
        assigned_to: str | None = None,
    ) -> OperationalException:
        # Idempotency check by (fund_id, business_date, source_ref) so repeated close runs don't duplicate
        for existing in self._store.list_exceptions(fund_id=fund_id, business_date=business_date):
            if existing.source_ref == source_ref and existing.category == category:
                return existing

        now = datetime.now(UTC)
        sla_hrs = self._config.sla_hours.get(severity.value, 24)
        exc = OperationalException(
            exception_id=f"EXC_{business_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}",
            fund_id=fund_id,
            business_date=business_date,
            category=category,
            severity=severity,
            status=ExceptionStatus.OPEN,
            title=title,
            description=description,
            financial_impact_usd=financial_impact_usd,
            source_ref=source_ref,
            created_by=actor_id,
            created_at_utc=now,
            sla_due_at_utc=now + timedelta(hours=sla_hrs),
            assigned_to=assigned_to,
        )
        self._store.save_exception(exc)
        user = self._store.get_user(actor_id)
        self._audit.record(
            actor_id=actor_id,
            actor_role=user.role if user else None,
            action="exception.open",
            entity_type="exception",
            entity_id=exc.exception_id,
            details={
                "category": category.value,
                "severity": severity.value,
                "source_ref": source_ref,
                "impact_usd": str(financial_impact_usd),
            },
        )
        self._audit.emit_event(
            event_type=EventType.EXCEPTION_CREATED,
            fund_id=fund_id,
            payload={"exception_id": exc.exception_id, "category": category.value, "severity": severity.value},
        )
        return exc

    def propose_resolution(
        self,
        *,
        exception_id: str,
        resolution_notes: str,
        actor_id: str,
    ) -> OperationalException:
        exc = self._require(exception_id)
        user = self._auth.authorize(
            actor_id=actor_id,
            action="exception.propose_resolution",
            entity_type="exception",
            entity_id=exception_id,
            fund_id=exc.fund_id,
        )
        if exc.status in (ExceptionStatus.RESOLVED, ExceptionStatus.WAIVED):
            raise DomainValidationError(f"Exception {exception_id} is already closed ({exc.status.value})")
        updated = exc.model_copy(
            update={
                "status": ExceptionStatus.PENDING_APPROVAL,
                "proposed_resolution": resolution_notes,
                "proposed_by": user.user_id,
                "proposed_at_utc": datetime.now(UTC),
            }
        )
        self._store.save_exception(updated)
        self._audit.record(
            actor_id=user.user_id,
            actor_role=user.role,
            action="exception.propose_resolution",
            entity_type="exception",
            entity_id=exception_id,
            details={"resolution_notes": resolution_notes},
        )
        self._audit.emit_event(
            event_type=EventType.EXCEPTION_PROPOSED,
            fund_id=exc.fund_id,
            payload={"exception_id": exception_id, "proposed_by": user.user_id},
        )
        return updated

    def decide_exception(
        self,
        *,
        exception_id: str,
        action: ApprovalAction,
        decision_notes: str,
        actor_id: str,
    ) -> OperationalException:
        exc = self._require(exception_id)
        if exc.status != ExceptionStatus.PENDING_APPROVAL or exc.proposed_by is None:
            raise DomainValidationError(
                f"Exception {exception_id} must have a proposed resolution before approval decision",
                details={"status": exc.status.value},
            )
        # Strict maker-checker: checker cannot be the same principal who proposed resolution
        checker = self._auth.authorize(
            actor_id=actor_id,
            action="exception.approve",
            entity_type="exception",
            entity_id=exception_id,
            maker_id=exc.proposed_by,
            fund_id=exc.fund_id,
        )
        if action == ApprovalAction.APPROVE:
            new_status = ExceptionStatus.RESOLVED
            evt_type = EventType.EXCEPTION_APPROVED
        elif action == ApprovalAction.WAIVE:
            new_status = ExceptionStatus.WAIVED
            evt_type = EventType.EXCEPTION_APPROVED
        else:
            new_status = ExceptionStatus.REJECTED
            evt_type = EventType.EXCEPTION_REJECTED

        updated = exc.model_copy(
            update={
                "status": new_status,
                "decided_by": checker.user_id,
                "decided_at_utc": datetime.now(UTC),
                "decision_action": action,
                "decision_notes": decision_notes,
            }
        )
        self._store.save_exception(updated)
        self._audit.record(
            actor_id=checker.user_id,
            actor_role=checker.role,
            action=f"exception.{action.value.lower()}",
            entity_type="exception",
            entity_id=exception_id,
            details={
                "maker_id": exc.proposed_by,
                "checker_id": checker.user_id,
                "new_status": new_status.value,
                "notes": decision_notes,
            },
        )
        self._audit.emit_event(
            event_type=evt_type,
            fund_id=exc.fund_id,
            payload={
                "exception_id": exception_id,
                "action": action.value,
                "new_status": new_status.value,
                "decided_by": checker.user_id,
            },
        )
        return updated

    def list_blocking_exceptions(self, fund_id: str, business_date: date) -> list[OperationalException]:
        all_exc = self._store.list_exceptions(fund_id=fund_id, business_date=business_date)
        return [
            e
            for e in all_exc
            if e.status not in (ExceptionStatus.RESOLVED, ExceptionStatus.WAIVED)
            and e.severity in (Severity.CRITICAL, Severity.HIGH)
        ]

    def list_exceptions(
        self, fund_id: str | None = None, business_date: date | None = None
    ) -> list[OperationalException]:
        return self._store.list_exceptions(fund_id=fund_id, business_date=business_date)

    def _require(self, exception_id: str) -> OperationalException:
        exc = self._store.get_exception(exception_id)
        if exc is None:
            raise NotFoundError(f"Exception not found: {exception_id}")
        return exc
