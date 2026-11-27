"""Daily close checklist, exception management, and maker-checker approval contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hf_platform.domain.enums import (
    ApprovalAction,
    BreakType,
    CloseStatus,
    CloseStepStatus,
    ExceptionStatus,
    Severity,
)
from hf_platform.domain.master import ensure_utc


class CloseStepResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    step_name: str
    order: int
    status: CloseStepStatus
    message: str
    started_at_utc: datetime
    completed_at_utc: datetime
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("started_at_utc", "completed_at_utc", mode="after")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return ensure_utc(v)


class DailyCloseRun(BaseModel):
    model_config = ConfigDict(frozen=True)

    close_run_id: str
    fund_id: str
    business_date: date
    attempt: int = 1
    status: CloseStatus
    initiated_by: str
    initiated_at_utc: datetime
    completed_at_utc: datetime | None = None
    steps: list[CloseStepResult] = Field(default_factory=list)
    reconciliation_id: str | None = None
    snapshot_id: str | None = None
    blocking_exception_ids: list[str] = Field(default_factory=list)
    late_fill_ids: list[str] = Field(default_factory=list)
    signoff_preparer_id: str | None = None
    signoff_approver_id: str | None = None
    signoff_at_utc: datetime | None = None
    failure_reason: str | None = None

    @field_validator("initiated_at_utc", mode="after")
    @classmethod
    def _utc_init(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @field_validator("completed_at_utc", "signoff_at_utc", mode="after")
    @classmethod
    def _utc_opt(cls, v: datetime | None) -> datetime | None:
        return ensure_utc(v) if v is not None else None


class OperationalException(BaseModel):
    model_config = ConfigDict(frozen=True)

    exception_id: str
    fund_id: str
    business_date: date
    category: BreakType
    severity: Severity
    status: ExceptionStatus = ExceptionStatus.OPEN
    title: str
    description: str
    financial_impact_usd: Decimal = Decimal("0.00")
    source_ref: str
    created_by: str
    created_at_utc: datetime
    sla_due_at_utc: datetime
    assigned_to: str | None = None
    proposed_resolution: str | None = None
    proposed_by: str | None = None
    proposed_at_utc: datetime | None = None
    decided_by: str | None = None
    decided_at_utc: datetime | None = None
    decision_action: ApprovalAction | None = None
    decision_notes: str | None = None

    @field_validator("created_at_utc", "sla_due_at_utc", mode="after")
    @classmethod
    def _utc_req(cls, v: datetime) -> datetime:
        return ensure_utc(v)

    @field_validator("proposed_at_utc", "decided_at_utc", mode="after")
    @classmethod
    def _utc_opt(cls, v: datetime | None) -> datetime | None:
        return ensure_utc(v) if v is not None else None
