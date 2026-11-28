"""Configuration loading, validation, and deterministic hashing."""

from __future__ import annotations

import decimal
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, FrozenSet, Tuple

from .errors import ConfigurationError

SUPPORTED_ROUNDING_MODES: Dict[str, str] = {
    "ROUND_HALF_UP": decimal.ROUND_HALF_UP,
    "ROUND_HALF_EVEN": decimal.ROUND_HALF_EVEN,
    "ROUND_DOWN": decimal.ROUND_DOWN,
}


@dataclass(frozen=True)
class RoundingConfig:
    cash_decimal_places: int
    share_decimal_places: int
    nav_decimal_places: int
    mode: str
    max_valuation_residual: Decimal

    @property
    def cash_quantum(self) -> Decimal:
        return Decimal("1").scaleb(-self.cash_decimal_places)

    @property
    def share_quantum(self) -> Decimal:
        return Decimal("1").scaleb(-self.share_decimal_places)

    @property
    def nav_quantum(self) -> Decimal:
        return Decimal("1").scaleb(-self.nav_decimal_places)

    @property
    def decimal_rounding(self) -> str:
        return SUPPORTED_ROUNDING_MODES[self.mode]


@dataclass(frozen=True)
class ShareClassConfig:
    fund_id: str
    share_class: str
    currency: str
    subscription_fee_bps: int
    redemption_fee_bps: int
    min_subscription_amount: Decimal
    min_redemption_shares: Decimal


@dataclass(frozen=True)
class FundConfig:
    fund_id: str
    name: str
    base_currency: str
    share_classes: Dict[str, ShareClassConfig]


@dataclass(frozen=True)
class WorkflowPolicy:
    require_maker_checker: bool
    allow_late_cash_auto_settle: bool
    allow_cutoff_override_without_exception: bool
    sanctioned_jurisdictions: FrozenSet[str]
    sanctioned_names: FrozenSet[str]


@dataclass(frozen=True)
class PlatformConfig:
    schema_version: str
    environment: str
    deterministic_seed: int
    supported_currencies: FrozenSet[str]
    rounding: RoundingConfig
    funds: Dict[str, FundConfig]
    workflow: WorkflowPolicy
    config_hash: str

    def get_share_class(self, fund_id: str, share_class: str) -> ShareClassConfig:
        fund = self.funds.get(fund_id)
        if fund is None:
            raise ConfigurationError(
                f"Unknown fund_id '{fund_id}' in platform configuration.",
                {"fund_id": fund_id},
            )
        sc = fund.share_classes.get(share_class)
        if sc is None:
            raise ConfigurationError(
                f"Unknown share_class '{share_class}' for fund '{fund_id}'.",
                {"fund_id": fund_id, "share_class": share_class},
            )
        return sc


def _parse_decimal_str(raw: Any, field_name: str) -> Decimal:
    if isinstance(raw, bool) or isinstance(raw, float):
        raise ConfigurationError(
            f"Configuration field '{field_name}' must be a decimal string or integer, not {type(raw).__name__}.",
            {"field": field_name},
        )
    try:
        value = Decimal(str(raw))
    except Exception as exc:
        raise ConfigurationError(
            f"Invalid decimal value for '{field_name}': {raw!r}.",
            {"field": field_name, "value": raw},
        ) from exc
    if not value.is_finite():
        raise ConfigurationError(
            f"Non-finite decimal value for '{field_name}'.",
            {"field": field_name},
        )
    return value


def load_config_from_dict(data: Dict[str, Any]) -> PlatformConfig:
    """Validate and construct an immutable PlatformConfig from a dictionary."""
    if not isinstance(data, dict):
        raise ConfigurationError("Configuration payload must be a JSON object.")

    required_top = ("schema_version", "environment", "deterministic_seed", "supported_currencies", "rounding", "funds", "workflow")
    missing = [k for k in required_top if k not in data]
    if missing:
        raise ConfigurationError(
            f"Missing required configuration keys: {', '.join(missing)}.",
            {"missing_keys": missing},
        )

    currencies = frozenset(str(c).upper() for c in data["supported_currencies"])
    if not currencies:
        raise ConfigurationError("At least one supported currency must be configured.")

    r_raw = data["rounding"]
    mode = str(r_raw.get("mode", ""))
    if mode not in SUPPORTED_ROUNDING_MODES:
        raise ConfigurationError(
            f"Unsupported rounding mode '{mode}'. Supported: {sorted(SUPPORTED_ROUNDING_MODES)}.",
            {"mode": mode},
        )
    try:
        cash_dp = int(r_raw["cash_decimal_places"])
        share_dp = int(r_raw["share_decimal_places"])
        nav_dp = int(r_raw["nav_decimal_places"])
    except (KeyError, ValueError, TypeError) as exc:
        raise ConfigurationError("Invalid decimal places in rounding configuration.") from exc
    if cash_dp < 0 or share_dp < 0 or nav_dp < 0:
        raise ConfigurationError("Decimal places must be non-negative integers.")

    max_residual = _parse_decimal_str(r_raw.get("max_valuation_residual", "0.01"), "rounding.max_valuation_residual")
    if max_residual < Decimal("0"):
        raise ConfigurationError("rounding.max_valuation_residual must be >= 0.")

    rounding = RoundingConfig(
        cash_decimal_places=cash_dp,
        share_decimal_places=share_dp,
        nav_decimal_places=nav_dp,
        mode=mode,
        max_valuation_residual=max_residual,
    )

    funds_raw = data["funds"]
    if not isinstance(funds_raw, dict) or not funds_raw:
        raise ConfigurationError("At least one fund must be defined in configuration.")

    funds: Dict[str, FundConfig] = {}
    for fund_id, f_raw in funds_raw.items():
        base_ccy = str(f_raw.get("base_currency", "")).upper()
        if base_ccy not in currencies:
            raise ConfigurationError(
                f"Fund '{fund_id}' base_currency '{base_ccy}' is not in supported_currencies.",
                {"fund_id": fund_id, "base_currency": base_ccy},
            )
        sc_raw_map = f_raw.get("share_classes", {})
        if not sc_raw_map:
            raise ConfigurationError(f"Fund '{fund_id}' must define at least one share class.")
        share_classes: Dict[str, ShareClassConfig] = {}
        for sc_id, sc_raw in sc_raw_map.items():
            sc_ccy = str(sc_raw.get("currency", base_ccy)).upper()
            if sc_ccy not in currencies:
                raise ConfigurationError(
                    f"Share class '{fund_id}/{sc_id}' currency '{sc_ccy}' is not supported.",
                    {"fund_id": fund_id, "share_class": sc_id, "currency": sc_ccy},
                )
            sub_bps = int(sc_raw.get("subscription_fee_bps", 0))
            red_bps = int(sc_raw.get("redemption_fee_bps", 0))
            if not (0 <= sub_bps <= 5000) or not (0 <= red_bps <= 5000):
                raise ConfigurationError(
                    f"Fee bps for '{fund_id}/{sc_id}' out of valid range [0, 5000].",
                    {"subscription_fee_bps": sub_bps, "redemption_fee_bps": red_bps},
                )
            min_sub = _parse_decimal_str(sc_raw.get("min_subscription_amount", "0"), "min_subscription_amount")
            min_red = _parse_decimal_str(sc_raw.get("min_redemption_shares", "0"), "min_redemption_shares")
            if min_sub < 0 or min_red < 0:
                raise ConfigurationError("Minimum subscription/redemption thresholds must be >= 0.")
            share_classes[sc_id] = ShareClassConfig(
                fund_id=fund_id,
                share_class=sc_id,
                currency=sc_ccy,
                subscription_fee_bps=sub_bps,
                redemption_fee_bps=red_bps,
                min_subscription_amount=min_sub,
                min_redemption_shares=min_red,
            )
        funds[fund_id] = FundConfig(
            fund_id=fund_id,
            name=str(f_raw.get("name", fund_id)),
            base_currency=base_ccy,
            share_classes=share_classes,
        )

    wf_raw = data["workflow"]
    workflow = WorkflowPolicy(
        require_maker_checker=bool(wf_raw.get("require_maker_checker", True)),
        allow_late_cash_auto_settle=bool(wf_raw.get("allow_late_cash_auto_settle", False)),
        allow_cutoff_override_without_exception=bool(wf_raw.get("allow_cutoff_override_without_exception", False)),
        sanctioned_jurisdictions=frozenset(str(x).upper() for x in wf_raw.get("sanctioned_jurisdictions", [])),
        sanctioned_names=frozenset(str(x).strip().lower() for x in wf_raw.get("sanctioned_names", [])),
    )

    canonical_bytes = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    config_hash = hashlib.sha256(canonical_bytes).hexdigest()

    return PlatformConfig(
        schema_version=str(data["schema_version"]),
        environment=str(data["environment"]),
        deterministic_seed=int(data["deterministic_seed"]),
        supported_currencies=currencies,
        rounding=rounding,
        funds=funds,
        workflow=workflow,
        config_hash=config_hash,
    )


def load_config(path: str | Path) -> PlatformConfig:
    """Load and validate PlatformConfig from a JSON file path."""
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise ConfigurationError(
            f"Configuration file not found: {cfg_path}",
            {"path": str(cfg_path)},
        )
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Invalid JSON in configuration file '{cfg_path}': {exc}",
            {"path": str(cfg_path)},
        ) from exc
    return load_config_from_dict(raw)
