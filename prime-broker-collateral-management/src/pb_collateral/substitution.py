"""Pure domain collateral substitution and pre-settlement shortfall review simulator (zero I/O)."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from decimal import Decimal
from typing import Dict, List, Optional, Sequence, Set

from pb_collateral.errors import DoubleCountingError, ValidationError
from pb_collateral.fx import FxTable
from pb_collateral.models import (
    ApprovalStatus,
    CashBalanceRecord,
    CollateralPolicy,
    CollateralTransfer,
    EncumbranceStatus,
    MarginRequirement,
    PositionRecord,
    SubstitutionRequest,
    SubstitutionReviewResult,
    quantize_money,
)
from pb_collateral.valuation import POSTED_MARGIN_STATUSES, evaluate_collateral


def simulate_substitution(
    *,
    request: SubstitutionRequest,
    current_positions: Sequence[PositionRecord],
    cash_balances: Sequence[CashBalanceRecord],
    margin_requirement: MarginRequirement,
    policy: CollateralPolicy,
    fx_table: FxTable,
    transfers: Sequence[CollateralTransfer] = (),
    externally_encumbered_lots: Optional[Set[str]] = None,
    rounding_mode: str = "ROUND_HALF_EVEN",
) -> SubstitutionReviewResult:
    """Simulate collateral substitution and enforce pre-settlement shortfall & anti-double-counting checks.

    Never mutates input records. If a shortfall would occur post-substitution, flags the shortfall
    BEFORE simulated settlement (settlement_permitted=False) and preserves original positions.
    """
    base_ccy = policy.base_currency
    zero = quantize_money(Decimal("0"), base_ccy, rounding_mode=rounding_mode)
    ext_lots = externally_encumbered_lots or set()

    if request.broker_id != policy.broker_id:
        raise ValidationError(
            f"Substitution broker '{request.broker_id}' does not match policy broker '{policy.broker_id}'",
            context={"request_id": request.request_id},
        )

    pre_val = evaluate_collateral(
        broker_id=request.broker_id,
        account_id=request.account_id,
        business_date=request.business_date,
        positions=current_positions,
        cash_balances=cash_balances,
        policy=policy,
        fx_table=fx_table,
        transfers=transfers,
        externally_encumbered_lots=ext_lots,
        rounding_mode=rounding_mode,
    )

    pos_by_lot: Dict[str, PositionRecord] = {p.lot_id: p for p in current_positions}
    eval_by_lot = {p.lot_id: p for p in pre_val.positions}
    warnings: List[str] = []
    rejected_lots: List[str] = []

    # Validate release lots exist and are currently encumbered/posted
    release_set = set(request.release_lot_ids)
    released_val = zero
    for lot_id in request.release_lot_ids:
        existing = pos_by_lot.get(lot_id)
        if existing is None:
            raise ValidationError(
                f"Cannot release unknown lot_id '{lot_id}' in substitution '{request.request_id}'",
                context={"request_id": request.request_id, "lot_id": lot_id},
            )
        if existing.encumbrance_status not in POSTED_MARGIN_STATUSES:
            raise ValidationError(
                f"Lot '{lot_id}' has status '{existing.encumbrance_status.value}' and is not pledged for release",
                context={"request_id": request.request_id, "lot_id": lot_id},
            )
        released_val += eval_by_lot[lot_id].net_collateral_value_base

    # Check proposed replacement positions for double-counting against external brokers or already-posted lots
    proposed_lot_ids: Set[str] = set()
    for p in request.pledge_positions:
        if p.lot_id in proposed_lot_ids:
            raise DoubleCountingError(
                f"Duplicate pledge lot_id '{p.lot_id}' in substitution request '{request.request_id}'",
                context={"request_id": request.request_id, "lot_id": p.lot_id},
            )
        proposed_lot_ids.add(p.lot_id)
        if p.lot_id in ext_lots:
            raise DoubleCountingError(
                f"Proposed replacement lot '{p.lot_id}' is already encumbered at another broker",
                context={"request_id": request.request_id, "lot_id": p.lot_id},
            )
        if p.lot_id in pos_by_lot and pos_by_lot[p.lot_id].encumbrance_status in POSTED_MARGIN_STATUSES and p.lot_id not in release_set:
            raise DoubleCountingError(
                f"Proposed replacement lot '{p.lot_id}' is already pledged at broker '{request.broker_id}'",
                context={"request_id": request.request_id, "lot_id": p.lot_id},
            )

    # Build candidate post-substitution position set
    candidate_positions: List[PositionRecord] = []
    for p in current_positions:
        if p.lot_id in release_set:
            candidate_positions.append(replace(p, encumbrance_status=EncumbranceStatus.UNENCUMBERED, pledge_ref=None))
        elif p.lot_id in proposed_lot_ids:
            # Existing unencumbered lot being promoted to pledged
            candidate_positions.append(
                replace(p, encumbrance_status=EncumbranceStatus.PLEDGED_MARGIN, pledge_ref=request.request_id)
            )
        else:
            candidate_positions.append(p)

    # Append any new inbound replacement positions not already in current_positions
    for p in request.pledge_positions:
        if p.lot_id not in pos_by_lot:
            candidate_positions.append(
                replace(
                    p,
                    broker_id=request.broker_id,
                    account_id=request.account_id,
                    encumbrance_status=EncumbranceStatus.PLEDGED_MARGIN,
                    pledge_ref=request.request_id,
                )
            )

    post_val = evaluate_collateral(
        broker_id=request.broker_id,
        account_id=request.account_id,
        business_date=request.business_date,
        positions=candidate_positions,
        cash_balances=cash_balances,
        policy=policy,
        fx_table=fx_table,
        transfers=transfers,
        externally_encumbered_lots=ext_lots,
        rounding_mode=rounding_mode,
    )

    post_eval_by_lot = {p.lot_id: p for p in post_val.positions}
    proposed_pledge_val = zero
    for p in request.pledge_positions:
        ev = post_eval_by_lot[p.lot_id]
        if not ev.eligible:
            rejected_lots.append(p.lot_id)
            warnings.append(
                f"Proposed lot '{p.lot_id}' ({p.security_id}) rejected: {ev.exclusion_reason or 'INELIGIBLE'}."
            )
        else:
            if ev.concentration_trim_base > zero:
                warnings.append(
                    f"Proposed lot '{p.lot_id}' trimmed by {ev.concentration_trim_base} {base_ccy} due to asset-class concentration limit."
                )
            proposed_pledge_val += ev.net_collateral_value_base

    mr_base = quantize_money(margin_requirement.total_requirement, base_ccy, rounding_mode=rounding_mode)
    post_surplus_or_deficit = quantize_money(
        post_val.total_posted_collateral_base - mr_base,
        base_ccy,
        rounding_mode=rounding_mode,
    )

    if post_surplus_or_deficit < zero:
        shortfall_flagged = True
        settlement_permitted = False
        approval_status = ApprovalStatus.AUTO_FLAGGED_SHORTFALL
        required_top_up = quantize_money(-post_surplus_or_deficit, base_ccy, rounding_mode=rounding_mode)
        warnings.append(
            f"Pre-settlement gate blocked substitution: projected shortfall of {required_top_up} {base_ccy} "
            f"(post posted={post_val.total_posted_collateral_base}, requirement={mr_base})."
        )
        final_positions = tuple(current_positions)
    else:
        shortfall_flagged = False
        settlement_permitted = True
        required_top_up = zero
        approval_status = ApprovalStatus.PENDING_REVIEW if warnings else ApprovalStatus.APPROVED
        final_positions = tuple(candidate_positions)

    review_digest = hashlib.sha256(
        "|".join(
            [
                request.request_id,
                request.broker_id,
                request.business_date.isoformat(),
                policy.policy_id,
                str(policy.version),
                str(post_val.total_posted_collateral_base),
            ]
        ).encode("utf-8")
    ).hexdigest()[:10].upper()

    return SubstitutionReviewResult(
        review_id=f"SUBREV-{request.broker_id}-{review_digest}",
        request_id=request.request_id,
        broker_id=request.broker_id,
        account_id=request.account_id,
        business_date=request.business_date,
        base_currency=base_ccy,
        pre_posted_collateral_base=pre_val.total_posted_collateral_base,
        released_collateral_value_base=quantize_money(released_val, base_ccy, rounding_mode=rounding_mode),
        proposed_pledge_value_base=quantize_money(proposed_pledge_val, base_ccy, rounding_mode=rounding_mode),
        post_posted_collateral_base=post_val.total_posted_collateral_base,
        margin_requirement_base=mr_base,
        post_surplus_or_deficit_base=post_surplus_or_deficit,
        shortfall_flagged_before_settlement=shortfall_flagged,
        settlement_permitted=settlement_permitted,
        approval_status=approval_status,
        required_top_up_base=required_top_up,
        rejected_lots=tuple(sorted(rejected_lots)),
        warnings=tuple(warnings),
        post_settlement_positions=final_positions,
    )
