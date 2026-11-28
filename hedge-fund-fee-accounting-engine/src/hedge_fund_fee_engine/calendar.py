"""Crystallization calendar and exact Decimal day-count conventions."""

from __future__ import annotations

import calendar as std_calendar
from datetime import date
from decimal import Decimal
from typing import List, Tuple

from .errors import CalendarBoundaryError, UnsupportedPolicyError
from .models import CrystallizationFrequency, DayCountConvention, FeeAgreement


def is_month_end(d: date) -> bool:
    """Return True if d is the last calendar day of its month."""
    _, last_day = std_calendar.monthrange(d.year, d.month)
    return d.day == last_day


def is_quarter_end(d: date) -> bool:
    """Return True if d is March 31, June 30, September 30, or December 31."""
    return d.month in (3, 6, 9, 12) and is_month_end(d)


def is_year_end(d: date) -> bool:
    """Return True if d is December 31."""
    return d.month == 12 and d.day == 31


def generate_crystallization_dates(
    frequency: CrystallizationFrequency,
    year: int,
    custom_dates: Tuple[date, ...] = (),
) -> Tuple[date, ...]:
    """Generate ordered crystallization dates for a given calendar year."""
    if frequency == CrystallizationFrequency.MONTHLY:
        return tuple(date(year, m, std_calendar.monthrange(year, m)[1]) for m in range(1, 13))
    if frequency == CrystallizationFrequency.QUARTERLY:
        return (
            date(year, 3, 31),
            date(year, 6, 30),
            date(year, 9, 30),
            date(year, 12, 31),
        )
    if frequency == CrystallizationFrequency.ANNUAL:
        return (date(year, 12, 31),)
    if frequency == CrystallizationFrequency.CUSTOM_DATES:
        if not custom_dates:
            raise CalendarBoundaryError(
                "CUSTOM_DATES crystallization frequency requires at least one explicit date",
                code="EMPTY_CUSTOM_CRYSTALLIZATION_DATES",
                field="crystallization_dates",
            )
        return tuple(sorted(d for d in custom_dates if d.year == year))
    raise UnsupportedPolicyError(
        f"Unsupported crystallization frequency '{frequency}'",
        code="UNSUPPORTED_CRYSTALLIZATION_FREQ",
        field="crystallization_frequency",
    )


def is_crystallization_date(agreement: FeeAgreement, valuation_date: date) -> bool:
    """Determine whether valuation_date is a scheduled crystallization boundary."""
    if valuation_date < agreement.effective_date:
        raise CalendarBoundaryError(
            f"Valuation date {valuation_date.isoformat()} precedes agreement effective_date {agreement.effective_date.isoformat()}",
            code="VALUATION_BEFORE_EFFECTIVE_DATE",
            field="valuation_date",
        )
    freq = agreement.crystallization_frequency
    if freq == CrystallizationFrequency.MONTHLY:
        return is_month_end(valuation_date)
    if freq == CrystallizationFrequency.QUARTERLY:
        return is_quarter_end(valuation_date)
    if freq == CrystallizationFrequency.ANNUAL:
        return is_year_end(valuation_date)
    if freq == CrystallizationFrequency.CUSTOM_DATES:
        if not agreement.crystallization_dates:
            raise CalendarBoundaryError(
                "CUSTOM_DATES crystallization frequency requires non-empty crystallization_dates",
                code="EMPTY_CUSTOM_CRYSTALLIZATION_DATES",
                field="crystallization_dates",
            )
        return valuation_date in agreement.crystallization_dates
    raise UnsupportedPolicyError(
        f"Unsupported crystallization frequency '{freq}'",
        code="UNSUPPORTED_CRYSTALLIZATION_FREQ",
        field="crystallization_frequency",
    )


def has_crossed_crystallization_boundary(
    agreement: FeeAgreement,
    period_start_date: date,
    valuation_date: date,
) -> List[date]:
    """Return any scheduled crystallization dates strictly between period_start_date and valuation_date.

    Used to guard against silently skipping a crystallization boundary inside a multi-period window.
    """
    crossed: List[date] = []
    for yr in range(period_start_date.year, valuation_date.year + 1):
        dates = generate_crystallization_dates(
            agreement.crystallization_frequency,
            yr,
            agreement.crystallization_dates,
        )
        for d in dates:
            if period_start_date < d < valuation_date:
                crossed.append(d)
    return crossed


def year_fraction(start_date: date, end_date: date, convention: DayCountConvention) -> Decimal:
    """Compute exact Decimal year fraction between start_date and end_date."""
    if end_date < start_date:
        raise CalendarBoundaryError(
            f"end_date ({end_date.isoformat()}) precedes start_date ({start_date.isoformat()})",
            code="NEGATIVE_ACCRUAL_PERIOD",
            field="valuation_date",
        )
    if end_date == start_date:
        return Decimal("0")

    actual_days = Decimal((end_date - start_date).days)

    if convention == DayCountConvention.ACT_365:
        return actual_days / Decimal(365)

    if convention == DayCountConvention.ACT_360:
        return actual_days / Decimal(360)

    if convention == DayCountConvention.THIRTY_360:
        d1 = min(start_date.day, 30)
        d2 = 30 if (end_date.day == 31 and d1 == 30) else min(end_date.day, 30)
        days_360 = (
            360 * (end_date.year - start_date.year)
            + 30 * (end_date.month - start_date.month)
            + (d2 - d1)
        )
        return Decimal(days_360) / Decimal(360)

    if convention == DayCountConvention.MONTHLY_1_12:
        # Exact 1/12 per full calendar month boundary (e.g., month-end to month-end);
        # falls back to pro-rata fraction within a partial month.
        months_diff = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
        if months_diff >= 1 and is_month_end(start_date) and is_month_end(end_date):
            return Decimal(months_diff) / Decimal(12)
        _, month_days = std_calendar.monthrange(end_date.year, end_date.month)
        return (actual_days / Decimal(month_days)) / Decimal(12)

    if convention == DayCountConvention.QUARTERLY_1_4:
        months_diff = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)
        if months_diff == 3 and is_month_end(start_date) and is_month_end(end_date):
            return Decimal("0.25")
        if months_diff > 0 and months_diff % 3 == 0 and is_month_end(start_date) and is_month_end(end_date):
            return Decimal(months_diff) / Decimal(12)
        # Pro-rata within a quarter using 90-day standard quarter length for mid-period lots
        return (actual_days / Decimal(90)) * Decimal("0.25")

    raise UnsupportedPolicyError(
        f"Unsupported day-count convention '{convention}'",
        code="UNSUPPORTED_DAY_COUNT",
        field="day_count",
    )
