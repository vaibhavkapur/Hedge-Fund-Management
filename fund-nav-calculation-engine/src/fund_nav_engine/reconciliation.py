"""Pure ledger reconciliation contract comparing source records against general ledger balances."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Dict, List, Sequence, Set, Tuple

from fund_nav_engine.models import (
    AccrualDirection,
    CapitalFlowRecord,
    FlowType,
    InstrumentType,
    LedgerAccountType,
    ReconciliationBreak,
    ReconciliationReport,
    ValuationConfig,
    ValuationInputBundle,
    quantize_decimal,
)

ZERO = Decimal("0")


def reconcile_ledger(
    bundle: ValuationInputBundle,
    eligible_flows: Sequence[CapitalFlowRecord],
    config: ValuationConfig,
) -> ReconciliationReport:
    """Reconcile expected sub-ledger balances against reported ledger_balances.

    Comparisons are executed in each account's native currency at currency precision.
    """
    expected: Dict[Tuple[LedgerAccountType, str], Decimal] = defaultdict(lambda: ZERO)
    reported: Dict[Tuple[LedgerAccountType, str], Decimal] = defaultdict(lambda: ZERO)

    for pos in bundle.positions:
        if pos.instrument_type == InstrumentType.CASH:
            expected[(LedgerAccountType.CASH, pos.currency)] += pos.quantity
        else:
            expected[(LedgerAccountType.SECURITIES_COST, pos.currency)] += pos.cost_basis

    for acc in bundle.accruals:
        if acc.direction == AccrualDirection.ASSET:
            expected[(LedgerAccountType.RECEIVABLES, acc.currency)] += acc.amount
        else:
            expected[(LedgerAccountType.ACCRUED_EXPENSES, acc.currency)] += acc.amount

    for liab in bundle.liabilities:
        expected[(LedgerAccountType.PAYABLES, liab.currency)] += liab.amount

    for flow in eligible_flows:
        if flow.flow_type == FlowType.SUBSCRIPTION and flow.amount is not None:
            expected[(LedgerAccountType.CAPITAL_SUBSCRIPTIONS, flow.currency)] += flow.amount
        elif flow.flow_type == FlowType.REDEMPTION and flow.amount is not None:
            expected[(LedgerAccountType.CAPITAL_REDEMPTIONS, flow.currency)] += flow.amount

    for bal in bundle.ledger_balances:
        reported[(bal.account_type, bal.currency)] += bal.balance

    keys: Set[Tuple[LedgerAccountType, str]] = set(expected.keys()) | set(reported.keys())
    checks: List[ReconciliationBreak] = []

    for account_type, currency in sorted(keys, key=lambda k: (k[0].value, k[1])):
        prec = config.currency_precision(currency)
        exp_q = quantize_decimal(expected[(account_type, currency)], prec, config.rounding_mode)
        rep_q = quantize_decimal(reported[(account_type, currency)], prec, config.rounding_mode)
        diff = quantize_decimal(exp_q - rep_q, prec, config.rounding_mode)
        passed = abs(diff) <= config.ledger_tolerance
        checks.append(
            ReconciliationBreak(
                account_type=account_type,
                currency=currency,
                expected_amount=exp_q,
                ledger_amount=rep_q,
                difference=diff,
                tolerance=config.ledger_tolerance,
                passed=passed,
            )
        )

    passed_count = sum(1 for c in checks if c.passed)
    reconciled = passed_count == len(checks)
    return ReconciliationReport(
        reconciled=reconciled,
        total_checks=len(checks),
        passed_checks=passed_count,
        breaks=tuple(checks),
    )
