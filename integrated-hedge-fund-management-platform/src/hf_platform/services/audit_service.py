"""Append-only hash-chained audit trail and domain event publishing service."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from hf_platform.adapters.interfaces import EventPublisherAdapter
from hf_platform.domain import AuditLogEntry, DomainEvent, EventType, Role
from hf_platform.errors import AuditIntegrityError
from hf_platform.storage.repository import StorageRepository

GENESIS_HASH = "0" * 64


class AuditService:
    """Records tamper-evident hash-chained audit logs and emits structured domain events."""

    def __init__(self, store: StorageRepository, event_publisher: EventPublisherAdapter) -> None:
        self._store = store
        self._event_publisher = event_publisher

    def record(
        self,
        *,
        actor_id: str,
        actor_role: Role | None,
        action: str,
        entity_type: str,
        entity_id: str,
        outcome: str = "SUCCESS",
        details: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        last = self._store.get_last_audit_entry()
        seq = (last.sequence_number + 1) if last else 1
        prev_hash = last.entry_hash if last else GENESIS_HASH
        draft = AuditLogEntry(
            audit_id=f"AUD_{seq:06d}_{uuid.uuid4().hex[:8]}",
            sequence_number=seq,
            timestamp_utc=datetime.now(UTC),
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            outcome=outcome,
            details=details or {},
            prev_hash=prev_hash,
            entry_hash="",
        )
        final_entry = draft.model_copy(update={"entry_hash": draft.compute_hash()})
        self._store.append_audit_entry(final_entry)
        return final_entry

    def emit_event(
        self,
        *,
        event_type: EventType,
        fund_id: str | None = None,
        correlation_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> DomainEvent:
        event = DomainEvent(
            event_id=f"EVT_{uuid.uuid4().hex[:12]}",
            event_type=event_type,
            occurred_at_utc=datetime.now(UTC),
            fund_id=fund_id,
            correlation_id=correlation_id,
            payload=payload or {},
        )
        self._store.append_event(event)
        self._event_publisher.publish(event)
        return event

    def verify_chain(self) -> dict[str, Any]:
        entries = self._store.list_audit_entries()
        expected_prev = GENESIS_HASH
        for idx, entry in enumerate(entries, start=1):
            if entry.sequence_number != idx:
                raise AuditIntegrityError(
                    f"Audit sequence gap at index {idx}: found {entry.sequence_number}",
                    details={"expected_seq": idx, "actual_seq": entry.sequence_number},
                )
            if entry.prev_hash != expected_prev:
                raise AuditIntegrityError(
                    f"Audit prev_hash mismatch at sequence {entry.sequence_number}",
                    details={"sequence_number": entry.sequence_number, "audit_id": entry.audit_id},
                )
            recomputed = entry.compute_hash()
            if entry.entry_hash != recomputed:
                raise AuditIntegrityError(
                    f"Audit entry hash tampered at sequence {entry.sequence_number}",
                    details={"sequence_number": entry.sequence_number, "audit_id": entry.audit_id},
                )
            expected_prev = entry.entry_hash
        return {
            "verified": True,
            "entry_count": len(entries),
            "head_hash": expected_prev,
        }

    def list_entries(self, limit: int | None = None) -> list[AuditLogEntry]:
        return self._store.list_audit_entries(limit=limit)

    def list_events(self, limit: int | None = None) -> list[DomainEvent]:
        return self._store.list_events(limit=limit)
