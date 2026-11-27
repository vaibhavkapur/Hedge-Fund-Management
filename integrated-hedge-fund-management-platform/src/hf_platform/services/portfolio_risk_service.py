"""Consolidated multi-currency portfolio valuation, risk analytics, attribution, and snapshot freezing."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from hf_platform.config import PlatformConfig
from hf_platform.domain import (
    AssetClass,
    CashBalance,
    Currency,
    EventType,
    FxRate,
    MarketMark,
    PortfolioSnapshot,
    Position,
    RiskLimitBreach,
    RiskMetrics,
    StrategyAttribution,
)
from hf_platform.errors import MissingMarketDataError, NotFoundError, SnapshotImmutableError
from hf_platform.services.audit_service import AuditService
from hf_platform.services.ledger_service import LedgerService, q2
from hf_platform.services.master_service import MasterDataService
from hf_platform.storage.repository import StorageRepository

Q4 = Decimal("0.0001")
SQRT_252 = Decimal(str(round(math.sqrt(252.0), 8)))
Z_95 = Decimal("1.64485")


def q4(val: Decimal) -> Decimal:
    return val.quantize(Q4, rounding=ROUND_HALF_UP)


class PortfolioRiskService:
    """Computes consolidated holdings, exposure, concentration, parametric VaR, attribution, and frozen snapshots."""

    def __init__(
        self,
        store: StorageRepository,
        master: MasterDataService,
        ledger: LedgerService,
        audit: AuditService,
        config: PlatformConfig,
    ) -> None:
        self._store = store
        self._master = master
        self._ledger = ledger
        self._audit = audit
        self._config = config

    def compute_open_position_lots(
        self,
        fund_id: str,
        business_date: date,
    ) -> dict[tuple[str, str, str], dict[str, Decimal]]:
        """Build deterministic weighted-average cost positions from non-late fills up to business_date."""
        fills = [
            f
            for f in self._store.list_fills(fund_id, up_to_date=business_date)
            if not f.is_late or (f.deferred_to_date is not None and f.deferred_to_date <= business_date)
        ]
        lots: dict[tuple[str, str, str], dict[str, Decimal]] = {}
        for fill in fills:
            key = (fill.account_id, fill.strategy_id, fill.instrument_id)
            lot = lots.setdefault(
                key,
                {
                    "quantity": Decimal("0"),
                    "avg_cost": Decimal("0"),
                    "realized_pnl_local": Decimal("0"),
                    "commissions_local": Decimal("0"),
                },
            )
            qty = lot["quantity"]
            avg_cost = lot["avg_cost"]
            signed_q = fill.signed_quantity
            lot["commissions_local"] += fill.commission

            if qty == Decimal("0") or (qty > 0 and signed_q > 0) or (qty < 0 and signed_q < 0):
                new_qty = qty + signed_q
                if new_qty != Decimal("0"):
                    new_cost = (abs(qty) * avg_cost + abs(signed_q) * fill.price) / abs(new_qty)
                    lot["avg_cost"] = new_cost
                lot["quantity"] = new_qty
            else:
                closing_qty = min(abs(qty), abs(signed_q))
                if qty > 0:
                    lot["realized_pnl_local"] += closing_qty * (fill.price - avg_cost)
                else:
                    lot["realized_pnl_local"] += closing_qty * (avg_cost - fill.price)
                new_qty = qty + signed_q
                if new_qty == Decimal("0"):
                    lot["avg_cost"] = Decimal("0")
                elif (new_qty > 0 and signed_q > 0) or (new_qty < 0 and signed_q < 0):
                    lot["avg_cost"] = fill.price
                lot["quantity"] = new_qty
        return lots

    def validate_marks_and_fx(
        self,
        *,
        fund_id: str,
        business_date: date,
        marks: list[MarketMark],
        fx_rates: list[FxRate],
    ) -> tuple[dict[str, MarketMark], dict[Currency, Decimal], list[dict[str, str]]]:
        """Validate completeness and freshness of EOD marks and FX rates for all open positions."""
        lots = self.compute_open_position_lots(fund_id, business_date)
        instruments = self._master.instrument_map()
        manual_marks = {m.instrument_id: m for m in self._store.list_manual_marks(business_date)}
        mark_map: dict[str, MarketMark] = {m.instrument_id: m for m in marks}
        mark_map.update(manual_marks)

        fx_map: dict[Currency, Decimal] = {Currency.USD: Decimal("1.000000")}
        for rate in fx_rates:
            fx_map[rate.base_currency] = rate.rate_to_usd

        cutoff_dt = datetime(
            business_date.year,
            business_date.month,
            business_date.day,
            self._config.close_controls.cutoff_utc_hour,
            self._config.close_controls.cutoff_utc_minute,
            tzinfo=UTC,
        )
        max_age_secs = self._config.close_controls.max_mark_age_hours * 3600

        issues: list[dict[str, str]] = []
        open_instruments = {inst_id for (_, _, inst_id), lot in lots.items() if lot["quantity"] != Decimal("0")}
        needed_ccys: set[Currency] = {Currency.USD}

        for acct in self._master.list_accounts(fund_id):
            needed_ccys.update(acct.initial_cash.keys())

        for inst_id in sorted(open_instruments):
            inst = instruments[inst_id]
            needed_ccys.add(inst.currency)
            mark = mark_map.get(inst_id)
            if mark is None:
                issues.append(
                    {
                        "type": "MISSING_MARK",
                        "instrument_id": inst_id,
                        "symbol": inst.symbol,
                        "reason": f"Missing EOD market mark for open position {inst.symbol} ({inst_id})",
                    }
                )
            else:
                age_secs = (cutoff_dt - mark.as_of_utc).total_seconds()
                if age_secs > max_age_secs and not mark.is_manual_override:
                    issues.append(
                        {
                            "type": "STALE_MARK",
                            "instrument_id": inst_id,
                            "symbol": inst.symbol,
                            "reason": f"Stale mark for {inst.symbol}: as_of={mark.as_of_utc.isoformat()} exceeds {self._config.close_controls.max_mark_age_hours}h limit",
                        }
                    )

        for ccy in sorted(needed_ccys, key=lambda c: c.value):
            if ccy not in fx_map:
                issues.append(
                    {
                        "type": "MISSING_FX",
                        "instrument_id": ccy.value,
                        "symbol": f"{ccy.value}/USD",
                        "reason": f"Missing FX rate to USD for currency {ccy.value}",
                    }
                )

        return mark_map, fx_map, issues

    def build_snapshot(
        self,
        *,
        fund_id: str,
        business_date: date,
        marks: list[MarketMark],
        fx_rates: list[FxRate],
        actor_id: str,
        post_ledger_eod: bool = True,
    ) -> PortfolioSnapshot:
        """Build a consolidated portfolio, risk, and attribution snapshot and reconcile against the ledger."""
        fund = self._master.get_fund(fund_id)
        instruments = self._master.instrument_map()
        accounts = self._master.account_map(fund_id)
        strategies = self._master.strategy_map(fund_id)
        counterparties = self._master.counterparty_map()

        mark_map, fx_map, issues = self.validate_marks_and_fx(
            fund_id=fund_id,
            business_date=business_date,
            marks=marks,
            fx_rates=fx_rates,
        )
        if issues:
            raise MissingMarketDataError(
                f"Cannot compute portfolio snapshot for {fund_id} on {business_date.isoformat()}: {len(issues)} market data issue(s)",
                details={"issues": issues},
            )

        # Determine prior NAV (latest frozen snapshot strictly before business_date, else initial NAV)
        prior_snaps = [
            s for s in self._store.list_snapshots(fund_id) if s.business_date < business_date and s.is_frozen
        ]
        prior_nav_usd = prior_snaps[-1].nav_usd if prior_snaps else fund.initial_nav_usd

        lots = self.compute_open_position_lots(fund_id, business_date)
        raw_positions: list[Position] = []
        mv_by_currency: dict[Currency, Decimal] = defaultdict(lambda: Decimal("0.00"))
        strat_unrealized: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        strat_realized: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        strat_comm: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        strat_gross: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        strat_net: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))

        total_long_mv = Decimal("0.00")
        total_short_mv = Decimal("0.00")

        for (acct_id, strat_id, inst_id), lot in sorted(lots.items()):
            inst = instruments[inst_id]
            fx = fx_map[inst.currency]
            strat_comm[strat_id] += q2(lot["commissions_local"] * fx)
            strat_realized[strat_id] += q2(lot["realized_pnl_local"] * fx)
            qty = lot["quantity"]
            if qty == Decimal("0"):
                continue
            mark = mark_map[inst_id]
            avg_cost = lot["avg_cost"]
            cost_basis_local = q2(qty * avg_cost * inst.multiplier)
            mv_local = q2(qty * mark.price * inst.multiplier)
            mv_usd = q2(mv_local * fx)
            unreal_local = q2(mv_local - cost_basis_local)
            unreal_usd = q2(unreal_local * fx)

            mv_by_currency[inst.currency] += mv_local
            if mv_usd >= Decimal("0"):
                total_long_mv += mv_usd
            else:
                total_short_mv += abs(mv_usd)

            strat_unrealized[strat_id] += unreal_usd
            strat_gross[strat_id] += abs(mv_usd)
            strat_net[strat_id] += mv_usd

            raw_positions.append(
                Position(
                    account_id=acct_id,
                    strategy_id=strat_id,
                    instrument_id=inst_id,
                    symbol=inst.symbol,
                    asset_class=inst.asset_class,
                    sector=inst.sector,
                    currency=inst.currency,
                    quantity=qty,
                    avg_cost_local=q4(avg_cost),
                    mark_price_local=q4(mark.price),
                    fx_rate_to_usd=fx,
                    cost_basis_local=cost_basis_local,
                    market_value_local=mv_local,
                    market_value_usd=mv_usd,
                    unrealized_pnl_local=unreal_local,
                    unrealized_pnl_usd=unreal_usd,
                    realized_pnl_usd=q2(lot["realized_pnl_local"] * fx),
                )
            )

        # Daily management fee accrual
        daily_fee_usd = q2(
            prior_nav_usd
            * self._config.fee_schedule.annual_management_fee_bps
            / Decimal("10000")
            / Decimal(str(self._config.fee_schedule.day_count_basis))
        )
        if post_ledger_eod:
            self._ledger.post_eod_mtm_and_fees(
                fund_id=fund_id,
                business_date=business_date,
                target_mv_by_currency=mv_by_currency,
                daily_management_fee_usd=daily_fee_usd,
                actor_id=actor_id,
            )

        # Cash balances from ledger
        ledger_cash = self._ledger.cash_balances_by_account_currency(fund_id, business_date)
        cash_balances: list[CashBalance] = []
        total_cash_usd = Decimal("0.00")
        for (acct_id, ccy), bal_local in sorted(ledger_cash.items(), key=lambda kv: (kv[0][0], kv[0][1].value)):
            fx = fx_map[ccy]
            bal_usd = q2(bal_local * fx)
            total_cash_usd += bal_usd
            cash_balances.append(
                CashBalance(
                    account_id=acct_id,
                    currency=ccy,
                    balance_local=bal_local,
                    fx_rate_to_usd=fx,
                    balance_usd=bal_usd,
                )
            )

        accrued_fees = self._ledger.accrued_fees_usd(fund_id, business_date)
        net_mv_usd = q2(total_long_mv - total_short_mv)
        nav_usd = q2(total_cash_usd + net_mv_usd - accrued_fees)
        nav_per_share = q4(nav_usd / fund.shares_outstanding)
        daily_pnl_usd = q2(nav_usd - prior_nav_usd)
        daily_return_pct = q4((daily_pnl_usd / prior_nav_usd) * Decimal("100")) if prior_nav_usd != 0 else Decimal("0")

        # Update position NAV weights
        positions: list[Position] = [
            p.model_copy(
                update={"weight_pct_nav": q2((abs(p.market_value_usd) / nav_usd) * Decimal("100")) if nav_usd > 0 else Decimal("0")}
            )
            for p in raw_positions
        ]

        # Risk & exposure metrics
        risk_metrics = self._compute_risk_metrics(
            positions=positions,
            cash_balances=cash_balances,
            nav_usd=nav_usd,
            instruments=instruments,
            accounts=accounts,
            counterparties=counterparties,
        )

        # Strategy attribution
        attribution: list[StrategyAttribution] = []
        for strat_id, strat in sorted(strategies.items()):
            net_pnl = q2(strat_unrealized[strat_id] + strat_realized[strat_id] - strat_comm[strat_id])
            ret_pct = (
                q4((net_pnl / strat.capital_allocation_usd) * Decimal("100"))
                if strat.capital_allocation_usd > 0
                else Decimal("0")
            )
            attribution.append(
                StrategyAttribution(
                    strategy_id=strat_id,
                    strategy_name=strat.name,
                    capital_allocation_usd=strat.capital_allocation_usd,
                    gross_exposure_usd=q2(strat_gross[strat_id]),
                    net_exposure_usd=q2(strat_net[strat_id]),
                    unrealized_pnl_usd=q2(strat_unrealized[strat_id]),
                    realized_pnl_usd=q2(strat_realized[strat_id]),
                    commissions_usd=q2(strat_comm[strat_id]),
                    net_pnl_usd=net_pnl,
                    return_on_allocated_pct=ret_pct,
                )
            )

        ledger_nav_usd = self._ledger.compute_ledger_nav_usd(fund_id, business_date, fx_map)
        nav_diff = q2(nav_usd - ledger_nav_usd)

        existing = self._store.list_snapshots(fund_id, business_date)
        version = len(existing) + 1
        snap_id = f"SNAP_{fund_id}_{business_date.strftime('%Y%m%d')}_v{version}"
        draft = PortfolioSnapshot(
            snapshot_id=snap_id,
            fund_id=fund_id,
            business_date=business_date,
            version=version,
            is_frozen=False,
            created_at_utc=datetime.now(UTC),
            prior_nav_usd=prior_nav_usd,
            total_cash_usd=q2(total_cash_usd),
            total_long_mv_usd=q2(total_long_mv),
            total_short_mv_usd=q2(total_short_mv),
            net_market_value_usd=net_mv_usd,
            accrued_fees_usd=accrued_fees,
            nav_usd=nav_usd,
            nav_per_share=nav_per_share,
            shares_outstanding=fund.shares_outstanding,
            daily_pnl_usd=daily_pnl_usd,
            daily_return_pct=daily_return_pct,
            positions=positions,
            cash_balances=cash_balances,
            risk_metrics=risk_metrics,
            strategy_attribution=attribution,
            ledger_nav_usd=ledger_nav_usd,
            nav_reconciliation_diff_usd=nav_diff,
            content_hash="",
        )
        snapshot = draft.model_copy(update={"content_hash": draft.compute_content_hash()})
        self._store.save_snapshot(snapshot)
        return snapshot

    def freeze_snapshot(self, snapshot_id: str, actor_id: str) -> PortfolioSnapshot:
        snap = self._store.get_snapshot(snapshot_id)
        if snap is None:
            raise NotFoundError(f"Snapshot not found: {snapshot_id}")
        if snap.is_frozen:
            raise SnapshotImmutableError(f"Snapshot {snapshot_id} is already frozen")
        frozen = snap.model_copy(
            update={
                "is_frozen": True,
                "frozen_at_utc": datetime.now(UTC),
                "frozen_by": actor_id,
            }
        )
        self._store.save_snapshot(frozen)
        user = self._store.get_user(actor_id)
        self._audit.record(
            actor_id=actor_id,
            actor_role=user.role if user else None,
            action="snapshot.freeze",
            entity_type="portfolio_snapshot",
            entity_id=frozen.snapshot_id,
            details={
                "fund_id": frozen.fund_id,
                "business_date": frozen.business_date.isoformat(),
                "nav_usd": str(frozen.nav_usd),
                "content_hash": frozen.content_hash,
            },
        )
        self._audit.emit_event(
            event_type=EventType.SNAPSHOT_FROZEN,
            fund_id=frozen.fund_id,
            payload={
                "snapshot_id": frozen.snapshot_id,
                "business_date": frozen.business_date.isoformat(),
                "nav_usd": str(frozen.nav_usd),
                "content_hash": frozen.content_hash,
            },
        )
        return frozen

    def _compute_risk_metrics(
        self,
        *,
        positions: list[Position],
        cash_balances: list[CashBalance],
        nav_usd: Decimal,
        instruments: dict,
        accounts: dict,
        counterparties: dict,
    ) -> RiskMetrics:
        long_exp = sum((p.market_value_usd for p in positions if p.market_value_usd > 0), Decimal("0.00"))
        short_exp = sum((abs(p.market_value_usd) for p in positions if p.market_value_usd < 0), Decimal("0.00"))
        gross_exp = q2(long_exp + short_exp)
        net_exp = q2(long_exp - short_exp)
        gross_lev = q4(gross_exp / nav_usd) if nav_usd > 0 else Decimal("0")
        net_lev = q4(net_exp / nav_usd) if nav_usd > 0 else Decimal("0")

        by_sector: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        by_asset_class: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        by_ccy: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        by_cp: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        by_symbol_abs: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))

        beta_adj = Decimal("0.00")
        variance_sum = 0.0
        stress_eq_10 = Decimal("0.00")
        stress_rates = Decimal("0.00")

        for p in positions:
            inst = instruments[p.instrument_id]
            acct = accounts[p.account_id]
            cp = counterparties[acct.counterparty_id]
            abs_mv = abs(p.market_value_usd)

            by_sector[p.sector] += p.market_value_usd
            by_asset_class[p.asset_class.value] += p.market_value_usd
            by_ccy[p.currency.value] += p.market_value_usd
            by_cp[cp.name] += abs_mv
            by_symbol_abs[p.symbol] += abs_mv

            beta_adj += p.market_value_usd * inst.beta
            daily_vol = float(inst.annualized_vol) / math.sqrt(252.0)
            variance_sum += (float(p.market_value_usd) * daily_vol) ** 2

            if p.asset_class in (AssetClass.EQUITY, AssetClass.EQUITY_ETF):
                stress_eq_10 += p.market_value_usd * inst.beta * Decimal("-0.10")
            elif p.asset_class == AssetClass.FIXED_INCOME_ETF:
                stress_rates += p.market_value_usd * Decimal("-0.04")

        for cb in cash_balances:
            acct = accounts[cb.account_id]
            cp = counterparties[acct.counterparty_id]
            by_cp[cp.name] += max(cb.balance_usd, Decimal("0.00"))

        daily_var_95 = q2(Decimal(str(math.sqrt(variance_sum))) * Z_95)
        var_pct = q4((daily_var_95 / nav_usd) * Decimal("100")) if nav_usd > 0 else Decimal("0")

        top_symbol = "NONE"
        top_pct = Decimal("0.00")
        if by_symbol_abs and nav_usd > 0:
            top_symbol, top_abs = max(by_symbol_abs.items(), key=lambda kv: kv[1])
            top_pct = q2((top_abs / nav_usd) * Decimal("100"))

        # Evaluate configured risk limits
        limits = self._config.risk_limits
        breaches: list[RiskLimitBreach] = []
        if gross_lev > limits.max_gross_leverage:
            breaches.append(
                RiskLimitBreach(
                    limit_name="max_gross_leverage",
                    observed_value=gross_lev,
                    limit_value=limits.max_gross_leverage,
                    dimension="FUND",
                    severity="HIGH",
                )
            )
        if abs(net_lev) > limits.max_net_leverage:
            breaches.append(
                RiskLimitBreach(
                    limit_name="max_net_leverage",
                    observed_value=abs(net_lev),
                    limit_value=limits.max_net_leverage,
                    dimension="FUND",
                    severity="HIGH",
                )
            )
        if top_pct > limits.max_single_name_concentration_pct:
            breaches.append(
                RiskLimitBreach(
                    limit_name="max_single_name_concentration_pct",
                    observed_value=top_pct,
                    limit_value=limits.max_single_name_concentration_pct,
                    dimension=top_symbol,
                    severity="MEDIUM",
                )
            )
        if var_pct > limits.max_daily_var_95_pct_nav:
            breaches.append(
                RiskLimitBreach(
                    limit_name="max_daily_var_95_pct_nav",
                    observed_value=var_pct,
                    limit_value=limits.max_daily_var_95_pct_nav,
                    dimension="FUND",
                    severity="HIGH",
                )
            )
        if nav_usd > 0:
            for sector, val in by_sector.items():
                sec_pct = q2((abs(val) / nav_usd) * Decimal("100"))
                if sec_pct > limits.max_sector_concentration_pct:
                    breaches.append(
                        RiskLimitBreach(
                            limit_name="max_sector_concentration_pct",
                            observed_value=sec_pct,
                            limit_value=limits.max_sector_concentration_pct,
                            dimension=sector,
                            severity="MEDIUM",
                        )
                    )

        return RiskMetrics(
            gross_exposure_usd=gross_exp,
            net_exposure_usd=net_exp,
            long_exposure_usd=q2(long_exp),
            short_exposure_usd=q2(short_exp),
            gross_leverage=gross_lev,
            net_leverage=net_lev,
            beta_adjusted_net_usd=q2(beta_adj),
            daily_var_95_usd=daily_var_95,
            daily_var_95_pct_nav=var_pct,
            stress_equity_down_10pct_usd=q2(stress_eq_10),
            stress_rates_shock_usd=q2(stress_rates),
            top_single_name_pct=top_pct,
            top_single_name_symbol=top_symbol,
            exposure_by_sector_usd={k: q2(v) for k, v in sorted(by_sector.items())},
            exposure_by_asset_class_usd={k: q2(v) for k, v in sorted(by_asset_class.items())},
            exposure_by_currency_usd={k: q2(v) for k, v in sorted(by_ccy.items())},
            exposure_by_counterparty_usd={k: q2(v) for k, v in sorted(by_cp.items())},
            limit_breaches=breaches,
        )
