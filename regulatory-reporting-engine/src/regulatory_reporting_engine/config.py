"""Typed configuration loader with strict validation and safety invariants."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Tuple

from regulatory_reporting_engine.errors import (
    AutomatedSubmissionProhibitedError,
    ConfigurationError,
)

VALID_ROUNDING_MODES = {
    "ROUND_HALF_UP",
    "ROUND_HALF_EVEN",
    "ROUND_HALF_DOWN",
    "ROUND_DOWN",
    "ROUND_UP",
}


@dataclass(frozen=True)
class SubmissionPolicy:
    automated_submission_enabled: bool
    allow_external_transmission: bool
    require_human_approval_before_export: bool
    watermark: str


@dataclass(frozen=True)
class NumericPolicy:
    default_rounding_mode: str
    monetary_decimal_places: int
    ratio_decimal_places: int
    minor_unit_scale: int


@dataclass(frozen=True)
class ReconciliationPolicy:
    monetary_tolerance: Decimal
    ratio_tolerance: Decimal
    fail_on_mismatch: bool


@dataclass(frozen=True)
class StorageConfig:
    backend: str
    sqlite_path: Path
    postgres_ddl_path: Path


@dataclass(frozen=True)
class PathsConfig:
    profiles_dir: Path
    mappings_dir: Path
    fixtures_dir: Path
    reports_dir: Path


@dataclass(frozen=True)
class EngineConfig:
    config_version: str
    environment: str
    random_seed: int
    submission_policy: SubmissionPolicy
    numeric_policy: NumericPolicy
    reconciliation_policy: ReconciliationPolicy
    storage: StorageConfig
    paths: PathsConfig
    supported_profiles: Tuple[str, ...]
    base_dir: Path

    def to_metadata_dict(self) -> Dict[str, Any]:
        return {
            "config_version": self.config_version,
            "environment": self.environment,
            "random_seed": self.random_seed,
            "automated_submission_enabled": self.submission_policy.automated_submission_enabled,
            "default_rounding_mode": self.numeric_policy.default_rounding_mode,
            "monetary_tolerance": str(self.reconciliation_policy.monetary_tolerance),
            "ratio_tolerance": str(self.reconciliation_policy.ratio_tolerance),
            "storage_backend": self.storage.backend,
            "supported_profiles": list(self.supported_profiles),
        }


def _require_section(raw: Dict[str, Any], key: str) -> Dict[str, Any]:
    val = raw.get(key)
    if not isinstance(val, dict):
        raise ConfigurationError(
            f"Missing or invalid configuration section '{key}'.",
            details={"section": key},
        )
    return val


def _parse_decimal(raw: Any, field_name: str) -> Decimal:
    try:
        dec = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ConfigurationError(
            f"Invalid decimal value for configuration field '{field_name}': {raw!r}",
            details={"field": field_name, "value": raw},
        ) from exc
    if dec < Decimal("0"):
        raise ConfigurationError(
            f"Configuration field '{field_name}' must be non-negative.",
            details={"field": field_name, "value": str(dec)},
        )
    return dec


def _resolve_path(base_dir: Path, path_str: Any, field_name: str) -> Path:
    if not isinstance(path_str, str) or not path_str.strip():
        raise ConfigurationError(
            f"Missing or invalid path string for '{field_name}'.",
            details={"field": field_name},
        )
    p = Path(path_str)
    return p if p.is_absolute() else (base_dir / p).resolve()


def load_config(config_path: Path | str, *, base_dir: Path | str | None = None) -> EngineConfig:
    """Load and validate an EngineConfig JSON file."""
    path = Path(config_path)
    if not path.exists() or not path.is_file():
        raise ConfigurationError(
            f"Configuration file not found: {path}",
            details={"config_path": str(path)},
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"Malformed JSON in configuration file {path}: {exc}",
            details={"config_path": str(path)},
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigurationError("Configuration root must be a JSON object.")

    resolved_base = Path(base_dir).resolve() if base_dir is not None else path.resolve().parent.parent

    sub_raw = _require_section(raw, "submission_policy")
    if bool(sub_raw.get("automated_submission_enabled", False)):
        raise AutomatedSubmissionProhibitedError(
            "Automated regulatory submission is structurally prohibited in this prototype.",
            details={"field": "submission_policy.automated_submission_enabled"},
        )
    if bool(sub_raw.get("allow_external_transmission", False)):
        raise AutomatedSubmissionProhibitedError(
            "External regulatory transmission is structurally prohibited in this prototype.",
            details={"field": "submission_policy.allow_external_transmission"},
        )

    submission_policy = SubmissionPolicy(
        automated_submission_enabled=False,
        allow_external_transmission=False,
        require_human_approval_before_export=bool(
            sub_raw.get("require_human_approval_before_export", False)
        ),
        watermark=str(sub_raw.get("watermark", "ILLUSTRATIVE_DRAFT_NOT_FOR_REGULATORY_FILING")),
    )

    num_raw = _require_section(raw, "numeric_policy")
    rounding = str(num_raw.get("default_rounding_mode", ""))
    if rounding not in VALID_ROUNDING_MODES:
        raise ConfigurationError(
            f"Unsupported rounding mode '{rounding}'.",
            details={"rounding_mode": rounding, "allowed": sorted(VALID_ROUNDING_MODES)},
        )
    numeric_policy = NumericPolicy(
        default_rounding_mode=rounding,
        monetary_decimal_places=int(num_raw.get("monetary_decimal_places", 2)),
        ratio_decimal_places=int(num_raw.get("ratio_decimal_places", 6)),
        minor_unit_scale=int(num_raw.get("minor_unit_scale", 100)),
    )

    rec_raw = _require_section(raw, "reconciliation_policy")
    reconciliation_policy = ReconciliationPolicy(
        monetary_tolerance=_parse_decimal(
            rec_raw.get("monetary_tolerance", "0.01"), "reconciliation_policy.monetary_tolerance"
        ),
        ratio_tolerance=_parse_decimal(
            rec_raw.get("ratio_tolerance", "0.0001"), "reconciliation_policy.ratio_tolerance"
        ),
        fail_on_mismatch=bool(rec_raw.get("fail_on_mismatch", True)),
    )

    stor_raw = _require_section(raw, "storage")
    backend = str(stor_raw.get("backend", ""))
    if backend not in {"sqlite", "memory", "postgres"}:
        raise ConfigurationError(
            f"Unsupported storage backend '{backend}'.",
            details={"backend": backend, "supported": ["sqlite", "memory", "postgres"]},
        )
    storage = StorageConfig(
        backend=backend,
        sqlite_path=_resolve_path(resolved_base, stor_raw.get("sqlite_path"), "storage.sqlite_path"),
        postgres_ddl_path=_resolve_path(
            resolved_base, stor_raw.get("postgres_ddl_path"), "storage.postgres_ddl_path"
        ),
    )

    paths_raw = _require_section(raw, "paths")
    paths = PathsConfig(
        profiles_dir=_resolve_path(resolved_base, paths_raw.get("profiles_dir"), "paths.profiles_dir"),
        mappings_dir=_resolve_path(resolved_base, paths_raw.get("mappings_dir"), "paths.mappings_dir"),
        fixtures_dir=_resolve_path(resolved_base, paths_raw.get("fixtures_dir"), "paths.fixtures_dir"),
        reports_dir=_resolve_path(resolved_base, paths_raw.get("reports_dir"), "paths.reports_dir"),
    )

    supported = raw.get("supported_profiles")
    if not isinstance(supported, list) or not supported:
        raise ConfigurationError("Configuration must list at least one entry in 'supported_profiles'.")

    return EngineConfig(
        config_version=str(raw.get("config_version", "1.0.0")),
        environment=str(raw.get("environment", "offline_synthetic")),
        random_seed=int(raw.get("random_seed", 20260331)),
        submission_policy=submission_policy,
        numeric_policy=numeric_policy,
        reconciliation_policy=reconciliation_policy,
        storage=storage,
        paths=paths,
        supported_profiles=tuple(str(p) for p in supported),
        base_dir=resolved_base,
    )
