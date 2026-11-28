"""Domain correctness unit tests: duplicate requests, delayed cash, and cash mismatches."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from isrp.config import load_config
from isrp.errors import DuplicateRequestError, ReconciliationError
from isrp.models import CashMatchStatus, ExceptionStatus, ExceptionType, RequestStatus
from isrp.repository import SQLiteRepository
from isrp.service import PlatformService


class TestDomainScenarios(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(PROJECT_ROOT / "config" / "default_config.json")
        self.edge = json.loads(
            (PROJECT_ROOT / "data" / "fixtures" / "edge_and_failure_scenarios.json").read_text(encoding="utf-8")
        )["scenarios"]
        self.service = PlatformService(self.config, SQLiteRepository(":memory:"))
        self.ts = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)

        self.service.register_investor(
            investor_id="INV-DOM-001",
            legal_name="Domain Test Synthetic Fund LP",
            investor_type="INSTITUTIONAL",
            jurisdiction="US",
            tax_form_status="VERIFIED",
            accredited=True,
            bank_account_ref="SYN-ACCT-3001",
            identity_doc_ref="SYN-DOC-3001",
            now=self.ts,
        )
        self.service.run_onboarding("INV-DOM-001", now=self.ts)
        self.service.activate_investor("INV-DOM-001", now=self.ts)
        self.service.register_dealing_window(
            window_id="DW-DOM-01",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            dealing_date="2026-03-31",
            open_at="2026-03-01T00:00:00Z",
            cutoff_at="2026-03-31T16:00:00Z",
            cash_due_date="2026-04-02",
            settlement_date="2026-04-02",
            now=self.ts,
        )
        self.service.publish_nav(
            nav_id="NAV-DOM-V1",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            dealing_date="2026-03-31",
            currency="USD",
            nav_per_share="125.000000",
            approved_by="nav_ctrl_01",
            approved_at="2026-04-01T10:00:00Z",
        )

    def test_duplicate_request_is_blocked_and_audited(self) -> None:
        dup = self.edge["duplicate_request"]
        first = self.service.submit_request(
            request_id=dup["request_id"],
            idempotency_key=dup["idempotency_key"],
            investor_id="INV-DOM-001",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            request_type="SUBSCRIPTION",
            dealing_date="2026-03-31",
            currency="USD",
            gross_amount=dup["gross_amount"],
            submitted_at="2026-03-25T10:00:00Z",
            maker_id="ops_maker_01",
        )
        self.assertEqual(first.status, RequestStatus.PENDING_APPROVAL)

        # Duplicate idempotency_key with different request_id must raise DuplicateRequestError
        with self.assertRaises(DuplicateRequestError) as ctx:
            self.service.submit_request(
                request_id=dup["duplicate_request_id"],
                idempotency_key=dup["idempotency_key"],
                investor_id="INV-DOM-001",
                fund_id="FUND-ALPHA",
                share_class="CLASS-A",
                request_type="SUBSCRIPTION",
                dealing_date="2026-03-31",
                currency="USD",
                gross_amount=dup["gross_amount"],
                submitted_at="2026-03-25T10:01:00Z",
                maker_id="ops_maker_01",
            )
        self.assertEqual(ctx.exception.details["existing_request_id"], dup["request_id"])
        self.assertEqual(len(self.service.repo.list_requests()), 1)
        self.assertEqual(self.service.diagnostics.snapshot_counters()["duplicate_requests_blocked"], 1)

    def test_delayed_cash_opens_exception_blocks_settlement_until_reviewed(self) -> None:
        dc = self.edge["delayed_cash"]
        self.service.submit_request(
            request_id=dc["request_id"],
            idempotency_key=dc["idempotency_key"],
            investor_id="INV-DOM-001",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            request_type="SUBSCRIPTION",
            dealing_date=dc["dealing_date"],
            currency="USD",
            gross_amount=dc["gross_amount"],
            submitted_at="2026-03-25T10:00:00Z",
            maker_id="ops_maker_01",
        )
        self.service.review_request(
            request_id=dc["request_id"],
            checker_id="ops_checker_01",
            decision="APPROVED",
            now=datetime(2026, 3, 26, 10, 0, 0, tzinfo=timezone.utc),
        )
        conf, exc_case = self.service.record_cash_confirmation(
            confirmation_id="CASH-DELAY-001",
            request_id=dc["request_id"],
            direction="INBOUND",
            currency="USD",
            amount=dc["gross_amount"],
            value_date=dc["cash_value_date"],  # After 2026-04-02 due date
            bank_reference="WIRE-DELAY-001",
            received_at="2026-04-06T14:00:00Z",
            actor_id="treasury_feed",
        )
        self.assertEqual(conf.match_status, CashMatchStatus.DELAYED_CASH)
        self.assertIsNotNone(exc_case)
        assert exc_case is not None
        self.assertEqual(exc_case.exception_type, ExceptionType.DELAYED_CASH)

        req_after_cash = self.service.repo.get_request(dc["request_id"])
        assert req_after_cash is not None
        self.assertEqual(req_after_cash.status, RequestStatus.EXCEPTION_REVIEW)

        # Settlement must fail while exception is open
        with self.assertRaises(ReconciliationError):
            self.service.settle_request(request_id=dc["request_id"], now=datetime(2026, 4, 6, 15, 0, 0, tzinfo=timezone.utc))

        # Independent exception reviewer approves late-value-date settlement
        resolved = self.service.resolve_exception(
            exception_id=exc_case.exception_id,
            reviewer_id="ops_manager_01",
            target_status=ExceptionStatus.RESOLVED,
            resolution_note="Late wire confirmed by custodian; approved for dealing window settlement.",
            now=datetime(2026, 4, 6, 16, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(resolved.status, ExceptionStatus.RESOLVED)

        # Now request can be priced and settled cleanly
        self.service.price_request(request_id=dc["request_id"], now=datetime(2026, 4, 6, 16, 5, 0, tzinfo=timezone.utc))
        settled, entry = self.service.settle_request(
            request_id=dc["request_id"], now=datetime(2026, 4, 6, 16, 10, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(settled.status, RequestStatus.SETTLED)
        # 40000 * 0.99 / 125 = 39600 / 125 = 316.800000 shares
        self.assertEqual(entry.share_delta, Decimal("316.800000"))
        self.assertTrue(self.service.verify_reconciliation().reconciled)

    def test_cash_amount_mismatch_and_duplicate_confirmation(self) -> None:
        self.service.submit_request(
            request_id="REQ-SHORT-001",
            idempotency_key="IDEM-SHORT-001",
            investor_id="INV-DOM-001",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            request_type="SUBSCRIPTION",
            dealing_date="2026-03-31",
            currency="USD",
            gross_amount="20000.00",
            submitted_at="2026-03-25T10:00:00Z",
            maker_id="ops_maker_01",
        )
        self.service.review_request(request_id="REQ-SHORT-001", checker_id="ops_checker_01", decision="APPROVED")
        conf, exc_case = self.service.record_cash_confirmation(
            confirmation_id="CASH-SHORT-001",
            request_id="REQ-SHORT-001",
            direction="INBOUND",
            currency="USD",
            amount="19500.00",
            value_date="2026-04-01",
            bank_reference="WIRE-SHORT-001",
            received_at="2026-04-01T12:00:00Z",
        )
        self.assertEqual(conf.match_status, CashMatchStatus.AMOUNT_MISMATCH)
        self.assertEqual(conf.discrepancy_amount, Decimal("-500.00"))
        self.assertIsNotNone(exc_case)
        assert exc_case is not None
        self.assertEqual(exc_case.exception_type, ExceptionType.CASH_MISMATCH)

        with self.assertRaises(DuplicateRequestError):
            self.service.record_cash_confirmation(
                confirmation_id="CASH-SHORT-001",
                request_id="REQ-SHORT-001",
                direction="INBOUND",
                currency="USD",
                amount="19500.00",
                value_date="2026-04-01",
                bank_reference="WIRE-SHORT-001",
                received_at="2026-04-01T12:05:00Z",
            )


if __name__ == "__main__":
    unittest.main()
