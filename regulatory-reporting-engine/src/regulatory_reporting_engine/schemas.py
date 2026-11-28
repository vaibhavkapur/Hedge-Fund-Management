"""Jurisdiction/profile schema registry, schema evolution checker, and XML/CSV schema validators."""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from regulatory_reporting_engine.errors import (
    ConfigurationError,
    SchemaEvolutionError,
    SchemaValidationError,
    UnsupportedInputError,
)
from regulatory_reporting_engine.identifiers import is_valid_lei, ISO_4217_CURRENCIES


@dataclass(frozen=True)
class SchemaFieldSpec:
    field_id: str
    xml_tag: str
    csv_column: str
    data_type: str
    required: bool
    unit: str
    description: str
    scale: Optional[int] = None
    min_value: Optional[Decimal] = None
    max_value: Optional[Decimal] = None
    allowed_values: Tuple[str, ...] = ()
    pattern: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SchemaFieldSpec":
        min_v = Decimal(str(raw["min_value"])) if raw.get("min_value") is not None else None
        max_v = Decimal(str(raw["max_value"])) if raw.get("max_value") is not None else None
        allowed = tuple(str(x) for x in raw.get("allowed_values", []))
        return cls(
            field_id=str(raw["field_id"]),
            xml_tag=str(raw["xml_tag"]),
            csv_column=str(raw["csv_column"]),
            data_type=str(raw["data_type"]),
            required=bool(raw.get("required", True)),
            unit=str(raw.get("unit", "none")),
            description=str(raw.get("description", "")),
            scale=int(raw["scale"]) if raw.get("scale") is not None else None,
            min_value=min_v,
            max_value=max_v,
            allowed_values=allowed,
            pattern=str(raw["pattern"]) if raw.get("pattern") is not None else None,
        )


@dataclass(frozen=True)
class ProfileSchema:
    profile_id: str
    schema_version: str
    jurisdiction: str
    display_name: str
    root_xml_element: str
    xml_namespace: str
    provenance: Dict[str, Any]
    required_currency: str
    fields: Tuple[SchemaFieldSpec, ...]

    @property
    def profile_key(self) -> str:
        return f"{self.profile_id}:{self.schema_version}"

    def field_map(self) -> Dict[str, SchemaFieldSpec]:
        return {f.field_id: f for f in self.fields}

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ProfileSchema":
        provenance = raw.get("provenance")
        if not isinstance(provenance, dict) or not provenance.get("effective_date") or not provenance.get("authored_date"):
            raise ConfigurationError(
                f"Profile schema '{raw.get('profile_id')}' missing dated provenance metadata.",
                details={"profile_id": raw.get("profile_id")},
            )
        if provenance.get("certified_for_filing") is not False:
            raise ConfigurationError(
                f"Illustrative schema '{raw.get('profile_id')}' must explicitly declare certified_for_filing=false."
            )
        fields = tuple(SchemaFieldSpec.from_dict(f) for f in raw.get("fields", []))
        if not fields:
            raise ConfigurationError(f"Profile schema '{raw.get('profile_id')}' defines zero fields.")
        return cls(
            profile_id=str(raw["profile_id"]),
            schema_version=str(raw["schema_version"]),
            jurisdiction=str(raw["jurisdiction"]),
            display_name=str(raw["display_name"]),
            root_xml_element=str(raw["root_xml_element"]),
            xml_namespace=str(raw["xml_namespace"]),
            provenance=dict(provenance),
            required_currency=str(raw.get("required_currency", "USD")),
            fields=fields,
        )


@dataclass(frozen=True)
class SchemaDiff:
    profile_id: str
    from_version: str
    to_version: str
    added_fields: Tuple[str, ...]
    removed_fields: Tuple[str, ...]
    type_or_constraint_changes: Tuple[str, ...]
    is_breaking: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "added_fields": list(self.added_fields),
            "removed_fields": list(self.removed_fields),
            "type_or_constraint_changes": list(self.type_or_constraint_changes),
            "is_breaking": self.is_breaking,
        }


class SchemaRegistry:
    """Loads versioned jurisdiction/profile schemas and mapping specifications."""

    def __init__(self, profiles_dir: Path, mappings_dir: Path) -> None:
        self._schemas: Dict[Tuple[str, str], ProfileSchema] = {}
        self._mappings: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._load_all(profiles_dir, mappings_dir)

    def _load_all(self, profiles_dir: Path, mappings_dir: Path) -> None:
        if not profiles_dir.exists() or not profiles_dir.is_dir():
            raise ConfigurationError(f"Profiles directory not found: {profiles_dir}")
        if not mappings_dir.exists() or not mappings_dir.is_dir():
            raise ConfigurationError(f"Mappings directory not found: {mappings_dir}")

        for path in sorted(profiles_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            schema = ProfileSchema.from_dict(raw)
            self._schemas[(schema.profile_id, schema.schema_version)] = schema

        for path in sorted(mappings_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            key = (str(raw["target_profile_id"]), str(raw["target_schema_version"]))
            self._mappings[key] = raw

    def list_profiles(self) -> List[str]:
        return sorted(f"{pid}:{ver}" for pid, ver in self._schemas)

    def get_schema(self, profile_id: str, schema_version: str) -> ProfileSchema:
        key = (profile_id, schema_version)
        if key not in self._schemas:
            raise UnsupportedInputError(
                f"Unknown profile schema '{profile_id}:{schema_version}'.",
                details={"profile_id": profile_id, "schema_version": schema_version, "available": self.list_profiles()},
            )
        return self._schemas[key]

    def get_mapping(self, profile_id: str, schema_version: str) -> Dict[str, Any]:
        key = (profile_id, schema_version)
        if key not in self._mappings:
            raise SchemaEvolutionError(
                f"No field mapping registered for '{profile_id}:{schema_version}'.",
                details={"profile_id": profile_id, "schema_version": schema_version},
            )
        return self._mappings[key]

    def compare_schema_versions(self, profile_id: str, from_version: str, to_version: str) -> SchemaDiff:
        """Analyze differences between two schema versions of the same profile."""
        old = self.get_schema(profile_id, from_version)
        new = self.get_schema(profile_id, to_version)
        old_map = old.field_map()
        new_map = new.field_map()

        added = tuple(sorted(set(new_map) - set(old_map)))
        removed = tuple(sorted(set(old_map) - set(new_map)))
        changes: List[str] = []
        for fid in sorted(set(old_map) & set(new_map)):
            of, nf = old_map[fid], new_map[fid]
            if (of.data_type, of.unit, of.scale, of.required, of.xml_tag, of.allowed_values) != (
                nf.data_type,
                nf.unit,
                nf.scale,
                nf.required,
                nf.xml_tag,
                nf.allowed_values,
            ):
                changes.append(fid)

        added_required = any(new_map[fid].required for fid in added)
        is_breaking = bool(removed or added_required or changes)
        return SchemaDiff(
            profile_id=profile_id,
            from_version=from_version,
            to_version=to_version,
            added_fields=added,
            removed_fields=removed,
            type_or_constraint_changes=tuple(changes),
            is_breaking=is_breaking,
        )


def validate_mapping_against_schema(mapping: Dict[str, Any], schema: ProfileSchema) -> None:
    """Detect schema evolution drift between a mapping definition and target profile schema."""
    if mapping.get("target_profile_id") != schema.profile_id:
        raise SchemaEvolutionError(
            f"Mapping target_profile_id '{mapping.get('target_profile_id')}' does not match schema '{schema.profile_id}'.",
            details={"mapping_profile": mapping.get("target_profile_id"), "schema_profile": schema.profile_id},
        )
    if mapping.get("target_schema_version") != schema.schema_version:
        raise SchemaEvolutionError(
            f"Schema version mismatch: mapping targets '{mapping.get('target_schema_version')}' "
            f"but requested schema is '{schema.schema_version}'.",
            details={
                "mapping_target_schema_version": mapping.get("target_schema_version"),
                "requested_schema_version": schema.schema_version,
            },
        )
    rules = mapping.get("rules", [])
    mapped_fields = {r["target_field"] for r in rules}
    schema_fields = schema.field_map()

    missing_required = sorted(fid for fid, spec in schema_fields.items() if spec.required and fid not in mapped_fields)
    unknown_targets = sorted(mapped_fields - set(schema_fields))

    if missing_required or unknown_targets:
        raise SchemaEvolutionError(
            f"Mapping '{mapping.get('mapping_id')}:{mapping.get('mapping_version')}' is incompatible with "
            f"schema '{schema.profile_key}'.",
            details={
                "missing_required_schema_fields": missing_required,
                "unknown_mapping_target_fields": unknown_targets,
            },
        )


def _validate_single_field_value(spec: SchemaFieldSpec, raw_value: Any) -> Optional[str]:
    if raw_value is None or (isinstance(raw_value, str) and raw_value.strip() == ""):
        if spec.required:
            return f"Field '{spec.field_id}' is required but missing/empty."
        return None

    val_str = str(raw_value)

    if spec.data_type == "string":
        if spec.pattern and not re.match(spec.pattern, val_str):
            return f"Field '{spec.field_id}' value {val_str!r} fails pattern {spec.pattern!r}."
    elif spec.data_type == "lei":
        if not is_valid_lei(val_str):
            return f"Field '{spec.field_id}' value {val_str!r} is not a valid ISO 17442 LEI."
    elif spec.data_type == "date":
        try:
            date.fromisoformat(val_str)
        except ValueError:
            return f"Field '{spec.field_id}' value {val_str!r} is not a valid ISO date (YYYY-MM-DD)."
    elif spec.data_type == "iso_currency":
        if val_str not in ISO_4217_CURRENCIES:
            return f"Field '{spec.field_id}' value {val_str!r} is not a supported ISO 4217 currency."
    elif spec.data_type == "enum":
        if val_str not in spec.allowed_values:
            return f"Field '{spec.field_id}' value {val_str!r} not in allowed values {spec.allowed_values}."
    elif spec.data_type == "integer":
        if isinstance(raw_value, bool):
            return f"Field '{spec.field_id}' cannot be boolean."
        try:
            if isinstance(raw_value, str) and "." in raw_value:
                return f"Field '{spec.field_id}' expected integer, got {val_str!r}."
            iv = int(val_str)
        except ValueError:
            return f"Field '{spec.field_id}' value {val_str!r} is not an integer."
        if spec.min_value is not None and Decimal(iv) < spec.min_value:
            return f"Field '{spec.field_id}' value {iv} < min_value {spec.min_value}."
        if spec.max_value is not None and Decimal(iv) > spec.max_value:
            return f"Field '{spec.field_id}' value {iv} > max_value {spec.max_value}."
    elif spec.data_type == "decimal":
        try:
            dv = Decimal(val_str)
        except InvalidOperation:
            return f"Field '{spec.field_id}' value {val_str!r} is not a valid decimal."
        if not dv.is_finite():
            return f"Field '{spec.field_id}' value {val_str!r} is not finite."
        if spec.scale is not None:
            exp = dv.as_tuple().exponent
            if not isinstance(exp, int) or -exp != spec.scale:
                return f"Field '{spec.field_id}' value {val_str!r} does not match required scale {spec.scale}."
        if spec.min_value is not None and dv < spec.min_value:
            return f"Field '{spec.field_id}' value {dv} < min_value {spec.min_value}."
        if spec.max_value is not None and dv > spec.max_value:
            return f"Field '{spec.field_id}' value {dv} > max_value {spec.max_value}."
    else:
        return f"Unsupported schema data_type '{spec.data_type}' on field '{spec.field_id}'."

    if spec.allowed_values and val_str not in spec.allowed_values:
        return f"Field '{spec.field_id}' value {val_str!r} not in allowed values {spec.allowed_values}."

    return None


def validate_fields_against_schema(fields: Dict[str, Any], schema: ProfileSchema) -> None:
    """Validate a mapped field dictionary against a ProfileSchema."""
    errors: List[str] = []
    spec_map = schema.field_map()

    extra = sorted(set(fields) - set(spec_map))
    if extra:
        errors.append(f"Unexpected undeclared fields present: {extra}")

    for spec in schema.fields:
        err = _validate_single_field_value(spec, fields.get(spec.field_id))
        if err:
            errors.append(err)

    if errors:
        raise SchemaValidationError(
            f"Report fields failed schema validation for '{schema.profile_key}' ({len(errors)} error(s)).",
            details={"profile": schema.profile_key, "errors": errors},
        )


def validate_xml_against_schema(xml_payload: str, schema: ProfileSchema) -> None:
    """Validate an XML report string against the profile's root element, namespace, element order, and field types."""
    errors: List[str] = []
    try:
        root = ET.fromstring(xml_payload)
    except ET.ParseError as exc:
        raise SchemaValidationError(
            f"Malformed XML payload for '{schema.profile_key}': {exc}",
            details={"profile": schema.profile_key, "parse_error": str(exc)},
        ) from exc

    expected_root_tag = f"{{{schema.xml_namespace}}}{schema.root_xml_element}"
    if root.tag != expected_root_tag:
        errors.append(f"Expected root element '{expected_root_tag}', found '{root.tag}'.")

    ns_prefix = f"{{{schema.xml_namespace}}}"
    header = root.find(f"{ns_prefix}Header")
    body = root.find(f"{ns_prefix}ReportBody")
    if header is None:
        errors.append("Missing required <Header> section.")
    else:
        if header.attrib.get("certifiedForFiling") != "false":
            errors.append("Header must explicitly declare certifiedForFiling='false'.")
        if header.attrib.get("schemaVersion") != schema.schema_version:
            errors.append(
                f"Header schemaVersion '{header.attrib.get('schemaVersion')}' != '{schema.schema_version}'."
            )

    if body is None:
        errors.append("Missing required <ReportBody> section.")
    else:
        children = list(body)
        expected_tags = [f"{ns_prefix}{f.xml_tag}" for f in schema.fields]
        actual_tags = [c.tag for c in children]
        if actual_tags != expected_tags:
            errors.append(
                f"XML element sequence mismatch. Expected {[f.xml_tag for f in schema.fields]}, "
                f"got {[t.replace(ns_prefix, '') for t in actual_tags]}."
            )
        else:
            for spec, elem in zip(schema.fields, children):
                err = _validate_single_field_value(spec, elem.text)
                if err:
                    errors.append(err)

    if errors:
        raise SchemaValidationError(
            f"XML schema validation failed for '{schema.profile_key}' ({len(errors)} error(s)).",
            details={"profile": schema.profile_key, "errors": errors},
        )


def validate_csv_against_schema(csv_payload: str, schema: ProfileSchema) -> None:
    """Validate a CSV report string against the profile's column order, row count, and field types."""
    errors: List[str] = []
    reader = csv.reader(io.StringIO(csv_payload))
    rows = list(reader)
    if len(rows) < 2:
        raise SchemaValidationError(
            f"CSV payload for '{schema.profile_key}' must contain a header row and at least one data row.",
            details={"profile": schema.profile_key, "row_count": len(rows)},
        )

    header_row = rows[0]
    expected_columns = [
        "report_id",
        "version_number",
        "profile_id",
        "schema_version",
        "watermark",
    ] + [f.csv_column for f in schema.fields]

    if header_row != expected_columns:
        errors.append(f"CSV header mismatch. Expected {expected_columns}, got {header_row}.")
    else:
        data_row = rows[1]
        if len(data_row) != len(expected_columns):
            errors.append(f"CSV data row has {len(data_row)} columns; expected {len(expected_columns)}.")
        else:
            field_values = dict(zip(expected_columns[5:], data_row[5:]))
            for spec in schema.fields:
                err = _validate_single_field_value(spec, field_values.get(spec.csv_column))
                if err:
                    errors.append(err)

    if errors:
        raise SchemaValidationError(
            f"CSV schema validation failed for '{schema.profile_key}' ({len(errors)} error(s)).",
            details={"profile": schema.profile_key, "errors": errors},
        )
