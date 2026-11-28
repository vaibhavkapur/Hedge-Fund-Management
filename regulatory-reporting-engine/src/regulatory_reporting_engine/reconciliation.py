"""Cross-report totals reconciliation and internal schedule consistency checks."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from regulatory_reporting_engine.errors import CrossReportReconciliationError
from regulatory_reporting_engine.models import ReportArtifact


@dataclass(frozen=True)
class ReconciliationCheckResult:
    check_name: str
    left_label: str
    right_label: str
    left_value: Decimal
    right_value: Decimal
    difference: Decimal
    tolerance: Decimal
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_name": self.check_name,
            "left_label": self.left_label,
            "right_label": self.right_label,
            "left_value": str(self.left_value),
            "right_value": str(self.right_value),
            "difference": str(self.difference),
            "tolerance": str(self.tolerance),
            "passed": self.passed,
        }


@dataclass(frozen=True)
class ReconciliationReport:
    passed: bool
    checks: Tuple[ReconciliationCheckResult, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "total_checks": len(self.checks),
            "failed_checks": sum(1 for c in self.checks if not c.passed),
            "checks": [c.to_dict() for c in self.checks],
        }


def _get_gross_exposure(report: ReportArtifact) -> Tuple[str, Decimal]:
    if "gross_exposure_usd" in report.fields:
        return "gross_exposure_usd", Decimal(str(report.fields["gross_exposure_usd"]))
    if "gross_notional_exposure_usd" in report.fields:
        return "gross_notional_exposure_usd", Decimal(str(report.fields["gross_notional_exposure_usd"]))
    if "aum_gross_method_base" in report.fields:
        return "aum_gross_method_base", Decimal(str(report.fields["aum_gross_method_base"]))
    raise KeyError(f"No gross exposure field found in report {report.report_id}")


def _get_nav(report: ReportArtifact) -> Tuple[str, Decimal]:
    if "nav_total_usd" in report.fields:
        return "nav_total_usd", Decimal(str(report.fields["nav_total_usd"]))
    if "aif_nav_base" in report.fields:
        return "aif_nav_base", Decimal(str(report.fields["aif_nav_base"]))
    raise KeyError(f"No NAV field found in report {report.report_id}")


def _get_net_exposure(report: ReportArtifact) -> Tuple[str, Decimal]:
    if "net_exposure_usd" in report.fields:
        return "net_exposure_usd", Decimal(str(report.fields["net_exposure_usd"]))
    if "aum_commitment_method_base" in report.fields:
        return "aum_commitment_method_base", Decimal(str(report.fields["aum_commitment_method_base"]))
    raise KeyError(f"No net exposure field found in report {report.report_id}")


def _make_check(
    check_name: str,
    left_label: str,
    left_val: Decimal,
    right_label: str,
    right_val: Decimal,
    tolerance: Decimal,
) -> ReconciliationCheckResult:
    diff = abs(left_val - right_val)
    return ReconciliationCheckResult(
        check_name=check_name,
        left_label=left_label,
        right_label=right_label,
        left_value=left_val,
        right_value=right_val,
        difference=diff,
        tolerance=tolerance,
        passed=diff <= tolerance,
    )


def reconcile_reports(
    left: ReportArtifact,
    right: ReportArtifact,
    *,
    monetary_tolerance: Decimal = Decimal("0.01"),
    ratio_tolerance: Decimal = Decimal("0.0001"),
    fail_on_mismatch: bool = True,
) -> ReconciliationReport:
    """
    Reconcile corresponding regulatory totals across two reports (e.g., US Form PF-style vs EU AIFMD-style)
    and validate internal schedule additivity.
    """
    checks: List[ReconciliationCheckResult] = []

    # 1. Cross-report NAV reconciliation
    l_nav_field, l_nav = _get_nav(left)
    r_nav_field, r_nav = _get_nav(right)
    checks.append(
        _make_check(
            "cross_report_nav_total",
            f"{left.profile_id}:{left.schema_version}.{l_nav_field}",
            l_nav,
            f"{right.profile_id}:{right.schema_version}.{r_nav_field}",
            r_nav,
            monetary_tolerance,
        )
    )

    # 2. Cross-report Gross Exposure reconciliation
    l_gross_field, l_gross = _get_gross_exposure(left)
    r_gross_field, r_gross = _get_gross_exposure(right)
    checks.append(
        _make_check(
            "cross_report_gross_exposure_total",
            f"{left.profile_id}:{left.schema_version}.{l_gross_field}",
            l_gross,
            f"{right.profile_id}:{right.schema_version}.{r_gross_field}",
            r_gross,
            monetary_tolerance,
        )
    )

    # 3. Cross-report Net Exposure reconciliation
    l_net_field, l_net = _get_net_exposure(left)
    r_net_field, r_net = _get_net_exposure(right)
    checks.append(
        _make_check(
            "cross_report_net_exposure_total",
            f"{left.profile_id}:{left.schema_version}.{l_net_field}",
            l_net,
            f"{right.profile_id}:{right.schema_version}.{r_net_field}",
            r_net,
            monetary_tolerance,
        )
    )

    # 4. Cross-report Position Count reconciliation
    if "position_count" in left.fields and "position_count" in right.fields:
        checks.append(
            _make_check(
                "cross_report_position_count",
                f"{left.profile_id}:{left.schema_version}.position_count",
                Decimal(str(left.fields["position_count"])),
                f"{right.profile_id}:{right.schema_version}.position_count",
                Decimal(str(right.fields["position_count"])),
                Decimal("0"),
            )
        )

    # 5. Cross-report Leverage ratio equivalence (ratio vs percent / 100)
    if "gross_leverage_ratio" in left.fields and "leverage_gross_pct" in right.fields:
        checks.append(
            _make_check(
                "cross_report_leverage_equivalence",
                f"{left.profile_id}:{left.schema_version}.gross_leverage_ratio",
                Decimal(str(left.fields["gross_leverage_ratio"])),
                f"{right.profile_id}:{right.schema_version}.leverage_gross_pct / 100",
                Decimal(str(right.fields["leverage_gross_pct"])) / Decimal("100"),
                ratio_tolerance,
            )
        )
    elif "leverage_gross_pct" in left.fields and "gross_leverage_ratio" in right.fields:
        checks.append(
            _make_check(
                "cross_report_leverage_equivalence",
                f"{left.profile_id}:{left.schema_version}.leverage_gross_pct / 100",
                Decimal(str(left.fields["leverage_gross_pct"])) / Decimal("100"),
                f"{right.profile_id}:{right.schema_version}.gross_leverage_ratio",
                Decimal(str(right.fields["gross_leverage_ratio"])),
                ratio_tolerance,
            )
        )

    # 6. Internal schedule additivity for EU AIFMD asset-class buckets if present
    for rpt in (left, right):
        if all(
            k in rpt.fields
            for k in (
                "aum_gross_method_base",
                "equity_exposure_gross_base",
                "fixed_income_exposure_gross_base",
                "derivatives_exposure_gross_base",
            )
        ):
            bucket_sum = (
                Decimal(str(rpt.fields["equity_exposure_gross_base"]))
                + Decimal(str(rpt.fields["fixed_income_exposure_gross_base"]))
                + Decimal(str(rpt.fields["derivatives_exposure_gross_base"]))
            )
            checks.append(
                _make_check(
                    f"internal_asset_class_sum_{rpt.profile_id}",
                    f"{rpt.profile_id}.aum_gross_method_base",
                    Decimal(str(rpt.fields["aum_gross_method_base"])),
                    f"{rpt.profile_id}.sum(asset_class_buckets)",
                    bucket_sum,
                    monetary_tolerance,
                )
            )

    all_passed = all(c.passed for c in checks)
    result = ReconciliationReport(passed=all_passed, checks=tuple(checks))

    if not all_passed and fail_on_mismatch:
        failed = [c.to_dict() for c in checks if not c.passed]
        raise CrossReportReconciliationError(
            f"Cross-report totals reconciliation failed ({len(failed)} mismatch(es)) between "
            f"'{left.report_id}' and '{right.report_id}'.",
            details={
                "left_report_id": left.report_id,
                "right_report_id": right.report_id,
                "mismatches": failed,
            },
        )
    return result
