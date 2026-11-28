# ADR 0001: Exact Decimal Arithmetic, Immutable Journal Versioning, and Dual SQLite/PostgreSQL Persistence

- **Status:** Accepted
- **Date:** 2026-11-28
- **Project:** Investor Subscription and Redemption Platform (`investor-subscription-and-redemption-platform/`)

## Context

Capital flow accounting requires exact share and cash reconciliation with zero binary floating-point drift, full auditability of every state transition and correction (including NAV restatements), and reproducible execution in both offline research environments (Python 3.9 stdlib) and relational server environments (FastAPI + PostgreSQL).

## Decision

1. **Strict `Decimal` Contract Boundary:** All monetary amounts, fee amounts, NAV per share values, and share quantities are parsed exclusively from decimal strings, integers, or `Decimal` objects. Python `float` inputs are explicitly rejected with `ValidationError`. Rounding precision (`cash_decimal_places=2`, `share_decimal_places=6`, `nav_decimal_places=6`) and mode (`ROUND_HALF_UP`) are loaded from versioned configuration (`config/default_config.json`) whose SHA-256 digest is embedded in every snapshot and report.
2. **Append-Only Journal and Hash-Chained Audit Log:** Settled share allocations are stored in an append-only `share_journal` table. Corrections due to NAV restatement never overwrite prior rows; instead, the platform marks the old NAV `SUPERSEDED`, publishes a new NAV version (`v+1`), and appends a `NAV_RESTATEMENT_ADJUSTMENT` journal row linking `supersedes_entry_id`. Every mutation and blocked violation appends an `AuditEvent` linked by a SHA-256 hash chain.
3. **Repository Protocol with Offline SQLite Default and PostgreSQL Adapter:** The domain and `PlatformService` depend only on the `Repository` protocol. `SQLiteRepository` executes all relational storage, foreign keys, and restart-recovery tests offline with zero third-party packages. `PostgresRepository` and `src/isrp/migrations/001_initial_schema.sql` maintain schema parity using `NUMERIC(20,2)`, `NUMERIC(24,6)`, `TIMESTAMPTZ`, and `JSONB` for environments with PostgreSQL and `psycopg`.

## Consequences

- **Positive:** Exact hand-verifiable reconciliation against `data/fixtures/reference_oracle.json`; deterministic replay producing identical `logical_state_hash` across runs; immediate offline testability on standard Python 3.9+.
- **Trade-offs / Limitations:** Live PostgreSQL and FastAPI runtime verification requires installing optional extras (`requirements.txt`) and provisioning a PostgreSQL server, and is explicitly marked `NOT_RUN` in `reports/acceptance.md` when executed in an offline environment.
