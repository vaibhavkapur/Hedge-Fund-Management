# ADR 0001 — Exact Decimal Arithmetic and Append-Only Immutable SQLite Journaling

**Status:** Accepted  
**Date:** 2026-11-28  
**Project:** `hedge-fund-fee-accounting-engine`

## Context

Hedge fund management and performance fee accounting involves multi-period state transitions (high-water marks, hurdle baselines, intra-period accruals, capital flows, and NAV restatements). Small floating-point drift (e.g., binary IEEE-754 rounding errors in `0.1 + 0.2`) or mutable ledger updates can cause unexplainable penny breaks, destroy audit lineage, and violate double-entry reconciliation invariants.

## Decision

1. **Reject binary floats at the schema boundary**: All monetary values, rates, units, and NAVs per unit are parsed strictly into `decimal.Decimal` from strings or integers (`to_decimal` raises `FLOAT_NOT_ALLOWED` for Python `float`).
2. **Explicit intermediate precision and minor-unit quantization**: Domain calculations execute inside a `decimal.localcontext()` with `prec=28`, quantizing final monetary amounts to currency-specific minor units (`USD=2`, `EUR=2`, `GBP=2`, `CHF=2`, `JPY=0`) using `ROUND_HALF_EVEN` (configurable in `config/default_engine_config.json`).
3. **Pure domain / I/O separation**: `domain.py` and `journal.py` perform zero filesystem, database, or network I/O, making every calculation deterministically testable and replayable.
4. **Immutable SQLite ledger**: `journal_entries` and `journal_lines` are protected by SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers that raise `ABORT`. Fee restatements are recorded strictly as new versioned runs (`version = prior_version + 1`) containing explicit reversing journal entries (`supersedes_entry_id`) plus corrected entries.
5. **Deterministic content hashing**: Every run records `input_hash` and `output_hash` (SHA-256 over canonical sorted JSON) to support audit-grade replay verification.

## Consequences

- **Positive**: Exact `0.00` reconciliation against hand-calculated reference fixtures and textbook worked examples; tamper-evident audit trail; zero third-party runtime dependencies.
- **Trade-offs**: Callers must serialize numeric inputs as strings rather than JSON floats; single-file SQLite is intended for offline/research prototyping rather than high-concurrency multi-writer production environments.
