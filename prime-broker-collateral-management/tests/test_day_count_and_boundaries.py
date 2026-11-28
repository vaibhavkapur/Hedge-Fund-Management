"""Boundary and failure tests for financing day-count conventions and leap-year/year-end transitions."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from pb_collateral.errors import DayCountBoundaryError
from pb_collateral.financing import calculate_financing_accruals, compute_year_fraction
from pb_collateral.fx import FxTable
from pb_collateral.models import (
    CashBalanceRecord,
    CashBalanceType,
    DayCountConvention,
    FinancingRateSchedule,
)


class TestDayCountBoundaries(unittest.TestCase):
    def test_end_date_before_start_date_raises(self) -> None:
        with self.assertRaises(DayCountBoundaryError):
            compute_year_fraction(date(2026, 3, 17), date(2026, 3, 16), DayCountConvention.ACT_360)

    def test_zero_day_period_allowed_vs_disallowed(self) -> None:
        yf, notes = compute_year_fraction(date(2026, 3, 16), date(2026, 3, 16), DayCountConvention.ACT_360, allow_zero_days=True)
        self.assertEqual(yf, Decimal("0"))
        self.assertIn("ZERO_DAY_PERIOD", notes)

        with self.assertRaises(DayCountBoundaryError):
            compute_year_fraction(date(2026, 3, 16), date(2026, 3, 16), DayCountConvention.ACT_360, allow_zero_days=False)

    def test_act_act_isda_leap_year_to_non_leap_year_boundary(self) -> None:
        # 2024 is a leap year (366 days); 2025 is non-leap (365 days)
        # 2024-12-30 to 2025-01-02: 2 days in 2024 (2/366) + 1 day in 2025 (1/365)
        yf, notes = compute_year_fraction(date(2024, 12, 30), date(2025, 1, 2), DayCountConvention.ACT_ACT_ISDA)
        expected = (Decimal("2") / Decimal("366")) + (Decimal("1") / Decimal("365"))
        self.assertEqual(yf, expected)
        self.assertIn("YEAR_BOUNDARY_SPLIT", notes)
        self.assertIn("LEAP_YEAR_DENOM_2024", notes)

    def test_feb_28_to_feb_29_to_mar_01_leap_year(self) -> None:
        # Leap year Feb 28 -> Mar 1 is 2 actual days
        yf_360, _ = compute_year_fraction(date(2024, 2, 28), date(2024, 3, 1), DayCountConvention.ACT_360)
        self.assertEqual(yf_360, Decimal("2") / Decimal("360"))

        yf_365f, notes_365 = compute_year_fraction(date(2024, 2, 28), date(2024, 3, 1), DayCountConvention.ACT_365F)
        self.assertEqual(yf_365f, Decimal("2") / Decimal("365"))
        self.assertIn("ACT_365F_FIXED_DENOMINATOR_IN_LEAP_YEAR", notes_365)

        yf_isda, _ = compute_year_fraction(date(2024, 2, 28), date(2024, 3, 1), DayCountConvention.ACT_ACT_ISDA)
        self.assertEqual(yf_isda, Decimal("2") / Decimal("366"))

    def test_thirty_360_month_end_clamping(self) -> None:
        # Jan 31 to Mar 31 -> d1=30, d2=30 -> 60/360
        yf, notes = compute_year_fraction(date(2026, 1, 31), date(2026, 3, 31), DayCountConvention.THIRTY_360)
        self.assertEqual(yf, Decimal("60") / Decimal("360"))
        self.assertIn("30_360_START_DAY_CLAMPED_31_TO_30", notes)
        self.assertIn("30_360_END_DAY_CLAMPED_31_TO_30", notes)

    def test_financing_accrual_across_leap_year_boundary_reconciles_exact_cents(self) -> None:
        sched = FinancingRateSchedule(
            schedule_id="FIN-ISDA-USD",
            broker_id="PB-ALPHA",
            currency="USD",
            benchmark_name="SOFR",
            benchmark_rate=Decimal("0.0500"),
            debit_spread_bps=Decimal("100"),  # 6.00% total
            credit_spread_bps=Decimal("50"),
            short_rebate_spread_bps=Decimal("50"),
            day_count=DayCountConvention.ACT_ACT_ISDA,
            effective_date=date(2024, 1, 1),
        )
        debit = CashBalanceRecord(
            balance_id="DB-1",
            broker_id="PB-ALPHA",
            account_id="ACC-1",
            currency="USD",
            amount=Decimal("-1000000.00"),
            balance_type=CashBalanceType.DEBIT_FINANCING,
        )
        summary = calculate_financing_accruals(
            broker_id="PB-ALPHA",
            account_id="ACC-1",
            cash_balances=[debit],
            schedules={("PB-ALPHA", "USD"): sched},
            fx_table=FxTable([]),
            base_currency="USD",
            start_date=date(2024, 12, 30),
            end_date=date(2025, 1, 2),
        )
        # -1,000,000 * 0.06 * (2/366 + 1/365) = -60,000 * (0.00546448087... + 0.00273972602...) = -492.252414... -> -492.25
        self.assertEqual(summary.net_accrual_base, Decimal("-492.25"))
        self.assertIn("YEAR_BOUNDARY_SPLIT", summary.lines[0].notes)


if __name__ == "__main__":
    unittest.main()
