"""Service layer orchestrating validation, domain calculation, journaling, storage, and approvals."""

from __future__ import annotations

from datetime import date
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .domain import evaluate_period
from .errors import StorageError, ValidationError
from .journal import build_journal_entries, build_reversal_entries, reconcile_run
from .models import (
    ApprovalRecord,
    ApprovalStatus,
    CalculationRunResult,
    CapitalFlow,
    EngineConfig,
    FeeAgreement,
    FeeCorrection,
    InvestorLot,
    NavSnapshot,
    stable_hash,
)
from .storage import SqliteFeeStore
from .validation import (
    validate_capital_flows,
    validate_fee_agreement,
    validate_investor_lots,
    validate_nav_snapshot,
)

logger = logging.getLogger("hedge_fund_fee_engine.service")


def _compute_input_hash(
    agreement: FeeAgreement,
    snapshot: NavSnapshot,
    lots: Sequence[InvestorLot],
    capital_flows: Sequence[CapitalFlow],
    config: EngineConfig,
    version: int,
) -> str:
    return stable_hash(
        {
            "config_version": config.config_version,
            "version": version,
            "agreement": agreement.to_dict(),
            "snapshot": snapshot.to_dict(),
            "lots": [lot.to_dict() for lot in lots],
            "capital_flows": [flow.to_dict() for flow in capital_flows],
        },
        length=16,
    )


def _compute_output_hash(
    lot_calculations: Sequence[Any],
    updated_lots: Sequence[Any],
    journal_entries: Sequence[Any],
    reconciliation: Any,
) -> str:
    return stable_hash(
        {
            "lot_calculations": [c.to_dict() for c in lot_calculations],
            "updated_lots": [lot.to_dict() for lot in updated_lots],
            "journal_entries": [e.to_dict() for e in journal_entries],
            "reconciliation": reconciliation.to_dict(),
        },
        length=16,
    )


class FeeAccountingService:
    """High-level boundary coordinating validation, pure domain math, immutable storage, and approvals."""

    def __init__(self, config: EngineConfig, store: Optional[SqliteFeeStore] = None) -> None:
        self.config = config
        self.store = store
        self.event_log: List[Dict[str, Any]] = []

    @classmethod
    def from_config_path(
        cls,
        config_path: Path,
        db_path: Optional[Path] = None,
    ) -> "FeeAccountingService":
        config = EngineConfig.from_json_file(config_path)
        store = SqliteFeeStore(db_path) if db_path is not None else None
        return cls(config=config, store=store)

    def _emit_log(self, event: str, payload: Dict[str, Any]) -> None:
        record = {"event": event, **payload}
        self.event_log.append(record)
        logger.info(json.dumps(record, sort_keys=True))

    def register_agreement(self, agreement: FeeAgreement) -> None:
        validate_fee_agreement(agreement, self.config)
        if self.store is not None:
            self.store.save_agreement(agreement)
        self._emit_log(
            "agreement_registered",
            {
                "agreement_id": agreement.agreement_id,
                "version": agreement.version,
                "share_class": agreement.share_class,
                "currency": agreement.currency,
            },
        )

    def run_period(
        self,
        *,
        agreement: FeeAgreement,
        snapshot: NavSnapshot,
        lots: Sequence[InvestorLot],
        capital_flows: Sequence[CapitalFlow] = (),
        posting_date: Optional[date] = None,
        version: int = 1,
        persist: bool = True,
        allow_multi_crystallization_span: bool = False,
    ) -> CalculationRunResult:
        """Validate inputs, calculate fees, generate balanced journals, reconcile, and persist."""
        validate_fee_agreement(agreement, self.config)
        validate_nav_snapshot(
            snapshot,
            agreement,
            self.config,
            allow_multi_crystallization_span=allow_multi_crystallization_span,
        )
        validate_investor_lots(lots, agreement, snapshot.valuation_date)
        validate_capital_flows(
            capital_flows,
            lots,
            agreement,
            snapshot.period_start_date,
            snapshot.valuation_date,
        )

        if persist and self.store is not None:
            if self.store.has_active_run(agreement.agreement_id, snapshot.valuation_date, version):
                raise StorageError(
                    f"Duplicate accrual run blocked for ({agreement.agreement_id}, {snapshot.valuation_date}, v{version})",
                    code="DUPLICATE_RUN",
                    field="valuation_date",
                )

        input_hash = _compute_input_hash(agreement, snapshot, lots, capital_flows, self.config, version)
        run_id = f"RUN-{snapshot.valuation_date.strftime('%Y%m%d')}-v{version}-{input_hash[:10]}"

        lot_calculations, updated_lots, counters = evaluate_period(
            agreement=agreement,
            snapshot=snapshot,
            lots=lots,
            config=self.config,
            capital_flows=capital_flows,
        )

        eff_posting_date = posting_date or snapshot.valuation_date
        journal_entries = build_journal_entries(
            run_id=run_id,
            version=version,
            posting_date=eff_posting_date,
            valuation_date=snapshot.valuation_date,
            currency=agreement.currency,
            lot_calculations=lot_calculations,
            config=self.config,
            is_correction=False,
        )

        reconciliation = reconcile_run(
            lot_calculations=lot_calculations,
            journal_entries=journal_entries,
            currency=agreement.currency,
            config=self.config,
            allow_negative_accrual=agreement.allow_negative_accrual,
        )

        output_hash = _compute_output_hash(lot_calculations, updated_lots, journal_entries, reconciliation)
        diagnostics: Dict[str, Any] = {
            "counters": counters,
            "journal_entry_count": len(journal_entries),
            "journal_line_count": sum(len(e.lines) for e in journal_entries),
            "reconciled": reconciliation.reconciled,
        }

        result = CalculationRunResult(
            run_id=run_id,
            version=version,
            agreement_id=agreement.agreement_id,
            share_class=agreement.share_class,
            currency=agreement.currency,
            valuation_date=snapshot.valuation_date,
            period_start_date=snapshot.period_start_date,
            config_version=self.config.config_version,
            input_hash=input_hash,
            output_hash=output_hash,
            lot_calculations=lot_calculations,
            updated_lots=updated_lots,
            journal_entries=journal_entries,
            reconciliation=reconciliation,
            diagnostics=diagnostics,
            status=ApprovalStatus.DRAFT,
            supersedes_run_id=None,
        )

        if persist and self.store is not None:
            self.store.save_agreement(agreement)
            self.store.save_run(
                result=result,
                agreement=agreement,
                snapshot=snapshot,
                opening_lots=lots,
                capital_flows=capital_flows,
            )

        self._emit_log(
            "period_run_completed",
            {
                "run_id": result.run_id,
                "agreement_id": agreement.agreement_id,
                "valuation_date": snapshot.valuation_date.isoformat(),
                "version": version,
                "input_hash": input_hash,
                "output_hash": output_hash,
                "reconciled": reconciliation.reconciled,
                "counters": counters,
            },
        )
        return result

    def apply_correction(
        self,
        *,
        correction: FeeCorrection,
        capital_flows: Optional[Sequence[CapitalFlow]] = None,
        posting_date: Optional[date] = None,
    ) -> CalculationRunResult:
        """Record a fee correction as a new immutable version with full reversal + corrected journals."""
        if self.store is None:
            raise StorageError("apply_correction requires a persistent SqliteFeeStore", code="STORE_REQUIRED")

        prior_record = self.store.get_run_record(correction.target_run_id)
        prior_inputs = prior_record["inputs"]
        prior_version = int(prior_record["version"])
        new_version = correction.version

        if new_version <= prior_version:
            raise ValidationError(
                f"Correction version ({new_version}) must be > prior version ({prior_version})",
                code="INVALID_CORRECTION_VERSION",
                field="version",
            )

        agreement = FeeAgreement.from_dict(prior_inputs["agreement"])
        opening_lots = tuple(InvestorLot.from_dict(d) for d in prior_inputs["opening_lots"])
        eff_flows = (
            tuple(capital_flows)
            if capital_flows is not None
            else tuple(CapitalFlow.from_dict(d) for d in prior_inputs["capital_flows"])
        )

        validate_fee_agreement(agreement, self.config)
        validate_nav_snapshot(correction.corrected_snapshot, agreement, self.config)
        validate_investor_lots(opening_lots, agreement, correction.corrected_snapshot.valuation_date)
        validate_capital_flows(
            eff_flows,
            opening_lots,
            agreement,
            correction.corrected_snapshot.period_start_date,
            correction.corrected_snapshot.valuation_date,
        )

        input_hash = _compute_input_hash(
            agreement,
            correction.corrected_snapshot,
            opening_lots,
            eff_flows,
            self.config,
            new_version,
        )
        new_run_id = (
            f"RUN-{correction.corrected_snapshot.valuation_date.strftime('%Y%m%d')}"
            f"-v{new_version}-{input_hash[:10]}"
        )

        prior_entries = self.store.get_journal_entries_for_run(correction.target_run_id)
        eff_posting_date = posting_date or correction.effective_date
        reversal_entries = build_reversal_entries(
            prior_entries=prior_entries,
            new_run_id=new_run_id,
            new_version=new_version,
            posting_date=eff_posting_date,
            reason=correction.reason,
        )

        lot_calculations, updated_lots, counters = evaluate_period(
            agreement=agreement,
            snapshot=correction.corrected_snapshot,
            lots=opening_lots,
            config=self.config,
            capital_flows=eff_flows,
        )

        corrected_entries = build_journal_entries(
            run_id=new_run_id,
            version=new_version,
            posting_date=eff_posting_date,
            valuation_date=correction.corrected_snapshot.valuation_date,
            currency=agreement.currency,
            lot_calculations=lot_calculations,
            config=self.config,
            is_correction=True,
        )

        all_entries = reversal_entries + corrected_entries
        reconciliation = reconcile_run(
            lot_calculations=lot_calculations,
            journal_entries=all_entries,
            currency=agreement.currency,
            config=self.config,
            allow_negative_accrual=agreement.allow_negative_accrual,
        )

        output_hash = _compute_output_hash(lot_calculations, updated_lots, all_entries, reconciliation)
        diagnostics: Dict[str, Any] = {
            "counters": counters,
            "correction_id": correction.correction_id,
            "correction_reason": correction.reason,
            "reversal_entry_count": len(reversal_entries),
            "corrected_entry_count": len(corrected_entries),
            "reconciled": reconciliation.reconciled,
        }

        result = CalculationRunResult(
            run_id=new_run_id,
            version=new_version,
            agreement_id=agreement.agreement_id,
            share_class=agreement.share_class,
            currency=agreement.currency,
            valuation_date=correction.corrected_snapshot.valuation_date,
            period_start_date=correction.corrected_snapshot.period_start_date,
            config_version=self.config.config_version,
            input_hash=input_hash,
            output_hash=output_hash,
            lot_calculations=lot_calculations,
            updated_lots=updated_lots,
            journal_entries=all_entries,
            reconciliation=reconciliation,
            diagnostics=diagnostics,
            status=ApprovalStatus.DRAFT,
            supersedes_run_id=correction.target_run_id,
        )

        self.store.save_run(
            result=result,
            agreement=agreement,
            snapshot=correction.corrected_snapshot,
            opening_lots=opening_lots,
            capital_flows=eff_flows,
        )

        self._emit_log(
            "fee_correction_applied",
            {
                "correction_id": correction.correction_id,
                "target_run_id": correction.target_run_id,
                "new_run_id": new_run_id,
                "version": new_version,
                "output_hash": output_hash,
            },
        )
        return result

    def replay_run(self, run_id: str) -> Dict[str, Any]:
        """Deterministically re-run a persisted calculation and compare output hashes."""
        if self.store is None:
            raise StorageError("replay_run requires a persistent SqliteFeeStore", code="STORE_REQUIRED")

        record = self.store.get_run_record(run_id)
        inputs = record["inputs"]
        agreement = FeeAgreement.from_dict(inputs["agreement"])
        snapshot = NavSnapshot.from_dict(inputs["snapshot"])
        opening_lots = tuple(InvestorLot.from_dict(d) for d in inputs["opening_lots"])
        flows = tuple(CapitalFlow.from_dict(d) for d in inputs["capital_flows"])
        version = int(record["version"])

        if record["supersedes_run_id"]:
            prior_entries = self.store.get_journal_entries_for_run(record["supersedes_run_id"])
            reversals = build_reversal_entries(
                prior_entries=prior_entries,
                new_run_id=run_id,
                new_version=version,
                posting_date=date.fromisoformat(record["result"]["journal_entries"][0]["posting_date"]),
                reason=record["result"]["diagnostics"]["correction_reason"],
            )
            lot_calcs, updated_lots, _ = evaluate_period(
                agreement=agreement,
                snapshot=snapshot,
                lots=opening_lots,
                config=self.config,
                capital_flows=flows,
            )
            new_entries = build_journal_entries(
                run_id=run_id,
                version=version,
                posting_date=date.fromisoformat(record["result"]["journal_entries"][0]["posting_date"]),
                valuation_date=snapshot.valuation_date,
                currency=agreement.currency,
                lot_calculations=lot_calcs,
                config=self.config,
                is_correction=True,
            )
            all_entries = reversals + new_entries
            recon = reconcile_run(
                lot_calculations=lot_calcs,
                journal_entries=all_entries,
                currency=agreement.currency,
                config=self.config,
                allow_negative_accrual=agreement.allow_negative_accrual,
            )
            replayed_hash = _compute_output_hash(lot_calcs, updated_lots, all_entries, recon)
        else:
            replayed = self.run_period(
                agreement=agreement,
                snapshot=snapshot,
                lots=opening_lots,
                capital_flows=flows,
                version=version,
                persist=False,
            )
            replayed_hash = replayed.output_hash

        matches = replayed_hash == record["output_hash"]
        self._emit_log(
            "run_replayed",
            {
                "run_id": run_id,
                "stored_output_hash": record["output_hash"],
                "replayed_output_hash": replayed_hash,
                "matches": matches,
            },
        )
        return {
            "run_id": run_id,
            "stored_output_hash": record["output_hash"],
            "replayed_output_hash": replayed_hash,
            "matches": matches,
        }

    def approve_run(self, run_id: str, *, actor: str, comment: str = "") -> ApprovalRecord:
        if self.store is None:
            raise StorageError("approve_run requires a persistent SqliteFeeStore", code="STORE_REQUIRED")
        rec = self.store.record_approval(
            run_id=run_id,
            target_status=ApprovalStatus.APPROVED,
            actor=actor,
            comment=comment,
        )
        self._emit_log("run_approved", rec.to_dict())
        return rec

    def reject_run(self, run_id: str, *, actor: str, comment: str = "") -> ApprovalRecord:
        if self.store is None:
            raise StorageError("reject_run requires a persistent SqliteFeeStore", code="STORE_REQUIRED")
        rec = self.store.record_approval(
            run_id=run_id,
            target_status=ApprovalStatus.REJECTED,
            actor=actor,
            comment=comment,
        )
        self._emit_log("run_rejected", rec.to_dict())
        return rec
