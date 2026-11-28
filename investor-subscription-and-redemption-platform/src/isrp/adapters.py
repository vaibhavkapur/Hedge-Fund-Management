"""Independent runnable integration adapters for sibling Trading-Management projects.

No sibling implementation is imported directly; all integration happens via versioned JSON
payload contracts documented in docs/integration.md.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .service import PlatformService


def ingest_approved_nav_payload(service: PlatformService, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Adapter contract accepting an approved NAV message from fund-nav-calculation-engine."""
    nav = service.publish_nav(
        nav_id=payload["nav_id"],
        fund_id=payload["fund_id"],
        share_class=payload["share_class"],
        dealing_date=payload["dealing_date"],
        currency=payload["currency"],
        nav_per_share=payload["nav_per_share"],
        approved_by=payload["approved_by"],
        approved_at=payload["approved_at"],
        version=int(payload.get("version", 1)),
    )
    return nav.to_dict()


def export_fee_accounting_feed(service: PlatformService) -> Dict[str, Any]:
    """Adapter contract exporting settled fee entries for hedge-fund-fee-accounting-engine."""
    entries = service.repo.list_journal_entries()
    fee_items: List[Dict[str, Any]] = [
        {
            "journal_entry_id": e.entry_id,
            "request_id": e.request_id,
            "investor_id": e.investor_id,
            "fund_id": e.fund_id,
            "share_class": e.share_class,
            "dealing_date": e.dealing_date.isoformat(),
            "currency": e.currency,
            "entry_type": e.entry_type.value,
            "fee_amount": str(e.fee_delta),
            "nav_version": e.nav_version,
        }
        for e in entries
    ]
    return {
        "contract": "isrp.fee_accounting_feed.v1",
        "config_hash": service.config.config_hash,
        "item_count": len(fee_items),
        "items": fee_items,
    }


def export_regulatory_flow_report(service: PlatformService) -> Dict[str, Any]:
    """Adapter contract exporting audited capital flows for regulatory-reporting-engine."""
    recon = service.verify_reconciliation()
    audit_ok, _ = service.verify_audit_trail()
    return {
        "contract": "isrp.regulatory_capital_flows.v1",
        "config_hash": service.config.config_hash,
        "audit_chain_verified": audit_ok,
        "reconciled": recon.reconciled,
        "total_register_shares": str(recon.total_register_shares),
        "total_inbound_cash": str(recon.total_inbound_cash_journal),
        "total_outbound_cash": str(recon.total_outbound_cash_journal),
        "positions": [p.to_dict() for p in service.get_share_register().values()],
    }
