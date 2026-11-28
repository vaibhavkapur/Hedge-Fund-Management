"""Hash-chained append-only audit log, structured logger, and diagnostic counters."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple

from .models import AuditEvent, format_iso_datetime

GENESIS_HASH = "0" * 64


def compute_audit_hash(
    sequence: int,
    event_id: str,
    timestamp: datetime,
    actor_id: str,
    action: str,
    entity_type: str,
    entity_id: str,
    payload: Dict[str, Any],
    prev_hash: str,
) -> str:
    canonical = json.dumps(
        {
            "sequence": sequence,
            "event_id": event_id,
            "timestamp": format_iso_datetime(timestamp),
            "actor_id": actor_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": payload,
            "prev_hash": prev_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_audit_chain(events: Iterable[AuditEvent]) -> Tuple[bool, List[str]]:
    """Verify monotonic sequence numbers and SHA-256 hash links across all audit events."""
    errors: List[str] = []
    expected_prev = GENESIS_HASH
    expected_seq = 1
    for evt in events:
        if evt.sequence != expected_seq:
            errors.append(f"Sequence gap at event {evt.event_id}: expected {expected_seq}, got {evt.sequence}.")
        if evt.prev_hash != expected_prev:
            errors.append(f"Broken prev_hash at sequence {evt.sequence} ({evt.event_id}).")
        recomputed = compute_audit_hash(
            sequence=evt.sequence,
            event_id=evt.event_id,
            timestamp=evt.timestamp,
            actor_id=evt.actor_id,
            action=evt.action,
            entity_type=evt.entity_type,
            entity_id=evt.entity_id,
            payload=evt.payload,
            prev_hash=evt.prev_hash,
        )
        if recomputed != evt.entry_hash:
            errors.append(f"Tampered entry_hash at sequence {evt.sequence} ({evt.event_id}).")
        expected_prev = evt.entry_hash
        expected_seq = evt.sequence + 1
    return len(errors) == 0, errors


class DiagnosticTracker:
    """In-memory structured event counters and optional JSON log emission."""

    def __init__(self, logger_name: str = "isrp") -> None:
        self._counters: Counter[str] = Counter()
        self._logger = logging.getLogger(logger_name)
        self._structured_logs: List[Dict[str, Any]] = []

    def increment(self, metric: str, value: int = 1) -> None:
        self._counters[metric] += value

    def record_log(self, level: str, event: str, **fields: Any) -> None:
        entry = {"level": level, "event": event, **fields}
        self._structured_logs.append(entry)
        log_fn = getattr(self._logger, level.lower(), self._logger.info)
        log_fn(json.dumps(entry, sort_keys=True))

    def snapshot_counters(self) -> Dict[str, int]:
        return dict(sorted(self._counters.items()))

    def get_logs(self) -> List[Dict[str, Any]]:
        return list(self._structured_logs)
