"""Domain correctness tests: reference 2-and-20 and loss/recovery below HWM."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from hedge_fund_fee_engine.models import EngineConfig
from hedge_fund_fee_engine.reporting import run_scenario_fixture
from hedge_fund_fee_engine.service import FeeAccountingService
from hedge_fund_fee_engine.storage import SqliteFeeStore


FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "default_engine_config.json"


class TestDomainHwmLossRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.config = EngineConfig.from_json_file(DEFAULT_CONFIG)

    def test_reference_basic_2_and_20_exact_match(self) -> None:
        store = SqliteFeeStore(Path(":memory:"))
        service = FeeAccountingService(config=self.config, store=store)
        report = run_scenario_fixture(FIXTURES_DIR / "reference_basic_2_and_20.json", service)
        self.assertTrue(report["all_passed"], msg=report["comparisons"])
        self.assertEqual(report["run_count"], 1)
        calc = report["runs"][0]["lot_calculations"][0]
        self.assertEqual(Decimal(calc["management_fee_amount"]), Decimal("5000.00"))
        self.assertEqual(Decimal(calc["crystallized_performance_fee"]), Decimal("19000.00"))
        self.assertEqual(Decimal(calc["new_hwm"]), Decimal("107.600000"))
        store.close()

    def test_loss_and_recovery_below_hwm_four_quarters(self) -> None:
        store = SqliteFeeStore(Path(":memory:"))
        service = FeeAccountingService(config=self.config, store=store)
        report = run_scenario_fixture(FIXTURES_DIR / "loss_and_recovery_hwm.json", service)
        self.assertTrue(report["all_passed"], msg=report["comparisons"])
        self.assertEqual(report["run_count"], 4)

        q1, q2, q3, q4 = [r["lot_calculations"][0] for r in report["runs"]]

        # Q1 sets HWM to 107.60
        self.assertEqual(Decimal(q1["new_hwm"]), Decimal("107.600000"))
        self.assertEqual(Decimal(q1["crystallized_performance_fee"]), Decimal("19000.00"))

        # Q2 drawdown below HWM -> zero perf fee, HWM never resets downward
        self.assertEqual(Decimal(q2["crystallized_performance_fee"]), Decimal("0.00"))
        self.assertEqual(Decimal(q2["new_hwm"]), Decimal("107.600000"))
        self.assertEqual(Decimal(q2["loss_carryforward_amount"]), Decimal("131380.00"))

        # Q3 partial recovery still below HWM -> zero perf fee, HWM still 107.60
        self.assertEqual(Decimal(q3["crystallized_performance_fee"]), Decimal("0.00"))
        self.assertEqual(Decimal(q3["new_hwm"]), Decimal("107.600000"))
        self.assertEqual(Decimal(q3["loss_carryforward_amount"]), Decimal("30723.10"))

        # Q4 recovery above HWM -> perf fee ONLY on excess above 107.60
        self.assertEqual(Decimal(q4["eligible_gain_per_unit"]), Decimal("6.877362"))
        self.assertEqual(Decimal(q4["crystallized_performance_fee"]), Decimal("13754.72"))
        self.assertEqual(Decimal(q4["new_hwm"]), Decimal("113.101890"))
        self.assertEqual(Decimal(q4["loss_carryforward_amount"]), Decimal("0.00"))
        store.close()


if __name__ == "__main__":
    unittest.main()
