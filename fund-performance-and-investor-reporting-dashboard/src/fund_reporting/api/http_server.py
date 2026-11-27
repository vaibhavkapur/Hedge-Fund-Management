"""Zero-dependency HTTP REST API router & server for local reproducible operation."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from fund_reporting.domain.enums import ReportAudience, Role
from fund_reporting.domain.errors import DomainError, NotFoundError, ValidationError
from fund_reporting.domain.models import CashFlowRecord, FundDefinition, NavSnapshot, StrategySnapshot
from fund_reporting.security.rbac import Actor, TokenAuthenticator
from fund_reporting.storage.sqlite_repo import SqliteRepository

from .service import FundReportingService

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "default_config.json"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    content_type: str
    body: bytes


def build_demo_authenticator() -> TokenAuthenticator:
    """Create local demo tokens from environment overrides (never used for live production)."""
    return TokenAuthenticator(
        {
            os.environ.get("SAAT_DEMO_TOKEN_ADMIN", "demo-admin-token"): Actor(
                actor_id="admin_ops", role=Role.ADMIN
            ),
            os.environ.get("SAAT_DEMO_TOKEN_ANALYST", "demo-analyst-token"): Actor(
                actor_id="analyst_maya", role=Role.ANALYST
            ),
            os.environ.get("SAAT_DEMO_TOKEN_REVIEWER", "demo-reviewer-token"): Actor(
                actor_id="reviewer_alex", role=Role.REVIEWER
            ),
            os.environ.get("SAAT_DEMO_TOKEN_APPROVER", "demo-approver-token"): Actor(
                actor_id="approver_cto", role=Role.APPROVER
            ),
            os.environ.get("SAAT_DEMO_TOKEN_INVESTOR", "demo-investor-token"): Actor(
                actor_id="investor_lp_01",
                role=Role.VIEWER_INVESTOR,
                entitled_funds={"FUND-SAAT-SIM-01"},
            ),
        }
    )


class ApiRouter:
    """Pure HTTP request dispatcher decoupled from sockets for deterministic testing and WSGI/HTTP reuse."""

    def __init__(self, service: FundReportingService, authenticator: TokenAuthenticator) -> None:
        self.service = service
        self.authenticator = authenticator

    @staticmethod
    def _json_resp(status: int, payload: Any) -> HttpResponse:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        return HttpResponse(status=status, content_type="application/json; charset=utf-8", body=body)

    @staticmethod
    def _serve_static(rel_path: str) -> HttpResponse:
        target = (_WEB_DIR / rel_path).resolve()
        if not str(target).startswith(str(_WEB_DIR.resolve())) or not target.is_file():
            return ApiRouter._json_resp(
                HTTPStatus.NOT_FOUND,
                {"error": {"code": "NOT_FOUND", "message": "Static asset not found"}},
            )
        ext = target.suffix.lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }.get(ext, "application/octet-stream")
        return HttpResponse(status=HTTPStatus.OK, content_type=ctype, body=target.read_bytes())

    def dispatch(
        self,
        method: str,
        raw_path: str,
        headers: Optional[Dict[str, str]] = None,
        raw_body: bytes = b"",
    ) -> HttpResponse:
        norm_headers = {k.lower(): v for k, v in (headers or {}).items()}
        parsed = urlparse(raw_path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        parts = [p for p in path.split("/") if p]
        m = method.upper()

        try:
            if m == "GET":
                if path in ("/", "/index.html"):
                    return self._serve_static("index.html")
                if path.startswith("/static/"):
                    return self._serve_static(path[len("/static/") :])
                if path == "/api/v1/health":
                    return self._json_resp(HTTPStatus.OK, self.service.get_health())

                actor = self.authenticator.authenticate(norm_headers.get("authorization"))

                if path == "/api/v1/whoami":
                    return self._json_resp(
                        HTTPStatus.OK,
                        {
                            "actor_id": actor.actor_id,
                            "role": actor.role.value,
                            "entitled_funds": sorted(actor.entitled_funds)
                            if actor.entitled_funds is not None
                            else None,
                        },
                    )
                if path == "/api/v1/funds":
                    return self._json_resp(
                        HTTPStatus.OK, {"funds": [f.to_dict() for f in self.service.list_funds(actor)]}
                    )
                if len(parts) == 5 and parts[:3] == ["api", "v1", "funds"] and parts[4] == "snapshots":
                    snaps = self.service.get_snapshots(
                        actor,
                        parts[3],
                        start_date=qs.get("start_date", [None])[0],
                        end_date=qs.get("end_date", [None])[0],
                        as_of_utc=qs.get("as_of_utc", [None])[0],
                    )
                    return self._json_resp(
                        HTTPStatus.OK, {"fund_id": parts[3], "snapshots": [s.to_dict() for s in snaps]}
                    )
                if len(parts) == 5 and parts[:3] == ["api", "v1", "funds"] and parts[4] == "analytics":
                    payload = self.service.get_dashboard_analytics(
                        actor,
                        parts[3],
                        start_date=qs.get("start_date", [None])[0],
                        end_date=qs.get("end_date", [None])[0],
                        reporting_currency=qs.get("currency", [None])[0],
                        as_of_utc=qs.get("as_of_utc", [None])[0],
                    )
                    return self._json_resp(HTTPStatus.OK, payload)
                if path == "/api/v1/reports":
                    reports = self.service.reports.list_reports(
                        actor,
                        fund_id=qs.get("fund_id", [None])[0],
                        report_series_id=qs.get("report_series_id", [None])[0],
                    )
                    summaries = [
                        {k: v for k, v in r.to_dict().items() if k != "payload"} for r in reports
                    ]
                    return self._json_resp(HTTPStatus.OK, {"reports": summaries})
                if len(parts) == 4 and parts[:3] == ["api", "v1", "reports"]:
                    report = self.service.reports.get_report(actor, parts[3])
                    return self._json_resp(HTTPStatus.OK, report.to_dict())
                if len(parts) == 5 and parts[:3] == ["api", "v1", "reports"] and parts[4] == "export":
                    fmt = qs.get("format", ["json"])[0]
                    ctype, body = self.service.export_report(actor, parts[3], fmt)
                    return HttpResponse(status=HTTPStatus.OK, content_type=ctype, body=body.encode("utf-8"))
                if path == "/api/v1/audit":
                    limit = int(qs.get("limit", ["100"])[0])
                    events = self.service.list_audit_events(actor, limit=limit)
                    return self._json_resp(HTTPStatus.OK, {"events": [e.to_dict() for e in events]})
                raise NotFoundError(f"Endpoint not found: {path}")

            if m == "POST":
                actor = self.authenticator.authenticate(norm_headers.get("authorization"))
                body: Dict[str, Any] = {}
                if raw_body:
                    try:
                        parsed_body = json.loads(raw_body.decode("utf-8"))
                    except json.JSONDecodeError as exc:
                        raise ValidationError(f"Invalid JSON request body: {exc}") from exc
                    if not isinstance(parsed_body, dict):
                        raise ValidationError("JSON request body must be an object")
                    body = parsed_body

                if path == "/api/v1/funds":
                    saved = self.service.register_fund(actor, FundDefinition.from_dict(body))
                    return self._json_resp(HTTPStatus.CREATED, saved.to_dict())
                if len(parts) == 5 and parts[:3] == ["api", "v1", "funds"] and parts[4] == "snapshots":
                    snap_raw = dict(body.get("snapshot", body))
                    snap_raw["fund_id"] = parts[3]
                    snap = NavSnapshot.from_dict(snap_raw)
                    cfs = [CashFlowRecord.from_dict(c) for c in body.get("cash_flows", [])]
                    sts = [StrategySnapshot.from_dict(s) for s in body.get("strategy_snapshots", [])]
                    saved = self.service.ingest_nav_snapshot(actor, snap, cash_flows=cfs, strategy_snapshots=sts)
                    return self._json_resp(HTTPStatus.CREATED, saved.to_dict())
                if path == "/api/v1/reports":
                    rec = self.service.reports.create_draft_report(
                        actor,
                        fund_id=str(body["fund_id"]),
                        report_series_id=str(body["report_series_id"]),
                        audience=ReportAudience(body.get("audience", "INVESTOR")),
                        reporting_currency=body.get("reporting_currency"),
                        start_date=body.get("start_date"),
                        end_date=body.get("end_date"),
                        as_of_utc=body.get("as_of_utc"),
                    )
                    return self._json_resp(HTTPStatus.CREATED, rec.to_dict())
                if len(parts) == 5 and parts[:3] == ["api", "v1", "reports"]:
                    version_id, action_name = parts[3], parts[4]
                    if action_name == "submit":
                        return self._json_resp(HTTPStatus.OK, self.service.reports.submit_report(actor, version_id).to_dict())
                    if action_name == "review":
                        rec = self.service.reports.review_report(
                            actor,
                            version_id,
                            approve=bool(body.get("approve", True)),
                            review_notes=str(body.get("review_notes", "")),
                        )
                        return self._json_resp(HTTPStatus.OK, rec.to_dict())
                    if action_name == "publish":
                        rec = self.service.reports.approve_and_publish_report(actor, version_id)
                        return self._json_resp(HTTPStatus.OK, rec.to_dict())
                    if action_name == "revise":
                        rec = self.service.reports.create_revision(
                            actor,
                            supersedes_version_id=version_id,
                            revision_reason=str(body.get("revision_reason", "")),
                            reporting_currency=body.get("reporting_currency"),
                            start_date=body.get("start_date"),
                            end_date=body.get("end_date"),
                            as_of_utc=body.get("as_of_utc"),
                        )
                        return self._json_resp(HTTPStatus.CREATED, rec.to_dict())
                raise NotFoundError(f"Endpoint not found: {path}")

            return self._json_resp(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {"error": {"code": "METHOD_NOT_ALLOWED", "message": f"Method {m} not allowed"}},
            )
        except KeyError as exc:
            return self._json_resp(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": {"code": "VALIDATION_ERROR", "message": f"Missing required field: {exc}"}},
            )
        except DomainError as exc:
            return self._json_resp(exc.http_status, exc.to_dict())
        except Exception as exc:  # pragma: no cover
            return self._json_resp(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": {"code": "INTERNAL_ERROR", "message": str(exc)}},
            )


def create_server(
    host: str = "127.0.0.1",
    port: int = 8080,
    *,
    service: FundReportingService,
    authenticator: TokenAuthenticator,
) -> ThreadingHTTPServer:
    """Create a ThreadingHTTPServer delegating all HTTP traffic to ApiRouter."""
    router = ApiRouter(service, authenticator)

    class RequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            if os.environ.get("SAAT_QUIET_HTTP") != "1":
                super().log_message(format, *args)

        def _handle(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length > 0 else b""
            hdrs = {k: v for k, v in self.headers.items()}
            resp = router.dispatch(method, self.path, headers=hdrs, raw_body=raw_body)
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(resp.body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(resp.body)

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

    return ThreadingHTTPServer((host, port), RequestHandler)


def bootstrap_service(
    db_path: str, fixture_path: Optional[Path] = None
) -> Tuple[FundReportingService, TokenAuthenticator]:
    repo = SqliteRepository(db_path)
    service = FundReportingService.from_config_path(repo, _DEFAULT_CONFIG)
    auth = build_demo_authenticator()
    if fixture_path and fixture_path.is_file():
        admin = Actor(actor_id="bootstrap_loader", role=Role.ADMIN)
        bundle = json.loads(fixture_path.read_text(encoding="utf-8"))
        service.load_fixture_bundle(admin, bundle)
    return service, auth


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Fund Performance & Investor Reporting Dashboard server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db", default=":memory:")
    parser.add_argument(
        "--fixture",
        default=str(Path(__file__).resolve().parents[3] / "data" / "fixtures" / "fund_alpha_synthetic.json"),
    )
    args = parser.parse_args()

    fixture_path = Path(args.fixture) if args.fixture else None
    service, auth = bootstrap_service(args.db, fixture_path)
    server = create_server(args.host, args.port, service=service, authenticator=auth)
    print(f"Fund Performance & Investor Reporting Dashboard listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
