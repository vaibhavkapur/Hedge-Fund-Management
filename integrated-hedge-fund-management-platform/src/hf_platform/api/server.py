"""Lightweight HTTP REST API and embedded Control Plane Web UI server."""

from __future__ import annotations

import json
import re
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from hf_platform.domain import ApprovalAction, ReportType
from hf_platform.errors import DomainValidationError, NotFoundError, PlatformError
from hf_platform.services.platform import HedgeFundPlatform

WEB_INDEX_PATH = Path(__file__).resolve().parent.parent / "web" / "index.html"


class ControlPlaneApp:
    """Transport-agnostic HTTP router for the Control Plane REST API and Web UI."""

    def __init__(self, platform: HedgeFundPlatform) -> None:
        self.platform = platform

    def dispatch(
        self,
        method: str,
        raw_path: str,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, str, Any]:
        """Dispatch a request and return (status_code, content_type, payload)."""
        parsed = urlparse(raw_path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        method_u = method.upper()

        try:
            if method_u == "GET":
                return self._handle_get(path, qs)
            if method_u == "POST":
                return self._handle_post(path, body or {})
            raise NotFoundError(f"Unsupported method/path: {method_u} {path}")
        except KeyError as exc:
            return 400, "application/json", {"error_code": "MISSING_FIELD", "message": f"Missing required field: {exc}"}
        except PlatformError as exc:
            return exc.http_status, "application/json", exc.to_dict()
        except Exception as exc:
            return 500, "application/json", {"error_code": "INTERNAL_ERROR", "message": str(exc)}

    def _handle_get(self, path: str, qs: dict[str, list[str]]) -> tuple[int, str, Any]:
        platform = self.platform

        if path in ("/", "/index.html"):
            return 200, "text/html", WEB_INDEX_PATH.read_text(encoding="utf-8")

        if path == "/api/health":
            return 200, "application/json", platform.health_and_metrics()

        if path == "/api/master":
            return (
                200,
                "application/json",
                {
                    "funds": [f.model_dump(mode="json") for f in platform.master.list_funds()],
                    "accounts": [a.model_dump(mode="json") for a in platform.master.list_accounts()],
                    "strategies": [s.model_dump(mode="json") for s in platform.master.list_strategies()],
                    "instruments": [i.model_dump(mode="json") for i in platform.master.list_instruments()],
                    "counterparties": [c.model_dump(mode="json") for c in platform.master.list_counterparties()],
                    "users": [u.model_dump(mode="json") for u in platform.master.list_users()],
                },
            )

        if path == "/api/exceptions":
            fund_id = qs.get("fund_id", [None])[0]
            bdate = date.fromisoformat(qs["date"][0]) if "date" in qs else None
            items = platform.exceptions.list_exceptions(fund_id=fund_id, business_date=bdate)
            return 200, "application/json", {"exceptions": [e.model_dump(mode="json") for e in items]}

        if path == "/api/audit":
            limit = int(qs["limit"][0]) if "limit" in qs else None
            entries = platform.audit.list_entries(limit=limit)
            verification = platform.audit.verify_chain()
            return (
                200,
                "application/json",
                {"verification": verification, "entries": [e.model_dump(mode="json") for e in entries]},
            )

        if path == "/api/events":
            limit = int(qs["limit"][0]) if "limit" in qs else None
            events = platform.audit.list_events(limit=limit)
            return 200, "application/json", {"events": [e.model_dump(mode="json") for e in events]}

        m = re.fullmatch(r"/api/funds/([^/]+)/snapshots", path)
        if m:
            fund_id = m.group(1)
            bdate = date.fromisoformat(qs["date"][0]) if "date" in qs else None
            snaps = platform.store.list_snapshots(fund_id, business_date=bdate)
            return 200, "application/json", {"snapshots": [s.model_dump(mode="json") for s in snaps]}

        m = re.fullmatch(r"/api/funds/([^/]+)/trial-balance", path)
        if m:
            fund_id = m.group(1)
            if "date" not in qs:
                raise DomainValidationError("Query parameter 'date' (YYYY-MM-DD) is required")
            tb = platform.ledger.compute_trial_balance(fund_id, date.fromisoformat(qs["date"][0]))
            return 200, "application/json", tb.model_dump(mode="json")

        m = re.fullmatch(r"/api/funds/([^/]+)/reconciliations", path)
        if m:
            fund_id = m.group(1)
            bdate = date.fromisoformat(qs["date"][0]) if "date" in qs else None
            recons = platform.store.list_reconciliations(fund_id, business_date=bdate)
            return 200, "application/json", {"reconciliations": [r.model_dump(mode="json") for r in recons]}

        m = re.fullmatch(r"/api/funds/([^/]+)/close-runs", path)
        if m:
            fund_id = m.group(1)
            bdate = date.fromisoformat(qs["date"][0]) if "date" in qs else None
            runs = platform.store.list_close_runs(fund_id, business_date=bdate)
            return 200, "application/json", {"close_runs": [r.model_dump(mode="json") for r in runs]}

        m = re.fullmatch(r"/api/funds/([^/]+)/reports", path)
        if m:
            fund_id = m.group(1)
            bdate = date.fromisoformat(qs["date"][0]) if "date" in qs else None
            reports = platform.store.list_reports(fund_id, business_date=bdate)
            return 200, "application/json", {"reports": [r.model_dump(mode="json") for r in reports]}

        raise NotFoundError(f"Unknown endpoint: GET {path}")

    def _handle_post(self, path: str, body: dict[str, Any]) -> tuple[int, str, Any]:
        platform = self.platform

        m = re.fullmatch(r"/api/funds/([^/]+)/close-runs", path)
        if m:
            run = platform.close.run_daily_close(
                fund_id=m.group(1),
                business_date=date.fromisoformat(body["business_date"]),
                actor_id=body["actor_id"],
            )
            return 200, "application/json", run.model_dump(mode="json")

        m = re.fullmatch(r"/api/close-runs/([^/]+)/signoff", path)
        if m:
            run = platform.close.sign_off_close(close_run_id=m.group(1), actor_id=body["actor_id"])
            return 200, "application/json", run.model_dump(mode="json")

        m = re.fullmatch(r"/api/exceptions/([^/]+)/propose", path)
        if m:
            exc_obj = platform.exceptions.propose_resolution(
                exception_id=m.group(1),
                resolution_notes=body["resolution_notes"],
                actor_id=body["actor_id"],
            )
            return 200, "application/json", exc_obj.model_dump(mode="json")

        m = re.fullmatch(r"/api/exceptions/([^/]+)/decide", path)
        if m:
            exc_obj = platform.exceptions.decide_exception(
                exception_id=m.group(1),
                action=ApprovalAction(body["action"]),
                decision_notes=body.get("decision_notes", ""),
                actor_id=body["actor_id"],
            )
            return 200, "application/json", exc_obj.model_dump(mode="json")

        if path == "/api/reports/generate":
            report = platform.reporting.generate_report(
                snapshot_id=body["snapshot_id"],
                report_type=ReportType(body["report_type"]),
                actor_id=body["actor_id"],
                notes=body.get("notes", ""),
            )
            return 201, "application/json", report.model_dump(mode="json")

        m = re.fullmatch(r"/api/reports/([^/]+)/approve", path)
        if m:
            report = platform.reporting.approve_and_publish(report_id=m.group(1), actor_id=body["actor_id"])
            return 200, "application/json", report.model_dump(mode="json")

        m = re.fullmatch(r"/api/reports/([^/]+)/revise", path)
        if m:
            report = platform.reporting.revise_report(
                prior_report_id=m.group(1),
                new_snapshot_id=body["new_snapshot_id"],
                reason=body["reason"],
                actor_id=body["actor_id"],
            )
            return 201, "application/json", report.model_dump(mode="json")

        raise NotFoundError(f"Unknown endpoint: POST {path}")


class ControlPlaneServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], platform: HedgeFundPlatform) -> None:
        self.app = ControlPlaneApp(platform)
        super().__init__(server_address, ControlPlaneHandler)


class ControlPlaneHandler(BaseHTTPRequestHandler):
    server: ControlPlaneServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _write_response(self, status: int, content_type: str, payload: Any) -> None:
        if content_type == "text/html":
            raw = str(payload).encode("utf-8")
        else:
            raw = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        status, ctype, payload = self.server.app.dispatch("GET", self.path)
        self._write_response(status, ctype, payload)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body: dict[str, Any] = {}
        if length > 0:
            try:
                parsed = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(parsed, dict):
                    self._write_response(
                        400,
                        "application/json",
                        {"error_code": "DOMAIN_VALIDATION_ERROR", "message": "JSON body must be an object"},
                    )
                    return
                body = parsed
            except json.JSONDecodeError as exc:
                self._write_response(
                    400,
                    "application/json",
                    {"error_code": "DOMAIN_VALIDATION_ERROR", "message": f"Invalid JSON: {exc}"},
                )
                return
        status, ctype, payload = self.server.app.dispatch("POST", self.path, body)
        self._write_response(status, ctype, payload)


def create_server(platform: HedgeFundPlatform, host: str = "127.0.0.1", port: int = 8080) -> ControlPlaneServer:
    return ControlPlaneServer((host, port), platform)
