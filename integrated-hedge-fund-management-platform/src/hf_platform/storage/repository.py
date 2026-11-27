"""Persistence repository contract (implemented by SQLiteStore; PostgreSQL-ready DDL)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from hf_platform.domain import (
    Account,
    AuditLogEntry,
    Counterparty,
    DailyCloseRun,
    DomainEvent,
    Fund,
    Instrument,
    JournalEntry,
    MarketMark,
    OperationalException,
    PortfolioSnapshot,
    ReconciliationResult,
    ReportArtifact,
    Strategy,
    TradeFill,
    UserPrincipal,
)


class StorageRepository(ABC):
    """Canonical persistence boundary for the hedge fund control plane."""

    @abstractmethod
    def close(self) -> None: ...

    # Master Data
    @abstractmethod
    def save_fund(self, fund: Fund) -> None: ...
    @abstractmethod
    def get_fund(self, fund_id: str) -> Fund | None: ...
    @abstractmethod
    def list_funds(self) -> list[Fund]: ...

    @abstractmethod
    def save_counterparty(self, cp: Counterparty) -> None: ...
    @abstractmethod
    def list_counterparties(self) -> list[Counterparty]: ...

    @abstractmethod
    def save_account(self, account: Account) -> None: ...
    @abstractmethod
    def list_accounts(self, fund_id: str | None = None) -> list[Account]: ...

    @abstractmethod
    def save_strategy(self, strategy: Strategy) -> None: ...
    @abstractmethod
    def list_strategies(self, fund_id: str | None = None) -> list[Strategy]: ...

    @abstractmethod
    def save_instrument(self, instrument: Instrument) -> None: ...
    @abstractmethod
    def list_instruments(self) -> list[Instrument]: ...

    @abstractmethod
    def save_user(self, user: UserPrincipal) -> None: ...
    @abstractmethod
    def get_user(self, user_id: str) -> UserPrincipal | None: ...
    @abstractmethod
    def list_users(self) -> list[UserPrincipal]: ...

    # Fills & Manual Marks
    @abstractmethod
    def save_fill(self, fill: TradeFill) -> bool:
        """Idempotent fill save. Returns True if inserted, False if already present."""

    @abstractmethod
    def list_fills(self, fund_id: str, up_to_date: date | None = None, business_date: date | None = None) -> list[TradeFill]: ...

    @abstractmethod
    def save_manual_mark(self, business_date: date, mark: MarketMark) -> None: ...
    @abstractmethod
    def list_manual_marks(self, business_date: date) -> list[MarketMark]: ...

    # Ledger
    @abstractmethod
    def save_journal_entry(self, entry: JournalEntry) -> bool:
        """Idempotent journal entry save. Returns True if newly persisted."""

    @abstractmethod
    def list_journal_entries(self, fund_id: str, up_to_date: date | None = None) -> list[JournalEntry]: ...

    # Portfolio Snapshots
    @abstractmethod
    def save_snapshot(self, snapshot: PortfolioSnapshot) -> None: ...
    @abstractmethod
    def get_snapshot(self, snapshot_id: str) -> PortfolioSnapshot | None: ...
    @abstractmethod
    def list_snapshots(self, fund_id: str, business_date: date | None = None) -> list[PortfolioSnapshot]: ...

    # Reconciliation
    @abstractmethod
    def save_reconciliation(self, recon: ReconciliationResult) -> None: ...
    @abstractmethod
    def get_reconciliation(self, reconciliation_id: str) -> ReconciliationResult | None: ...
    @abstractmethod
    def list_reconciliations(self, fund_id: str, business_date: date | None = None) -> list[ReconciliationResult]: ...

    # Daily Close
    @abstractmethod
    def save_close_run(self, close_run: DailyCloseRun) -> None: ...
    @abstractmethod
    def get_close_run(self, close_run_id: str) -> DailyCloseRun | None: ...
    @abstractmethod
    def list_close_runs(self, fund_id: str, business_date: date | None = None) -> list[DailyCloseRun]: ...

    # Exceptions
    @abstractmethod
    def save_exception(self, exc: OperationalException) -> None: ...
    @abstractmethod
    def get_exception(self, exception_id: str) -> OperationalException | None: ...
    @abstractmethod
    def list_exceptions(self, fund_id: str | None = None, business_date: date | None = None) -> list[OperationalException]: ...

    # Reports
    @abstractmethod
    def save_report(self, report: ReportArtifact) -> None: ...
    @abstractmethod
    def get_report(self, report_id: str) -> ReportArtifact | None: ...
    @abstractmethod
    def list_reports(self, fund_id: str, business_date: date | None = None) -> list[ReportArtifact]: ...

    # Append-only Audit & Events
    @abstractmethod
    def append_audit_entry(self, entry: AuditLogEntry) -> None: ...
    @abstractmethod
    def get_last_audit_entry(self) -> AuditLogEntry | None: ...
    @abstractmethod
    def list_audit_entries(self, limit: int | None = None) -> list[AuditLogEntry]: ...

    @abstractmethod
    def append_event(self, event: DomainEvent) -> None: ...
    @abstractmethod
    def list_events(self, limit: int | None = None) -> list[DomainEvent]: ...
