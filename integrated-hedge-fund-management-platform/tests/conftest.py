"""Shared pytest fixtures for the Integrated Hedge Fund Management Platform."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from hf_platform.config import PlatformConfig
from hf_platform.services.platform import HedgeFundPlatform


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def platform(project_root: Path) -> HedgeFundPlatform:
    cfg = PlatformConfig.from_file(project_root / "config" / "platform_config.json")
    p = HedgeFundPlatform(config=cfg, db_path=":memory:")
    p.master.load_bundle_file(project_root / "data" / "fixtures" / "master_data.json")
    p.dataset.load_day_file(project_root / "data" / "fixtures" / "day1_clean_close.json")
    p.dataset.load_day_file(project_root / "data" / "fixtures" / "day2_reconciliation_breaks.json")
    yield p
    p.shutdown()


@pytest.fixture
def day1_closed_platform(platform: HedgeFundPlatform) -> tuple[HedgeFundPlatform, str]:
    run = platform.close.run_daily_close(
        fund_id="FUND_SAAT_MS",
        business_date=date(2026, 11, 25),
        actor_id="usr_acct_01",
    )
    closed = platform.close.sign_off_close(close_run_id=run.close_run_id, actor_id="usr_ctrl_01")
    assert closed.snapshot_id is not None
    return platform, closed.snapshot_id
