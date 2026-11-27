"""Clean-room adapter contracts separating external providers from core business logic.

These interfaces map cleanly to the external dependencies identified in plan.md:
- BrokerFeedAdapter      <-> Trading-Software / 13-broker-reliability-reconciliation
- ExecutionFeedAdapter   <-> High-Frequency-Trading-Engine (read-only fill ingestion; execution disabled)
- MarketDataAdapter      <-> Personal-Finance-Agents / Personal-Trading-Desk market data contract
- FundAdminAdapter       <-> External fund administrator / custodian reporting extract handoff
- EventPublisherAdapter  <-> Event integration bus / outbox
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

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


class BrokerFeedAdapter(ABC):
    """Provides external prime broker / custodian EOD positions, cash balances, and broker fills."""

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """Return provider health metadata."""

    @abstractmethod
    def fetch_external_positions(self, fund_id: str, business_date: date) -> list[ExternalPositionRecord]:
        """Fetch external EOD position records for the given fund and date."""

    @abstractmethod
    def fetch_external_cash(self, fund_id: str, business_date: date) -> list[ExternalCashRecord]:
        """Fetch external EOD cash balances for the given fund and date."""

    @abstractmethod
    def fetch_external_fills(self, fund_id: str, business_date: date) -> list[ExternalFillRecord]:
        """Fetch external trade confirmations/fills for the given fund and date."""


class ExecutionFeedAdapter(ABC):
    """Read-only ingestion of internal OMS / execution engine fills.

    Order submission is intentionally absent from this interface; see ExecutionControlsConfig.
    """

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """Return execution feed health metadata."""

    @abstractmethod
    def fetch_fills(self, fund_id: str, business_date: date) -> list[TradeFill]:
        """Fetch internal trade fills for the business date."""


class MarketDataAdapter(ABC):
    """Provides point-in-time EOD market marks and FX conversion rates."""

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        """Return market data provider health metadata."""

    @abstractmethod
    def fetch_marks(self, business_date: date) -> list[MarketMark]:
        """Fetch EOD market marks for the business date."""

    @abstractmethod
    def fetch_fx_rates(self, business_date: date) -> list[FxRate]:
        """Fetch EOD FX rates to base currency (USD) for the business date."""


class FundAdminAdapter(ABC):
    """Exports frozen, approved reporting packages to external fund administrator / auditor."""

    @abstractmethod
    def deliver_package(self, report: ReportArtifact, export_format: str = "json") -> dict[str, Any]:
        """Deliver a published report package and return delivery receipt metadata."""


class EventPublisherAdapter(ABC):
    """Publishes versioned domain events for downstream consumers."""

    @abstractmethod
    def publish(self, event: DomainEvent) -> None:
        """Publish an immutable domain event."""

    @abstractmethod
    def list_events(self) -> list[DomainEvent]:
        """Return published events."""
