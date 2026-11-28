"""Request state machine, cutoff checks, maker-checker enforcement, and exception review rules."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, FrozenSet, Optional

from .errors import (
    CutoffViolationError,
    InvalidStateTransitionError,
    ReviewerSeparationError,
    UnapprovedRequestError,
    ValidationError,
)
from .models import (
    CapitalFlowRequest,
    DealingWindow,
    ExceptionCase,
    ExceptionStatus,
    RequestStatus,
)

ALLOWED_REQUEST_TRANSITIONS: Dict[RequestStatus, FrozenSet[RequestStatus]] = {
    RequestStatus.DRAFT: frozenset({RequestStatus.SUBMITTED, RequestStatus.CANCELLED}),
    RequestStatus.SUBMITTED: frozenset(
        {
            RequestStatus.VALIDATED,
            RequestStatus.PENDING_APPROVAL,
            RequestStatus.EXCEPTION_REVIEW,
            RequestStatus.REJECTED,
            RequestStatus.CANCELLED,
        }
    ),
    RequestStatus.VALIDATED: frozenset(
        {
            RequestStatus.PENDING_APPROVAL,
            RequestStatus.EXCEPTION_REVIEW,
            RequestStatus.CANCELLED,
        }
    ),
    RequestStatus.PENDING_APPROVAL: frozenset(
        {
            RequestStatus.APPROVED,
            RequestStatus.REJECTED,
            RequestStatus.CANCELLED,
            RequestStatus.EXCEPTION_REVIEW,
        }
    ),
    RequestStatus.EXCEPTION_REVIEW: frozenset(
        {
            RequestStatus.PENDING_APPROVAL,
            RequestStatus.APPROVED,
            RequestStatus.CASH_CONFIRMED,
            RequestStatus.REJECTED,
            RequestStatus.CANCELLED,
        }
    ),
    RequestStatus.APPROVED: frozenset(
        {
            RequestStatus.CASH_PENDING,
            RequestStatus.CASH_CONFIRMED,
            RequestStatus.PRICED,
            RequestStatus.EXCEPTION_REVIEW,
            RequestStatus.CANCELLED,
        }
    ),
    RequestStatus.CASH_PENDING: frozenset(
        {
            RequestStatus.CASH_CONFIRMED,
            RequestStatus.EXCEPTION_REVIEW,
            RequestStatus.CANCELLED,
        }
    ),
    RequestStatus.CASH_CONFIRMED: frozenset(
        {
            RequestStatus.PRICED,
            RequestStatus.SETTLED,
            RequestStatus.EXCEPTION_REVIEW,
            RequestStatus.CANCELLED,
        }
    ),
    RequestStatus.PRICED: frozenset(
        {
            RequestStatus.CASH_PENDING,
            RequestStatus.CASH_CONFIRMED,
            RequestStatus.SETTLED,
            RequestStatus.EXCEPTION_REVIEW,
            RequestStatus.CANCELLED,
        }
    ),
    RequestStatus.SETTLED: frozenset({RequestStatus.RESTATED}),
    RequestStatus.RESTATED: frozenset({RequestStatus.RESTATED}),
    RequestStatus.REJECTED: frozenset(),
    RequestStatus.CANCELLED: frozenset(),
}

CANCELLABLE_STATUSES: FrozenSet[RequestStatus] = frozenset(
    {
        RequestStatus.DRAFT,
        RequestStatus.SUBMITTED,
        RequestStatus.VALIDATED,
        RequestStatus.PENDING_APPROVAL,
        RequestStatus.EXCEPTION_REVIEW,
        RequestStatus.APPROVED,
        RequestStatus.CASH_PENDING,
        RequestStatus.CASH_CONFIRMED,
        RequestStatus.PRICED,
    }
)

ACTIVE_REDEMPTION_LOCK_STATUSES: FrozenSet[RequestStatus] = frozenset(
    {
        RequestStatus.SUBMITTED,
        RequestStatus.VALIDATED,
        RequestStatus.PENDING_APPROVAL,
        RequestStatus.EXCEPTION_REVIEW,
        RequestStatus.APPROVED,
        RequestStatus.CASH_PENDING,
        RequestStatus.CASH_CONFIRMED,
        RequestStatus.PRICED,
    }
)


def validate_request_transition(current: RequestStatus, target: RequestStatus) -> None:
    if target not in ALLOWED_REQUEST_TRANSITIONS[current]:
        raise InvalidStateTransitionError(
            f"Cannot transition request from {current.value} to {target.value}.",
            {"current_status": current.value, "target_status": target.value},
        )


def ensure_reviewer_separation(maker_id: str, checker_id: str, context: str = "approval") -> None:
    if not maker_id or not maker_id.strip():
        raise ValidationError("maker_id must be non-empty.", {"field": "maker_id"})
    if not checker_id or not checker_id.strip():
        raise ValidationError("checker_id must be non-empty.", {"field": "checker_id"})
    if maker_id.strip().lower() == checker_id.strip().lower():
        raise ReviewerSeparationError(
            f"Maker-checker violation during {context}: actor '{checker_id}' cannot review/approve their own action.",
            {"maker_id": maker_id, "checker_id": checker_id, "context": context},
        )


def ensure_request_approved(request: CapitalFlowRequest, require_maker_checker: bool = True) -> None:
    if not request.checker_id or request.approved_at is None:
        raise UnapprovedRequestError(
            f"Request '{request.request_id}' has not received maker-checker approval and cannot settle.",
            {"request_id": request.request_id, "status": request.status.value},
        )
    if require_maker_checker:
        ensure_reviewer_separation(request.maker_id, request.checker_id, context="settlement")


def check_dealing_window_cutoff(submitted_at: datetime, window: DealingWindow) -> None:
    if submitted_at < window.open_at:
        raise CutoffViolationError(
            f"Request submitted at {submitted_at.isoformat()} is before dealing window open {window.open_at.isoformat()}.",
            {"window_id": window.window_id, "open_at": window.open_at.isoformat(), "submitted_at": submitted_at.isoformat()},
        )
    if submitted_at > window.cutoff_at:
        raise CutoffViolationError(
            f"Request submitted at {submitted_at.isoformat()} breached dealing cutoff {window.cutoff_at.isoformat()}.",
            {"window_id": window.window_id, "cutoff_at": window.cutoff_at.isoformat(), "submitted_at": submitted_at.isoformat()},
        )


def ensure_cancellable(request: CapitalFlowRequest) -> None:
    if request.status not in CANCELLABLE_STATUSES:
        raise InvalidStateTransitionError(
            f"Request '{request.request_id}' in status {request.status.value} cannot be cancelled; settled entries require restatement.",
            {"request_id": request.request_id, "status": request.status.value},
        )


def validate_exception_resolution(
    exc_case: ExceptionCase,
    reviewer_id: str,
    target_status: ExceptionStatus,
    resolution_note: Optional[str],
) -> None:
    if exc_case.status in (ExceptionStatus.RESOLVED, ExceptionStatus.REJECTED):
        raise InvalidStateTransitionError(
            f"Exception '{exc_case.exception_id}' is already finalized in status {exc_case.status.value}.",
            {"exception_id": exc_case.exception_id, "status": exc_case.status.value},
        )
    if target_status not in (ExceptionStatus.UNDER_REVIEW, ExceptionStatus.RESOLVED, ExceptionStatus.REJECTED):
        raise InvalidStateTransitionError(
            f"Invalid target status '{target_status.value}' for exception '{exc_case.exception_id}'.",
            {"exception_id": exc_case.exception_id, "target_status": target_status.value},
        )
    ensure_reviewer_separation(exc_case.created_by, reviewer_id, context="exception_review")
    if target_status in (ExceptionStatus.RESOLVED, ExceptionStatus.REJECTED) and (
        not resolution_note or not resolution_note.strip()
    ):
        raise ValidationError("resolution_note is required when resolving or rejecting an exception.")
