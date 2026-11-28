"""Boundary and failure behavior tests: hurdles, crystallization dates, and non-negative accrual."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from hedge_fund_fee_engine.domain import evaluate_lot
from hedge_fund_fee_engine.models import (
    CrystallizationFrequency,
    DayCountConvention,
    EngineConfig,
    FeeAgreement,
    HurdleType,
    HwmResetPolicy,
    InvestorLot,
    ManagementFeeBasis,
)
from hedge_fund_fee_engine.reporting import run_scenario_fixture
from hedge_fund_fee_engine.service import FeeAccountingService
from hedge_fund_fee_engine.storage import SqliteFeeStore


FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "default_engine_config.json"


class TestHurdleAndCrystallizationBoundaries(unittest.TestCase):
    def setUp(self) -> None:
        self.config = EngineConfig.from_json_file(DEFAULT_CONFIG)

    def test_annual_crystallization_and_hard_hurdle_lifecycle(self) -> None:
        store = SqliteFeeStore(Path(":memory:"))
        service = FeeAccountingService(config=self.config, store=store)
        report = run_scenario_fixture(
            FIXTURES_DIR / "hurdle_and_crystallization_boundaries.json", service
        )
        self.assertTrue(report["all_passed"], msg=report["comparisons"])
        self.assertEqual(report["run_count"], 4)

        q1, q2, q3, q4 = [r["lot_calculations"][0] for r in report["runs"]]

        # Q1: above HWM (100), below hurdle (102) -> 0 perf fee, interim
        self.assertFalse(q1["is_crystallization_event"])
        self.assertEqual(Decimal(q1["performance_fee_delta"]), Decimal("0.00"))
        self.assertEqual(Decimal(q1["new_hwm"]), Decimal("100.000000"))

        # Q2: interim accrual 10,000, HWM MUST NOT reset on non-crystallization date
        self.assertFalse(q2["is_crystallization_event"])
        self.assertEqual(Decimal(q2["ending_accrued_perf_fee"]), Decimal("10000.00"))
        self.assertEqual(Decimal(q2["crystallized_performance_fee"]), Decimal("0.00"))
        self.assertEqual(Decimal(q2["new_hwm"]), Decimal("100.000000"))

        # Q3: pullback triggers reversal of 6,000 -> accrued drops to 4,000 (>= 0)
        self.assertFalse(q3["is_crystallization_event"])
        self.assertEqual(Decimal(q3["performance_fee_delta"]), Decimal("-6000.00"))
        self.assertEqual(Decimal(q3["ending_accrued_perf_fee"]), Decimal("4000.00"))

        # Q4: annual crystallization boundary settles 10,000 payable & resets HWM to 112.00
        self.assertTrue(q4["is_crystallization_event"])
        self.assertEqual(Decimal(q4["performance_fee_delta"]), Decimal("6000.00"))
        self.assertEqual(Decimal(q4["crystallized_performance_fee"]), Decimal("10000.00"))
        self.assertEqual(Decimal(q4["ending_accrued_perf_fee"]), Decimal("0.00"))
        self.assertEqual(Decimal(q4["new_hwm"]), Decimal("112.000000"))

        # Verify cumulative net expense across Q1-Q4 equals crystallized payable (10,000.00)
        net_perf_expense = sum(
            Decimal(r["reconciliation"]["total_performance_fee_journaled"]) for r in report["runs"]
        )
        self.assertEqual(net_perf_expense, Decimal("10000.00"))
        store.close()

    def test_no_negative_delta_when_intra_period_reversal_disabled(self) -> None:
        agr = FeeAgreement(
            agreement_id="AGR-NO-REV",
            version=1,
            share_class="CLASS_NR",
            currency="USD",
            effective_date=date(2024, 12, 31),
            management_fee_rate=Decimal("0.00"),
            management_fee_basis=ManagementFeeBasis.BEGINNING_NAV,
            day_count=DayCountConvention.QUARTERLY_1_4,
            performance_fee_rate=Decimal("0.20"),
            hurdle_type=HurdleType.NONE,
            hurdle_rate=Decimal("0"),
            crystallization_frequency=CrystallizationFrequency.ANNUAL,
            hwm_reset_policy=HwmResetPolicy.ON_CRYSTALLIZATION_GAIN,
            allow_negative_accrual=False,
            allow_intra_period_perf_reversal=False,
        )
        lot = InvestorLot(
            lot_id="LOT-NR",
            investor_id="INV-NR",
            share_class="CLASS_NR",
            series_id="S1",
            subscription_date=date(2024, 12, 31),
            units=Decimal("1000.000000"),
            opening_nav_per_unit=Decimal("110.000000"),
            high_water_mark=Decimal("100.000000"),
            hurdle_base_nav=Decimal("100.000000"),
            prior_accrued_perf_fee=Decimal("2000.00"),
            last_crystallization_date=date(2024, 12, 31),
        )
        calc, _ = evaluate_lot(
            lot=lot,
            period_start_date=date(2025, 3, 31),
            valuation_date=date(2025, 6, 30),
            gross_nav_per_unit=Decimal("105.000000"),
            agreement=agr,
            config=self.config,
        )
        self.assertEqual(calc.performance_fee_delta, Decimal("0.00"))
        self.assertEqual(calc.ending_accrued_perf_fee, Decimal("2000.00"))
        self.assertIn("REVERSAL_BLOCKED_BY_POLICY", calc.notes)

    def test_exact_hurdle_boundary_zero_fee(self) -> None:
        agr = FeeAgreement(
            agreement_id="AGR-EXACT-HURDLE",
            version=1,
            share_class="CLASS_EH",
            currency="USD",
            effective_date=date(2024, 12, 31),
            management_fee_rate=Decimal("0.00"),
            management_fee_basis=ManagementFeeBasis.BEGINNING_NAV,
            day_count=DayCountConvention.QUARTERLY_1_4,
            performance_fee_rate=Decimal("0.20"),
            hurdle_type=HurdleType.HARD_NON_COMPOUNDING,
            hurdle_rate=Decimal("0.08"),
            crystallization_frequency=CrystallizationFrequency.QUARTERLY,
            hwm_reset_policy=HwmResetPolicy.ON_CRYSTALLIZATION_GAIN,
        )
        lot = InvestorLot(
            lot_id="LOT-EH",
            investor_id="INV-EH",
            share_class="CLASS_EH",
            series_id="S1",
            subscription_date=date(2024, 12, 31),
            units=Decimal("1000.000000"),
            opening_nav_per_unit=Decimal("100.000000"),
            high_water_mark=Decimal("100.000000"),
            hurdle_base_nav=Decimal("100.000000"),
            last_crystallization_date=date(2024, 12, 31),
        )
        # Exactly at hurdle = 102.00 -> 0 fee
        calc_at, _ = evaluate_lot(
            lot=lot,
            period_start_date=date(2024, 12, 31),
            valuation_date=date(2025, 3, 31),
            gross_nav_per_unit=Decimal("102.000000"),
            agreement=agr,
            config=self.config,
        )
        self.assertEqual(calc_at.crystallized_performance_fee, Decimal("0.00"))

        # 1 cent above hurdle = 102.01 -> positive fee = 0.01 * 1000 * 0.20 = 2.00
        calc_above, _ = evaluate_lot(
            lot=lot,
            period_start_date=date(2024, 12, 31),
            valuation_date=date(2025, 3, 31),
            gross_nav_per_unit=Decimal("102.010000"),
            agreement=agr,
            config=self.config,
        )
        self.assertEqual(calc_above.crystallized_performance_fee, Decimal("2.00"))


if __name__ == "__main__":
    unittest.main()
