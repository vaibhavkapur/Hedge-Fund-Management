"""Immutable canonical domain models with exact Decimal accounting and strict contract validation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from regulatory_reporting_engine.errors import (
    InvalidIdentifierError,
    MissingRequiredDataError,
    UnitMismatchError,
    UnsupportedInputError,
)
from regulatory_reporting_engine.identifiers import (
    validate_entity_id,
    validate_isin,
    validate_iso_country,
    validate_iso_currency,
    validate_lei,
)

SUPPORTED_CONTRACT_VERSIONS = frozenset({"1.0.0"})
SUPPORTED_ASSET_CLASSES = frozenset({"EQUITY", "FIXED_INCOME", "DERIVATIVES", "FX", "CASH", "COMMODITY"})
SUPPORTED_DIRECTIONS = frozenset({"LONG", "SHORT"})
SUPPORTED_FREQUENCIES = frozenset({"MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "ANNUAL"})
SUPPORTED_LIQUIDITY_BUCKETS = frozenset({"DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "ILLIQUID"})


class ReportStatus(str, Enum):
    DRAFT = "DRAFT"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


def _require_field(data: Dict[str, Any], key: str, path_prefix: str) -> Any:
    if key not in data or data[key] is None or (isinstance(data[key], str) and not data[key].strip()):
        raise MissingRequiredDataError(
            f"Missing required field '{path_prefix}.{key}'.",
            details={"field": f"{path_prefix}.{key}"},
        )
    return data[key]


def _parse_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise MissingRequiredDataError(
            f"Boolean value not valid for decimal field '{field_name}'.",
            details={"field": field_name, "value": value},
        )
    if isinstance(value, float):
        raise UnitMismatchError(
            f"Floating-point input rejected for exact decimal field '{field_name}'; pass decimal strings.",
            details={"field": field_name, "value": value},
        )
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise MissingRequiredDataError(
            f"Invalid decimal value for '{field_name}': {value!r}",
            details={"field": field_name, "value": value},
        ) from exc
    if not dec.is_finite():
        raise MissingRequiredDataError(
            f"Non-finite decimal value for '{field_name}': {value!r}",
            details={"field": field_name, "value": value},
        )
    return dec


def _parse_iso_date(value: Any, field_name: str) -> date:
    if not isinstance(value, str):
        raise MissingRequiredDataError(
            f"Expected ISO date string (YYYY-MM-DD) for '{field_name}', got {type(value).__name__}.",
            details={"field": field_name},
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidIdentifierError(
            f"Invalid ISO business date for '{field_name}': {value!r}",
            details={"field": field_name, "value": value},
        ) from exc


def _parse_utc_timestamp(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise InvalidIdentifierError(
            f"Timestamp '{field_name}' must be an ISO-8601 UTC string ending in 'Z': {value!r}",
            details={"field": field_name, "value": value},
        )
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidIdentifierError(
            f"Invalid ISO-8601 UTC timestamp for '{field_name}': {value!r}",
            details={"field": field_name, "value": value},
        ) from exc
    return value


def to_minor_units(amount: Decimal, scale: int = 100) -> int:
    """Convert an exact Decimal monetary amount to integer minor units (e.g. cents)."""
    return int((amount * Decimal(scale)).to_integral_exact())


def canonical_json_sha256(obj: Any) -> str:
    """Compute a deterministic SHA-256 hash over canonical sorted JSON."""
    serialized = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FundMetadata:
    fund_id: str
    fund_name: str
    fund_lei: str
    manager_lei: str
    base_currency: str
    domicile_country: str
    liquidity_bucket_primary: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "FundMetadata":
        fund_id = validate_entity_id(str(_require_field(raw, "fund_id", "fund")), field_name="fund.fund_id")
        fund_name = str(_require_field(raw, "fund_name", "fund"))
        fund_lei = validate_lei(str(_require_field(raw, "fund_lei", "fund")), field_name="fund.fund_lei")
        manager_lei = validate_lei(str(_require_field(raw, "manager_lei", "fund")), field_name="fund.manager_lei")
        base_currency = validate_iso_currency(
            str(_require_field(raw, "base_currency", "fund")), field_name="fund.base_currency"
        )
        domicile_country = validate_iso_country(
            str(_require_field(raw, "domicile_country", "fund")), field_name="fund.domicile_country"
        )
        liq = raw.get("liquidity_bucket_primary")
        if liq is not None:
            liq_str = str(liq)
            if liq_str not in SUPPORTED_LIQUIDITY_BUCKETS:
                raise UnsupportedInputError(
                    f"Unsupported liquidity bucket '{liq_str}'.",
                    details={"field": "fund.liquidity_bucket_primary", "value": liq_str},
                )
            liq = liq_str
        return cls(
            fund_id=fund_id,
            fund_name=fund_name,
            fund_lei=fund_lei,
            manager_lei=manager_lei,
            base_currency=base_currency,
            domicile_country=domicile_country,
            liquidity_bucket_primary=liq,
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "fund_id": self.fund_id,
            "fund_name": self.fund_name,
            "fund_lei": self.fund_lei,
            "manager_lei": self.manager_lei,
            "base_currency": self.base_currency,
            "domicile_country": self.domicile_country,
        }
        if self.liquidity_bucket_primary is not None:
            out["liquidity_bucket_primary"] = self.liquidity_bucket_primary
        return out


@dataclass(frozen=True)
class ReportingPeriod:
    period_id: str
    start_date: date
    end_date: date
    frequency: str

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ReportingPeriod":
        period_id = validate_entity_id(
            str(_require_field(raw, "period_id", "reporting_period")),
            field_name="reporting_period.period_id",
        )
        start_date = _parse_iso_date(_require_field(raw, "start_date", "reporting_period"), "reporting_period.start_date")
        end_date = _parse_iso_date(_require_field(raw, "end_date", "reporting_period"), "reporting_period.end_date")
        if end_date < start_date:
            raise InvalidIdentifierError(
                f"Reporting period end_date ({end_date}) precedes start_date ({start_date}).",
                details={"start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            )
        frequency = str(_require_field(raw, "frequency", "reporting_period"))
        if frequency not in SUPPORTED_FREQUENCIES:
            raise UnsupportedInputError(
                f"Unsupported reporting frequency '{frequency}'.",
                details={"field": "reporting_period.frequency", "value": frequency},
            )
        return cls(period_id=period_id, start_date=start_date, end_date=end_date, frequency=frequency)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "period_id": self.period_id,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "frequency": self.frequency,
        }


@dataclass(frozen=True)
class NavRecord:
    nav_record_id: str
    as_of_date: date
    currency: str
    gross_asset_value: Decimal
    total_liabilities: Decimal
    net_asset_value: Decimal
    cash_and_equivalents: Optional[Decimal] = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], *, expected_currency: str, expected_as_of: date) -> "NavRecord":
        nav_record_id = validate_entity_id(
            str(_require_field(raw, "nav_record_id", "nav")), field_name="nav.nav_record_id"
        )
        as_of_date = _parse_iso_date(_require_field(raw, "as_of_date", "nav"), "nav.as_of_date")
        if as_of_date != expected_as_of:
            raise UnitMismatchError(
                f"NAV as_of_date ({as_of_date}) does not match reporting_period.end_date ({expected_as_of}).",
                details={"nav_as_of_date": as_of_date.isoformat(), "period_end": expected_as_of.isoformat()},
            )
        currency = validate_iso_currency(str(_require_field(raw, "currency", "nav")), field_name="nav.currency")
        if currency != expected_currency:
            raise UnitMismatchError(
                f"NAV currency ({currency}) does not match fund base_currency ({expected_currency}).",
                details={"nav_currency": currency, "base_currency": expected_currency},
            )
        gav = _parse_decimal(_require_field(raw, "gross_asset_value", "nav"), "nav.gross_asset_value")
        liab = _parse_decimal(_require_field(raw, "total_liabilities", "nav"), "nav.total_liabilities")
        nav = _parse_decimal(_require_field(raw, "net_asset_value", "nav"), "nav.net_asset_value")
        if gav - liab != nav:
            raise UnitMismatchError(
                f"NAV identity violated: gross_asset_value ({gav}) - total_liabilities ({liab}) != net_asset_value ({nav}).",
                details={"gav": str(gav), "liabilities": str(liab), "nav": str(nav)},
            )
        cash_raw = raw.get("cash_and_equivalents")
        cash = _parse_decimal(cash_raw, "nav.cash_and_equivalents") if cash_raw is not None else None
        return cls(
            nav_record_id=nav_record_id,
            as_of_date=as_of_date,
            currency=currency,
            gross_asset_value=gav,
            total_liabilities=liab,
            net_asset_value=nav,
            cash_and_equivalents=cash,
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "nav_record_id": self.nav_record_id,
            "as_of_date": self.as_of_date.isoformat(),
            "currency": self.currency,
            "gross_asset_value": str(self.gross_asset_value),
            "total_liabilities": str(self.total_liabilities),
            "net_asset_value": str(self.net_asset_value),
        }
        if self.cash_and_equivalents is not None:
            out["cash_and_equivalents"] = str(self.cash_and_equivalents)
        return out


@dataclass(frozen=True)
class PositionRecord:
    position_id: str
    instrument_isin: str
    asset_class: str
    direction: str
    quantity: Decimal
    price_local: Decimal
    local_currency: str
    fx_rate_to_base: Decimal
    market_value_base: Decimal

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], index: int) -> "PositionRecord":
        prefix = f"positions[{index}]"
        pos_id = validate_entity_id(str(_require_field(raw, "position_id", prefix)), field_name=f"{prefix}.position_id")
        isin = validate_isin(str(_require_field(raw, "instrument_isin", prefix)), field_name=f"{prefix}.instrument_isin")
        asset_class = str(_require_field(raw, "asset_class", prefix))
        if asset_class not in SUPPORTED_ASSET_CLASSES:
            raise UnsupportedInputError(
                f"Unsupported asset_class '{asset_class}' at {prefix}.",
                details={"field": f"{prefix}.asset_class", "value": asset_class},
            )
        direction = str(_require_field(raw, "direction", prefix))
        if direction not in SUPPORTED_DIRECTIONS:
            raise UnsupportedInputError(
                f"Unsupported direction '{direction}' at {prefix}.",
                details={"field": f"{prefix}.direction", "value": direction},
            )
        quantity = _parse_decimal(_require_field(raw, "quantity", prefix), f"{prefix}.quantity")
        price_local = _parse_decimal(_require_field(raw, "price_local", prefix), f"{prefix}.price_local")
        local_ccy = validate_iso_currency(
            str(_require_field(raw, "local_currency", prefix)), field_name=f"{prefix}.local_currency"
        )
        fx_rate = _parse_decimal(_require_field(raw, "fx_rate_to_base", prefix), f"{prefix}.fx_rate_to_base")
        if fx_rate <= Decimal("0"):
            raise UnitMismatchError(
                f"FX rate must be positive at {prefix}.fx_rate_to_base.",
                details={"field": f"{prefix}.fx_rate_to_base", "value": str(fx_rate)},
            )
        mv_base = _parse_decimal(_require_field(raw, "market_value_base", prefix), f"{prefix}.market_value_base")
        expected_mv = (quantity * price_local * fx_rate).quantize(Decimal("0.01"))
        if abs(expected_mv - mv_base) > Decimal("0.01"):
            raise UnitMismatchError(
                f"Position market_value_base ({mv_base}) inconsistent with quantity * price_local * fx_rate_to_base ({expected_mv}) at {prefix}.",
                details={"position_id": pos_id, "reported_mv": str(mv_base), "computed_mv": str(expected_mv)},
            )
        if direction == "LONG" and mv_base < Decimal("0"):
            raise UnitMismatchError(
                f"LONG position {pos_id} cannot have negative market_value_base ({mv_base}).",
                details={"position_id": pos_id, "direction": direction, "market_value_base": str(mv_base)},
            )
        if direction == "SHORT" and mv_base > Decimal("0"):
            raise UnitMismatchError(
                f"SHORT position {pos_id} cannot have positive market_value_base ({mv_base}).",
                details={"position_id": pos_id, "direction": direction, "market_value_base": str(mv_base)},
            )
        return cls(
            position_id=pos_id,
            instrument_isin=isin,
            asset_class=asset_class,
            direction=direction,
            quantity=quantity,
            price_local=price_local,
            local_currency=local_ccy,
            fx_rate_to_base=fx_rate,
            market_value_base=mv_base,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "position_id": self.position_id,
            "instrument_isin": self.instrument_isin,
            "asset_class": self.asset_class,
            "direction": self.direction,
            "quantity": str(self.quantity),
            "price_local": str(self.price_local),
            "local_currency": self.local_currency,
            "fx_rate_to_base": str(self.fx_rate_to_base),
            "market_value_base": str(self.market_value_base),
        }


@dataclass(frozen=True)
class ExposureRecord:
    exposure_id: str
    asset_class: str
    counterparty_lei: str
    currency: str
    long_exposure_base: Decimal
    short_exposure_base: Decimal
    gross_exposure_base: Decimal
    net_exposure_base: Decimal

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], index: int, *, expected_currency: str) -> "ExposureRecord":
        prefix = f"exposures[{index}]"
        exp_id = validate_entity_id(str(_require_field(raw, "exposure_id", prefix)), field_name=f"{prefix}.exposure_id")
        asset_class = str(_require_field(raw, "asset_class", prefix))
        if asset_class not in SUPPORTED_ASSET_CLASSES:
            raise UnsupportedInputError(
                f"Unsupported asset_class '{asset_class}' at {prefix}.",
                details={"field": f"{prefix}.asset_class", "value": asset_class},
            )
        cp_lei = validate_lei(
            str(_require_field(raw, "counterparty_lei", prefix)), field_name=f"{prefix}.counterparty_lei"
        )
        ccy = validate_iso_currency(str(_require_field(raw, "currency", prefix)), field_name=f"{prefix}.currency")
        if ccy != expected_currency:
            raise UnitMismatchError(
                f"Exposure currency ({ccy}) at {prefix} does not match fund base_currency ({expected_currency}).",
                details={"exposure_id": exp_id, "currency": ccy, "base_currency": expected_currency},
            )
        long_exp = _parse_decimal(_require_field(raw, "long_exposure_base", prefix), f"{prefix}.long_exposure_base")
        short_exp = _parse_decimal(_require_field(raw, "short_exposure_base", prefix), f"{prefix}.short_exposure_base")
        gross_exp = _parse_decimal(_require_field(raw, "gross_exposure_base", prefix), f"{prefix}.gross_exposure_base")
        net_exp = _parse_decimal(_require_field(raw, "net_exposure_base", prefix), f"{prefix}.net_exposure_base")

        if long_exp < Decimal("0") or short_exp < Decimal("0") or gross_exp < Decimal("0"):
            raise UnitMismatchError(
                f"Long, short, and gross exposure magnitudes must be non-negative at {prefix}.",
                details={"exposure_id": exp_id},
            )
        if long_exp + short_exp != gross_exp:
            raise UnitMismatchError(
                f"Exposure gross identity violated at {exp_id}: {long_exp} + {short_exp} != {gross_exp}.",
                details={"exposure_id": exp_id, "long": str(long_exp), "short": str(short_exp), "gross": str(gross_exp)},
            )
        if long_exp - short_exp != net_exp:
            raise UnitMismatchError(
                f"Exposure net identity violated at {exp_id}: {long_exp} - {short_exp} != {net_exp}.",
                details={"exposure_id": exp_id, "long": str(long_exp), "short": str(short_exp), "net": str(net_exp)},
            )
        return cls(
            exposure_id=exp_id,
            asset_class=asset_class,
            counterparty_lei=cp_lei,
            currency=ccy,
            long_exposure_base=long_exp,
            short_exposure_base=short_exp,
            gross_exposure_base=gross_exp,
            net_exposure_base=net_exp,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exposure_id": self.exposure_id,
            "asset_class": self.asset_class,
            "counterparty_lei": self.counterparty_lei,
            "currency": self.currency,
            "long_exposure_base": str(self.long_exposure_base),
            "short_exposure_base": str(self.short_exposure_base),
            "gross_exposure_base": str(self.gross_exposure_base),
            "net_exposure_base": str(self.net_exposure_base),
        }


@dataclass(frozen=True)
class GovernedSnapshot:
    snapshot_id: str
    schema_contract_version: str
    captured_at_utc: str
    provenance: Dict[str, Any]
    fund: FundMetadata
    reporting_period: ReportingPeriod
    nav: NavRecord
    positions: Tuple[PositionRecord, ...]
    exposures: Tuple[ExposureRecord, ...]
    payload_sha256: str

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "GovernedSnapshot":
        if not isinstance(raw, dict):
            raise MissingRequiredDataError("Snapshot payload must be a JSON object.")

        snapshot_id = validate_entity_id(
            str(_require_field(raw, "snapshot_id", "snapshot")), field_name="snapshot.snapshot_id"
        )
        contract_ver = str(_require_field(raw, "schema_contract_version", "snapshot"))
        if contract_ver not in SUPPORTED_CONTRACT_VERSIONS:
            raise UnsupportedInputError(
                f"Unsupported snapshot schema_contract_version '{contract_ver}'.",
                details={"schema_contract_version": contract_ver, "supported": sorted(SUPPORTED_CONTRACT_VERSIONS)},
            )
        captured_at_utc = _parse_utc_timestamp(
            _require_field(raw, "captured_at_utc", "snapshot"), "snapshot.captured_at_utc"
        )
        provenance = raw.get("provenance")
        if not isinstance(provenance, dict):
            raise MissingRequiredDataError("Missing required object 'snapshot.provenance'.")

        fund_raw = _require_field(raw, "fund", "snapshot")
        period_raw = _require_field(raw, "reporting_period", "snapshot")
        nav_raw = _require_field(raw, "nav", "snapshot")
        if not isinstance(fund_raw, dict) or not isinstance(period_raw, dict) or not isinstance(nav_raw, dict):
            raise MissingRequiredDataError("'fund', 'reporting_period', and 'nav' must be JSON objects.")

        fund = FundMetadata.from_dict(fund_raw)
        period = ReportingPeriod.from_dict(period_raw)
        nav = NavRecord.from_dict(nav_raw, expected_currency=fund.base_currency, expected_as_of=period.end_date)

        positions_raw = raw.get("positions")
        exposures_raw = raw.get("exposures")
        if not isinstance(positions_raw, list) or not isinstance(exposures_raw, list):
            raise MissingRequiredDataError("'positions' and 'exposures' must be JSON arrays.")

        positions = tuple(PositionRecord.from_dict(item, i) for i, item in enumerate(positions_raw))
        seen_pos: set[str] = set()
        for p in positions:
            if p.position_id in seen_pos:
                raise InvalidIdentifierError(
                    f"Duplicate position_id '{p.position_id}' in snapshot {snapshot_id}.",
                    details={"position_id": p.position_id},
                )
            seen_pos.add(p.position_id)

        exposures = tuple(
            ExposureRecord.from_dict(item, i, expected_currency=fund.base_currency)
            for i, item in enumerate(exposures_raw)
        )
        seen_exp: set[str] = set()
        for e in exposures:
            if e.exposure_id in seen_exp:
                raise InvalidIdentifierError(
                    f"Duplicate exposure_id '{e.exposure_id}' in snapshot {snapshot_id}.",
                    details={"exposure_id": e.exposure_id},
                )
            seen_exp.add(e.exposure_id)

        canonical_dict = {
            "snapshot_id": snapshot_id,
            "schema_contract_version": contract_ver,
            "captured_at_utc": captured_at_utc,
            "provenance": provenance,
            "fund": fund.to_dict(),
            "reporting_period": period.to_dict(),
            "nav": nav.to_dict(),
            "positions": [p.to_dict() for p in positions],
            "exposures": [e.to_dict() for e in exposures],
        }
        payload_hash = canonical_json_sha256(canonical_dict)

        return cls(
            snapshot_id=snapshot_id,
            schema_contract_version=contract_ver,
            captured_at_utc=captured_at_utc,
            provenance=dict(provenance),
            fund=fund,
            reporting_period=period,
            nav=nav,
            positions=positions,
            exposures=exposures,
            payload_sha256=payload_hash,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "schema_contract_version": self.schema_contract_version,
            "captured_at_utc": self.captured_at_utc,
            "provenance": self.provenance,
            "fund": self.fund.to_dict(),
            "reporting_period": self.reporting_period.to_dict(),
            "nav": self.nav.to_dict(),
            "positions": [p.to_dict() for p in self.positions],
            "exposures": [e.to_dict() for e in self.exposures],
        }


@dataclass(frozen=True)
class FieldLineage:
    target_field: str
    value: Any
    unit: str
    source_type: str
    mapping_id: str
    mapping_version: str
    snapshot_id: str
    source_record_ids: Tuple[str, ...]
    derivation_expression: str
    upstream_fields: Tuple[str, ...] = ()
    rounding_mode: Optional[str] = None
    decimal_places: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_field": self.target_field,
            "value": str(self.value) if isinstance(self.value, Decimal) else self.value,
            "unit": self.unit,
            "source_type": self.source_type,
            "mapping_id": self.mapping_id,
            "mapping_version": self.mapping_version,
            "snapshot_id": self.snapshot_id,
            "source_record_ids": list(self.source_record_ids),
            "derivation_expression": self.derivation_expression,
            "upstream_fields": list(self.upstream_fields),
            "rounding_mode": self.rounding_mode,
            "decimal_places": self.decimal_places,
        }


@dataclass(frozen=True)
class ReportArtifact:
    report_id: str
    filing_key: str
    version_number: int
    supersedes_report_id: Optional[str]
    correction_reason: Optional[str]
    snapshot_id: str
    snapshot_sha256: str
    profile_id: str
    schema_version: str
    mapping_id: str
    mapping_version: str
    status: ReportStatus
    watermark: str
    fields: Dict[str, Any]
    lineage: Dict[str, FieldLineage]
    xml_payload: str
    csv_payload: str
    content_sha256: str
    created_at_utc: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "filing_key": self.filing_key,
            "version_number": self.version_number,
            "supersedes_report_id": self.supersedes_report_id,
            "correction_reason": self.correction_reason,
            "snapshot_id": self.snapshot_id,
            "snapshot_sha256": self.snapshot_sha256,
            "profile_id": self.profile_id,
            "schema_version": self.schema_version,
            "mapping_id": self.mapping_id,
            "mapping_version": self.mapping_version,
            "status": self.status.value,
            "watermark": self.watermark,
            "fields": {
                k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.fields.items()
            },
            "lineage": {k: v.to_dict() for k, v in self.lineage.items()},
            "content_sha256": self.content_sha256,
            "created_at_utc": self.created_at_utc,
        }


@dataclass(frozen=True)
class ApprovalAuditEntry:
    audit_id: int
    report_id: str
    from_status: str
    to_status: str
    actor: str
    rationale: str
    event_sha256: str
    prev_event_sha256: Optional[str]
    recorded_at_utc: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "report_id": self.report_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "actor": self.actor,
            "rationale": self.rationale,
            "event_sha256": self.event_sha256,
            "prev_event_sha256": self.prev_event_sha256,
            "recorded_at_utc": self.recorded_at_utc,
        }
