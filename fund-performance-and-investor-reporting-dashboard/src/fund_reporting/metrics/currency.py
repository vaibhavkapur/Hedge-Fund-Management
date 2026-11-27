"""Point-in-time multi-currency FX conversion with strict missing-rate rejection."""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Optional, Sequence, Tuple

from fund_reporting.domain.enums import CashFlowTiming, MetricFlagCode
from fund_reporting.domain.errors import CurrencyConversionError
from fund_reporting.domain.models import FxRateRecord, MetricFlag, NavSnapshot
from fund_reporting.domain.validation import compute_ledger_hash


def resolve_fx_rate(
    fx_rates: Sequence[FxRateRecord],
    valuation_date: str,
    from_currency: str,
    to_currency: str,
) -> float:
    """Resolve point-in-time FX rate (units of to_currency per 1 unit of from_currency).

    Supports direct lookup (`from -> to`) and exact inverse (`1 / (to -> from)`).
    Fails closed with CurrencyConversionError if no rate exists for the date.
    """
    src = from_currency.upper()
    dst = to_currency.upper()
    if src == dst:
        return 1.0

    for rec in fx_rates:
        if rec.valuation_date != valuation_date:
            continue
        if rec.base_currency == src and rec.quote_currency == dst:
            if rec.rate <= 0.0:
                raise CurrencyConversionError(
                    f"Non-positive FX rate for {src}/{dst} on {valuation_date}",
                    details={"valuation_date": valuation_date, "pair": f"{src}/{dst}", "rate": rec.rate},
                )
            return rec.rate
        if rec.base_currency == dst and rec.quote_currency == src:
            if rec.rate <= 0.0:
                raise CurrencyConversionError(
                    f"Non-positive inverse FX rate for {dst}/{src} on {valuation_date}",
                    details={"valuation_date": valuation_date, "pair": f"{dst}/{src}", "rate": rec.rate},
                )
            return 1.0 / rec.rate

    raise CurrencyConversionError(
        f"Missing point-in-time FX rate for {src}->{dst} on {valuation_date}",
        details={
            "valuation_date": valuation_date,
            "from_currency": src,
            "to_currency": dst,
        },
    )


def _find_prior_fx_rate(
    fx_rates: Sequence[FxRateRecord],
    first_date: str,
    from_currency: str,
    to_currency: str,
) -> Optional[float]:
    """Find the most recent FX rate strictly prior to first_date, if available."""
    prior_dates = sorted({r.valuation_date for r in fx_rates if r.valuation_date < first_date})
    for dt in reversed(prior_dates):
        try:
            return resolve_fx_rate(fx_rates, dt, from_currency, to_currency)
        except CurrencyConversionError:
            continue
    return None


def convert_nav_series(
    snapshots: Sequence[NavSnapshot],
    target_currency: str,
    fx_rates: Sequence[FxRateRecord],
) -> Tuple[List[NavSnapshot], List[MetricFlag], Dict[str, float]]:
    """Convert a chronologically ordered NAV series into target_currency using point-in-time FX.

    Preserves the exact NAV bridge in target_currency by incorporating FX translation P&L
    into converted gross_pnl:
      - opening_nav_target = opening_nav_base * fx_open
      - closing_nav_target = closing_nav_base * fx_close
      - cash_flows_target  = net_cf_base * (fx_open if START_OF_DAY else fx_close)
      - fees_target        = fees_base * fx_close
      - gross_pnl_target   = closing_nav_target - opening_nav_target - cash_flows_target + fees_target

    This ensures converted sub-period TWR satisfies:
        (1 + r_target) == (1 + r_base) * (fx_close / fx_open)
    identically for START_OF_DAY cash flows, and reconciles the NAV bridge to 0.00.
    """
    dst = target_currency.upper()
    ordered = sorted(snapshots, key=lambda s: s.valuation_date)
    if not ordered:
        return [], [], {}

    if all(s.currency == dst for s in ordered):
        return list(ordered), [], {s.valuation_date: 1.0 for s in ordered}

    converted: List[NavSnapshot] = []
    used_rates: Dict[str, float] = {}
    prev_close_fx: Optional[float] = _find_prior_fx_rate(
        fx_rates, ordered[0].valuation_date, ordered[0].currency, dst
    )

    for snap in ordered:
        fx_close = resolve_fx_rate(fx_rates, snap.valuation_date, snap.currency, dst)
        fx_open = prev_close_fx if prev_close_fx is not None else fx_close
        used_rates[snap.valuation_date] = fx_close

        opening_dst = snap.opening_nav * fx_open
        closing_dst = snap.closing_nav * fx_close
        cf_fx = fx_open if snap.cash_flow_timing == CashFlowTiming.START_OF_DAY else fx_close
        subs_dst = snap.subscriptions * cf_fx
        reds_dst = snap.redemptions * cf_fx
        mgmt_dst = snap.management_fee * fx_close
        perf_dst = snap.performance_fee * fx_close
        other_dst = snap.other_expenses * fx_close
        total_fees_dst = mgmt_dst + perf_dst + other_dst
        # Gross PnL in target currency includes both local asset P&L and FX translation P&L
        gross_pnl_dst = closing_dst - opening_dst - (subs_dst - reds_dst) + total_fees_dst

        updated = replace(
            snap,
            currency=dst,
            opening_nav=opening_dst,
            gross_pnl=gross_pnl_dst,
            management_fee=mgmt_dst,
            performance_fee=perf_dst,
            other_expenses=other_dst,
            subscriptions=subs_dst,
            redemptions=reds_dst,
            closing_nav=closing_dst,
            cash_balance=snap.cash_balance * fx_close,
            gross_exposure=snap.gross_exposure * fx_close,
            net_exposure=snap.net_exposure * fx_close,
        )
        updated = replace(updated, ledger_hash=compute_ledger_hash(updated))
        converted.append(updated)
        prev_close_fx = fx_close

    flags = [
        MetricFlag(
            code=MetricFlagCode.CURRENCY_CONVERTED,
            metric="currency_conversion",
            message=(
                f"Converted from base currency {ordered[0].currency} to reporting currency {dst} "
                "using point-in-time FX rates; gross/net returns include unhedged FX translation."
            ),
            severity="INFO",
        )
    ]
    return converted, flags, used_rates
