"""Standalone integration adapters for neighboring Trading-Management projects.

These adapters define explicit, versioned JSON/dict boundaries so the fee engine
can interoperate with sibling modules (fund-nav-calculation-engine,
investor-subscription-and-redemption-platform, regulatory-reporting-engine)
without copying implementations or requiring runtime coupling.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .models import CalculationRunResult, CapitalFlow, NavSnapshot


class NavSnapshotFeedAdapter:
    """Adapter translating fund-nav-calculation-engine NAV output payloads into NavSnapshot."""

    @staticmethod
    def from_nav_engine_payload(payload: Mapping[str, Any]) -> NavSnapshot:
        """Accept canonical NavSnapshot fields or aliased NAV-engine export keys."""
        normalized = {
            "snapshot_id": payload.get("snapshot_id") or payload["valuation_id"],
            "valuation_date": payload["valuation_date"],
            "period_start_date": payload["period_start_date"],
            "share_class": payload["share_class"],
            "currency": payload["currency"],
            "gross_nav_per_unit": payload.get("gross_nav_per_unit") or payload["pre_fee_nav_per_share"],
            "version": payload.get("version", 1),
            "source": payload.get("source", "fund-nav-calculation-engine"),
        }
        return NavSnapshot.from_dict(normalized)

    @classmethod
    def load_from_file(cls, path: Path) -> List[NavSnapshot]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else [raw]
        return [cls.from_nav_engine_payload(item) for item in items]


class CapitalFlowFeedAdapter:
    """Adapter translating investor-subscription-and-redemption-platform flow events."""

    @staticmethod
    def from_investor_platform_payload(payload: Mapping[str, Any]) -> CapitalFlow:
        normalized = {
            "flow_id": payload.get("flow_id") or payload["order_id"],
            "investor_id": payload["investor_id"],
            "lot_id": payload["lot_id"],
            "share_class": payload["share_class"],
            "flow_date": payload.get("flow_date") or payload["trade_date"],
            "flow_type": payload["flow_type"],
            "nav_per_unit": payload.get("nav_per_unit") or payload["dealing_nav_per_unit"],
            "currency": payload["currency"],
            "amount": payload.get("amount"),
            "units": payload.get("units"),
            "series_id": payload.get("series_id"),
            "policy_note": payload.get("policy_note", "Imported via CapitalFlowFeedAdapter"),
        }
        return CapitalFlow.from_dict(normalized)


class RegulatoryLedgerExportAdapter:
    """Adapter exporting balanced fee journals in a flat format for regulatory-reporting-engine."""

    @staticmethod
    def to_regulatory_fee_schedule(runs: Sequence[CalculationRunResult]) -> Dict[str, Any]:
        rows: List[Dict[str, Any]] = []
        for run in runs:
            for calc in run.lot_calculations:
                rows.append(
                    {
                        "run_id": run.run_id,
                        "version": run.version,
                        "status": run.status.value,
                        "agreement_id": run.agreement_id,
                        "share_class": run.share_class,
                        "currency": run.currency,
                        "valuation_date": calc.valuation_date.isoformat(),
                        "lot_id": calc.lot_id,
                        "investor_id": calc.investor_id,
                        "management_fee_amount": format(calc.management_fee_amount, "f"),
                        "performance_fee_delta": format(calc.performance_fee_delta, "f"),
                        "crystallized_performance_fee": format(calc.crystallized_performance_fee, "f"),
                        "ending_net_nav": format(calc.ending_net_nav, "f"),
                        "new_hwm": format(calc.new_hwm, "f"),
                        "reconciled": run.reconciliation.reconciled,
                    }
                )
        return {
            "export_schema_version": "1.0.0",
            "producer": "hedge-fund-fee-accounting-engine",
            "row_count": len(rows),
            "rows": rows,
        }
