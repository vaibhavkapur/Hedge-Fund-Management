"""Double-entry fee journaling, correction reversals, and reconciliation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import List, Sequence, Set, Tuple

from .errors import ReconciliationError
from .models import (
    EngineConfig,
    JournalEntry,
    JournalEntryType,
    JournalLine,
    LotFeeCalculation,
    ReconciliationReport,
    stable_hash,
)


def _zero(currency: str, config: EngineConfig) -> Decimal:
    return config.quantize_money(Decimal("0"), currency)


def _make_entry_id(run_id: str, entry_type: JournalEntryType, lot_id: str, seq: int) -> str:
    digest = stable_hash(
        {"run_id": run_id, "entry_type": entry_type.value, "lot_id": lot_id, "seq": seq},
        length=10,
    )
    return f"JE-{run_id[:8]}-{seq:03d}-{digest}"


def build_journal_entries(
    *,
    run_id: str,
    version: int,
    posting_date: date,
    valuation_date: date,
    currency: str,
    lot_calculations: Sequence[LotFeeCalculation],
    config: EngineConfig,
    is_correction: bool = False,
) -> Tuple[JournalEntry, ...]:
    """Generate balanced double-entry journal entries for all fee calculations."""
    coa = config.chart_of_accounts
    zero = _zero(currency, config)
    entries: List[JournalEntry] = []
    seq = 1

    for calc in lot_calculations:
        # 1. Management fee accrual
        if calc.management_fee_amount > zero:
            entry_type = (
                JournalEntryType.CORRECTION_ENTRY
                if is_correction
                else JournalEntryType.MANAGEMENT_FEE_ACCRUAL
            )
            lines = (
                JournalLine(
                    account_code=coa["management_fee_expense"].code,
                    account_name=coa["management_fee_expense"].name,
                    debit=calc.management_fee_amount,
                    credit=zero,
                    currency=currency,
                    share_class=calc.share_class,
                    lot_id=calc.lot_id,
                    memo=f"Management fee accrual {calc.period_start_date}..{calc.valuation_date}",
                ),
                JournalLine(
                    account_code=coa["management_fee_payable"].code,
                    account_name=coa["management_fee_payable"].name,
                    debit=zero,
                    credit=calc.management_fee_amount,
                    currency=currency,
                    share_class=calc.share_class,
                    lot_id=calc.lot_id,
                    memo=f"Management fee payable {calc.period_start_date}..{calc.valuation_date}",
                ),
            )
            entries.append(
                JournalEntry(
                    entry_id=_make_entry_id(run_id, entry_type, calc.lot_id, seq),
                    run_id=run_id,
                    version=version,
                    posting_date=posting_date,
                    valuation_date=calc.valuation_date,
                    currency=currency,
                    entry_type=entry_type,
                    description=f"Management fee accrual for lot {calc.lot_id}",
                    lines=lines,
                )
            )
            seq += 1

        # 2. Performance fee incremental accrual or intra-period reversal
        if calc.performance_fee_delta > zero:
            entry_type = (
                JournalEntryType.CORRECTION_ENTRY
                if is_correction
                else JournalEntryType.PERFORMANCE_FEE_ACCRUAL
            )
            lines = (
                JournalLine(
                    account_code=coa["performance_fee_expense"].code,
                    account_name=coa["performance_fee_expense"].name,
                    debit=calc.performance_fee_delta,
                    credit=zero,
                    currency=currency,
                    share_class=calc.share_class,
                    lot_id=calc.lot_id,
                    memo=f"Performance fee accrual {calc.valuation_date}",
                ),
                JournalLine(
                    account_code=coa["performance_fee_accrued"].code,
                    account_name=coa["performance_fee_accrued"].name,
                    debit=zero,
                    credit=calc.performance_fee_delta,
                    currency=currency,
                    share_class=calc.share_class,
                    lot_id=calc.lot_id,
                    memo=f"Performance fee accrued liability {calc.valuation_date}",
                ),
            )
            entries.append(
                JournalEntry(
                    entry_id=_make_entry_id(run_id, entry_type, calc.lot_id, seq),
                    run_id=run_id,
                    version=version,
                    posting_date=posting_date,
                    valuation_date=calc.valuation_date,
                    currency=currency,
                    entry_type=entry_type,
                    description=f"Performance fee accrual for lot {calc.lot_id}",
                    lines=lines,
                )
            )
            seq += 1

        elif calc.performance_fee_delta < zero:
            rev_amount = abs(calc.performance_fee_delta)
            entry_type = JournalEntryType.PERFORMANCE_FEE_REVERSAL
            lines = (
                JournalLine(
                    account_code=coa["performance_fee_accrued"].code,
                    account_name=coa["performance_fee_accrued"].name,
                    debit=rev_amount,
                    credit=zero,
                    currency=currency,
                    share_class=calc.share_class,
                    lot_id=calc.lot_id,
                    memo=f"Intra-period performance fee reversal {calc.valuation_date}",
                ),
                JournalLine(
                    account_code=coa["performance_fee_expense"].code,
                    account_name=coa["performance_fee_expense"].name,
                    debit=zero,
                    credit=rev_amount,
                    currency=currency,
                    share_class=calc.share_class,
                    lot_id=calc.lot_id,
                    memo=f"Intra-period performance fee expense reversal {calc.valuation_date}",
                ),
            )
            entries.append(
                JournalEntry(
                    entry_id=_make_entry_id(run_id, entry_type, calc.lot_id, seq),
                    run_id=run_id,
                    version=version,
                    posting_date=posting_date,
                    valuation_date=calc.valuation_date,
                    currency=currency,
                    entry_type=entry_type,
                    description=f"Intra-period performance fee reversal for lot {calc.lot_id}",
                    lines=lines,
                )
            )
            seq += 1

        # 3. Performance fee crystallization transfer: Accrued Liability -> Payable
        if calc.is_crystallization_event and calc.crystallized_performance_fee > zero:
            if calc.crystallization_reason.startswith("REDEMPTION:"):
                entry_type = JournalEntryType.REDEMPTION_CRYSTALLIZATION
            else:
                entry_type = JournalEntryType.PERFORMANCE_FEE_CRYSTALLIZATION
            lines = (
                JournalLine(
                    account_code=coa["performance_fee_accrued"].code,
                    account_name=coa["performance_fee_accrued"].name,
                    debit=calc.crystallized_performance_fee,
                    credit=zero,
                    currency=currency,
                    share_class=calc.share_class,
                    lot_id=calc.lot_id,
                    memo=f"Crystallize accrued perf fee ({calc.crystallization_reason})",
                ),
                JournalLine(
                    account_code=coa["performance_fee_payable"].code,
                    account_name=coa["performance_fee_payable"].name,
                    debit=zero,
                    credit=calc.crystallized_performance_fee,
                    currency=currency,
                    share_class=calc.share_class,
                    lot_id=calc.lot_id,
                    memo=f"Performance fee crystallized payable ({calc.crystallization_reason})",
                ),
            )
            entries.append(
                JournalEntry(
                    entry_id=_make_entry_id(run_id, entry_type, calc.lot_id, seq),
                    run_id=run_id,
                    version=version,
                    posting_date=posting_date,
                    valuation_date=calc.valuation_date,
                    currency=currency,
                    entry_type=entry_type,
                    description=f"Performance fee crystallization for lot {calc.lot_id} ({calc.crystallization_reason})",
                    lines=lines,
                )
            )
            seq += 1

    return tuple(entries)


def build_reversal_entries(
    *,
    prior_entries: Sequence[JournalEntry],
    new_run_id: str,
    new_version: int,
    posting_date: date,
    reason: str,
) -> Tuple[JournalEntry, ...]:
    """Create immutable reversing journal entries for a superseded run."""
    reversals: List[JournalEntry] = []
    for idx, prior in enumerate(prior_entries, start=1):
        swapped_lines = tuple(
            JournalLine(
                account_code=line.account_code,
                account_name=line.account_name,
                debit=line.credit,
                credit=line.debit,
                currency=line.currency,
                share_class=line.share_class,
                lot_id=line.lot_id,
                memo=f"Reversal of {prior.entry_id}: {reason}",
            )
            for line in prior.lines
        )
        reversals.append(
            JournalEntry(
                entry_id=_make_entry_id(new_run_id, JournalEntryType.CORRECTION_REVERSAL, prior.entry_id, idx),
                run_id=new_run_id,
                version=new_version,
                posting_date=posting_date,
                valuation_date=prior.valuation_date,
                currency=prior.currency,
                entry_type=JournalEntryType.CORRECTION_REVERSAL,
                description=f"Reversal of {prior.entry_id} ({reason})",
                lines=swapped_lines,
                supersedes_entry_id=prior.entry_id,
            )
        )
    return tuple(reversals)


def reconcile_run(
    *,
    lot_calculations: Sequence[LotFeeCalculation],
    journal_entries: Sequence[JournalEntry],
    currency: str,
    config: EngineConfig,
    allow_negative_accrual: bool = False,
) -> ReconciliationReport:
    """Verify accounting invariants across lot calculations and journal entries.

    Raises ReconciliationError if any invariant fails.
    """
    coa = config.chart_of_accounts
    zero = _zero(currency, config)

    total_debits = zero
    total_credits = zero
    all_entries_balanced = True
    no_negative_line_amounts = True
    seen_accrual_keys: Set[Tuple[str, str, str, int]] = set()
    no_duplicate_accruals = True

    mgmt_journaled = zero
    perf_journaled = zero
    crystallized_journaled = zero

    for entry in journal_entries:
        if entry.total_debits != entry.total_credits:
            all_entries_balanced = False
        total_debits += entry.total_debits
        total_credits += entry.total_credits

        for line in entry.lines:
            if line.debit < zero or line.credit < zero:
                no_negative_line_amounts = False
            if entry.entry_type != JournalEntryType.CORRECTION_REVERSAL:
                if line.account_code == coa["management_fee_expense"].code:
                    mgmt_journaled += line.debit - line.credit
                elif line.account_code == coa["performance_fee_expense"].code:
                    perf_journaled += line.debit - line.credit
                elif line.account_code == coa["performance_fee_payable"].code:
                    crystallized_journaled += line.credit - line.debit

        if entry.entry_type in (
            JournalEntryType.MANAGEMENT_FEE_ACCRUAL,
            JournalEntryType.PERFORMANCE_FEE_ACCRUAL,
            JournalEntryType.PERFORMANCE_FEE_CRYSTALLIZATION,
            JournalEntryType.REDEMPTION_CRYSTALLIZATION,
        ):
            lot_ids = {line.lot_id for line in entry.lines}
            for lid in lot_ids:
                key = (entry.valuation_date.isoformat(), lid, entry.entry_type.value, entry.version)
                if key in seen_accrual_keys:
                    no_duplicate_accruals = False
                seen_accrual_keys.add(key)

    mgmt_calc = sum((c.management_fee_amount for c in lot_calculations), zero)
    perf_calc = sum((c.performance_fee_delta for c in lot_calculations), zero)
    cryst_calc = sum((c.crystallized_performance_fee for c in lot_calculations), zero)
    total_gross = sum((c.gross_ending_nav for c in lot_calculations), zero)
    total_net = sum((c.ending_net_nav for c in lot_calculations), zero)
    total_effective_perf = sum((c.target_cumulative_perf_fee for c in lot_calculations), zero)

    nav_bridge_diff = (total_gross - mgmt_calc - total_effective_perf) - total_net
    journal_imbalance = total_debits - total_credits

    no_negative_fee_accrual = True
    if not allow_negative_accrual:
        for calc in lot_calculations:
            if calc.management_fee_amount < zero or calc.target_cumulative_perf_fee < zero:
                no_negative_fee_accrual = False

    checks = {
        "all_entries_balanced": all_entries_balanced and journal_imbalance == zero,
        "no_negative_line_amounts": no_negative_line_amounts,
        "no_negative_fee_accrual": no_negative_fee_accrual,
        "no_duplicate_accruals": no_duplicate_accruals,
        "management_fee_reconciles": mgmt_calc == mgmt_journaled,
        "performance_fee_reconciles": perf_calc == perf_journaled,
        "crystallized_perf_reconciles": cryst_calc == crystallized_journaled,
        "nav_bridge_reconciles": nav_bridge_diff == zero,
    }
    reconciled = all(checks.values())

    report = ReconciliationReport(
        reconciled=reconciled,
        total_journal_debits=total_debits,
        total_journal_credits=total_credits,
        journal_imbalance=journal_imbalance,
        total_management_fee_calculated=mgmt_calc,
        total_management_fee_journaled=mgmt_journaled,
        total_performance_fee_calculated=perf_calc,
        total_performance_fee_journaled=perf_journaled,
        total_crystallized_perf_fee=cryst_calc,
        total_gross_nav=total_gross,
        total_net_nav=total_net,
        nav_bridge_difference=nav_bridge_diff,
        checks=checks,
    )

    if not reconciled:
        failed = [k for k, ok in checks.items() if not ok]
        raise ReconciliationError(
            f"Run failed reconciliation checks: {', '.join(failed)}",
            code="RECONCILIATION_FAILED",
            field="reconciliation",
        )
    return report
