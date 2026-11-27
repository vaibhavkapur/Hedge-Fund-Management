"""Versioned reporting contracts anchored to immutable frozen snapshots."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hf_platform.domain.enums import ReportStatus, ReportType
from hf_platform.domain.master import ensure_utc


class ReportRevisionDiff(BaseModel):
    model_config = ConfigDict(frozen=True)

    prior_report_id: str
    prior_version: int
    new_version: int
    reason: str
    changed_fields: dict[str, dict[str, Any]]


class ReportArtifact(BaseModel):
    model_config = ConfigDict(frozen=True)

    report_id: str
    fund_id: str
    business_date: date
    report_type: ReportType
    version: int = 1
    status: ReportStatus = ReportStatus.DRAFT
    snapshot_id: str
    snapshot_content_hash: str
    generated_by: str
    generated_at_utc: datetime
    approved_by: str | None = None
    approved_at_utc: datetime | None = None
    supersedes_report_id: str | None = None
    revision_reason: str | None = None
    revision_diff: ReportRevisionDiff | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    content_hash: str = ""

    @field_validator("generated_at_utc", mode="after")
    @classmethod
    def _utc_gen(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @field_validator("approved_at_utc", mode="after")
    @classmethod
    def _utc_app(cls, v: datetime | None) -> datetime | None:
        return ensure_utc(v) if v is not None else None

    def compute_content_hash(self) -> str:
        data = {
            "report_id": self.report_id,
            "fund_id": self.fund_id,
            "business_date": self.business_date.isoformat(),
            "report_type": self.report_type.value,
            "version": self.version,
            "snapshot_id": self.snapshot_id,
            "snapshot_content_hash": self.snapshot_content_hash,
            "supersedes_report_id": self.supersedes_report_id,
            "revision_reason": self.revision_reason,
            "payload": self.payload,
        }
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
