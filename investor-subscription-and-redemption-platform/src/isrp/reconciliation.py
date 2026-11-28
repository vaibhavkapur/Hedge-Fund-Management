"""Cash reconciliation engine, share register aggregation, and exact reconciliation proofs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Tuple

from .config import RoundingConfig
from .models import (
    CapitalFlowRequest,
    CashConfirmation,
    CashDirection,
    CashMatchStatus,
    DealingWindow,
    ExceptionType,
    JournalEntryType,
    RequestStatus,
    RequestType,
    ShareJournalEntry,
    SharePosition,
)
from .pricing import quantize_cash, quantize_shares
from .workflow import ACTIVE_REDEMPTION_LOCK_STATUSES


@dataclass(frozen=True)
class CashMatchResult:
    status: CashMatchStatus
    expected_amount: Decimal
    discrepancy_amount: Decimal
    exception_type: Optional[ExceptionType]
    reason: str


def evaluate_cash_confirmation(
    request: CapitalFlowRequest,
    confirmation: CashConfirmation,
    window: DealingWindow,
    rounding: RoundingConfig,
) -> CashMatchResult:
    """Match an inbound/outbound cash confirmation against request terms and dealing window."""
    expected_direction = (
        CashDirection.INBOUND if request.request_type == RequestType.SUBSCRIPTION else CashDirection.OUTBOUND
    )
    if confirmation.direction != expected_direction:
        return CashMatchResult(
            status=CashMatchStatus.UNMATCHED,
            expected_amount=Decimal("0.00"),
            discrepancy_amount=confirmation.amount,
            exception_type=ExceptionType.CASH_MISMATCH,
            reason=(
                f"Direction mismatch: expected {expected_direction.value} for {request.request_type.value}, "
                f"received {confirmation.direction.value}."
            ),
        )

    if confirmation.currency != request.currency:
        return CashMatchResult(
            status=CashMatchStatus.CURRENCY_MISMATCH,
            expected_amount=Decimal("0.00"),
            discrepancy_amount=confirmation.amount,
            exception_type=ExceptionType.CASH_MISMATCH,
            reason=f"Currency mismatch: request expects {request.currency}, wire is {confirmation.currency}.",
        )

    if request.request_type == RequestType.SUBSCRIPTION:
        assert request.gross_amount is not None
        expected_amount = quantize_cash(request.gross_amount, rounding)
        due_date = window.cash_due_date
    else:
        if request.net_cash is None:
            return CashMatchResult(
                status=CashMatchStatus.UNMATCHED,
                expected_amount=Decimal("0.00"),
                discrepancy_amount=confirmation.amount,
                exception_type=ExceptionType.CASH_MISMATCH,
                reason="Redemption must be priced before matching outbound payout confirmation.",
            )
        expected_amount = quantize_cash(request.net_cash, rounding)
        due_date = window.settlement_date

    received = quantize_cash(confirmation.amount, rounding)
    discrepancy = quantize_cash(received - expected_amount, rounding)
    if discrepancy != Decimal("0.00"):
        return CashMatchResult(
            status=CashMatchStatus.AMOUNT_MISMATCH,
            expected_amount=expected_amount,
            discrepancy_amount=discrepancy,
            exception_type=ExceptionType.CASH_MISMATCH,
            reason=f"Cash amount mismatch: expected {expected_amount} {request.currency}, received {received} (diff={discrepancy}).",
        )

    if confirmation.value_date > due_date:
        return CashMatchResult(
            status=CashMatchStatus.DELAYED_CASH,
            expected_amount=expected_amount,
            discrepancy_amount=Decimal("0.00"),
            exception_type=ExceptionType.DELAYED_CASH,
            reason=(
                f"Delayed cash: value_date {confirmation.value_date.isoformat()} is after "
                f"due date {due_date.isoformat()}."
            ),
        )

    return CashMatchResult(
        status=CashMatchStatus.MATCHED,
        expected_amount=expected_amount,
        discrepancy_amount=Decimal("0.00"),
        exception_type=None,
        reason="Cash confirmation matched exactly within due date.",
    )


def build_share_register(
    journal_entries: Iterable[ShareJournalEntry],
    requests: Iterable[CapitalFlowRequest],
    rounding: RoundingConfig,
) -> Dict[Tuple[str, str, str, str], SharePosition]:
    """Compute point-in-time share register from immutable journal entries and pending redemptions."""
    zero_shares = quantize_shares(Decimal("0"), rounding)
    zero_cash = quantize_cash(Decimal("0"), rounding)

    acc: Dict[Tuple[str, str, str, str], Dict[str, Decimal]] = {}

    def _bucket(key: Tuple[str, str, str, str]) -> Dict[str, Decimal]:
        if key not in acc:
            acc[key] = {
                "settled_shares": zero_shares,
                "pending_redemption_shares": zero_shares,
                "total_subscribed_gross": zero_cash,
                "total_redeemed_net": zero_cash,
                "total_fees_paid": zero_cash,
            }
        return acc[key]

    for entry in journal_entries:
        key = (entry.investor_id, entry.fund_id, entry.share_class, entry.currency)
        b = _bucket(key)
        b["settled_shares"] = quantize_shares(b["settled_shares"] + entry.share_delta, rounding)
        if entry.entry_type == JournalEntryType.SUBSCRIPTION_ISSUE:
            b["total_subscribed_gross"] = quantize_cash(b["total_subscribed_gross"] + entry.gross_cash_delta, rounding)
        elif entry.entry_type == JournalEntryType.REDEMPTION_BURN:
            b["total_redeemed_net"] = quantize_cash(b["total_redeemed_net"] + abs(entry.net_cash_delta), rounding)
        elif entry.entry_type == JournalEntryType.NAV_RESTATEMENT_ADJUSTMENT:
            # Restatement cash adjustments apply to redemptions when net_cash_delta != 0
            if entry.net_cash_delta != Decimal("0.00"):
                b["total_redeemed_net"] = quantize_cash(b["total_redeemed_net"] + entry.net_cash_delta, rounding)
        b["total_fees_paid"] = quantize_cash(b["total_fees_paid"] + entry.fee_delta, rounding)

    for req in requests:
        if req.request_type == RequestType.REDEMPTION and req.status in ACTIVE_REDEMPTION_LOCK_STATUSES:
            locked = req.calculated_shares if req.calculated_shares is not None else req.requested_shares
            if locked is not None:
                key = (req.investor_id, req.fund_id, req.share_class, req.currency)
                b = _bucket(key)
                b["pending_redemption_shares"] = quantize_shares(b["pending_redemption_shares"] + locked, rounding)

    positions: Dict[Tuple[str, str, str, str], SharePosition] = {}
    for key in sorted(acc.keys()):
        b = acc[key]
        positions[key] = SharePosition(
            investor_id=key[0],
            fund_id=key[1],
            share_class=key[2],
            currency=key[3],
            settled_shares=b["settled_shares"],
            pending_redemption_shares=b["pending_redemption_shares"],
            total_subscribed_gross=b["total_subscribed_gross"],
            total_redeemed_net=b["total_redeemed_net"],
            total_fees_paid=b["total_fees_paid"],
        )
    return positions


@dataclass(frozen=True)
class ReconciliationReport:
    reconciled: bool
    total_register_shares: Decimal
    total_journal_shares: Decimal
    share_discrepancy: Decimal
    total_inbound_cash_confirmed: Decimal
    total_inbound_cash_journal: Decimal
    inbound_cash_discrepancy: Decimal
    total_outbound_cash_confirmed: Decimal
    total_outbound_cash_journal: Decimal
    outbound_cash_discrepancy: Decimal
    total_fees_collected: Decimal
    settled_request_count: int
    unapproved_settled_count: int
    negative_balance_positions: List[str]
    discrepancies: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "reconciled": self.reconciled,
            "total_register_shares": str(self.total_register_shares),
            "total_journal_shares": str(self.total_journal_shares),
            "share_discrepancy": str(self.share_discrepancy),
            "total_inbound_cash_confirmed": str(self.total_inbound_cash_confirmed),
            "total_inbound_cash_journal": str(self.total_inbound_cash_journal),
            "inbound_cash_discrepancy": str(self.inbound_cash_discrepancy),
            "total_outbound_cash_confirmed": str(self.total_outbound_cash_confirmed),
            "total_outbound_cash_journal": str(self.total_outbound_cash_journal),
            "outbound_cash_discrepancy": str(self.outbound_cash_discrepancy),
            "total_fees_collected": str(self.total_fees_collected),
            "settled_request_count": self.settled_request_count,
            "unapproved_settled_count": self.unapproved_settled_count,
            "negative_balance_positions": self.negative_balance_positions,
            "discrepancies": self.discrepancies,
        }


def verify_platform_reconciliation(
    requests: Iterable[CapitalFlowRequest],
    journal_entries: Iterable[ShareJournalEntry],
    cash_confirmations: Iterable[CashConfirmation],
    positions: Dict[Tuple[str, str, str, str], SharePosition],
    rounding: RoundingConfig,
) -> ReconciliationReport:
    """Verify exact Decimal reconciliation invariants across register, journal, and cash."""
    zero_shares = quantize_shares(Decimal("0"), rounding)
    zero_cash = quantize_cash(Decimal("0"), rounding)

    req_list = list(requests)
    journal_list = list(journal_entries)
    cash_list = list(cash_confirmations)

    discrepancies: List[str] = []
    negative_positions: List[str] = []

    total_register_shares = zero_shares
    for pos in positions.values():
        total_register_shares = quantize_shares(total_register_shares + pos.settled_shares, rounding)
        if pos.settled_shares < Decimal("0"):
            negative_positions.append(f"{pos.investor_id}:{pos.fund_id}:{pos.share_class}")
            discrepancies.append(f"Negative share balance for {pos.investor_id} ({pos.settled_shares}).")

    total_journal_shares = zero_shares
    total_inbound_journal = zero_cash
    total_outbound_journal = zero_cash
    total_fees = zero_cash

    for entry in journal_list:
        total_journal_shares = quantize_shares(total_journal_shares + entry.share_delta, rounding)
        total_fees = quantize_cash(total_fees + entry.fee_delta, rounding)
        if entry.entry_type == JournalEntryType.SUBSCRIPTION_ISSUE:
            total_inbound_journal = quantize_cash(total_inbound_journal + entry.gross_cash_delta, rounding)
            if quantize_cash(entry.net_cash_delta + entry.fee_delta, rounding) != entry.gross_cash_delta:
                discrepancies.append(f"Journal entry {entry.entry_id} fails gross == net + fee invariant.")
        elif entry.entry_type == JournalEntryType.REDEMPTION_BURN:
            total_outbound_journal = quantize_cash(total_outbound_journal + abs(entry.net_cash_delta), rounding)
            if quantize_cash(abs(entry.net_cash_delta) + entry.fee_delta, rounding) != abs(entry.gross_cash_delta):
                discrepancies.append(f"Journal entry {entry.entry_id} fails redemption gross == net + fee invariant.")

    settled_ids = {
        r.request_id for r in req_list if r.status in (RequestStatus.SETTLED, RequestStatus.RESTATED)
    }
    unapproved_settled = [
        r.request_id
        for r in req_list
        if r.status in (RequestStatus.SETTLED, RequestStatus.RESTATED)
        and (not r.checker_id or r.approved_at is None or r.checker_id.lower() == r.maker_id.lower())
    ]
    if unapproved_settled:
        discrepancies.append(f"Unapproved or non-separated settled requests detected: {unapproved_settled}")

    total_inbound_confirmed = zero_cash
    total_outbound_confirmed = zero_cash
    for conf in cash_list:
        if conf.request_id in settled_ids and conf.match_status in (
            CashMatchStatus.MATCHED,
            CashMatchStatus.DELAYED_CASH,
        ):
            if conf.direction == CashDirection.INBOUND:
                total_inbound_confirmed = quantize_cash(total_inbound_confirmed + conf.amount, rounding)
            else:
                total_outbound_confirmed = quantize_cash(total_outbound_confirmed + conf.amount, rounding)

    share_disc = quantize_shares(total_register_shares - total_journal_shares, rounding)
    inbound_disc = quantize_cash(total_inbound_confirmed - total_inbound_journal, rounding)
    outbound_disc = quantize_cash(total_outbound_confirmed - total_outbound_journal, rounding)

    if share_disc != zero_shares:
        discrepancies.append(f"Share register vs journal mismatch: {share_disc}")
    if inbound_disc != zero_cash:
        discrepancies.append(f"Inbound cash confirmations vs settled subscriptions mismatch: {inbound_disc}")
    if outbound_disc != zero_cash:
        discrepancies.append(f"Outbound cash confirmations vs settled redemptions mismatch: {outbound_disc}")

    return ReconciliationReport(
        reconciled=len(discrepancies) == 0,
        total_register_shares=total_register_shares,
        total_journal_shares=total_journal_shares,
        share_discrepancy=share_disc,
        total_inbound_cash_confirmed=total_inbound_confirmed,
        total_inbound_cash_journal=total_inbound_journal,
        inbound_cash_discrepancy=inbound_disc,
        total_outbound_cash_confirmed=total_outbound_confirmed,
        total_outbound_cash_journal=total_outbound_journal,
        outbound_cash_discrepancy=outbound_disc,
        total_fees_collected=total_fees,
        settled_request_count=len(settled_ids),
        unapproved_settled_count=len(unapproved_settled),
        negative_balance_positions=negative_positions,
        discrepancies=discrepancies,
    )
