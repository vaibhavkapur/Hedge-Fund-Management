"""Input schema and domain invariant validation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Iterable, Sequence, Set

from .calendar import has_crossed_crystallization_boundary
from .errors import CalendarBoundaryError, UnsupportedPolicyError, ValidationError
from .models import (
    CapitalFlow,
    CapitalFlowType,
    CrystallizationFrequency,
    EngineConfig,
    FeeAgreement,
    HurdleType,
    InvestorLot,
    NavSnapshot,
)


def validate_fee_agreement(agreement: FeeAgreement, config: EngineConfig) -> None:
    """Validate fee agreement rates, currency, hurdle, and calendar configuration."""
    if not agreement.agreement_id.strip():
        raise ValidationError("agreement_id must be non-empty", code="EMPTY_AGREEMENT_ID", field="agreement_id")
    if agreement.version < 1:
        raise ValidationError("agreement version must be >= 1", code="INVALID_VERSION", field="version")
    if not agreement.share_class.strip():
        raise ValidationError("share_class must be non-empty", code="EMPTY_SHARE_CLASS", field="share_class")
    if agreement.currency.upper() not in config.currency_minor_units:
        raise UnsupportedPolicyError(
            f"Currency '{agreement.currency}' is not configured in currency_minor_units",
            code="UNSUPPORTED_CURRENCY",
            field="currency",
        )
    if agreement.management_fee_rate < Decimal("0") or agreement.management_fee_rate > Decimal("1"):
        raise ValidationError(
            f"management_fee_rate must be in [0, 1], got {agreement.management_fee_rate}",
            code="INVALID_MGMT_FEE_RATE",
            field="management_fee_rate",
        )
    if agreement.performance_fee_rate < Decimal("0") or agreement.performance_fee_rate > Decimal("1"):
        raise ValidationError(
            f"performance_fee_rate must be in [0, 1], got {agreement.performance_fee_rate}",
            code="INVALID_PERF_FEE_RATE",
            field="performance_fee_rate",
        )
    if agreement.hurdle_rate < Decimal("0") or agreement.hurdle_rate > Decimal("1"):
        raise ValidationError(
            f"hurdle_rate must be in [0, 1], got {agreement.hurdle_rate}",
            code="INVALID_HURDLE_RATE",
            field="hurdle_rate",
        )
    if agreement.hurdle_type == HurdleType.NONE and agreement.hurdle_rate != Decimal("0"):
        raise ValidationError(
            "hurdle_rate must be 0 when hurdle_type is NONE",
            code="HURDLE_RATE_WITH_NONE_TYPE",
            field="hurdle_rate",
        )
    if agreement.hurdle_type == HurdleType.SOFT_CATCH_UP:
        if agreement.catch_up_rate <= Decimal("0") or agreement.catch_up_rate > Decimal("1"):
            raise ValidationError(
                f"catch_up_rate must be in (0, 1], got {agreement.catch_up_rate}",
                code="INVALID_CATCH_UP_RATE",
                field="catch_up_rate",
            )
    if agreement.crystallization_frequency == CrystallizationFrequency.CUSTOM_DATES:
        if not agreement.crystallization_dates:
            raise CalendarBoundaryError(
                "CUSTOM_DATES frequency requires non-empty crystallization_dates",
                code="EMPTY_CUSTOM_CRYSTALLIZATION_DATES",
                field="crystallization_dates",
            )
        if len(set(agreement.crystallization_dates)) != len(agreement.crystallization_dates):
            raise ValidationError(
                "Duplicate dates in crystallization_dates",
                code="DUPLICATE_CRYSTALLIZATION_DATE",
                field="crystallization_dates",
            )


def validate_nav_snapshot(
    snapshot: NavSnapshot,
    agreement: FeeAgreement,
    config: EngineConfig,
    *,
    allow_multi_crystallization_span: bool = False,
) -> None:
    """Validate NAV snapshot consistency against agreement and calendar boundaries."""
    if not snapshot.snapshot_id.strip():
        raise ValidationError("snapshot_id must be non-empty", code="EMPTY_SNAPSHOT_ID", field="snapshot_id")
    if snapshot.version < 1:
        raise ValidationError("snapshot version must be >= 1", code="INVALID_VERSION", field="version")
    if snapshot.share_class != agreement.share_class:
        raise ValidationError(
            f"Snapshot share_class '{snapshot.share_class}' does not match agreement '{agreement.share_class}'",
            code="SHARE_CLASS_MISMATCH",
            field="share_class",
        )
    if snapshot.currency.upper() != agreement.currency.upper():
        raise ValidationError(
            f"Snapshot currency '{snapshot.currency}' does not match agreement '{agreement.currency}'",
            code="CURRENCY_MISMATCH",
            field="currency",
        )
    if snapshot.currency.upper() not in config.currency_minor_units:
        raise UnsupportedPolicyError(
            f"Unsupported currency '{snapshot.currency}'",
            code="UNSUPPORTED_CURRENCY",
            field="currency",
        )
    if snapshot.gross_nav_per_unit < Decimal("0"):
        raise ValidationError(
            f"gross_nav_per_unit must be non-negative, got {snapshot.gross_nav_per_unit}",
            code="NEGATIVE_NAV",
            field="gross_nav_per_unit",
        )
    if snapshot.valuation_date < snapshot.period_start_date:
        raise CalendarBoundaryError(
            f"valuation_date ({snapshot.valuation_date}) precedes period_start_date ({snapshot.period_start_date})",
            code="VALUATION_BEFORE_PERIOD_START",
            field="valuation_date",
        )
    if snapshot.valuation_date < agreement.effective_date:
        raise CalendarBoundaryError(
            f"valuation_date ({snapshot.valuation_date}) precedes agreement effective_date ({agreement.effective_date})",
            code="VALUATION_BEFORE_EFFECTIVE_DATE",
            field="valuation_date",
        )
    if not allow_multi_crystallization_span:
        crossed = has_crossed_crystallization_boundary(
            agreement, snapshot.period_start_date, snapshot.valuation_date
        )
        if crossed:
            crossed_str = ", ".join(d.isoformat() for d in crossed)
            raise CalendarBoundaryError(
                f"Period ({snapshot.period_start_date} -> {snapshot.valuation_date}) skips crystallization boundary: {crossed_str}",
                code="SKIPPED_CRYSTALLIZATION_BOUNDARY",
                field="valuation_date",
            )


def validate_investor_lots(
    lots: Sequence[InvestorLot],
    agreement: FeeAgreement,
    valuation_date: date,
) -> None:
    """Validate investor lots for uniqueness, share class match, positive units, and valid HWMs."""
    if not lots:
        raise ValidationError("At least one investor lot is required", code="EMPTY_INVESTOR_LOTS", field="lots")

    seen_ids: Set[str] = set()
    for lot in lots:
        if not lot.lot_id.strip():
            raise ValidationError("lot_id must be non-empty", code="EMPTY_LOT_ID", field="lot_id")
        if lot.lot_id in seen_ids:
            raise ValidationError(f"Duplicate lot_id '{lot.lot_id}'", code="DUPLICATE_LOT_ID", field="lot_id")
        seen_ids.add(lot.lot_id)

        if lot.share_class != agreement.share_class:
            raise ValidationError(
                f"Lot '{lot.lot_id}' share_class '{lot.share_class}' does not match agreement '{agreement.share_class}'",
                code="SHARE_CLASS_MISMATCH",
                field="lots.share_class",
            )
        if lot.units < Decimal("0"):
            raise ValidationError(
                f"Lot '{lot.lot_id}' units must be non-negative, got {lot.units}",
                code="NEGATIVE_UNITS",
                field="lots.units",
            )
        if lot.opening_nav_per_unit <= Decimal("0"):
            raise ValidationError(
                f"Lot '{lot.lot_id}' opening_nav_per_unit must be > 0, got {lot.opening_nav_per_unit}",
                code="NON_POSITIVE_OPENING_NAV",
                field="lots.opening_nav_per_unit",
            )
        if lot.high_water_mark <= Decimal("0"):
            raise ValidationError(
                f"Lot '{lot.lot_id}' high_water_mark must be > 0, got {lot.high_water_mark}",
                code="NON_POSITIVE_HWM",
                field="lots.high_water_mark",
            )
        if lot.hurdle_base_nav <= Decimal("0"):
            raise ValidationError(
                f"Lot '{lot.lot_id}' hurdle_base_nav must be > 0, got {lot.hurdle_base_nav}",
                code="NON_POSITIVE_HURDLE_BASE",
                field="lots.hurdle_base_nav",
            )
        if lot.prior_accrued_perf_fee < Decimal("0"):
            raise ValidationError(
                f"Lot '{lot.lot_id}' prior_accrued_perf_fee must be >= 0, got {lot.prior_accrued_perf_fee}",
                code="NEGATIVE_PRIOR_ACCRUAL",
                field="lots.prior_accrued_perf_fee",
            )
        if lot.subscription_date > valuation_date:
            raise ValidationError(
                f"Lot '{lot.lot_id}' subscription_date ({lot.subscription_date}) is after valuation_date ({valuation_date})",
                code="SUBSCRIPTION_AFTER_VALUATION",
                field="lots.subscription_date",
            )


def validate_capital_flows(
    flows: Iterable[CapitalFlow],
    existing_lots: Sequence[InvestorLot],
    agreement: FeeAgreement,
    period_start_date: date,
    valuation_date: date,
) -> None:
    """Validate mid-period subscriptions and redemptions."""
    lot_map = {lot.lot_id: lot for lot in existing_lots}
    seen_flow_ids: Set[str] = set()
    remaining_units = {lot.lot_id: lot.units for lot in existing_lots}

    for flow in flows:
        if flow.flow_id in seen_flow_ids:
            raise ValidationError(f"Duplicate flow_id '{flow.flow_id}'", code="DUPLICATE_FLOW_ID", field="flow_id")
        seen_flow_ids.add(flow.flow_id)

        if flow.share_class != agreement.share_class:
            raise ValidationError(
                f"Flow '{flow.flow_id}' share_class '{flow.share_class}' mismatches agreement '{agreement.share_class}'",
                code="SHARE_CLASS_MISMATCH",
                field="flows.share_class",
            )
        if flow.currency.upper() != agreement.currency.upper():
            raise ValidationError(
                f"Flow '{flow.flow_id}' currency '{flow.currency}' mismatches agreement '{agreement.currency}'",
                code="CURRENCY_MISMATCH",
                field="flows.currency",
            )
        if flow.flow_date < period_start_date or flow.flow_date > valuation_date:
            raise CalendarBoundaryError(
                f"Flow '{flow.flow_id}' date {flow.flow_date} outside period [{period_start_date}, {valuation_date}]",
                code="FLOW_DATE_OUT_OF_PERIOD",
                field="flows.flow_date",
            )
        if flow.nav_per_unit <= Decimal("0"):
            raise ValidationError(
                f"Flow '{flow.flow_id}' nav_per_unit must be > 0",
                code="NON_POSITIVE_FLOW_NAV",
                field="flows.nav_per_unit",
            )
        if flow.amount is None and flow.units is None:
            raise ValidationError(
                f"Flow '{flow.flow_id}' must specify either amount or units",
                code="MISSING_FLOW_QUANTITY",
                field="flows.amount",
            )
        if flow.amount is not None and flow.amount <= Decimal("0"):
            raise ValidationError(
                f"Flow '{flow.flow_id}' amount must be > 0",
                code="NON_POSITIVE_FLOW_AMOUNT",
                field="flows.amount",
            )
        if flow.units is not None and flow.units <= Decimal("0"):
            raise ValidationError(
                f"Flow '{flow.flow_id}' units must be > 0",
                code="NON_POSITIVE_FLOW_UNITS",
                field="flows.units",
            )

        if flow.flow_type == CapitalFlowType.SUBSCRIPTION:
            if flow.lot_id in lot_map:
                raise ValidationError(
                    f"Subscription flow '{flow.flow_id}' must create a distinct lot_id; '{flow.lot_id}' already exists",
                    code="SUBSCRIPTION_LOT_COLLISION",
                    field="flows.lot_id",
                )
            units_added = flow.units if flow.units is not None else (flow.amount / flow.nav_per_unit)  # type: ignore[operator]
            remaining_units[flow.lot_id] = units_added
            lot_map[flow.lot_id] = InvestorLot(
                lot_id=flow.lot_id,
                investor_id=flow.investor_id,
                share_class=flow.share_class,
                series_id=flow.series_id or flow.flow_date.isoformat(),
                subscription_date=flow.flow_date,
                units=units_added,
                opening_nav_per_unit=flow.nav_per_unit,
                high_water_mark=flow.nav_per_unit,
                hurdle_base_nav=flow.nav_per_unit,
            )
        elif flow.flow_type == CapitalFlowType.REDEMPTION:
            if flow.lot_id not in lot_map:
                raise ValidationError(
                    f"Redemption flow '{flow.flow_id}' references unknown lot_id '{flow.lot_id}'",
                    code="UNKNOWN_REDEMPTION_LOT",
                    field="flows.lot_id",
                )
            redeem_units = flow.units if flow.units is not None else (flow.amount / flow.nav_per_unit)  # type: ignore[operator]
            if redeem_units > remaining_units[flow.lot_id]:
                raise ValidationError(
                    f"Redemption flow '{flow.flow_id}' units ({redeem_units}) exceed available units ({remaining_units[flow.lot_id]}) in lot '{flow.lot_id}'",
                    code="REDEMPTION_EXCEEDS_UNITS",
                    field="flows.units",
                )
            remaining_units[flow.lot_id] -= redeem_units
