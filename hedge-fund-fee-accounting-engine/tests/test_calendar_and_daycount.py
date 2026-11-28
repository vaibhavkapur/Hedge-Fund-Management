"""Crystallization calendar and exact Decimal day-count tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from hedge_fund_fee_engine.calendar import (
    generate_crystallization_dates,
    has_crossed_crystallization_boundary,
    is_crystallization_date,
    year_fraction,
)
from hedge_fund_fee_engine.errors import CalendarBoundaryError
from hedge_fund_fee_engine.models import (
    CrystallizationFrequency,
    DayCountConvention,
    FeeAgreement,
    HurdleType,
    HwmResetPolicy,
    ManagementFeeBasis,
)


class TestCalendarAndDayCount(unittest.TestCase):
    def _make_agreement(
        self,
        freq: CrystallizationFrequency,
        custom_dates: tuple = (),
    ) -> FeeAgreement:
        return FeeAgreement(
            agreement_id="AGR-CAL",
            version=1,
            share_class="CLASS_CAL",
            currency="USD",
            effective_date=date(2024, 12, 31),
            management_fee_rate=Decimal("0.02"),
            management_fee_basis=ManagementFeeBasis.BEGINNING_NAV,
            day_count=DayCountConvention.ACT_365,
            performance_fee_rate=Decimal("0.20"),
            hurdle_type=HurdleType.NONE,
            hurdle_rate=Decimal("0"),
            crystallization_frequency=freq,
            hwm_reset_policy=HwmResetPolicy.ON_CRYSTALLIZATION_GAIN,
            crystallization_dates=custom_dates,
        )

    def test_day_count_conventions_exact_fractions(self) -> None:
        self.assertEqual(
            year_fraction(date(2024, 12, 31), date(2025, 3, 31), DayCountConvention.ACT_365),
            Decimal(90) / Decimal(365),
        )
        self.assertEqual(
            year_fraction(date(2024, 12, 31), date(2025, 3, 31), DayCountConvention.ACT_360),
            Decimal("0.25"),
        )
        self.assertEqual(
            year_fraction(date(2024, 12, 31), date(2025, 12, 31), DayCountConvention.THIRTY_360),
            Decimal("1"),
        )
        self.assertEqual(
            year_fraction(date(2024, 12, 31), date(2025, 1, 31), DayCountConvention.MONTHLY_1_12),
            Decimal(1) / Decimal(12),
        )
        self.assertEqual(
            year_fraction(date(2024, 12, 31), date(2025, 3, 31), DayCountConvention.QUARTERLY_1_4),
            Decimal("0.25"),
        )
        # Leap year February month-end
        self.assertEqual(
            year_fraction(date(2024, 1, 31), date(2024, 2, 29), DayCountConvention.MONTHLY_1_12),
            Decimal(1) / Decimal(12),
        )

    def test_crystallization_schedules_and_boundaries(self) -> None:
        q_dates = generate_crystallization_dates(CrystallizationFrequency.QUARTERLY, 2025)
        self.assertEqual(
            q_dates,
            (date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31)),
        )

        agr_q = self._make_agreement(CrystallizationFrequency.QUARTERLY)
        self.assertTrue(is_crystallization_date(agr_q, date(2025, 3, 31)))
        self.assertFalse(is_crystallization_date(agr_q, date(2025, 2, 28)))

        crossed = has_crossed_crystallization_boundary(agr_q, date(2024, 12, 31), date(2025, 6, 30))
        self.assertEqual(crossed, [date(2025, 3, 31)])

        agr_custom = self._make_agreement(
            CrystallizationFrequency.CUSTOM_DATES,
            custom_dates=(date(2025, 6, 15), date(2025, 12, 15)),
        )
        self.assertTrue(is_crystallization_date(agr_custom, date(2025, 6, 15)))
        self.assertFalse(is_crystallization_date(agr_custom, date(2025, 6, 30)))

    def test_negative_period_raises_calendar_error(self) -> None:
        with self.assertRaises(CalendarBoundaryError) as ctx:
            year_fraction(date(2025, 3, 31), date(2025, 1, 31), DayCountConvention.ACT_365)
        self.assertEqual(ctx.exception.code, "NEGATIVE_ACCRUAL_PERIOD")


if __name__ == "__main__":
    unittest.main()
