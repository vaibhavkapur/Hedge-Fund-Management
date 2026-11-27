"""Top-level HedgeFundPlatform control plane facade wiring services, adapters, storage, and controls."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hf_platform.adapters.fixture_adapters import (
    FaultState,
    FixtureBrokerFeedAdapter,
    FixtureDataset,
    FixtureExecutionFeedAdapter,
    FixtureMarketDataAdapter,
    InMemoryEventPublisher,
    LocalFundAdminAdapter,
)
from hf_platform.config import PlatformConfig
from hf_platform.domain import EventType
from hf_platform.errors import ExecutionDisabledError
from hf_platform.services.audit_service import AuditService
from hf_platform.services.auth_service import AuthService
from hf_platform.services.close_service import CloseService
from hf_platform.services.exception_service import ExceptionService
from hf_platform.services.ledger_service import LedgerService
from hf_platform.services.master_service import MasterDataService
from hf_platform.services.portfolio_risk_service import PortfolioRiskService
from hf_platform.services.reconciliation_service import ReconciliationService
from hf_platform.services.reporting_service import ReportingService
from hf_platform.storage.sqlite_store import SQLiteStore


class HedgeFundPlatform:
    """Unified hedge fund control plane entrypoint."""

    def __init__(
        self,
        *,
        config: PlatformConfig | None = None,
        db_path: str | Path = ":memory:",
        dataset: FixtureDataset | None = None,
        fault_state: FaultState | None = None,
        outbox_dir: str | Path | None = None,
        event_log_path: str | Path | None = None,
    ) -> None:
        self.config = config or PlatformConfig()
        self.store = SQLiteStore(db_path)
        self.dataset = dataset or FixtureDataset()
        self.fault_state = fault_state or FaultState()

        self.broker_adapter = FixtureBrokerFeedAdapter(self.dataset, self.fault_state)
        self.execution_adapter = FixtureExecutionFeedAdapter(self.dataset, self.fault_state)
        self.market_data_adapter = FixtureMarketDataAdapter(self.dataset, self.fault_state)
        self.admin_adapter = LocalFundAdminAdapter(outbox_dir=outbox_dir, fault_state=self.fault_state)
        self.event_publisher = InMemoryEventPublisher(jsonl_path=event_log_path)

        self.audit = AuditService(self.store, self.event_publisher)
        self.auth = AuthService(self.store, self.audit)
        self.master = MasterDataService(self.store, self.audit)
        self.ledger = LedgerService(self.store, self.master, self.auth, self.audit)
        self.portfolio = PortfolioRiskService(self.store, self.master, self.ledger, self.audit, self.config)
        self.reconciliation = ReconciliationService(
            self.store, self.master, self.ledger, self.portfolio, self.audit, self.config
        )
        self.exceptions = ExceptionService(self.store, self.auth, self.audit, self.config)
        self.close = CloseService(
            store=self.store,
            master=self.master,
            ledger=self.ledger,
            portfolio=self.portfolio,
            recon=self.reconciliation,
            exceptions=self.exceptions,
            auth=self.auth,
            audit=self.audit,
            broker_adapter=self.broker_adapter,
            execution_adapter=self.execution_adapter,
            market_data_adapter=self.market_data_adapter,
            config=self.config,
        )
        self.reporting = ReportingService(self.store, self.auth, self.audit, self.admin_adapter)

    def submit_order(self, *, actor_id: str, fund_id: str, symbol: str, quantity: str) -> None:
        """Hard kill-switch: live/automated order execution is disabled by default policy."""
        if not self.config.execution_controls.order_execution_enabled:
            user = self.store.get_user(actor_id)
            self.audit.record(
                actor_id=actor_id,
                actor_role=user.role if user else None,
                action="execution.submit_order",
                entity_type="order",
                entity_id=f"{fund_id}:{symbol}",
                outcome="DENIED",
                details={
                    "policy_reference": self.config.execution_controls.policy_reference,
                    "symbol": symbol,
                    "quantity": quantity,
                },
            )
            self.audit.emit_event(
                event_type=EventType.EXECUTION_BLOCKED,
                fund_id=fund_id,
                payload={"actor_id": actor_id, "symbol": symbol, "quantity": quantity},
            )
            raise ExecutionDisabledError(
                f"Order execution is disabled by policy {self.config.execution_controls.policy_reference}",
                details={"policy_reference": self.config.execution_controls.policy_reference},
            )

    def health_and_metrics(self) -> dict[str, Any]:
        """Return operational health, adapter states, audit integrity, and control-plane metrics."""
        adapters = [
            self.broker_adapter.health_check(),
            self.execution_adapter.health_check(),
            self.market_data_adapter.health_check(),
        ]
        all_healthy = all(a["status"] == "HEALTHY" for a in adapters)
        audit_status = self.audit.verify_chain()
        funds = self.master.list_funds()
        all_exceptions = self.store.list_exceptions()
        open_exceptions = [e for e in all_exceptions if e.status.value not in ("RESOLVED", "WAIVED")]
        return {
            "status": "HEALTHY" if all_healthy else "DEGRADED",
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "config_version": self.config.config_version,
            "environment": self.config.environment,
            "order_execution_enabled": self.config.execution_controls.order_execution_enabled,
            "adapters": adapters,
            "audit_integrity": audit_status,
            "metrics": {
                "fund_count": len(funds),
                "total_exceptions": len(all_exceptions),
                "open_exceptions": len(open_exceptions),
                "audit_entries": audit_status["entry_count"],
                "events_emitted": len(self.store.list_events()),
            },
        }

    def shutdown(self) -> None:
        self.store.close()
