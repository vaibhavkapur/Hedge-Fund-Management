"""Exact financing accrual and day-count calculation engine (zero I/O)."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Dict, List, Mapping, Sequence, Tuple

from pb_collateral.errors import DayCountBoundaryError, MissingConfigurationError
from pb_collateral.fx import FxTable
from pb_collateral.models import (
    CashBalanceRecord,
    CashBalanceType,
    DayCountConvention,
    FinancingAccrualLine,
    FinancingAccrualSummary,
    FinancingRateSchedule,
    quantize_money,
)

BPS_DIVISOR = Decimal("10000")


def is_leap_year(year: int) -> bool:
    return calendar.isleap(year)


def compute_year_fraction(
    start_date: date,
    end_date: date,
    convention: DayCountConvention,
    *,
    allow_zero_days: bool = True,
) -> Tuple[Decimal, Tuple[str, ...]]:
    """Compute exact year fraction with explicit boundary handling.

    Rules:
    - end_date < start_date raises DayCountBoundaryError.
    - end_date == start_date returns Decimal("0") if allow_zero_days else raises DayCountBoundaryError.
    - ACT_360: actual days / 360.
    - ACT_365F: actual days / 365 (fixed 365 even in leap years).
    - ACT_ACT_ISDA: splits across calendar years using 366 for leap years and 365 for non-leap years.
    - THIRTY_360: US 30/360 (Bond Basis) with month-end Feb and 31st day adjustments.
    """
    if end_date < start_date:
        raise DayCountBoundaryError(
            f"Financing accrual end_date ({end_date.isoformat()}) precedes start_date ({start_date.isoformat()})",
            context={"start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "convention": convention.value},
        )
    if end_date == start_date:
        if not allow_zero_days:
            raise DayCountBoundaryError(
                f"Zero-day financing accrual period disallowed ({start_date.isoformat()} to {end_date.isoformat()})",
                context={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            )
        return Decimal("0"), ("ZERO_DAY_PERIOD",)

    notes: List[str] = []
    actual_days = (end_date - start_date).days

    if convention == DayCountConvention.ACT_360:
        return Decimal(actual_days) / Decimal("360"), tuple(notes)

    if convention == DayCountConvention.ACT_365F:
        if is_leap_year(start_date.year) or is_leap_year(end_date.year):
            notes.append("ACT_365F_FIXED_DENOMINATOR_IN_LEAP_YEAR")
        return Decimal(actual_days) / Decimal("365"), tuple(notes)

    if convention == DayCountConvention.ACT_ACT_ISDA:
        total = Decimal("0")
        cursor = start_date
        if start_date.year != end_date.year:
            notes.append("YEAR_BOUNDARY_SPLIT")
        while cursor < end_date:
            next_year_start = date(cursor.year + 1, 1, 1)
            seg_end = min(end_date, next_year_start)
            seg_days = (seg_end - cursor).days
            denom = Decimal("366") if is_leap_year(cursor.year) else Decimal("365")
            if denom == Decimal("366"):
                notes.append(f"LEAP_YEAR_DENOM_{cursor.year}")
            total += Decimal(seg_days) / denom
            cursor = seg_end
        return total, tuple(dict.fromkeys(notes))

    if convention == DayCountConvention.THIRTY_360:
        d1 = start_date.day
        m1 = start_date.month
        y1 = start_date.year
        d2 = end_date.day
        m2 = end_date.month
        y2 = end_date.year

        # US 30/360 NASD rules
        if d1 == 31:
            d1 = 30
            notes.append("30_360_START_DAY_CLAMPED_31_TO_30")
        if d2 == 31 and d1 == 30:
            d2 = 30
            notes.append("30_360_END_DAY_CLAMPED_31_TO_30")
        # Feb end-of-month boundary diagnostic
        if m1 == 2 and d1 == calendar.monthrange(y1, 2)[1]:
            notes.append("30_360_FEB_MONTH_END_START")
        days_360 = 360 * (y2 - y1) + 30 * (m2 - m1) + (d2 - d1)
        if days_360 < 0:
            raise DayCountBoundaryError(
                f"THIRTY_360 produced negative day count ({days_360})",
                context={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            )
        return Decimal(days_360) / Decimal("360"), tuple(notes)

    raise DayCountBoundaryError(
        f"Unhandled day-count convention: {convention}",
        context={"convention": str(convention)},
    )


def effective_rate_for_balance(
    balance_type: CashBalanceType,
    schedule: FinancingRateSchedule,
) -> Decimal:
    """Compute effective annualized interest rate for a cash balance bucket."""
    if balance_type == CashBalanceType.DEBIT_FINANCING:
        return schedule.benchmark_rate + (schedule.debit_spread_bps / BPS_DIVISOR)
    if balance_type in (CashBalanceType.FREE_CASH, CashBalanceType.MARGIN_CASH):
        return max(Decimal("0"), schedule.benchmark_rate - (schedule.credit_spread_bps / BPS_DIVISOR))
    if balance_type == CashBalanceType.SHORT_PROCEEDS:
        return schedule.benchmark_rate - (schedule.short_rebate_spread_bps / BPS_DIVISOR)
    return Decimal("0")


def calculate_financing_accruals(
    *,
    broker_id: str,
    account_id: str,
    cash_balances: Sequence[CashBalanceRecord],
    schedules: Mapping[Tuple[str, str], FinancingRateSchedule],
    fx_table: FxTable,
    base_currency: str,
    start_date: date,
    end_date: date,
    rounding_mode: str = "ROUND_HALF_EVEN",
    allow_zero_days: bool = True,
) -> FinancingAccrualSummary:
    """Compute exact financing accruals across all cash balances for a broker account.

    Sign convention:
    - Positive accrual = income to the fund (credit cash interest, positive short rebate).
    - Negative accrual = financing expense paid to prime broker (margin debit interest or hard-to-borrow negative rebate).
    """
    lines: List[FinancingAccrualLine] = []
    net_base = quantize_money(Decimal("0"), base_currency, rounding_mode=rounding_mode)

    for cb in cash_balances:
        schedule = schedules.get((broker_id, cb.currency))
        if schedule is None:
            raise MissingConfigurationError(
                f"Missing financing rate schedule for broker='{broker_id}', currency='{cb.currency}'",
                context={"broker_id": broker_id, "currency": cb.currency, "balance_id": cb.balance_id},
            )

        year_frac, notes = compute_year_fraction(
            start_date,
            end_date,
            schedule.day_count,
            allow_zero_days=allow_zero_days,
        )
        eff_rate = effective_rate_for_balance(cb.balance_type, schedule)
        # cb.amount is negative for DEBIT_FINANCING, positive for credit/short proceeds -> sign follows naturally!
        raw_accrual_local = cb.amount * eff_rate * year_frac
        accrual_local = quantize_money(raw_accrual_local, cb.currency, rounding_mode=rounding_mode)
        accrual_base, fx_rate = fx_table.convert(accrual_local, cb.currency, base_currency, as_of_date=end_date)

        lines.append(
            FinancingAccrualLine(
                balance_id=cb.balance_id,
                broker_id=broker_id,
                account_id=account_id,
                currency=cb.currency,
                balance_type=cb.balance_type,
                principal_local=quantize_money(cb.amount, cb.currency, rounding_mode=rounding_mode),
                start_date=start_date,
                end_date=end_date,
                day_count=schedule.day_count,
                year_fraction=year_frac,
                effective_rate=eff_rate,
                accrual_local=accrual_local,
                fx_rate_to_base=fx_rate,
                accrual_base=accrual_base,
                notes=notes,
            )
        )
        net_base += accrual_base

    return FinancingAccrualSummary(
        broker_id=broker_id,
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        base_currency=base_currency,
        lines=tuple(lines),
        net_accrual_base=quantize_money(net_base, base_currency, rounding_mode=rounding_mode),
    )
