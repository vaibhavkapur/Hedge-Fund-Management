"""Pure domain collateral eligibility, haircut, concentration, encumbrance, and anti-double-counting engine."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from pb_collateral.errors import DoubleCountingError
from pb_collateral.fx import FxTable
from pb_collateral.models import (
    AssetClass,
    CashBalanceRecord,
    CashBalanceType,
    CollateralPolicy,
    CollateralTransfer,
    CollateralValuationSummary,
    EncumbranceStatus,
    EvaluatedPosition,
    HaircutRule,
    PositionRecord,
    TransferStatus,
    quantize_money,
)

ENCUMBERED_STATUSES: Set[EncumbranceStatus] = {
    EncumbranceStatus.PLEDGED_MARGIN,
    EncumbranceStatus.REHYPOTHECATED,
    EncumbranceStatus.LOCKED_SUBSTITUTION,
}

# Statuses that count toward posted margin coverage at the broker
POSTED_MARGIN_STATUSES: Set[EncumbranceStatus] = {
    EncumbranceStatus.PLEDGED_MARGIN,
    EncumbranceStatus.REHYPOTHECATED,
    EncumbranceStatus.LOCKED_SUBSTITUTION,
}


def match_haircut_rule(policy: CollateralPolicy, pos: PositionRecord) -> Optional[HaircutRule]:
    """Select the most specific matching haircut rule for a position."""
    candidates: List[Tuple[int, HaircutRule]] = []
    for rule in policy.rules:
        if rule.asset_class != pos.asset_class:
            continue
        rating_ok = rule.rating_tier == "ANY" or rule.rating_tier == pos.rating_tier
        maturity_ok = rule.maturity_bucket == "ANY" or rule.maturity_bucket == pos.maturity_bucket
        if rating_ok and maturity_ok:
            specificity = (0 if rule.rating_tier == "ANY" else 2) + (0 if rule.maturity_bucket == "ANY" else 1)
            candidates.append((specificity, rule))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def verify_no_global_double_counting(
    positions_by_broker: Dict[str, Sequence[PositionRecord]],
    transfers: Sequence[CollateralTransfer] = (),
) -> None:
    """Enforce global invariant: a collateral lot_id cannot be actively counted at two brokers simultaneously.

    Exceptions:
    - An outbound PENDING_TRANSFER_OUT / FAILED_TRANSFER lot paired with a FAILED transfer at the receiver
      is handled by quarantining the receiver's PENDING_TRANSFER_IN claim (not counted).
    - Two brokers both claiming active ownership (UNENCUMBERED / PLEDGED_MARGIN / REHYPOTHECATED / LOCKED_SUBSTITUTION)
      of the same lot_id raises DoubleCountingError immediately.
    """
    active_statuses = {
        EncumbranceStatus.UNENCUMBERED,
        EncumbranceStatus.PLEDGED_MARGIN,
        EncumbranceStatus.REHYPOTHECATED,
        EncumbranceStatus.LOCKED_SUBSTITUTION,
    }
    failed_transfer_lots = {t.lot_id for t in transfers if t.status == TransferStatus.FAILED}
    lot_owners: Dict[str, List[Tuple[str, EncumbranceStatus]]] = defaultdict(list)

    for broker_id, positions in positions_by_broker.items():
        for pos in positions:
            if pos.encumbrance_status in active_statuses:
                lot_owners[pos.lot_id].append((broker_id, pos.encumbrance_status))
            elif pos.encumbrance_status == EncumbranceStatus.PENDING_TRANSFER_IN and pos.lot_id not in failed_transfer_lots:
                # Pending inbound cannot coexist with active ownership elsewhere unless explicitly quarantined
                lot_owners[pos.lot_id].append((broker_id, pos.encumbrance_status))

    for lot_id, owners in lot_owners.items():
        active_owners = [o for o in owners if o[1] in active_statuses]
        if len(active_owners) > 1:
            raise DoubleCountingError(
                f"Collateral lot '{lot_id}' is simultaneously counted across multiple brokers/positions: "
                + ", ".join(f"{b}({s.value})" for b, s in active_owners),
                context={"lot_id": lot_id, "owners": [(b, s.value) for b, s in active_owners]},
            )


def evaluate_collateral(
    *,
    broker_id: str,
    account_id: str,
    business_date: date,
    positions: Sequence[PositionRecord],
    cash_balances: Sequence[CashBalanceRecord],
    policy: CollateralPolicy,
    fx_table: FxTable,
    transfers: Sequence[CollateralTransfer] = (),
    externally_encumbered_lots: Optional[Set[str]] = None,
    rounding_mode: str = "ROUND_HALF_EVEN",
) -> CollateralValuationSummary:
    """Calculate eligible collateral, haircuts, concentration trims, encumbrance, and available buffer.

    Zero I/O pure domain function.
    """
    base_ccy = policy.base_currency
    zero = quantize_money(Decimal("0"), base_ccy, rounding_mode=rounding_mode)

    # Identify failed inbound/outbound transfer lots impacting this broker
    failed_inbound_lots: Set[str] = {
        t.lot_id
        for t in transfers
        if t.status == TransferStatus.FAILED and t.to_broker_id == broker_id
    }
    failed_outbound_lots: Set[str] = {
        t.lot_id
        for t in transfers
        if t.status == TransferStatus.FAILED and t.from_broker_id == broker_id
    }
    external_lots = externally_encumbered_lots or set()

    # Guard against duplicate lot_ids in the position list itself
    seen_lots: Set[str] = set()
    for pos in positions:
        if pos.lot_id in seen_lots:
            raise DoubleCountingError(
                f"Duplicate lot_id '{pos.lot_id}' supplied for broker '{broker_id}'",
                context={"broker_id": broker_id, "lot_id": pos.lot_id},
            )
        seen_lots.add(pos.lot_id)

        if pos.lot_id in external_lots and pos.encumbrance_status in (
            POSTED_MARGIN_STATUSES | {EncumbranceStatus.UNENCUMBERED}
        ):
            raise DoubleCountingError(
                f"Lot '{pos.lot_id}' is already encumbered/counted at another broker",
                context={"broker_id": broker_id, "lot_id": pos.lot_id},
            )

    # First pass: compute gross values, eligibility, and pre-concentration net collateral
    preliminary: List[Tuple[PositionRecord, Decimal, Decimal, Decimal, bool, Decimal, Decimal, Decimal, Decimal, bool, Optional[str]]] = []
    quarantined_failed: List[str] = []
    ineligible_lots: List[str] = []

    for pos in positions:
        gross_local = quantize_money(pos.gross_market_value_local, pos.currency, rounding_mode=rounding_mode)
        gross_base, fx_rate = fx_table.convert(gross_local, pos.currency, base_ccy, as_of_date=business_date)

        # Check failed/unsettled transfer quarantine BEFORE counting collateral
        if pos.encumbrance_status == EncumbranceStatus.FAILED_TRANSFER or pos.lot_id in failed_inbound_lots:
            quarantined_failed.append(pos.lot_id)
            preliminary.append(
                (
                    pos,
                    gross_local,
                    fx_rate,
                    gross_base,
                    False,
                    Decimal("1.00"),
                    Decimal("0.00"),
                    Decimal("1.00"),
                    zero,
                    False,
                    "FAILED_TRANSFER_QUARANTINED",
                )
            )
            continue

        if pos.encumbrance_status == EncumbranceStatus.PENDING_TRANSFER_IN:
            # Unsettled inbound transfer is never counted as posted margin until settled
            preliminary.append(
                (
                    pos,
                    gross_local,
                    fx_rate,
                    gross_base,
                    False,
                    Decimal("1.00"),
                    Decimal("0.00"),
                    Decimal("1.00"),
                    zero,
                    False,
                    "UNSETTLED_INBOUND_TRANSFER",
                )
            )
            continue

        if pos.encumbrance_status == EncumbranceStatus.PENDING_TRANSFER_OUT and pos.lot_id not in failed_outbound_lots:
            # Outbound transfer in flight cannot be double-counted as available or posted margin
            preliminary.append(
                (
                    pos,
                    gross_local,
                    fx_rate,
                    gross_base,
                    False,
                    Decimal("1.00"),
                    Decimal("0.00"),
                    Decimal("1.00"),
                    zero,
                    False,
                    "PENDING_OUTBOUND_TRANSFER",
                )
            )
            continue

        rule = match_haircut_rule(policy, pos)
        if rule is None or not rule.eligible:
            ineligible_lots.append(pos.lot_id)
            reason = "NO_POLICY_RULE" if rule is None else "INELIGIBLE_UNDER_POLICY"
            preliminary.append(
                (
                    pos,
                    gross_local,
                    fx_rate,
                    gross_base,
                    False,
                    Decimal("1.00"),
                    Decimal("0.00"),
                    Decimal("1.00"),
                    zero,
                    False,
                    reason,
                )
            )
            continue

        fx_addon = rule.currency_mismatch_addon_pct if pos.currency != base_ccy else Decimal("0.00")
        effective_hc = min(Decimal("1.00"), rule.haircut_pct + fx_addon)
        net_val = quantize_money(
            gross_base * (Decimal("1") - effective_hc),
            base_ccy,
            rounding_mode=rounding_mode,
        )
        counted_for_margin = pos.encumbrance_status in POSTED_MARGIN_STATUSES
        preliminary.append(
            (
                pos,
                gross_local,
                fx_rate,
                gross_base,
                True,
                rule.haircut_pct,
                fx_addon,
                effective_hc,
                net_val,
                counted_for_margin,
                None,
            )
        )

    # Evaluate cash balances first so total eligible pool includes margin cash for concentration limits
    margin_cash_base = zero
    free_cash_base = zero
    for cb in cash_balances:
        if cb.balance_type == CashBalanceType.MARGIN_CASH:
            val_base, _ = fx_table.convert(cb.amount, cb.currency, base_ccy, as_of_date=business_date)
            # Apply cash currency mismatch haircut if non-base currency
            if cb.currency != base_ccy:
                cash_rule = next((r for r in policy.rules if r.asset_class == AssetClass.CASH), None)
                addon = cash_rule.currency_mismatch_addon_pct if cash_rule else Decimal("0.00")
                val_base = quantize_money(val_base * (Decimal("1") - addon), base_ccy, rounding_mode=rounding_mode)
            margin_cash_base += val_base
        elif cb.balance_type == CashBalanceType.FREE_CASH:
            val_base, _ = fx_table.convert(cb.amount, cb.currency, base_ccy, as_of_date=business_date)
            free_cash_base += val_base

    # Second pass: apply deterministic concentration caps across eligible posted securities
    total_pre_cap_posted = margin_cash_base + sum(
        (item[8] for item in preliminary if item[4] and item[9]),
        zero,
    )
    by_asset_class: Dict[AssetClass, Decimal] = defaultdict(lambda: zero)
    for item in preliminary:
        if item[4] and item[9]:
            by_asset_class[item[0].asset_class] += item[8]

    # Compute trim ratio per asset class if concentration limit exceeded
    class_trim_excess: Dict[AssetClass, Decimal] = {}
    if total_pre_cap_posted > zero:
        for ac, ac_total in by_asset_class.items():
            limits = [
                r.concentration_limit_pct
                for r in policy.rules
                if r.asset_class == ac and r.eligible and r.concentration_limit_pct is not None
            ]
            if limits:
                cap_pct = min(limits)
                cap_amount = quantize_money(total_pre_cap_posted * cap_pct, base_ccy, rounding_mode=rounding_mode)
                if ac_total > cap_amount:
                    class_trim_excess[ac] = ac_total - cap_amount

    # Allocate concentration trim deterministically in sorted lot_id order
    trim_by_lot: Dict[str, Decimal] = defaultdict(lambda: zero)
    for ac, excess in class_trim_excess.items():
        ac_items = sorted(
            [item for item in preliminary if item[4] and item[9] and item[0].asset_class == ac],
            key=lambda x: x[0].lot_id,
        )
        ac_total = by_asset_class[ac]
        remaining_trim = excess
        for idx, item in enumerate(ac_items):
            if idx == len(ac_items) - 1:
                trim = min(item[8], remaining_trim)
            else:
                share = (item[8] / ac_total) * excess
                trim = min(item[8], quantize_money(share, base_ccy, rounding_mode=rounding_mode))
            trim_by_lot[item[0].lot_id] = trim
            remaining_trim -= trim

    evaluated_positions: List[EvaluatedPosition] = []
    total_gross_sec = zero
    eligible_sec_total = zero
    ineligible_gross_total = zero
    encumbered_total = zero
    pledged_margin_total = zero
    rehyp_total = zero
    available_unenc_total = zero

    for (
        pos,
        gross_local,
        fx_rate,
        gross_base,
        eligible,
        base_hc,
        fx_addon,
        eff_hc,
        pre_trim_net,
        counted_for_margin,
        exclusion_reason,
    ) in preliminary:
        trim = trim_by_lot.get(pos.lot_id, zero)
        final_net = max(zero, pre_trim_net - trim)

        evaluated_positions.append(
            EvaluatedPosition(
                position_id=pos.position_id,
                lot_id=pos.lot_id,
                broker_id=pos.broker_id,
                security_id=pos.security_id,
                asset_class=pos.asset_class,
                currency=pos.currency,
                encumbrance_status=pos.encumbrance_status,
                gross_value_local=gross_local,
                fx_rate_to_base=fx_rate,
                gross_value_base=gross_base,
                eligible=eligible,
                base_haircut_pct=base_hc,
                fx_addon_haircut_pct=fx_addon,
                effective_haircut_pct=eff_hc,
                concentration_trim_base=trim,
                net_collateral_value_base=final_net,
                counted_for_margin=counted_for_margin and eligible,
                exclusion_reason=exclusion_reason,
            )
        )

        total_gross_sec += gross_base
        if eligible:
            eligible_sec_total += final_net
            if pos.encumbrance_status in ENCUMBERED_STATUSES:
                encumbered_total += final_net
            if pos.encumbrance_status in POSTED_MARGIN_STATUSES:
                pledged_margin_total += final_net
            if pos.encumbrance_status == EncumbranceStatus.REHYPOTHECATED:
                rehyp_total += final_net
            if pos.encumbrance_status == EncumbranceStatus.UNENCUMBERED:
                available_unenc_total += final_net
        else:
            ineligible_gross_total += gross_base

    total_posted = pledged_margin_total + margin_cash_base
    total_available_buffer = available_unenc_total + free_cash_base

    return CollateralValuationSummary(
        broker_id=broker_id,
        account_id=account_id,
        business_date=business_date,
        base_currency=base_ccy,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        positions=tuple(evaluated_positions),
        total_gross_securities_base=quantize_money(total_gross_sec, base_ccy, rounding_mode=rounding_mode),
        eligible_securities_value_base=quantize_money(eligible_sec_total, base_ccy, rounding_mode=rounding_mode),
        ineligible_securities_gross_base=quantize_money(ineligible_gross_total, base_ccy, rounding_mode=rounding_mode),
        encumbered_collateral_base=quantize_money(encumbered_total, base_ccy, rounding_mode=rounding_mode),
        pledged_margin_collateral_base=quantize_money(pledged_margin_total, base_ccy, rounding_mode=rounding_mode),
        rehypothecated_collateral_base=quantize_money(rehyp_total, base_ccy, rounding_mode=rounding_mode),
        available_unencumbered_collateral_base=quantize_money(available_unenc_total, base_ccy, rounding_mode=rounding_mode),
        margin_cash_base=quantize_money(margin_cash_base, base_ccy, rounding_mode=rounding_mode),
        free_cash_base=quantize_money(free_cash_base, base_ccy, rounding_mode=rounding_mode),
        total_posted_collateral_base=quantize_money(total_posted, base_ccy, rounding_mode=rounding_mode),
        total_available_buffer_base=quantize_money(total_available_buffer, base_ccy, rounding_mode=rounding_mode),
        quarantined_failed_transfer_lots=tuple(sorted(quarantined_failed)),
        ineligible_lots=tuple(sorted(ineligible_lots)),
    )
