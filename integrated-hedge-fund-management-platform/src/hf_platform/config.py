"""Versioned configuration loader (no secrets stored in config files)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ExecutionControlsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    order_execution_enabled: bool = False
    live_capital_permitted: bool = False
    policy_reference: str = "POL-EXEC-001-DISABLED-BY-DEFAULT"


class CloseControlsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    cutoff_utc_hour: int = Field(default=21, ge=0, le=23)
    cutoff_utc_minute: int = Field(default=0, ge=0, le=59)
    max_mark_age_hours: int = Field(default=24, ge=1)
    require_zero_unresolved_critical_breaks: bool = True
    require_zero_missing_marks: bool = True
    require_dual_control_close_signoff: bool = True
    allow_late_fill_auto_post_after_freeze: bool = False


class ReconciliationTolerancesConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    quantity_tolerance: Decimal = Decimal("0.0001")
    price_tolerance_bps: Decimal = Decimal("5.0")
    cash_tolerance_usd: Decimal = Decimal("10.00")
    material_break_usd: Decimal = Decimal("1000.00")


class RiskLimitsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_gross_leverage: Decimal = Decimal("3.00")
    max_net_leverage: Decimal = Decimal("1.50")
    max_single_name_concentration_pct: Decimal = Decimal("25.0")
    max_sector_concentration_pct: Decimal = Decimal("55.0")
    max_counterparty_exposure_pct: Decimal = Decimal("75.0")
    max_daily_var_95_pct_nav: Decimal = Decimal("3.50")


class FeeScheduleConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    annual_management_fee_bps: Decimal = Decimal("150")
    day_count_basis: int = Field(default=365, ge=360, le=366)


class StorageConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    backend: str = "sqlite"
    sqlite_path: str = "data/runtime/hf_platform.db"


class PlatformConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    config_version: str = "1.0.0"
    environment: str = "local-prototype"
    base_currency: str = "USD"
    execution_controls: ExecutionControlsConfig = Field(default_factory=ExecutionControlsConfig)
    close_controls: CloseControlsConfig = Field(default_factory=CloseControlsConfig)
    reconciliation_tolerances: ReconciliationTolerancesConfig = Field(
        default_factory=ReconciliationTolerancesConfig
    )
    risk_limits: RiskLimitsConfig = Field(default_factory=RiskLimitsConfig)
    fee_schedule: FeeScheduleConfig = Field(default_factory=FeeScheduleConfig)
    sla_hours: dict[str, int] = Field(
        default_factory=lambda: {"CRITICAL": 4, "HIGH": 12, "MEDIUM": 24, "LOW": 72}
    )
    storage: StorageConfig = Field(default_factory=StorageConfig)

    @classmethod
    def from_file(cls, path: str | Path) -> PlatformConfig:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(raw)
