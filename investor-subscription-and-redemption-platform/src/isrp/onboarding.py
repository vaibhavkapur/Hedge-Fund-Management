"""Investor lifecycle state machine and deterministic mock onboarding checks.

DISCLAIMER: All onboarding checks in this module are synthetic educational mocks.
Real KYC, AML, sanctions screening, and investor eligibility require licensed
compliance providers and legal review.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Dict, FrozenSet, List, Tuple

from .config import WorkflowPolicy
from .errors import InvalidStateTransitionError, ValidationError
from .models import InvestorRecord, InvestorStatus, OnboardingCheckResult

ALLOWED_INVESTOR_TRANSITIONS: Dict[InvestorStatus, FrozenSet[InvestorStatus]] = {
    InvestorStatus.PROSPECT: frozenset({InvestorStatus.ONBOARDING_PENDING, InvestorStatus.CLOSED}),
    InvestorStatus.ONBOARDING_PENDING: frozenset({InvestorStatus.KYC_APPROVED, InvestorStatus.KYC_REJECTED}),
    InvestorStatus.KYC_APPROVED: frozenset({InvestorStatus.ACTIVE, InvestorStatus.SUSPENDED, InvestorStatus.CLOSED}),
    InvestorStatus.KYC_REJECTED: frozenset({InvestorStatus.ONBOARDING_PENDING, InvestorStatus.CLOSED}),
    InvestorStatus.ACTIVE: frozenset({InvestorStatus.SUSPENDED, InvestorStatus.CLOSED}),
    InvestorStatus.SUSPENDED: frozenset({InvestorStatus.ACTIVE, InvestorStatus.CLOSED}),
    InvestorStatus.CLOSED: frozenset(),
}

ELIGIBLE_INVESTOR_TYPES: FrozenSet[str] = frozenset(
    {"INSTITUTIONAL", "QUALIFIED_PURCHASER", "ACCREDITED_INDIVIDUAL", "SOVEREIGN_ENTITY"}
)


def validate_investor_transition(current: InvestorStatus, target: InvestorStatus) -> None:
    if target not in ALLOWED_INVESTOR_TRANSITIONS[current]:
        raise InvalidStateTransitionError(
            f"Cannot transition investor from {current.value} to {target.value}.",
            {"current_status": current.value, "target_status": target.value},
        )


def run_mock_onboarding_checks(
    investor: InvestorRecord,
    policy: WorkflowPolicy,
) -> Tuple[bool, Tuple[OnboardingCheckResult, ...]]:
    """Run deterministic synthetic KYC/AML/eligibility checks."""
    checks: List[OnboardingCheckResult] = []

    # 1. Synthetic identity document format check
    id_ok = bool(investor.identity_doc_ref and investor.identity_doc_ref.startswith("SYN-DOC-"))
    checks.append(
        OnboardingCheckResult(
            check_name="MOCK_IDENTITY_VERIFICATION",
            passed=id_ok,
            reason="Synthetic identity document prefix verified." if id_ok else "Missing or invalid synthetic document reference (expected SYN-DOC-*).",
        )
    )

    # 2. Mock sanctions / jurisdiction screening
    jurisdiction_blocked = investor.jurisdiction.upper() in policy.sanctioned_jurisdictions
    name_blocked = investor.legal_name.strip().lower() in policy.sanctioned_names
    aml_ok = not (jurisdiction_blocked or name_blocked)
    checks.append(
        OnboardingCheckResult(
            check_name="MOCK_SANCTIONS_AML_SCREEN",
            passed=aml_ok,
            reason="Cleared mock sanctions screening."
            if aml_ok
            else f"Flagged by mock sanctions policy (jurisdiction={investor.jurisdiction}, legal_name={investor.legal_name}).",
        )
    )

    # 3. Accredited / qualified eligibility check
    elig_ok = investor.accredited and investor.investor_type.upper() in ELIGIBLE_INVESTOR_TYPES
    checks.append(
        OnboardingCheckResult(
            check_name="MOCK_INVESTOR_ELIGIBILITY",
            passed=elig_ok,
            reason="Investor meets synthetic eligibility criteria."
            if elig_ok
            else f"Investor is not accredited or investor_type '{investor.investor_type}' is unsupported.",
        )
    )

    # 4. Tax documentation check
    tax_ok = investor.tax_form_status.upper() == "VERIFIED"
    checks.append(
        OnboardingCheckResult(
            check_name="MOCK_TAX_FORM_CHECK",
            passed=tax_ok,
            reason="Synthetic tax documentation verified." if tax_ok else f"Tax form status is '{investor.tax_form_status}', expected 'VERIFIED'.",
        )
    )

    # 5. Bank account reference check
    bank_ok = bool(investor.bank_account_ref and investor.bank_account_ref.startswith("SYN-ACCT-"))
    checks.append(
        OnboardingCheckResult(
            check_name="MOCK_BANK_ACCOUNT_VERIFICATION",
            passed=bank_ok,
            reason="Synthetic bank account reference verified." if bank_ok else "Invalid synthetic bank account reference (expected SYN-ACCT-*).",
        )
    )

    all_passed = all(c.passed for c in checks)
    return all_passed, tuple(checks)


def create_prospect_investor(
    investor_id: str,
    legal_name: str,
    investor_type: str,
    jurisdiction: str,
    tax_form_status: str,
    accredited: bool,
    bank_account_ref: str,
    identity_doc_ref: str,
    created_at: datetime,
) -> InvestorRecord:
    if not investor_id or not investor_id.strip():
        raise ValidationError("investor_id must be a non-empty string.", {"field": "investor_id"})
    if not legal_name or not legal_name.strip():
        raise ValidationError("legal_name must be a non-empty string.", {"field": "legal_name"})
    if not jurisdiction or not jurisdiction.strip():
        raise ValidationError("jurisdiction must be a non-empty string.", {"field": "jurisdiction"})
    return InvestorRecord(
        investor_id=investor_id.strip(),
        legal_name=legal_name.strip(),
        investor_type=investor_type.strip().upper(),
        jurisdiction=jurisdiction.strip().upper(),
        tax_form_status=tax_form_status.strip().upper(),
        accredited=bool(accredited),
        bank_account_ref=bank_account_ref.strip(),
        identity_doc_ref=identity_doc_ref.strip(),
        status=InvestorStatus.PROSPECT,
        version=1,
        onboarding_checks=(),
        updated_at=created_at,
    )


def evaluate_investor_onboarding(
    investor: InvestorRecord,
    policy: WorkflowPolicy,
    evaluated_at: datetime,
) -> InvestorRecord:
    """Advance PROSPECT -> ONBOARDING_PENDING -> KYC_APPROVED or KYC_REJECTED."""
    working = investor
    if working.status in (InvestorStatus.PROSPECT, InvestorStatus.KYC_REJECTED):
        validate_investor_transition(working.status, InvestorStatus.ONBOARDING_PENDING)
        working = replace(
            working,
            status=InvestorStatus.ONBOARDING_PENDING,
            version=working.version + 1,
            updated_at=evaluated_at,
        )
    elif working.status != InvestorStatus.ONBOARDING_PENDING:
        raise InvalidStateTransitionError(
            f"Cannot evaluate onboarding for investor in status {working.status.value}.",
            {"investor_id": investor.investor_id, "status": working.status.value},
        )

    passed, checks = run_mock_onboarding_checks(working, policy)
    next_status = InvestorStatus.KYC_APPROVED if passed else InvestorStatus.KYC_REJECTED
    validate_investor_transition(working.status, next_status)
    return replace(
        working,
        status=next_status,
        version=working.version + 1,
        onboarding_checks=checks,
        updated_at=evaluated_at,
    )


def transition_investor_status(
    investor: InvestorRecord,
    target_status: InvestorStatus,
    updated_at: datetime,
) -> InvestorRecord:
    validate_investor_transition(investor.status, target_status)
    return replace(
        investor,
        status=target_status,
        version=investor.version + 1,
        updated_at=updated_at,
    )
