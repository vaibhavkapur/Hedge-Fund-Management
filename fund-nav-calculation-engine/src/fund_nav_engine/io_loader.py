"""JSON loaders and serializers for canonical configs, input bundles, and results."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Union

from fund_nav_engine.errors import ConfigurationError, ValidationError
from fund_nav_engine.models import (
    AccrualDirection,
    AccrualRecord,
    AccrualType,
    CapitalFlowRecord,
    FlowStatus,
    FlowTimingRule,
    FlowType,
    FxRateRecord,
    InstrumentType,
    LedgerAccountType,
    LedgerBalanceRecord,
    LiabilityRecord,
    NavCalculationResult,
    PositionRecord,
    PositionValuation,
    PostCutoffPolicy,
    PriceRecord,
    ReconciliationBreak,
    ReconciliationReport,
    ShareClassNav,
    ShareClassRecord,
    SnapshotStatus,
    StalePricePolicy,
    ValuationConfig,
    ValuationInputBundle,
    parse_date,
    parse_decimal,
    parse_utc_datetime,
)
from fund_nav_engine.validation import validate_config


def _require_key(obj: Dict[str, Any], key: str, context: str) -> Any:
    if key not in obj:
        raise ValidationError(
            f"Missing required field '{key}' in {context}.",
            details={"field": key, "context": context},
        )
    return obj[key]


def _parse_enum(enum_cls: Any, raw: Any, field_name: str) -> Any:
    try:
        return enum_cls(raw)
    except ValueError as exc:
        valid = [e.value for e in enum_cls]
        raise ValidationError(
            f"Invalid value {raw!r} for '{field_name}'; expected one of {valid}.",
            details={"field": field_name, "value": raw, "allowed": valid},
        ) from exc


def config_from_dict(data: Dict[str, Any]) -> ValuationConfig:
    """Deserialize and validate ValuationConfig from a dictionary."""
    if not isinstance(data, dict):
        raise ConfigurationError("Configuration payload must be a JSON object.")
    try:
        stale_policy = StalePricePolicy(data.get("stale_price_policy", "error"))
    except ValueError as exc:
        raise ConfigurationError(
            f"Unsupported stale_price_policy: {data.get('stale_price_policy')!r}"
        ) from exc
    try:
        cutoff_policy = PostCutoffPolicy(data.get("post_cutoff_policy", "defer"))
    except ValueError as exc:
        raise ConfigurationError(
            f"Unsupported post_cutoff_policy: {data.get('post_cutoff_policy')!r}"
        ) from exc

    required = ("config_version", "fund_id", "base_currency", "currency_precisions")
    for req in required:
        if req not in data:
            raise ConfigurationError(f"Missing required configuration field '{req}'.")

    try:
        ledger_tol = parse_decimal(data.get("ledger_tolerance", "0.01"), "ledger_tolerance")
    except ValidationError as exc:
        raise ConfigurationError(exc.message, details=exc.details) from exc

    cfg = ValuationConfig(
        config_version=str(data["config_version"]),
        fund_id=str(data["fund_id"]),
        base_currency=str(data["base_currency"]),
        currency_precisions=dict(data["currency_precisions"]),
        nav_per_share_precision=int(data.get("nav_per_share_precision", 6)),
        share_precision=int(data.get("share_precision", 6)),
        fx_rate_precision=int(data.get("fx_rate_precision", 8)),
        rounding_mode=str(data.get("rounding_mode", "ROUND_HALF_EVEN")),
        max_price_age_days=int(data.get("max_price_age_days", 2)),
        stale_price_policy=stale_policy,
        allow_inverse_fx=bool(data.get("allow_inverse_fx", True)),
        flow_cutoff_utc=str(data.get("flow_cutoff_utc", "16:00:00")),
        post_cutoff_policy=cutoff_policy,
        ledger_tolerance=ledger_tol,
        require_reconciliation=bool(data.get("require_reconciliation", True)),
        seed=int(data.get("seed", 42)),
    )
    validate_config(cfg)
    return cfg


def load_config(path: Union[str, Path]) -> ValuationConfig:
    """Load ValuationConfig from a JSON file."""
    cfg_path = Path(path)
    if not cfg_path.is_file():
        raise ConfigurationError(
            f"Configuration file not found: {cfg_path}",
            details={"path": str(cfg_path)},
        )
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Invalid JSON in configuration file {cfg_path}: {exc}",
            details={"path": str(cfg_path)},
        ) from exc
    return config_from_dict(data)


def bundle_from_dict(data: Dict[str, Any]) -> ValuationInputBundle:
    """Deserialize ValuationInputBundle from a dictionary with strict decimal parsing."""
    if not isinstance(data, dict):
        raise ValidationError("Valuation input bundle must be a JSON object.")

    bundle_id = str(_require_key(data, "bundle_id", "bundle"))
    fund_id = str(_require_key(data, "fund_id", "bundle"))
    valuation_date = parse_date(_require_key(data, "valuation_date", "bundle"), "valuation_date")
    input_version = str(_require_key(data, "input_version", "bundle"))

    positions: List[PositionRecord] = []
    for idx, item in enumerate(data.get("positions", [])):
        ctx = f"positions[{idx}]"
        positions.append(
            PositionRecord(
                position_id=str(_require_key(item, "position_id", ctx)),
                fund_id=str(item.get("fund_id", fund_id)),
                instrument_id=str(_require_key(item, "instrument_id", ctx)),
                instrument_type=_parse_enum(
                    InstrumentType, _require_key(item, "instrument_type", ctx), f"{ctx}.instrument_type"
                ),
                quantity=parse_decimal(_require_key(item, "quantity", ctx), f"{ctx}.quantity"),
                cost_basis=parse_decimal(_require_key(item, "cost_basis", ctx), f"{ctx}.cost_basis"),
                currency=str(_require_key(item, "currency", ctx)),
                valuation_date=parse_date(
                    item.get("valuation_date", valuation_date.isoformat()), f"{ctx}.valuation_date"
                ),
                source_system=str(item.get("source_system", "portfolio_ledger")),
                record_version=int(item.get("record_version", 1)),
            )
        )

    prices: List[PriceRecord] = []
    for idx, item in enumerate(data.get("prices", [])):
        ctx = f"prices[{idx}]"
        prices.append(
            PriceRecord(
                price_id=str(_require_key(item, "price_id", ctx)),
                instrument_id=str(_require_key(item, "instrument_id", ctx)),
                price=parse_decimal(_require_key(item, "price", ctx), f"{ctx}.price"),
                currency=str(_require_key(item, "currency", ctx)),
                price_date=parse_date(_require_key(item, "price_date", ctx), f"{ctx}.price_date"),
                as_of_timestamp=parse_utc_datetime(
                    _require_key(item, "as_of_timestamp", ctx), f"{ctx}.as_of_timestamp"
                ),
                pricing_source=str(item.get("pricing_source", "composite_close")),
                is_override=bool(item.get("is_override", False)),
                override_reason=item.get("override_reason"),
            )
        )

    fx_rates: List[FxRateRecord] = []
    for idx, item in enumerate(data.get("fx_rates", [])):
        ctx = f"fx_rates[{idx}]"
        fx_rates.append(
            FxRateRecord(
                fx_id=str(_require_key(item, "fx_id", ctx)),
                base_currency=str(_require_key(item, "base_currency", ctx)),
                quote_currency=str(_require_key(item, "quote_currency", ctx)),
                rate=parse_decimal(_require_key(item, "rate", ctx), f"{ctx}.rate"),
                rate_date=parse_date(_require_key(item, "rate_date", ctx), f"{ctx}.rate_date"),
                as_of_timestamp=parse_utc_datetime(
                    _require_key(item, "as_of_timestamp", ctx), f"{ctx}.as_of_timestamp"
                ),
                source=str(item.get("source", "wm_reuters_mock")),
            )
        )

    liabilities: List[LiabilityRecord] = []
    for idx, item in enumerate(data.get("liabilities", [])):
        ctx = f"liabilities[{idx}]"
        liabilities.append(
            LiabilityRecord(
                liability_id=str(_require_key(item, "liability_id", ctx)),
                fund_id=str(item.get("fund_id", fund_id)),
                category=str(_require_key(item, "category", ctx)),
                amount=parse_decimal(_require_key(item, "amount", ctx), f"{ctx}.amount"),
                currency=str(_require_key(item, "currency", ctx)),
                valuation_date=parse_date(
                    item.get("valuation_date", valuation_date.isoformat()), f"{ctx}.valuation_date"
                ),
                share_class_id=item.get("share_class_id"),
                description=str(item.get("description", "")),
            )
        )

    accruals: List[AccrualRecord] = []
    for idx, item in enumerate(data.get("accruals", [])):
        ctx = f"accruals[{idx}]"
        accruals.append(
            AccrualRecord(
                accrual_id=str(_require_key(item, "accrual_id", ctx)),
                fund_id=str(item.get("fund_id", fund_id)),
                accrual_type=_parse_enum(
                    AccrualType, _require_key(item, "accrual_type", ctx), f"{ctx}.accrual_type"
                ),
                direction=_parse_enum(
                    AccrualDirection, _require_key(item, "direction", ctx), f"{ctx}.direction"
                ),
                amount=parse_decimal(_require_key(item, "amount", ctx), f"{ctx}.amount"),
                currency=str(_require_key(item, "currency", ctx)),
                valuation_date=parse_date(
                    item.get("valuation_date", valuation_date.isoformat()), f"{ctx}.valuation_date"
                ),
                period_start=parse_date(_require_key(item, "period_start", ctx), f"{ctx}.period_start"),
                period_end=parse_date(_require_key(item, "period_end", ctx), f"{ctx}.period_end"),
                share_class_id=item.get("share_class_id"),
                description=str(item.get("description", "")),
            )
        )

    share_classes: List[ShareClassRecord] = []
    for idx, item in enumerate(data.get("share_classes", [])):
        ctx = f"share_classes[{idx}]"
        prior_nav = (
            parse_decimal(item["prior_nav_per_share"], f"{ctx}.prior_nav_per_share")
            if item.get("prior_nav_per_share") is not None
            else None
        )
        prior_net = (
            parse_decimal(item["prior_net_assets_base"], f"{ctx}.prior_net_assets_base")
            if item.get("prior_net_assets_base") is not None
            else None
        )
        share_classes.append(
            ShareClassRecord(
                share_class_id=str(_require_key(item, "share_class_id", ctx)),
                fund_id=str(item.get("fund_id", fund_id)),
                name=str(_require_key(item, "name", ctx)),
                currency=str(_require_key(item, "currency", ctx)),
                opening_shares=parse_decimal(
                    _require_key(item, "opening_shares", ctx), f"{ctx}.opening_shares"
                ),
                management_fee_bps=parse_decimal(
                    item.get("management_fee_bps", "0"), f"{ctx}.management_fee_bps"
                ),
                launch_nav_per_share=parse_decimal(
                    _require_key(item, "launch_nav_per_share", ctx), f"{ctx}.launch_nav_per_share"
                ),
                prior_nav_per_share=prior_nav,
                prior_net_assets_base=prior_net,
            )
        )

    capital_flows: List[CapitalFlowRecord] = []
    for idx, item in enumerate(data.get("capital_flows", [])):
        ctx = f"capital_flows[{idx}]"
        amt = (
            parse_decimal(item["amount"], f"{ctx}.amount")
            if item.get("amount") is not None
            else None
        )
        sh = (
            parse_decimal(item["shares"], f"{ctx}.shares")
            if item.get("shares") is not None
            else None
        )
        capital_flows.append(
            CapitalFlowRecord(
                flow_id=str(_require_key(item, "flow_id", ctx)),
                fund_id=str(item.get("fund_id", fund_id)),
                share_class_id=str(_require_key(item, "share_class_id", ctx)),
                flow_type=_parse_enum(
                    FlowType, _require_key(item, "flow_type", ctx), f"{ctx}.flow_type"
                ),
                currency=str(_require_key(item, "currency", ctx)),
                trade_date=parse_date(_require_key(item, "trade_date", ctx), f"{ctx}.trade_date"),
                settlement_date=parse_date(
                    _require_key(item, "settlement_date", ctx), f"{ctx}.settlement_date"
                ),
                submitted_at=parse_utc_datetime(
                    _require_key(item, "submitted_at", ctx), f"{ctx}.submitted_at"
                ),
                amount=amt,
                shares=sh,
                status=_parse_enum(
                    FlowStatus, item.get("status", "confirmed"), f"{ctx}.status"
                ),
                timing_rule=_parse_enum(
                    FlowTimingRule, item.get("timing_rule", "post_nav"), f"{ctx}.timing_rule"
                ),
            )
        )

    ledger_balances: List[LedgerBalanceRecord] = []
    for idx, item in enumerate(data.get("ledger_balances", [])):
        ctx = f"ledger_balances[{idx}]"
        ledger_balances.append(
            LedgerBalanceRecord(
                ledger_account_id=str(_require_key(item, "ledger_account_id", ctx)),
                fund_id=str(item.get("fund_id", fund_id)),
                account_type=_parse_enum(
                    LedgerAccountType,
                    _require_key(item, "account_type", ctx),
                    f"{ctx}.account_type",
                ),
                balance=parse_decimal(_require_key(item, "balance", ctx), f"{ctx}.balance"),
                currency=str(_require_key(item, "currency", ctx)),
                as_of_date=parse_date(
                    item.get("as_of_date", valuation_date.isoformat()), f"{ctx}.as_of_date"
                ),
                share_class_id=item.get("share_class_id"),
            )
        )

    return ValuationInputBundle(
        bundle_id=bundle_id,
        fund_id=fund_id,
        valuation_date=valuation_date,
        input_version=input_version,
        positions=tuple(positions),
        prices=tuple(prices),
        fx_rates=tuple(fx_rates),
        liabilities=tuple(liabilities),
        accruals=tuple(accruals),
        share_classes=tuple(share_classes),
        capital_flows=tuple(capital_flows),
        ledger_balances=tuple(ledger_balances),
    )


def load_bundle(path: Union[str, Path]) -> ValuationInputBundle:
    """Load ValuationInputBundle from a JSON file."""
    bundle_path = Path(path)
    if not bundle_path.is_file():
        raise ValidationError(
            f"Input bundle file not found: {bundle_path}",
            details={"path": str(bundle_path)},
        )
    try:
        data = json.loads(bundle_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"Invalid JSON in input bundle {bundle_path}: {exc}",
            details={"path": str(bundle_path)},
        ) from exc
    return bundle_from_dict(data)


def result_from_dict(data: Dict[str, Any]) -> NavCalculationResult:
    """Reconstruct NavCalculationResult from a serialized dictionary."""
    position_valuations = tuple(
        PositionValuation(
            position_id=str(p["position_id"]),
            instrument_id=str(p["instrument_id"]),
            instrument_type=InstrumentType(p["instrument_type"]),
            quantity=parse_decimal(p["quantity"], "quantity"),
            unit_price=parse_decimal(p["unit_price"], "unit_price"),
            price_currency=str(p["price_currency"]),
            local_market_value=parse_decimal(p["local_market_value"], "local_market_value"),
            fx_rate=parse_decimal(p["fx_rate"], "fx_rate"),
            base_market_value=parse_decimal(p["base_market_value"], "base_market_value"),
            price_date=parse_date(p["price_date"], "price_date"),
            price_age_days=int(p["price_age_days"]),
            is_stale=bool(p["is_stale"]),
            stale_override_used=bool(p["stale_override_used"]),
        )
        for p in data["position_valuations"]
    )

    share_class_navs = tuple(
        ShareClassNav(
            share_class_id=str(s["share_class_id"]),
            currency=str(s["currency"]),
            opening_shares=parse_decimal(s["opening_shares"], "opening_shares"),
            allocation_weight=parse_decimal(s["allocation_weight"], "allocation_weight"),
            allocated_gross_assets_base=parse_decimal(
                s["allocated_gross_assets_base"], "allocated_gross_assets_base"
            ),
            allocated_liabilities_base=parse_decimal(
                s["allocated_liabilities_base"], "allocated_liabilities_base"
            ),
            class_specific_asset_accruals_base=parse_decimal(
                s["class_specific_asset_accruals_base"], "class_specific_asset_accruals_base"
            ),
            class_specific_liabilities_base=parse_decimal(
                s["class_specific_liabilities_base"], "class_specific_liabilities_base"
            ),
            pre_flow_net_assets_base=parse_decimal(
                s["pre_flow_net_assets_base"], "pre_flow_net_assets_base"
            ),
            dealing_nav_per_share=(
                parse_decimal(s["dealing_nav_per_share"], "dealing_nav_per_share")
                if s["dealing_nav_per_share"] is not None
                else None
            ),
            subscriptions_amount_base=parse_decimal(
                s["subscriptions_amount_base"], "subscriptions_amount_base"
            ),
            redemptions_amount_base=parse_decimal(
                s["redemptions_amount_base"], "redemptions_amount_base"
            ),
            subscription_shares=parse_decimal(s["subscription_shares"], "subscription_shares"),
            redemption_shares=parse_decimal(s["redemption_shares"], "redemption_shares"),
            closing_shares=parse_decimal(s["closing_shares"], "closing_shares"),
            closing_net_assets_base=parse_decimal(
                s["closing_net_assets_base"], "closing_net_assets_base"
            ),
            closing_net_assets_class_ccy=parse_decimal(
                s["closing_net_assets_class_ccy"], "closing_net_assets_class_ccy"
            ),
            fx_rate_to_class_ccy=parse_decimal(s["fx_rate_to_class_ccy"], "fx_rate_to_class_ccy"),
            nav_per_share=(
                parse_decimal(s["nav_per_share"], "nav_per_share")
                if s["nav_per_share"] is not None
                else None
            ),
            zero_shares_flag=bool(s["zero_shares_flag"]),
        )
        for s in data["share_class_navs"]
    )

    rec_data = data["reconciliation"]
    reconciliation = ReconciliationReport(
        reconciled=bool(rec_data["reconciled"]),
        total_checks=int(rec_data["total_checks"]),
        passed_checks=int(rec_data["passed_checks"]),
        breaks=tuple(
            ReconciliationBreak(
                account_type=LedgerAccountType(b["account_type"]),
                currency=str(b["currency"]),
                expected_amount=parse_decimal(b["expected_amount"], "expected_amount"),
                ledger_amount=parse_decimal(b["ledger_amount"], "ledger_amount"),
                difference=parse_decimal(b["difference"], "difference"),
                tolerance=parse_decimal(b["tolerance"], "tolerance"),
                passed=bool(b["passed"]),
            )
            for b in rec_data["breaks"]
        ),
    )

    return NavCalculationResult(
        snapshot_id=str(data["snapshot_id"]),
        fund_id=str(data["fund_id"]),
        valuation_date=parse_date(data["valuation_date"], "valuation_date"),
        version=int(data["version"]),
        status=SnapshotStatus(data["status"]),
        prior_snapshot_id=data.get("prior_snapshot_id"),
        restatement_reason=data.get("restatement_reason"),
        base_currency=str(data["base_currency"]),
        pre_flow_gross_assets=parse_decimal(data["pre_flow_gross_assets"], "pre_flow_gross_assets"),
        pre_flow_liabilities=parse_decimal(data["pre_flow_liabilities"], "pre_flow_liabilities"),
        pre_flow_net_assets=parse_decimal(data["pre_flow_net_assets"], "pre_flow_net_assets"),
        gross_assets=parse_decimal(data["gross_assets"], "gross_assets"),
        total_liabilities=parse_decimal(data["total_liabilities"], "total_liabilities"),
        net_assets=parse_decimal(data["net_assets"], "net_assets"),
        total_subscriptions=parse_decimal(data["total_subscriptions"], "total_subscriptions"),
        total_redemptions=parse_decimal(data["total_redemptions"], "total_redemptions"),
        net_capital_flows=parse_decimal(data["net_capital_flows"], "net_capital_flows"),
        position_valuations=position_valuations,
        share_class_navs=share_class_navs,
        reconciliation=reconciliation,
        warnings=tuple(data.get("warnings", [])),
        diagnostics=dict(data.get("diagnostics", {})),
        input_hash=str(data["input_hash"]),
        config_version=str(data["config_version"]),
        calculated_at=parse_utc_datetime(data["calculated_at"], "calculated_at"),
        output_hash=str(data["output_hash"]),
    )
