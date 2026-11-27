# ADR-002: Double-Entry General Ledger with Exact `Decimal` Arithmetic

- **Status**: Accepted
- **Date**: 2026-11-27
- **Context**: Hedge fund accounting requires deterministic reconciliation between portfolio NAV (IBOR) and general ledger balances (ABOR) across multiple currencies without binary floating-point rounding drift.
- **Decision**: Represent all quantities, prices, FX rates, fees, and balances as `decimal.Decimal` quantized with `ROUND_HALF_UP`. Enforce strict per-currency `sum(debits) == sum(credits)` inside the `JournalEntry` Pydantic validator (raising `LedgerBalanceError` before persistence). Post opening capital, trade fills, incremental EOD MTM entries, and daily management fee accruals idempotently.
- **Consequences**: Guarantees balanced trial balances across all currencies and exact `$0.00` discrepancy between Portfolio NAV and Ledger NAV; any unbalanced manual adjustment is rejected and logged in the audit trail.
