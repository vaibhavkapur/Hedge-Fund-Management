"""Pure, I/O-free domain calculation and state-transition engine.

All monetary calculations use exact Decimal arithmetic with explicit currency
minor-unit quantization and configurable rounding modes. No filesystem,
database, or logging side effects occur inside this module.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext
from typing import Dict, List, Optional, Sequence, Tuple

from .calendar import is_crystallization_date, year_fraction
from .errors import ValidationError
from .models import (
    CapitalFlow,
    CapitalFlowType,
    EngineConfig,
    FeeAgreement,
    HurdleType,
    InvestorLot,
    LotFeeCalculation,
    ManagementFeeBasis,
    NavSnapshot,
)


def calculate_management_fee(
    *,
    units: Decimal,
    opening_nav_per_unit: Decimal,
    gross_nav_per_unit: Decimal,
    yf: Decimal,
    agreement: FeeAgreement,
    config: EngineConfig,
) -> Tuple[Decimal, Decimal]:
    """Return (basis_nav, quantized_management_fee) for a lot over year fraction yf."""
    gross_opening_nav = units * opening_nav_per_unit
    gross_ending_nav = units * gross_nav_per_unit

    if agreement.management_fee_basis == ManagementFeeBasis.BEGINNING_NAV:
        basis_nav = gross_opening_nav
    elif agreement.management_fee_basis == ManagementFeeBasis.ENDING_PRE_FEE_NAV:
        basis_nav = gross_ending_nav
    elif agreement.management_fee_basis == ManagementFeeBasis.AVERAGE_NAV:
        basis_nav = (gross_opening_nav + gross_ending_nav) / Decimal("2")
    else:  # pragma: no cover - guarded by enum validation
        raise ValidationError(
            f"Unsupported management fee basis '{agreement.management_fee_basis}'",
            code="UNSUPPORTED_FEE_BASIS",
            field="management_fee_basis",
        )

    raw_fee = basis_nav * agreement.management_fee_rate * yf
    mgmt_fee = config.quantize_money(raw_fee, agreement.currency)
    if mgmt_fee < Decimal("0") and not agreement.allow_negative_accrual:
        raise ValidationError(
            f"Computed negative management fee ({mgmt_fee}) when allow_negative_accrual=False",
            code="NEGATIVE_ACCRUAL_FORBIDDEN",
            field="management_fee_amount",
        )
    return config.quantize_money(basis_nav, agreement.currency), mgmt_fee


def calculate_hurdle_nav_per_unit(
    *,
    lot: InvestorLot,
    period_start_date: date,
    valuation_date: date,
    period_yf: Decimal,
    agreement: FeeAgreement,
    config: EngineConfig,
) -> Decimal:
    """Compute per-unit hurdle benchmark NAV at valuation_date."""
    if agreement.hurdle_type == HurdleType.NONE:
        return config.quantize_nav(lot.high_water_mark)

    if agreement.hurdle_type == HurdleType.HARD_COMPOUNDING:
        raw_hurdle = lot.hurdle_base_nav * (Decimal("1") + agreement.hurdle_rate * period_yf)
        return config.quantize_nav(raw_hurdle)

    # HARD_NON_COMPOUNDING and SOFT_CATCH_UP use simple interest over the active cycle
    anchor_date = lot.last_crystallization_date or period_start_date
    if lot.subscription_date > anchor_date:
        anchor_date = lot.subscription_date
    cycle_yf = year_fraction(anchor_date, valuation_date, agreement.day_count)
    raw_hurdle = lot.hurdle_base_nav * (Decimal("1") + agreement.hurdle_rate * cycle_yf)
    return config.quantize_nav(raw_hurdle)


def evaluate_lot(
    *,
    lot: InvestorLot,
    period_start_date: date,
    valuation_date: date,
    gross_nav_per_unit: Decimal,
    agreement: FeeAgreement,
    config: EngineConfig,
    force_crystallization: bool = False,
    crystallization_reason: Optional[str] = None,
    lot_id_override: Optional[str] = None,
) -> Tuple[LotFeeCalculation, InvestorLot]:
    """Pure evaluation of a single investor lot for a period ending at valuation_date."""
    with localcontext() as ctx:
        ctx.prec = config.intermediate_precision

        effective_start = max(period_start_date, lot.subscription_date)
        yf = year_fraction(effective_start, valuation_date, agreement.day_count)

        gross_ending_nav = config.quantize_money(lot.units * gross_nav_per_unit, agreement.currency)
        basis_nav, mgmt_fee = calculate_management_fee(
            units=lot.units,
            opening_nav_per_unit=lot.opening_nav_per_unit,
            gross_nav_per_unit=gross_nav_per_unit,
            yf=yf,
            agreement=agreement,
            config=config,
        )

        notes: List[str] = []
        if lot.units > Decimal("0"):
            post_mgmt_total = gross_ending_nav - mgmt_fee
            post_mgmt_nav_exact = post_mgmt_total / lot.units
            post_mgmt_nav_per_unit = config.quantize_nav(post_mgmt_nav_exact)
        else:
            post_mgmt_nav_exact = Decimal("0")
            post_mgmt_nav_per_unit = Decimal("0")

        eval_nav_exact = (
            post_mgmt_nav_exact
            if agreement.management_fee_deducted_before_perf
            else gross_nav_per_unit
        )

        hurdle_nav = calculate_hurdle_nav_per_unit(
            lot=lot,
            period_start_date=period_start_date,
            valuation_date=valuation_date,
            period_yf=yf,
            agreement=agreement,
            config=config,
        )
        effective_benchmark = max(lot.high_water_mark, hurdle_nav)

        if agreement.hurdle_type == HurdleType.SOFT_CATCH_UP:
            if eval_nav_exact > effective_benchmark and lot.units > Decimal("0"):
                excess_above_hurdle = eval_nav_exact - effective_benchmark
                gain_above_hwm = max(Decimal("0"), eval_nav_exact - lot.high_water_mark)
                catch_up_fee_per_unit = excess_above_hurdle * agreement.catch_up_rate
                full_perf_fee_per_unit = gain_above_hwm * agreement.performance_fee_rate
                fee_per_unit = min(catch_up_fee_per_unit, full_perf_fee_per_unit)
                eligible_gain_per_unit = config.quantize_nav(gain_above_hwm)
                target_cumulative_perf_fee = config.quantize_money(
                    fee_per_unit * lot.units, agreement.currency
                )
                if catch_up_fee_per_unit < full_perf_fee_per_unit:
                    notes.append("SOFT_HURDLE_CATCH_UP_ZONE")
                else:
                    notes.append("SOFT_HURDLE_FULL_PERFORMANCE_RATE")
            else:
                eligible_gain_per_unit = config.quantize_nav(Decimal("0"))
                target_cumulative_perf_fee = config.quantize_money(Decimal("0"), agreement.currency)
                notes.append("BELOW_EFFECTIVE_BENCHMARK")
        else:
            if eval_nav_exact > effective_benchmark and lot.units > Decimal("0"):
                eligible_gain_exact = eval_nav_exact - effective_benchmark
                eligible_gain_per_unit = config.quantize_nav(eligible_gain_exact)
                target_cumulative_perf_fee = config.quantize_money(
                    eligible_gain_exact * lot.units * agreement.performance_fee_rate,
                    agreement.currency,
                )
                notes.append("ABOVE_EFFECTIVE_BENCHMARK")
            else:
                eligible_gain_per_unit = config.quantize_nav(Decimal("0"))
                target_cumulative_perf_fee = config.quantize_money(Decimal("0"), agreement.currency)
                if eval_nav_exact <= lot.high_water_mark:
                    notes.append("AT_OR_BELOW_HWM")
                else:
                    notes.append("ABOVE_HWM_BELOW_HURDLE")

        if target_cumulative_perf_fee < Decimal("0") and not agreement.allow_negative_accrual:
            raise ValidationError(
                f"Computed negative cumulative performance fee ({target_cumulative_perf_fee})",
                code="NEGATIVE_ACCRUAL_FORBIDDEN",
                field="target_cumulative_perf_fee",
            )

        raw_delta = target_cumulative_perf_fee - lot.prior_accrued_perf_fee
        if raw_delta < Decimal("0"):
            if agreement.allow_intra_period_perf_reversal:
                perf_delta = raw_delta
                effective_cumulative_perf = target_cumulative_perf_fee
                notes.append("INTRA_PERIOD_PERF_REVERSAL")
            elif agreement.allow_negative_accrual:
                perf_delta = raw_delta
                effective_cumulative_perf = target_cumulative_perf_fee
            else:
                perf_delta = config.quantize_money(Decimal("0"), agreement.currency)
                effective_cumulative_perf = lot.prior_accrued_perf_fee
                notes.append("REVERSAL_BLOCKED_BY_POLICY")
        else:
            perf_delta = raw_delta
            effective_cumulative_perf = target_cumulative_perf_fee

        scheduled_cryst = is_crystallization_date(agreement, valuation_date)
        is_cryst = force_crystallization or scheduled_cryst
        if force_crystallization:
            cryst_reason = crystallization_reason or "FORCED_CRYSTALLIZATION"
        elif scheduled_cryst:
            cryst_reason = f"SCHEDULED_{agreement.crystallization_frequency.value}"
        else:
            cryst_reason = "INTERIM_NON_CRYSTALLIZATION"

        if is_cryst:
            crystallized_perf_fee = effective_cumulative_perf
            ending_accrued_perf_fee = config.quantize_money(Decimal("0"), agreement.currency)
        else:
            crystallized_perf_fee = config.quantize_money(Decimal("0"), agreement.currency)
            ending_accrued_perf_fee = effective_cumulative_perf

        ending_net_nav = config.quantize_money(
            gross_ending_nav - mgmt_fee - effective_cumulative_perf,
            agreement.currency,
        )
        if lot.units > Decimal("0"):
            ending_net_nav_per_unit = config.quantize_nav(ending_net_nav / lot.units)
        else:
            ending_net_nav_per_unit = config.quantize_nav(Decimal("0"))

        # HWM and hurdle base reset rules
        if is_cryst:
            if ending_net_nav_per_unit > lot.high_water_mark:
                new_hwm = ending_net_nav_per_unit
                notes.append("HWM_RESET_UPWARD")
            else:
                new_hwm = lot.high_water_mark
                notes.append("HWM_PRESERVED_NO_GAIN")

            if agreement.hurdle_type == HurdleType.HARD_COMPOUNDING:
                if crystallized_perf_fee > Decimal("0"):
                    new_hurdle_base = new_hwm
                    notes.append("COMPOUNDING_HURDLE_RESET")
                else:
                    new_hurdle_base = hurdle_nav
                    notes.append("COMPOUNDING_HURDLE_CARRIED_FORWARD")
            else:
                new_hurdle_base = new_hwm

            next_last_cryst_date: Optional[date] = valuation_date
        else:
            new_hwm = lot.high_water_mark
            notes.append("HWM_UNCHANGED_INTERIM")
            if agreement.hurdle_type == HurdleType.HARD_COMPOUNDING:
                new_hurdle_base = hurdle_nav
            else:
                new_hurdle_base = lot.hurdle_base_nav
            next_last_cryst_date = lot.last_crystallization_date

        loss_cf = config.quantize_money(
            max(Decimal("0"), (new_hwm - ending_net_nav_per_unit) * lot.units),
            agreement.currency,
        )

        calc = LotFeeCalculation(
            lot_id=lot_id_override or lot.lot_id,
            investor_id=lot.investor_id,
            share_class=lot.share_class,
            currency=agreement.currency,
            period_start_date=effective_start,
            valuation_date=valuation_date,
            year_fraction=yf,
            units=lot.units,
            opening_nav_per_unit=lot.opening_nav_per_unit,
            gross_nav_per_unit=gross_nav_per_unit,
            gross_ending_nav=gross_ending_nav,
            management_fee_basis_nav=basis_nav,
            management_fee_amount=mgmt_fee,
            post_mgmt_nav_per_unit=post_mgmt_nav_per_unit,
            prior_hwm=lot.high_water_mark,
            hurdle_nav_per_unit=hurdle_nav,
            effective_benchmark_nav_per_unit=effective_benchmark,
            eligible_gain_per_unit=eligible_gain_per_unit,
            target_cumulative_perf_fee=effective_cumulative_perf,
            prior_accrued_perf_fee=lot.prior_accrued_perf_fee,
            performance_fee_delta=perf_delta,
            crystallized_performance_fee=crystallized_perf_fee,
            ending_accrued_perf_fee=ending_accrued_perf_fee,
            is_crystallization_event=is_cryst,
            crystallization_reason=cryst_reason,
            new_hwm=new_hwm,
            new_hurdle_base_nav=new_hurdle_base,
            ending_net_nav=ending_net_nav,
            ending_net_nav_per_unit=ending_net_nav_per_unit,
            loss_carryforward_amount=loss_cf,
            notes=tuple(notes),
        )

        # Updated lot state for the next period:
        # On crystallization, next period's opening NAV is the net post-fee NAV.
        # On interim valuation, next period's opening NAV is post-management-fee NAV
        # (since uncrystallized perf fee remains tracked in prior_accrued_perf_fee).
        next_opening_nav = ending_net_nav_per_unit if is_cryst else post_mgmt_nav_per_unit
        updated_lot = replace(
            lot,
            opening_nav_per_unit=next_opening_nav,
            high_water_mark=new_hwm,
            hurdle_base_nav=new_hurdle_base,
            prior_accrued_perf_fee=ending_accrued_perf_fee,
            loss_carryforward_amount=loss_cf,
            last_crystallization_date=next_last_cryst_date,
            version=lot.version + 1,
        )
        return calc, updated_lot


def evaluate_period(
    *,
    agreement: FeeAgreement,
    snapshot: NavSnapshot,
    lots: Sequence[InvestorLot],
    config: EngineConfig,
    capital_flows: Sequence[CapitalFlow] = (),
) -> Tuple[Tuple[LotFeeCalculation, ...], Tuple[InvestorLot, ...], Dict[str, int]]:
    """Evaluate all active lots and mid-period capital flows for a valuation snapshot.

    Returns (lot_calculations, updated_lots, diagnostic_counters).
    """
    working_lots: Dict[str, InvestorLot] = {lot.lot_id: lot for lot in lots}
    lot_order: List[str] = [lot.lot_id for lot in lots]
    calculations: List[LotFeeCalculation] = []

    counters: Dict[str, int] = {
        "lots_evaluated": 0,
        "subscriptions_processed": 0,
        "redemptions_processed": 0,
        "crystallization_events": 0,
        "reversals_processed": 0,
    }

    sorted_flows = sorted(capital_flows, key=lambda f: (f.flow_date, f.flow_id))

    for flow in sorted_flows:
        if flow.flow_type == CapitalFlowType.SUBSCRIPTION:
            units_added = (
                flow.units
                if flow.units is not None
                else config.quantize_units(flow.amount / flow.nav_per_unit)  # type: ignore[operator]
            )
            new_lot = InvestorLot(
                lot_id=flow.lot_id,
                investor_id=flow.investor_id,
                share_class=flow.share_class,
                series_id=flow.series_id or f"SER-{flow.flow_date.isoformat()}",
                subscription_date=flow.flow_date,
                units=units_added,
                opening_nav_per_unit=flow.nav_per_unit,
                high_water_mark=flow.nav_per_unit,
                hurdle_base_nav=flow.nav_per_unit,
                prior_accrued_perf_fee=config.quantize_money(Decimal("0"), agreement.currency),
                loss_carryforward_amount=config.quantize_money(Decimal("0"), agreement.currency),
                last_crystallization_date=flow.flow_date,
                status="ACTIVE",
                version=1,
            )
            working_lots[new_lot.lot_id] = new_lot
            lot_order.append(new_lot.lot_id)
            counters["subscriptions_processed"] += 1

        elif flow.flow_type == CapitalFlowType.REDEMPTION:
            source_lot = working_lots[flow.lot_id]
            redeem_units = (
                flow.units
                if flow.units is not None
                else config.quantize_units(flow.amount / flow.nav_per_unit)  # type: ignore[operator]
            )
            proportion = redeem_units / source_lot.units
            redeemed_prior_accrued = config.quantize_money(
                source_lot.prior_accrued_perf_fee * proportion,
                agreement.currency,
            )

            redeemed_slice = replace(
                source_lot,
                units=redeem_units,
                prior_accrued_perf_fee=redeemed_prior_accrued,
            )
            redemption_calc, _ = evaluate_lot(
                lot=redeemed_slice,
                period_start_date=snapshot.period_start_date,
                valuation_date=flow.flow_date,
                gross_nav_per_unit=flow.nav_per_unit,
                agreement=agreement,
                config=config,
                force_crystallization=True,
                crystallization_reason=f"REDEMPTION:{flow.flow_id}",
                lot_id_override=f"{source_lot.lot_id}#REDEMPTION:{flow.flow_id}",
            )
            calculations.append(redemption_calc)
            counters["redemptions_processed"] += 1
            counters["crystallization_events"] += 1
            if redemption_calc.performance_fee_delta < Decimal("0"):
                counters["reversals_processed"] += 1

            remaining_units = source_lot.units - redeem_units
            remaining_prior_accrued = source_lot.prior_accrued_perf_fee - redeemed_prior_accrued
            # Per-unit HWM stays constant; dollar loss carryforward shrinks proportionally with units
            remaining_loss_cf = config.quantize_money(
                max(Decimal("0"), (source_lot.high_water_mark - flow.nav_per_unit) * remaining_units),
                agreement.currency,
            )
            working_lots[source_lot.lot_id] = replace(
                source_lot,
                units=remaining_units,
                prior_accrued_perf_fee=remaining_prior_accrued,
                loss_carryforward_amount=remaining_loss_cf,
                status="REDEEMED" if remaining_units == Decimal("0") else "ACTIVE",
                version=source_lot.version + 1,
            )

    updated_lots: List[InvestorLot] = []
    for lot_id in lot_order:
        lot = working_lots[lot_id]
        if lot.status != "ACTIVE" or lot.units == Decimal("0"):
            updated_lots.append(lot)
            continue

        calc, updated_lot = evaluate_lot(
            lot=lot,
            period_start_date=snapshot.period_start_date,
            valuation_date=snapshot.valuation_date,
            gross_nav_per_unit=snapshot.gross_nav_per_unit,
            agreement=agreement,
            config=config,
        )
        calculations.append(calc)
        updated_lots.append(updated_lot)
        counters["lots_evaluated"] += 1
        if calc.is_crystallization_event:
            counters["crystallization_events"] += 1
        if calc.performance_fee_delta < Decimal("0"):
            counters["reversals_processed"] += 1

    return tuple(calculations), tuple(updated_lots), counters
