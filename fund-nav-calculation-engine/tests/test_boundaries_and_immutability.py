"""Boundary and failure behavior tests: flow timing, ledger reconciliation, immutability, restatements."""

from __future__ import annotations

import sqlite3
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from fund_nav_engine.calculator import calculate_nav
from fund_nav_engine.errors import (
    FlowTimingError,
    ImmutabilityViolationError,
    LedgerReconciliationError,
    RestatementError,
    ValidationError,
)
from fund_nav_engine.io_loader import load_bundle, load_config
from fund_nav_engine.models import (
    CapitalFlowRecord,
    FlowStatus,
    FlowTimingRule,
    FlowType,
    PostCutoffPolicy,
    SnapshotStatus,
)
from fund_nav_engine.service import NavCalculationService
from fund_nav_engine.store import SqliteNavStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "data" / "fixtures"
CONFIG_PATH = PROJECT_ROOT / "config" / "default_config.json"


class TestBoundariesAndImmutability(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(CONFIG_PATH)

    def test_flow_timing_pre_nav_post_cutoff_rejected(self) -> None:
        bundle = load_bundle(FIXTURES / "flow_timing_boundary.json")
        with self.assertRaises(FlowTimingError) as ctx:
            calculate_nav(bundle, self.config)
        self.assertEqual(ctx.exception.code, "NAV-3003")

    def test_flow_timing_exact_cutoff_accepted_and_post_cutoff_policies(self) -> None:
        bundle = load_bundle(FIXTURES / "flow_timing_boundary.json")
        # Exact cutoff 16:00:00Z is accepted
        at_cutoff = replace(
            bundle.capital_flows[0],
            submitted_at=datetime(2026, 3, 31, 16, 0, 0, tzinfo=timezone.utc),
        )
        # Update ledger to reflect accepted subscription
        from fund_nav_engine.models import LedgerAccountType, LedgerBalanceRecord

        ledgers = bundle.ledger_balances + (
            LedgerBalanceRecord(
                ledger_account_id="GL-SUB",
                fund_id="FUND-ALPHA-01",
                account_type=LedgerAccountType.CAPITAL_SUBSCRIPTIONS,
                balance=Decimal("10000.00"),
                currency="USD",
                as_of_date=date(2026, 3, 31),
            ),
        )
        res = calculate_nav(
            replace(bundle, capital_flows=(at_cutoff,), ledger_balances=ledgers), self.config
        )
        self.assertEqual(res.total_subscriptions, Decimal("10000.00"))
        self.assertEqual(res.diagnostics["flows_processed"], 1)

        # Post-cutoff with post_nav + reject policy raises NAV-3003
        late_post_nav = replace(bundle.capital_flows[0], timing_rule=FlowTimingRule.POST_NAV)
        cfg_reject = replace(self.config, post_cutoff_policy=PostCutoffPolicy.REJECT)
        with self.assertRaises(FlowTimingError) as ctx:
            calculate_nav(replace(bundle, capital_flows=(late_post_nav,)), cfg_reject)
        self.assertEqual(ctx.exception.code, "NAV-3003")

        # Post-cutoff with post_nav + defer policy defers cleanly
        res_defer = calculate_nav(replace(bundle, capital_flows=(late_post_nav,)), self.config)
        self.assertEqual(res_defer.total_subscriptions, Decimal("0.00"))
        self.assertEqual(res_defer.diagnostics["flows_deferred_post_cutoff"], 1)

    def test_flow_timing_future_backdated_settlement_and_over_redemption(self) -> None:
        bundle = load_bundle(FIXTURES / "flow_timing_boundary.json")

        # Future trade date
        future_flow = replace(
            bundle.capital_flows[0],
            trade_date=date(2026, 4, 1),
            settlement_date=date(2026, 4, 3),
        )
        with self.assertRaises(FlowTimingError) as ctx_fut:
            calculate_nav(replace(bundle, capital_flows=(future_flow,)), self.config)
        self.assertEqual(ctx_fut.exception.code, "NAV-3001")

        # Settlement precedes trade date
        bad_settle = replace(
            bundle.capital_flows[0],
            trade_date=date(2026, 3, 31),
            settlement_date=date(2026, 3, 30),
        )
        with self.assertRaises(FlowTimingError) as ctx_settle:
            calculate_nav(replace(bundle, capital_flows=(bad_settle,)), self.config)
        self.assertEqual(ctx_settle.exception.code, "NAV-3002")

        # Backdated trade date requires restatement of trade_date
        backdated = replace(
            bundle.capital_flows[0],
            trade_date=date(2026, 3, 30),
            settlement_date=date(2026, 4, 1),
            submitted_at=datetime(2026, 3, 30, 14, 0, tzinfo=timezone.utc),
        )
        with self.assertRaises(FlowTimingError) as ctx_back:
            calculate_nav(replace(bundle, capital_flows=(backdated,)), self.config)
        self.assertEqual(ctx_back.exception.code, "NAV-3004")

        # Over-redemption exceeding available shares
        over_red = CapitalFlowRecord(
            flow_id="FLOW-OVER-RED",
            fund_id="FUND-ALPHA-01",
            share_class_id="CLS-A-USD",
            flow_type=FlowType.REDEMPTION,
            currency="USD",
            shares=Decimal("1500"),
            trade_date=date(2026, 3, 31),
            settlement_date=date(2026, 4, 2),
            submitted_at=datetime(2026, 3, 31, 14, 0, tzinfo=timezone.utc),
            status=FlowStatus.CONFIRMED,
        )
        with self.assertRaises(FlowTimingError) as ctx_over:
            calculate_nav(replace(bundle, capital_flows=(over_red,)), self.config)
        self.assertEqual(ctx_over.exception.code, "NAV-3006")

    def test_pending_and_cancelled_flows_ignored_with_diagnostics(self) -> None:
        bundle = load_bundle(FIXTURES / "flow_timing_boundary.json")
        pending = replace(
            bundle.capital_flows[0],
            flow_id="FLOW-PEND",
            status=FlowStatus.PENDING,
            submitted_at=datetime(2026, 3, 31, 14, 0, tzinfo=timezone.utc),
        )
        cancelled = replace(
            bundle.capital_flows[0],
            flow_id="FLOW-CANC",
            status=FlowStatus.CANCELLED,
            submitted_at=datetime(2026, 3, 31, 14, 5, tzinfo=timezone.utc),
        )
        res = calculate_nav(replace(bundle, capital_flows=(pending, cancelled)), self.config)
        self.assertEqual(res.diagnostics["flows_pending"], 1)
        self.assertEqual(res.diagnostics["flows_cancelled"], 1)
        self.assertEqual(res.total_subscriptions, Decimal("0.00"))

    def test_ledger_reconciliation_break_enforced_and_reportable(self) -> None:
        bundle = load_bundle(FIXTURES / "ledger_reconciliation_break.json")

        # Required reconciliation raises structured LedgerReconciliationError
        with self.assertRaises(LedgerReconciliationError) as ctx:
            calculate_nav(bundle, self.config)
        self.assertEqual(ctx.exception.code, "NAV-4001")
        breaks = ctx.exception.details["breaks"]
        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0]["difference"], "500.00")

        # Optional reconciliation returns report showing reconciled=False
        cfg_soft = replace(self.config, require_reconciliation=False)
        res = calculate_nav(bundle, cfg_soft)
        self.assertFalse(res.reconciliation.reconciled)
        self.assertEqual(res.reconciliation.passed_checks, 0)
        self.assertEqual(res.reconciliation.total_checks, 1)

    def test_frozen_snapshot_immutability_and_audit_journal_protection(self) -> None:
        v1_bundle = load_bundle(FIXTURES / "reference_scenario.json")
        with SqliteNavStore(":memory:") as store:
            svc = NavCalculationService(store)
            draft = svc.calculate_and_store_draft(v1_bundle, self.config, actor="acct")

            # Cannot freeze directly without approval via store
            with self.assertRaises(ValidationError):
                store.freeze_snapshot(draft.snapshot_id, actor="ctrl")

            frozen = svc.approve_and_freeze(draft.snapshot_id, approver="ctrl")
            self.assertEqual(frozen.status, SnapshotStatus.FROZEN)

            # Cannot re-approve or re-freeze
            with self.assertRaises(ImmutabilityViolationError):
                store.approve_snapshot(frozen.snapshot_id, actor="ctrl")
            with self.assertRaises(ImmutabilityViolationError):
                store.freeze_snapshot(frozen.snapshot_id, actor="ctrl")

            # Direct DB mutation and deletion blocked by SQLite triggers
            with self.assertRaises(ImmutabilityViolationError) as ctx_upd:
                store.attempt_raw_mutation(frozen.snapshot_id, '{"tampered": true}')
            self.assertEqual(ctx_upd.exception.code, "NAV-5001")

            with self.assertRaises(ImmutabilityViolationError) as ctx_del:
                store.attempt_raw_delete(frozen.snapshot_id)
            self.assertEqual(ctx_del.exception.code, "NAV-5001")

            # Direct DB mutation/deletion on audit_journal blocked by triggers
            with self.assertRaises(sqlite3.DatabaseError):
                store._conn.execute("DELETE FROM audit_journal")
            with self.assertRaises(sqlite3.DatabaseError):
                store._conn.execute("UPDATE audit_journal SET actor = 'tamper'")

    def test_restatement_preconditions_and_multi_version_chain(self) -> None:
        v1_bundle = load_bundle(FIXTURES / "reference_scenario.json")
        v2_bundle = load_bundle(FIXTURES / "late_correction_bundle.json")

        with SqliteNavStore(":memory:") as store:
            svc = NavCalculationService(store)
            v1_draft = svc.calculate_and_store_draft(v1_bundle, self.config)

            # Cannot restate an unfrozen draft
            with self.assertRaises(RestatementError):
                svc.publish_restatement(
                    prior_snapshot_id=v1_draft.snapshot_id,
                    corrected_bundle=v2_bundle,
                    config=self.config,
                    reason="Premature restatement",
                )

            v1_frozen = svc.approve_and_freeze(v1_draft.snapshot_id, approver="ctrl")

            # Empty reason rejected
            with self.assertRaises(RestatementError):
                svc.publish_restatement(
                    prior_snapshot_id=v1_frozen.snapshot_id,
                    corrected_bundle=v2_bundle,
                    config=self.config,
                    reason="   ",
                )

            # Publish v2 and then v3
            v2 = svc.publish_restatement(
                prior_snapshot_id=v1_frozen.snapshot_id,
                corrected_bundle=v2_bundle,
                config=self.config,
                reason="Correction 1: equity close restatement",
            )["snapshot"]

            v3_price = replace(v2_bundle.prices[0], price_id="PX-01-CORR-V3", price=Decimal("105.00"))
            v3_bundle = replace(
                v2_bundle,
                bundle_id="BUNDLE-REF-V3",
                input_version="1.0.2",
                prices=(v3_price,) + v2_bundle.prices[1:],
            )
            v3 = svc.publish_restatement(
                prior_snapshot_id=v2.snapshot_id,
                corrected_bundle=v3_bundle,
                config=self.config,
                reason="Correction 2: final exchange settlement price",
            )["snapshot"]

            chain = store.get_restatement_chain(v3.snapshot_id)
            self.assertEqual([s.version for s in chain], [1, 2, 3])
            self.assertEqual([s.snapshot_id for s in chain], [v1_frozen.snapshot_id, v2.snapshot_id, v3.snapshot_id])
            self.assertEqual(chain[1].prior_snapshot_id, v1_frozen.snapshot_id)
            self.assertEqual(chain[2].prior_snapshot_id, v2.snapshot_id)

            latest = store.get_latest_frozen("FUND-ALPHA-01", date(2026, 3, 31))
            assert latest is not None
            self.assertEqual(latest.snapshot_id, v3.snapshot_id)


if __name__ == "__main__":
    unittest.main()
