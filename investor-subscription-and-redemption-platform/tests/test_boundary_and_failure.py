"""Boundary and failure tests: NAV restatement, reviewer separation, unapproved settlement, cancellation, tamper detection, replay."""

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
from isrp.errors import (
    InvalidStateTransitionError,
    ReviewerSeparationError,
    UnapprovedRequestError,
)
from isrp.models import ExceptionStatus, JournalEntryType, NAVStatus, RequestStatus
from isrp.repository import SQLiteRepository
from isrp.service import PlatformService
from test_integration_scenarios import run_valid_fixture_on_service


class TestBoundaryAndFailureBehavior(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(PROJECT_ROOT / "config" / "default_config.json")
        self.valid_fixture = json.loads(
            (PROJECT_ROOT / "data" / "fixtures" / "valid_scenario.json").read_text(encoding="utf-8")
        )
        self.edge = json.loads(
            (PROJECT_ROOT / "data" / "fixtures" / "edge_and_failure_scenarios.json").read_text(encoding="utf-8")
        )["scenarios"]
        self.ts = datetime(2026, 3, 15, 10, 0, 0, tzinfo=timezone.utc)

    def _setup_single_investor_and_window(self) -> PlatformService:
        service = PlatformService(self.config, SQLiteRepository(":memory:"))
        service.register_investor(
            investor_id="INV-BND-001",
            legal_name="Boundary Synthetic Partners",
            investor_type="INSTITUTIONAL",
            jurisdiction="US",
            tax_form_status="VERIFIED",
            accredited=True,
            bank_account_ref="SYN-ACCT-8001",
            identity_doc_ref="SYN-DOC-8001",
            now=self.ts,
        )
        service.run_onboarding("INV-BND-001", now=self.ts)
        service.activate_investor("INV-BND-001", now=self.ts)
        service.register_dealing_window(
            window_id="DW-BND-01",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            dealing_date="2026-03-31",
            open_at="2026-03-01T00:00:00Z",
            cutoff_at="2026-03-31T16:00:00Z",
            cash_due_date="2026-04-02",
            settlement_date="2026-04-02",
            now=self.ts,
        )
        service.publish_nav(
            nav_id="NAV-BND-V1",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            dealing_date="2026-03-31",
            currency="USD",
            nav_per_share="125.000000",
            approved_by="nav_ctrl_01",
            approved_at="2026-04-01T10:00:00Z",
        )
        return service

    def test_reviewer_separation_and_unapproved_request_cannot_settle(self) -> None:
        service = self._setup_single_investor_and_window()
        rs = self.edge["reviewer_separation"]

        service.submit_request(
            request_id="REQ-UNAPP-01",
            idempotency_key="IDEM-UNAPP-01",
            investor_id="INV-BND-001",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            request_type="SUBSCRIPTION",
            dealing_date="2026-03-31",
            currency="USD",
            gross_amount="100000.00",
            submitted_at="2026-03-20T12:00:00Z",
            maker_id=rs["maker_id"],
        )

        # Unapproved request cannot be priced or settled
        with self.assertRaises(UnapprovedRequestError):
            service.price_request(request_id="REQ-UNAPP-01", now=self.ts)
        with self.assertRaises(UnapprovedRequestError):
            service.settle_request(request_id="REQ-UNAPP-01", now=self.ts)

        # Maker attempting to self-approve raises ReviewerSeparationError
        with self.assertRaises(ReviewerSeparationError):
            service.review_request(
                request_id="REQ-UNAPP-01",
                checker_id=rs["checker_id"],
                decision="APPROVED",
                now=self.ts,
            )

        # Exception reviewer separation check as well
        service.review_request(
            request_id="REQ-UNAPP-01",
            checker_id="ops_checker_01",
            decision="APPROVED",
            now=self.ts,
        )
        _, exc_case = service.record_cash_confirmation(
            confirmation_id="CASH-SEP-01",
            request_id="REQ-UNAPP-01",
            direction="INBOUND",
            currency="USD",
            amount="100000.00",
            value_date="2026-04-05",  # delayed cash
            bank_reference="WIRE-SEP-01",
            received_at="2026-04-05T12:00:00Z",
            actor_id="treasury_ops_01",
        )
        assert exc_case is not None
        with self.assertRaises(ReviewerSeparationError):
            service.resolve_exception(
                exception_id=exc_case.exception_id,
                reviewer_id="treasury_ops_01",  # same actor as creator
                target_status=ExceptionStatus.RESOLVED,
                resolution_note="Self-approval attempt",
                now=self.ts,
            )

    def test_nav_restatement_posts_immutable_adjustment_and_enforces_separation(self) -> None:
        service = self._setup_single_investor_and_window()
        nr = self.edge["nav_restatement"]

        service.submit_request(
            request_id="REQ-RST-01",
            idempotency_key="IDEM-RST-01",
            investor_id="INV-BND-001",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            request_type="SUBSCRIPTION",
            dealing_date=nr["dealing_date"],
            currency="USD",
            gross_amount="100000.00",
            submitted_at="2026-03-20T12:00:00Z",
            maker_id="ops_maker_01",
        )
        service.review_request(request_id="REQ-RST-01", checker_id="ops_checker_01", decision="APPROVED", now=self.ts)
        service.record_cash_confirmation(
            confirmation_id="CASH-RST-01",
            request_id="REQ-RST-01",
            direction="INBOUND",
            currency="USD",
            amount="100000.00",
            value_date="2026-04-01",
            bank_reference="WIRE-RST-01",
            received_at="2026-04-01T12:00:00Z",
        )
        service.price_request(request_id="REQ-RST-01", now=self.ts)
        service.settle_request(request_id="REQ-RST-01", now=self.ts)

        # Settled request cannot be cancelled directly
        with self.assertRaises(InvalidStateTransitionError):
            service.cancel_request(request_id="REQ-RST-01", actor_id="ops_admin", reason="Attempt cancel settled", now=self.ts)

        # NAV restatement rejects maker == checker
        with self.assertRaises(ReviewerSeparationError):
            service.restate_nav(
                new_nav_id="NAV-BND-V2",
                fund_id="FUND-ALPHA",
                share_class="CLASS-A",
                dealing_date=nr["dealing_date"],
                restated_nav_per_share=nr["restated_nav"],
                reason="Valuation correction",
                maker_id="nav_ctrl_01",
                checker_id="nav_ctrl_01",
                now=datetime(2026, 4, 10, 10, 0, 0, tzinfo=timezone.utc),
            )

        result = service.restate_nav(
            new_nav_id="NAV-BND-V2",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            dealing_date=nr["dealing_date"],
            restated_nav_per_share=nr["restated_nav"],
            reason="Vendor pricing feed accrual correction",
            maker_id="nav_ctrl_01",
            checker_id="nav_director_02",
            now=datetime(2026, 4, 10, 10, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result["impacted_count"], 1)
        self.assertEqual(result["deltas"][0]["share_delta"], nr["expected_share_delta"])
        self.assertEqual(result["deltas"][0]["restated_shares"], nr["expected_new_shares"])

        # Original NAV preserved as SUPERSEDED; new NAV is APPROVED v2
        old_nav = service.repo.get_nav("NAV-BND-V1")
        new_nav = service.repo.get_nav("NAV-BND-V2")
        assert old_nav is not None and new_nav is not None
        self.assertEqual(old_nav.status, NAVStatus.SUPERSEDED)
        self.assertEqual(new_nav.status, NAVStatus.APPROVED)
        self.assertEqual(new_nav.version, 2)

        # Immutable journal has 2 entries (original 792 + adjustment +8 = 800)
        entries = service.repo.list_journal_entries("REQ-RST-01")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].share_delta, Decimal("792.000000"))
        self.assertEqual(entries[1].entry_type, JournalEntryType.NAV_RESTATEMENT_ADJUSTMENT)
        self.assertEqual(entries[1].share_delta, Decimal("8.000000"))
        self.assertEqual(entries[1].supersedes_entry_id, entries[0].entry_id)

        pos = service.get_share_register()[("INV-BND-001", "FUND-ALPHA", "CLASS-A", "USD")]
        self.assertEqual(pos.settled_shares, Decimal("800.000000"))
        self.assertTrue(service.verify_reconciliation().reconciled)

    def test_pre_settlement_cancellation_works(self) -> None:
        service = self._setup_single_investor_and_window()
        service.submit_request(
            request_id="REQ-CAN-01",
            idempotency_key="IDEM-CAN-01",
            investor_id="INV-BND-001",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            request_type="SUBSCRIPTION",
            dealing_date="2026-03-31",
            currency="USD",
            gross_amount="25000.00",
            submitted_at="2026-03-20T12:00:00Z",
            maker_id="ops_maker_01",
        )
        cancelled = service.cancel_request(
            request_id="REQ-CAN-01",
            actor_id="ops_checker_01",
            reason="Investor withdrew instruction prior to cutoff.",
            now=self.ts,
        )
        self.assertEqual(cancelled.status, RequestStatus.CANCELLED)
        self.assertEqual(cancelled.version, 2)

    def test_audit_chain_tamper_detection_and_deterministic_replay(self) -> None:
        # Run identical scenario twice in isolated repositories -> identical logical hash
        svc1 = PlatformService(self.config, SQLiteRepository(":memory:"))
        svc2 = PlatformService(self.config, SQLiteRepository(":memory:"))
        run_valid_fixture_on_service(svc1, self.valid_fixture)
        run_valid_fixture_on_service(svc2, self.valid_fixture)

        snap1 = svc1.export_state_snapshot()
        snap2 = svc2.export_state_snapshot()
        self.assertEqual(snap1["logical_state_hash"], snap2["logical_state_hash"])
        self.assertEqual(
            [e.entry_hash for e in svc1.repo.list_audit_events()],
            [e.entry_hash for e in svc2.repo.list_audit_events()],
        )

        # Tamper directly with SQLite audit_events row and confirm verification detects it
        assert isinstance(svc1.repo, SQLiteRepository)
        svc1.repo._conn.execute("UPDATE audit_events SET actor_id = 'tampered_actor' WHERE sequence = 2")
        svc1.repo._conn.commit()
        valid, errors = svc1.verify_audit_trail()
        self.assertFalse(valid)
        self.assertGreaterEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
