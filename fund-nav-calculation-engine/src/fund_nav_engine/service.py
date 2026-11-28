"""Application service coordinating calculation, approval, freezing, restatements, and replay."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fund_nav_engine.calculator import calculate_nav
from fund_nav_engine.errors import ImmutabilityViolationError, RestatementError
from fund_nav_engine.models import (
    NavCalculationResult,
    SnapshotStatus,
    ValuationConfig,
    ValuationInputBundle,
    format_decimal,
)
from fund_nav_engine.store import SqliteNavStore

logger = logging.getLogger("fund_nav_engine")


def compute_restatement_diff(
    prior: NavCalculationResult,
    restated: NavCalculationResult,
) -> Dict[str, Any]:
    """Produce a structured accounting diff between prior and restated snapshots."""
    prior_classes = {c.share_class_id: c for c in prior.share_class_navs}
    restated_classes = {c.share_class_id: c for c in restated.share_class_navs}
    class_diffs: Dict[str, Any] = {}

    for cid in sorted(set(prior_classes.keys()) | set(restated_classes.keys())):
        p = prior_classes.get(cid)
        r = restated_classes.get(cid)
        p_net = p.closing_net_assets_base if p else Decimal("0")
        r_net = r.closing_net_assets_base if r else Decimal("0")
        p_nav = p.nav_per_share if p else None
        r_nav = r.nav_per_share if r else None
        nav_delta = (
            format_decimal(r_nav - p_nav)
            if (p_nav is not None and r_nav is not None)
            else None
        )
        class_diffs[cid] = {
            "prior_net_assets_base": format_decimal(p_net),
            "restated_net_assets_base": format_decimal(r_net),
            "net_assets_delta_base": format_decimal(r_net - p_net),
            "prior_nav_per_share": format_decimal(p_nav),
            "restated_nav_per_share": format_decimal(r_nav),
            "nav_per_share_delta": nav_delta,
        }

    return {
        "prior_snapshot_id": prior.snapshot_id,
        "restated_snapshot_id": restated.snapshot_id,
        "prior_version": prior.version,
        "restated_version": restated.version,
        "restatement_reason": restated.restatement_reason,
        "gross_assets_delta": format_decimal(restated.gross_assets - prior.gross_assets),
        "total_liabilities_delta": format_decimal(
            restated.total_liabilities - prior.total_liabilities
        ),
        "net_assets_delta": format_decimal(restated.net_assets - prior.net_assets),
        "share_class_diffs": class_diffs,
    }


class NavCalculationService:
    """Orchestrates daily NAV runs, snapshot approval/freezing, restatements, and replay."""

    def __init__(self, store: SqliteNavStore) -> None:
        self.store = store

    def _log_event(self, event: str, payload: Dict[str, Any]) -> None:
        logger.info("%s", json.dumps({"event": event, **payload}, sort_keys=True))

    def calculate_and_store_draft(
        self,
        bundle: ValuationInputBundle,
        config: ValuationConfig,
        *,
        actor: str = "nav_operator",
        calculated_at: Optional[datetime] = None,
    ) -> NavCalculationResult:
        """Compute initial v1 draft NAV and persist it to the store."""
        result = calculate_nav(
            bundle=bundle,
            config=config,
            version=1,
            calculated_at=calculated_at,
        )
        saved = self.store.save_draft(
            result, bundle, config, actor=actor, recorded_at=calculated_at
        )
        self._log_event(
            "nav_draft_calculated",
            {
                "snapshot_id": saved.snapshot_id,
                "fund_id": saved.fund_id,
                "valuation_date": saved.valuation_date.isoformat(),
                "version": saved.version,
                "net_assets": format_decimal(saved.net_assets),
                "reconciled": saved.reconciliation.reconciled,
                "diagnostics": saved.diagnostics,
            },
        )
        return saved

    def approve_and_freeze(
        self,
        snapshot_id: str,
        *,
        approver: str,
        publisher: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> NavCalculationResult:
        """Approve a draft snapshot and freeze it permanently for publication."""
        ts = (timestamp or datetime.now(timezone.utc)).astimezone(timezone.utc)
        self.store.approve_snapshot(snapshot_id, actor=approver, approved_at=ts)
        frozen = self.store.freeze_snapshot(
            snapshot_id, actor=publisher or approver, frozen_at=ts
        )
        self._log_event(
            "nav_snapshot_frozen",
            {
                "snapshot_id": frozen.snapshot_id,
                "fund_id": frozen.fund_id,
                "valuation_date": frozen.valuation_date.isoformat(),
                "version": frozen.version,
                "output_hash": frozen.output_hash,
            },
        )
        return frozen

    def publish_restatement(
        self,
        prior_snapshot_id: str,
        corrected_bundle: ValuationInputBundle,
        config: ValuationConfig,
        *,
        reason: str,
        actor: str = "nav_controller",
        approver: Optional[str] = None,
        auto_freeze: bool = True,
        calculated_at: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Create and optionally freeze an auditable restatement linked to a prior frozen snapshot."""
        if not reason or not reason.strip():
            raise RestatementError(
                "A non-empty restatement reason is required.",
                details={"prior_snapshot_id": prior_snapshot_id},
            )

        prior = self.store.get_snapshot(prior_snapshot_id)
        if prior.status != SnapshotStatus.FROZEN:
            raise RestatementError(
                f"Cannot restate snapshot '{prior_snapshot_id}' because its status is '{prior.status.value}', not 'frozen'.",
                details={"prior_snapshot_id": prior_snapshot_id, "status": prior.status.value},
            )
        if corrected_bundle.fund_id != prior.fund_id:
            raise RestatementError(
                f"Restatement fund_id '{corrected_bundle.fund_id}' does not match prior snapshot fund_id '{prior.fund_id}'."
            )
        if corrected_bundle.valuation_date != prior.valuation_date:
            raise RestatementError(
                f"Restatement valuation_date {corrected_bundle.valuation_date} does not match prior snapshot date {prior.valuation_date}."
            )

        next_version = prior.version + 1
        restated = calculate_nav(
            bundle=corrected_bundle,
            config=config,
            version=next_version,
            prior_snapshot_id=prior.snapshot_id,
            restatement_reason=reason.strip(),
            calculated_at=calculated_at,
        )
        saved = self.store.save_draft(
            restated,
            corrected_bundle,
            config,
            actor=actor,
            recorded_at=calculated_at,
        )
        diff = compute_restatement_diff(prior, saved)
        self.store.record_audit_event(
            snapshot_id=saved.snapshot_id,
            event_type="RESTATEMENT_DIFF_RECORDED",
            actor=actor,
            details=diff,
            timestamp=calculated_at,
        )

        final_snapshot = saved
        if auto_freeze:
            final_snapshot = self.approve_and_freeze(
                saved.snapshot_id,
                approver=approver or actor,
                publisher=actor,
                timestamp=calculated_at,
            )

        self._log_event(
            "nav_restatement_published",
            {
                "snapshot_id": final_snapshot.snapshot_id,
                "prior_snapshot_id": prior.snapshot_id,
                "version": final_snapshot.version,
                "net_assets_delta": diff["net_assets_delta"],
                "status": final_snapshot.status.value,
            },
        )
        return {"snapshot": final_snapshot, "diff": diff}

    def replay_and_verify(self, snapshot_id: str) -> Dict[str, Any]:
        """Replay calculation from persisted inputs and verify exact hash reproduction."""
        stored = self.store.get_snapshot(snapshot_id)
        bundle, config = self.store.get_input_bundle_and_config(stored.input_hash)
        replayed = calculate_nav(
            bundle=bundle,
            config=config,
            version=stored.version,
            prior_snapshot_id=stored.prior_snapshot_id,
            restatement_reason=stored.restatement_reason,
            calculated_at=stored.calculated_at,
        )
        matches = (
            replayed.output_hash == stored.output_hash
            and replayed.snapshot_id == stored.snapshot_id
        )
        if not matches:
            raise ImmutabilityViolationError(
                f"Replay of snapshot '{snapshot_id}' produced divergent output hash.",
                details={
                    "stored_hash": stored.output_hash,
                    "replayed_hash": replayed.output_hash,
                },
            )
        chain = self.store.get_restatement_chain(snapshot_id)
        return {
            "snapshot_id": snapshot_id,
            "verified": True,
            "output_hash": stored.output_hash,
            "input_hash": stored.input_hash,
            "status": stored.status.value,
            "lineage_depth": len(chain),
            "lineage_ids": [s.snapshot_id for s in chain],
        }

    def list_audit_trail(self, snapshot_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self.store.get_audit_journal(snapshot_id)]
