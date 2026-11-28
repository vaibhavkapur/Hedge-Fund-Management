"""Canonical input parsing, schema validation, and broker statement normalization."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from pb_collateral import __schema_version__
from pb_collateral.errors import UnsupportedInputError, ValidationError
from pb_collateral.models import (
    AssetClass,
    BrokerStatement,
    CashBalanceRecord,
    CashBalanceType,
    CollateralPolicy,
    CollateralTransfer,
    DayCountConvention,
    EncumbranceStatus,
    FinancingRateSchedule,
    FxRate,
    HaircutRule,
    InternalLedgerSnapshot,
    MarginRequirement,
    PositionRecord,
    ROUNDING_MAP,
    SUPPORTED_CURRENCIES,
    SubstitutionRequest,
    TransferStatus,
)

# Canonical normalization aliases from varied prime broker statement feeds
ASSET_CLASS_ALIASES: Dict[str, AssetClass] = {
    "CASH": AssetClass.CASH,
    "USD_CASH": AssetClass.CASH,
    "UST": AssetClass.US_TREASURY,
    "US_TREASURY": AssetClass.US_TREASURY,
    "TREASURY": AssetClass.US_TREASURY,
    "SOV": AssetClass.SOVEREIGN_BOND,
    "SOVEREIGN": AssetClass.SOVEREIGN_BOND,
    "SOVEREIGN_BOND": AssetClass.SOVEREIGN_BOND,
    "CORP_IG": AssetClass.IG_CORPORATE_BOND,
    "IG_CORP": AssetClass.IG_CORPORATE_BOND,
    "IG_CORPORATE_BOND": AssetClass.IG_CORPORATE_BOND,
    "CORP_HY": AssetClass.HY_CORPORATE_BOND,
    "HY_CORP": AssetClass.HY_CORPORATE_BOND,
    "HY_CORPORATE_BOND": AssetClass.HY_CORPORATE_BOND,
    "EQ_LC": AssetClass.EQUITY_LARGE_CAP,
    "LARGE_CAP_EQUITY": AssetClass.EQUITY_LARGE_CAP,
    "EQUITY_LARGE_CAP": AssetClass.EQUITY_LARGE_CAP,
    "EQ_SC": AssetClass.EQUITY_SMALL_CAP,
    "SMALL_CAP_EQUITY": AssetClass.EQUITY_SMALL_CAP,
    "EQUITY_SMALL_CAP": AssetClass.EQUITY_SMALL_CAP,
    "ETF": AssetClass.ETF,
    "MMF": AssetClass.MONEY_MARKET_FUND,
    "MONEY_MARKET_FUND": AssetClass.MONEY_MARKET_FUND,
}

ENCUMBRANCE_ALIASES: Dict[str, EncumbranceStatus] = {
    "FREE": EncumbranceStatus.UNENCUMBERED,
    "AVAILABLE": EncumbranceStatus.UNENCUMBERED,
    "UNENCUMBERED": EncumbranceStatus.UNENCUMBERED,
    "PLEDGED": EncumbranceStatus.PLEDGED_MARGIN,
    "PLEDGED_MARGIN": EncumbranceStatus.PLEDGED_MARGIN,
    "MARGIN_PLEDGED": EncumbranceStatus.PLEDGED_MARGIN,
    "REHYPOTHECATED": EncumbranceStatus.REHYPOTHECATED,
    "REHYP": EncumbranceStatus.REHYPOTHECATED,
    "PENDING_OUT": EncumbranceStatus.PENDING_TRANSFER_OUT,
    "PENDING_TRANSFER_OUT": EncumbranceStatus.PENDING_TRANSFER_OUT,
    "PENDING_IN": EncumbranceStatus.PENDING_TRANSFER_IN,
    "PENDING_TRANSFER_IN": EncumbranceStatus.PENDING_TRANSFER_IN,
    "FAILED": EncumbranceStatus.FAILED_TRANSFER,
    "FAILED_TRANSFER": EncumbranceStatus.FAILED_TRANSFER,
    "LOCKED_SUBSTITUTION": EncumbranceStatus.LOCKED_SUBSTITUTION,
}

CASH_TYPE_ALIASES: Dict[str, CashBalanceType] = {
    "FREE_CASH": CashBalanceType.FREE_CASH,
    "EXCESS_CASH": CashBalanceType.FREE_CASH,
    "MARGIN_CASH": CashBalanceType.MARGIN_CASH,
    "COLLATERAL_CASH": CashBalanceType.MARGIN_CASH,
    "DEBIT_FINANCING": CashBalanceType.DEBIT_FINANCING,
    "MARGIN_DEBIT": CashBalanceType.DEBIT_FINANCING,
    "SHORT_PROCEEDS": CashBalanceType.SHORT_PROCEEDS,
    "SHORT_CREDIT": CashBalanceType.SHORT_PROCEEDS,
}


def parse_decimal(value: Any, field_name: str, *, allow_negative: bool = True) -> Decimal:
    """Parse exact Decimal from string or int; reject IEEE-754 floats to prevent binary drift."""
    if isinstance(value, bool):
        raise ValidationError(
            f"Field '{field_name}' must be a decimal string or integer, got bool",
            context={"field": field_name, "value": value},
        )
    if isinstance(value, float):
        raise ValidationError(
            f"Field '{field_name}' received raw float {value!r}; pass exact decimal string",
            context={"field": field_name, "value": value},
        )
    if isinstance(value, Decimal):
        dec = value
    else:
        try:
            dec = Decimal(str(value).strip())
        except (InvalidOperation, AttributeError) as exc:
            raise ValidationError(
                f"Field '{field_name}' is not a valid decimal: {value!r}",
                context={"field": field_name, "value": value},
            ) from exc
    if not dec.is_finite():
        raise ValidationError(
            f"Field '{field_name}' must be finite: {value!r}",
            context={"field": field_name, "value": str(dec)},
        )
    if not allow_negative and dec < Decimal("0"):
        raise ValidationError(
            f"Field '{field_name}' cannot be negative: {dec}",
            context={"field": field_name, "value": str(dec)},
        )
    return dec


def parse_date(value: Any, field_name: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValidationError(
            f"Field '{field_name}' must be ISO date string YYYY-MM-DD",
            context={"field": field_name, "value": value},
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(
            f"Field '{field_name}' has invalid ISO date: {value!r}",
            context={"field": field_name, "value": value},
        ) from exc


def parse_timestamp_utc(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(
            f"Field '{field_name}' must be an ISO-8601 UTC timestamp string",
            context={"field": field_name, "value": value},
        )
    normalized = value.strip()
    candidate = normalized[:-1] + "+00:00" if normalized.endswith("Z") else normalized
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValidationError(
            f"Field '{field_name}' has invalid ISO-8601 timestamp: {value!r}",
            context={"field": field_name, "value": value},
        ) from exc
    if dt.tzinfo is None:
        raise ValidationError(
            f"Field '{field_name}' must include explicit timezone offset or 'Z'",
            context={"field": field_name, "value": value},
        )
    return normalized


def validate_currency(code: Any, field_name: str) -> str:
    if not isinstance(code, str):
        raise ValidationError(
            f"Field '{field_name}' must be a 3-letter ISO currency string",
            context={"field": field_name, "value": code},
        )
    normalized = code.strip().upper()
    if normalized not in SUPPORTED_CURRENCIES:
        raise UnsupportedInputError(
            f"Unsupported currency '{normalized}' in field '{field_name}'",
            context={"field": field_name, "currency": normalized, "supported": sorted(SUPPORTED_CURRENCIES)},
        )
    return normalized


def validate_schema_version(raw: Mapping[str, Any]) -> str:
    version = raw.get("schema_version", __schema_version__)
    if version != __schema_version__:
        raise UnsupportedInputError(
            f"Unsupported schema_version '{version}'; expected '{__schema_version__}'",
            context={"schema_version": version, "expected": __schema_version__},
        )
    return str(version)


def normalize_asset_class(value: Any, field_name: str = "asset_class") -> AssetClass:
    if isinstance(value, AssetClass):
        return value
    if not isinstance(value, str):
        raise ValidationError(
            f"Field '{field_name}' must be a string",
            context={"field": field_name, "value": value},
        )
    key = value.strip().upper()
    if key not in ASSET_CLASS_ALIASES:
        raise UnsupportedInputError(
            f"Unsupported asset_class '{value}'",
            context={"field": field_name, "value": value},
        )
    return ASSET_CLASS_ALIASES[key]


def normalize_encumbrance(value: Any, field_name: str = "encumbrance_status") -> EncumbranceStatus:
    if isinstance(value, EncumbranceStatus):
        return value
    if not isinstance(value, str):
        raise ValidationError(
            f"Field '{field_name}' must be a string",
            context={"field": field_name, "value": value},
        )
    key = value.strip().upper()
    if key not in ENCUMBRANCE_ALIASES:
        raise UnsupportedInputError(
            f"Unsupported encumbrance_status '{value}'",
            context={"field": field_name, "value": value},
        )
    return ENCUMBRANCE_ALIASES[key]


def normalize_cash_type(value: Any, field_name: str = "balance_type") -> CashBalanceType:
    if isinstance(value, CashBalanceType):
        return value
    if not isinstance(value, str):
        raise ValidationError(
            f"Field '{field_name}' must be a string",
            context={"field": field_name, "value": value},
        )
    key = value.strip().upper()
    if key not in CASH_TYPE_ALIASES:
        raise UnsupportedInputError(
            f"Unsupported balance_type '{value}'",
            context={"field": field_name, "value": value},
        )
    return CASH_TYPE_ALIASES[key]


def parse_day_count(value: Any, field_name: str = "day_count") -> DayCountConvention:
    if isinstance(value, DayCountConvention):
        return value
    if not isinstance(value, str):
        raise ValidationError(
            f"Field '{field_name}' must be a day-count string",
            context={"field": field_name, "value": value},
        )
    key = value.strip().upper()
    try:
        return DayCountConvention(key)
    except ValueError as exc:
        raise UnsupportedInputError(
            f"Unsupported day-count convention '{value}'",
            context={"field": field_name, "value": value, "supported": [c.value for c in DayCountConvention]},
        ) from exc


def require_str(raw: Mapping[str, Any], key: str) -> str:
    val = raw.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValidationError(
            f"Missing or empty required string field '{key}'",
            context={"field": key},
        )
    return val.strip()


def canonical_content_hash(payload: Mapping[str, Any]) -> str:
    """Compute stable SHA-256 hash over canonical JSON representation."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_policy_config(raw: Mapping[str, Any]) -> Tuple[Dict[str, CollateralPolicy], str]:
    validate_schema_version(raw)
    rounding_mode = str(raw.get("rounding_mode", "ROUND_HALF_EVEN"))
    if rounding_mode not in ROUNDING_MAP:
        raise UnsupportedInputError(
            f"Unsupported rounding_mode '{rounding_mode}'",
            context={"rounding_mode": rounding_mode},
        )
    policies_raw = raw.get("policies")
    if not isinstance(policies_raw, list) or not policies_raw:
        raise ValidationError("Policy config must include non-empty 'policies' list")

    policies: Dict[str, CollateralPolicy] = {}
    for item in policies_raw:
        policy = parse_policy(item)
        policies[policy.broker_id] = policy
    return policies, rounding_mode


def parse_policy(raw: Mapping[str, Any]) -> CollateralPolicy:
    policy_id = require_str(raw, "policy_id")
    broker_id = require_str(raw, "broker_id")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValidationError("Policy 'version' must be a positive integer", context={"policy_id": policy_id})
    base_currency = validate_currency(raw.get("base_currency"), "base_currency")
    effective_date = parse_date(raw.get("effective_date"), "effective_date")
    default_dc = parse_day_count(raw.get("default_day_count", "ACT_360"), "default_day_count")
    mta = parse_decimal(raw.get("minimum_transfer_amount", "0.00"), "minimum_transfer_amount", allow_negative=False)
    rounding_inc = parse_decimal(raw.get("rounding_increment", "0.01"), "rounding_increment", allow_negative=False)
    if rounding_inc <= Decimal("0"):
        raise ValidationError("Policy 'rounding_increment' must be positive", context={"policy_id": policy_id})
    tolerance = parse_decimal(raw.get("reconciliation_tolerance", "0.01"), "reconciliation_tolerance", allow_negative=False)

    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise ValidationError("Policy must contain at least one haircut rule", context={"policy_id": policy_id})

    rules: List[HaircutRule] = []
    for idx, r in enumerate(rules_raw):
        ac = normalize_asset_class(r.get("asset_class"), f"rules[{idx}].asset_class")
        rating = require_str(r, "rating_tier").upper()
        maturity = require_str(r, "maturity_bucket").upper()
        eligible = r.get("eligible")
        if not isinstance(eligible, bool):
            raise ValidationError(f"rules[{idx}].eligible must be boolean")
        hc = parse_decimal(r.get("haircut_pct"), f"rules[{idx}].haircut_pct", allow_negative=False)
        if hc > Decimal("1"):
            raise ValidationError(
                f"rules[{idx}].haircut_pct must be between 0 and 1, got {hc}",
                context={"policy_id": policy_id, "haircut_pct": str(hc)},
            )
        conc_raw = r.get("concentration_limit_pct")
        conc = None if conc_raw is None else parse_decimal(conc_raw, f"rules[{idx}].concentration_limit_pct", allow_negative=False)
        if conc is not None and conc > Decimal("1"):
            raise ValidationError(f"rules[{idx}].concentration_limit_pct cannot exceed 1.00")
        fx_addon = parse_decimal(r.get("currency_mismatch_addon_pct", "0.00"), f"rules[{idx}].currency_mismatch_addon_pct", allow_negative=False)
        if fx_addon > Decimal("1"):
            raise ValidationError(f"rules[{idx}].currency_mismatch_addon_pct cannot exceed 1.00")
        rules.append(
            HaircutRule(
                asset_class=ac,
                rating_tier=rating,
                maturity_bucket=maturity,
                eligible=eligible,
                haircut_pct=hc,
                concentration_limit_pct=conc,
                currency_mismatch_addon_pct=fx_addon,
            )
        )

    return CollateralPolicy(
        policy_id=policy_id,
        version=version,
        broker_id=broker_id,
        base_currency=base_currency,
        effective_date=effective_date,
        default_day_count=default_dc,
        minimum_transfer_amount=mta,
        rounding_increment=rounding_inc,
        reconciliation_tolerance=tolerance,
        rules=tuple(rules),
    )


def parse_financing_schedules(raw: Mapping[str, Any]) -> Dict[Tuple[str, str], FinancingRateSchedule]:
    validate_schema_version(raw)
    items = raw.get("schedules")
    if not isinstance(items, list) or not items:
        raise ValidationError("Financing schedule config must contain non-empty 'schedules' list")
    result: Dict[Tuple[str, str], FinancingRateSchedule] = {}
    for item in items:
        sched = FinancingRateSchedule(
            schedule_id=require_str(item, "schedule_id"),
            broker_id=require_str(item, "broker_id"),
            currency=validate_currency(item.get("currency"), "currency"),
            benchmark_name=require_str(item, "benchmark_name"),
            benchmark_rate=parse_decimal(item.get("benchmark_rate"), "benchmark_rate", allow_negative=True),
            debit_spread_bps=parse_decimal(item.get("debit_spread_bps"), "debit_spread_bps", allow_negative=False),
            credit_spread_bps=parse_decimal(item.get("credit_spread_bps"), "credit_spread_bps", allow_negative=False),
            short_rebate_spread_bps=parse_decimal(
                item.get("short_rebate_spread_bps"), "short_rebate_spread_bps", allow_negative=False
            ),
            day_count=parse_day_count(item.get("day_count"), "day_count"),
            effective_date=parse_date(item.get("effective_date"), "effective_date"),
        )
        result[(sched.broker_id, sched.currency)] = sched
    return result


def parse_fx_rates(raw_rates: Sequence[Mapping[str, Any]]) -> Tuple[FxRate, ...]:
    rates: List[FxRate] = []
    for idx, item in enumerate(raw_rates):
        rates.append(
            FxRate(
                base_currency=validate_currency(item.get("base_currency"), f"fx_rates[{idx}].base_currency"),
                quote_currency=validate_currency(item.get("quote_currency"), f"fx_rates[{idx}].quote_currency"),
                rate=parse_decimal(item.get("rate"), f"fx_rates[{idx}].rate", allow_negative=False),
                as_of_date=parse_date(item.get("as_of_date"), f"fx_rates[{idx}].as_of_date"),
                source=str(item.get("source", "SIMULATED_FIXING")),
            )
        )
    return tuple(rates)


def parse_position(raw: Mapping[str, Any], default_broker: str, default_account: str) -> PositionRecord:
    position_id = require_str(raw, "position_id")
    lot_id = require_str(raw, "lot_id")
    broker_id = str(raw.get("broker_id", default_broker)).strip()
    account_id = str(raw.get("account_id", default_account)).strip()
    security_id = require_str(raw, "security_id")
    asset_class = normalize_asset_class(raw.get("asset_class"), "asset_class")
    rating_tier = str(raw.get("rating_tier", "UNRATED")).strip().upper()
    maturity_bucket = str(raw.get("maturity_bucket", "ANY")).strip().upper()
    quantity = parse_decimal(raw.get("quantity"), "quantity", allow_negative=False)
    market_price = parse_decimal(raw.get("market_price"), "market_price", allow_negative=False)
    currency = validate_currency(raw.get("currency"), "currency")
    encumbrance = normalize_encumbrance(raw.get("encumbrance_status", "UNENCUMBERED"))
    isin = raw.get("isin")
    pledge_ref = raw.get("pledge_ref")
    return PositionRecord(
        position_id=position_id,
        lot_id=lot_id,
        broker_id=broker_id,
        account_id=account_id,
        security_id=security_id,
        asset_class=asset_class,
        rating_tier=rating_tier,
        maturity_bucket=maturity_bucket,
        quantity=quantity,
        market_price=market_price,
        currency=currency,
        encumbrance_status=encumbrance,
        isin=str(isin) if isin is not None else None,
        pledge_ref=str(pledge_ref) if pledge_ref is not None else None,
    )


def parse_cash_balance(raw: Mapping[str, Any], default_broker: str, default_account: str) -> CashBalanceRecord:
    balance_id = require_str(raw, "balance_id")
    broker_id = str(raw.get("broker_id", default_broker)).strip()
    account_id = str(raw.get("account_id", default_account)).strip()
    currency = validate_currency(raw.get("currency"), "currency")
    balance_type = normalize_cash_type(raw.get("balance_type"))
    amount = parse_decimal(raw.get("amount"), "amount", allow_negative=True)
    # Normalize sign semantics: debit financing is stored as negative balance if positive was passed under DEBIT_FINANCING?
    # Keep explicit sign validation: DEBIT_FINANCING must be <= 0, others >= 0.
    if balance_type == CashBalanceType.DEBIT_FINANCING and amount > Decimal("0"):
        raise ValidationError(
            f"DEBIT_FINANCING balance '{balance_id}' must be <= 0 (received {amount})",
            context={"balance_id": balance_id, "amount": str(amount)},
        )
    if balance_type != CashBalanceType.DEBIT_FINANCING and amount < Decimal("0"):
        raise ValidationError(
            f"{balance_type.value} balance '{balance_id}' cannot be negative (received {amount})",
            context={"balance_id": balance_id, "amount": str(amount)},
        )
    return CashBalanceRecord(
        balance_id=balance_id,
        broker_id=broker_id,
        account_id=account_id,
        currency=currency,
        amount=amount,
        balance_type=balance_type,
    )


def parse_transfer(raw: Mapping[str, Any]) -> CollateralTransfer:
    transfer_id = require_str(raw, "transfer_id")
    from_broker_id = require_str(raw, "from_broker_id")
    to_broker_id = require_str(raw, "to_broker_id")
    lot_id = require_str(raw, "lot_id")
    security_id = require_str(raw, "security_id")
    quantity = parse_decimal(raw.get("quantity"), "quantity", allow_negative=False)
    currency = validate_currency(raw.get("currency"), "currency")
    market_value = parse_decimal(raw.get("market_value"), "market_value", allow_negative=False)
    status_raw = require_str(raw, "status").upper()
    try:
        status = TransferStatus(status_raw)
    except ValueError as exc:
        raise UnsupportedInputError(f"Unsupported transfer status '{status_raw}'") from exc
    trade_date = parse_date(raw.get("trade_date"), "trade_date")
    settlement_date = parse_date(raw.get("settlement_date"), "settlement_date")
    if settlement_date < trade_date:
        raise ValidationError(
            f"Transfer '{transfer_id}' settlement_date cannot precede trade_date",
            context={"transfer_id": transfer_id},
        )
    failure_reason = raw.get("failure_reason")
    return CollateralTransfer(
        transfer_id=transfer_id,
        from_broker_id=from_broker_id,
        to_broker_id=to_broker_id,
        lot_id=lot_id,
        security_id=security_id,
        quantity=quantity,
        currency=currency,
        market_value=market_value,
        status=status,
        trade_date=trade_date,
        settlement_date=settlement_date,
        failure_reason=str(failure_reason) if failure_reason is not None else None,
    )


def normalize_broker_statement(raw: Mapping[str, Any]) -> BrokerStatement:
    """Validate and normalize a raw prime broker statement into canonical form with stable content hash."""
    validate_schema_version(raw)
    statement_id = require_str(raw, "statement_id")
    version = raw.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValidationError("Statement 'version' must be a positive integer", context={"statement_id": statement_id})
    broker_id = require_str(raw, "broker_id")
    account_id = require_str(raw, "account_id")
    business_date = parse_date(raw.get("business_date"), "business_date")
    received_at = parse_timestamp_utc(raw.get("received_at"), "received_at")
    base_currency = validate_currency(raw.get("base_currency"), "base_currency")

    positions_raw = raw.get("positions", [])
    if not isinstance(positions_raw, list):
        raise ValidationError("Statement 'positions' must be a list")
    positions = tuple(parse_position(p, broker_id, account_id) for p in positions_raw)

    # Check duplicate lot_ids inside a single broker statement
    seen_lots: set = set()
    for p in positions:
        if p.lot_id in seen_lots:
            raise ValidationError(
                f"Duplicate lot_id '{p.lot_id}' within broker statement '{statement_id}'",
                context={"statement_id": statement_id, "lot_id": p.lot_id},
            )
        seen_lots.add(p.lot_id)

    cash_raw = raw.get("cash_balances", [])
    if not isinstance(cash_raw, list):
        raise ValidationError("Statement 'cash_balances' must be a list")
    cash_balances = tuple(parse_cash_balance(c, broker_id, account_id) for c in cash_raw)

    mr_raw = raw.get("margin_requirement")
    if not isinstance(mr_raw, Mapping):
        raise ValidationError("Statement must include 'margin_requirement' object")
    mr_currency = validate_currency(mr_raw.get("currency", base_currency), "margin_requirement.currency")
    if mr_currency != base_currency:
        raise ValidationError(
            f"Margin requirement currency '{mr_currency}' must match statement base_currency '{base_currency}'",
            context={"statement_id": statement_id, "mr_currency": mr_currency, "base_currency": base_currency},
        )
    margin_req = MarginRequirement(
        broker_id=broker_id,
        account_id=account_id,
        business_date=business_date,
        initial_margin=parse_decimal(mr_raw.get("initial_margin", "0.00"), "initial_margin", allow_negative=False),
        variation_margin=parse_decimal(mr_raw.get("variation_margin", "0.00"), "variation_margin", allow_negative=True),
        house_addon=parse_decimal(mr_raw.get("house_addon", "0.00"), "house_addon", allow_negative=False),
        currency=mr_currency,
    )

    rep_col_raw = raw.get("reported_collateral_value")
    rep_col = None if rep_col_raw is None else parse_decimal(rep_col_raw, "reported_collateral_value", allow_negative=True)
    rep_fin_raw = raw.get("reported_financing_accrual")
    rep_fin = None if rep_fin_raw is None else parse_decimal(rep_fin_raw, "reported_financing_accrual", allow_negative=True)
    supersedes = raw.get("supersedes_statement_id")

    canonical_dict = {
        "statement_id": statement_id,
        "version": version,
        "supersedes_statement_id": supersedes,
        "broker_id": broker_id,
        "account_id": account_id,
        "business_date": business_date.isoformat(),
        "base_currency": base_currency,
        "positions": [
            {
                "position_id": p.position_id,
                "lot_id": p.lot_id,
                "security_id": p.security_id,
                "asset_class": p.asset_class.value,
                "rating_tier": p.rating_tier,
                "maturity_bucket": p.maturity_bucket,
                "quantity": str(p.quantity),
                "market_price": str(p.market_price),
                "currency": p.currency,
                "encumbrance_status": p.encumbrance_status.value,
            }
            for p in sorted(positions, key=lambda x: x.lot_id)
        ],
        "cash_balances": [
            {
                "balance_id": c.balance_id,
                "currency": c.currency,
                "amount": str(c.amount),
                "balance_type": c.balance_type.value,
            }
            for c in sorted(cash_balances, key=lambda x: x.balance_id)
        ],
        "margin_requirement": {
            "initial_margin": str(margin_req.initial_margin),
            "variation_margin": str(margin_req.variation_margin),
            "house_addon": str(margin_req.house_addon),
            "currency": margin_req.currency,
        },
        "reported_collateral_value": str(rep_col) if rep_col is not None else None,
        "reported_financing_accrual": str(rep_fin) if rep_fin is not None else None,
    }

    return BrokerStatement(
        statement_id=statement_id,
        version=version,
        broker_id=broker_id,
        account_id=account_id,
        business_date=business_date,
        received_at=received_at,
        base_currency=base_currency,
        positions=positions,
        cash_balances=cash_balances,
        margin_requirement=margin_req,
        content_hash=canonical_content_hash(canonical_dict),
        supersedes_statement_id=str(supersedes) if supersedes is not None else None,
        reported_collateral_value=rep_col,
        reported_financing_accrual=rep_fin,
    )


def parse_internal_snapshot(raw: Mapping[str, Any]) -> InternalLedgerSnapshot:
    validate_schema_version(raw)
    snapshot_id = require_str(raw, "snapshot_id")
    version = raw.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValidationError("Snapshot 'version' must be a positive integer")
    broker_id = require_str(raw, "broker_id")
    account_id = require_str(raw, "account_id")
    business_date = parse_date(raw.get("business_date"), "business_date")
    positions = tuple(parse_position(p, broker_id, account_id) for p in raw.get("positions", []))
    cash_balances = tuple(parse_cash_balance(c, broker_id, account_id) for c in raw.get("cash_balances", []))
    transfers = tuple(parse_transfer(t) for t in raw.get("transfers", []))
    return InternalLedgerSnapshot(
        snapshot_id=snapshot_id,
        version=version,
        broker_id=broker_id,
        account_id=account_id,
        business_date=business_date,
        positions=positions,
        cash_balances=cash_balances,
        transfers=transfers,
    )


def parse_substitution_request(raw: Mapping[str, Any]) -> SubstitutionRequest:
    validate_schema_version(raw)
    request_id = require_str(raw, "request_id")
    broker_id = require_str(raw, "broker_id")
    account_id = require_str(raw, "account_id")
    business_date = parse_date(raw.get("business_date"), "business_date")
    requested_by = require_str(raw, "requested_by")
    release_ids_raw = raw.get("release_lot_ids", [])
    if not isinstance(release_ids_raw, list) or not all(isinstance(x, str) and x.strip() for x in release_ids_raw):
        raise ValidationError("Substitution 'release_lot_ids' must be a list of non-empty strings")
    pledge_raw = raw.get("pledge_positions", [])
    if not isinstance(pledge_raw, list):
        raise ValidationError("Substitution 'pledge_positions' must be a list")
    pledge_positions = tuple(parse_position(p, broker_id, account_id) for p in pledge_raw)
    return SubstitutionRequest(
        request_id=request_id,
        broker_id=broker_id,
        account_id=account_id,
        business_date=business_date,
        release_lot_ids=tuple(x.strip() for x in release_ids_raw),
        pledge_positions=pledge_positions,
        requested_by=requested_by,
    )
