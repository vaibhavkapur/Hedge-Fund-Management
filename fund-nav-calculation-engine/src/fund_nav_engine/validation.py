"""Pure input and configuration validation for canonical NAV contracts."""

from __future__ import annotations

import re
from datetime import time
from decimal import Decimal
from typing import Iterable, Set

from fund_nav_engine.errors import ConfigurationError, FlowTimingError, ValidationError
from fund_nav_engine.models import (
    FlowType,
    InstrumentType,
    ValuationConfig,
    ValuationInputBundle,
)

ISO_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
SUPPORTED_ROUNDING_MODES = frozenset(
    {"ROUND_HALF_EVEN", "ROUND_HALF_UP", "ROUND_DOWN", "ROUND_UP"}
)


def _check_currency(currency: str, config: ValuationConfig, context: str) -> None:
    if not isinstance(currency, str) or not ISO_CURRENCY_RE.match(currency):
        raise ValidationError(
            f"Invalid ISO-4217 currency code '{currency}' in {context}.",
            details={"currency": currency, "context": context},
        )
    if currency not in config.currency_precisions:
        raise ValidationError(
            f"Unsupported currency '{currency}' in {context}; not configured in currency_precisions.",
            details={"currency": currency, "context": context},
        )


def _check_unique_ids(ids: Iterable[str], entity_name: str) -> None:
    seen: Set[str] = set()
    for item_id in ids:
        if not item_id or not item_id.strip():
            raise ValidationError(
                f"Empty identifier detected in {entity_name}.",
                details={"entity": entity_name},
            )
        if item_id in seen:
            raise ValidationError(
                f"Duplicate identifier '{item_id}' detected in {entity_name}.",
                details={"entity": entity_name, "id": item_id},
            )
        seen.add(item_id)


def validate_config(config: ValuationConfig) -> None:
    """Validate ValuationConfig before running any calculation."""
    if not config.config_version or not config.config_version.strip():
        raise ConfigurationError("config_version must be non-empty.")
    if not config.fund_id or not config.fund_id.strip():
        raise ConfigurationError("fund_id must be non-empty.")
    if not ISO_CURRENCY_RE.match(config.base_currency):
        raise ConfigurationError(
            f"base_currency '{config.base_currency}' must be a 3-letter uppercase ISO code."
        )
    if not config.currency_precisions:
        raise ConfigurationError("currency_precisions must not be empty.")
    if config.base_currency not in config.currency_precisions:
        raise ConfigurationError(
            f"base_currency '{config.base_currency}' must exist in currency_precisions."
        )
    for ccy, prec in config.currency_precisions.items():
        if not ISO_CURRENCY_RE.match(ccy):
            raise ConfigurationError(f"Invalid currency key '{ccy}' in currency_precisions.")
        if not isinstance(prec, int) or isinstance(prec, bool) or prec < 0 or prec > 8:
            raise ConfigurationError(
                f"Precision for currency '{ccy}' must be an integer in [0, 8], got {prec!r}."
            )
    if config.rounding_mode not in SUPPORTED_ROUNDING_MODES:
        raise ConfigurationError(
            f"Unsupported rounding_mode '{config.rounding_mode}'.",
            details={"supported": sorted(SUPPORTED_ROUNDING_MODES)},
        )
    if config.nav_per_share_precision < 0 or config.nav_per_share_precision > 10:
        raise ConfigurationError("nav_per_share_precision must be in [0, 10].")
    if config.share_precision < 0 or config.share_precision > 10:
        raise ConfigurationError("share_precision must be in [0, 10].")
    if config.fx_rate_precision < 1 or config.fx_rate_precision > 12:
        raise ConfigurationError("fx_rate_precision must be in [1, 12].")
    if config.max_price_age_days < 0:
        raise ConfigurationError("max_price_age_days must be >= 0.")
    if config.ledger_tolerance < Decimal("0"):
        raise ConfigurationError("ledger_tolerance must be >= 0.")
    try:
        time.fromisoformat(config.flow_cutoff_utc)
    except ValueError as exc:
        raise ConfigurationError(
            f"flow_cutoff_utc '{config.flow_cutoff_utc}' must be valid HH:MM:SS time."
        ) from exc


def validate_bundle(bundle: ValuationInputBundle, config: ValuationConfig) -> None:
    """Validate canonical ValuationInputBundle against domain contracts and config."""
    validate_config(config)

    if not bundle.bundle_id or not bundle.bundle_id.strip():
        raise ValidationError("bundle_id must be non-empty.")
    if not bundle.input_version or not bundle.input_version.strip():
        raise ValidationError("input_version must be non-empty.")
    if bundle.fund_id != config.fund_id:
        raise ValidationError(
            f"Bundle fund_id '{bundle.fund_id}' does not match config fund_id '{config.fund_id}'.",
            details={"bundle_fund_id": bundle.fund_id, "config_fund_id": config.fund_id},
        )

    if not bundle.share_classes:
        raise ValidationError("At least one share class must be defined in share_classes.")

    _check_unique_ids((p.position_id for p in bundle.positions), "positions")
    _check_unique_ids((p.price_id for p in bundle.prices), "prices")
    _check_unique_ids((f.fx_id for f in bundle.fx_rates), "fx_rates")
    _check_unique_ids((l.liability_id for l in bundle.liabilities), "liabilities")
    _check_unique_ids((a.accrual_id for a in bundle.accruals), "accruals")
    _check_unique_ids((s.share_class_id for s in bundle.share_classes), "share_classes")
    _check_unique_ids((c.flow_id for c in bundle.capital_flows), "capital_flows")
    _check_unique_ids((b.ledger_account_id for b in bundle.ledger_balances), "ledger_balances")

    share_class_ids = {sc.share_class_id for sc in bundle.share_classes}

    for sc in bundle.share_classes:
        if sc.fund_id != bundle.fund_id:
            raise ValidationError(
                f"Share class '{sc.share_class_id}' fund_id mismatch.",
                details={"share_class_id": sc.share_class_id},
            )
        _check_currency(sc.currency, config, f"share_class:{sc.share_class_id}")
        if sc.opening_shares < Decimal("0"):
            raise ValidationError(
                f"Share class '{sc.share_class_id}' opening_shares cannot be negative.",
                details={"share_class_id": sc.share_class_id},
            )
        if sc.management_fee_bps < Decimal("0"):
            raise ValidationError(
                f"Share class '{sc.share_class_id}' management_fee_bps cannot be negative.",
                details={"share_class_id": sc.share_class_id},
            )
        if sc.launch_nav_per_share <= Decimal("0"):
            raise ValidationError(
                f"Share class '{sc.share_class_id}' launch_nav_per_share must be > 0.",
                details={"share_class_id": sc.share_class_id},
            )
        if sc.prior_nav_per_share is not None and sc.prior_nav_per_share < Decimal("0"):
            raise ValidationError(
                f"Share class '{sc.share_class_id}' prior_nav_per_share cannot be negative.",
                details={"share_class_id": sc.share_class_id},
            )

    for pos in bundle.positions:
        if pos.fund_id != bundle.fund_id:
            raise ValidationError(
                f"Position '{pos.position_id}' fund_id mismatch.",
                details={"position_id": pos.position_id},
            )
        if not pos.instrument_id.strip():
            raise ValidationError(f"Position '{pos.position_id}' has empty instrument_id.")
        _check_currency(pos.currency, config, f"position:{pos.position_id}")
        if pos.valuation_date > bundle.valuation_date:
            raise ValidationError(
                f"Position '{pos.position_id}' valuation_date {pos.valuation_date} exceeds bundle date {bundle.valuation_date}.",
                details={"position_id": pos.position_id},
            )

    for pr in bundle.prices:
        if not pr.instrument_id.strip():
            raise ValidationError(f"Price '{pr.price_id}' has empty instrument_id.")
        _check_currency(pr.currency, config, f"price:{pr.price_id}")
        if pr.is_override and (not pr.override_reason or not pr.override_reason.strip()):
            raise ValidationError(
                f"Price '{pr.price_id}' marked is_override=True requires a non-empty override_reason.",
                details={"price_id": pr.price_id},
            )

    # Check non-negative prices for non-derivative instruments
    instrument_types = {p.instrument_id: p.instrument_type for p in bundle.positions}
    for pr in bundle.prices:
        inst_type = instrument_types.get(pr.instrument_id)
        if pr.price < Decimal("0") and inst_type != InstrumentType.DERIVATIVE_MTM:
            raise ValidationError(
                f"Price '{pr.price_id}' for instrument '{pr.instrument_id}' cannot be negative.",
                details={"price_id": pr.price_id, "price": str(pr.price)},
            )

    for fx in bundle.fx_rates:
        _check_currency(fx.base_currency, config, f"fx_rate:{fx.fx_id}:base")
        _check_currency(fx.quote_currency, config, f"fx_rate:{fx.fx_id}:quote")
        if fx.rate <= Decimal("0"):
            raise ValidationError(
                f"FX rate '{fx.fx_id}' must be strictly positive, got {fx.rate}.",
                details={"fx_id": fx.fx_id, "rate": str(fx.rate)},
            )

    for liab in bundle.liabilities:
        if liab.fund_id != bundle.fund_id:
            raise ValidationError(f"Liability '{liab.liability_id}' fund_id mismatch.")
        _check_currency(liab.currency, config, f"liability:{liab.liability_id}")
        if liab.amount < Decimal("0"):
            raise ValidationError(
                f"Liability '{liab.liability_id}' amount cannot be negative.",
                details={"liability_id": liab.liability_id},
            )
        if liab.valuation_date > bundle.valuation_date:
            raise ValidationError(
                f"Liability '{liab.liability_id}' valuation_date exceeds bundle date."
            )
        if liab.share_class_id is not None and liab.share_class_id not in share_class_ids:
            raise ValidationError(
                f"Liability '{liab.liability_id}' references unknown share_class_id '{liab.share_class_id}'."
            )

    for acc in bundle.accruals:
        if acc.fund_id != bundle.fund_id:
            raise ValidationError(f"Accrual '{acc.accrual_id}' fund_id mismatch.")
        _check_currency(acc.currency, config, f"accrual:{acc.accrual_id}")
        if acc.amount < Decimal("0"):
            raise ValidationError(
                f"Accrual '{acc.accrual_id}' amount cannot be negative.",
                details={"accrual_id": acc.accrual_id},
            )
        if acc.period_end < acc.period_start:
            raise ValidationError(
                f"Accrual '{acc.accrual_id}' period_end precedes period_start.",
                details={"accrual_id": acc.accrual_id},
            )
        if acc.valuation_date > bundle.valuation_date:
            raise ValidationError(
                f"Accrual '{acc.accrual_id}' valuation_date exceeds bundle date."
            )
        if acc.share_class_id is not None and acc.share_class_id not in share_class_ids:
            raise ValidationError(
                f"Accrual '{acc.accrual_id}' references unknown share_class_id '{acc.share_class_id}'."
            )

    for flow in bundle.capital_flows:
        if flow.fund_id != bundle.fund_id:
            raise ValidationError(f"Capital flow '{flow.flow_id}' fund_id mismatch.")
        if flow.share_class_id not in share_class_ids:
            raise ValidationError(
                f"Capital flow '{flow.flow_id}' references unknown share_class_id '{flow.share_class_id}'."
            )
        _check_currency(flow.currency, config, f"capital_flow:{flow.flow_id}")
        if flow.settlement_date < flow.trade_date:
            raise FlowTimingError(
                f"Capital flow '{flow.flow_id}' settlement_date {flow.settlement_date} precedes trade_date {flow.trade_date}.",
                code="NAV-3002",
                details={"flow_id": flow.flow_id},
            )
        if flow.trade_date > bundle.valuation_date:
            raise FlowTimingError(
                f"Capital flow '{flow.flow_id}' trade_date {flow.trade_date} is after valuation_date {bundle.valuation_date}.",
                code="NAV-3001",
                details={"flow_id": flow.flow_id},
            )
        if flow.flow_type == FlowType.SUBSCRIPTION:
            if flow.amount is None or flow.amount <= Decimal("0"):
                raise ValidationError(
                    f"Subscription flow '{flow.flow_id}' requires a strictly positive amount.",
                    details={"flow_id": flow.flow_id},
                )
            if flow.shares is not None:
                raise ValidationError(
                    f"Subscription flow '{flow.flow_id}' should specify amount only, not shares.",
                    details={"flow_id": flow.flow_id},
                )
        elif flow.flow_type == FlowType.REDEMPTION:
            has_amount = flow.amount is not None
            has_shares = flow.shares is not None
            if has_amount == has_shares:
                raise ValidationError(
                    f"Redemption flow '{flow.flow_id}' must specify exactly one of amount or shares.",
                    details={"flow_id": flow.flow_id},
                )
            if has_amount and flow.amount is not None and flow.amount <= Decimal("0"):
                raise ValidationError(
                    f"Redemption flow '{flow.flow_id}' amount must be > 0.",
                    details={"flow_id": flow.flow_id},
                )
            if has_shares and flow.shares is not None and flow.shares <= Decimal("0"):
                raise ValidationError(
                    f"Redemption flow '{flow.flow_id}' shares must be > 0.",
                    details={"flow_id": flow.flow_id},
                )

    for bal in bundle.ledger_balances:
        if bal.fund_id != bundle.fund_id:
            raise ValidationError(f"Ledger balance '{bal.ledger_account_id}' fund_id mismatch.")
        _check_currency(bal.currency, config, f"ledger_balance:{bal.ledger_account_id}")
        if bal.as_of_date > bundle.valuation_date:
            raise ValidationError(
                f"Ledger balance '{bal.ledger_account_id}' as_of_date exceeds bundle date."
            )
        if bal.share_class_id is not None and bal.share_class_id not in share_class_ids:
            raise ValidationError(
                f"Ledger balance '{bal.ledger_account_id}' references unknown share_class_id '{bal.share_class_id}'."
            )
