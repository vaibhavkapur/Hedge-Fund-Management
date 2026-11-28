"""Pure domain statement reconciliation and margin shortage/call workflow (zero I/O)."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from pb_collateral.financing import calculate_financing_accruals
from pb_collateral.fx import FxTable
from pb_collateral.models import (
    ApprovalStatus,
    BrokerStatement,
    CollateralPolicy,
    CollateralValuationSummary,
    EncumbranceStatus,
    FinancingRateSchedule,
    InternalLedgerSnapshot,
    MarginCallAction,
    MarginCallNotice,
    ReconciliationBreak,
    ReconciliationResult,
    TransferStatus,
    quantize_money,
)
from pb_collateral.valuation import evaluate_collateral


def _stable_id(prefix: str, parts: Sequence[str]) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefix}-{digest}"


def _round_to_increment(amount: Decimal, increment: Decimal, *, round_up: bool) -> Decimal:
    """Round a non-negative amount to the configured increment (up for calls, down for returns)."""
    if amount <= Decimal("0"):
        return Decimal("0.00")
    units = amount / increment
    rounded_units = units.to_integral_value(rounding=ROUND_CEILING if round_up else ROUND_FLOOR)
    return rounded_units * increment


def build_margin_call_notice(
    *,
    statement: BrokerStatement,
    valuation: CollateralValuationSummary,
    policy: CollateralPolicy,
    rounding_mode: str = "ROUND_HALF_EVEN",
) -> MarginCallNotice:
    """Compute margin call / excess return notice and flag shortfalls prior to settlement."""
    base_ccy = policy.base_currency
    mr = quantize_money(statement.margin_requirement.total_requirement, base_ccy, rounding_mode=rounding_mode)
    posted = valuation.total_posted_collateral_base
    raw_surplus_or_deficit = quantize_money(posted - mr, base_ccy, rounding_mode=rounding_mode)
    mta = quantize_money(policy.minimum_transfer_amount, base_ccy, rounding_mode=rounding_mode)
    inc = policy.rounding_increment
    notes: List[str] = []

    if valuation.quarantined_failed_transfer_lots:
        notes.append(
            "Quarantined failed transfer lots excluded from posted collateral: "
            + ", ".join(valuation.quarantined_failed_transfer_lots)
        )
    if valuation.ineligible_lots:
        notes.append(
            "Ineligible collateral lots assigned 100% haircut: " + ", ".join(valuation.ineligible_lots)
        )

    recommended_lots: List[str] = []
    if raw_surplus_or_deficit < Decimal("0"):
        shortfall = -raw_surplus_or_deficit
        flagged = True
        if shortfall >= mta:
            action = MarginCallAction.MARGIN_CALL
            call_amt = quantize_money(_round_to_increment(shortfall, inc, round_up=True), base_ccy, rounding_mode=rounding_mode)
            approval = ApprovalStatus.AUTO_FLAGGED_SHORTFALL
            notes.append(f"Margin shortfall of {shortfall} {base_ccy} meets/exceeds MTA ({mta} {base_ccy}); call raised for {call_amt} {base_ccy}.")
        else:
            action = MarginCallAction.NO_ACTION
            call_amt = quantize_money(Decimal("0"), base_ccy, rounding_mode=rounding_mode)
            approval = ApprovalStatus.PENDING_REVIEW
            notes.append(f"Margin shortfall of {shortfall} {base_ccy} is below MTA ({mta} {base_ccy}); flagged for pre-settlement monitoring.")

        # Recommend unencumbered eligible lots (lowest haircut first, then largest net value)
        unenc_eligible = [
            p
            for p in valuation.positions
            if p.eligible and p.encumbrance_status == EncumbranceStatus.UNENCUMBERED and p.net_collateral_value_base > Decimal("0")
        ]
        unenc_eligible.sort(key=lambda p: (p.effective_haircut_pct, -p.net_collateral_value_base, p.lot_id))
        covered = Decimal("0")
        target = call_amt if call_amt > Decimal("0") else shortfall
        for pos in unenc_eligible:
            recommended_lots.append(pos.lot_id)
            covered += pos.net_collateral_value_base
            if covered >= target:
                break
        if covered + valuation.free_cash_base < target:
            notes.append(
                f"Insufficient local unencumbered buffer ({valuation.total_available_buffer_base} {base_ccy}) to cover {target} {base_ccy}; cross-broker funding required."
            )
    elif raw_surplus_or_deficit > Decimal("0"):
        excess = raw_surplus_or_deficit
        flagged = False
        if excess >= mta:
            action = MarginCallAction.EXCESS_RETURN
            call_amt = quantize_money(_round_to_increment(excess, inc, round_up=False), base_ccy, rounding_mode=rounding_mode)
            approval = ApprovalStatus.PENDING_REVIEW
            notes.append(f"Excess collateral of {excess} {base_ccy} exceeds MTA ({mta} {base_ccy}); eligible for recall of {call_amt} {base_ccy}.")
        else:
            action = MarginCallAction.NO_ACTION
            call_amt = quantize_money(Decimal("0"), base_ccy, rounding_mode=rounding_mode)
            approval = ApprovalStatus.APPROVED
            notes.append(f"Excess collateral of {excess} {base_ccy} is below MTA ({mta} {base_ccy}); no transfer required.")
    else:
        flagged = False
        action = MarginCallAction.NO_ACTION
        call_amt = quantize_money(Decimal("0"), base_ccy, rounding_mode=rounding_mode)
        approval = ApprovalStatus.APPROVED
        notes.append("Posted collateral matches margin requirement exactly.")

    call_id = _stable_id(
        f"MC-{statement.broker_id}-{statement.business_date.isoformat()}-V{statement.version}",
        [
            statement.statement_id,
            statement.content_hash,
            policy.policy_id,
            str(policy.version),
            str(raw_surplus_or_deficit),
        ],
    )

    return MarginCallNotice(
        call_id=call_id,
        broker_id=statement.broker_id,
        account_id=statement.account_id,
        business_date=statement.business_date,
        base_currency=base_ccy,
        margin_requirement_base=mr,
        posted_eligible_collateral_base=posted,
        raw_surplus_or_deficit_base=raw_surplus_or_deficit,
        minimum_transfer_amount_base=mta,
        rounding_increment_base=inc,
        action=action,
        call_or_return_amount_base=call_amt,
        unencumbered_buffer_base=valuation.total_available_buffer_base,
        flagged_before_settlement=flagged,
        approval_status=approval,
        recommended_funding_lots=tuple(recommended_lots),
        notes=tuple(notes),
    )


def reconcile_statement(
    *,
    statement: BrokerStatement,
    internal_snapshot: InternalLedgerSnapshot,
    policy: CollateralPolicy,
    financing_schedules: Mapping[Tuple[str, str], FinancingRateSchedule],
    fx_table: FxTable,
    accrual_start_date: Optional[date] = None,
    accrual_end_date: Optional[date] = None,
    externally_encumbered_lots: Optional[Set[str]] = None,
    rounding_mode: str = "ROUND_HALF_EVEN",
) -> ReconciliationResult:
    """Reconcile broker statement against internal ledger, independent valuation, and financing accruals."""
    base_ccy = policy.base_currency
    tolerance = policy.reconciliation_tolerance
    end_dt = accrual_end_date or statement.business_date
    start_dt = accrual_start_date or (end_dt - timedelta(days=1))

    # Evaluate collateral on broker statement (applying internal transfer failure statuses to prevent optimistic counting)
    valuation = evaluate_collateral(
        broker_id=statement.broker_id,
        account_id=statement.account_id,
        business_date=statement.business_date,
        positions=statement.positions,
        cash_balances=statement.cash_balances,
        policy=policy,
        fx_table=fx_table,
        transfers=internal_snapshot.transfers,
        externally_encumbered_lots=externally_encumbered_lots,
        rounding_mode=rounding_mode,
    )

    financing = calculate_financing_accruals(
        broker_id=statement.broker_id,
        account_id=statement.account_id,
        cash_balances=statement.cash_balances,
        schedules=financing_schedules,
        fx_table=fx_table,
        base_currency=base_ccy,
        start_date=start_dt,
        end_date=end_dt,
        rounding_mode=rounding_mode,
    )

    breaks: List[ReconciliationBreak] = []
    seq = 0

    def add_break(
        category: str,
        severity: str,
        ref_id: str,
        currency: str,
        broker_val: Decimal,
        internal_val: Decimal,
        description: str,
    ) -> None:
        nonlocal seq
        seq += 1
        diff = quantize_money(broker_val - internal_val, currency, rounding_mode=rounding_mode)
        brk_id = _stable_id(
            f"BRK-{statement.broker_id}-{statement.business_date.isoformat()}",
            [statement.statement_id, str(seq), category, ref_id, str(diff)],
        )
        breaks.append(
            ReconciliationBreak(
                break_id=brk_id,
                broker_id=statement.broker_id,
                account_id=statement.account_id,
                business_date=statement.business_date,
                category=category,
                severity=severity,
                reference_id=ref_id,
                currency=currency,
                broker_value=broker_val,
                internal_value=internal_val,
                difference=diff,
                description=description,
            )
        )

    # 1. Reconcile positions by lot_id
    broker_pos_map = {p.lot_id: p for p in statement.positions}
    internal_pos_map = {p.lot_id: p for p in internal_snapshot.positions}
    all_lot_ids = sorted(set(broker_pos_map) | set(internal_pos_map))

    failed_transfers_by_lot = {
        t.lot_id: t
        for t in internal_snapshot.transfers
        if t.status == TransferStatus.FAILED and (t.to_broker_id == statement.broker_id or t.from_broker_id == statement.broker_id)
    }

    for lot_id in all_lot_ids:
        bp = broker_pos_map.get(lot_id)
        ip = internal_pos_map.get(lot_id)

        if lot_id in failed_transfers_by_lot:
            ft = failed_transfers_by_lot[lot_id]
            broker_qty = bp.quantity if bp else Decimal("0")
            internal_qty = ip.quantity if ip else Decimal("0")
            add_break(
                "FAILED_TRANSFER_BREAK",
                "HIGH",
                ft.transfer_id,
                ft.currency,
                bp.gross_market_value_local if bp else Decimal("0.00"),
                Decimal("0.00"),
                f"Transfer '{ft.transfer_id}' for lot '{lot_id}' failed ({ft.failure_reason or 'settlement failure'}); "
                f"broker qty={broker_qty}, internal qty={internal_qty}; lot quarantined from posted collateral.",
            )
            continue

        if bp is not None and ip is None:
            add_break(
                "MISSING_INTERNAL_POSITION",
                "HIGH",
                lot_id,
                bp.currency,
                quantize_money(bp.gross_market_value_local, bp.currency, rounding_mode=rounding_mode),
                Decimal("0.00"),
                f"Broker statement includes lot '{lot_id}' ({bp.security_id}) absent from internal snapshot.",
            )
        elif bp is None and ip is not None:
            add_break(
                "MISSING_BROKER_POSITION",
                "HIGH",
                lot_id,
                ip.currency,
                Decimal("0.00"),
                quantize_money(ip.gross_market_value_local, ip.currency, rounding_mode=rounding_mode),
                f"Internal snapshot includes lot '{lot_id}' ({ip.security_id}) missing from broker statement.",
            )
        elif bp is not None and ip is not None:
            if bp.quantity != ip.quantity:
                add_break(
                    "POSITION_QUANTITY_BREAK",
                    "HIGH",
                    lot_id,
                    bp.currency,
                    bp.quantity,
                    ip.quantity,
                    f"Quantity mismatch on lot '{lot_id}': broker={bp.quantity}, internal={ip.quantity}.",
                )
            elif abs(bp.gross_market_value_local - ip.gross_market_value_local) > tolerance:
                add_break(
                    "POSITION_VALUATION_BREAK",
                    "MEDIUM",
                    lot_id,
                    bp.currency,
                    quantize_money(bp.gross_market_value_local, bp.currency, rounding_mode=rounding_mode),
                    quantize_money(ip.gross_market_value_local, ip.currency, rounding_mode=rounding_mode),
                    f"Market valuation mismatch on lot '{lot_id}': broker={bp.gross_market_value_local}, internal={ip.gross_market_value_local}.",
                )

    # 2. Reconcile cash balances by (balance_type, currency)
    broker_cash: Dict[Tuple[str, str], Decimal] = {}
    internal_cash: Dict[Tuple[str, str], Decimal] = {}
    for cb in statement.cash_balances:
        key = (cb.balance_type.value, cb.currency)
        broker_cash[key] = broker_cash.get(key, Decimal("0")) + cb.amount
    for cb in internal_snapshot.cash_balances:
        key = (cb.balance_type.value, cb.currency)
        internal_cash[key] = internal_cash.get(key, Decimal("0")) + cb.amount

    for key in sorted(set(broker_cash) | set(internal_cash)):
        b_amt = quantize_money(broker_cash.get(key, Decimal("0")), key[1], rounding_mode=rounding_mode)
        i_amt = quantize_money(internal_cash.get(key, Decimal("0")), key[1], rounding_mode=rounding_mode)
        if abs(b_amt - i_amt) > tolerance:
            add_break(
                "CASH_BALANCE_BREAK",
                "HIGH",
                f"{key[0]}:{key[1]}",
                key[1],
                b_amt,
                i_amt,
                f"Cash balance break for {key[0]} ({key[1]}): broker={b_amt}, internal={i_amt}.",
            )

    # 3. Reconcile broker-reported eligible collateral vs independently calculated posted collateral
    if statement.reported_collateral_value is not None:
        reported_col = quantize_money(statement.reported_collateral_value, base_ccy, rounding_mode=rounding_mode)
        calc_col = valuation.total_posted_collateral_base
        if abs(reported_col - calc_col) > tolerance:
            category = (
                "ELIGIBILITY_OR_HAIRCUT_BREAK"
                if (valuation.ineligible_lots or any(p.concentration_trim_base > Decimal("0") for p in valuation.positions))
                else "COLLATERAL_VALUATION_BREAK"
            )
            add_break(
                category,
                "HIGH",
                statement.statement_id,
                base_ccy,
                reported_col,
                calc_col,
                f"Broker reported collateral value ({reported_col} {base_ccy}) differs from policy-calculated posted collateral ({calc_col} {base_ccy}).",
            )

    # 4. Reconcile broker-reported financing accrual vs independently calculated accrual
    if statement.reported_financing_accrual is not None:
        reported_fin = quantize_money(statement.reported_financing_accrual, base_ccy, rounding_mode=rounding_mode)
        calc_fin = financing.net_accrual_base
        if abs(reported_fin - calc_fin) > tolerance:
            add_break(
                "FINANCING_ACCRUAL_BREAK",
                "MEDIUM",
                statement.statement_id,
                base_ccy,
                reported_fin,
                calc_fin,
                f"Broker reported financing accrual ({reported_fin} {base_ccy}) differs from calculated accrual ({calc_fin} {base_ccy}).",
            )

    margin_call = build_margin_call_notice(
        statement=statement,
        valuation=valuation,
        policy=policy,
        rounding_mode=rounding_mode,
    )

    run_id = _stable_id(
        f"RUN-{statement.broker_id}-{statement.business_date.isoformat()}-V{statement.version}",
        [
            statement.statement_id,
            statement.content_hash,
            internal_snapshot.snapshot_id,
            str(internal_snapshot.version),
            policy.policy_id,
            str(policy.version),
            start_dt.isoformat(),
            end_dt.isoformat(),
        ],
    )

    return ReconciliationResult(
        run_id=run_id,
        statement_id=statement.statement_id,
        statement_version=statement.version,
        broker_id=statement.broker_id,
        account_id=statement.account_id,
        business_date=statement.business_date,
        base_currency=base_ccy,
        duplicate_skipped=False,
        valuation=valuation,
        financing=financing,
        breaks=tuple(breaks),
        margin_call=margin_call,
        reconciled_clean=len(breaks) == 0,
    )
