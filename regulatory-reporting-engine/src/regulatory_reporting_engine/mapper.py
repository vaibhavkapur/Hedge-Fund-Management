"""Pure domain field mapping, aggregation, unit validation, and 100% evidence lineage builder."""

from __future__ import annotations

from decimal import (
    Decimal,
    ROUND_DOWN,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
)
from typing import Any, Dict, List, Optional, Tuple

from regulatory_reporting_engine.errors import (
    MappingError,
    MissingRequiredDataError,
    UnitMismatchError,
    UnsupportedInputError,
)
from regulatory_reporting_engine.models import FieldLineage, GovernedSnapshot
from regulatory_reporting_engine.schemas import (
    ProfileSchema,
    validate_fields_against_schema,
    validate_mapping_against_schema,
)

ROUNDING_MAP = {
    "ROUND_HALF_UP": ROUND_HALF_UP,
    "ROUND_HALF_EVEN": ROUND_HALF_EVEN,
    "ROUND_HALF_DOWN": ROUND_HALF_DOWN,
    "ROUND_DOWN": ROUND_DOWN,
    "ROUND_UP": ROUND_UP,
}


def _quantize(value: Decimal, scale: Optional[int], rounding_mode: str) -> Decimal:
    if scale is None:
        return value
    quantum = Decimal(1).scaleb(-scale)
    return value.quantize(quantum, rounding=ROUNDING_MAP[rounding_mode])


def _resolve_dot_path(snapshot: GovernedSnapshot, dot_path: str) -> Tuple[Any, Tuple[str, ...]]:
    parts = dot_path.split(".")
    if len(parts) != 2:
        raise MappingError(
            f"Unsupported source_path '{dot_path}'; expected '<section>.<attribute>'.",
            details={"source_path": dot_path},
        )
    section, attr = parts
    if section == "fund":
        val = getattr(snapshot.fund, attr, None)
        rec_id = f"fund:{snapshot.fund.fund_id}"
    elif section == "reporting_period":
        val = getattr(snapshot.reporting_period, attr, None)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        rec_id = f"period:{snapshot.reporting_period.period_id}"
    elif section == "nav":
        val = getattr(snapshot.nav, attr, None)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        rec_id = f"nav:{snapshot.nav.nav_record_id}"
    else:
        raise UnsupportedInputError(
            f"Unsupported source_path section '{section}' in '{dot_path}'.",
            details={"source_path": dot_path},
        )

    if val is None:
        raise MissingRequiredDataError(
            f"Mapped source field '{dot_path}' is missing in snapshot '{snapshot.snapshot_id}'.",
            details={"snapshot_id": snapshot.snapshot_id, "source_path": dot_path},
        )
    return val, (rec_id,)


def _matches_filter(record: Any, filt: Optional[Dict[str, Any]]) -> bool:
    if not filt:
        return True
    for k, expected in filt.items():
        if getattr(record, k, None) != expected:
            return False
    return True


def _run_aggregation(
    records: Tuple[Any, ...],
    id_attr: str,
    rule: Dict[str, Any],
    rounding_mode: str,
) -> Tuple[Any, Tuple[str, ...], str]:
    agg = str(rule.get("aggregation", ""))
    src_field = str(rule.get("source_field", ""))
    filt = rule.get("filter")
    scale = rule.get("scale")

    matched = [r for r in records if _matches_filter(r, filt)]
    record_ids = tuple(str(getattr(r, id_attr)) for r in matched)
    filter_desc = f" WHERE {filt}" if filt else ""

    if agg == "count":
        return len(matched), record_ids, f"COUNT({src_field}){filter_desc}"

    if agg == "sum":
        total = sum((Decimal(getattr(r, src_field)) for r in matched), Decimal("0"))
        val = _quantize(total, scale, rounding_mode)
        return val, record_ids, f"SUM({src_field}){filter_desc}"

    if agg == "abs_sum":
        total = sum((abs(Decimal(getattr(r, src_field))) for r in matched), Decimal("0"))
        val = _quantize(total, scale, rounding_mode)
        return val, record_ids, f"SUM(ABS({src_field})){filter_desc}"

    if agg == "max_by_group":
        group_by = str(rule.get("group_by", ""))
        if not group_by:
            raise MappingError("Aggregation 'max_by_group' requires 'group_by'.")
        grouped: Dict[str, Decimal] = {}
        grouped_ids: Dict[str, List[str]] = {}
        for r in matched:
            gkey = str(getattr(r, group_by))
            grouped[gkey] = grouped.get(gkey, Decimal("0")) + Decimal(getattr(r, src_field))
            grouped_ids.setdefault(gkey, []).append(str(getattr(r, id_attr)))
        if not grouped:
            val = _quantize(Decimal("0"), scale, rounding_mode)
            return val, (), f"MAX(SUM({src_field}) GROUP BY {group_by}){filter_desc} [empty]"
        top_group = max(sorted(grouped.keys()), key=lambda k: grouped[k])
        val = _quantize(grouped[top_group], scale, rounding_mode)
        return (
            val,
            tuple(grouped_ids[top_group]),
            f"MAX(SUM({src_field}) GROUP BY {group_by}){filter_desc} [top_group={top_group}]",
        )

    raise UnsupportedInputError(
        f"Unsupported aggregation '{agg}' for target_field '{rule.get('target_field')}'.",
        details={"aggregation": agg, "target_field": rule.get("target_field")},
    )


def map_snapshot_to_profile(
    snapshot: GovernedSnapshot,
    schema: ProfileSchema,
    mapping: Dict[str, Any],
    *,
    default_rounding_mode: str = "ROUND_HALF_UP",
) -> Tuple[Dict[str, Any], Dict[str, FieldLineage]]:
    """
    Pure domain transformation: map a GovernedSnapshot into a validated profile field dictionary
    with complete field-level evidence lineage.
    """
    validate_mapping_against_schema(mapping, schema)

    if schema.required_currency and snapshot.fund.base_currency != schema.required_currency:
        raise UnitMismatchError(
            f"Snapshot base_currency '{snapshot.fund.base_currency}' does not match "
            f"profile '{schema.profile_key}' required_currency '{schema.required_currency}'.",
            details={
                "snapshot_currency": snapshot.fund.base_currency,
                "required_currency": schema.required_currency,
                "profile": schema.profile_key,
            },
        )

    rounding_mode = str(mapping.get("rounding_mode", default_rounding_mode))
    if rounding_mode not in ROUNDING_MAP:
        raise UnsupportedInputError(f"Unsupported rounding_mode '{rounding_mode}' in mapping.")

    mapping_id = str(mapping["mapping_id"])
    mapping_version = str(mapping["mapping_version"])
    spec_map = schema.field_map()

    fields: Dict[str, Any] = {}
    lineage: Dict[str, FieldLineage] = {}

    # Pass 1: direct metadata, nav, positions_agg, exposures_agg
    derived_rules: List[Dict[str, Any]] = []
    for rule in mapping["rules"]:
        src_type = str(rule["source_type"])
        target = str(rule["target_field"])
        spec = spec_map[target]
        scale = rule.get("scale", spec.scale)

        if src_type == "derived":
            derived_rules.append(rule)
            continue

        if src_type in ("metadata", "nav"):
            raw_val, rec_ids = _resolve_dot_path(snapshot, str(rule["source_path"]))
            val = _quantize(raw_val, scale, rounding_mode) if isinstance(raw_val, Decimal) else raw_val
            expr = f"DIRECT({rule['source_path']})"
            fields[target] = val
            lineage[target] = FieldLineage(
                target_field=target,
                value=val,
                unit=spec.unit,
                source_type=src_type,
                mapping_id=mapping_id,
                mapping_version=mapping_version,
                snapshot_id=snapshot.snapshot_id,
                source_record_ids=rec_ids,
                derivation_expression=expr,
                rounding_mode=rounding_mode if isinstance(val, Decimal) else None,
                decimal_places=scale if isinstance(val, Decimal) else None,
            )
        elif src_type == "positions_agg":
            val, rec_ids, expr = _run_aggregation(snapshot.positions, "position_id", rule, rounding_mode)
            fields[target] = val
            lineage[target] = FieldLineage(
                target_field=target,
                value=val,
                unit=spec.unit,
                source_type=src_type,
                mapping_id=mapping_id,
                mapping_version=mapping_version,
                snapshot_id=snapshot.snapshot_id,
                source_record_ids=rec_ids,
                derivation_expression=expr,
                rounding_mode=rounding_mode if isinstance(val, Decimal) else None,
                decimal_places=scale if isinstance(val, Decimal) else None,
            )
        elif src_type == "exposures_agg":
            val, rec_ids, expr = _run_aggregation(snapshot.exposures, "exposure_id", rule, rounding_mode)
            fields[target] = val
            lineage[target] = FieldLineage(
                target_field=target,
                value=val,
                unit=spec.unit,
                source_type=src_type,
                mapping_id=mapping_id,
                mapping_version=mapping_version,
                snapshot_id=snapshot.snapshot_id,
                source_record_ids=rec_ids,
                derivation_expression=expr,
                rounding_mode=rounding_mode if isinstance(val, Decimal) else None,
                decimal_places=scale if isinstance(val, Decimal) else None,
            )
        else:
            raise UnsupportedInputError(
                f"Unsupported mapping source_type '{src_type}' on target_field '{target}'.",
                details={"source_type": src_type, "target_field": target},
            )

    # Pass 2: derived fields referencing upstream mapped fields
    for rule in derived_rules:
        target = str(rule["target_field"])
        spec = spec_map[target]
        formula = str(rule.get("formula", ""))
        scale = rule.get("scale", spec.scale)

        if formula in ("ratio", "percent_ratio"):
            num_field = str(rule["numerator_field"])
            den_field = str(rule["denominator_field"])
            if num_field not in fields or den_field not in fields:
                raise MappingError(
                    f"Derived field '{target}' references unresolved upstream fields '{num_field}' / '{den_field}'.",
                    details={"target_field": target, "numerator_field": num_field, "denominator_field": den_field},
                )
            num_val = Decimal(fields[num_field])
            den_val = Decimal(fields[den_field])
            multiplier = Decimal("100") if formula == "percent_ratio" else Decimal("1")
            if den_val == Decimal("0"):
                zero_fallback = Decimal(str(rule.get("zero_denominator_value", "0")))
                val = _quantize(zero_fallback, scale, rounding_mode)
                expr = f"{formula.upper()}({num_field}/{den_field}) [zero_denominator_fallback={val}]"
            else:
                val = _quantize((num_val / den_val) * multiplier, scale, rounding_mode)
                expr = (
                    f"({num_field} / {den_field}) * 100"
                    if formula == "percent_ratio"
                    else f"{num_field} / {den_field}"
                )

            # Propagate union of upstream source record IDs deterministically
            upstream_records = sorted(
                set(lineage[num_field].source_record_ids) | set(lineage[den_field].source_record_ids)
            )
            fields[target] = val
            lineage[target] = FieldLineage(
                target_field=target,
                value=val,
                unit=spec.unit,
                source_type="derived",
                mapping_id=mapping_id,
                mapping_version=mapping_version,
                snapshot_id=snapshot.snapshot_id,
                source_record_ids=tuple(upstream_records),
                derivation_expression=expr,
                upstream_fields=(num_field, den_field),
                rounding_mode=rounding_mode,
                decimal_places=scale,
            )
        else:
            raise UnsupportedInputError(
                f"Unsupported derived formula '{formula}' on target_field '{target}'.",
                details={"formula": formula, "target_field": target},
            )

    # Verify 100% lineage coverage invariant
    missing_lineage = sorted(set(fields.keys()) - set(lineage.keys()))
    if missing_lineage:
        raise MappingError(
            f"Lineage completeness invariant violated; untraced fields: {missing_lineage}",
            details={"untraced_fields": missing_lineage},
        )

    # Reorder fields to match schema declaration order deterministically
    ordered_fields = {spec.field_id: fields[spec.field_id] for spec in schema.fields if spec.field_id in fields}
    ordered_lineage = {spec.field_id: lineage[spec.field_id] for spec in schema.fields if spec.field_id in lineage}

    # Validate final mapped values against the profile schema
    validate_fields_against_schema(ordered_fields, schema)

    return ordered_fields, ordered_lineage
