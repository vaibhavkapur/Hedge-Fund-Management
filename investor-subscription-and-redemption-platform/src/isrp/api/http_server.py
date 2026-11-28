"""Zero-dependency stdlib JSON HTTP server mirroring the FastAPI contract for offline execution/tests."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional, Tuple

from ..errors import ISRPBaseError, NotFoundError
from ..service import PlatformService


def dispatch_http_request(
    service: PlatformService,
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Pure in-process HTTP router usable directly in tests or inside BaseHTTPRequestHandler."""
    body = payload or {}
    clean_path = path.rstrip("/") or "/"
    parts = [p for p in clean_path.split("/") if p]

    try:
        if method == "GET":
            if clean_path == "/health":
                return 200, {"status": "ok", "config_hash": service.config.config_hash}
            if clean_path == "/share-register":
                return 200, {"positions": [p.to_dict() for p in service.get_share_register().values()]}
            if clean_path == "/reconciliation":
                return 200, service.verify_reconciliation().to_dict()
            if clean_path == "/audit":
                ok, errs = service.verify_audit_trail()
                return 200, {
                    "valid": ok,
                    "errors": errs,
                    "events": [e.to_dict() for e in service.repo.list_audit_events()],
                }
            raise NotFoundError(f"Unknown GET route '{clean_path}'.")

        if method == "POST":
            if clean_path == "/investors":
                return 201, service.register_investor(**body).to_dict()
            if len(parts) == 3 and parts[0] == "investors" and parts[2] == "onboarding":
                return 200, service.run_onboarding(parts[1], actor_id=body.get("actor_id", "compliance_mock")).to_dict()
            if len(parts) == 3 and parts[0] == "investors" and parts[2] == "activate":
                return 200, service.activate_investor(parts[1], actor_id=body.get("actor_id", "ops_admin")).to_dict()
            if clean_path == "/dealing-windows":
                return 201, service.register_dealing_window(**body).to_dict()
            if clean_path == "/navs":
                return 201, service.publish_nav(**body).to_dict()
            if clean_path == "/navs/restate":
                return 200, service.restate_nav(**body)
            if clean_path == "/requests":
                return 201, service.submit_request(**body).to_dict()
            if len(parts) == 3 and parts[0] == "requests" and parts[2] == "review":
                req, approval = service.review_request(request_id=parts[1], **body)
                return 200, {"request": req.to_dict(), "approval": approval.to_dict()}
            if len(parts) == 3 and parts[0] == "requests" and parts[2] == "cancel":
                return 200, service.cancel_request(request_id=parts[1], **body).to_dict()
            if len(parts) == 3 and parts[0] == "requests" and parts[2] == "price":
                req, calc = service.price_request(request_id=parts[1], actor_id=body.get("actor_id", "pricing_engine"))
                return 200, {"request": req.to_dict(), "calculation": calc.to_dict()}
            if len(parts) == 3 and parts[0] == "requests" and parts[2] == "settle":
                req, entry = service.settle_request(request_id=parts[1], actor_id=body.get("actor_id", "settlement_engine"))
                return 200, {"request": req.to_dict(), "journal_entry": entry.to_dict()}
            if clean_path == "/cash-confirmations":
                conf, exc_case = service.record_cash_confirmation(**body)
                return 201, {
                    "cash_confirmation": conf.to_dict(),
                    "exception": exc_case.to_dict() if exc_case else None,
                }
            if len(parts) == 3 and parts[0] == "exceptions" and parts[2] == "resolve":
                return 200, service.resolve_exception(exception_id=parts[1], **body).to_dict()
            raise NotFoundError(f"Unknown POST route '{clean_path}'.")

        return 405, {"error_code": "METHOD_NOT_ALLOWED", "message": f"Unsupported method {method}."}
    except ISRPBaseError as err:
        return err.http_status, err.to_dict()
    except TypeError as exc:
        return 422, {"error_code": "VALIDATION_ERROR", "message": f"Invalid payload fields: {exc}", "details": {}}


def create_http_server(service: PlatformService, host: str = "127.0.0.1", port: int = 8080) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, status_code: int, data: Dict[str, Any]) -> None:
            raw = json.dumps(data, sort_keys=True).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            status, body = dispatch_http_request(service, "GET", self.path)
            self._send_json(status, body)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw_bytes = self.rfile.read(length) if length > 0 else b"{}"
            try:
                payload = json.loads(raw_bytes.decode("utf-8"))
            except json.JSONDecodeError as exc:
                self._send_json(400, {"error_code": "INVALID_JSON", "message": str(exc)})
                return
            status, body = dispatch_http_request(service, "POST", self.path, payload)
            self._send_json(status, body)

        def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover
            return

    return HTTPServer((host, port), Handler)
