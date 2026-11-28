"""Integration tests: end-to-end fixture, cutoff violation, insufficient shares, SQLite reload, HTTP API & adapters."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from isrp.adapters import (
    export_fee_accounting_feed,
    export_regulatory_flow_report,
    ingest_approved_nav_payload,
)
from isrp.api.http_server import dispatch_http_request
from isrp.config import load_config
from isrp.errors import CutoffViolationError, InsufficientSharesError
from isrp.models import ExceptionType, RequestStatus
from isrp.repository import SQLiteRepository
from isrp.service import PlatformService


def run_valid_fixture_on_service(service: PlatformService, fixture: dict) -> None:
    """Deterministic helper replaying valid_scenario.json end-to-end."""
    t0 = datetime(2026, 3, 1, 8, 0, 0, tzinfo=timezone.utc)
    for inv in fixture["investors"]:
        service.register_investor(**inv, now=t0)
        service.run_onboarding(inv["investor_id"], now=t0)
        service.activate_investor(inv["investor_id"], now=t0)

    for win in fixture["dealing_windows"]:
        service.register_dealing_window(**win, now=t0)

    for nav in fixture["nav_records"]:
        ingest_approved_nav_payload(service, nav)

    cash_by_req = {c["request_id"]: c for c in fixture["cash_confirmations"]}
    for r in fixture["requests"]:
        service.submit_request(
            request_id=r["request_id"],
            idempotency_key=r["idempotency_key"],
            investor_id=r["investor_id"],
            fund_id=r["fund_id"],
            share_class=r["share_class"],
            request_type=r["request_type"],
            dealing_date=r["dealing_date"],
            currency=r["currency"],
            gross_amount=r["gross_amount"],
            requested_shares=r["requested_shares"],
            submitted_at=r["submitted_at"],
            maker_id=r["maker_id"],
        )
        t_rev = datetime(2026, 4, 1, 11, 0, 0, tzinfo=timezone.utc) if r["dealing_date"] == "2026-03-31" else datetime(2026, 5, 1, 11, 0, 0, tzinfo=timezone.utc)
        service.review_request(
            request_id=r["request_id"],
            checker_id=r["checker_id"],
            decision="APPROVED",
            now=t_rev,
        )
        if r["request_type"] == "SUBSCRIPTION":
            service.record_cash_confirmation(**cash_by_req[r["request_id"]])
            service.price_request(request_id=r["request_id"], now=t_rev)
            service.settle_request(request_id=r["request_id"], now=t_rev)
        else:
            service.price_request(request_id=r["request_id"], now=t_rev)
            service.record_cash_confirmation(**cash_by_req[r["request_id"]])
            service.settle_request(request_id=r["request_id"], now=t_rev)


class TestIntegrationScenarios(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(PROJECT_ROOT / "config" / "default_config.json")
        self.valid_fixture = json.loads(
            (PROJECT_ROOT / "data" / "fixtures" / "valid_scenario.json").read_text(encoding="utf-8")
        )
        self.edge = json.loads(
            (PROJECT_ROOT / "data" / "fixtures" / "edge_and_failure_scenarios.json").read_text(encoding="utf-8")
        )["scenarios"]

    def test_end_to_end_reconciliation_and_sqlite_restart_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "isrp_test.sqlite3"
            repo = SQLiteRepository(db_file)
            service = PlatformService(self.config, repo)
            run_valid_fixture_on_service(service, self.valid_fixture)

            recon = service.verify_reconciliation()
            self.assertTrue(recon.reconciled, msg=str(recon.discrepancies))
            exp = self.valid_fixture["expected_summary"]
            self.assertEqual(recon.total_register_shares, Decimal(exp["total_register_shares"]))
            self.assertEqual(recon.total_inbound_cash_journal, Decimal(exp["total_inbound_cash"]))
            self.assertEqual(recon.total_outbound_cash_journal, Decimal(exp["total_outbound_cash"]))
            self.assertEqual(recon.total_fees_collected, Decimal(exp["total_fees_collected"]))

            reg = service.get_share_register()
            for inv_id, exp_pos in exp["positions"].items():
                pos = reg[(inv_id, "FUND-ALPHA", "CLASS-A", "USD")]
                self.assertEqual(pos.settled_shares, Decimal(exp_pos["settled_shares"]))
                self.assertEqual(pos.total_subscribed_gross, Decimal(exp_pos["total_subscribed_gross"]))
                self.assertEqual(pos.total_redeemed_net, Decimal(exp_pos["total_redeemed_net"]))

            fee_feed = export_fee_accounting_feed(service)
            self.assertEqual(fee_feed["item_count"], 3)
            reg_report = export_regulatory_flow_report(service)
            self.assertTrue(reg_report["reconciled"])
            self.assertTrue(reg_report["audit_chain_verified"])

            before_hash = service.export_state_snapshot()["logical_state_hash"]
            repo.close()

            # Simulate restart/reload from persisted SQLite file
            reloaded_repo = SQLiteRepository(db_file)
            reloaded_service = PlatformService(self.config, reloaded_repo)
            after_hash = reloaded_service.export_state_snapshot()["logical_state_hash"]
            self.assertEqual(before_hash, after_hash)
            ok, errs = reloaded_service.verify_audit_trail()
            self.assertTrue(ok, msg=str(errs))
            reloaded_repo.close()

    def test_cutoff_violation_and_insufficient_shares(self) -> None:
        service = PlatformService(self.config, SQLiteRepository(":memory:"))
        run_valid_fixture_on_service(service, self.valid_fixture)

        # Cutoff violation: submitted 5 minutes after 16:00:00Z cutoff
        cv = self.edge["cutoff_violation"]
        with self.assertRaises(CutoffViolationError) as cv_ctx:
            service.submit_request(
                request_id=cv["request_id"],
                idempotency_key=cv["idempotency_key"],
                investor_id="INV-SYN-001",
                fund_id="FUND-ALPHA",
                share_class="CLASS-A",
                request_type="SUBSCRIPTION",
                dealing_date=cv["dealing_date"],
                currency="USD",
                gross_amount=cv["gross_amount"],
                submitted_at=cv["submitted_at"],
                maker_id="ops_maker_01",
            )
        self.assertIn("exception_id", cv_ctx.exception.details)
        cv_excs = service.repo.list_exceptions(cv["request_id"])
        self.assertEqual(cv_excs[0].exception_type, ExceptionType.CUTOFF_VIOLATION)

        # Insufficient shares: INV-SYN-001 currently holds 592.000000 shares, requests 900.000000
        ins = self.edge["insufficient_shares"]
        with self.assertRaises(InsufficientSharesError) as ins_ctx:
            service.submit_request(
                request_id=ins["request_id"],
                idempotency_key=ins["idempotency_key"],
                investor_id="INV-SYN-001",
                fund_id="FUND-ALPHA",
                share_class="CLASS-A",
                request_type="REDEMPTION",
                dealing_date="2026-04-30",
                currency="USD",
                requested_shares=ins["requested_shares"],
                submitted_at="2026-04-29T10:00:00Z",
                maker_id="ops_maker_01",
            )
        self.assertIn("exception_id", ins_ctx.exception.details)
        ins_excs = service.repo.list_exceptions(ins["request_id"])
        self.assertEqual(ins_excs[0].exception_type, ExceptionType.INSUFFICIENT_SHARES)

        # Pending redemption lock prevents double-redeeming same shares
        pending_red = service.submit_request(
            request_id="REQ-RED-LOCK-1",
            idempotency_key="IDEM-RED-LOCK-1",
            investor_id="INV-SYN-001",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            request_type="REDEMPTION",
            dealing_date="2026-04-30",
            currency="USD",
            requested_shares="400.000000",
            submitted_at="2026-04-29T10:05:00Z",
            maker_id="ops_maker_01",
        )
        self.assertEqual(pending_red.status, RequestStatus.PENDING_APPROVAL)
        with self.assertRaises(InsufficientSharesError):
            service.submit_request(
                request_id="REQ-RED-LOCK-2",
                idempotency_key="IDEM-RED-LOCK-2",
                investor_id="INV-SYN-001",
                fund_id="FUND-ALPHA",
                share_class="CLASS-A",
                request_type="REDEMPTION",
                dealing_date="2026-04-30",
                currency="USD",
                requested_shares="250.000000",  # only 192 available after 400 locked
                submitted_at="2026-04-29T10:06:00Z",
                maker_id="ops_maker_01",
            )

    def test_http_api_router_end_to_end(self) -> None:
        service = PlatformService(self.config, SQLiteRepository(":memory:"))
        st, body = dispatch_http_request(service, "GET", "/health")
        self.assertEqual(st, 200)
        self.assertEqual(body["status"], "ok")

        st, _ = dispatch_http_request(
            service,
            "POST",
            "/investors",
            {
                "investor_id": "INV-HTTP-01",
                "legal_name": "HTTP Synthetic Partners",
                "investor_type": "INSTITUTIONAL",
                "jurisdiction": "US",
                "tax_form_status": "VERIFIED",
                "accredited": True,
                "bank_account_ref": "SYN-ACCT-7001",
                "identity_doc_ref": "SYN-DOC-7001",
            },
        )
        self.assertEqual(st, 201)
        st, body = dispatch_http_request(service, "POST", "/investors/INV-HTTP-01/onboarding", {})
        self.assertEqual(st, 200)
        self.assertEqual(body["status"], "KYC_APPROVED")
        st, body = dispatch_http_request(service, "POST", "/investors/INV-HTTP-01/activate", {})
        self.assertEqual(st, 200)
        self.assertEqual(body["status"], "ACTIVE")

        st, _ = dispatch_http_request(
            service,
            "POST",
            "/dealing-windows",
            {
                "window_id": "DW-HTTP-01",
                "fund_id": "FUND-ALPHA",
                "share_class": "CLASS-A",
                "dealing_date": "2026-03-31",
                "open_at": "2026-03-01T00:00:00Z",
                "cutoff_at": "2026-03-31T16:00:00Z",
                "cash_due_date": "2026-04-02",
                "settlement_date": "2026-04-02",
            },
        )
        self.assertEqual(st, 201)
        st, _ = dispatch_http_request(
            service,
            "POST",
            "/navs",
            {
                "nav_id": "NAV-HTTP-V1",
                "fund_id": "FUND-ALPHA",
                "share_class": "CLASS-A",
                "dealing_date": "2026-03-31",
                "currency": "USD",
                "nav_per_share": "125.000000",
                "approved_by": "nav_ctrl",
                "approved_at": "2026-04-01T10:00:00Z",
            },
        )
        self.assertEqual(st, 201)
        st, _ = dispatch_http_request(
            service,
            "POST",
            "/requests",
            {
                "request_id": "REQ-HTTP-01",
                "idempotency_key": "IDEM-HTTP-01",
                "investor_id": "INV-HTTP-01",
                "fund_id": "FUND-ALPHA",
                "share_class": "CLASS-A",
                "request_type": "SUBSCRIPTION",
                "dealing_date": "2026-03-31",
                "currency": "USD",
                "gross_amount": "100000.00",
                "submitted_at": "2026-03-20T12:00:00Z",
                "maker_id": "maker_http",
            },
        )
        self.assertEqual(st, 201)

        # Reviewer separation via HTTP returns 403
        st, err = dispatch_http_request(
            service,
            "POST",
            "/requests/REQ-HTTP-01/review",
            {"checker_id": "maker_http", "decision": "APPROVED"},
        )
        self.assertEqual(st, 403)
        self.assertEqual(err["error_code"], "REVIEWER_SEPARATION_VIOLATION")

        st, _ = dispatch_http_request(
            service,
            "POST",
            "/requests/REQ-HTTP-01/review",
            {"checker_id": "checker_http", "decision": "APPROVED"},
        )
        self.assertEqual(st, 200)
        st, _ = dispatch_http_request(
            service,
            "POST",
            "/cash-confirmations",
            {
                "confirmation_id": "CASH-HTTP-01",
                "request_id": "REQ-HTTP-01",
                "direction": "INBOUND",
                "currency": "USD",
                "amount": "100000.00",
                "value_date": "2026-04-01",
                "bank_reference": "WIRE-HTTP-01",
                "received_at": "2026-04-01T12:00:00Z",
            },
        )
        self.assertEqual(st, 201)
        st, _ = dispatch_http_request(service, "POST", "/requests/REQ-HTTP-01/price", {})
        self.assertEqual(st, 200)
        st, _ = dispatch_http_request(service, "POST", "/requests/REQ-HTTP-01/settle", {})
        self.assertEqual(st, 200)

        st, recon = dispatch_http_request(service, "GET", "/reconciliation")
        self.assertEqual(st, 200)
        self.assertTrue(recon["reconciled"])
        self.assertEqual(recon["total_register_shares"], "792.000000")

        st, audit = dispatch_http_request(service, "GET", "/audit")
        self.assertEqual(st, 200)
        self.assertTrue(audit["valid"])


if __name__ == "__main__":
    unittest.main()
