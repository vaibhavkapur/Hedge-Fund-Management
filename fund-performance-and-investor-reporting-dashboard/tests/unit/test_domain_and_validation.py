"""Unit tests for domain contracts, UTC validation, ledger bridge, and invariants."""

from __future__ import annotations

import math
import unittest

from fund_reporting.domain.enums import CashFlowTiming, DataStatus, EnvironmentMode
from fund_reporting.domain.errors import (
    MissingNavError,
    MixedEnvironmentError,
    ReconciliationError,
    ValidationError,
)
from fund_reporting.domain.models import NavSnapshot
from fund_reporting.domain.validation import (
    compute_ledger_hash,
    validate_environment_homogeneity,
    validate_nav_series_continuity,
    validate_nav_snapshot,
    validate_utc_timestamp,
)


def _make_snapshot(**overrides) -> NavSnapshot:
    base = dict(
        snapshot_id="SNAP-1",
        fund_id="FUND-1",
        valuation_date="2026-01-05",
        as_of_utc="2026-01-05T21:00:00Z",
        availability_utc="2026-01-05T22:00:00Z",
        revision=1,
        currency="USD",
        environment=EnvironmentMode.SIMULATED,
        data_status=DataStatus.SYNTHETIC,
        opening_nav=1_000_000.0,
        gross_pnl=10_000.0,
        management_fee=500.0,
        performance_fee=0.0,
        other_expenses=0.0,
        subscriptions=0.0,
        redemptions=0.0,
        closing_nav=1_009_500.0,
        shares_outstanding=10_000.0,
        cash_balance=150_000.0,
        gross_exposure=850_000.0,
        net_exposure=600_000.0,
        ledger_reconciled=True,
        ledger_hash="placeholder",
        provenance_source="UNIT_TEST",
        cash_flow_timing=CashFlowTiming.START_OF_DAY,
    )
    base.update(overrides)
    snap = NavSnapshot(**base)
    return NavSnapshot(**{**base, "ledger_hash": compute_ledger_hash(snap)})


class TestDomainAndValidation(unittest.TestCase):
    def test_valid_snapshot_passes(self) -> None:
        snap = _make_snapshot()
        validate_nav_snapshot(snap)
        self.assertEqual(len(snap.ledger_hash), 64)

    def test_rejects_non_utc_timestamp(self) -> None:
        with self.assertRaises(ValidationError):
            validate_utc_timestamp("2026-01-05T21:00:00")
        with self.assertRaises(ValidationError):
            validate_utc_timestamp("2026-01-05T21:00:00+05:30")

    def test_rejects_availability_before_as_of(self) -> None:
        snap = _make_snapshot(
            as_of_utc="2026-01-05T21:00:00Z",
            availability_utc="2026-01-05T20:00:00Z",
        )
        with self.assertRaises(ValidationError):
            validate_nav_snapshot(snap)

    def test_rejects_broken_ledger_bridge(self) -> None:
        snap = _make_snapshot(closing_nav=1_015_000.0)
        with self.assertRaises(ReconciliationError) as ctx:
            validate_nav_snapshot(snap)
        self.assertEqual(ctx.exception.code, "LEDGER_RECONCILIATION_FAILED")

    def test_rejects_non_positive_nav_and_nan(self) -> None:
        with self.assertRaises(MissingNavError):
            validate_nav_snapshot(_make_snapshot(closing_nav=0.0))
        with self.assertRaises(ValidationError):
            validate_nav_snapshot(_make_snapshot(gross_pnl=math.nan))

    def test_rejects_net_exposure_exceeding_gross(self) -> None:
        snap = _make_snapshot(gross_exposure=500_000.0, net_exposure=600_000.0)
        with self.assertRaises(ValidationError):
            validate_nav_snapshot(snap)

    def test_rejects_roll_forward_break(self) -> None:
        s1 = _make_snapshot(snapshot_id="S1", valuation_date="2026-01-05", closing_nav=1_009_500.0)
        s2 = _make_snapshot(
            snapshot_id="S2",
            valuation_date="2026-01-06",
            as_of_utc="2026-01-06T21:00:00Z",
            availability_utc="2026-01-06T22:00:00Z",
            opening_nav=1_020_000.0,  # Broken vs s1.closing_nav
            gross_pnl=5_000.0,
            management_fee=0.0,
            closing_nav=1_025_000.0,
        )
        with self.assertRaises(ReconciliationError):
            validate_nav_series_continuity([s1, s2])

    def test_environment_homogeneity_rejects_mixed(self) -> None:
        with self.assertRaises(MixedEnvironmentError):
            validate_environment_homogeneity([EnvironmentMode.PAPER, EnvironmentMode.LIVE])


if __name__ == "__main__":
    unittest.main()
