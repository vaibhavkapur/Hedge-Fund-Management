"""Reporting, fixture execution, and worked-example oracle comparison utilities."""

from __future__ import annotations

import csv
from decimal import Decimal
import json
from pathlib import Path
import platform
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .models import (
    CalculationRunResult,
    CapitalFlow,
    EngineConfig,
    FeeAgreement,
    FeeCorrection,
    InvestorLot,
    NavSnapshot,
    to_date,
    to_decimal,
)
from .service import FeeAccountingService


def collect_runtime_metadata(config: EngineConfig) -> Dict[str, Any]:
    """Capture deterministic environment and configuration metadata for reports."""
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "config_version": config.config_version,
        "schema_version": config.schema_version,
        "rounding_mode": config.monetary_rounding_mode,
        "intermediate_precision": config.intermediate_precision,
        "oracle_comparison_tolerance": format(config.oracle_comparison_tolerance, "f"),
    }


def write_json_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def export_journal_csv(path: Path, runs: Sequence[CalculationRunResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_id",
        "version",
        "entry_id",
        "posting_date",
        "valuation_date",
        "entry_type",
        "supersedes_entry_id",
        "account_code",
        "account_name",
        "debit",
        "credit",
        "currency",
        "share_class",
        "lot_id",
        "memo",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for run in runs:
            for entry in run.journal_entries:
                for line in entry.lines:
                    writer.writerow(
                        {
                            "run_id": run.run_id,
                            "version": entry.version,
                            "entry_id": entry.entry_id,
                            "posting_date": entry.posting_date.isoformat(),
                            "valuation_date": entry.valuation_date.isoformat(),
                            "entry_type": entry.entry_type.value,
                            "supersedes_entry_id": entry.supersedes_entry_id or "",
                            "account_code": line.account_code,
                            "account_name": line.account_name,
                            "debit": format(line.debit, "f"),
                            "credit": format(line.credit, "f"),
                            "currency": line.currency,
                            "share_class": line.share_class,
                            "lot_id": line.lot_id,
                            "memo": line.memo,
                        }
                    )


def compare_calculation_against_expected(
    *,
    actual: CalculationRunResult,
    expected_lots: Mapping[str, Mapping[str, Any]],
    tolerance: Decimal = Decimal("0.00"),
) -> Dict[str, Any]:
    """Compare lot calculations against hand-calculated expected oracle values."""
    actual_by_lot = {c.lot_id: c for c in actual.lot_calculations}
    discrepancies: List[Dict[str, Any]] = []
    comparisons: List[Dict[str, Any]] = []

    for lot_id, exp in expected_lots.items():
        if lot_id not in actual_by_lot:
            discrepancies.append(
                {"lot_id": lot_id, "field": "lot_id", "expected": lot_id, "actual": None, "diff": None}
            )
            continue
        calc = actual_by_lot[lot_id]
        calc_dict = calc.to_dict()
        for field_name, exp_val in exp.items():
            act_val = calc_dict.get(field_name)
            if isinstance(exp_val, bool):
                matched = act_val == exp_val
                diff_str = "0" if matched else "BOOL_MISMATCH"
            else:
                exp_dec = to_decimal(exp_val, field_name=field_name)
                act_dec = to_decimal(act_val, field_name=field_name)
                diff = abs(act_dec - exp_dec)
                matched = diff <= tolerance
                diff_str = format(diff, "f")
            entry = {
                "lot_id": lot_id,
                "field": field_name,
                "expected": str(exp_val),
                "actual": str(act_val),
                "abs_diff": diff_str,
                "matched": matched,
            }
            comparisons.append(entry)
            if not matched:
                discrepancies.append(entry)

    return {
        "run_id": actual.run_id,
        "valuation_date": actual.valuation_date.isoformat(),
        "tolerance": format(tolerance, "f"),
        "passed": len(discrepancies) == 0 and actual.reconciliation.reconciled,
        "checks_evaluated": len(comparisons),
        "discrepancy_count": len(discrepancies),
        "discrepancies": discrepancies,
        "comparisons": comparisons,
    }


def run_scenario_fixture(
    fixture_path: Path,
    service: FeeAccountingService,
    *,
    persist: bool = True,
) -> Dict[str, Any]:
    """Execute a multi-period JSON scenario fixture (including optional flows & corrections)."""
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    agreement = FeeAgreement.from_dict(raw["agreement"])
    lots = tuple(InvestorLot.from_dict(item) for item in raw["initial_lots"])
    tolerance = service.config.oracle_comparison_tolerance

    if persist:
        service.register_agreement(agreement)

    runs: List[CalculationRunResult] = []
    period_comparisons: List[Dict[str, Any]] = []
    run_ids_by_snapshot: Dict[str, str] = {}

    for period in raw["periods"]:
        snapshot = NavSnapshot.from_dict(period["snapshot"])
        flows = tuple(CapitalFlow.from_dict(f) for f in period.get("capital_flows", []))
        result = service.run_period(
            agreement=agreement,
            snapshot=snapshot,
            lots=lots,
            capital_flows=flows,
            persist=persist,
        )
        runs.append(result)
        run_ids_by_snapshot[snapshot.snapshot_id] = result.run_id
        lots = result.updated_lots

        if "expected" in period:
            comp = compare_calculation_against_expected(
                actual=result,
                expected_lots=period["expected"],
                tolerance=tolerance,
            )
            period_comparisons.append(comp)

    correction_results: List[Dict[str, Any]] = []
    for corr_spec in raw.get("corrections", []):
        target_run_id = run_ids_by_snapshot[corr_spec["target_snapshot_id"]]
        corrected_snapshot = NavSnapshot.from_dict(corr_spec["corrected_snapshot"])
        correction = FeeCorrection(
            correction_id=str(corr_spec["correction_id"]),
            target_run_id=target_run_id,
            effective_date=to_date(corr_spec["effective_date"], field_name="effective_date"),
            reason=str(corr_spec["reason"]),
            corrected_snapshot=corrected_snapshot,
            version=int(corr_spec["version"]),
        )
        corr_run = service.apply_correction(correction=correction)
        runs.append(corr_run)
        corr_comp: Optional[Dict[str, Any]] = None
        if "expected" in corr_spec:
            corr_comp = compare_calculation_against_expected(
                actual=corr_run,
                expected_lots=corr_spec["expected"],
                tolerance=tolerance,
            )
            period_comparisons.append(corr_comp)
        correction_results.append(
            {
                "correction_id": correction.correction_id,
                "target_run_id": target_run_id,
                "new_run_id": corr_run.run_id,
                "comparison": corr_comp,
            }
        )

    all_passed = all(c["passed"] for c in period_comparisons) and all(r.reconciliation.reconciled for r in runs)
    return {
        "scenario_id": raw["scenario_id"],
        "description": raw.get("description", ""),
        "fixture_file": fixture_path.name,
        "all_passed": all_passed,
        "run_count": len(runs),
        "runs": [r.to_dict() for r in runs],
        "comparisons": period_comparisons,
        "corrections": correction_results,
    }
