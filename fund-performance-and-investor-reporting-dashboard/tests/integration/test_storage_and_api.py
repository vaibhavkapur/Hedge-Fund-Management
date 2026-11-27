"""Integration tests for SQLite persistence, HTTP API routing, exporters, and adapter contracts."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Dict, Optional, Tuple

from fund_reporting.api.http_server import ApiRouter, bootstrap_service
from fund_reporting.domain.enums import ReportStatus, Role
from fund_reporting.domain.errors import DomainError
from fund_reporting.security.rbac import Actor
from fund_reporting.storage.postgres_adapter import PostgresRepository

ROOT = Path(__file__).resolve().parents[2]
ALPHA_FIXTURE = ROOT / "data" / "fixtures" / "fund_alpha_synthetic.json"


class TestStorageAdaptersAndHttpApi(unittest.TestCase):
    def setUp(self) -> None:
        self.service, self.auth = bootstrap_service(":memory:", ALPHA_FIXTURE)
        self.router = ApiRouter(self.service, self.auth)

    def _call(
        self,
        path: str,
        *,
        method: str = "GET",
        token: Optional[str] = None,
        body: Optional[Dict[str, object]] = None,
    ) -> Tuple[int, str, str]:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        raw = json.dumps(body).encode("utf-8") if body is not None else b""
        resp = self.router.dispatch(method, path, headers=headers, raw_body=raw)
        return resp.status, resp.content_type, resp.body.decode("utf-8")

    def test_end_to_end_http_workflow_and_exports(self) -> None:
        # Health check without auth
        status, _, body = self._call("/api/v1/health")
        self.assertEqual(status, 200)
        health = json.loads(body)
        self.assertFalse(health["order_execution_enabled"])
        self.assertEqual(health["fund_count"], 1)

        # Unauthenticated API call returns 401
        status, _, body = self._call("/api/v1/funds")
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error"]["code"], "UNAUTHENTICATED")

        # Static dashboard HTML, CSS, JS assets load cleanly
        for asset, expected_ctype in [
            ("/", "text/html"),
            ("/static/styles.css", "text/css"),
            ("/static/app.js", "application/javascript"),
        ]:
            status, ctype, _ = self._call(asset)
            self.assertEqual(status, 200)
            self.assertIn(expected_ctype, ctype)

        # Analytics in EUR with upstream foundation diagnostics
        status, _, body = self._call(
            "/api/v1/funds/FUND-SAAT-SIM-01/analytics?currency=EUR",
            token="demo-analyst-token",
        )
        self.assertEqual(status, 200)
        analytics = json.loads(body)
        self.assertEqual(analytics["reporting_currency"], "EUR")
        self.assertTrue(analytics["disclosure"]["is_simulated_or_paper"])
        self.assertIn("risk_engine", analytics["foundation_diagnostics"])
        self.assertIn("portfolio_optimizer", analytics["foundation_diagnostics"])
        self.assertIn("transaction_cost_analysis", analytics["foundation_diagnostics"])

        # Report lifecycle: Analyst creates & submits -> Reviewer reviews -> Approver publishes -> Investor exports
        status, _, body = self._call(
            "/api/v1/reports",
            method="POST",
            token="demo-analyst-token",
            body={
                "fund_id": "FUND-SAAT-SIM-01",
                "report_series_id": "RPT-HTTP-TEST",
                "audience": "INVESTOR",
                "reporting_currency": "USD",
            },
        )
        self.assertEqual(status, 201)
        vid = json.loads(body)["version_id"]

        # Investor cannot see DRAFT
        status, _, _ = self._call(f"/api/v1/reports/{vid}", token="demo-investor-token")
        self.assertEqual(status, 403)

        status, _, _ = self._call(
            f"/api/v1/reports/{vid}/submit", method="POST", token="demo-analyst-token", body={}
        )
        self.assertEqual(status, 200)

        status, _, _ = self._call(
            f"/api/v1/reports/{vid}/review",
            method="POST",
            token="demo-reviewer-token",
            body={"approve": True, "review_notes": "Reconciled and verified"},
        )
        self.assertEqual(status, 200)

        status, _, body = self._call(
            f"/api/v1/reports/{vid}/publish", method="POST", token="demo-approver-token", body={}
        )
        self.assertEqual(status, 200)
        published = json.loads(body)
        self.assertEqual(published["status"], ReportStatus.PUBLISHED.value)

        # Investor CAN now export in all 4 formats
        for fmt, expected_token in [
            ("json", vid),
            ("markdown", "SIMULATED"),
            ("csv", "valuation_date"),
            ("html", "Immutable SHA-256 Digest"),
        ]:
            status, _, content = self._call(
                f"/api/v1/reports/{vid}/export?format={fmt}",
                token="demo-investor-token",
            )
            self.assertEqual(status, 200, f"Export {fmt} failed")
            self.assertIn(expected_token, content)

    def test_postgres_adapter_fails_safely_without_driver_and_schema_exists(self) -> None:
        schema_file = ROOT / "config" / "schema_postgres.sql"
        self.assertTrue(schema_file.is_file())
        sql_text = schema_file.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS nav_snapshots", sql_text)
        self.assertIn("CREATE TABLE IF NOT EXISTS reports", sql_text)
        with self.assertRaises(DomainError) as ctx:
            PostgresRepository("postgresql://localhost/nonexistent")
        self.assertIn(ctx.exception.code, ("OPTIONAL_DEPENDENCY_MISSING", "POSTGRES_NOT_CONFIGURED"))

    def test_point_in_time_as_of_snapshot_retrieval(self) -> None:
        analyst = Actor("analyst_maya", Role.ANALYST)
        early = self.service.get_snapshots(analyst, "FUND-SAAT-SIM-01", as_of_utc="2026-01-08T23:00:00Z")
        full = self.service.get_snapshots(analyst, "FUND-SAAT-SIM-01")
        self.assertGreater(len(full), len(early))
        self.assertTrue(all(s.availability_utc <= "2026-01-08T23:00:00Z" for s in early))


if __name__ == "__main__":
    unittest.main()
