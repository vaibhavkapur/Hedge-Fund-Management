"""Orchestration service, structured JSON logging, diagnostic counters, and replay support."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from pb_collateral.errors import MissingConfigurationError
from pb_collateral.fx import FxTable
from pb_collateral.journal import SQLiteJournalStore
from pb_collateral.models import (
    BrokerStatement,
    CashBalanceRecord,
    CollateralPolicy,
    CollateralTransfer,
    FinancingRateSchedule,
    InternalLedgerSnapshot,
    MarginRequirement,
    PositionRecord,
    ReconciliationResult,
    SubstitutionRequest,
    SubstitutionReviewResult,
)
from pb_collateral.reconciliation import reconcile_statement
from pb_collateral.substitution import simulate_substitution
from pb_collateral.valuation import verify_no_global_double_counting


@dataclass
class DiagnosticCounters:
    statements_processed: int = 0
    duplicate_statements_skipped: int = 0
    corrections_versioned: int = 0
    breaks_detected: int = 0
    shortfalls_flagged: int = 0
    margin_calls_issued: int = 0
    substitutions_approved: int = 0
    substitutions_blocked: int = 0
    failed_transfers_quarantined: int = 0
    ineligible_assets_excluded: int = 0

    def to_dict(self) -> Dict[str, int]:
        return asdict(self)


def build_json_logger(name: str = "pb_collateral", level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    return logger


class CollateralManagementService:
    """High-level typed service coordinating validation, calculation, journal persistence, and replay."""

    def __init__(
        self,
        *,
        policies: Mapping[str, CollateralPolicy],
        financing_schedules: Mapping[Tuple[str, str], FinancingRateSchedule],
        journal: Optional[SQLiteJournalStore] = None,
        rounding_mode: str = "ROUND_HALF_EVEN",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._policies = dict(policies)
        self._financing_schedules = dict(financing_schedules)
        self._journal = journal or SQLiteJournalStore(":memory:")
        self._rounding_mode = rounding_mode
        self._logger = logger or build_json_logger()
        self.counters = DiagnosticCounters()
        self.structured_logs: List[Dict[str, Any]] = []

    def _log_event(self, event: str, **fields: Any) -> None:
        payload = {"event": event, **fields}
        self.structured_logs.append(payload)
        self._logger.debug(json.dumps(payload, sort_keys=True, default=str))

    @property
    def journal(self) -> SQLiteJournalStore:
        return self._journal

    def get_policy(self, broker_id: str) -> CollateralPolicy:
        policy = self._policies.get(broker_id)
        if policy is None:
            raise MissingConfigurationError(
                f"No collateral eligibility policy configured for broker '{broker_id}'",
                context={"broker_id": broker_id, "configured_brokers": sorted(self._policies)},
            )
        return policy

    def process_statement(
        self,
        *,
        statement: BrokerStatement,
        internal_snapshot: InternalLedgerSnapshot,
        fx_table: FxTable,
        accrual_start_date: Optional[date] = None,
        accrual_end_date: Optional[date] = None,
        externally_encumbered_lots: Optional[Set[str]] = None,
    ) -> ReconciliationResult:
        """Process a single normalized broker statement with idempotent deduplication and journal persistence."""
        policy = self.get_policy(statement.broker_id)
        duplicate_skipped, is_correction = self._journal.register_statement(statement)

        if duplicate_skipped:
            self.counters.duplicate_statements_skipped += 1
            self._log_event(
                "duplicate_statement_skipped",
                statement_id=statement.statement_id,
                broker_id=statement.broker_id,
                business_date=statement.business_date.isoformat(),
                content_hash=statement.content_hash,
            )
            # Re-run pure calculation to return typed result marked duplicate_skipped=True without double-recording
            res = reconcile_statement(
                statement=statement,
                internal_snapshot=internal_snapshot,
                policy=policy,
                financing_schedules=self._financing_schedules,
                fx_table=fx_table,
                accrual_start_date=accrual_start_date,
                accrual_end_date=accrual_end_date,
                externally_encumbered_lots=externally_encumbered_lots,
                rounding_mode=self._rounding_mode,
            )
            return ReconciliationResult(
                run_id=res.run_id,
                statement_id=res.statement_id,
                statement_version=res.statement_version,
                broker_id=res.broker_id,
                account_id=res.account_id,
                business_date=res.business_date,
                base_currency=res.base_currency,
                duplicate_skipped=True,
                valuation=res.valuation,
                financing=res.financing,
                breaks=res.breaks,
                margin_call=res.margin_call,
                reconciled_clean=res.reconciled_clean,
            )

        if is_correction:
            self.counters.corrections_versioned += 1
            self._log_event(
                "statement_correction_versioned",
                statement_id=statement.statement_id,
                version=statement.version,
                supersedes=statement.supersedes_statement_id,
                broker_id=statement.broker_id,
            )

        result = reconcile_statement(
            statement=statement,
            internal_snapshot=internal_snapshot,
            policy=policy,
            financing_schedules=self._financing_schedules,
            fx_table=fx_table,
            accrual_start_date=accrual_start_date,
            accrual_end_date=accrual_end_date,
            externally_encumbered_lots=externally_encumbered_lots,
            rounding_mode=self._rounding_mode,
        )
        self._journal.record_reconciliation(result)

        self.counters.statements_processed += 1
        self.counters.breaks_detected += len(result.breaks)
        self.counters.failed_transfers_quarantined += len(result.valuation.quarantined_failed_transfer_lots)
        self.counters.ineligible_assets_excluded += len(result.valuation.ineligible_lots)
        if result.margin_call.flagged_before_settlement:
            self.counters.shortfalls_flagged += 1
        if result.margin_call.action.value == "MARGIN_CALL":
            self.counters.margin_calls_issued += 1

        self._log_event(
            "reconciliation_completed",
            run_id=result.run_id,
            statement_id=result.statement_id,
            broker_id=result.broker_id,
            business_date=result.business_date.isoformat(),
            break_count=len(result.breaks),
            margin_action=result.margin_call.action.value,
            shortfall_flagged=result.margin_call.flagged_before_settlement,
            posted_collateral=str(result.valuation.total_posted_collateral_base),
            net_accrual=str(result.financing.net_accrual_base),
        )
        return result

    def process_multi_broker_batch(
        self,
        *,
        pairs: Sequence[Tuple[BrokerStatement, InternalLedgerSnapshot]],
        fx_table: FxTable,
        accrual_start_date: Optional[date] = None,
        accrual_end_date: Optional[date] = None,
    ) -> List[ReconciliationResult]:
        """Validate global no-double-counting across all prime brokers, then reconcile each statement."""
        positions_by_broker: Dict[str, List[PositionRecord]] = {}
        all_transfers: List[CollateralTransfer] = []
        for stmt, snap in pairs:
            positions_by_broker.setdefault(stmt.broker_id, []).extend(stmt.positions)
            all_transfers.extend(snap.transfers)

        verify_no_global_double_counting(positions_by_broker, all_transfers)

        results: List[ReconciliationResult] = []
        for stmt, snap in pairs:
            res = self.process_statement(
                statement=stmt,
                internal_snapshot=snap,
                fx_table=fx_table,
                accrual_start_date=accrual_start_date,
                accrual_end_date=accrual_end_date,
            )
            results.append(res)
        return results

    def review_substitution(
        self,
        *,
        request: SubstitutionRequest,
        current_positions: Sequence[PositionRecord],
        cash_balances: Sequence[CashBalanceRecord],
        margin_requirement: MarginRequirement,
        fx_table: FxTable,
        transfers: Sequence[CollateralTransfer] = (),
        externally_encumbered_lots: Optional[Set[str]] = None,
    ) -> SubstitutionReviewResult:
        """Simulate collateral substitution, enforce pre-settlement gate, and persist review to journal."""
        policy = self.get_policy(request.broker_id)
        review = simulate_substitution(
            request=request,
            current_positions=current_positions,
            cash_balances=cash_balances,
            margin_requirement=margin_requirement,
            policy=policy,
            fx_table=fx_table,
            transfers=transfers,
            externally_encumbered_lots=externally_encumbered_lots,
            rounding_mode=self._rounding_mode,
        )
        self._journal.record_substitution(review)
        if review.settlement_permitted:
            self.counters.substitutions_approved += 1
        else:
            self.counters.substitutions_blocked += 1

        self._log_event(
            "substitution_reviewed",
            review_id=review.review_id,
            request_id=review.request_id,
            broker_id=review.broker_id,
            settlement_permitted=review.settlement_permitted,
            shortfall_flagged=review.shortfall_flagged_before_settlement,
            required_top_up=str(review.required_top_up_base),
            approval_status=review.approval_status.value,
        )
        return review

    def replay_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve immutable persisted reconciliation snapshot by deterministic run_id."""
        return self._journal.get_reconciliation_run(run_id)
