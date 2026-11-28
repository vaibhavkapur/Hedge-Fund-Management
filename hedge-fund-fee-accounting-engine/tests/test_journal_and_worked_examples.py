"""Independent worked-example oracle comparisons, journal reconciliation, and CLI tests."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from hedge_fund_fee_engine.adapters import RegulatoryLedgerExportAdapter
from hedge_fund_fee_engine.models import (
    EngineConfig,
    FeeAgreement,
    InvestorLot,
    NavSnapshot,
)
from hedge_fund_fee_engine.reporting import compare_calculation_against_expected, export_journal_csv
from hedge_fund_fee_engine.service import FeeAccountingService
from hedge_fund_fee_engine.storage import SqliteFeeStore


FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "default_engine_config.json"


class TestJournalAndWorkedExamples(unittest.TestCase):
    def setUp(self) -> None:
        self.config = EngineConfig.from_json_file(DEFAULT_CONFIG)

    def test_all_independent_worked_examples_match_zero_tolerance(self) -> None:
        suite = json.loads((FIXTURES_DIR / "independent_worked_examples.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(suite["examples"]), 5)

        for ex in suite["examples"]:
            with self.subTest(example=ex["scenario_id"]):
                store = SqliteFeeStore(Path(":memory:"))
                service = FeeAccountingService(config=self.config, store=store)
                agreement = FeeAgreement.from_dict(ex["agreement"])
                lots = tuple(InvestorLot.from_dict(d) for d in ex["initial_lots"])
                runs = []
                for period in ex["periods"]:
                    snapshot = NavSnapshot.from_dict(period["snapshot"])
                    res = service.run_period(agreement=agreement, snapshot=snapshot, lots=lots)
                    runs.append(res)
                    lots = res.updated_lots

                    # Exact zero-tolerance comparison
                    comp = compare_calculation_against_expected(
                        actual=res,
                        expected_lots=period["expected"],
                        tolerance=Decimal("0.00"),
                    )
                    self.assertTrue(comp["passed"], msg=comp["discrepancies"])

                    # Every journal entry strictly balanced
                    for entry in res.journal_entries:
                        self.assertEqual(entry.total_debits, entry.total_credits)
                        self.assertGreater(entry.total_debits, Decimal("0"))
                    self.assertTrue(res.reconciliation.reconciled)

                # Regulatory adapter export sanity check
                export = RegulatoryLedgerExportAdapter.to_regulatory_fee_schedule(runs)
                self.assertEqual(export["row_count"], len(runs))
                store.close()

    def test_reproducibility_identical_runs_produce_identical_hashes(self) -> None:
        ref = json.loads((FIXTURES_DIR / "reference_basic_2_and_20.json").read_text(encoding="utf-8"))
        agr = FeeAgreement.from_dict(ref["agreement"])
        snap = NavSnapshot.from_dict(ref["periods"][0]["snapshot"])
        lots = [InvestorLot.from_dict(d) for d in ref["initial_lots"]]

        svc1 = FeeAccountingService(config=self.config)
        svc2 = FeeAccountingService(config=self.config)
        r1 = svc1.run_period(agreement=agr, snapshot=snap, lots=lots, persist=False)
        r2 = svc2.run_period(agreement=agr, snapshot=snap, lots=lots, persist=False)

        self.assertEqual(r1.run_id, r2.run_id)
        self.assertEqual(r1.input_hash, r2.input_hash)
        self.assertEqual(r1.output_hash, r2.output_hash)

    def test_cli_run_fixture_and_replay_and_csv_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = tmp / "cli_ledger.sqlite"
            out_path = tmp / "cli_out.json"
            env_py = str(PROJECT_ROOT / "src")

            cmd = [
                sys.executable,
                "-m",
                "hedge_fund_fee_engine",
                "--config",
                str(DEFAULT_CONFIG),
                "--db",
                str(db_path),
                "run-fixture",
                str(FIXTURES_DIR / "reference_basic_2_and_20.json"),
                "--out",
                str(out_path),
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                env={"PYTHONPATH": env_py},
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
            summary = json.loads(proc.stdout)
            self.assertTrue(summary["all_passed"])
            self.assertTrue(out_path.exists())

            # Replay via CLI
            report_data = json.loads(out_path.read_text(encoding="utf-8"))
            run_id = report_data["runs"][0]["run_id"]
            replay_proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "hedge_fund_fee_engine",
                    "--config",
                    str(DEFAULT_CONFIG),
                    "--db",
                    str(db_path),
                    "replay",
                    run_id,
                ],
                capture_output=True,
                text=True,
                env={"PYTHONPATH": env_py},
                check=False,
            )
            self.assertEqual(replay_proc.returncode, 0, msg=replay_proc.stderr + replay_proc.stdout)
            self.assertTrue(json.loads(replay_proc.stdout)["matches"])

            # CSV export check
            svc = FeeAccountingService(config=self.config)
            ref = json.loads((FIXTURES_DIR / "reference_basic_2_and_20.json").read_text(encoding="utf-8"))
            res = svc.run_period(
                agreement=FeeAgreement.from_dict(ref["agreement"]),
                snapshot=NavSnapshot.from_dict(ref["periods"][0]["snapshot"]),
                lots=[InvestorLot.from_dict(d) for d in ref["initial_lots"]],
                persist=False,
            )
            csv_path = tmp / "journals.csv"
            export_journal_csv(csv_path, [res])
            lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertGreaterEqual(len(lines), 5)  # header + 6 journal lines (mgmt, perf accrual, cryst)


if __name__ == "__main__":
    unittest.main()
