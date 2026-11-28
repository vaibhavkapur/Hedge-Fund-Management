"""Integration and resilience tests: zero shares, class-level allocation, multi-currency, adapters."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fund_nav_engine.adapters import (
    DictCapitalFlowAdapter,
    DictFeeAccrualAdapter,
    RegulatoryReportingNavPublisher,
)
from fund_nav_engine.calculator import calculate_nav
from fund_nav_engine.io_loader import load_bundle, load_config
from fund_nav_engine.models import (
    AccrualDirection,
    AccrualRecord,
    AccrualType,
    InstrumentType,
    LedgerAccountType,
    LedgerBalanceRecord,
    PositionRecord,
    ShareClassRecord,
    ValuationInputBundle,
)
from fund_nav_engine.service import NavCalculationService
from fund_nav_engine.store import SqliteNavStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "data" / "fixtures"
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"


class TestIntegrationAndAllocation(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)

    def test_zero_shares_dormant_full_redemption_and_seeding(self) -> None:
        bundle = load_bundle(FIXTURES / "zero_shares_class.json")
        res = calculate_nav(bundle, self.config)

        by_cls = {sc.share_class_id: sc for sc in res.share_class_navs}

        # Active class fully redeemed -> 0 shares, 0.00 net assets, nav_per_share is None
        active = by_cls["CLS-ACTIVE-USD"]
        self.assertEqual(active.closing_shares, Decimal("0.000000"))
        self.assertEqual(active.closing_net_assets_base, Decimal("0.00"))
        self.assertIsNone(active.nav_per_share)
        self.assertTrue(active.zero_shares_flag)

        # Dormant class with 0 opening shares and no flows -> 0 allocation, nav_per_share is None
        dormant = by_cls["CLS-EMPTY-USD"]
        self.assertEqual(dormant.allocation_weight, Decimal("0.00000000"))
        self.assertEqual(dormant.closing_shares, Decimal("0.000000"))
        self.assertIsNone(dormant.nav_per_share)
        self.assertTrue(dormant.zero_shares_flag)

        # Seeded class starts at 0 shares and issues 2500 shares at launch NAV 100.00
        seeded = by_cls["CLS-SEED-USD"]
        self.assertEqual(seeded.dealing_nav_per_share, Decimal("100.000000"))
        self.assertEqual(seeded.subscription_shares, Decimal("2500.000000"))
        self.assertEqual(seeded.closing_shares, Decimal("2500.000000"))
        self.assertEqual(seeded.closing_net_assets_base, Decimal("250000.00"))
        self.assertEqual(seeded.nav_per_share, Decimal("100.000000"))
        self.assertFalse(seeded.zero_shares_flag)

        # Exact accounting identities
        self.assertEqual(res.gross_assets, Decimal("750000.00"))
        self.assertEqual(res.total_liabilities, Decimal("500000.00"))
        self.assertEqual(res.net_assets, Decimal("250000.00"))
        self.assertEqual(res.net_assets, res.gross_assets - res.total_liabilities)
        self.assertEqual(res.diagnostics["zero_share_classes"], 2)

    def test_all_classes_zero_opening_shares_with_residual_does_not_divide_by_zero(self) -> None:
        val_date = date(2026, 3, 31)
        bundle = ValuationInputBundle(
            bundle_id="ALL-ZERO",
            fund_id="FUND-ALPHA-01",
            valuation_date=val_date,
            input_version="1.0.0",
            positions=(
                PositionRecord(
                    position_id="POS-CASH",
                    fund_id="FUND-ALPHA-01",
                    instrument_id="CASH-USD",
                    instrument_type=InstrumentType.CASH,
                    quantity=Decimal("100.00"),
                    cost_basis=Decimal("100.00"),
                    currency="USD",
                    valuation_date=val_date,
                ),
            ),
            share_classes=(
                ShareClassRecord(
                    share_class_id="CLS-0",
                    fund_id="FUND-ALPHA-01",
                    name="Empty",
                    currency="USD",
                    opening_shares=Decimal("0"),
                    management_fee_bps=Decimal("0"),
                    launch_nav_per_share=Decimal("100.00"),
                ),
            ),
            ledger_balances=(
                LedgerBalanceRecord(
                    ledger_account_id="GL-CASH",
                    fund_id="FUND-ALPHA-01",
                    account_type=LedgerAccountType.CASH,
                    balance=Decimal("100.00"),
                    currency="USD",
                    as_of_date=val_date,
                ),
            ),
        )
        res = calculate_nav(bundle, self.config)
        self.assertIsNone(res.share_class_navs[0].nav_per_share)
        self.assertTrue(res.share_class_navs[0].zero_shares_flag)
        self.assertTrue(any("zero opening shares" in w for w in res.warnings))

    def test_three_way_penny_reconciliation_and_class_specific_accruals(self) -> None:
        """100.00 split equally across 3 classes must allocate 33.34 + 33.33 + 33.33 == 100.00 exactly."""
        val_date = date(2026, 3, 31)
        bundle = ValuationInputBundle(
            bundle_id="PENNY-ALLOC",
            fund_id="FUND-ALPHA-01",
            valuation_date=val_date,
            input_version="1.0.0",
            positions=(
                PositionRecord(
                    position_id="POS-CASH",
                    fund_id="FUND-ALPHA-01",
                    instrument_id="CASH-USD",
                    instrument_type=InstrumentType.CASH,
                    quantity=Decimal("100.00"),
                    cost_basis=Decimal("100.00"),
                    currency="USD",
                    valuation_date=val_date,
                ),
            ),
            accruals=(
                AccrualRecord(
                    accrual_id="ACC-FEE-C1",
                    fund_id="FUND-ALPHA-01",
                    accrual_type=AccrualType.MANAGEMENT_FEE,
                    direction=AccrualDirection.LIABILITY,
                    amount=Decimal("1.00"),
                    currency="USD",
                    valuation_date=val_date,
                    period_start=val_date,
                    period_end=val_date,
                    share_class_id="CLS-1",
                ),
            ),
            share_classes=(
                ShareClassRecord(
                    share_class_id="CLS-1",
                    fund_id="FUND-ALPHA-01",
                    name="Class 1",
                    currency="USD",
                    opening_shares=Decimal("10"),
                    management_fee_bps=Decimal("100"),
                    launch_nav_per_share=Decimal("10.00"),
                    prior_net_assets_base=Decimal("100.00"),
                ),
                ShareClassRecord(
                    share_class_id="CLS-2",
                    fund_id="FUND-ALPHA-01",
                    name="Class 2",
                    currency="USD",
                    opening_shares=Decimal("10"),
                    management_fee_bps=Decimal("0"),
                    launch_nav_per_share=Decimal("10.00"),
                    prior_net_assets_base=Decimal("100.00"),
                ),
                ShareClassRecord(
                    share_class_id="CLS-3",
                    fund_id="FUND-ALPHA-01",
                    name="Class 3",
                    currency="USD",
                    opening_shares=Decimal("10"),
                    management_fee_bps=Decimal("0"),
                    launch_nav_per_share=Decimal("10.00"),
                    prior_net_assets_base=Decimal("100.00"),
                ),
            ),
            ledger_balances=(
                LedgerBalanceRecord(
                    ledger_account_id="GL-CASH",
                    fund_id="FUND-ALPHA-01",
                    account_type=LedgerAccountType.CASH,
                    balance=Decimal("100.00"),
                    currency="USD",
                    as_of_date=val_date,
                ),
                LedgerBalanceRecord(
                    ledger_account_id="GL-ACC",
                    fund_id="FUND-ALPHA-01",
                    account_type=LedgerAccountType.ACCRUED_EXPENSES,
                    balance=Decimal("1.00"),
                    currency="USD",
                    as_of_date=val_date,
                ),
            ),
        )
        res = calculate_nav(bundle, self.config)
        self.assertEqual(res.gross_assets, Decimal("100.00"))
        self.assertEqual(res.total_liabilities, Decimal("1.00"))
        self.assertEqual(res.net_assets, Decimal("99.00"))

        allocated_sum = sum((sc.allocated_gross_assets_base for sc in res.share_class_navs), Decimal("0"))
        self.assertEqual(allocated_sum, Decimal("100.00"))

        net_sum = sum((sc.closing_net_assets_base for sc in res.share_class_navs), Decimal("0"))
        self.assertEqual(net_sum, Decimal("99.00"))

        by_cls = {sc.share_class_id: sc for sc in res.share_class_navs}
        # CLS-1 gets the deterministic penny (tie-broken by class_id) minus its $1.00 specific fee
        self.assertEqual(by_cls["CLS-1"].allocated_gross_assets_base, Decimal("33.34"))
        self.assertEqual(by_cls["CLS-1"].class_specific_liabilities_base, Decimal("1.00"))
        self.assertEqual(by_cls["CLS-1"].closing_net_assets_base, Decimal("32.34"))
        self.assertEqual(by_cls["CLS-2"].closing_net_assets_base, Decimal("33.33"))
        self.assertEqual(by_cls["CLS-3"].closing_net_assets_base, Decimal("33.33"))

    def test_integration_adapters_roundtrip(self) -> None:
        val_date = date(2026, 3, 31)
        fee_adapter = DictFeeAccrualAdapter(
            [
                {
                    "accrual_id": "FEE-ADAPT-01",
                    "fund_id": "FUND-ALPHA-01",
                    "fee_type": "management_fee",
                    "accrued_amount": "500.00",
                    "currency": "USD",
                    "valuation_date": "2026-03-31",
                    "period_start": "2026-03-01",
                    "period_end": "2026-03-31",
                    "share_class_id": "CLS-A-USD",
                }
            ]
        )
        flow_adapter = DictCapitalFlowAdapter(
            [
                {
                    "order_id": "ORD-ADAPT-01",
                    "fund_id": "FUND-ALPHA-01",
                    "share_class_id": "CLS-A-USD",
                    "order_type": "subscription",
                    "currency": "USD",
                    "cash_amount": "10000.00",
                    "trade_date": "2026-03-31",
                    "settlement_date": "2026-04-02",
                    "submitted_at": "2026-03-31T13:00:00Z",
                    "status": "confirmed",
                }
            ]
        )

        accruals = fee_adapter.fetch_accruals("FUND-ALPHA-01", val_date)
        flows = flow_adapter.fetch_capital_flows("FUND-ALPHA-01", val_date)

        bundle = ValuationInputBundle(
            bundle_id="ADAPT-BUNDLE-01",
            fund_id="FUND-ALPHA-01",
            valuation_date=val_date,
            input_version="1.0.0",
            positions=(
                PositionRecord(
                    position_id="POS-CASH",
                    fund_id="FUND-ALPHA-01",
                    instrument_id="CASH-USD",
                    instrument_type=InstrumentType.CASH,
                    quantity=Decimal("100500.00"),
                    cost_basis=Decimal("100500.00"),
                    currency="USD",
                    valuation_date=val_date,
                ),
            ),
            accruals=accruals,
            share_classes=(
                ShareClassRecord(
                    share_class_id="CLS-A-USD",
                    fund_id="FUND-ALPHA-01",
                    name="Class A USD",
                    currency="USD",
                    opening_shares=Decimal("1000"),
                    management_fee_bps=Decimal("100"),
                    launch_nav_per_share=Decimal("100.00"),
                ),
            ),
            capital_flows=flows,
            ledger_balances=(
                LedgerBalanceRecord(
                    ledger_account_id="GL-CASH",
                    fund_id="FUND-ALPHA-01",
                    account_type=LedgerAccountType.CASH,
                    balance=Decimal("100500.00"),
                    currency="USD",
                    as_of_date=val_date,
                ),
                LedgerBalanceRecord(
                    ledger_account_id="GL-ACC",
                    fund_id="FUND-ALPHA-01",
                    account_type=LedgerAccountType.ACCRUED_EXPENSES,
                    balance=Decimal("500.00"),
                    currency="USD",
                    as_of_date=val_date,
                ),
                LedgerBalanceRecord(
                    ledger_account_id="GL-SUB",
                    fund_id="FUND-ALPHA-01",
                    account_type=LedgerAccountType.CAPITAL_SUBSCRIPTIONS,
                    balance=Decimal("10000.00"),
                    currency="USD",
                    as_of_date=val_date,
                ),
            ),
        )

        with SqliteNavStore(":memory:") as store:
            svc = NavCalculationService(store)
            draft = svc.calculate_and_store_draft(bundle, self.config)
            frozen = svc.approve_and_freeze(draft.snapshot_id, approver="controller")
            published = RegulatoryReportingNavPublisher().publish_snapshot(frozen)

            self.assertEqual(published["schema_version"], "nav_pub.v1")
            self.assertEqual(published["status"], "frozen")
            self.assertEqual(published["net_assets"], "110000.00")
            self.assertEqual(published["share_classes"][0]["closing_shares"], "1100.000000")
            self.assertEqual(published["share_classes"][0]["nav_per_share"], "100.000000")


if __name__ == "__main__":
    unittest.main()
