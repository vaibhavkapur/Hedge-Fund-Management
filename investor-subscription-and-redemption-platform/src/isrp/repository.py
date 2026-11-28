"""Relational persistence layer: SQLiteRepository (tested offline) and PostgresRepository adapter."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from .audit import GENESIS_HASH, compute_audit_hash
from .errors import ConfigurationError, NotFoundError
from .models import (
    ApprovalDecision,
    ApprovalRecord,
    AuditEvent,
    CapitalFlowRequest,
    CashConfirmation,
    CashDirection,
    CashMatchStatus,
    DealingWindow,
    ExceptionCase,
    ExceptionStatus,
    ExceptionType,
    InvestorRecord,
    InvestorStatus,
    JournalEntryType,
    NAVRecord,
    NAVStatus,
    OnboardingCheckResult,
    RequestStatus,
    RequestType,
    ShareJournalEntry,
    format_iso_datetime,
    parse_iso_date,
    parse_iso_datetime,
)


class Repository(Protocol):
    def save_investor(self, investor: InvestorRecord) -> None: ...
    def get_investor(self, investor_id: str) -> Optional[InvestorRecord]: ...
    def list_investors(self) -> List[InvestorRecord]: ...

    def save_dealing_window(self, window: DealingWindow) -> None: ...
    def get_dealing_window(self, fund_id: str, share_class: str, dealing_date: date) -> Optional[DealingWindow]: ...
    def list_dealing_windows(self) -> List[DealingWindow]: ...

    def save_nav(self, nav: NAVRecord) -> None: ...
    def get_nav(self, nav_id: str) -> Optional[NAVRecord]: ...
    def get_active_nav(self, fund_id: str, share_class: str, dealing_date: date) -> Optional[NAVRecord]: ...
    def list_navs(self) -> List[NAVRecord]: ...

    def save_request(self, request: CapitalFlowRequest) -> None: ...
    def get_request(self, request_id: str) -> Optional[CapitalFlowRequest]: ...
    def get_request_by_idempotency_key(self, idempotency_key: str) -> Optional[CapitalFlowRequest]: ...
    def list_requests(self) -> List[CapitalFlowRequest]: ...

    def save_approval(self, approval: ApprovalRecord) -> None: ...
    def list_approvals(self, entity_id: Optional[str] = None) -> List[ApprovalRecord]: ...

    def save_cash_confirmation(self, confirmation: CashConfirmation) -> None: ...
    def get_cash_confirmation(self, confirmation_id: str) -> Optional[CashConfirmation]: ...
    def list_cash_confirmations(self, request_id: Optional[str] = None) -> List[CashConfirmation]: ...

    def save_exception(self, exc_case: ExceptionCase) -> None: ...
    def get_exception(self, exception_id: str) -> Optional[ExceptionCase]: ...
    def list_exceptions(self, request_id: Optional[str] = None) -> List[ExceptionCase]: ...

    def append_journal_entry(self, entry: ShareJournalEntry) -> None: ...
    def list_journal_entries(self, request_id: Optional[str] = None) -> List[ShareJournalEntry]: ...

    def append_audit_event(
        self,
        event_id: str,
        timestamp: datetime,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload: Dict[str, Any],
    ) -> AuditEvent: ...
    def list_audit_events(self) -> List[AuditEvent]: ...


SQLITE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS investors (
    investor_id TEXT PRIMARY KEY,
    legal_name TEXT NOT NULL,
    investor_type TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    tax_form_status TEXT NOT NULL,
    accredited INTEGER NOT NULL,
    bank_account_ref TEXT NOT NULL,
    identity_doc_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    onboarding_checks_json TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS dealing_windows (
    window_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    share_class TEXT NOT NULL,
    dealing_date TEXT NOT NULL,
    open_at TEXT NOT NULL,
    cutoff_at TEXT NOT NULL,
    cash_due_date TEXT NOT NULL,
    settlement_date TEXT NOT NULL,
    UNIQUE(fund_id, share_class, dealing_date)
);

CREATE TABLE IF NOT EXISTS nav_records (
    nav_id TEXT PRIMARY KEY,
    fund_id TEXT NOT NULL,
    share_class TEXT NOT NULL,
    dealing_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    nav_per_share TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    supersedes_nav_id TEXT,
    restatement_reason TEXT
);

CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE NOT NULL,
    investor_id TEXT NOT NULL,
    fund_id TEXT NOT NULL,
    share_class TEXT NOT NULL,
    request_type TEXT NOT NULL,
    dealing_date TEXT NOT NULL,
    currency TEXT NOT NULL,
    gross_amount TEXT,
    requested_shares TEXT,
    submitted_at TEXT NOT NULL,
    maker_id TEXT NOT NULL,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    checker_id TEXT,
    approved_at TEXT,
    nav_id TEXT,
    nav_version INTEGER,
    fee_amount TEXT,
    net_cash TEXT,
    calculated_shares TEXT,
    rounding_residual TEXT,
    cancellation_reason TEXT,
    cancelled_by TEXT,
    FOREIGN KEY(investor_id) REFERENCES investors(investor_id)
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    maker_id TEXT NOT NULL,
    checker_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cash_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    currency TEXT NOT NULL,
    amount TEXT NOT NULL,
    value_date TEXT NOT NULL,
    bank_reference TEXT NOT NULL,
    received_at TEXT NOT NULL,
    match_status TEXT NOT NULL,
    discrepancy_amount TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exceptions (
    exception_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    exception_type TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    reviewer_id TEXT,
    resolution_note TEXT,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS share_journal (
    entry_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    investor_id TEXT NOT NULL,
    fund_id TEXT NOT NULL,
    share_class TEXT NOT NULL,
    currency TEXT NOT NULL,
    dealing_date TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    share_delta TEXT NOT NULL,
    gross_cash_delta TEXT NOT NULL,
    fee_delta TEXT NOT NULL,
    net_cash_delta TEXT NOT NULL,
    nav_id TEXT NOT NULL,
    nav_version INTEGER NOT NULL,
    posted_at TEXT NOT NULL,
    posted_by TEXT NOT NULL,
    version INTEGER NOT NULL,
    supersedes_entry_id TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    sequence INTEGER PRIMARY KEY,
    event_id TEXT UNIQUE NOT NULL,
    timestamp TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    entry_hash TEXT NOT NULL
);
"""


class SQLiteRepository:
    """Relational SQLite persistence implementing append-only journal and hash-chained audit."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SQLITE_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- Investors --

    def save_investor(self, investor: InvestorRecord) -> None:
        checks_json = json.dumps([c.to_dict() for c in investor.onboarding_checks], sort_keys=True)
        self._conn.execute(
            """
            INSERT INTO investors (
                investor_id, legal_name, investor_type, jurisdiction, tax_form_status,
                accredited, bank_account_ref, identity_doc_ref, status, version,
                onboarding_checks_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(investor_id) DO UPDATE SET
                legal_name=excluded.legal_name,
                investor_type=excluded.investor_type,
                jurisdiction=excluded.jurisdiction,
                tax_form_status=excluded.tax_form_status,
                accredited=excluded.accredited,
                bank_account_ref=excluded.bank_account_ref,
                identity_doc_ref=excluded.identity_doc_ref,
                status=excluded.status,
                version=excluded.version,
                onboarding_checks_json=excluded.onboarding_checks_json,
                updated_at=excluded.updated_at
            """,
            (
                investor.investor_id,
                investor.legal_name,
                investor.investor_type,
                investor.jurisdiction,
                investor.tax_form_status,
                1 if investor.accredited else 0,
                investor.bank_account_ref,
                investor.identity_doc_ref,
                investor.status.value,
                investor.version,
                checks_json,
                format_iso_datetime(investor.updated_at) if investor.updated_at else None,
            ),
        )
        self._conn.commit()

    def _row_to_investor(self, row: sqlite3.Row) -> InvestorRecord:
        raw_checks = json.loads(row["onboarding_checks_json"])
        checks = tuple(
            OnboardingCheckResult(
                check_name=c["check_name"],
                passed=bool(c["passed"]),
                reason=c["reason"],
                mock_disclaimer=c.get("mock_disclaimer", "SYNTHETIC_MOCK_CHECK_NOT_LEGAL_COMPLIANCE"),
            )
            for c in raw_checks
        )
        return InvestorRecord(
            investor_id=row["investor_id"],
            legal_name=row["legal_name"],
            investor_type=row["investor_type"],
            jurisdiction=row["jurisdiction"],
            tax_form_status=row["tax_form_status"],
            accredited=bool(row["accredited"]),
            bank_account_ref=row["bank_account_ref"],
            identity_doc_ref=row["identity_doc_ref"],
            status=InvestorStatus(row["status"]),
            version=int(row["version"]),
            onboarding_checks=checks,
            updated_at=parse_iso_datetime(row["updated_at"], "updated_at") if row["updated_at"] else None,
        )

    def get_investor(self, investor_id: str) -> Optional[InvestorRecord]:
        row = self._conn.execute("SELECT * FROM investors WHERE investor_id = ?", (investor_id,)).fetchone()
        return self._row_to_investor(row) if row else None

    def list_investors(self) -> List[InvestorRecord]:
        rows = self._conn.execute("SELECT * FROM investors ORDER BY investor_id").fetchall()
        return [self._row_to_investor(r) for r in rows]

    # -- Dealing windows --

    def save_dealing_window(self, window: DealingWindow) -> None:
        self._conn.execute(
            """
            INSERT INTO dealing_windows (
                window_id, fund_id, share_class, dealing_date, open_at, cutoff_at, cash_due_date, settlement_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(window_id) DO UPDATE SET
                open_at=excluded.open_at,
                cutoff_at=excluded.cutoff_at,
                cash_due_date=excluded.cash_due_date,
                settlement_date=excluded.settlement_date
            """,
            (
                window.window_id,
                window.fund_id,
                window.share_class,
                window.dealing_date.isoformat(),
                format_iso_datetime(window.open_at),
                format_iso_datetime(window.cutoff_at),
                window.cash_due_date.isoformat(),
                window.settlement_date.isoformat(),
            ),
        )
        self._conn.commit()

    def _row_to_window(self, row: sqlite3.Row) -> DealingWindow:
        return DealingWindow(
            window_id=row["window_id"],
            fund_id=row["fund_id"],
            share_class=row["share_class"],
            dealing_date=parse_iso_date(row["dealing_date"], "dealing_date"),
            open_at=parse_iso_datetime(row["open_at"], "open_at"),
            cutoff_at=parse_iso_datetime(row["cutoff_at"], "cutoff_at"),
            cash_due_date=parse_iso_date(row["cash_due_date"], "cash_due_date"),
            settlement_date=parse_iso_date(row["settlement_date"], "settlement_date"),
        )

    def get_dealing_window(self, fund_id: str, share_class: str, dealing_date: date) -> Optional[DealingWindow]:
        row = self._conn.execute(
            "SELECT * FROM dealing_windows WHERE fund_id = ? AND share_class = ? AND dealing_date = ?",
            (fund_id, share_class, dealing_date.isoformat()),
        ).fetchone()
        return self._row_to_window(row) if row else None

    def list_dealing_windows(self) -> List[DealingWindow]:
        rows = self._conn.execute("SELECT * FROM dealing_windows ORDER BY dealing_date, window_id").fetchall()
        return [self._row_to_window(r) for r in rows]

    # -- NAV records --

    def save_nav(self, nav: NAVRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO nav_records (
                nav_id, fund_id, share_class, dealing_date, currency, nav_per_share,
                version, status, approved_by, approved_at, supersedes_nav_id, restatement_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(nav_id) DO UPDATE SET
                status=excluded.status,
                restatement_reason=excluded.restatement_reason
            """,
            (
                nav.nav_id,
                nav.fund_id,
                nav.share_class,
                nav.dealing_date.isoformat(),
                nav.currency,
                str(nav.nav_per_share),
                nav.version,
                nav.status.value,
                nav.approved_by,
                format_iso_datetime(nav.approved_at),
                nav.supersedes_nav_id,
                nav.restatement_reason,
            ),
        )
        self._conn.commit()

    def _row_to_nav(self, row: sqlite3.Row) -> NAVRecord:
        return NAVRecord(
            nav_id=row["nav_id"],
            fund_id=row["fund_id"],
            share_class=row["share_class"],
            dealing_date=parse_iso_date(row["dealing_date"], "dealing_date"),
            currency=row["currency"],
            nav_per_share=Decimal(row["nav_per_share"]),
            version=int(row["version"]),
            status=NAVStatus(row["status"]),
            approved_by=row["approved_by"],
            approved_at=parse_iso_datetime(row["approved_at"], "approved_at"),
            supersedes_nav_id=row["supersedes_nav_id"],
            restatement_reason=row["restatement_reason"],
        )

    def get_nav(self, nav_id: str) -> Optional[NAVRecord]:
        row = self._conn.execute("SELECT * FROM nav_records WHERE nav_id = ?", (nav_id,)).fetchone()
        return self._row_to_nav(row) if row else None

    def get_active_nav(self, fund_id: str, share_class: str, dealing_date: date) -> Optional[NAVRecord]:
        row = self._conn.execute(
            """
            SELECT * FROM nav_records
            WHERE fund_id = ? AND share_class = ? AND dealing_date = ? AND status = ?
            ORDER BY version DESC LIMIT 1
            """,
            (fund_id, share_class, dealing_date.isoformat(), NAVStatus.APPROVED.value),
        ).fetchone()
        return self._row_to_nav(row) if row else None

    def list_navs(self) -> List[NAVRecord]:
        rows = self._conn.execute("SELECT * FROM nav_records ORDER BY dealing_date, version, nav_id").fetchall()
        return [self._row_to_nav(r) for r in rows]

    # -- Requests --

    def save_request(self, request: CapitalFlowRequest) -> None:
        self._conn.execute(
            """
            INSERT INTO requests (
                request_id, idempotency_key, investor_id, fund_id, share_class, request_type,
                dealing_date, currency, gross_amount, requested_shares, submitted_at, maker_id,
                status, version, checker_id, approved_at, nav_id, nav_version, fee_amount,
                net_cash, calculated_shares, rounding_residual, cancellation_reason, cancelled_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                status=excluded.status,
                version=excluded.version,
                checker_id=excluded.checker_id,
                approved_at=excluded.approved_at,
                nav_id=excluded.nav_id,
                nav_version=excluded.nav_version,
                gross_amount=excluded.gross_amount,
                fee_amount=excluded.fee_amount,
                net_cash=excluded.net_cash,
                calculated_shares=excluded.calculated_shares,
                rounding_residual=excluded.rounding_residual,
                cancellation_reason=excluded.cancellation_reason,
                cancelled_by=excluded.cancelled_by
            """,
            (
                request.request_id,
                request.idempotency_key,
                request.investor_id,
                request.fund_id,
                request.share_class,
                request.request_type.value,
                request.dealing_date.isoformat(),
                request.currency,
                str(request.gross_amount) if request.gross_amount is not None else None,
                str(request.requested_shares) if request.requested_shares is not None else None,
                format_iso_datetime(request.submitted_at),
                request.maker_id,
                request.status.value,
                request.version,
                request.checker_id,
                format_iso_datetime(request.approved_at) if request.approved_at else None,
                request.nav_id,
                request.nav_version,
                str(request.fee_amount) if request.fee_amount is not None else None,
                str(request.net_cash) if request.net_cash is not None else None,
                str(request.calculated_shares) if request.calculated_shares is not None else None,
                str(request.rounding_residual) if request.rounding_residual is not None else None,
                request.cancellation_reason,
                request.cancelled_by,
            ),
        )
        self._conn.commit()

    def _row_to_request(self, row: sqlite3.Row) -> CapitalFlowRequest:
        return CapitalFlowRequest(
            request_id=row["request_id"],
            idempotency_key=row["idempotency_key"],
            investor_id=row["investor_id"],
            fund_id=row["fund_id"],
            share_class=row["share_class"],
            request_type=RequestType(row["request_type"]),
            dealing_date=parse_iso_date(row["dealing_date"], "dealing_date"),
            currency=row["currency"],
            gross_amount=Decimal(row["gross_amount"]) if row["gross_amount"] is not None else None,
            requested_shares=Decimal(row["requested_shares"]) if row["requested_shares"] is not None else None,
            submitted_at=parse_iso_datetime(row["submitted_at"], "submitted_at"),
            maker_id=row["maker_id"],
            status=RequestStatus(row["status"]),
            version=int(row["version"]),
            checker_id=row["checker_id"],
            approved_at=parse_iso_datetime(row["approved_at"], "approved_at") if row["approved_at"] else None,
            nav_id=row["nav_id"],
            nav_version=int(row["nav_version"]) if row["nav_version"] is not None else None,
            fee_amount=Decimal(row["fee_amount"]) if row["fee_amount"] is not None else None,
            net_cash=Decimal(row["net_cash"]) if row["net_cash"] is not None else None,
            calculated_shares=Decimal(row["calculated_shares"]) if row["calculated_shares"] is not None else None,
            rounding_residual=Decimal(row["rounding_residual"]) if row["rounding_residual"] is not None else None,
            cancellation_reason=row["cancellation_reason"],
            cancelled_by=row["cancelled_by"],
        )

    def get_request(self, request_id: str) -> Optional[CapitalFlowRequest]:
        row = self._conn.execute("SELECT * FROM requests WHERE request_id = ?", (request_id,)).fetchone()
        return self._row_to_request(row) if row else None

    def get_request_by_idempotency_key(self, idempotency_key: str) -> Optional[CapitalFlowRequest]:
        row = self._conn.execute(
            "SELECT * FROM requests WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        return self._row_to_request(row) if row else None

    def list_requests(self) -> List[CapitalFlowRequest]:
        rows = self._conn.execute("SELECT * FROM requests ORDER BY submitted_at, request_id").fetchall()
        return [self._row_to_request(r) for r in rows]

    # -- Approvals --

    def save_approval(self, approval: ApprovalRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO approvals (
                approval_id, entity_type, entity_id, maker_id, checker_id, decision, reason, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval.approval_id,
                approval.entity_type,
                approval.entity_id,
                approval.maker_id,
                approval.checker_id,
                approval.decision.value,
                approval.reason,
                format_iso_datetime(approval.decided_at),
            ),
        )
        self._conn.commit()

    def list_approvals(self, entity_id: Optional[str] = None) -> List[ApprovalRecord]:
        if entity_id:
            rows = self._conn.execute(
                "SELECT * FROM approvals WHERE entity_id = ? ORDER BY decided_at, approval_id", (entity_id,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM approvals ORDER BY decided_at, approval_id").fetchall()
        return [
            ApprovalRecord(
                approval_id=r["approval_id"],
                entity_type=r["entity_type"],
                entity_id=r["entity_id"],
                maker_id=r["maker_id"],
                checker_id=r["checker_id"],
                decision=ApprovalDecision(r["decision"]),
                reason=r["reason"],
                decided_at=parse_iso_datetime(r["decided_at"], "decided_at"),
            )
            for r in rows
        ]

    # -- Cash confirmations --

    def save_cash_confirmation(self, confirmation: CashConfirmation) -> None:
        self._conn.execute(
            """
            INSERT INTO cash_confirmations (
                confirmation_id, request_id, direction, currency, amount, value_date,
                bank_reference, received_at, match_status, discrepancy_amount
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(confirmation_id) DO UPDATE SET
                match_status=excluded.match_status,
                discrepancy_amount=excluded.discrepancy_amount
            """,
            (
                confirmation.confirmation_id,
                confirmation.request_id,
                confirmation.direction.value,
                confirmation.currency,
                str(confirmation.amount),
                confirmation.value_date.isoformat(),
                confirmation.bank_reference,
                format_iso_datetime(confirmation.received_at),
                confirmation.match_status.value,
                str(confirmation.discrepancy_amount),
            ),
        )
        self._conn.commit()

    def _row_to_cash(self, r: sqlite3.Row) -> CashConfirmation:
        return CashConfirmation(
            confirmation_id=r["confirmation_id"],
            request_id=r["request_id"],
            direction=CashDirection(r["direction"]),
            currency=r["currency"],
            amount=Decimal(r["amount"]),
            value_date=parse_iso_date(r["value_date"], "value_date"),
            bank_reference=r["bank_reference"],
            received_at=parse_iso_datetime(r["received_at"], "received_at"),
            match_status=CashMatchStatus(r["match_status"]),
            discrepancy_amount=Decimal(r["discrepancy_amount"]),
        )

    def get_cash_confirmation(self, confirmation_id: str) -> Optional[CashConfirmation]:
        row = self._conn.execute(
            "SELECT * FROM cash_confirmations WHERE confirmation_id = ?", (confirmation_id,)
        ).fetchone()
        return self._row_to_cash(row) if row else None

    def list_cash_confirmations(self, request_id: Optional[str] = None) -> List[CashConfirmation]:
        if request_id:
            rows = self._conn.execute(
                "SELECT * FROM cash_confirmations WHERE request_id = ? ORDER BY received_at, confirmation_id",
                (request_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM cash_confirmations ORDER BY received_at, confirmation_id"
            ).fetchall()
        return [self._row_to_cash(r) for r in rows]

    # -- Exceptions --

    def save_exception(self, exc_case: ExceptionCase) -> None:
        self._conn.execute(
            """
            INSERT INTO exceptions (
                exception_id, request_id, exception_type, status, summary, created_by,
                created_at, details_json, reviewer_id, resolution_note, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exception_id) DO UPDATE SET
                status=excluded.status,
                reviewer_id=excluded.reviewer_id,
                resolution_note=excluded.resolution_note,
                resolved_at=excluded.resolved_at
            """,
            (
                exc_case.exception_id,
                exc_case.request_id,
                exc_case.exception_type.value,
                exc_case.status.value,
                exc_case.summary,
                exc_case.created_by,
                format_iso_datetime(exc_case.created_at),
                json.dumps(exc_case.details, sort_keys=True),
                exc_case.reviewer_id,
                exc_case.resolution_note,
                format_iso_datetime(exc_case.resolved_at) if exc_case.resolved_at else None,
            ),
        )
        self._conn.commit()

    def _row_to_exception(self, r: sqlite3.Row) -> ExceptionCase:
        return ExceptionCase(
            exception_id=r["exception_id"],
            request_id=r["request_id"],
            exception_type=ExceptionType(r["exception_type"]),
            status=ExceptionStatus(r["status"]),
            summary=r["summary"],
            created_by=r["created_by"],
            created_at=parse_iso_datetime(r["created_at"], "created_at"),
            details=json.loads(r["details_json"]),
            reviewer_id=r["reviewer_id"],
            resolution_note=r["resolution_note"],
            resolved_at=parse_iso_datetime(r["resolved_at"], "resolved_at") if r["resolved_at"] else None,
        )

    def get_exception(self, exception_id: str) -> Optional[ExceptionCase]:
        row = self._conn.execute("SELECT * FROM exceptions WHERE exception_id = ?", (exception_id,)).fetchone()
        return self._row_to_exception(row) if row else None

    def list_exceptions(self, request_id: Optional[str] = None) -> List[ExceptionCase]:
        if request_id:
            rows = self._conn.execute(
                "SELECT * FROM exceptions WHERE request_id = ? ORDER BY created_at, exception_id",
                (request_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM exceptions ORDER BY created_at, exception_id").fetchall()
        return [self._row_to_exception(r) for r in rows]

    # -- Immutable Share Journal (append-only) --

    def append_journal_entry(self, entry: ShareJournalEntry) -> None:
        self._conn.execute(
            """
            INSERT INTO share_journal (
                entry_id, request_id, investor_id, fund_id, share_class, currency, dealing_date,
                entry_type, share_delta, gross_cash_delta, fee_delta, net_cash_delta, nav_id,
                nav_version, posted_at, posted_by, version, supersedes_entry_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.entry_id,
                entry.request_id,
                entry.investor_id,
                entry.fund_id,
                entry.share_class,
                entry.currency,
                entry.dealing_date.isoformat(),
                entry.entry_type.value,
                str(entry.share_delta),
                str(entry.gross_cash_delta),
                str(entry.fee_delta),
                str(entry.net_cash_delta),
                entry.nav_id,
                entry.nav_version,
                format_iso_datetime(entry.posted_at),
                entry.posted_by,
                entry.version,
                entry.supersedes_entry_id,
            ),
        )
        self._conn.commit()

    def list_journal_entries(self, request_id: Optional[str] = None) -> List[ShareJournalEntry]:
        if request_id:
            rows = self._conn.execute(
                "SELECT * FROM share_journal WHERE request_id = ? ORDER BY posted_at, entry_id", (request_id,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM share_journal ORDER BY posted_at, entry_id").fetchall()
        return [
            ShareJournalEntry(
                entry_id=r["entry_id"],
                request_id=r["request_id"],
                investor_id=r["investor_id"],
                fund_id=r["fund_id"],
                share_class=r["share_class"],
                currency=r["currency"],
                dealing_date=parse_iso_date(r["dealing_date"], "dealing_date"),
                entry_type=JournalEntryType(r["entry_type"]),
                share_delta=Decimal(r["share_delta"]),
                gross_cash_delta=Decimal(r["gross_cash_delta"]),
                fee_delta=Decimal(r["fee_delta"]),
                net_cash_delta=Decimal(r["net_cash_delta"]),
                nav_id=r["nav_id"],
                nav_version=int(r["nav_version"]),
                posted_at=parse_iso_datetime(r["posted_at"], "posted_at"),
                posted_by=r["posted_by"],
                version=int(r["version"]),
                supersedes_entry_id=r["supersedes_entry_id"],
            )
            for r in rows
        ]

    # -- Append-only Hash-Chained Audit Log --

    def append_audit_event(
        self,
        event_id: str,
        timestamp: datetime,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload: Dict[str, Any],
    ) -> AuditEvent:
        last = self._conn.execute(
            "SELECT sequence, entry_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if last is None:
            sequence = 1
            prev_hash = GENESIS_HASH
        else:
            sequence = int(last["sequence"]) + 1
            prev_hash = str(last["entry_hash"])

        entry_hash = compute_audit_hash(
            sequence=sequence,
            event_id=event_id,
            timestamp=timestamp,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            prev_hash=prev_hash,
        )
        self._conn.execute(
            """
            INSERT INTO audit_events (
                sequence, event_id, timestamp, actor_id, action, entity_type, entity_id,
                payload_json, prev_hash, entry_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sequence,
                event_id,
                format_iso_datetime(timestamp),
                actor_id,
                action,
                entity_type,
                entity_id,
                json.dumps(payload, sort_keys=True),
                prev_hash,
                entry_hash,
            ),
        )
        self._conn.commit()
        return AuditEvent(
            sequence=sequence,
            event_id=event_id,
            timestamp=timestamp,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
        )

    def list_audit_events(self) -> List[AuditEvent]:
        rows = self._conn.execute("SELECT * FROM audit_events ORDER BY sequence ASC").fetchall()
        return [
            AuditEvent(
                sequence=int(r["sequence"]),
                event_id=r["event_id"],
                timestamp=parse_iso_datetime(r["timestamp"], "timestamp"),
                actor_id=r["actor_id"],
                action=r["action"],
                entity_type=r["entity_type"],
                entity_id=r["entity_id"],
                payload=json.loads(r["payload_json"]),
                prev_hash=r["prev_hash"],
                entry_hash=r["entry_hash"],
            )
            for r in rows
        ]


class PostgresRepository:
    """PostgreSQL repository adapter requiring `psycopg` and a live PostgreSQL instance.

    NOTE: In offline environments without PostgreSQL/psycopg, instantiate SQLiteRepository instead.
    The schema DDL is maintained in `src/isrp/migrations/001_initial_schema.sql`.
    """

    def __init__(self, dsn: str) -> None:
        if not dsn or not dsn.startswith(("postgresql://", "postgres://")):
            raise ConfigurationError(
                "Invalid PostgreSQL DSN; expected 'postgresql://...'.",
                {"dsn_prefix": dsn[:16] if dsn else ""},
            )
        try:
            import psycopg  # type: ignore
        except ImportError as exc:
            raise ConfigurationError(
                "PostgreSQL support requires optional dependency 'psycopg[binary]'. "
                "Install extras or use SQLiteRepository for offline execution."
            ) from exc
        self._dsn = dsn
        self._psycopg = psycopg
        self._conn = psycopg.connect(dsn)
        ddl_path = Path(__file__).parent / "migrations" / "001_initial_schema.sql"
        with self._conn.cursor() as cur:
            cur.execute(ddl_path.read_text(encoding="utf-8"))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
