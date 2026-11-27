"""Deterministic synthetic fixture generator (fixed seed, zero external data dependencies).

All generated records are explicitly labeled environment='SIMULATED' and data_status='SYNTHETIC'.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from fund_reporting.domain.enums import (
    CashFlowTiming,
    CashFlowType,
    DataStatus,
    EnvironmentMode,
)
from fund_reporting.domain.models import (
    BenchmarkPoint,
    CashFlowRecord,
    FeeSchedule,
    FundDefinition,
    FxRateRecord,
    NavSnapshot,
    StrategySnapshot,
)
from fund_reporting.domain.validation import compute_ledger_hash


def _trading_days(start: date, count: int) -> List[date]:
    days: List[date] = []
    curr = start
    while len(days) < count:
        if curr.weekday() < 5:
            days.append(curr)
        curr += timedelta(days=1)
    return days


def generate_fund_alpha_bundle(seed: int = 20260101, n_days: int = 90) -> Dict[str, Any]:
    """Generate 90 trading days of reconciled synthetic multi-strategy fund data."""
    rng = random.Random(seed)
    fund_id = "FUND-SAAT-SIM-01"
    benchmark_id = "BM-SYNTH-6040-USD"
    days = _trading_days(date(2026, 1, 2), n_days)

    fee_schedule = FeeSchedule(
        management_fee_annual_bps=150.0,
        performance_fee_pct=20.0,
        hurdle_rate_annual=0.04,
        high_water_mark_enabled=True,
        accrual_basis_days=252,
    )
    fund = FundDefinition(
        fund_id=fund_id,
        name="SAAT Multi-Strategy Research Fund (Synthetic)",
        base_currency="USD",
        environment=EnvironmentMode.SIMULATED,
        data_status=DataStatus.SYNTHETIC,
        inception_date=days[0].isoformat(),
        benchmark_id=benchmark_id,
        fee_schedule=fee_schedule,
        created_at_utc="2026-01-02T00:00:00Z",
    )

    strategies_meta = [
        ("STRAT-EQ-STATARB", "Equity StatArb", "Equities", 0.38, 0.00045, 0.0065, 0.72, 0.58),
        ("STRAT-FUT-TREND", "Systematic Macro Trend", "Futures", 0.30, 0.00035, 0.0080, 0.55, 0.30),
        ("STRAT-VOL-CARRY", "Cross-Asset Volatility Carry", "Options", 0.18, 0.00028, 0.0055, 0.32, 0.24),
        ("STRAT-FX-RELVAL", "G10 FX Relative Value", "FX", 0.14, 0.00020, 0.0040, 0.25, 0.20),
    ]

    nav_snapshots: List[Dict[str, Any]] = []
    cash_flows: List[Dict[str, Any]] = []
    strategy_snapshots: List[Dict[str, Any]] = []
    benchmark_points: List[Dict[str, Any]] = []
    fx_rates: List[Dict[str, Any]] = []

    # Prior day FX anchor (2026-01-01) so day 0 FX translation has exact prior close
    eur_usd = 0.9200  # 1 USD = 0.92 EUR
    gbp_usd = 0.7900  # 1 USD = 0.79 GBP
    fx_rates.append(
        FxRateRecord(
            rate_id="FX-2026-01-01-USDEUR",
            valuation_date="2026-01-01",
            as_of_utc="2026-01-01T21:00:00Z",
            base_currency="USD",
            quote_currency="EUR",
            rate=eur_usd,
            source="SYNTHETIC_FX_FEED_SEED_20260101",
        ).to_dict()
    )
    fx_rates.append(
        FxRateRecord(
            rate_id="FX-2026-01-01-USDGBP",
            valuation_date="2026-01-01",
            as_of_utc="2026-01-01T21:00:00Z",
            base_currency="USD",
            quote_currency="GBP",
            rate=gbp_usd,
            source="SYNTHETIC_FX_FEED_SEED_20260101",
        ).to_dict()
    )

    nav = 25_000_000.0
    shares = 250_000.0
    hwm_nav_per_share = nav / shares
    bm_level = 100.0

    # Scheduled realistic capital flows to exercise TWR invariance
    scheduled_flows = {
        15: (CashFlowType.SUBSCRIPTION, 5_000_000.0),
        42: (CashFlowType.REDEMPTION, 2_500_000.0),
        68: (CashFlowType.SUBSCRIPTION, 3_000_000.0),
    }

    for idx, dt in enumerate(days):
        dt_str = dt.isoformat()
        as_of_utc = f"{dt_str}T21:00:00Z"
        avail_utc = f"{dt_str}T22:15:00Z"
        snap_id = f"SNAP-{fund_id}-{dt_str}-r1"

        opening_nav = nav
        nav_per_share_open = opening_nav / shares

        subs = 0.0
        reds = 0.0
        if idx in scheduled_flows:
            ftype, amt = scheduled_flows[idx]
            if ftype == CashFlowType.SUBSCRIPTION:
                subs = amt
                shares += amt / nav_per_share_open
                signed_amt = amt
            else:
                reds = amt
                shares -= amt / nav_per_share_open
                signed_amt = -amt
            cash_flows.append(
                CashFlowRecord(
                    flow_id=f"CF-{fund_id}-{dt_str}",
                    fund_id=fund_id,
                    snapshot_id=snap_id,
                    valuation_date=dt_str,
                    as_of_utc=f"{dt_str}T13:30:00Z",
                    flow_type=ftype,
                    amount=signed_amt,
                    currency="USD",
                    timing=CashFlowTiming.START_OF_DAY,
                    environment=EnvironmentMode.SIMULATED,
                    investor_class="CLASS_A",
                ).to_dict()
            )

        effective_cap = opening_nav + subs - reds

        # Inject a controlled drawdown regime during days 25..36 for realistic drawdown testing
        regime_shock = -0.0045 if 25 <= idx <= 36 else 0.0
        market_factor = rng.gauss(0.00025 + regime_shock * 0.6, 0.0072)

        total_gross_pnl = 0.0
        total_long = 0.0
        total_short = 0.0

        for sid, sname, ac, base_w, drift, vol, long_mult, short_mult in strategies_meta:
            s_ret = drift + regime_shock * 0.7 + 0.35 * market_factor + rng.gauss(0.0, vol)
            s_alloc = effective_cap * base_w
            s_pnl = round(s_alloc * s_ret, 2)
            s_tca = round(abs(s_alloc) * rng.uniform(0.00003, 0.00009), 2)
            s_long = round(s_alloc * long_mult, 2)
            s_short = round(s_alloc * short_mult, 2)
            s_cash = round(s_alloc * 0.14, 2)

            total_gross_pnl += s_pnl
            total_long += s_long
            total_short += s_short

            strategy_snapshots.append(
                StrategySnapshot(
                    strategy_snapshot_id=f"SS-{dt_str}-{sid}",
                    snapshot_id=snap_id,
                    fund_id=fund_id,
                    valuation_date=dt_str,
                    strategy_id=sid,
                    strategy_name=sname,
                    asset_class=ac,
                    currency="USD",
                    environment=EnvironmentMode.SIMULATED,
                    weight=base_w,
                    gross_pnl=s_pnl,
                    transaction_costs=s_tca,
                    long_exposure=s_long,
                    short_exposure=s_short,
                    cash_allocation=s_cash,
                ).to_dict()
            )

        total_gross_pnl = round(total_gross_pnl, 2)
        mgmt_fee = round(effective_cap * (fee_schedule.management_fee_annual_bps / 10000.0) / 252.0, 2)
        other_exp = round(effective_cap * 0.000008, 2)

        pre_perf_nav = effective_cap + total_gross_pnl - mgmt_fee - other_exp
        pre_perf_nps = pre_perf_nav / shares
        hurdle_nps = hwm_nav_per_share * (1.0 + fee_schedule.hurdle_rate_annual / 252.0)
        if pre_perf_nps > hurdle_nps:
            perf_fee = round((pre_perf_nps - hurdle_nps) * shares * (fee_schedule.performance_fee_pct / 100.0), 2)
        else:
            perf_fee = 0.0

        closing_nav = round(effective_cap + total_gross_pnl - mgmt_fee - perf_fee - other_exp, 2)
        closing_nps = closing_nav / shares
        if closing_nps > hwm_nav_per_share:
            hwm_nav_per_share = closing_nps

        cash_bal = round(closing_nav * 0.14, 2)
        gross_exp = round(total_long + total_short, 2)
        net_exp = round(total_long - total_short, 2)

        snap = NavSnapshot(
            snapshot_id=snap_id,
            fund_id=fund_id,
            valuation_date=dt_str,
            as_of_utc=as_of_utc,
            availability_utc=avail_utc,
            revision=1,
            currency="USD",
            environment=EnvironmentMode.SIMULATED,
            data_status=DataStatus.SYNTHETIC,
            opening_nav=round(opening_nav, 2),
            gross_pnl=total_gross_pnl,
            management_fee=mgmt_fee,
            performance_fee=perf_fee,
            other_expenses=other_exp,
            subscriptions=round(subs, 2),
            redemptions=round(reds, 2),
            closing_nav=closing_nav,
            shares_outstanding=round(shares, 6),
            cash_balance=cash_bal,
            gross_exposure=gross_exp,
            net_exposure=net_exp,
            ledger_reconciled=True,
            ledger_hash="pending",
            provenance_source="SYNTHETIC_LEDGER_GENERATOR_SEED_20260101",
            cash_flow_timing=CashFlowTiming.START_OF_DAY,
        )
        snap_dict = snap.to_dict()
        snap_dict["ledger_hash"] = compute_ledger_hash(snap)
        nav_snapshots.append(snap_dict)
        nav = closing_nav

        # Benchmark point
        bm_ret = round(market_factor + rng.gauss(0.0001, 0.002), 8)
        bm_level = round(bm_level * (1.0 + bm_ret), 6)
        benchmark_points.append(
            BenchmarkPoint(
                benchmark_id=benchmark_id,
                valuation_date=dt_str,
                currency="USD",
                daily_return=bm_ret,
                level=bm_level,
                source="SYNTHETIC_BENCHMARK_SEED_20260101",
            ).to_dict()
        )

        # FX evolution
        eur_usd = round(max(0.75, min(1.15, eur_usd * (1.0 + rng.gauss(0.0001, 0.0035)))), 6)
        gbp_usd = round(max(0.65, min(0.95, gbp_usd * (1.0 + rng.gauss(0.00005, 0.0030)))), 6)
        fx_rates.append(
            FxRateRecord(
                rate_id=f"FX-{dt_str}-USDEUR",
                valuation_date=dt_str,
                as_of_utc=as_of_utc,
                base_currency="USD",
                quote_currency="EUR",
                rate=eur_usd,
                source="SYNTHETIC_FX_FEED_SEED_20260101",
            ).to_dict()
        )
        fx_rates.append(
            FxRateRecord(
                rate_id=f"FX-{dt_str}-USDGBP",
                valuation_date=dt_str,
                as_of_utc=as_of_utc,
                base_currency="USD",
                quote_currency="GBP",
                rate=gbp_usd,
                source="SYNTHETIC_FX_FEED_SEED_20260101",
            ).to_dict()
        )

    return {
        "schema_version": "1.0.0",
        "seed": seed,
        "provenance": "SYNTHETIC — Generated deterministically for testing and demo; NOT live performance.",
        "fund": fund.to_dict(),
        "nav_snapshots": nav_snapshots,
        "cash_flows": cash_flows,
        "strategy_snapshots": strategy_snapshots,
        "benchmark_points": benchmark_points,
        "fx_rates": fx_rates,
    }


def generate_hand_calculated_case() -> Dict[str, Any]:
    """Generate a 4-day exact hand-calculated dataset with known analytical ground truth.

    Hand derivation (START_OF_DAY cash flows, USD base, EUR conversion):
      Day 1 (2026-02-02): open=1000, cf=0, gross_pnl=+100, fees=10 -> net_pnl=+90, close=1090
          r_gross_1 = 100/1000 = +0.100000, r_net_1 = 90/1000 = +0.090000
      Day 2 (2026-02-03): open=1090, sub=+410 (eff=1500), gross_pnl=-150, fees=0 -> close=1350
          r_gross_2 = -150/1500 = -0.100000, r_net_2 = -150/1500 = -0.100000
      Day 3 (2026-02-04): open=1350, red=350 (eff=1000), gross_pnl=+50, fees=5 -> close=1045
          r_gross_3 = 50/1000 = +0.050000, r_net_3 = 45/1000 = +0.045000
      Day 4 (2026-02-05): open=1045, cf=0, gross_pnl=+104.5, fees=0 -> close=1149.5
          r_gross_4 = 104.5/1045 = +0.100000, r_net_4 = +0.100000

      Cumulative Gross TWR = (1.10)*(0.90)*(1.05)*(1.10) - 1 = 1.14345 - 1 = +0.14345
      Cumulative Net TWR   = (1.09)*(0.90)*(1.045)*(1.10) - 1 = 1.1276595 - 1 = +0.1276595
      Max Net Drawdown: peak after Day 1 = 1.09, trough after Day 2 = 1.09*0.90 = 0.981 -> DD = -0.10 (-10.0%)
      Recovery: Day 3 wealth = 1.025145 (< 1.09), Day 4 wealth = 1.1276595 (>= 1.09) -> recovered on 2026-02-05!
      EUR conversion (FX prior=0.80, Day1=0.80, Day2=0.88, Day3=0.88, Day4=0.88):
          Total FX multiplier = 0.88 / 0.80 = 1.10 -> Net EUR wealth = 1.1276595 * 1.10 = 1.24042545 (+24.042545%)
    """
    fund_id = "FUND-HAND-CALC-01"
    fund = FundDefinition(
        fund_id=fund_id,
        name="Hand-Calculated Verification Fund (Synthetic)",
        base_currency="USD",
        environment=EnvironmentMode.SIMULATED,
        data_status=DataStatus.SYNTHETIC,
        inception_date="2026-02-02",
        benchmark_id="BM-HAND-CALC",
        fee_schedule=FeeSchedule(100.0, 0.0, 0.0, False, 252),
        created_at_utc="2026-02-02T00:00:00Z",
    )

    rows = [
        ("2026-02-02", 1000.0, 100.0, 10.0, 0.0, 0.0, 0.0, 1090.0, 100.0, 0.80),
        ("2026-02-03", 1090.0, -150.0, 0.0, 0.0, 410.0, 0.0, 1350.0, 137.614679, 0.88),
        ("2026-02-04", 1350.0, 50.0, 5.0, 0.0, 0.0, 350.0, 1045.0, 101.936799, 0.88),
        ("2026-02-05", 1045.0, 104.5, 0.0, 0.0, 0.0, 0.0, 1149.5, 101.936799, 0.88),
    ]

    snaps = []
    fx = [
        FxRateRecord(
            rate_id="FX-HC-2026-02-01",
            valuation_date="2026-02-01",
            as_of_utc="2026-02-01T21:00:00Z",
            base_currency="USD",
            quote_currency="EUR",
            rate=0.80,
            source="HAND_CALC_EXACT",
        ).to_dict()
    ]
    for dt_s, op, gpnl, mfee, pfee, sub, red, cl, sh, fx_rate in rows:
        snap = NavSnapshot(
            snapshot_id=f"SNAP-HC-{dt_s}",
            fund_id=fund_id,
            valuation_date=dt_s,
            as_of_utc=f"{dt_s}T21:00:00Z",
            availability_utc=f"{dt_s}T21:30:00Z",
            revision=1,
            currency="USD",
            environment=EnvironmentMode.SIMULATED,
            data_status=DataStatus.SYNTHETIC,
            opening_nav=op,
            gross_pnl=gpnl,
            management_fee=mfee,
            performance_fee=pfee,
            other_expenses=0.0,
            subscriptions=sub,
            redemptions=red,
            closing_nav=cl,
            shares_outstanding=sh,
            cash_balance=cl * 0.2,
            gross_exposure=cl * 0.8,
            net_exposure=cl * 0.8,
            ledger_reconciled=True,
            ledger_hash="pending",
            provenance_source="HAND_CALCULATED_REFERENCE",
            cash_flow_timing=CashFlowTiming.START_OF_DAY,
        )
        d = snap.to_dict()
        d["ledger_hash"] = compute_ledger_hash(snap)
        snaps.append(d)
        fx.append(
            FxRateRecord(
                rate_id=f"FX-HC-{dt_s}",
                valuation_date=dt_s,
                as_of_utc=f"{dt_s}T21:00:00Z",
                base_currency="USD",
                quote_currency="EUR",
                rate=fx_rate,
                source="HAND_CALC_EXACT",
            ).to_dict()
        )

    return {
        "schema_version": "1.0.0",
        "provenance": "EXACT_HAND_CALCULATED_TEST_CASE",
        "expected": {
            "cumulative_gross_twr_usd": 0.14345,
            "cumulative_net_twr_usd": 0.1276595,
            "cumulative_net_twr_eur": 0.24042545,
            "max_net_drawdown_usd": -0.10,
            "max_net_drawdown_peak_date": "2026-02-02",
            "max_net_drawdown_trough_date": "2026-02-03",
            "max_net_drawdown_recovery_date": "2026-02-05",
        },
        "fund": fund.to_dict(),
        "nav_snapshots": snaps,
        "fx_rates": fx,
        "benchmark_points": [],
        "strategy_snapshots": [],
        "cash_flows": [],
    }


def main() -> None:
    out_dir = Path(__file__).resolve().parent
    alpha = generate_fund_alpha_bundle()
    (out_dir / "fund_alpha_synthetic.json").write_text(
        json.dumps(alpha, indent=2, sort_keys=True), encoding="utf-8"
    )
    hc = generate_hand_calculated_case()
    (out_dir / "hand_calculated_case.json").write_text(
        json.dumps(hc, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"Generated fixtures in {out_dir}")


if __name__ == "__main__":
    main()
