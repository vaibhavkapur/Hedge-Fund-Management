"""Standalone integration adapter interfaces for neighboring Trading-Management systems."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Protocol, Tuple

from fund_nav_engine.models import (
    AccrualDirection,
    AccrualRecord,
    AccrualType,
    CapitalFlowRecord,
    FlowStatus,
    FlowTimingRule,
    FlowType,
    NavCalculationResult,
    format_decimal,
    parse_date,
    parse_decimal,
    parse_utc_datetime,
)


class FeeAccrualSourceAdapter(Protocol):
    """Contract for consuming fee accruals from hedge-fund-fee-accounting-engine."""

    def fetch_accruals(self, fund_id: str, valuation_date: date) -> Tuple[AccrualRecord, ...]:
        ...


class CapitalFlowSourceAdapter(Protocol):
    """Contract for consuming investor flows from investor-subscription-and-redemption-platform."""

    def fetch_capital_flows(
        self, fund_id: str, valuation_date: date
    ) -> Tuple[CapitalFlowRecord, ...]:
        ...


class NavPublicationSinkAdapter(Protocol):
    """Contract for publishing frozen NAV snapshots to downstream reporting/collateral consumers."""

    def publish_snapshot(self, snapshot: NavCalculationResult) -> Dict[str, Any]:
        ...


class DictFeeAccrualAdapter:
    """Runnable in-memory adapter converting external fee-engine payloads into AccrualRecord contracts."""

    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self._records = records

    def fetch_accruals(self, fund_id: str, valuation_date: date) -> Tuple[AccrualRecord, ...]:
        out: List[AccrualRecord] = []
        for idx, rec in enumerate(self._records):
            if rec.get("fund_id") != fund_id:
                continue
            val_date = parse_date(rec["valuation_date"], f"fee_accrual[{idx}].valuation_date")
            if val_date != valuation_date:
                continue
            out.append(
                AccrualRecord(
                    accrual_id=str(rec["accrual_id"]),
                    fund_id=fund_id,
                    accrual_type=AccrualType(rec["fee_type"]),
                    direction=AccrualDirection.LIABILITY,
                    amount=parse_decimal(rec["accrued_amount"], f"fee_accrual[{idx}].accrued_amount"),
                    currency=str(rec["currency"]),
                    valuation_date=val_date,
                    period_start=parse_date(rec["period_start"], f"fee_accrual[{idx}].period_start"),
                    period_end=parse_date(rec["period_end"], f"fee_accrual[{idx}].period_end"),
                    share_class_id=rec.get("share_class_id"),
                    description=str(rec.get("description", "Adapted fee accrual")),
                )
            )
        return tuple(out)


class DictCapitalFlowAdapter:
    """Runnable in-memory adapter converting subscription/redemption platform payloads into CapitalFlowRecord."""

    def __init__(self, records: List[Dict[str, Any]]) -> None:
        self._records = records

    def fetch_capital_flows(
        self, fund_id: str, valuation_date: date
    ) -> Tuple[CapitalFlowRecord, ...]:
        out: List[CapitalFlowRecord] = []
        for idx, rec in enumerate(self._records):
            if rec.get("fund_id") != fund_id:
                continue
            trade_dt = parse_date(rec["trade_date"], f"flow[{idx}].trade_date")
            if trade_dt != valuation_date:
                continue
            out.append(
                CapitalFlowRecord(
                    flow_id=str(rec["order_id"]),
                    fund_id=fund_id,
                    share_class_id=str(rec["share_class_id"]),
                    flow_type=FlowType(rec["order_type"]),
                    currency=str(rec["currency"]),
                    trade_date=trade_dt,
                    settlement_date=parse_date(rec["settlement_date"], f"flow[{idx}].settlement_date"),
                    submitted_at=parse_utc_datetime(rec["submitted_at"], f"flow[{idx}].submitted_at"),
                    amount=(
                        parse_decimal(rec["cash_amount"], f"flow[{idx}].cash_amount")
                        if rec.get("cash_amount") is not None
                        else None
                    ),
                    shares=(
                        parse_decimal(rec["share_quantity"], f"flow[{idx}].share_quantity")
                        if rec.get("share_quantity") is not None
                        else None
                    ),
                    status=FlowStatus(rec.get("status", "confirmed")),
                    timing_rule=FlowTimingRule(rec.get("timing_rule", "post_nav")),
                )
            )
        return tuple(out)


class RegulatoryReportingNavPublisher:
    """Converts a frozen NavCalculationResult into a stable regulatory-reporting-engine summary payload."""

    def publish_snapshot(self, snapshot: NavCalculationResult) -> Dict[str, Any]:
        return {
            "schema_version": "nav_pub.v1",
            "snapshot_id": snapshot.snapshot_id,
            "fund_id": snapshot.fund_id,
            "valuation_date": snapshot.valuation_date.isoformat(),
            "version": snapshot.version,
            "status": snapshot.status.value,
            "prior_snapshot_id": snapshot.prior_snapshot_id,
            "base_currency": snapshot.base_currency,
            "gross_assets": format_decimal(snapshot.gross_assets),
            "total_liabilities": format_decimal(snapshot.total_liabilities),
            "net_assets": format_decimal(snapshot.net_assets),
            "output_hash": snapshot.output_hash,
            "share_classes": [
                {
                    "share_class_id": sc.share_class_id,
                    "currency": sc.currency,
                    "closing_shares": format_decimal(sc.closing_shares),
                    "closing_net_assets_class_ccy": format_decimal(sc.closing_net_assets_class_ccy),
                    "nav_per_share": format_decimal(sc.nav_per_share),
                    "zero_shares_flag": sc.zero_shares_flag,
                }
                for sc in snapshot.share_class_navs
            ],
        }
