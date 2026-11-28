"""Tests for configuration loading, schema contracts, invalid inputs, and storage adapters."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from isrp.config import load_config, load_config_from_dict
from isrp.errors import ConfigurationError, ValidationError
from isrp.repository import PostgresRepository, SQLiteRepository
from isrp.service import PlatformService


class TestContractsAndConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.config_path = PROJECT_ROOT / "config" / "default_config.json"
        self.config = load_config(self.config_path)
        self.service = PlatformService(self.config, SQLiteRepository(":memory:"))
        self.now = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

        # Register active investor and dealing window for input validation tests
        self.service.register_investor(
            investor_id="INV-VAL-001",
            legal_name="Validation Synthetic LP",
            investor_type="INSTITUTIONAL",
            jurisdiction="US",
            tax_form_status="VERIFIED",
            accredited=True,
            bank_account_ref="SYN-ACCT-2001",
            identity_doc_ref="SYN-DOC-2001",
            now=self.now,
        )
        self.service.run_onboarding("INV-VAL-001", now=self.now)
        self.service.activate_investor("INV-VAL-001", now=self.now)
        self.service.register_dealing_window(
            window_id="DW-VAL-01",
            fund_id="FUND-ALPHA",
            share_class="CLASS-A",
            dealing_date="2026-03-31",
            open_at="2026-03-01T00:00:00Z",
            cutoff_at="2026-03-31T16:00:00Z",
            cash_due_date="2026-04-02",
            settlement_date="2026-04-02",
            now=self.now,
        )

    def test_missing_config_raises_configuration_error(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_config(PROJECT_ROOT / "config" / "does_not_exist.json")

    def test_invalid_config_rejects_missing_keys_and_float_decimals(self) -> None:
        with self.assertRaises(ConfigurationError):
            load_config_from_dict({"schema_version": "1.0.0"})

        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        raw["rounding"]["mode"] = "UNSUPPORTED_MODE"
        with self.assertRaises(ConfigurationError):
            load_config_from_dict(raw)

        raw2 = json.loads(self.config_path.read_text(encoding="utf-8"))
        raw2["funds"]["FUND-ALPHA"]["share_classes"]["CLASS-A"]["min_subscription_amount"] = 100.5
        with self.assertRaises(ConfigurationError):
            load_config_from_dict(raw2)

    def test_invalid_inputs_from_fixture_fail_explicitly(self) -> None:
        edge_fixture = json.loads(
            (PROJECT_ROOT / "data" / "fixtures" / "edge_and_failure_scenarios.json").read_text(encoding="utf-8")
        )
        for idx, case in enumerate(edge_fixture["scenarios"]["invalid_inputs"]):
            with self.subTest(case=case["case"]):
                with self.assertRaises(ValidationError):
                    self.service.submit_request(
                        request_id=f"REQ-INV-{idx}",
                        idempotency_key=f"IDEM-INV-{idx}",
                        investor_id="INV-VAL-001",
                        fund_id="FUND-ALPHA",
                        share_class="CLASS-A",
                        request_type="SUBSCRIPTION",
                        dealing_date="2026-03-31",
                        currency=case.get("currency", "USD"),
                        gross_amount=case.get("gross_amount", "20000.00"),
                        submitted_at="2026-03-20T10:00:00Z",
                        maker_id="ops_maker_01",
                    )

    def test_float_amount_and_naive_timestamp_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.submit_request(
                request_id="REQ-FLOAT",
                idempotency_key="IDEM-FLOAT",
                investor_id="INV-VAL-001",
                fund_id="FUND-ALPHA",
                share_class="CLASS-A",
                request_type="SUBSCRIPTION",
                dealing_date="2026-03-31",
                currency="USD",
                gross_amount=15000.25,  # floats must be rejected
                submitted_at="2026-03-20T10:00:00Z",
                maker_id="ops_maker_01",
            )

        with self.assertRaises(ValidationError):
            self.service.submit_request(
                request_id="REQ-NAIVE-TS",
                idempotency_key="IDEM-NAIVE-TS",
                investor_id="INV-VAL-001",
                fund_id="FUND-ALPHA",
                share_class="CLASS-A",
                request_type="SUBSCRIPTION",
                dealing_date="2026-03-31",
                currency="USD",
                gross_amount="15000.00",
                submitted_at="2026-03-20T10:00:00",  # missing timezone offset
                maker_id="ops_maker_01",
            )

    def test_postgres_ddl_exists_and_bad_dsn_rejected(self) -> None:
        ddl_path = PROJECT_ROOT / "src" / "isrp" / "migrations" / "001_initial_schema.sql"
        self.assertTrue(ddl_path.exists())
        ddl = ddl_path.read_text(encoding="utf-8")
        for table in ("investors", "nav_records", "requests", "approvals", "cash_confirmations", "share_journal", "audit_events"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", ddl)
        with self.assertRaises(ConfigurationError):
            PostgresRepository("mysql://invalid")


if __name__ == "__main__":
    unittest.main()
