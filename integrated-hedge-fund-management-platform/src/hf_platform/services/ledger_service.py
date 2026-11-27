"""Double-entry fund accounting ledger service with strict balance invariants and trial balance."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from hf_platform.domain import (
    Currency,
    EventType,
    JournalEntry,
    JournalLine,
    LedgerAccountType,
    TradeFill,
    TradeSide,
    TrialBalance,
    TrialBalanceLine,
)
from hf_platform.errors import LedgerBalanceError
from hf_platform.services.audit_service import AuditService
from hf_platform.services.auth_service import AuthService
from hf_platform.services.master_service import MasterDataService
from hf_platform.storage.repository import StorageRepository

CENT = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class LedgerService:
    """Posts balanced double-entry journal entries and verifies trial balance integrity."""

    def __init__(
        self,
        store: StorageRepository,
        master: MasterDataService,
        auth: AuthService,
        audit: AuditService,
    ) -> None:
        self._store = store
        self._master = master
        self._auth = auth
        self._audit = audit

    def ensure_opening_capital(self, fund_id: str, actor_id: str) -> list[JournalEntry]:
        """Idempotently post opening investor capital & initial cash per account/currency."""
        fund = self._master.get_fund(fund_id)
        accounts = self._master.list_accounts(fund_id)
        created: list[JournalEntry] = []
        for acct in accounts:
            for ccy, amount in sorted(acct.initial_cash.items(), key=lambda kv: kv[0].value):
                if amount <= Decimal("0"):
                    continue
                entry_id = f"JE_OPEN_{fund_id}_{acct.account_id}_{ccy.value}"
                entry = JournalEntry(
                    entry_id=entry_id,
                    fund_id=fund_id,
                    business_date=fund.inception_date,
                    description=f"Opening investor capital funding for {acct.account_id} ({ccy.value})",
                    source_ref=f"OPENING:{acct.account_id}:{ccy.value}",
                    created_by=actor_id,
                    lines=[
                        JournalLine(
                            account_id=acct.account_id,
                            ledger_account=LedgerAccountType.CASH,
                            currency=ccy,
                            debit=q2(amount),
                            memo="Initial cash funding",
                        ),
                        JournalLine(
                            account_id=acct.account_id,
                            ledger_account=LedgerAccountType.INVESTOR_CAPITAL,
                            currency=ccy,
                            credit=q2(amount),
                            memo="Investor capital subscription",
                        ),
                    ],
                )
                if self._store.save_journal_entry(entry):
                    created.append(entry)
                    self._audit.record(
                        actor_id=actor_id,
                        actor_role=self._store.get_user(actor_id).role if self._store.get_user(actor_id) else None,
                        action="ledger.post_opening_capital",
                        entity_type="journal_entry",
                        entity_id=entry.entry_id,
                        details={"fund_id": fund_id, "currency": ccy.value, "amount": str(q2(amount))},
                    )
        return created

    def post_fill(self, fill: TradeFill, business_date: date, actor_id: str) -> JournalEntry | None:
        """Idempotently convert a trade fill into a balanced double-entry journal entry."""
        notional = q2(fill.gross_notional_local)
        comm = q2(fill.commission)
        lines: list[JournalLine] = []
        if fill.side == TradeSide.BUY:
            lines.append(
                JournalLine(
                    account_id=fill.account_id,
                    ledger_account=LedgerAccountType.SECURITIES_LONG,
                    currency=fill.currency,
                    debit=notional,
                    instrument_id=fill.instrument_id,
                    strategy_id=fill.strategy_id,
                    memo=f"Buy {fill.quantity} @ {fill.price}",
                )
            )
            lines.append(
                JournalLine(
                    account_id=fill.account_id,
                    ledger_account=LedgerAccountType.CASH,
                    currency=fill.currency,
                    credit=notional,
                    instrument_id=fill.instrument_id,
                    strategy_id=fill.strategy_id,
                    memo=f"Cash settlement for {fill.fill_id}",
                )
            )
        else:
            lines.append(
                JournalLine(
                    account_id=fill.account_id,
                    ledger_account=LedgerAccountType.CASH,
                    currency=fill.currency,
                    debit=notional,
                    instrument_id=fill.instrument_id,
                    strategy_id=fill.strategy_id,
                    memo=f"Sale proceeds for {fill.fill_id}",
                )
            )
            lines.append(
                JournalLine(
                    account_id=fill.account_id,
                    ledger_account=LedgerAccountType.SECURITIES_SHORT,
                    currency=fill.currency,
                    credit=notional,
                    instrument_id=fill.instrument_id,
                    strategy_id=fill.strategy_id,
                    memo=f"Sell {fill.quantity} @ {fill.price}",
                )
            )

        if comm > Decimal("0"):
            lines.append(
                JournalLine(
                    account_id=fill.account_id,
                    ledger_account=LedgerAccountType.COMMISSION_EXPENSE,
                    currency=fill.currency,
                    debit=comm,
                    instrument_id=fill.instrument_id,
                    strategy_id=fill.strategy_id,
                    memo=f"Commission on {fill.fill_id}",
                )
            )
            lines.append(
                JournalLine(
                    account_id=fill.account_id,
                    ledger_account=LedgerAccountType.CASH,
                    currency=fill.currency,
                    credit=comm,
                    instrument_id=fill.instrument_id,
                    strategy_id=fill.strategy_id,
                    memo=f"Commission cash debit for {fill.fill_id}",
                )
            )

        entry = JournalEntry(
            entry_id=f"JE_FILL_{fill.fill_id}",
            fund_id=fill.fund_id,
            business_date=business_date,
            description=f"Trade fill {fill.fill_id} ({fill.side.value} {fill.quantity} {fill.instrument_id})",
            source_ref=fill.fill_id,
            created_by=actor_id,
            lines=lines,
        )
        if self._store.save_journal_entry(entry):
            self._audit.emit_event(
                event_type=EventType.JOURNAL_POSTED,
                fund_id=fill.fund_id,
                payload={"entry_id": entry.entry_id, "source_ref": fill.fill_id},
            )
            return entry
        return None

    def post_manual_entry(
        self,
        *,
        entry_id: str,
        fund_id: str,
        business_date: date,
        description: str,
        source_ref: str,
        lines: list[dict],
        actor_id: str,
        supersedes_entry_id: str | None = None,
    ) -> JournalEntry:
        """Validate RBAC and strict double-entry balance before posting a manual journal entry."""
        user = self._auth.authorize(
            actor_id=actor_id,
            action="ledger.post",
            entity_type="journal_entry",
            entity_id=entry_id,
            fund_id=fund_id,
        )
        try:
            parsed_lines = [JournalLine.model_validate(line) for line in lines]
            entry = JournalEntry(
                entry_id=entry_id,
                fund_id=fund_id,
                business_date=business_date,
                description=description,
                source_ref=source_ref,
                lines=parsed_lines,
                created_by=user.user_id,
                supersedes_entry_id=supersedes_entry_id,
            )
        except LedgerBalanceError as exc:
            self._audit.record(
                actor_id=user.user_id,
                actor_role=user.role,
                action="ledger.post_manual",
                entity_type="journal_entry",
                entity_id=entry_id,
                outcome="FAILED",
                details={"error_code": exc.code, "imbalances": exc.details},
            )
            self._audit.emit_event(
                event_type=EventType.JOURNAL_REJECTED,
                fund_id=fund_id,
                payload={"entry_id": entry_id, "error_code": exc.code, "details": exc.details},
            )
            raise

        self._store.save_journal_entry(entry)
        self._audit.record(
            actor_id=user.user_id,
            actor_role=user.role,
            action="ledger.post_manual",
            entity_type="journal_entry",
            entity_id=entry.entry_id,
            details={"fund_id": fund_id, "line_count": len(entry.lines), "source_ref": source_ref},
        )
        self._audit.emit_event(
            event_type=EventType.JOURNAL_POSTED,
            fund_id=fund_id,
            payload={"entry_id": entry.entry_id, "source_ref": source_ref},
        )
        return entry

    def post_eod_mtm_and_fees(
        self,
        *,
        fund_id: str,
        business_date: date,
        target_mv_by_currency: dict[Currency, Decimal],
        daily_management_fee_usd: Decimal,
        actor_id: str,
    ) -> list[JournalEntry]:
        """Post idempotent EOD mark-to-market adjustment and daily management fee accrual."""
        accounts = self._master.list_accounts(fund_id)
        primary_acct = accounts[0].account_id
        entries = self._store.list_journal_entries(fund_id, up_to_date=business_date)

        # Compute current ledger net securities carrying value per currency (excluding any re-run of today's MTM)
        sec_carrying: dict[Currency, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for je in entries:
            for line in je.lines:
                if line.ledger_account in (LedgerAccountType.SECURITIES_LONG, LedgerAccountType.SECURITIES_SHORT):
                    sec_carrying[line.currency] += line.debit - line.credit

        created: list[JournalEntry] = []
        all_ccys = sorted(set(target_mv_by_currency.keys()) | set(sec_carrying.keys()), key=lambda c: c.value)
        for ccy in all_ccys:
            target_mv = q2(target_mv_by_currency.get(ccy, Decimal("0.00")))
            current_carrying = q2(sec_carrying.get(ccy, Decimal("0.00")))
            delta = q2(target_mv - current_carrying)
            if delta == Decimal("0.00"):
                continue
            base_entry_id = f"JE_MTM_{fund_id}_{business_date.isoformat()}_{ccy.value}"
            existing_mtm = [e for e in entries if e.entry_id.startswith(base_entry_id)]
            version = len(existing_mtm) + 1
            entry_id = base_entry_id if version == 1 else f"{base_entry_id}_v{version}"
            supersedes = existing_mtm[-1].entry_id if existing_mtm else None
            if delta > Decimal("0"):
                lines = [
                    JournalLine(
                        account_id=primary_acct,
                        ledger_account=LedgerAccountType.SECURITIES_LONG,
                        currency=ccy,
                        debit=delta,
                        memo=f"EOD MTM valuation gain ({business_date.isoformat()})",
                    ),
                    JournalLine(
                        account_id=primary_acct,
                        ledger_account=LedgerAccountType.UNREALIZED_PNL,
                        currency=ccy,
                        credit=delta,
                        memo=f"EOD MTM P&L recognition ({business_date.isoformat()})",
                    ),
                ]
            else:
                abs_delta = abs(delta)
                lines = [
                    JournalLine(
                        account_id=primary_acct,
                        ledger_account=LedgerAccountType.UNREALIZED_PNL,
                        currency=ccy,
                        debit=abs_delta,
                        memo=f"EOD MTM P&L loss ({business_date.isoformat()})",
                    ),
                    JournalLine(
                        account_id=primary_acct,
                        ledger_account=LedgerAccountType.SECURITIES_LONG,
                        currency=ccy,
                        credit=abs_delta,
                        memo=f"EOD MTM valuation write-down ({business_date.isoformat()})",
                    ),
                ]
            mtm_entry = JournalEntry(
                entry_id=entry_id,
                fund_id=fund_id,
                business_date=business_date,
                description=f"EOD Mark-to-Market adjustment for {business_date.isoformat()} ({ccy.value})",
                source_ref=f"EOD_MTM:{business_date.isoformat()}:{ccy.value}",
                created_by=actor_id,
                version=version,
                supersedes_entry_id=supersedes,
                lines=lines,
            )
            if self._store.save_journal_entry(mtm_entry):
                created.append(mtm_entry)

        fee_amt = q2(daily_management_fee_usd)
        if fee_amt > Decimal("0.00"):
            fee_entry = JournalEntry(
                entry_id=f"JE_FEE_{fund_id}_{business_date.isoformat()}",
                fund_id=fund_id,
                business_date=business_date,
                description=f"Daily management fee accrual for {business_date.isoformat()}",
                source_ref=f"MGMT_FEE:{business_date.isoformat()}",
                created_by=actor_id,
                lines=[
                    JournalLine(
                        account_id=primary_acct,
                        ledger_account=LedgerAccountType.MANAGEMENT_FEE_EXPENSE,
                        currency=Currency.USD,
                        debit=fee_amt,
                        memo="Daily management fee expense",
                    ),
                    JournalLine(
                        account_id=primary_acct,
                        ledger_account=LedgerAccountType.ACCRUED_FEES_PAYABLE,
                        currency=Currency.USD,
                        credit=fee_amt,
                        memo="Accrued management fees payable",
                    ),
                ],
            )
            if self._store.save_journal_entry(fee_entry):
                created.append(fee_entry)

        return created

    def compute_trial_balance(self, fund_id: str, business_date: date) -> TrialBalance:
        entries = self._store.list_journal_entries(fund_id, up_to_date=business_date)
        buckets: dict[tuple[LedgerAccountType, Currency], list[Decimal]] = defaultdict(
            lambda: [Decimal("0.00"), Decimal("0.00")]
        )
        totals_by_ccy: dict[str, dict[str, Decimal]] = {}

        for je in entries:
            for line in je.lines:
                b = buckets[(line.ledger_account, line.currency)]
                b[0] += line.debit
                b[1] += line.credit
                c_key = line.currency.value
                if c_key not in totals_by_ccy:
                    totals_by_ccy[c_key] = {"total_debit": Decimal("0.00"), "total_credit": Decimal("0.00")}
                totals_by_ccy[c_key]["total_debit"] += line.debit
                totals_by_ccy[c_key]["total_credit"] += line.credit

        lines = [
            TrialBalanceLine(
                ledger_account=acct,
                currency=ccy,
                total_debit=q2(vals[0]),
                total_credit=q2(vals[1]),
                net_balance=q2(vals[0] - vals[1]),
            )
            for (acct, ccy), vals in sorted(buckets.items(), key=lambda kv: (kv[0][1].value, kv[0][0].value))
        ]
        is_balanced = all(
            q2(tot["total_debit"]) == q2(tot["total_credit"]) for tot in totals_by_ccy.values()
        )
        return TrialBalance(
            fund_id=fund_id,
            business_date=business_date,
            generated_at_utc=datetime.now(UTC),
            lines=lines,
            totals_by_currency={
                k: {"total_debit": q2(v["total_debit"]), "total_credit": q2(v["total_credit"])}
                for k, v in totals_by_ccy.items()
            },
            is_balanced=is_balanced,
            entry_count=len(entries),
        )

    def cash_balances_by_account_currency(self, fund_id: str, business_date: date) -> dict[tuple[str, Currency], Decimal]:
        entries = self._store.list_journal_entries(fund_id, up_to_date=business_date)
        balances: dict[tuple[str, Currency], Decimal] = defaultdict(lambda: Decimal("0.00"))
        for je in entries:
            for line in je.lines:
                if line.ledger_account == LedgerAccountType.CASH:
                    balances[(line.account_id, line.currency)] += line.debit - line.credit
        return {k: q2(v) for k, v in balances.items()}

    def accrued_fees_usd(self, fund_id: str, business_date: date) -> Decimal:
        entries = self._store.list_journal_entries(fund_id, up_to_date=business_date)
        accrued = Decimal("0.00")
        for je in entries:
            for line in je.lines:
                if line.ledger_account == LedgerAccountType.ACCRUED_FEES_PAYABLE and line.currency == Currency.USD:
                    accrued += line.credit - line.debit
        return q2(accrued)

    def compute_ledger_nav_usd(self, fund_id: str, business_date: date, fx_to_usd: dict[Currency, Decimal]) -> Decimal:
        """Compute Net Asset Value directly from Balance Sheet ledger accounts (Cash + Securities - Accrued Fees)."""
        entries = self._store.list_journal_entries(fund_id, up_to_date=business_date)
        net_by_ccy: dict[Currency, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for je in entries:
            for line in je.lines:
                if line.ledger_account in (
                    LedgerAccountType.CASH,
                    LedgerAccountType.SECURITIES_LONG,
                    LedgerAccountType.SECURITIES_SHORT,
                    LedgerAccountType.ACCRUED_FEES_PAYABLE,
                ):
                    net_by_ccy[line.currency] += line.debit - line.credit
        total_usd = Decimal("0.00")
        for ccy, amt in net_by_ccy.items():
            rate = fx_to_usd[ccy]
            total_usd += amt * rate
        return q2(total_usd)
