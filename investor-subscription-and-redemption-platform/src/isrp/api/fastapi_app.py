"""FastAPI application wrapper for PlatformService.

Requires optional dependencies: `fastapi`, `pydantic`, `uvicorn`.
Use `src/isrp/api/http_server.py` for zero-dependency stdlib HTTP execution.
"""

from __future__ import annotations

from typing import Any, Dict

from ..errors import ISRPBaseError
from ..service import PlatformService


def create_fastapi_app(service: PlatformService) -> Any:
    """Construct a FastAPI ASGI application wired to the provided PlatformService."""
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse
    except ImportError as exc:
        raise RuntimeError(
            "FastAPI is not installed in this Python environment. "
            "Install optional extra `.[api]` or run `isrp.api.http_server`."
        ) from exc

    app = FastAPI(
        title="Investor Subscription and Redemption Platform (Prototype)",
        version="0.1.0",
    )

    @app.exception_handler(ISRPBaseError)
    async def handle_domain_error(_request: Request, exc: ISRPBaseError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {"status": "ok", "config_hash": service.config.config_hash}

    @app.post("/investors")
    def register_investor(payload: Dict[str, Any]) -> Dict[str, Any]:
        return service.register_investor(**payload).to_dict()

    @app.post("/investors/{investor_id}/onboarding")
    def run_onboarding(investor_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return service.run_onboarding(investor_id, actor_id=payload.get("actor_id", "compliance_mock")).to_dict()

    @app.post("/investors/{investor_id}/activate")
    def activate_investor(investor_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return service.activate_investor(investor_id, actor_id=payload.get("actor_id", "ops_admin")).to_dict()

    @app.post("/dealing-windows")
    def register_window(payload: Dict[str, Any]) -> Dict[str, Any]:
        return service.register_dealing_window(**payload).to_dict()

    @app.post("/navs")
    def publish_nav(payload: Dict[str, Any]) -> Dict[str, Any]:
        return service.publish_nav(**payload).to_dict()

    @app.post("/navs/restate")
    def restate_nav(payload: Dict[str, Any]) -> Dict[str, Any]:
        return service.restate_nav(**payload)

    @app.post("/requests")
    def submit_request(payload: Dict[str, Any]) -> Dict[str, Any]:
        return service.submit_request(**payload).to_dict()

    @app.post("/requests/{request_id}/review")
    def review_request(request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        req, approval = service.review_request(request_id=request_id, **payload)
        return {"request": req.to_dict(), "approval": approval.to_dict()}

    @app.post("/requests/{request_id}/cancel")
    def cancel_request(request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return service.cancel_request(request_id=request_id, **payload).to_dict()

    @app.post("/requests/{request_id}/price")
    def price_request(request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        req, calc = service.price_request(request_id=request_id, actor_id=payload.get("actor_id", "pricing_engine"))
        return {"request": req.to_dict(), "calculation": calc.to_dict()}

    @app.post("/requests/{request_id}/settle")
    def settle_request(request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        req, entry = service.settle_request(request_id=request_id, actor_id=payload.get("actor_id", "settlement_engine"))
        return {"request": req.to_dict(), "journal_entry": entry.to_dict()}

    @app.post("/cash-confirmations")
    def record_cash(payload: Dict[str, Any]) -> Dict[str, Any]:
        conf, exc_case = service.record_cash_confirmation(**payload)
        return {
            "cash_confirmation": conf.to_dict(),
            "exception": exc_case.to_dict() if exc_case else None,
        }

    @app.post("/exceptions/{exception_id}/resolve")
    def resolve_exception(exception_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return service.resolve_exception(exception_id=exception_id, **payload).to_dict()

    @app.get("/share-register")
    def share_register() -> Dict[str, Any]:
        return {"positions": [p.to_dict() for p in service.get_share_register().values()]}

    @app.get("/reconciliation")
    def reconciliation() -> Dict[str, Any]:
        return service.verify_reconciliation().to_dict()

    @app.get("/audit")
    def audit() -> Dict[str, Any]:
        ok, errs = service.verify_audit_trail()
        return {
            "valid": ok,
            "errors": errs,
            "events": [e.to_dict() for e in service.repo.list_audit_events()],
        }

    return app
