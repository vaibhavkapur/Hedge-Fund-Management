"""Tests for investor lifecycle state machine and deterministic mock onboarding checks."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from isrp.config import load_config
from isrp.errors import InvalidStateTransitionError, ValidationError
from isrp.models import ExceptionType, InvestorStatus
from isrp.repository import SQLiteRepository
from isrp.service import PlatformService


class TestOnboardingStateMachine(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(PROJECT_ROOT / "config" / "default_config.json")
        self.service = PlatformService(self.config, SQLiteRepository(":memory:"))
        self.now = datetime(2026, 3, 10, 9, 0, 0, tzinfo=timezone.utc)

    def test_valid_investor_lifecycle_and_checks(self) -> None:
        inv = self.service.register_investor(
            investor_id="INV-OK-01",
            legal_name="Evergreen Synthetic Endowment",
            investor_type="INSTITUTIONAL",
            jurisdiction="US",
            tax_form_status="VERIFIED",
            accredited=True,
            bank_account_ref="SYN-ACCT-5001",
            identity_doc_ref="SYN-DOC-5001",
            now=self.now,
        )
        self.assertEqual(inv.status, InvestorStatus.PROSPECT)

        # Cannot jump directly from PROSPECT to ACTIVE
        with self.assertRaises(InvalidStateTransitionError):
            self.service.activate_investor("INV-OK-01", now=self.now)

        checked = self.service.run_onboarding("INV-OK-01", now=self.now)
        self.assertEqual(checked.status, InvestorStatus.KYC_APPROVED)
        self.assertEqual(len(checked.onboarding_checks), 5)
        self.assertTrue(all(c.passed for c in checked.onboarding_checks))

        active = self.service.activate_investor("INV-OK-01", now=self.now)
        self.assertEqual(active.status, InvestorStatus.ACTIVE)

        suspended = self.service.suspend_investor("INV-OK-01", actor_id="compliance_01", reason="Periodic review", now=self.now)
        self.assertEqual(suspended.status, InvestorStatus.SUSPENDED)

        reactivated = self.service.activate_investor("INV-OK-01", now=self.now)
        self.assertEqual(reactivated.status, InvestorStatus.ACTIVE)

    def test_sanctioned_investor_is_kyc_rejected_and_blocked_from_subscribing(self) -> None:
        self.service.register_investor(
            investor_id="INV-SYN-BAD-001",
            legal_name="Blocked Entity Synthetic LLC",
            investor_type="INSTITUTIONAL",
            jurisdiction="SYN-SANCTIONED",
            tax_form_status="VERIFIED",
            accredited=True,
            bank_account_ref="SYN-ACCT-9999",
            identity_doc_ref="SYN-DOC-9999",
            now=self.now,
        )
        checked = self.service.run_onboarding("INV-SYN-BAD-001", now=self.now)
        self.assertEqual(checked.status, InvestorStatus.KYC_REJECTED)
        failed = [c.check_name for c in checked.onboarding_checks if not c.passed]
        self.assertIn("MOCK_SANCTIONS_AML_SCREEN", failed)

        with self.assertRaises(InvalidStateTransitionError):
            self.service.activate_investor("INV-SYN-BAD-001", now=self.now)

        self.service.register_dealing_window(
            window_id="DW-ONB-01",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            dealing_date="2026-03-31",
            open_at="2026-03-01T00:00:00Z",
            cutoff_at="2026-03-31T16:00:00Z",
            cash_due_date="2026-04-02",
            settlement_date="2026-04-02",
            now=self.now,
        )
        with self.assertRaises(ValidationError):
            self.service.submit_request(
                request_id="REQ-BLOCKED-KYC",
                idempotency_key="IDEM-BLOCKED-KYC",
                investor_id="INV-SYN-BAD-001",
                fund_id="FUND-ALPHA",
                share_class="CLASS-A",
                request_type="SUBSCRIPTION",
                dealing_date="2026-03-31",
                currency="USD",
                gross_amount="25000.00",
                submitted_at="2026-03-20T10:00:00Z",
                maker_id="ops_maker_01",
            )
        excs = self.service.repo.list_exceptions("REQ-BLOCKED-KYC")
        self.assertEqual(len(excs), 1)
        self.assertEqual(excs[0].exception_type, ExceptionType.KYC_NOT_ACTIVE)


if __name__ == "__main__":
    unittest.main()
