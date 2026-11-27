"""Deterministic fixture-backed adapters with structured fault injection for scenario drills."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from hf_platform.adapters.interfaces import (
    BrokerFeedAdapter,
    EventPublisherAdapter,
    ExecutionFeedAdapter,
    FundAdminAdapter,
    MarketDataAdapter,
)
from hf_platform.domain import (
    DomainEvent,
    ExternalCashRecord,
    ExternalFillRecord,
    ExternalPositionRecord,
    FxRate,
    MarketMark,
    ReportArtifact,
    TradeFill,
)
from hf_platform.errors import ExternalServiceError


@dataclass
class FaultState:
    """Controllable fault injector for testing service outages and degraded feeds."""

    broker_feed_outage: bool = False
    broker_outage_reason: str = "Prime broker EOD feed timeout"
    market_data_outage: bool = False
    market_data_outage_reason: str = "Market data vendor HTTP 503 Service Unavailable"
    execution_feed_outage: bool = False
    execution_outage_reason: str = "Internal OMS fill stream unreachable"
    admin_delivery_outage: bool = False
    omitted_mark_instruments: set[str] = field(default_factory=set)
    override_marks: dict[str, MarketMark] = field(default_factory=dict)
    extra_fills: list[TradeFill] = field(default_factory=list)


class FixtureDataset:
    """In-memory multi-day fixture repository indexed by (fund_id, business_date)."""

    def __init__(self) -> None:
        self.fills: dict[tuple[str, date], list[TradeFill]] = {}
        self.marks: dict[date, list[MarketMark]] = {}
        self.fx_rates: dict[date, list[FxRate]] = {}
        self.external_positions: dict[tuple[str, date], list[ExternalPositionRecord]] = {}
        self.external_cash: dict[tuple[str, date], list[ExternalCashRecord]] = {}
        self.external_fills: dict[tuple[str, date], list[ExternalFillRecord]] = {}

    def load_day_file(self, path: str | Path) -> date:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        bdate = date.fromisoformat(raw["business_date"])
        fund_id = raw["fund_id"]
        key = (fund_id, bdate)
        self.fills[key] = [TradeFill.model_validate({**item, "business_date": bdate}) for item in raw.get("fills", [])]
        self.marks[bdate] = [MarketMark.model_validate(item) for item in raw.get("marks", [])]
        self.fx_rates[bdate] = [FxRate.model_validate(item) for item in raw.get("fx_rates", [])]
        self.external_positions[key] = [
            ExternalPositionRecord.model_validate(item) for item in raw.get("external_positions", [])
        ]
        self.external_cash[key] = [
            ExternalCashRecord.model_validate(item) for item in raw.get("external_cash", [])
        ]
        self.external_fills[key] = [
            ExternalFillRecord.model_validate(item) for item in raw.get("external_fills", [])
        ]
        return bdate


class FixtureBrokerFeedAdapter(BrokerFeedAdapter):
    def __init__(self, dataset: FixtureDataset, fault_state: FaultState) -> None:
        self._dataset = dataset
        self._fault = fault_state

    def health_check(self) -> dict[str, Any]:
        return {
            "adapter": "broker_feed",
            "status": "DOWN" if self._fault.broker_feed_outage else "HEALTHY",
            "detail": self._fault.broker_outage_reason if self._fault.broker_feed_outage else "ok",
        }

    def _guard(self) -> None:
        if self._fault.broker_feed_outage:
            raise ExternalServiceError(
                self._fault.broker_outage_reason,
                code="PB_FEED_UNAVAILABLE",
                details={"adapter": "broker_feed"},
            )

    def fetch_external_positions(self, fund_id: str, business_date: date) -> list[ExternalPositionRecord]:
        self._guard()
        return list(self._dataset.external_positions.get((fund_id, business_date), []))

    def fetch_external_cash(self, fund_id: str, business_date: date) -> list[ExternalCashRecord]:
        self._guard()
        return list(self._dataset.external_cash.get((fund_id, business_date), []))

    def fetch_external_fills(self, fund_id: str, business_date: date) -> list[ExternalFillRecord]:
        self._guard()
        return list(self._dataset.external_fills.get((fund_id, business_date), []))


class FixtureExecutionFeedAdapter(ExecutionFeedAdapter):
    def __init__(self, dataset: FixtureDataset, fault_state: FaultState) -> None:
        self._dataset = dataset
        self._fault = fault_state

    def health_check(self) -> dict[str, Any]:
        return {
            "adapter": "execution_feed",
            "status": "DOWN" if self._fault.execution_feed_outage else "HEALTHY",
            "detail": self._fault.execution_outage_reason if self._fault.execution_feed_outage else "ok",
        }

    def fetch_fills(self, fund_id: str, business_date: date) -> list[TradeFill]:
        if self._fault.execution_feed_outage:
            raise ExternalServiceError(
                self._fault.execution_outage_reason,
                code="EXECUTION_FEED_UNAVAILABLE",
                details={"adapter": "execution_feed"},
            )
        base = list(self._dataset.fills.get((fund_id, business_date), []))
        extras = [
            f
            for f in self._fault.extra_fills
            if f.fund_id == fund_id and (f.business_date is None or f.business_date == business_date)
        ]
        return base + extras


class FixtureMarketDataAdapter(MarketDataAdapter):
    def __init__(self, dataset: FixtureDataset, fault_state: FaultState) -> None:
        self._dataset = dataset
        self._fault = fault_state

    def health_check(self) -> dict[str, Any]:
        return {
            "adapter": "market_data",
            "status": "DOWN" if self._fault.market_data_outage else "HEALTHY",
            "detail": self._fault.market_data_outage_reason if self._fault.market_data_outage else "ok",
        }

    def _guard(self) -> None:
        if self._fault.market_data_outage:
            raise ExternalServiceError(
                self._fault.market_data_outage_reason,
                code="MARKET_DATA_UNAVAILABLE",
                details={"adapter": "market_data"},
            )

    def fetch_marks(self, business_date: date) -> list[MarketMark]:
        self._guard()
        raw_marks = self._dataset.marks.get(business_date, [])
        result: list[MarketMark] = []
        seen: set[str] = set()
        for mark in raw_marks:
            if mark.instrument_id in self._fault.omitted_mark_instruments:
                continue
            if mark.instrument_id in self._fault.override_marks:
                result.append(self._fault.override_marks[mark.instrument_id])
            else:
                result.append(mark)
            seen.add(mark.instrument_id)
        for inst_id, override in self._fault.override_marks.items():
            if inst_id not in seen and inst_id not in self._fault.omitted_mark_instruments:
                result.append(override)
        return result

    def fetch_fx_rates(self, business_date: date) -> list[FxRate]:
        self._guard()
        return list(self._dataset.fx_rates.get(business_date, []))


class LocalFundAdminAdapter(FundAdminAdapter):
    """Writes immutable administrator delivery packages to disk or in-memory outbox."""

    def __init__(self, outbox_dir: str | Path | None = None, fault_state: FaultState | None = None) -> None:
        self._outbox_dir = Path(outbox_dir) if outbox_dir else None
        self._fault = fault_state or FaultState()
        self.deliveries: list[dict[str, Any]] = []

    def deliver_package(self, report: ReportArtifact, export_format: str = "json") -> dict[str, Any]:
        if self._fault.admin_delivery_outage:
            raise ExternalServiceError(
                "External fund administrator SFTP/API gateway unreachable",
                code="FUND_ADMIN_UNAVAILABLE",
                details={"report_id": report.report_id},
            )
        receipt: dict[str, Any] = {
            "delivery_id": f"DELIV_{report.report_id}_v{report.version}",
            "report_id": report.report_id,
            "report_type": report.report_type.value,
            "version": report.version,
            "snapshot_id": report.snapshot_id,
            "content_hash": report.content_hash,
            "format": export_format,
            "delivered_at_utc": datetime.now(UTC).isoformat(),
            "status": "DELIVERED",
        }
        if self._outbox_dir is not None:
            self._outbox_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{report.report_id}_v{report.version}.{export_format}"
            target = self._outbox_dir / filename
            target.write_text(
                json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            receipt["artifact_path"] = str(target)
        self.deliveries.append(receipt)
        return receipt


class InMemoryEventPublisher(EventPublisherAdapter):
    """In-memory and optional JSONL event sink."""

    def __init__(self, jsonl_path: str | Path | None = None) -> None:
        self._events: list[DomainEvent] = []
        self._jsonl_path = Path(jsonl_path) if jsonl_path else None

    def publish(self, event: DomainEvent) -> None:
        self._events.append(event)
        if self._jsonl_path is not None:
            self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            with self._jsonl_path.open("a", encoding="utf-8") as f:
                f.write(event.model_dump_json() + "\n")

    def list_events(self) -> list[DomainEvent]:
        return list(self._events)
