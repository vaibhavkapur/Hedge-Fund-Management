"""Canonical domain models with explicit units, currencies, and UTC timestamps."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .enums import (
    CashFlowTiming,
    CashFlowType,
    DataStatus,
    EnvironmentMode,
    MetricFlagCode,
    ReportAudience,
    ReportStatus,
    Role,
)


@dataclass(frozen=True)
class FeeSchedule:
    management_fee_annual_bps: float = 150.0
    performance_fee_pct: float = 20.0
    hurdle_rate_annual: float = 0.0
    high_water_mark_enabled: bool = True
    accrual_basis_days: int = 252

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FeeSchedule":
        return cls(
            management_fee_annual_bps=float(data.get("management_fee_annual_bps", 150.0)),
            performance_fee_pct=float(data.get("performance_fee_pct", 20.0)),
            hurdle_rate_annual=float(data.get("hurdle_rate_annual", 0.0)),
            high_water_mark_enabled=bool(data.get("high_water_mark_enabled", True)),
            accrual_basis_days=int(data.get("accrual_basis_days", 252)),
        )


@dataclass(frozen=True)
class FundDefinition:
    fund_id: str
    name: str
    base_currency: str
    environment: EnvironmentMode
    data_status: DataStatus
    inception_date: str
    benchmark_id: str
    fee_schedule: FeeSchedule
    created_at_utc: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fund_id": self.fund_id,
            "name": self.name,
            "base_currency": self.base_currency,
            "environment": self.environment.value,
            "data_status": self.data_status.value,
            "inception_date": self.inception_date,
            "benchmark_id": self.benchmark_id,
            "fee_schedule": self.fee_schedule.to_dict(),
            "created_at_utc": self.created_at_utc,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FundDefinition":
        return cls(
            fund_id=str(data["fund_id"]),
            name=str(data["name"]),
            base_currency=str(data["base_currency"]).upper(),
            environment=EnvironmentMode(data["environment"]),
            data_status=DataStatus(data["data_status"]),
            inception_date=str(data["inception_date"]),
            benchmark_id=str(data["benchmark_id"]),
            fee_schedule=FeeSchedule.from_dict(data.get("fee_schedule", {})),
            created_at_utc=str(data["created_at_utc"]),
        )


@dataclass(frozen=True)
class CashFlowRecord:
    flow_id: str
    fund_id: str
    snapshot_id: str
    valuation_date: str
    as_of_utc: str
    flow_type: CashFlowType
    amount: float  # Positive for inflows (subscription), negative for outflows (redemption)
    currency: str
    timing: CashFlowTiming
    environment: EnvironmentMode
    investor_class: str = "CLASS_A"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "fund_id": self.fund_id,
            "snapshot_id": self.snapshot_id,
            "valuation_date": self.valuation_date,
            "as_of_utc": self.as_of_utc,
            "flow_type": self.flow_type.value,
            "amount": self.amount,
            "currency": self.currency,
            "timing": self.timing.value,
            "environment": self.environment.value,
            "investor_class": self.investor_class,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CashFlowRecord":
        return cls(
            flow_id=str(data["flow_id"]),
            fund_id=str(data["fund_id"]),
            snapshot_id=str(data["snapshot_id"]),
            valuation_date=str(data["valuation_date"]),
            as_of_utc=str(data["as_of_utc"]),
            flow_type=CashFlowType(data["flow_type"]),
            amount=float(data["amount"]),
            currency=str(data["currency"]).upper(),
            timing=CashFlowTiming(data.get("timing", "START_OF_DAY")),
            environment=EnvironmentMode(data["environment"]),
            investor_class=str(data.get("investor_class", "CLASS_A")),
        )


@dataclass(frozen=True)
class NavSnapshot:
    snapshot_id: str
    fund_id: str
    valuation_date: str
    as_of_utc: str
    availability_utc: str
    revision: int
    currency: str
    environment: EnvironmentMode
    data_status: DataStatus
    opening_nav: float
    gross_pnl: float
    management_fee: float
    performance_fee: float
    other_expenses: float
    subscriptions: float
    redemptions: float  # Stored as non-negative magnitude; subtracted in NAV bridge
    closing_nav: float
    shares_outstanding: float
    cash_balance: float
    gross_exposure: float
    net_exposure: float
    ledger_reconciled: bool
    ledger_hash: str
    provenance_source: str
    cash_flow_timing: CashFlowTiming = CashFlowTiming.START_OF_DAY

    @property
    def net_cash_flow(self) -> float:
        return self.subscriptions - self.redemptions

    @property
    def total_fees(self) -> float:
        return self.management_fee + self.performance_fee + self.other_expenses

    @property
    def net_pnl(self) -> float:
        return self.gross_pnl - self.total_fees

    @property
    def nav_per_share(self) -> float:
        return self.closing_nav / self.shares_outstanding

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "fund_id": self.fund_id,
            "valuation_date": self.valuation_date,
            "as_of_utc": self.as_of_utc,
            "availability_utc": self.availability_utc,
            "revision": self.revision,
            "currency": self.currency,
            "environment": self.environment.value,
            "data_status": self.data_status.value,
            "opening_nav": self.opening_nav,
            "gross_pnl": self.gross_pnl,
            "management_fee": self.management_fee,
            "performance_fee": self.performance_fee,
            "other_expenses": self.other_expenses,
            "subscriptions": self.subscriptions,
            "redemptions": self.redemptions,
            "closing_nav": self.closing_nav,
            "shares_outstanding": self.shares_outstanding,
            "cash_balance": self.cash_balance,
            "gross_exposure": self.gross_exposure,
            "net_exposure": self.net_exposure,
            "ledger_reconciled": self.ledger_reconciled,
            "ledger_hash": self.ledger_hash,
            "provenance_source": self.provenance_source,
            "cash_flow_timing": self.cash_flow_timing.value,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NavSnapshot":
        return cls(
            snapshot_id=str(data["snapshot_id"]),
            fund_id=str(data["fund_id"]),
            valuation_date=str(data["valuation_date"]),
            as_of_utc=str(data["as_of_utc"]),
            availability_utc=str(data["availability_utc"]),
            revision=int(data.get("revision", 1)),
            currency=str(data["currency"]).upper(),
            environment=EnvironmentMode(data["environment"]),
            data_status=DataStatus(data["data_status"]),
            opening_nav=float(data["opening_nav"]),
            gross_pnl=float(data["gross_pnl"]),
            management_fee=float(data["management_fee"]),
            performance_fee=float(data["performance_fee"]),
            other_expenses=float(data.get("other_expenses", 0.0)),
            subscriptions=float(data.get("subscriptions", 0.0)),
            redemptions=float(data.get("redemptions", 0.0)),
            closing_nav=float(data["closing_nav"]),
            shares_outstanding=float(data["shares_outstanding"]),
            cash_balance=float(data["cash_balance"]),
            gross_exposure=float(data["gross_exposure"]),
            net_exposure=float(data["net_exposure"]),
            ledger_reconciled=bool(data.get("ledger_reconciled", True)),
            ledger_hash=str(data["ledger_hash"]),
            provenance_source=str(data["provenance_source"]),
            cash_flow_timing=CashFlowTiming(data.get("cash_flow_timing", "START_OF_DAY")),
        )


@dataclass(frozen=True)
class FxRateRecord:
    rate_id: str
    valuation_date: str
    as_of_utc: str
    base_currency: str
    quote_currency: str
    rate: float  # 1 unit of base_currency = `rate` units of quote_currency
    source: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FxRateRecord":
        return cls(
            rate_id=str(data["rate_id"]),
            valuation_date=str(data["valuation_date"]),
            as_of_utc=str(data["as_of_utc"]),
            base_currency=str(data["base_currency"]).upper(),
            quote_currency=str(data["quote_currency"]).upper(),
            rate=float(data["rate"]),
            source=str(data["source"]),
        )


@dataclass(frozen=True)
class BenchmarkPoint:
    benchmark_id: str
    valuation_date: str
    currency: str
    daily_return: float
    level: float
    source: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BenchmarkPoint":
        return cls(
            benchmark_id=str(data["benchmark_id"]),
            valuation_date=str(data["valuation_date"]),
            currency=str(data["currency"]).upper(),
            daily_return=float(data["daily_return"]),
            level=float(data["level"]),
            source=str(data["source"]),
        )


@dataclass(frozen=True)
class StrategySnapshot:
    strategy_snapshot_id: str
    snapshot_id: str
    fund_id: str
    valuation_date: str
    strategy_id: str
    strategy_name: str
    asset_class: str
    currency: str
    environment: EnvironmentMode
    weight: float
    gross_pnl: float
    transaction_costs: float
    long_exposure: float
    short_exposure: float
    cash_allocation: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_snapshot_id": self.strategy_snapshot_id,
            "snapshot_id": self.snapshot_id,
            "fund_id": self.fund_id,
            "valuation_date": self.valuation_date,
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "asset_class": self.asset_class,
            "currency": self.currency,
            "environment": self.environment.value,
            "weight": self.weight,
            "gross_pnl": self.gross_pnl,
            "transaction_costs": self.transaction_costs,
            "long_exposure": self.long_exposure,
            "short_exposure": self.short_exposure,
            "cash_allocation": self.cash_allocation,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StrategySnapshot":
        return cls(
            strategy_snapshot_id=str(data["strategy_snapshot_id"]),
            snapshot_id=str(data["snapshot_id"]),
            fund_id=str(data["fund_id"]),
            valuation_date=str(data["valuation_date"]),
            strategy_id=str(data["strategy_id"]),
            strategy_name=str(data["strategy_name"]),
            asset_class=str(data["asset_class"]),
            currency=str(data["currency"]).upper(),
            environment=EnvironmentMode(data["environment"]),
            weight=float(data["weight"]),
            gross_pnl=float(data["gross_pnl"]),
            transaction_costs=float(data.get("transaction_costs", 0.0)),
            long_exposure=float(data["long_exposure"]),
            short_exposure=float(data["short_exposure"]),
            cash_allocation=float(data.get("cash_allocation", 0.0)),
        )


@dataclass(frozen=True)
class MetricFlag:
    code: MetricFlagCode
    metric: str
    message: str
    severity: str = "WARNING"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code.value,
            "metric": self.metric,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass(frozen=True)
class DisclosureBlock:
    environment: EnvironmentMode
    data_status: DataStatus
    methodology_version: str
    banner: str
    is_simulated_or_paper: bool
    fee_disclosure: str
    currency_disclosure: str
    audit_disclosure: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "environment": self.environment.value,
            "data_status": self.data_status.value,
            "methodology_version": self.methodology_version,
            "banner": self.banner,
            "is_simulated_or_paper": self.is_simulated_or_paper,
            "fee_disclosure": self.fee_disclosure,
            "currency_disclosure": self.currency_disclosure,
            "audit_disclosure": self.audit_disclosure,
        }


@dataclass(frozen=True)
class ReportRecord:
    version_id: str
    report_series_id: str
    fund_id: str
    revision_number: int
    supersedes_version_id: Optional[str]
    revision_reason: Optional[str]
    audience: ReportAudience
    reporting_currency: str
    period_start: str
    period_end: str
    as_of_utc: str
    environment: EnvironmentMode
    data_status: DataStatus
    methodology_version: str
    status: ReportStatus
    created_by: str
    reviewed_by: Optional[str]
    approved_by: Optional[str]
    published_at_utc: Optional[str]
    content_sha256: str
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_id": self.version_id,
            "report_series_id": self.report_series_id,
            "fund_id": self.fund_id,
            "revision_number": self.revision_number,
            "supersedes_version_id": self.supersedes_version_id,
            "revision_reason": self.revision_reason,
            "audience": self.audience.value,
            "reporting_currency": self.reporting_currency,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "as_of_utc": self.as_of_utc,
            "environment": self.environment.value,
            "data_status": self.data_status.value,
            "methodology_version": self.methodology_version,
            "status": self.status.value,
            "created_by": self.created_by,
            "reviewed_by": self.reviewed_by,
            "approved_by": self.approved_by,
            "published_at_utc": self.published_at_utc,
            "content_sha256": self.content_sha256,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class AuditEvent:
    audit_id: str
    timestamp_utc: str
    actor_id: str
    actor_role: Role
    action: str
    resource_type: str
    resource_id: str
    outcome: str  # ALLOW | DENY | ERROR
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "timestamp_utc": self.timestamp_utc,
            "actor_id": self.actor_id,
            "actor_role": self.actor_role.value,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "outcome": self.outcome,
            "details": self.details,
        }
