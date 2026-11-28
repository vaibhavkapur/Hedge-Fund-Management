"""Canonical domain data models for the Hedge Fund Fee Accounting Engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .errors import UnsupportedPolicyError, ValidationError


ROUNDING_MODES: Dict[str, str] = {
    "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
    "ROUND_HALF_UP": ROUND_HALF_UP,
}


def to_decimal(value: Any, *, field_name: str) -> Decimal:
    """Parse exact Decimal from str, int, or Decimal; reject float to prevent precision drift."""
    if isinstance(value, Decimal):
        if value.is_nan() or value.is_infinite():
            raise ValidationError(f"Non-finite decimal for {field_name}", code="NON_FINITE_DECIMAL", field=field_name)
        return value
    if isinstance(value, bool):
        raise ValidationError(f"Boolean is not a valid decimal for {field_name}", code="INVALID_DECIMAL", field=field_name)
    if isinstance(value, float):
        raise ValidationError(
            f"Float input rejected for {field_name}; provide exact string or Decimal",
            code="FLOAT_NOT_ALLOWED",
            field=field_name,
        )
    if isinstance(value, (int, str)):
        try:
            parsed = Decimal(str(value).strip())
        except InvalidOperation as exc:
            raise ValidationError(f"Invalid decimal '{value}' for {field_name}", code="INVALID_DECIMAL", field=field_name) from exc
        if parsed.is_nan() or parsed.is_infinite():
            raise ValidationError(f"Non-finite decimal for {field_name}", code="NON_FINITE_DECIMAL", field=field_name)
        return parsed
    raise ValidationError(
        f"Unsupported type {type(value).__name__} for decimal field {field_name}",
        code="INVALID_DECIMAL",
        field=field_name,
    )


def to_date(value: Any, *, field_name: str) -> date:
    """Parse ISO-8601 date string (YYYY-MM-DD) or date instance."""
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ValidationError(
                f"Invalid ISO date '{value}' for {field_name} (expected YYYY-MM-DD)",
                code="INVALID_DATE",
                field=field_name,
            ) from exc
    raise ValidationError(
        f"Unsupported type {type(value).__name__} for date field {field_name}",
        code="INVALID_DATE",
        field=field_name,
    )


def canonical_json(obj: Any) -> str:
    """Produce stable, canonical JSON string with sorted keys and stringified Decimals/dates."""

    def _default(item: Any) -> Any:
        if isinstance(item, Decimal):
            return format(item, "f")
        if isinstance(item, date):
            return item.isoformat()
        if isinstance(item, Enum):
            return item.value
        if hasattr(item, "to_dict"):
            return item.to_dict()
        raise TypeError(f"Object of type {type(item).__name__} is not JSON serializable")

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default)


def stable_hash(obj: Any, *, length: int = 16) -> str:
    """Compute deterministic SHA-256 hex digest prefix for canonical JSON representation."""
    digest = hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
    return digest[:length]


class DayCountConvention(str, Enum):
    ACT_365 = "ACT_365"
    ACT_360 = "ACT_360"
    THIRTY_360 = "THIRTY_360"
    MONTHLY_1_12 = "MONTHLY_1_12"
    QUARTERLY_1_4 = "QUARTERLY_1_4"


class ManagementFeeBasis(str, Enum):
    BEGINNING_NAV = "BEGINNING_NAV"
    ENDING_PRE_FEE_NAV = "ENDING_PRE_FEE_NAV"
    AVERAGE_NAV = "AVERAGE_NAV"


class HurdleType(str, Enum):
    NONE = "NONE"
    HARD_NON_COMPOUNDING = "HARD_NON_COMPOUNDING"
    HARD_COMPOUNDING = "HARD_COMPOUNDING"
    SOFT_CATCH_UP = "SOFT_CATCH_UP"


class HwmResetPolicy(str, Enum):
    ON_CRYSTALLIZATION_GAIN = "ON_CRYSTALLIZATION_GAIN"
    NEVER_RESET_BELOW_HWM = "NEVER_RESET_BELOW_HWM"
    PROPORTIONAL_REDEMPTION_ADJUSTMENT = "PROPORTIONAL_REDEMPTION_ADJUSTMENT"


class CrystallizationFrequency(str, Enum):
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUAL = "ANNUAL"
    CUSTOM_DATES = "CUSTOM_DATES"


class CapitalFlowType(str, Enum):
    SUBSCRIPTION = "SUBSCRIPTION"
    REDEMPTION = "REDEMPTION"


class JournalEntryType(str, Enum):
    MANAGEMENT_FEE_ACCRUAL = "MANAGEMENT_FEE_ACCRUAL"
    PERFORMANCE_FEE_ACCRUAL = "PERFORMANCE_FEE_ACCRUAL"
    PERFORMANCE_FEE_CRYSTALLIZATION = "PERFORMANCE_FEE_CRYSTALLIZATION"
    PERFORMANCE_FEE_REVERSAL = "PERFORMANCE_FEE_REVERSAL"
    REDEMPTION_CRYSTALLIZATION = "REDEMPTION_CRYSTALLIZATION"
    CORRECTION_REVERSAL = "CORRECTION_REVERSAL"
    CORRECTION_ENTRY = "CORRECTION_ENTRY"


class ApprovalStatus(str, Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"


@dataclass(frozen=True)
class AccountSpec:
    code: str
    name: str


@dataclass(frozen=True)
class EngineConfig:
    config_version: str
    schema_version: str
    monetary_rounding_mode: str
    nav_per_unit_decimals: int
    unit_decimals: int
    intermediate_precision: int
    currency_minor_units: Dict[str, int]
    allow_negative_accrual: bool
    allow_intra_period_perf_reversal: bool
    default_day_count: DayCountConvention
    default_management_fee_basis: ManagementFeeBasis
    oracle_comparison_tolerance: Decimal
    chart_of_accounts: Dict[str, AccountSpec]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EngineConfig":
        required_top = ("config_version", "schema_version", "rounding", "currency_minor_units", "defaults", "chart_of_accounts")
        for key in required_top:
            if key not in data:
                raise ValidationError(f"Missing required config key '{key}'", code="MISSING_CONFIG_KEY", field=key)

        rounding = data["rounding"]
        mode_name = rounding.get("monetary_rounding_mode", "ROUND_HALF_EVEN")
        if mode_name not in ROUNDING_MODES:
            raise UnsupportedPolicyError(
                f"Unsupported monetary rounding mode '{mode_name}'",
                code="UNSUPPORTED_ROUNDING_MODE",
                field="rounding.monetary_rounding_mode",
            )

        defaults = data["defaults"]
        try:
            default_dc = DayCountConvention(defaults["default_day_count"])
        except (KeyError, ValueError) as exc:
            raise UnsupportedPolicyError(
                f"Unsupported default day count '{defaults.get('default_day_count')}'",
                code="UNSUPPORTED_DAY_COUNT",
                field="defaults.default_day_count",
            ) from exc

        try:
            default_basis = ManagementFeeBasis(defaults["default_management_fee_basis"])
        except (KeyError, ValueError) as exc:
            raise UnsupportedPolicyError(
                f"Unsupported default management fee basis '{defaults.get('default_management_fee_basis')}'",
                code="UNSUPPORTED_FEE_BASIS",
                field="defaults.default_management_fee_basis",
            ) from exc

        coa_raw = data["chart_of_accounts"]
        required_accounts = (
            "management_fee_expense",
            "management_fee_payable",
            "performance_fee_expense",
            "performance_fee_accrued",
            "performance_fee_payable",
        )
        coa: Dict[str, AccountSpec] = {}
        for acct_key in required_accounts:
            if acct_key not in coa_raw:
                raise ValidationError(
                    f"Missing chart of accounts entry '{acct_key}'",
                    code="MISSING_ACCOUNT",
                    field=f"chart_of_accounts.{acct_key}",
                )
            coa[acct_key] = AccountSpec(
                code=str(coa_raw[acct_key]["code"]),
                name=str(coa_raw[acct_key]["name"]),
            )

        minor_units = {str(k).upper(): int(v) for k, v in data["currency_minor_units"].items()}
        return cls(
            config_version=str(data["config_version"]),
            schema_version=str(data["schema_version"]),
            monetary_rounding_mode=mode_name,
            nav_per_unit_decimals=int(rounding["nav_per_unit_decimals"]),
            unit_decimals=int(rounding["unit_decimals"]),
            intermediate_precision=int(rounding["intermediate_precision"]),
            currency_minor_units=minor_units,
            allow_negative_accrual=bool(defaults.get("allow_negative_accrual", False)),
            allow_intra_period_perf_reversal=bool(defaults.get("allow_intra_period_perf_reversal", True)),
            default_day_count=default_dc,
            default_management_fee_basis=default_basis,
            oracle_comparison_tolerance=to_decimal(
                defaults.get("oracle_comparison_tolerance", "0.00"),
                field_name="defaults.oracle_comparison_tolerance",
            ),
            chart_of_accounts=coa,
        )

    @classmethod
    def from_json_file(cls, path: Path) -> "EngineConfig":
        if not path.exists():
            raise ValidationError(f"Configuration file not found: {path}", code="CONFIG_NOT_FOUND", field="config_path")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"Invalid JSON in config file {path}: {exc}", code="INVALID_CONFIG_JSON", field="config_path") from exc
        return cls.from_dict(raw)

    def quantize_money(self, amount: Decimal, currency: str) -> Decimal:
        cur = currency.upper()
        if cur not in self.currency_minor_units:
            raise UnsupportedPolicyError(f"Unsupported currency '{currency}'", code="UNSUPPORTED_CURRENCY", field="currency")
        places = self.currency_minor_units[cur]
        quantum = Decimal(10) ** (-places)
        return amount.quantize(quantum, rounding=ROUNDING_MODES[self.monetary_rounding_mode])

    def quantize_nav(self, nav_per_unit: Decimal) -> Decimal:
        quantum = Decimal(10) ** (-self.nav_per_unit_decimals)
        return nav_per_unit.quantize(quantum, rounding=ROUNDING_MODES[self.monetary_rounding_mode])

    def quantize_units(self, units: Decimal) -> Decimal:
        quantum = Decimal(10) ** (-self.unit_decimals)
        return units.quantize(quantum, rounding=ROUNDING_MODES[self.monetary_rounding_mode])


@dataclass(frozen=True)
class FeeAgreement:
    agreement_id: str
    version: int
    share_class: str
    currency: str
    effective_date: date
    management_fee_rate: Decimal
    management_fee_basis: ManagementFeeBasis
    day_count: DayCountConvention
    performance_fee_rate: Decimal
    hurdle_type: HurdleType
    hurdle_rate: Decimal
    crystallization_frequency: CrystallizationFrequency
    hwm_reset_policy: HwmResetPolicy
    management_fee_deducted_before_perf: bool = True
    catch_up_rate: Decimal = Decimal("1.00")
    crystallization_dates: Tuple[date, ...] = ()
    allow_negative_accrual: bool = False
    allow_intra_period_perf_reversal: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FeeAgreement":
        try:
            mgmt_basis = ManagementFeeBasis(data["management_fee_basis"])
        except (KeyError, ValueError) as exc:
            raise UnsupportedPolicyError(
                f"Unsupported management_fee_basis '{data.get('management_fee_basis')}'",
                code="UNSUPPORTED_FEE_BASIS",
                field="management_fee_basis",
            ) from exc

        try:
            day_count = DayCountConvention(data["day_count"])
        except (KeyError, ValueError) as exc:
            raise UnsupportedPolicyError(
                f"Unsupported day_count '{data.get('day_count')}'",
                code="UNSUPPORTED_DAY_COUNT",
                field="day_count",
            ) from exc

        try:
            hurdle_type = HurdleType(data["hurdle_type"])
        except (KeyError, ValueError) as exc:
            raise UnsupportedPolicyError(
                f"Unsupported hurdle_type '{data.get('hurdle_type')}'",
                code="UNSUPPORTED_HURDLE_TYPE",
                field="hurdle_type",
            ) from exc

        try:
            cryst_freq = CrystallizationFrequency(data["crystallization_frequency"])
        except (KeyError, ValueError) as exc:
            raise UnsupportedPolicyError(
                f"Unsupported crystallization_frequency '{data.get('crystallization_frequency')}'",
                code="UNSUPPORTED_CRYSTALLIZATION_FREQ",
                field="crystallization_frequency",
            ) from exc

        try:
            hwm_policy = HwmResetPolicy(data.get("hwm_reset_policy", HwmResetPolicy.ON_CRYSTALLIZATION_GAIN.value))
        except ValueError as exc:
            raise UnsupportedPolicyError(
                f"Unsupported hwm_reset_policy '{data.get('hwm_reset_policy')}'",
                code="UNSUPPORTED_HWM_POLICY",
                field="hwm_reset_policy",
            ) from exc

        custom_dates = tuple(
            to_date(d, field_name="crystallization_dates")
            for d in data.get("crystallization_dates", [])
        )

        return cls(
            agreement_id=str(data["agreement_id"]),
            version=int(data.get("version", 1)),
            share_class=str(data["share_class"]),
            currency=str(data["currency"]).upper(),
            effective_date=to_date(data["effective_date"], field_name="effective_date"),
            management_fee_rate=to_decimal(data["management_fee_rate"], field_name="management_fee_rate"),
            management_fee_basis=mgmt_basis,
            day_count=day_count,
            performance_fee_rate=to_decimal(data["performance_fee_rate"], field_name="performance_fee_rate"),
            hurdle_type=hurdle_type,
            hurdle_rate=to_decimal(data.get("hurdle_rate", "0"), field_name="hurdle_rate"),
            crystallization_frequency=cryst_freq,
            hwm_reset_policy=hwm_policy,
            management_fee_deducted_before_perf=bool(data.get("management_fee_deducted_before_perf", True)),
            catch_up_rate=to_decimal(data.get("catch_up_rate", "1.00"), field_name="catch_up_rate"),
            crystallization_dates=custom_dates,
            allow_negative_accrual=bool(data.get("allow_negative_accrual", False)),
            allow_intra_period_perf_reversal=bool(data.get("allow_intra_period_perf_reversal", True)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agreement_id": self.agreement_id,
            "version": self.version,
            "share_class": self.share_class,
            "currency": self.currency,
            "effective_date": self.effective_date.isoformat(),
            "management_fee_rate": format(self.management_fee_rate, "f"),
            "management_fee_basis": self.management_fee_basis.value,
            "day_count": self.day_count.value,
            "performance_fee_rate": format(self.performance_fee_rate, "f"),
            "hurdle_type": self.hurdle_type.value,
            "hurdle_rate": format(self.hurdle_rate, "f"),
            "crystallization_frequency": self.crystallization_frequency.value,
            "hwm_reset_policy": self.hwm_reset_policy.value,
            "management_fee_deducted_before_perf": self.management_fee_deducted_before_perf,
            "catch_up_rate": format(self.catch_up_rate, "f"),
            "crystallization_dates": [d.isoformat() for d in self.crystallization_dates],
            "allow_negative_accrual": self.allow_negative_accrual,
            "allow_intra_period_perf_reversal": self.allow_intra_period_perf_reversal,
        }


@dataclass(frozen=True)
class InvestorLot:
    lot_id: str
    investor_id: str
    share_class: str
    series_id: str
    subscription_date: date
    units: Decimal
    opening_nav_per_unit: Decimal
    high_water_mark: Decimal
    hurdle_base_nav: Decimal
    prior_accrued_perf_fee: Decimal = Decimal("0.00")
    loss_carryforward_amount: Decimal = Decimal("0.00")
    last_crystallization_date: Optional[date] = None
    status: str = "ACTIVE"
    version: int = 1

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InvestorLot":
        opening_nav = to_decimal(data["opening_nav_per_unit"], field_name="opening_nav_per_unit")
        hwm = to_decimal(data.get("high_water_mark", opening_nav), field_name="high_water_mark")
        hurdle_base = to_decimal(data.get("hurdle_base_nav", hwm), field_name="hurdle_base_nav")
        last_cryst_raw = data.get("last_crystallization_date")
        last_cryst = to_date(last_cryst_raw, field_name="last_crystallization_date") if last_cryst_raw else None
        return cls(
            lot_id=str(data["lot_id"]),
            investor_id=str(data["investor_id"]),
            share_class=str(data["share_class"]),
            series_id=str(data.get("series_id", "INITIAL")),
            subscription_date=to_date(data["subscription_date"], field_name="subscription_date"),
            units=to_decimal(data["units"], field_name="units"),
            opening_nav_per_unit=opening_nav,
            high_water_mark=hwm,
            hurdle_base_nav=hurdle_base,
            prior_accrued_perf_fee=to_decimal(data.get("prior_accrued_perf_fee", "0.00"), field_name="prior_accrued_perf_fee"),
            loss_carryforward_amount=to_decimal(data.get("loss_carryforward_amount", "0.00"), field_name="loss_carryforward_amount"),
            last_crystallization_date=last_cryst,
            status=str(data.get("status", "ACTIVE")),
            version=int(data.get("version", 1)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lot_id": self.lot_id,
            "investor_id": self.investor_id,
            "share_class": self.share_class,
            "series_id": self.series_id,
            "subscription_date": self.subscription_date.isoformat(),
            "units": format(self.units, "f"),
            "opening_nav_per_unit": format(self.opening_nav_per_unit, "f"),
            "high_water_mark": format(self.high_water_mark, "f"),
            "hurdle_base_nav": format(self.hurdle_base_nav, "f"),
            "prior_accrued_perf_fee": format(self.prior_accrued_perf_fee, "f"),
            "loss_carryforward_amount": format(self.loss_carryforward_amount, "f"),
            "last_crystallization_date": self.last_crystallization_date.isoformat() if self.last_crystallization_date else None,
            "status": self.status,
            "version": self.version,
        }


@dataclass(frozen=True)
class NavSnapshot:
    snapshot_id: str
    valuation_date: date
    period_start_date: date
    share_class: str
    currency: str
    gross_nav_per_unit: Decimal
    version: int = 1
    source: str = "FIXTURE"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NavSnapshot":
        return cls(
            snapshot_id=str(data["snapshot_id"]),
            valuation_date=to_date(data["valuation_date"], field_name="valuation_date"),
            period_start_date=to_date(data["period_start_date"], field_name="period_start_date"),
            share_class=str(data["share_class"]),
            currency=str(data["currency"]).upper(),
            gross_nav_per_unit=to_decimal(data["gross_nav_per_unit"], field_name="gross_nav_per_unit"),
            version=int(data.get("version", 1)),
            source=str(data.get("source", "FIXTURE")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "valuation_date": self.valuation_date.isoformat(),
            "period_start_date": self.period_start_date.isoformat(),
            "share_class": self.share_class,
            "currency": self.currency,
            "gross_nav_per_unit": format(self.gross_nav_per_unit, "f"),
            "version": self.version,
            "source": self.source,
        }


@dataclass(frozen=True)
class CapitalFlow:
    flow_id: str
    investor_id: str
    lot_id: str
    share_class: str
    flow_date: date
    flow_type: CapitalFlowType
    nav_per_unit: Decimal
    currency: str
    amount: Optional[Decimal] = None
    units: Optional[Decimal] = None
    series_id: Optional[str] = None
    policy_note: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CapitalFlow":
        try:
            flow_type = CapitalFlowType(data["flow_type"])
        except (KeyError, ValueError) as exc:
            raise UnsupportedPolicyError(
                f"Unsupported flow_type '{data.get('flow_type')}'",
                code="UNSUPPORTED_FLOW_TYPE",
                field="flow_type",
            ) from exc
        amt = to_decimal(data["amount"], field_name="amount") if data.get("amount") is not None else None
        units = to_decimal(data["units"], field_name="units") if data.get("units") is not None else None
        return cls(
            flow_id=str(data["flow_id"]),
            investor_id=str(data["investor_id"]),
            lot_id=str(data["lot_id"]),
            share_class=str(data["share_class"]),
            flow_date=to_date(data["flow_date"], field_name="flow_date"),
            flow_type=flow_type,
            nav_per_unit=to_decimal(data["nav_per_unit"], field_name="nav_per_unit"),
            currency=str(data["currency"]).upper(),
            amount=amt,
            units=units,
            series_id=str(data["series_id"]) if data.get("series_id") is not None else None,
            policy_note=str(data.get("policy_note", "")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "investor_id": self.investor_id,
            "lot_id": self.lot_id,
            "share_class": self.share_class,
            "flow_date": self.flow_date.isoformat(),
            "flow_type": self.flow_type.value,
            "nav_per_unit": format(self.nav_per_unit, "f"),
            "currency": self.currency,
            "amount": format(self.amount, "f") if self.amount is not None else None,
            "units": format(self.units, "f") if self.units is not None else None,
            "series_id": self.series_id,
            "policy_note": self.policy_note,
        }


@dataclass(frozen=True)
class FeeCorrection:
    correction_id: str
    target_run_id: str
    effective_date: date
    reason: str
    corrected_snapshot: NavSnapshot
    version: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "correction_id": self.correction_id,
            "target_run_id": self.target_run_id,
            "effective_date": self.effective_date.isoformat(),
            "reason": self.reason,
            "corrected_snapshot": self.corrected_snapshot.to_dict(),
            "version": self.version,
        }


@dataclass(frozen=True)
class LotFeeCalculation:
    lot_id: str
    investor_id: str
    share_class: str
    currency: str
    period_start_date: date
    valuation_date: date
    year_fraction: Decimal
    units: Decimal
    opening_nav_per_unit: Decimal
    gross_nav_per_unit: Decimal
    gross_ending_nav: Decimal
    management_fee_basis_nav: Decimal
    management_fee_amount: Decimal
    post_mgmt_nav_per_unit: Decimal
    prior_hwm: Decimal
    hurdle_nav_per_unit: Decimal
    effective_benchmark_nav_per_unit: Decimal
    eligible_gain_per_unit: Decimal
    target_cumulative_perf_fee: Decimal
    prior_accrued_perf_fee: Decimal
    performance_fee_delta: Decimal
    crystallized_performance_fee: Decimal
    ending_accrued_perf_fee: Decimal
    is_crystallization_event: bool
    crystallization_reason: str
    new_hwm: Decimal
    new_hurdle_base_nav: Decimal
    ending_net_nav: Decimal
    ending_net_nav_per_unit: Decimal
    loss_carryforward_amount: Decimal
    notes: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lot_id": self.lot_id,
            "investor_id": self.investor_id,
            "share_class": self.share_class,
            "currency": self.currency,
            "period_start_date": self.period_start_date.isoformat(),
            "valuation_date": self.valuation_date.isoformat(),
            "year_fraction": format(self.year_fraction, "f"),
            "units": format(self.units, "f"),
            "opening_nav_per_unit": format(self.opening_nav_per_unit, "f"),
            "gross_nav_per_unit": format(self.gross_nav_per_unit, "f"),
            "gross_ending_nav": format(self.gross_ending_nav, "f"),
            "management_fee_basis_nav": format(self.management_fee_basis_nav, "f"),
            "management_fee_amount": format(self.management_fee_amount, "f"),
            "post_mgmt_nav_per_unit": format(self.post_mgmt_nav_per_unit, "f"),
            "prior_hwm": format(self.prior_hwm, "f"),
            "hurdle_nav_per_unit": format(self.hurdle_nav_per_unit, "f"),
            "effective_benchmark_nav_per_unit": format(self.effective_benchmark_nav_per_unit, "f"),
            "eligible_gain_per_unit": format(self.eligible_gain_per_unit, "f"),
            "target_cumulative_perf_fee": format(self.target_cumulative_perf_fee, "f"),
            "prior_accrued_perf_fee": format(self.prior_accrued_perf_fee, "f"),
            "performance_fee_delta": format(self.performance_fee_delta, "f"),
            "crystallized_performance_fee": format(self.crystallized_performance_fee, "f"),
            "ending_accrued_perf_fee": format(self.ending_accrued_perf_fee, "f"),
            "is_crystallization_event": self.is_crystallization_event,
            "crystallization_reason": self.crystallization_reason,
            "new_hwm": format(self.new_hwm, "f"),
            "new_hurdle_base_nav": format(self.new_hurdle_base_nav, "f"),
            "ending_net_nav": format(self.ending_net_nav, "f"),
            "ending_net_nav_per_unit": format(self.ending_net_nav_per_unit, "f"),
            "loss_carryforward_amount": format(self.loss_carryforward_amount, "f"),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class JournalLine:
    account_code: str
    account_name: str
    debit: Decimal
    credit: Decimal
    currency: str
    share_class: str
    lot_id: str
    memo: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "account_code": self.account_code,
            "account_name": self.account_name,
            "debit": format(self.debit, "f"),
            "credit": format(self.credit, "f"),
            "currency": self.currency,
            "share_class": self.share_class,
            "lot_id": self.lot_id,
            "memo": self.memo,
        }


@dataclass(frozen=True)
class JournalEntry:
    entry_id: str
    run_id: str
    version: int
    posting_date: date
    valuation_date: date
    currency: str
    entry_type: JournalEntryType
    description: str
    lines: Tuple[JournalLine, ...]
    supersedes_entry_id: Optional[str] = None

    @property
    def total_debits(self) -> Decimal:
        return sum((line.debit for line in self.lines), Decimal("0"))

    @property
    def total_credits(self) -> Decimal:
        return sum((line.credit for line in self.lines), Decimal("0"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "run_id": self.run_id,
            "version": self.version,
            "posting_date": self.posting_date.isoformat(),
            "valuation_date": self.valuation_date.isoformat(),
            "currency": self.currency,
            "entry_type": self.entry_type.value,
            "description": self.description,
            "supersedes_entry_id": self.supersedes_entry_id,
            "total_debits": format(self.total_debits, "f"),
            "total_credits": format(self.total_credits, "f"),
            "lines": [line.to_dict() for line in self.lines],
        }


@dataclass(frozen=True)
class ReconciliationReport:
    reconciled: bool
    total_journal_debits: Decimal
    total_journal_credits: Decimal
    journal_imbalance: Decimal
    total_management_fee_calculated: Decimal
    total_management_fee_journaled: Decimal
    total_performance_fee_calculated: Decimal
    total_performance_fee_journaled: Decimal
    total_crystallized_perf_fee: Decimal
    total_gross_nav: Decimal
    total_net_nav: Decimal
    nav_bridge_difference: Decimal
    checks: Dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reconciled": self.reconciled,
            "total_journal_debits": format(self.total_journal_debits, "f"),
            "total_journal_credits": format(self.total_journal_credits, "f"),
            "journal_imbalance": format(self.journal_imbalance, "f"),
            "total_management_fee_calculated": format(self.total_management_fee_calculated, "f"),
            "total_management_fee_journaled": format(self.total_management_fee_journaled, "f"),
            "total_performance_fee_calculated": format(self.total_performance_fee_calculated, "f"),
            "total_performance_fee_journaled": format(self.total_performance_fee_journaled, "f"),
            "total_crystallized_perf_fee": format(self.total_crystallized_perf_fee, "f"),
            "total_gross_nav": format(self.total_gross_nav, "f"),
            "total_net_nav": format(self.total_net_nav, "f"),
            "nav_bridge_difference": format(self.nav_bridge_difference, "f"),
            "checks": dict(self.checks),
        }


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    run_id: str
    status: ApprovalStatus
    actor: str
    decided_at: str
    comment: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "run_id": self.run_id,
            "status": self.status.value,
            "actor": self.actor,
            "decided_at": self.decided_at,
            "comment": self.comment,
        }


@dataclass(frozen=True)
class CalculationRunResult:
    run_id: str
    version: int
    agreement_id: str
    share_class: str
    currency: str
    valuation_date: date
    period_start_date: date
    config_version: str
    input_hash: str
    output_hash: str
    lot_calculations: Tuple[LotFeeCalculation, ...]
    updated_lots: Tuple[InvestorLot, ...]
    journal_entries: Tuple[JournalEntry, ...]
    reconciliation: ReconciliationReport
    diagnostics: Dict[str, Any]
    status: ApprovalStatus = ApprovalStatus.DRAFT
    supersedes_run_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "version": self.version,
            "agreement_id": self.agreement_id,
            "share_class": self.share_class,
            "currency": self.currency,
            "valuation_date": self.valuation_date.isoformat(),
            "period_start_date": self.period_start_date.isoformat(),
            "config_version": self.config_version,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "status": self.status.value,
            "supersedes_run_id": self.supersedes_run_id,
            "lot_calculations": [c.to_dict() for c in self.lot_calculations],
            "updated_lots": [lot.to_dict() for lot in self.updated_lots],
            "journal_entries": [entry.to_dict() for entry in self.journal_entries],
            "reconciliation": self.reconciliation.to_dict(),
            "diagnostics": dict(self.diagnostics),
        }
