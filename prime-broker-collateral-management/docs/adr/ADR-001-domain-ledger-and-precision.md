# ADR-001: Exact Decimal Precision, Pure Domain Isolation, and Append-Only Journal

- **Status:** Accepted
- **Date:** 2026-11-28
- **Project:** Prime Broker Collateral Management (Project 51 of 67)

## Context

Collateral valuation, haircut application, FX conversion, financing accruals, and margin call thresholds require deterministic penny-exact reproducibility. Floating-point drift causes spurious reconciliation breaks across reruns, while mutable database updates destroy auditability for statement restatements and failed transfers.

## Decision

1. **Exact Decimal Arithmetic:** Represent all prices, quantities, balances, FX rates, haircuts, and accruals using Python `decimal.Decimal` parsed strictly from strings or integers. Reject raw JSON floats during schema validation. Quantize monetary outputs explicitly per ISO-4217 currency minor units (`ROUND_HALF_EVEN` default; `ROUND_CEILING` for margin calls; `ROUND_FLOOR` for excess recalls).
2. **Pure Domain Isolation:** Keep `valuation.py`, `financing.py`, `reconciliation.py`, and `substitution.py` free of database, filesystem, and network I/O so every calculation is a pure function of immutable inputs and versioned policies.
3. **Append-Only Versioned Journal:** Store statements, reconciliation runs, margin calls, and substitution reviews in append-only relational tables protected by `BEFORE UPDATE` and `BEFORE DELETE` database triggers. Implement `SQLiteJournalStore` as the zero-dependency default and `PostgresJournalAdapter` + `postgres_schema.sql` as the PostgreSQL 14+ adapter boundary.

## Consequences

- **Positive:** Bit-for-bit reproducible runs, zero third-party runtime dependencies for core execution, full audit trail for statement corrections, and fast unit/integration testing (<50ms for the full suite).
- **Trade-offs:** Callers must pass decimal values as strings in JSON payloads; live PostgreSQL integration tests require an external Postgres instance and optional `psycopg` driver.
