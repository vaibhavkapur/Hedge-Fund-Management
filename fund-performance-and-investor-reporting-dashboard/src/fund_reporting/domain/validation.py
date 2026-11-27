"""Domain validation, ledger reconciliation, and fail-closed invariants."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from typing import Iterable, List, Optional, Sequence

from .enums import EnvironmentMode
from .errors import (
    MissingNavError,
    MixedEnvironmentError,
    ReconciliationError,
    ValidationError,
)
from .models import NavSnapshot

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def utc_now_iso() -> str:
    """Return current UTC timestamp in canonical ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_currency_code(currency: str, field_name: str = "currency") -> str:
    code = currency.strip().upper()
    if not _CURRENCY_RE.match(code):
        raise ValidationError(
            f"Invalid ISO-4217 currency code for {field_name}: {currency!r}",
            details={"field": field_name, "value": currency},
        )
    return code


def validate_date_str(value: str, field_name: str = "valuation_date") -> date:
    if not _DATE_RE.match(value):
        raise ValidationError(
            f"Invalid ISO date for {field_name}: {value!r} (expected YYYY-MM-DD)",
            details={"field": field_name, "value": value},
        )
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(
            f"Unparseable date for {field_name}: {value!r}",
            details={"field": field_name, "value": value},
        ) from exc


def validate_utc_timestamp(value: str, field_name: str = "as_of_utc") -> datetime:
    if not value or ("Z" not in value and "+00:00" not in value):
        raise ValidationError(
            f"Timestamp {field_name} must be explicit UTC (ending in Z or +00:00): {value!r}",
            details={"field": field_name, "value": value},
        )
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError(
            f"Unparseable UTC timestamp for {field_name}: {value!r}",
            details={"field": field_name, "value": value},
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValidationError(
            f"Timestamp {field_name} must have zero UTC offset: {value!r}",
            details={"field": field_name, "value": value},
        )
    return parsed


def compute_ledger_hash(snapshot: NavSnapshot) -> str:
    """Compute a deterministic SHA-256 digest of canonical ledger fields."""
    canonical = {
        "fund_id": snapshot.fund_id,
        "valuation_date": snapshot.valuation_date,
        "revision": snapshot.revision,
        "currency": snapshot.currency,
        "environment": snapshot.environment.value,
        "opening_nav": round(snapshot.opening_nav, 6),
        "gross_pnl": round(snapshot.gross_pnl, 6),
        "management_fee": round(snapshot.management_fee, 6),
        "performance_fee": round(snapshot.performance_fee, 6),
        "other_expenses": round(snapshot.other_expenses, 6),
        "subscriptions": round(snapshot.subscriptions, 6),
        "redemptions": round(snapshot.redemptions, 6),
        "closing_nav": round(snapshot.closing_nav, 6),
        "shares_outstanding": round(snapshot.shares_outstanding, 6),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_nav_snapshot(
    snapshot: NavSnapshot,
    *,
    tolerance_abs: float = 0.01,
    tolerance_rel: float = 1e-6,
    expected_environment: Optional[EnvironmentMode] = None,
) -> None:
    """Validate structural integrity, finiteness, UTC timestamps, and NAV ledger bridge."""
    validate_date_str(snapshot.valuation_date, "valuation_date")
    as_of = validate_utc_timestamp(snapshot.as_of_utc, "as_of_utc")
    avail = validate_utc_timestamp(snapshot.availability_utc, "availability_utc")
    if avail < as_of:
        raise ValidationError(
            "availability_utc cannot precede as_of_utc (point-in-time violation)",
            details={
                "snapshot_id": snapshot.snapshot_id,
                "as_of_utc": snapshot.as_of_utc,
                "availability_utc": snapshot.availability_utc,
            },
        )
    validate_currency_code(snapshot.currency, "currency")

    numeric_fields = {
        "opening_nav": snapshot.opening_nav,
        "gross_pnl": snapshot.gross_pnl,
        "management_fee": snapshot.management_fee,
        "performance_fee": snapshot.performance_fee,
        "other_expenses": snapshot.other_expenses,
        "subscriptions": snapshot.subscriptions,
        "redemptions": snapshot.redemptions,
        "closing_nav": snapshot.closing_nav,
        "shares_outstanding": snapshot.shares_outstanding,
        "cash_balance": snapshot.cash_balance,
        "gross_exposure": snapshot.gross_exposure,
        "net_exposure": snapshot.net_exposure,
    }
    for name, val in numeric_fields.items():
        if not math.isfinite(val):
            raise ValidationError(
                f"Non-finite numeric value for {name}",
                details={"snapshot_id": snapshot.snapshot_id, "field": name, "value": str(val)},
            )

    if snapshot.opening_nav <= 0.0:
        raise MissingNavError(
            f"opening_nav must be positive for snapshot {snapshot.snapshot_id}",
            details={"snapshot_id": snapshot.snapshot_id, "opening_nav": snapshot.opening_nav},
        )
    if snapshot.closing_nav <= 0.0:
        raise MissingNavError(
            f"closing_nav must be positive for snapshot {snapshot.snapshot_id}",
            details={"snapshot_id": snapshot.snapshot_id, "closing_nav": snapshot.closing_nav},
        )
    if snapshot.shares_outstanding <= 0.0:
        raise ValidationError(
            "shares_outstanding must be positive",
            details={"snapshot_id": snapshot.snapshot_id, "shares_outstanding": snapshot.shares_outstanding},
        )
    if snapshot.subscriptions < 0.0 or snapshot.redemptions < 0.0:
        raise ValidationError(
            "subscriptions and redemptions must be non-negative magnitudes",
            details={
                "snapshot_id": snapshot.snapshot_id,
                "subscriptions": snapshot.subscriptions,
                "redemptions": snapshot.redemptions,
            },
        )
    if snapshot.management_fee < 0.0 or snapshot.performance_fee < 0.0 or snapshot.other_expenses < 0.0:
        raise ValidationError(
            "fee and expense amounts must be non-negative",
            details={"snapshot_id": snapshot.snapshot_id},
        )
    if snapshot.gross_exposure < 0.0:
        raise ValidationError(
            "gross_exposure cannot be negative",
            details={"snapshot_id": snapshot.snapshot_id, "gross_exposure": snapshot.gross_exposure},
        )
    if abs(snapshot.net_exposure) > snapshot.gross_exposure + 1e-6:
        raise ValidationError(
            "abs(net_exposure) cannot exceed gross_exposure",
            details={
                "snapshot_id": snapshot.snapshot_id,
                "net_exposure": snapshot.net_exposure,
                "gross_exposure": snapshot.gross_exposure,
            },
        )

    if expected_environment is not None and snapshot.environment != expected_environment:
        raise MixedEnvironmentError(
            f"Snapshot environment {snapshot.environment.value} does not match fund environment {expected_environment.value}",
            details={
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_environment": snapshot.environment.value,
                "fund_environment": expected_environment.value,
            },
        )

    expected_closing = (
        snapshot.opening_nav
        + snapshot.net_cash_flow
        + snapshot.gross_pnl
        - snapshot.total_fees
    )
    diff = abs(snapshot.closing_nav - expected_closing)
    tol = max(tolerance_abs, abs(expected_closing) * tolerance_rel)
    if diff > tol:
        raise ReconciliationError(
            f"NAV ledger bridge failed for {snapshot.snapshot_id}: expected closing_nav={expected_closing:.4f}, got {snapshot.closing_nav:.4f} (diff={diff:.4f})",
            details={
                "snapshot_id": snapshot.snapshot_id,
                "valuation_date": snapshot.valuation_date,
                "expected_closing_nav": round(expected_closing, 6),
                "actual_closing_nav": round(snapshot.closing_nav, 6),
                "difference": round(diff, 6),
                "tolerance": tol,
            },
        )


def validate_environment_homogeneity(
    environments: Iterable[EnvironmentMode],
    context: str = "dataset",
) -> EnvironmentMode:
    """Enforce that SIMULATED, PAPER, and LIVE data are never mixed."""
    seen = {env for env in environments}
    if not seen:
        raise ValidationError(f"Empty environment set for {context}", details={"context": context})
    if len(seen) > 1:
        raise MixedEnvironmentError(
            f"Mixed environments detected in {context}: {sorted(e.value for e in seen)}. "
            "Combining SIMULATED, PAPER, and LIVE records is strictly forbidden.",
            details={"context": context, "environments": sorted(e.value for e in seen)},
        )
    return next(iter(seen))


def validate_nav_series_continuity(
    snapshots: Sequence[NavSnapshot],
    *,
    expected_dates: Optional[Sequence[str]] = None,
    tolerance_abs: float = 0.01,
    tolerance_rel: float = 1e-6,
    max_calendar_gap_days: int = 5,
) -> List[NavSnapshot]:
    """Validate ordered NAV series: homogeneous environment, no missing dates, opening == prior closing."""
    if not snapshots:
        raise MissingNavError(
            "No NAV snapshots available for requested period",
            details={"expected_dates_count": len(expected_dates) if expected_dates else 0},
        )

    validate_environment_homogeneity((s.environment for s in snapshots), context="nav_series")

    ordered = sorted(snapshots, key=lambda s: (s.valuation_date, s.revision))
    # Deduplicate by keeping latest revision per valuation_date
    latest_by_date = {}
    for snap in ordered:
        validate_nav_snapshot(snap, tolerance_abs=tolerance_abs, tolerance_rel=tolerance_rel)
        latest_by_date[snap.valuation_date] = snap

    series = [latest_by_date[d] for d in sorted(latest_by_date.keys())]

    if expected_dates is not None:
        missing = sorted(set(expected_dates) - set(latest_by_date.keys()))
        if missing:
            raise MissingNavError(
                f"Missing NAV snapshots for {len(missing)} required valuation date(s): {missing[:5]}",
                details={"missing_dates": missing, "missing_count": len(missing)},
            )

    for prev, curr in zip(series, series[1:]):
        prev_dt = date.fromisoformat(prev.valuation_date)
        curr_dt = date.fromisoformat(curr.valuation_date)
        gap_days = (curr_dt - prev_dt).days
        if gap_days > max_calendar_gap_days:
            raise MissingNavError(
                f"Missing NAV gap detected between {prev.valuation_date} and {curr.valuation_date} ({gap_days} calendar days > max {max_calendar_gap_days})",
                details={
                    "previous_date": prev.valuation_date,
                    "next_date": curr.valuation_date,
                    "calendar_gap_days": gap_days,
                    "max_calendar_gap_days": max_calendar_gap_days,
                },
            )
        # Verify opening NAV links to prior closing NAV (same currency)
        if prev.currency == curr.currency:
            roll_diff = abs(curr.opening_nav - prev.closing_nav)
            tol = max(tolerance_abs, abs(prev.closing_nav) * tolerance_rel)
            if roll_diff > tol:
                raise ReconciliationError(
                    f"NAV roll-forward break between {prev.valuation_date} (closing={prev.closing_nav:.4f}) "
                    f"and {curr.valuation_date} (opening={curr.opening_nav:.4f})",
                    details={
                        "previous_date": prev.valuation_date,
                        "previous_closing_nav": prev.closing_nav,
                        "current_date": curr.valuation_date,
                        "current_opening_nav": curr.opening_nav,
                        "difference": round(roll_diff, 6),
                    },
                )

    return series
