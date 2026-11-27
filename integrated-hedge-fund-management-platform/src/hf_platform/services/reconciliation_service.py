"""Multi-way reconciliation engine comparing internal IBOR/ledger vs prime broker/custodian feeds."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

from hf_platform.config import PlatformConfig
from hf_platform.domain import (
    BreakType,
    Currency,
    EventType,
    ExternalCashRecord,
    ExternalFillRecord,
    ExternalPositionRecord,
    MarketMark,
    ReconciliationBreak,
    ReconciliationResult,
    Severity,
    TradeFill,
)
from hf_platform.services.audit_service import AuditService
from hf_platform.services.ledger_service import LedgerService, q2
from hf_platform.services.master_service import MasterDataService
from hf_platform.services.portfolio_risk_service import PortfolioRiskService
from hf_platform.storage.repository import StorageRepository


class ReconciliationService:
    """Reconciles internal positions, cash, and fills against external prime broker / custodian records."""

    def __init__(
        self,
        store: StorageRepository,
        master: MasterDataService,
        ledger: LedgerService,
        portfolio: PortfolioRiskService,
        audit: AuditService,
        config: PlatformConfig,
    ) -> None:
        self._store = store
        self._master = master
        self._ledger = ledger
        self._portfolio = portfolio
        self._audit = audit
        self._config = config

    def reconcile(
        self,
        *,
        fund_id: str,
        business_date: date,
        external_positions: list[ExternalPositionRecord],
        external_cash: list[ExternalCashRecord],
        external_fills: list[ExternalFillRecord],
        marks: dict[str, MarketMark],
        fx_map: dict[Currency, Decimal],
        actor_id: str,
    ) -> ReconciliationResult:
        recon_id = f"RECON_{fund_id}_{business_date.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
        now = datetime.now(UTC)
        tol = self._config.reconciliation_tolerances
        instruments = self._master.instrument_map()
        breaks: list[ReconciliationBreak] = []

        # 1. Position reconciliation (aggregated at account_id + instrument_id)
        lots = self._portfolio.compute_open_position_lots(fund_id, business_date)
        internal_pos: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
        for (acct_id, _strat_id, inst_id), lot in lots.items():
            internal_pos[(acct_id, inst_id)] += lot["quantity"]
        internal_pos = {k: v for k, v in internal_pos.items() if v != Decimal("0")}

        ext_pos_map: dict[tuple[str, str], ExternalPositionRecord] = {
            (r.account_id, r.instrument_id): r for r in external_positions
        }
        all_pos_keys = sorted(set(internal_pos.keys()) | set(ext_pos_map.keys()))

        for acct_id, inst_id in all_pos_keys:
            inst = instruments[inst_id]
            fx = fx_map.get(inst.currency, Decimal("1.0"))
            int_qty = internal_pos.get((acct_id, inst_id))
            ext_rec = ext_pos_map.get((acct_id, inst_id))

            if int_qty is not None and ext_rec is None:
                mark_px = marks[inst_id].price if inst_id in marks else Decimal("0")
                impact = q2(abs(int_qty * mark_px * inst.multiplier * fx))
                breaks.append(
                    self._make_break(
                        recon_id=recon_id,
                        fund_id=fund_id,
                        business_date=business_date,
                        account_id=acct_id,
                        break_type=BreakType.MISSING_EXTERNAL_POSITION,
                        instrument_id=inst_id,
                        currency=inst.currency,
                        internal_value=str(int_qty),
                        external_value="MISSING",
                        difference=str(int_qty),
                        impact_usd=impact,
                        description=f"Internal position {inst.symbol} qty={int_qty} not reported by prime broker",
                        now=now,
                    )
                )
            elif int_qty is None and ext_rec is not None:
                impact = q2(abs(ext_rec.quantity * ext_rec.market_price * inst.multiplier * fx))
                breaks.append(
                    self._make_break(
                        recon_id=recon_id,
                        fund_id=fund_id,
                        business_date=business_date,
                        account_id=acct_id,
                        break_type=BreakType.MISSING_INTERNAL_POSITION,
                        instrument_id=inst_id,
                        currency=inst.currency,
                        internal_value="MISSING",
                        external_value=str(ext_rec.quantity),
                        difference=str(-ext_rec.quantity),
                        impact_usd=impact,
                        description=f"Prime broker reports {inst.symbol} qty={ext_rec.quantity} absent from IBOR",
                        now=now,
                    )
                )
            elif int_qty is not None and ext_rec is not None:
                qty_diff = int_qty - ext_rec.quantity
                if abs(qty_diff) > tol.quantity_tolerance:
                    ref_px = marks[inst_id].price if inst_id in marks else ext_rec.market_price
                    impact = q2(abs(qty_diff * ref_px * inst.multiplier * fx))
                    breaks.append(
                        self._make_break(
                            recon_id=recon_id,
                            fund_id=fund_id,
                            business_date=business_date,
                            account_id=acct_id,
                            break_type=BreakType.POSITION_QUANTITY,
                            instrument_id=inst_id,
                            currency=inst.currency,
                            internal_value=str(int_qty),
                            external_value=str(ext_rec.quantity),
                            difference=str(qty_diff),
                            impact_usd=impact,
                            description=f"Quantity mismatch on {inst.symbol} in {acct_id}: IBOR={int_qty} vs PB={ext_rec.quantity}",
                            now=now,
                        )
                    )
                if inst_id in marks:
                    int_px = marks[inst_id].price
                    if int_px > 0:
                        bps_diff = abs((int_px - ext_rec.market_price) / int_px) * Decimal("10000")
                        if bps_diff > tol.price_tolerance_bps:
                            impact = q2(abs(int_qty * (int_px - ext_rec.market_price) * inst.multiplier * fx))
                            breaks.append(
                                self._make_break(
                                    recon_id=recon_id,
                                    fund_id=fund_id,
                                    business_date=business_date,
                                    account_id=acct_id,
                                    break_type=BreakType.POSITION_PRICE,
                                    instrument_id=inst_id,
                                    currency=inst.currency,
                                    internal_value=str(int_px),
                                    external_value=str(ext_rec.market_price),
                                    difference=f"{q2(bps_diff)} bps",
                                    impact_usd=impact,
                                    description=f"Price tolerance breach on {inst.symbol}: IBOR={int_px} vs PB={ext_rec.market_price}",
                                    now=now,
                                )
                            )

        # 2. Cash reconciliation (account_id + currency)
        int_cash = self._ledger.cash_balances_by_account_currency(fund_id, business_date)
        ext_cash_map = {(c.account_id, c.currency): c for c in external_cash}
        all_cash_keys = sorted(set(int_cash.keys()) | set(ext_cash_map.keys()), key=lambda k: (k[0], k[1].value))

        for acct_id, ccy in all_cash_keys:
            fx = fx_map.get(ccy, Decimal("1.0"))
            int_bal = int_cash.get((acct_id, ccy), Decimal("0.00"))
            ext_rec = ext_cash_map.get((acct_id, ccy))
            ext_bal = ext_rec.balance if ext_rec else Decimal("0.00")
            diff_local = q2(int_bal - ext_bal)
            diff_usd = q2(abs(diff_local * fx))
            if diff_usd > tol.cash_tolerance_usd:
                breaks.append(
                    self._make_break(
                        recon_id=recon_id,
                        fund_id=fund_id,
                        business_date=business_date,
                        account_id=acct_id,
                        break_type=BreakType.CASH_BALANCE,
                        currency=ccy,
                        internal_value=str(int_bal),
                        external_value=str(ext_bal),
                        difference=str(diff_local),
                        impact_usd=diff_usd,
                        description=f"Cash mismatch in {acct_id} ({ccy.value}): Ledger={int_bal} vs PB={ext_bal}",
                        now=now,
                    )
                )

        # 3. Fill reconciliation for business_date (by broker_ref)
        int_fills: list[TradeFill] = [
            f for f in self._store.list_fills(fund_id, business_date=business_date) if not f.is_late
        ]
        int_fill_map = {f.broker_ref: f for f in int_fills}
        ext_fill_map = {f.broker_ref: f for f in external_fills}
        all_refs = sorted(set(int_fill_map.keys()) | set(ext_fill_map.keys()))

        for ref in all_refs:
            int_f = int_fill_map.get(ref)
            ext_f = ext_fill_map.get(ref)
            if int_f and not ext_f:
                fx = fx_map.get(int_f.currency, Decimal("1.0"))
                impact = q2(int_f.gross_notional_local * fx)
                breaks.append(
                    self._make_break(
                        recon_id=recon_id,
                        fund_id=fund_id,
                        business_date=business_date,
                        account_id=int_f.account_id,
                        break_type=BreakType.MISSING_EXTERNAL_FILL,
                        instrument_id=int_f.instrument_id,
                        currency=int_f.currency,
                        broker_ref=ref,
                        internal_value=f"{int_f.side.value} {int_f.quantity} @ {int_f.price}",
                        external_value="MISSING",
                        difference=str(int_f.quantity),
                        impact_usd=impact,
                        description=f"Internal fill {int_f.fill_id} (broker_ref={ref}) missing from PB confirmation feed",
                        now=now,
                    )
                )
            elif ext_f and not int_f:
                fx = fx_map.get(ext_f.currency, Decimal("1.0"))
                impact = q2(ext_f.quantity * ext_f.price * fx)
                breaks.append(
                    self._make_break(
                        recon_id=recon_id,
                        fund_id=fund_id,
                        business_date=business_date,
                        account_id=ext_f.account_id,
                        break_type=BreakType.MISSING_INTERNAL_FILL,
                        instrument_id=ext_f.instrument_id,
                        currency=ext_f.currency,
                        broker_ref=ref,
                        internal_value="MISSING",
                        external_value=f"{ext_f.side.value} {ext_f.quantity} @ {ext_f.price}",
                        difference=str(-ext_f.quantity),
                        impact_usd=impact,
                        description=f"Unbooked prime broker fill {ref} ({ext_f.side.value} {ext_f.quantity} {ext_f.instrument_id})",
                        now=now,
                    )
                )
            elif int_f and ext_f:
                if abs(int_f.quantity - ext_f.quantity) > tol.quantity_tolerance or int_f.price != ext_f.price:
                    fx = fx_map.get(int_f.currency, Decimal("1.0"))
                    impact = q2(abs((int_f.quantity * int_f.price) - (ext_f.quantity * ext_f.price)) * fx)
                    breaks.append(
                        self._make_break(
                            recon_id=recon_id,
                            fund_id=fund_id,
                            business_date=business_date,
                            account_id=int_f.account_id,
                            break_type=BreakType.FILL_MISMATCH,
                            instrument_id=int_f.instrument_id,
                            currency=int_f.currency,
                            broker_ref=ref,
                            internal_value=f"{int_f.quantity} @ {int_f.price}",
                            external_value=f"{ext_f.quantity} @ {ext_f.price}",
                            difference=str(int_f.quantity - ext_f.quantity),
                            impact_usd=impact,
                            description=f"Fill terms mismatch on {ref}: IBOR={int_f.quantity}@{int_f.price} vs PB={ext_f.quantity}@{ext_f.price}",
                            now=now,
                        )
                    )

        total_impact = q2(sum((b.financial_impact_usd for b in breaks), Decimal("0.00")))
        critical_count = sum(1 for b in breaks if b.severity in (Severity.CRITICAL, Severity.HIGH))
        result = ReconciliationResult(
            reconciliation_id=recon_id,
            fund_id=fund_id,
            business_date=business_date,
            run_at_utc=now,
            positions_checked=len(all_pos_keys),
            cash_balances_checked=len(all_cash_keys),
            fills_checked=len(all_refs),
            breaks=breaks,
            total_break_impact_usd=total_impact,
            critical_break_count=critical_count,
            is_clean=len(breaks) == 0,
        )
        self._store.save_reconciliation(result)

        user = self._store.get_user(actor_id)
        self._audit.record(
            actor_id=actor_id,
            actor_role=user.role if user else None,
            action="reconciliation.run",
            entity_type="reconciliation",
            entity_id=recon_id,
            details={
                "fund_id": fund_id,
                "business_date": business_date.isoformat(),
                "break_count": len(breaks),
                "total_break_impact_usd": str(total_impact),
                "is_clean": result.is_clean,
            },
        )
        self._audit.emit_event(
            event_type=EventType.RECONCILIATION_COMPLETED,
            fund_id=fund_id,
            correlation_id=recon_id,
            payload={"reconciliation_id": recon_id, "break_count": len(breaks), "is_clean": result.is_clean},
        )
        for brk in breaks:
            self._audit.emit_event(
                event_type=EventType.BREAK_DETECTED,
                fund_id=fund_id,
                correlation_id=recon_id,
                payload={
                    "break_id": brk.break_id,
                    "break_type": brk.break_type.value,
                    "severity": brk.severity.value,
                    "impact_usd": str(brk.financial_impact_usd),
                },
            )
        return result

    def _make_break(
        self,
        *,
        recon_id: str,
        fund_id: str,
        business_date: date,
        account_id: str,
        break_type: BreakType,
        internal_value: str,
        external_value: str,
        difference: str,
        impact_usd: Decimal,
        description: str,
        now: datetime,
        instrument_id: str | None = None,
        currency: Currency | None = None,
        broker_ref: str | None = None,
    ) -> ReconciliationBreak:
        mat = self._config.reconciliation_tolerances.material_break_usd
        if impact_usd >= mat * Decimal("10"):
            severity = Severity.CRITICAL
        elif impact_usd >= mat:
            severity = Severity.HIGH
        else:
            severity = Severity.MEDIUM
        return ReconciliationBreak(
            break_id=f"BRK_{uuid.uuid4().hex[:8]}",
            reconciliation_id=recon_id,
            fund_id=fund_id,
            business_date=business_date,
            account_id=account_id,
            break_type=break_type,
            severity=severity,
            instrument_id=instrument_id,
            currency=currency,
            broker_ref=broker_ref,
            internal_value=internal_value,
            external_value=external_value,
            difference=difference,
            financial_impact_usd=impact_usd,
            description=description,
            detected_at_utc=now,
        )
