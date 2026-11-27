"""Orchestrated analytics engine combining returns, risk, benchmark, FX, attribution, and disclosures."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from fund_reporting.domain.enums import DataStatus, EnvironmentMode, MetricFlagCode
from fund_reporting.domain.models import (
    BenchmarkPoint,
    DisclosureBlock,
    FundDefinition,
    FxRateRecord,
    MetricFlag,
    NavSnapshot,
    StrategySnapshot,
)
from fund_reporting.domain.validation import (
    validate_environment_homogeneity,
    validate_nav_series_continuity,
)

from .attribution import compute_exposure_drilldown, compute_strategy_attribution
from .benchmark import compute_benchmark_comparison
from .currency import convert_nav_series
from .returns import compute_daily_twr_series, compute_modified_dietz
from .risk import compute_risk_metrics


@dataclass
class AnalyticsConfig:
    methodology_version: str = "SAAT-PERF-METH-1.0.0"
    trading_days_per_year: int = 252
    risk_free_rate_annual: float = 0.04
    base_reporting_currency: str = "USD"
    reconciliation_tolerance_abs: float = 0.01
    reconciliation_tolerance_rel: float = 1e-6
    minimum_history_observations: Dict[str, int] = field(
        default_factory=lambda: {
            "annualized_return": 20,
            "annualized_volatility": 20,
            "sharpe_ratio": 60,
            "sortino_ratio": 60,
            "calmar_ratio": 60,
            "beta_and_alpha": 30,
            "var_cvar": 30,
        }
    )
    disclosure_templates: Dict[str, str] = field(
        default_factory=lambda: {
            "SIMULATED": "SIMULATED / SYNTHETIC DATA — NOT LIVE OR AUDITED TRACK RECORD.",
            "PAPER": "PAPER / SANDBOX TRADING DATA — NOT LIVE CAPITAL.",
            "LIVE_UNAUDITED": "LIVE UNAUDITED — INTERNAL RECONCILIATION ONLY.",
            "LIVE_AUDITED": "LIVE AUDITED — Reconciled against independent administrator statement.",
        }
    )

    @classmethod
    def from_file(cls, path: Path) -> "AnalyticsConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            methodology_version=str(raw.get("methodology_version", "SAAT-PERF-METH-1.0.0")),
            trading_days_per_year=int(raw.get("trading_days_per_year", 252)),
            risk_free_rate_annual=float(raw.get("risk_free_rate_annual", 0.04)),
            base_reporting_currency=str(raw.get("base_reporting_currency", "USD")).upper(),
            reconciliation_tolerance_abs=float(raw.get("reconciliation_tolerance_abs", 0.01)),
            reconciliation_tolerance_rel=float(raw.get("reconciliation_tolerance_rel", 1e-6)),
            minimum_history_observations=dict(raw.get("minimum_history_observations", {})),
            disclosure_templates=dict(raw.get("disclosure_templates", {})),
        )


class AnalyticsEngine:
    """Stateless, deterministic analytics engine for reconciled fund reporting."""

    def __init__(self, config: Optional[AnalyticsConfig] = None) -> None:
        self.config = config or AnalyticsConfig()

    def build_disclosure(
        self,
        fund: FundDefinition,
        reporting_currency: str,
        *,
        all_reconciled: bool,
    ) -> DisclosureBlock:
        env = fund.environment
        status = fund.data_status
        is_sim_or_paper = env in (EnvironmentMode.SIMULATED, EnvironmentMode.PAPER)

        if env == EnvironmentMode.SIMULATED:
            banner = self.config.disclosure_templates.get(
                "SIMULATED", "SIMULATED / SYNTHETIC DATA — NOT LIVE PERFORMANCE."
            )
        elif env == EnvironmentMode.PAPER:
            banner = self.config.disclosure_templates.get(
                "PAPER", "PAPER / SANDBOX DATA — NOT LIVE CAPITAL."
            )
        elif status == DataStatus.AUDITED:
            banner = self.config.disclosure_templates.get("LIVE_AUDITED", "LIVE AUDITED.")
        else:
            banner = self.config.disclosure_templates.get(
                "LIVE_UNAUDITED", "LIVE UNAUDITED — INTERNAL RECONCILIATION ONLY."
            )

        fs = fund.fee_schedule
        fee_disclosure = (
            f"Net metrics reflect accrued fees: {fs.management_fee_annual_bps:.1f} bps/yr management fee, "
            f"{fs.performance_fee_pct:.1f}% performance fee "
            f"({'with' if fs.high_water_mark_enabled else 'without'} high-water mark, "
            f"hurdle {fs.hurdle_rate_annual * 100:.2f}%/yr). Gross metrics exclude management/performance fees."
        )
        if reporting_currency != fund.base_currency:
            currency_disclosure = (
                f"Fund base currency is {fund.base_currency}; converted to reporting currency "
                f"{reporting_currency} using point-in-time FX rates (unhedged)."
            )
        else:
            currency_disclosure = f"Reported in fund base currency {fund.base_currency}; no FX translation applied."

        audit_disclosure = (
            f"Data status: {status.value}. Ledger reconciliation: "
            f"{'ALL SNAPSHOTS RECONCILED' if all_reconciled else 'CONTAINS UNRECONCILED SNAPSHOTS'}."
        )

        return DisclosureBlock(
            environment=env,
            data_status=status,
            methodology_version=self.config.methodology_version,
            banner=banner,
            is_simulated_or_paper=is_sim_or_paper,
            fee_disclosure=fee_disclosure,
            currency_disclosure=currency_disclosure,
            audit_disclosure=audit_disclosure,
        )

    def analyze(
        self,
        fund: FundDefinition,
        snapshots: Sequence[NavSnapshot],
        *,
        benchmark_points: Sequence[BenchmarkPoint] = (),
        strategy_snapshots: Sequence[StrategySnapshot] = (),
        fx_rates: Sequence[FxRateRecord] = (),
        reporting_currency: Optional[str] = None,
        expected_dates: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Run full reconciled fund analytics and return a structured, serializable payload."""
        target_ccy = (reporting_currency or fund.base_currency).upper()

        # Enforce strict environment homogeneity across fund, NAV snapshots, and strategy snapshots
        envs = [fund.environment] + [s.environment for s in snapshots] + [
            st.environment for st in strategy_snapshots
        ]
        validate_environment_homogeneity(envs, context=f"fund:{fund.fund_id}")

        validated = validate_nav_series_continuity(
            snapshots,
            expected_dates=expected_dates,
            tolerance_abs=self.config.reconciliation_tolerance_abs,
            tolerance_rel=self.config.reconciliation_tolerance_rel,
        )

        converted, fx_flags, fx_used = convert_nav_series(validated, target_ccy, fx_rates)
        daily_points = compute_daily_twr_series(converted)
        md_gross, md_net = compute_modified_dietz(converted)

        dates = [pt.valuation_date for pt in daily_points]
        gross_rets = [pt.gross_return for pt in daily_points]
        net_rets = [pt.net_return for pt in daily_points]

        gross_metrics, gross_flags = compute_risk_metrics(
            dates,
            gross_rets,
            trading_days_per_year=self.config.trading_days_per_year,
            risk_free_rate_annual=self.config.risk_free_rate_annual,
            min_history=self.config.minimum_history_observations,
            basis_label="GROSS",
        )
        net_metrics, net_flags = compute_risk_metrics(
            dates,
            net_rets,
            trading_days_per_year=self.config.trading_days_per_year,
            risk_free_rate_annual=self.config.risk_free_rate_annual,
            min_history=self.config.minimum_history_observations,
            basis_label="NET",
        )
        gross_metrics["modified_dietz_return"] = md_gross
        net_metrics["modified_dietz_return"] = md_net

        benchmark_data, bm_flags = compute_benchmark_comparison(
            dates,
            net_rets,
            benchmark_points,
            reporting_currency=target_ccy,
            fx_rates=fx_rates,
            trading_days_per_year=self.config.trading_days_per_year,
            risk_free_rate_annual=self.config.risk_free_rate_annual,
            min_observations=self.config.minimum_history_observations.get("beta_and_alpha", 30),
        )

        attribution = compute_strategy_attribution(
            daily_points,
            strategy_snapshots,
            reporting_currency=target_ccy,
            fx_rates=fx_rates,
        )
        exposure = compute_exposure_drilldown(
            converted,
            strategy_snapshots,
            reporting_currency=target_ccy,
            fx_rates=fx_rates,
        )

        all_reconciled = all(s.ledger_reconciled for s in validated)
        flags: List[MetricFlag] = []
        if fund.environment == EnvironmentMode.SIMULATED:
            flags.append(
                MetricFlag(
                    code=MetricFlagCode.SIMULATED_DATA,
                    metric="fund.environment",
                    message="Underlying fund series is SIMULATED / SYNTHETIC — not live performance.",
                    severity="WARNING",
                )
            )
        elif fund.environment == EnvironmentMode.PAPER:
            flags.append(
                MetricFlag(
                    code=MetricFlagCode.PAPER_DATA,
                    metric="fund.environment",
                    message="Underlying fund series is PAPER / SANDBOX — not live capital.",
                    severity="WARNING",
                )
            )
        if fund.data_status != DataStatus.AUDITED:
            flags.append(
                MetricFlag(
                    code=MetricFlagCode.UNAUDITED_DATA,
                    metric="fund.data_status",
                    message=f"Data status is {fund.data_status.value}; not an audited investor statement.",
                    severity="WARNING",
                )
            )
        if not all_reconciled:
            flags.append(
                MetricFlag(
                    code=MetricFlagCode.UNRECONCILED_LEDGER,
                    metric="ledger.reconciliation",
                    message="One or more snapshots are marked unreconciled.",
                    severity="ERROR",
                )
            )
        flags.extend(fx_flags)
        flags.extend(gross_flags)
        flags.extend(net_flags)
        flags.extend(bm_flags)

        disclosure = self.build_disclosure(fund, target_ccy, all_reconciled=all_reconciled)

        total_subs = sum(s.subscriptions for s in converted)
        total_reds = sum(s.redemptions for s in converted)
        total_gross_pnl = sum(s.gross_pnl for s in converted)
        total_fees = sum(s.total_fees for s in converted)
        total_net_pnl = sum(s.net_pnl for s in converted)

        return {
            "fund": fund.to_dict(),
            "reporting_currency": target_ccy,
            "period_start": dates[0],
            "period_end": dates[-1],
            "as_of_utc": validated[-1].as_of_utc,
            "availability_utc": validated[-1].availability_utc,
            "methodology_version": self.config.methodology_version,
            "disclosure": disclosure.to_dict(),
            "reconciliation_summary": {
                "all_snapshots_reconciled": all_reconciled,
                "snapshot_count": len(validated),
                "opening_nav": round(converted[0].opening_nav, 4),
                "closing_nav": round(converted[-1].closing_nav, 4),
                "total_subscriptions": round(total_subs, 4),
                "total_redemptions": round(total_reds, 4),
                "net_cash_flows": round(total_subs - total_reds, 4),
                "total_gross_pnl": round(total_gross_pnl, 4),
                "total_fees": round(total_fees, 4),
                "total_net_pnl": round(total_net_pnl, 4),
                "nav_bridge_residual": round(
                    converted[-1].closing_nav
                    - (converted[0].opening_nav + (total_subs - total_reds) + total_net_pnl),
                    6,
                ),
                "latest_ledger_hash": validated[-1].ledger_hash,
                "fx_rates_applied": fx_used,
            },
            "metrics": {
                "gross": gross_metrics,
                "net": net_metrics,
                "fee_drag_cumulative": gross_metrics["cumulative_return"] - net_metrics["cumulative_return"],
            },
            "benchmark": benchmark_data,
            "attribution": attribution,
            "exposure": exposure,
            "daily_series": [pt.to_dict() for pt in daily_points],
            "flags": [f.to_dict() for f in flags],
        }
