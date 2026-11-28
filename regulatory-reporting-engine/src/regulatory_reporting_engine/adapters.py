"""Independent runnable adapters translating upstream Trading-Management payloads into canonical GovernedSnapshots."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

from regulatory_reporting_engine.errors import MissingRequiredDataError
from regulatory_reporting_engine.models import GovernedSnapshot


def adapt_fund_nav_engine_snapshot(
    nav_payload: Dict[str, Any],
    *,
    fund_lei: str,
    manager_lei: str,
    domicile_country: str,
    period_id: str,
    period_start: str,
    frequency: str = "QUARTERLY",
    liquidity_bucket_primary: str = "MONTHLY",
    captured_at_utc: str = "2026-04-01T00:10:00Z",
) -> GovernedSnapshot:
    """
    Independent adapter converting a fund-nav-calculation-engine snapshot JSON dictionary
    into a canonical GovernedSnapshot without importing the sibling package.
    """
    required_top = (
        "snapshot_id",
        "business_date",
        "fund_code",
        "fund_name",
        "base_currency",
        "gross_assets",
        "total_liabilities",
        "net_asset_value",
        "holdings",
    )
    for key in required_top:
        if key not in nav_payload:
            raise MissingRequiredDataError(
                f"NAV engine adapter missing required key '{key}'.",
                details={"adapter": "fund_nav_calculation_engine", "missing_key": key},
            )

    positions: List[Dict[str, Any]] = []
    exposures_by_bucket: Dict[tuple, Dict[str, Any]] = {}

    for h in nav_payload["holdings"]:
        mv = Decimal(str(h["market_value_base"]))
        direction = "SHORT" if mv < Decimal("0") else "LONG"
        positions.append(
            {
                "position_id": str(h["holding_id"]),
                "instrument_isin": str(h["isin"]),
                "asset_class": str(h["asset_class"]),
                "direction": direction,
                "quantity": str(h["quantity"]),
                "price_local": str(h["unit_price"]),
                "local_currency": str(h["currency"]),
                "fx_rate_to_base": str(h["fx_rate"]),
                "market_value_base": str(mv),
            }
        )

        # Aggregate holdings into exposure buckets by (asset_class, counterparty_lei)
        bkey = (str(h["asset_class"]), str(h["counterparty_lei"]))
        if bkey not in exposures_by_bucket:
            exposures_by_bucket[bkey] = {
                "asset_class": bkey[0],
                "counterparty_lei": bkey[1],
                "long": Decimal("0.00"),
                "short": Decimal("0.00"),
            }
        if mv >= Decimal("0"):
            exposures_by_bucket[bkey]["long"] += mv
        else:
            exposures_by_bucket[bkey]["short"] += abs(mv)

    exposures: List[Dict[str, Any]] = []
    for idx, bkey in enumerate(sorted(exposures_by_bucket.keys()), start=1):
        bucket = exposures_by_bucket[bkey]
        long_e = bucket["long"].quantize(Decimal("0.01"))
        short_e = bucket["short"].quantize(Decimal("0.01"))
        gross_e = (long_e + short_e).quantize(Decimal("0.01"))
        net_e = (long_e - short_e).quantize(Decimal("0.01"))
        exposures.append(
            {
                "exposure_id": f"EXP-ADAPT-{idx:03d}",
                "asset_class": bucket["asset_class"],
                "counterparty_lei": bucket["counterparty_lei"],
                "currency": str(nav_payload["base_currency"]),
                "long_exposure_base": str(long_e),
                "short_exposure_base": str(short_e),
                "gross_exposure_base": str(gross_e),
                "net_exposure_base": str(net_e),
            }
        )

    canonical = {
        "snapshot_id": str(nav_payload["snapshot_id"]),
        "schema_contract_version": "1.0.0",
        "captured_at_utc": captured_at_utc,
        "provenance": {
            "source_system": "fund-nav-calculation-engine-adapter",
            "synthetic_data": True,
            "upstream_snapshot_id": str(nav_payload["snapshot_id"]),
        },
        "fund": {
            "fund_id": str(nav_payload["fund_code"]),
            "fund_name": str(nav_payload["fund_name"]),
            "fund_lei": fund_lei,
            "manager_lei": manager_lei,
            "base_currency": str(nav_payload["base_currency"]),
            "domicile_country": domicile_country,
            "liquidity_bucket_primary": liquidity_bucket_primary,
        },
        "reporting_period": {
            "period_id": period_id,
            "start_date": period_start,
            "end_date": str(nav_payload["business_date"]),
            "frequency": frequency,
        },
        "nav": {
            "nav_record_id": f"NAV-ADAPT-{nav_payload['business_date']}",
            "as_of_date": str(nav_payload["business_date"]),
            "currency": str(nav_payload["base_currency"]),
            "gross_asset_value": str(nav_payload["gross_assets"]),
            "total_liabilities": str(nav_payload["total_liabilities"]),
            "net_asset_value": str(nav_payload["net_asset_value"]),
            "cash_and_equivalents": str(nav_payload.get("cash_balance", "0.00")),
        },
        "positions": positions,
        "exposures": exposures,
    }
    return GovernedSnapshot.from_dict(canonical)
