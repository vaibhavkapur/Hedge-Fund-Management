"""Unit tests for consolidated portfolio valuation, NAV reconciliation, risk limits, and snapshot freezing."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from hf_platform.config import PlatformConfig, RiskLimitsConfig
from hf_platform.errors import SnapshotImmutableError
from hf_platform.services.platform import HedgeFundPlatform


def test_portfolio_nav_reconciles_exactly_with_ledger(day1_closed_platform) -> None:
    platform, snap_id = day1_closed_platform
    snap = platform.store.get_snapshot(snap_id)
    assert snap is not None
    assert snap.is_frozen is True
    assert snap.nav_reconciliation_diff_usd == Decimal("0.00")
    assert snap.nav_usd == snap.ledger_nav_usd
    assert len(snap.positions) == 7
    assert len(snap.cash_balances) == 3
    assert snap.content_hash == snap.compute_content_hash()
    # Verify NAV math: total_cash + net_mv - accrued_fees == nav
    assert snap.total_cash_usd + snap.net_market_value_usd - snap.accrued_fees_usd == snap.nav_usd


def test_frozen_snapshot_cannot_be_overwritten_or_refrozen(day1_closed_platform) -> None:
    platform, snap_id = day1_closed_platform
    snap = platform.store.get_snapshot(snap_id)
    assert snap is not None
    with pytest.raises(SnapshotImmutableError):
        platform.portfolio.freeze_snapshot(snap_id, actor_id="usr_ctrl_01")
    with pytest.raises(SnapshotImmutableError):
        platform.store.save_snapshot(snap.model_copy(update={"nav_usd": Decimal("1.00")}))


def test_risk_limit_breach_detection_opens_exceptions(project_root) -> None:
    tight_cfg = PlatformConfig(
        risk_limits=RiskLimitsConfig(
            max_gross_leverage=Decimal("0.50"),
            max_single_name_concentration_pct=Decimal("10.0"),
        )
    )
    p = HedgeFundPlatform(config=tight_cfg, db_path=":memory:")
    p.master.load_bundle_file(project_root / "data" / "fixtures" / "master_data.json")
    p.dataset.load_day_file(project_root / "data" / "fixtures" / "day1_clean_close.json")
    try:
        run = p.close.run_daily_close(
            fund_id="FUND_SAAT_MS",
            business_date=date(2026, 11, 25),
            actor_id="usr_acct_01",
        )
        assert run.status.value == "BLOCKED"
        assert run.snapshot_id is not None
        snap = p.store.get_snapshot(run.snapshot_id)
        breach_names = {b.limit_name for b in snap.risk_metrics.limit_breaches}
        assert "max_gross_leverage" in breach_names
        assert "max_single_name_concentration_pct" in breach_names
    finally:
        p.shutdown()
