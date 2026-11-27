"""Immutable hash-chained audit trail and domain event contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hf_platform.domain.enums import EventType, Role
from hf_platform.domain.master import ensure_utc


class AuditLogEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    audit_id: str
    sequence_number: int
    timestamp_utc: datetime
    actor_id: str
    actor_role: Role | None = None
    action: str
    entity_type: str
    entity_id: str
    outcome: str  # SUCCESS | DENIED | FAILED
    details: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str
    entry_hash: str = ""

    @field_validator("timestamp_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    def compute_hash(self) -> str:
        payload = {
            "audit_id": self.audit_id,
            "sequence_number": self.sequence_number,
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "actor_id": self.actor_id,
            "actor_role": self.actor_role.value if self.actor_role else None,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "outcome": self.outcome,
            "details": self.details,
            "prev_hash": self.prev_hash,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    event_type: EventType
    schema_version: str = "1.0.0"
    occurred_at_utc: datetime
    fund_id: str | None = None
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)
