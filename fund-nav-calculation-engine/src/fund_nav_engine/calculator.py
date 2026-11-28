"""Pure, deterministic fund NAV calculation engine (isolated from I/O)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from fund_nav_engine.errors import (
    FlowTimingError,
    FxMismatchError,
    LedgerReconciliationError,
    MissingValuationError,
    StalePriceError,
)
from fund_nav_engine.models import (
    AccrualDirection,
    CapitalFlowRecord,
    FlowStatus,
    FlowTimingRule,
    FlowType,
    FxRateRecord,
    InstrumentType,
    NavCalculationResult,
    PositionRecord,
    PositionValuation,
    PostCutoffPolicy,
    PriceRecord,
    ReconciliationReport,
    ShareClassNav,
    ShareClassRecord,
    SnapshotStatus,
    StalePricePolicy,
    ValuationConfig,
    ValuationInputBundle,
    quantize_decimal,
    sha256_canonical,
)
from fund_nav_engine.reconciliation import reconcile_ledger
from fund_nav_engine.validation import validate_bundle

ZERO = Decimal("0")
ONE = Decimal("1")


def _quantize_ccy(value: Decimal, currency: str, config: ValuationConfig) -> Decimal:
    return quantize_decimal(value, config.currency_precision(currency), config.rounding_mode)


def resolve_fx_rate(
    from_currency: str,
    to_currency: str,
    valuation_date: date,
    fx_rates: Tuple[FxRateRecord, ...],
    config: ValuationConfig,
) -> Decimal:
    """Resolve exact FX rate converting 1 unit of from_currency into to_currency."""
    if from_currency == to_currency:
        return ONE

    direct_candidates = [
        fx
        for fx in fx_rates
        if fx.base_currency == from_currency
        and fx.quote_currency == to_currency
        and fx.rate_date <= valuation_date
    ]
    if direct_candidates:
        latest_date = max(fx.rate_date for fx in direct_candidates)
        same_date = [fx for fx in direct_candidates if fx.rate_date == latest_date]
        distinct_rates = {fx.rate for fx in same_date}
        if len(distinct_rates) > 1:
            raise FxMismatchError(
                f"Conflicting FX rates for {from_currency}/{to_currency} on {latest_date}.",
                code="NAV-2005",
                details={"pair": f"{from_currency}/{to_currency}", "date": latest_date.isoformat()},
            )
        chosen = same_date[0]
        age = (valuation_date - chosen.rate_date).days
        if age > config.max_price_age_days:
            raise FxMismatchError(
                f"FX rate for {from_currency}/{to_currency} dated {chosen.rate_date} is stale ({age} days > {config.max_price_age_days}).",
                code="NAV-2004",
                details={"pair": f"{from_currency}/{to_currency}", "age_days": age},
            )
        return chosen.rate

    if config.allow_inverse_fx:
        inverse_candidates = [
            fx
            for fx in fx_rates
            if fx.base_currency == to_currency
            and fx.quote_currency == from_currency
            and fx.rate_date <= valuation_date
        ]
        if inverse_candidates:
            latest_date = max(fx.rate_date for fx in inverse_candidates)
            same_date = [fx for fx in inverse_candidates if fx.rate_date == latest_date]
            distinct_rates = {fx.rate for fx in same_date}
            if len(distinct_rates) > 1:
                raise FxMismatchError(
                    f"Conflicting inverse FX rates for {to_currency}/{from_currency} on {latest_date}.",
                    code="NAV-2005",
                    details={"pair": f"{to_currency}/{from_currency}", "date": latest_date.isoformat()},
                )
            chosen = same_date[0]
            age = (valuation_date - chosen.rate_date).days
            if age > config.max_price_age_days:
                raise FxMismatchError(
                    f"Inverse FX rate for {to_currency}/{from_currency} dated {chosen.rate_date} is stale ({age} days > {config.max_price_age_days}).",
                    code="NAV-2004",
                    details={"pair": f"{to_currency}/{from_currency}", "age_days": age},
                )
            return quantize_decimal(ONE / chosen.rate, config.fx_rate_precision, config.rounding_mode)

    raise FxMismatchError(
        f"Missing FX rate for currency conversion {from_currency} -> {to_currency} on {valuation_date}.",
        code="NAV-2003",
        details={"from_currency": from_currency, "to_currency": to_currency},
    )


def _select_price(
    position: PositionRecord,
    prices: Tuple[PriceRecord, ...],
    valuation_date: date,
) -> Optional[PriceRecord]:
    candidates = [
        pr
        for pr in prices
        if pr.instrument_id == position.instrument_id and pr.price_date <= valuation_date
    ]
    if not candidates:
        return None
    # Deterministic selection: latest price_date, override preferred on same date, then latest timestamp, then price_id
    candidates.sort(
        key=lambda pr: (pr.price_date, pr.is_override, pr.as_of_timestamp, pr.price_id),
        reverse=True,
    )
    return candidates[0]


def _allocate_amount_by_weights(
    total_amount: Decimal,
    weights: Dict[str, Decimal],
    currency: str,
    config: ValuationConfig,
) -> Dict[str, Decimal]:
    """Allocate a total currency amount across share classes with exact penny reconciliation."""
    precision = config.currency_precision(currency)
    quantum = ONE if precision == 0 else ONE.scaleb(-precision)
    positive_ids = sorted(cid for cid, w in weights.items() if w > ZERO)
    result: Dict[str, Decimal] = {cid: _quantize_ccy(ZERO, currency, config) for cid in weights}
    if not positive_ids or total_amount == ZERO:
        return result

    raw_shares: Dict[str, Decimal] = {}
    rounded: Dict[str, Decimal] = {}
    remainders: Dict[str, Decimal] = {}
    for cid in positive_ids:
        raw = total_amount * weights[cid]
        q = _quantize_ccy(raw, currency, config)
        raw_shares[cid] = raw
        rounded[cid] = q
        remainders[cid] = raw - q

    allocated_sum = sum(rounded.values(), ZERO)
    residual = _quantize_ccy(total_amount - allocated_sum, currency, config)
    if residual != ZERO:
        steps = int(abs(residual) / quantum)
        step_delta = quantum if residual > ZERO else -quantum
        # Deterministic ordering: largest signed remainder (towards residual sign), then largest weight, then class_id
        if residual > ZERO:
            order = sorted(
                positive_ids,
                key=lambda cid: (-remainders[cid], -weights[cid], cid),
            )
        else:
            order = sorted(
                positive_ids,
                key=lambda cid: (remainders[cid], -weights[cid], cid),
            )
        for idx in range(steps):
            target = order[idx % len(order)]
            rounded[target] = _quantize_ccy(rounded[target] + step_delta, currency, config)

    for cid, amt in rounded.items():
        result[cid] = amt
    return result


def _evaluate_positions(
    bundle: ValuationInputBundle,
    config: ValuationConfig,
    warnings: List[str],
    diagnostics: Dict[str, int],
) -> Tuple[List[PositionValuation], Decimal]:
    valuations: List[PositionValuation] = []
    total_positions_base = _quantize_ccy(ZERO, config.base_currency, config)

    for pos in sorted(bundle.positions, key=lambda p: p.position_id):
        selected_price = _select_price(pos, bundle.prices, bundle.valuation_date)
        if selected_price is None:
            if pos.instrument_type == InstrumentType.CASH:
                unit_price = ONE
                price_currency = pos.currency
                price_date = bundle.valuation_date
                age_days = 0
                is_stale = False
                stale_override_used = False
            else:
                raise MissingValuationError(
                    f"Missing valuation price for instrument '{pos.instrument_id}' (position '{pos.position_id}') on {bundle.valuation_date}.",
                    details={
                        "position_id": pos.position_id,
                        "instrument_id": pos.instrument_id,
                        "valuation_date": bundle.valuation_date.isoformat(),
                    },
                )
        else:
            if selected_price.currency != pos.currency:
                raise FxMismatchError(
                    f"Currency mismatch for position '{pos.position_id}' ({pos.currency}) vs price '{selected_price.price_id}' ({selected_price.currency}).",
                    code="NAV-2003",
                    details={
                        "position_id": pos.position_id,
                        "position_currency": pos.currency,
                        "price_id": selected_price.price_id,
                        "price_currency": selected_price.currency,
                    },
                )
            unit_price = selected_price.price
            price_currency = selected_price.currency
            price_date = selected_price.price_date
            age_days = (bundle.valuation_date - price_date).days
            is_stale = age_days > config.max_price_age_days
            stale_override_used = False

            if is_stale:
                diagnostics["stale_prices_detected"] += 1
                if config.stale_price_policy == StalePricePolicy.ERROR:
                    raise StalePriceError(
                        f"Stale price for instrument '{pos.instrument_id}': price_date {price_date} is {age_days} days old (max {config.max_price_age_days}).",
                        details={
                            "instrument_id": pos.instrument_id,
                            "price_id": selected_price.price_id,
                            "price_date": price_date.isoformat(),
                            "age_days": age_days,
                            "max_price_age_days": config.max_price_age_days,
                        },
                    )
                if config.stale_price_policy == StalePricePolicy.REQUIRE_OVERRIDE:
                    if not selected_price.is_override:
                        raise StalePriceError(
                            f"Stale price for instrument '{pos.instrument_id}' requires explicit valuation override.",
                            details={
                                "instrument_id": pos.instrument_id,
                                "price_id": selected_price.price_id,
                                "age_days": age_days,
                            },
                        )
                    stale_override_used = True
                    diagnostics["stale_price_overrides_used"] += 1
                    warnings.append(
                        f"Stale price override applied for {pos.instrument_id} (age={age_days}d, reason={selected_price.override_reason})."
                    )
                elif config.stale_price_policy == StalePricePolicy.WARN_AND_USE_LAST:
                    stale_override_used = selected_price.is_override
                    warnings.append(
                        f"Stale price used under warn_and_use_last for {pos.instrument_id} (price_date={price_date.isoformat()}, age={age_days}d)."
                    )

        local_mv = _quantize_ccy(pos.quantity * unit_price, pos.currency, config)
        fx_rate = resolve_fx_rate(
            pos.currency,
            config.base_currency,
            bundle.valuation_date,
            bundle.fx_rates,
            config,
        )
        base_mv = _quantize_ccy(local_mv * fx_rate, config.base_currency, config)
        total_positions_base = _quantize_ccy(
            total_positions_base + base_mv, config.base_currency, config
        )

        valuations.append(
            PositionValuation(
                position_id=pos.position_id,
                instrument_id=pos.instrument_id,
                instrument_type=pos.instrument_type,
                quantity=pos.quantity,
                unit_price=unit_price,
                price_currency=price_currency,
                local_market_value=local_mv,
                fx_rate=fx_rate,
                base_market_value=base_mv,
                price_date=price_date,
                price_age_days=age_days,
                is_stale=is_stale,
                stale_override_used=stale_override_used,
            )
        )
        diagnostics["positions_valued"] += 1

    return valuations, total_positions_base


def _classify_flows(
    bundle: ValuationInputBundle,
    config: ValuationConfig,
    warnings: List[str],
    diagnostics: Dict[str, int],
) -> List[CapitalFlowRecord]:
    cutoff_dt = datetime.combine(
        bundle.valuation_date, config.cutoff_time(), tzinfo=timezone.utc
    )
    eligible: List[CapitalFlowRecord] = []

    for flow in sorted(bundle.capital_flows, key=lambda f: (f.submitted_at, f.flow_id)):
        if flow.status == FlowStatus.CANCELLED:
            diagnostics["flows_cancelled"] += 1
            continue
        if flow.status == FlowStatus.PENDING:
            diagnostics["flows_pending"] += 1
            warnings.append(
                f"Capital flow '{flow.flow_id}' is pending and excluded from valuation {bundle.valuation_date.isoformat()}."
            )
            continue
        if flow.trade_date < bundle.valuation_date:
            raise FlowTimingError(
                f"Capital flow '{flow.flow_id}' has backdated trade_date {flow.trade_date} < valuation_date {bundle.valuation_date}; publish a restatement for {flow.trade_date} instead.",
                code="NAV-3004",
                details={
                    "flow_id": flow.flow_id,
                    "trade_date": flow.trade_date.isoformat(),
                    "valuation_date": bundle.valuation_date.isoformat(),
                },
            )
        if flow.submitted_at > cutoff_dt:
            if (
                flow.timing_rule == FlowTimingRule.PRE_NAV
                or config.post_cutoff_policy == PostCutoffPolicy.REJECT
            ):
                raise FlowTimingError(
                    f"Capital flow '{flow.flow_id}' submitted at {flow.submitted_at.isoformat()} exceeds dealing cutoff {cutoff_dt.isoformat()}.",
                    code="NAV-3003",
                    details={
                        "flow_id": flow.flow_id,
                        "submitted_at": flow.submitted_at.isoformat(),
                        "cutoff_utc": cutoff_dt.isoformat(),
                    },
                )
            diagnostics["flows_deferred_post_cutoff"] += 1
            warnings.append(
                f"Capital flow '{flow.flow_id}' submitted after cutoff ({flow.submitted_at.isoformat()} > {cutoff_dt.isoformat()}) deferred to next dealing date."
            )
            continue

        eligible.append(flow)
        diagnostics["flows_processed"] += 1

    return eligible


def calculate_nav(
    bundle: ValuationInputBundle,
    config: ValuationConfig,
    *,
    version: int = 1,
    prior_snapshot_id: Optional[str] = None,
    restatement_reason: Optional[str] = None,
    calculated_at: Optional[datetime] = None,
) -> NavCalculationResult:
    """Run deterministic daily fund NAV calculation and ledger reconciliation."""
    validate_bundle(bundle, config)

    calc_ts = (
        calculated_at.astimezone(timezone.utc)
        if calculated_at is not None
        else datetime.combine(bundle.valuation_date, config.cutoff_time(), tzinfo=timezone.utc)
    )
    warnings: List[str] = []
    diagnostics: Dict[str, int] = {
        "positions_valued": 0,
        "stale_prices_detected": 0,
        "stale_price_overrides_used": 0,
        "flows_processed": 0,
        "flows_deferred_post_cutoff": 0,
        "flows_pending": 0,
        "flows_cancelled": 0,
        "zero_share_classes": 0,
    }

    # 1. Value positions in local and base currency
    position_valuations, positions_base_total = _evaluate_positions(
        bundle, config, warnings, diagnostics
    )

    # 2. Convert liabilities and accruals into base currency (fund-level vs class-specific)
    base_ccy = config.base_currency
    fund_asset_accruals = _quantize_ccy(ZERO, base_ccy, config)
    fund_liabilities = _quantize_ccy(ZERO, base_ccy, config)
    class_asset_accruals: Dict[str, Decimal] = {
        sc.share_class_id: _quantize_ccy(ZERO, base_ccy, config)
        for sc in bundle.share_classes
    }
    class_liabilities: Dict[str, Decimal] = {
        sc.share_class_id: _quantize_ccy(ZERO, base_ccy, config)
        for sc in bundle.share_classes
    }

    for acc in sorted(bundle.accruals, key=lambda a: a.accrual_id):
        fx_rate = resolve_fx_rate(
            acc.currency, base_ccy, bundle.valuation_date, bundle.fx_rates, config
        )
        amt_base = _quantize_ccy(acc.amount * fx_rate, base_ccy, config)
        if acc.direction == AccrualDirection.ASSET:
            if acc.share_class_id is None:
                fund_asset_accruals = _quantize_ccy(
                    fund_asset_accruals + amt_base, base_ccy, config
                )
            else:
                class_asset_accruals[acc.share_class_id] = _quantize_ccy(
                    class_asset_accruals[acc.share_class_id] + amt_base, base_ccy, config
                )
        else:
            if acc.share_class_id is None:
                fund_liabilities = _quantize_ccy(fund_liabilities + amt_base, base_ccy, config)
            else:
                class_liabilities[acc.share_class_id] = _quantize_ccy(
                    class_liabilities[acc.share_class_id] + amt_base, base_ccy, config
                )

    for liab in sorted(bundle.liabilities, key=lambda l: l.liability_id):
        fx_rate = resolve_fx_rate(
            liab.currency, base_ccy, bundle.valuation_date, bundle.fx_rates, config
        )
        amt_base = _quantize_ccy(liab.amount * fx_rate, base_ccy, config)
        if liab.share_class_id is None:
            fund_liabilities = _quantize_ccy(fund_liabilities + amt_base, base_ccy, config)
        else:
            class_liabilities[liab.share_class_id] = _quantize_ccy(
                class_liabilities[liab.share_class_id] + amt_base, base_ccy, config
            )

    fund_level_gross_assets = _quantize_ccy(
        positions_base_total + fund_asset_accruals, base_ccy, config
    )
    total_class_asset_accruals = _quantize_ccy(
        sum(class_asset_accruals.values(), ZERO), base_ccy, config
    )
    total_class_liabilities = _quantize_ccy(
        sum(class_liabilities.values(), ZERO), base_ccy, config
    )

    pre_flow_gross_assets = _quantize_ccy(
        fund_level_gross_assets + total_class_asset_accruals, base_ccy, config
    )
    pre_flow_liabilities = _quantize_ccy(
        fund_liabilities + total_class_liabilities, base_ccy, config
    )
    pre_flow_net_assets = _quantize_ccy(
        pre_flow_gross_assets - pre_flow_liabilities, base_ccy, config
    )

    # 3. Compute share-class opening allocation weights
    sorted_classes: List[ShareClassRecord] = sorted(
        bundle.share_classes, key=lambda sc: sc.share_class_id
    )
    basis_by_class: Dict[str, Decimal] = {}
    fx_to_class_map: Dict[str, Decimal] = {}
    fx_from_class_map: Dict[str, Decimal] = {}

    for sc in sorted_classes:
        fx_from_class = resolve_fx_rate(
            sc.currency, base_ccy, bundle.valuation_date, bundle.fx_rates, config
        )
        fx_to_class = resolve_fx_rate(
            base_ccy, sc.currency, bundle.valuation_date, bundle.fx_rates, config
        )
        fx_from_class_map[sc.share_class_id] = fx_from_class
        fx_to_class_map[sc.share_class_id] = fx_to_class

        if sc.opening_shares == ZERO:
            basis_by_class[sc.share_class_id] = ZERO
        elif sc.prior_net_assets_base is not None:
            basis_by_class[sc.share_class_id] = sc.prior_net_assets_base
        else:
            ref_nav = (
                sc.prior_nav_per_share
                if sc.prior_nav_per_share is not None
                else sc.launch_nav_per_share
            )
            basis_by_class[sc.share_class_id] = sc.opening_shares * ref_nav * fx_from_class

    total_basis = sum(basis_by_class.values(), ZERO)
    weights: Dict[str, Decimal] = {}
    if total_basis > ZERO:
        for sc in sorted_classes:
            weights[sc.share_class_id] = basis_by_class[sc.share_class_id] / total_basis
    else:
        for sc in sorted_classes:
            weights[sc.share_class_id] = ZERO
        if fund_level_gross_assets != ZERO or fund_liabilities != ZERO:
            warnings.append(
                "All share classes have zero opening shares while non-zero fund-level balances exist."
            )

    allocated_gross = _allocate_amount_by_weights(
        fund_level_gross_assets, weights, base_ccy, config
    )
    allocated_liab = _allocate_amount_by_weights(
        fund_liabilities, weights, base_ccy, config
    )

    # 4. Classify and filter capital flows for dealing date
    eligible_flows = _classify_flows(bundle, config, warnings, diagnostics)

    # 5. Perform ledger reconciliation on sub-ledger totals vs ledger balances
    reconciliation_report: ReconciliationReport = reconcile_ledger(
        bundle=bundle,
        eligible_flows=eligible_flows,
        config=config,
    )
    if config.require_reconciliation and not reconciliation_report.reconciled:
        failed_breaks = [b.to_dict() for b in reconciliation_report.breaks if not b.passed]
        raise LedgerReconciliationError(
            f"Ledger reconciliation failed with {len(failed_breaks)} break(s) exceeding tolerance {config.ledger_tolerance}.",
            details={"breaks": failed_breaks},
        )

    # 6. Compute class-level dealing NAV, apply capital flows, and compute closing NAV
    flows_by_class: Dict[str, List[CapitalFlowRecord]] = {
        sc.share_class_id: [] for sc in sorted_classes
    }
    for fl in eligible_flows:
        flows_by_class[fl.share_class_id].append(fl)

    share_class_navs: List[ShareClassNav] = []
    total_subscriptions_base = _quantize_ccy(ZERO, base_ccy, config)
    total_redemptions_base = _quantize_ccy(ZERO, base_ccy, config)

    for sc in sorted_classes:
        cid = sc.share_class_id
        fx_to_class = fx_to_class_map[cid]
        fx_from_class = fx_from_class_map[cid]

        alloc_gross = allocated_gross[cid]
        alloc_liab = allocated_liab[cid]
        cls_asset_acc = class_asset_accruals[cid]
        cls_liab = class_liabilities[cid]

        class_pre_flow_net_base = _quantize_ccy(
            (alloc_gross + cls_asset_acc) - (alloc_liab + cls_liab), base_ccy, config
        )
        class_pre_flow_net_ccy = _quantize_ccy(
            class_pre_flow_net_base * fx_to_class, sc.currency, config
        )

        if sc.opening_shares > ZERO:
            dealing_nav: Optional[Decimal] = quantize_decimal(
                class_pre_flow_net_ccy / sc.opening_shares,
                config.nav_per_share_precision,
                config.rounding_mode,
            )
        else:
            # Launch NAV is available for seeding subscriptions; reported dealing NAV is None if no shares and no flows
            dealing_nav = (
                quantize_decimal(
                    sc.launch_nav_per_share,
                    config.nav_per_share_precision,
                    config.rounding_mode,
                )
                if flows_by_class[cid]
                else None
            )

        sub_amt_base = _quantize_ccy(ZERO, base_ccy, config)
        red_amt_base = _quantize_ccy(ZERO, base_ccy, config)
        sub_shares_total = quantize_decimal(ZERO, config.share_precision, config.rounding_mode)
        red_shares_total = quantize_decimal(ZERO, config.share_precision, config.rounding_mode)

        class_flows = flows_by_class[cid]
        for fl in class_flows:
            if fl.flow_type == FlowType.SUBSCRIPTION:
                if dealing_nav is None or dealing_nav <= ZERO:
                    raise FlowTimingError(
                        f"Cannot process subscription '{fl.flow_id}' for class '{cid}' with non-positive dealing NAV.",
                        code="NAV-3005",
                        details={"flow_id": fl.flow_id, "share_class_id": cid},
                    )
                fx_flow_to_class = resolve_fx_rate(
                    fl.currency, sc.currency, bundle.valuation_date, bundle.fx_rates, config
                )
                assert fl.amount is not None
                amt_class_ccy = _quantize_ccy(fl.amount * fx_flow_to_class, sc.currency, config)
                issued_shares = quantize_decimal(
                    amt_class_ccy / dealing_nav,
                    config.share_precision,
                    config.rounding_mode,
                )
                amt_base = _quantize_ccy(amt_class_ccy * fx_from_class, base_ccy, config)
                sub_amt_base = _quantize_ccy(sub_amt_base + amt_base, base_ccy, config)
                sub_shares_total = quantize_decimal(
                    sub_shares_total + issued_shares, config.share_precision, config.rounding_mode
                )
            else:
                available_shares = quantize_decimal(
                    sc.opening_shares + sub_shares_total - red_shares_total,
                    config.share_precision,
                    config.rounding_mode,
                )
                if fl.shares is not None:
                    req_shares = quantize_decimal(
                        fl.shares, config.share_precision, config.rounding_mode
                    )
                    if req_shares > available_shares:
                        raise FlowTimingError(
                            f"Redemption '{fl.flow_id}' requests {req_shares} shares but class '{cid}' only has {available_shares} available.",
                            code="NAV-3006",
                            details={
                                "flow_id": fl.flow_id,
                                "requested_shares": str(req_shares),
                                "available_shares": str(available_shares),
                            },
                        )
                    if req_shares == available_shares:
                        # Exact full redemption pays out exact remaining class net assets
                        remaining_base = _quantize_ccy(
                            class_pre_flow_net_base + sub_amt_base - red_amt_base,
                            base_ccy,
                            config,
                        )
                        amt_base = max(_quantize_ccy(ZERO, base_ccy, config), remaining_base)
                    else:
                        if dealing_nav is None or dealing_nav <= ZERO:
                            raise FlowTimingError(
                                f"Cannot process redemption '{fl.flow_id}' for class '{cid}' with non-positive dealing NAV.",
                                code="NAV-3005",
                                details={"flow_id": fl.flow_id, "share_class_id": cid},
                            )
                        amt_class_ccy = _quantize_ccy(req_shares * dealing_nav, sc.currency, config)
                        amt_base = _quantize_ccy(amt_class_ccy * fx_from_class, base_ccy, config)
                    redeemed_shares = req_shares
                else:
                    if dealing_nav is None or dealing_nav <= ZERO:
                        raise FlowTimingError(
                            f"Cannot process redemption '{fl.flow_id}' for class '{cid}' with non-positive dealing NAV.",
                            code="NAV-3005",
                            details={"flow_id": fl.flow_id, "share_class_id": cid},
                        )
                    fx_flow_to_class = resolve_fx_rate(
                        fl.currency, sc.currency, bundle.valuation_date, bundle.fx_rates, config
                    )
                    assert fl.amount is not None
                    amt_class_ccy = _quantize_ccy(fl.amount * fx_flow_to_class, sc.currency, config)
                    redeemed_shares = quantize_decimal(
                        amt_class_ccy / dealing_nav,
                        config.share_precision,
                        config.rounding_mode,
                    )
                    if redeemed_shares > available_shares:
                        raise FlowTimingError(
                            f"Redemption '{fl.flow_id}' requires {redeemed_shares} shares but class '{cid}' only has {available_shares} available.",
                            code="NAV-3006",
                            details={
                                "flow_id": fl.flow_id,
                                "requested_shares": str(redeemed_shares),
                                "available_shares": str(available_shares),
                            },
                        )
                    amt_base = _quantize_ccy(amt_class_ccy * fx_from_class, base_ccy, config)

                red_amt_base = _quantize_ccy(red_amt_base + amt_base, base_ccy, config)
                red_shares_total = quantize_decimal(
                    red_shares_total + redeemed_shares, config.share_precision, config.rounding_mode
                )

        total_subscriptions_base = _quantize_ccy(
            total_subscriptions_base + sub_amt_base, base_ccy, config
        )
        total_redemptions_base = _quantize_ccy(
            total_redemptions_base + red_amt_base, base_ccy, config
        )

        closing_shares = quantize_decimal(
            sc.opening_shares + sub_shares_total - red_shares_total,
            config.share_precision,
            config.rounding_mode,
        )
        closing_net_base = _quantize_ccy(
            class_pre_flow_net_base + sub_amt_base - red_amt_base, base_ccy, config
        )
        closing_net_ccy = _quantize_ccy(closing_net_base * fx_to_class, sc.currency, config)

        if closing_shares == ZERO:
            zero_shares_flag = True
            closing_nav_per_share: Optional[Decimal] = None
            diagnostics["zero_share_classes"] += 1
            warnings.append(
                f"Share class '{cid}' has zero closing shares on {bundle.valuation_date.isoformat()}; NAV per share is null."
            )
        else:
            zero_shares_flag = False
            closing_nav_per_share = quantize_decimal(
                closing_net_ccy / closing_shares,
                config.nav_per_share_precision,
                config.rounding_mode,
            )

        share_class_navs.append(
            ShareClassNav(
                share_class_id=cid,
                currency=sc.currency,
                opening_shares=quantize_decimal(
                    sc.opening_shares, config.share_precision, config.rounding_mode
                ),
                allocation_weight=quantize_decimal(
                    weights[cid], config.fx_rate_precision, config.rounding_mode
                ),
                allocated_gross_assets_base=alloc_gross,
                allocated_liabilities_base=alloc_liab,
                class_specific_asset_accruals_base=cls_asset_acc,
                class_specific_liabilities_base=cls_liab,
                pre_flow_net_assets_base=class_pre_flow_net_base,
                dealing_nav_per_share=dealing_nav,
                subscriptions_amount_base=sub_amt_base,
                redemptions_amount_base=red_amt_base,
                subscription_shares=sub_shares_total,
                redemption_shares=red_shares_total,
                closing_shares=closing_shares,
                closing_net_assets_base=closing_net_base,
                closing_net_assets_class_ccy=closing_net_ccy,
                fx_rate_to_class_ccy=fx_to_class,
                nav_per_share=closing_nav_per_share,
                zero_shares_flag=zero_shares_flag,
            )
        )

    # 7. Closing fund totals (exact accounting identity: NAV == Gross Assets - Liabilities)
    closing_gross_assets = _quantize_ccy(
        pre_flow_gross_assets + total_subscriptions_base, base_ccy, config
    )
    closing_liabilities = _quantize_ccy(
        pre_flow_liabilities + total_redemptions_base, base_ccy, config
    )
    closing_net_assets = _quantize_ccy(
        closing_gross_assets - closing_liabilities, base_ccy, config
    )
    net_capital_flows = _quantize_ccy(
        total_subscriptions_base - total_redemptions_base, base_ccy, config
    )

    assert closing_net_assets == _quantize_ccy(
        closing_gross_assets - closing_liabilities, base_ccy, config
    )

    input_hash = sha256_canonical(
        {"bundle": bundle.to_dict(), "config": config.to_dict()}
    )
    snapshot_id = (
        f"SNAP-{bundle.fund_id}-{bundle.valuation_date.isoformat()}"
        f"-v{version}-{input_hash[:12]}"
    )

    draft = NavCalculationResult(
        snapshot_id=snapshot_id,
        fund_id=bundle.fund_id,
        valuation_date=bundle.valuation_date,
        version=version,
        status=SnapshotStatus.DRAFT,
        prior_snapshot_id=prior_snapshot_id,
        restatement_reason=restatement_reason,
        base_currency=base_ccy,
        pre_flow_gross_assets=pre_flow_gross_assets,
        pre_flow_liabilities=pre_flow_liabilities,
        pre_flow_net_assets=pre_flow_net_assets,
        gross_assets=closing_gross_assets,
        total_liabilities=closing_liabilities,
        net_assets=closing_net_assets,
        total_subscriptions=total_subscriptions_base,
        total_redemptions=total_redemptions_base,
        net_capital_flows=net_capital_flows,
        position_valuations=tuple(position_valuations),
        share_class_navs=tuple(share_class_navs),
        reconciliation=reconciliation_report,
        warnings=tuple(warnings),
        diagnostics=diagnostics,
        input_hash=input_hash,
        config_version=config.config_version,
        calculated_at=calc_ts,
        output_hash="",
    )
    output_hash = sha256_canonical(draft.core_payload_dict())
    return replace(draft, output_hash=output_hash)
