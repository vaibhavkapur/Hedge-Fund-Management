"""Reproducibility, operational error handling, restart/rerun idempotency, and CLI tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from fund_nav_engine.calculator import calculate_nav
from fund_nav_engine.cli import main as cli_main
from fund_nav_engine.errors import ConfigurationError, ValidationError
from fund_nav_engine.io_loader import bundle_from_dict, config_from_dict, load_bundle, load_config
from fund_nav_engine.models import canonical_json
from fund_nav_engine.service import NavCalculationService
from fund_nav_engine.store import SqliteNavStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "data" / "fixtures"
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"


class TestReproducibilityAndOps(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)

    def test_reproducibility_order_invariance_and_hash_stability(self) -> None:
        bundle = load_bundle(FIXTURES / "reference_scenario.json")
        # Reverse order of all input collections
        reversed_bundle = replace(
            bundle,
            positions=tuple(reversed(bundle.positions)),
            prices=tuple(reversed(bundle.prices)),
            fx_rates=tuple(reversed(bundle.fx_rates)),
            liabilities=tuple(reversed(bundle.liabilities)),
            accruals=tuple(reversed(bundle.accruals)),
            share_classes=tuple(reversed(bundle.share_classes)),
            capital_flows=tuple(reversed(bundle.capital_flows)),
            ledger_balances=tuple(reversed(bundle.ledger_balances)),
        )
        fixed_ts = datetime(2026, 3, 31, 17, 0, tzinfo=timezone.utc)
        res1 = calculate_nav(bundle, self.config, calculated_at=fixed_ts)
        res2 = calculate_nav(reversed_bundle, self.config, calculated_at=fixed_ts)

        self.assertEqual(res1.input_hash, res2.input_hash)
        self.assertEqual(res1.snapshot_id, res2.snapshot_id)
        self.assertEqual(res1.output_hash, res2.output_hash)
        self.assertEqual(canonical_json(res1.to_dict()), canonical_json(res2.to_dict()))

    def test_restart_rerun_idempotency_and_replay_verification(self) -> None:
        bundle = load_bundle(FIXTURES / "reference_scenario.json")
        fixed_ts = datetime(2026, 3, 31, 17, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "nav.sqlite"

            # First process run
            with SqliteNavStore(db_path) as store1:
                svc1 = NavCalculationService(store1)
                draft1 = svc1.calculate_and_store_draft(bundle, self.config, calculated_at=fixed_ts)
                # Idempotent rerun of exact same draft
                draft1_again = svc1.calculate_and_store_draft(
                    bundle, self.config, calculated_at=fixed_ts
                )
                self.assertEqual(draft1.snapshot_id, draft1_again.snapshot_id)
                frozen1 = svc1.approve_and_freeze(
                    draft1.snapshot_id, approver="ctrl", timestamp=fixed_ts
                )

            # Simulate restart by reopening database from disk
            with SqliteNavStore(db_path) as store2:
                svc2 = NavCalculationService(store2)
                verify = svc2.replay_and_verify(frozen1.snapshot_id)
                self.assertTrue(verify["verified"])
                self.assertEqual(verify["output_hash"], frozen1.output_hash)
                self.assertEqual(verify["lineage_depth"], 1)

    def test_missing_and_invalid_configuration_fails_explicitly(self) -> None:
        with self.assertRaises(ConfigurationError) as ctx:
            load_config(PROJECT_ROOT / "config" / "does_not_exist.json")
        self.assertEqual(ctx.exception.code, "NAV-1001")

        with self.assertRaises(ConfigurationError):
            config_from_dict(
                {
                    "config_version": "1.0.0",
                    "fund_id": "F1",
                    "base_currency": "USD",
                    "currency_precisions": {"USD": 2},
                    "rounding_mode": "ROUND_INVALID",
                }
            )

        with self.assertRaises(ConfigurationError):
            config_from_dict(
                {
                    "config_version": "1.0.0",
                    "fund_id": "F1",
                    "base_currency": "USD",
                    "currency_precisions": {"EUR": 2},
                }
            )

    def test_unsupported_and_invalid_inputs_rejected(self) -> None:
        # Invalid fixture (empty share classes, unsupported currency XYZ, negative price)
        invalid_bundle = load_bundle(FIXTURES / "invalid_inputs.json")
        with self.assertRaises(ValidationError) as ctx:
            calculate_nav(invalid_bundle, self.config)
        self.assertEqual(ctx.exception.code, "NAV-1002")

        # Binary float in JSON rejected to prevent silent precision drift
        with self.assertRaises(ValidationError):
            bundle_from_dict(
                {
                    "bundle_id": "B-FLOAT",
                    "fund_id": "FUND-ALPHA-01",
                    "valuation_date": "2026-03-31",
                    "input_version": "1.0.0",
                    "positions": [
                        {
                            "position_id": "P1",
                            "instrument_id": "EQ1",
                            "instrument_type": "equity",
                            "quantity": 10.5,
                            "cost_basis": "100.00",
                            "currency": "USD",
                        }
                    ],
                    "share_classes": [],
                }
            )

        # Duplicate position IDs rejected
        bundle = load_bundle(FIXTURES / "reference_scenario.json")
        dup = replace(bundle, positions=bundle.positions + (bundle.positions[0],))
        with self.assertRaises(ValidationError):
            calculate_nav(dup, self.config)

    def test_cli_end_to_end_workflow_and_error_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = tmp / "cli_test.sqlite"
            out_v1 = tmp / "v1.json"

            # 1. Calculate & store draft
            rc1 = cli_main(
                [
                    "calculate",
                    "--config",
                    str(CONFIG_PATH),
                    "--bundle",
                    str(FIXTURES / "reference_scenario.json"),
                    "--db",
                    str(db_path),
                    "--output",
                    str(out_v1),
                ]
            )
            self.assertEqual(rc1, 0)
            v1_data = json.loads(out_v1.read_text(encoding="utf-8"))
            snap_id = v1_data["snapshot_id"]

            # 2. Freeze
            rc2 = cli_main(["freeze", "--db", str(db_path), "--snapshot-id", snap_id, "--approver", "ctrl"])
            self.assertEqual(rc2, 0)

            # 3. Restate
            rc3 = cli_main(
                [
                    "restate",
                    "--db",
                    str(db_path),
                    "--prior-snapshot-id",
                    snap_id,
                    "--config",
                    str(CONFIG_PATH),
                    "--bundle",
                    str(FIXTURES / "late_correction_bundle.json"),
                    "--reason",
                    "CLI test restatement",
                ]
            )
            self.assertEqual(rc3, 0)

            # 4. Verify v1
            rc4 = cli_main(["verify", "--db", str(db_path), "--snapshot-id", snap_id])
            self.assertEqual(rc4, 0)

            # 5. Missing config exits cleanly with non-zero code 2
            rc_err = cli_main(
                [
                    "calculate",
                    "--config",
                    str(tmp / "missing.json"),
                    "--bundle",
                    str(FIXTURES / "reference_scenario.json"),
                ]
            )
            self.assertEqual(rc_err, 2)


if __name__ == "__main__":
    unittest.main()
