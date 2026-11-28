# ADR-0001: Architecture, Zero-Dependency Core, and Storage Parity

- **Status:** Accepted
- **Date:** 2026-11-28
- **Project:** Regulatory Reporting Engine (`regulatory-reporting-engine/`)

## Context

`plan.md` proposes a stack of Python, Pydantic, PostgreSQL, and XML/CSV schema validation while explicitly noting:

> "Numerical thresholds and technology choices in this plan are proposed engineering targets, not measured results or guarantees. Adjust them only with documented rationale and retain baseline evidence."

The target execution environment is an offline macOS workstation with Python 3.9.6 and SQLite 3.51.0, without pre-installed third-party packages (`pydantic`, `psycopg`, `lxml`, `xmlschema`) or a running PostgreSQL server. Additionally, sibling project `fund-nav-calculation-engine/` follows a zero-dependency Python 3.9+ stdlib core pattern.

## Decision

1. **Pure Python 3.9+ Standard Library Core with Strict Typed Contracts:**
   - Implement frozen dataclass domain models (`models.py`), ISO identifier validators (`identifiers.py`), declarative XML/CSV schema validators (`schemas.py`), and pure mapping/reconciliation engines using only Python standard library modules (`decimal`, `dataclasses`, `xml.etree.ElementTree`, `csv`, `sqlite3`, `hashlib`, `json`, `re`, `unittest`).
   - Declare `pydantic`, `psycopg[binary]`, `xmlschema`, and `lxml` as optional dependency extras in `pyproject.toml` so downstream deployments can integrate them without breaking clean offline checkouts.

2. **Dual Storage Design — SQLite Implementation + PostgreSQL DDL Parity:**
   - Define a storage protocol (`JournalStore` in `store.py`).
   - Ship `SqliteJournalStore` with database-level `BEFORE UPDATE` / `BEFORE DELETE` immutability triggers and cryptographic audit hash chaining for offline deterministic execution and CI.
   - Ship matching PostgreSQL DDL (`sql/schema_postgres.sql`) using `JSONB`, `TIMESTAMPTZ`, `BIGSERIAL`, and PL/pgSQL immutability triggers. Explicitly mark live PostgreSQL integration tests as **not run** in offline acceptance evidence rather than mocking or faking coverage.

3. **Exact `Decimal` Arithmetic & No Arbitrary Expression Evaluation:**
   - Reject `float` inputs on monetary and ratio fields to eliminate binary floating-point drift.
   - Implement declarative aggregation (`sum`, `abs_sum`, `count`, `max_by_group`) and derivation primitives (`ratio`, `percent_ratio`) with explicit `ROUND_HALF_UP` quantization instead of `eval()`.

4. **Structural Prohibition of Automated Filing:**
   - Enforce `automated_submission_enabled: false` in configuration validation (`config.py`) and implement `submit_to_regulator()` solely as a failing guard (`REG-9001`).

## Consequences

- **Positive:** Any developer can clone and run the full test suite, demo, CLI, and acceptance benchmark on standard Python 3.9+ with zero network access or package installation issues.
- **Positive:** Deterministic replay and zero-deviation oracle matching are guaranteed across platforms.
- **Negative / Trade-off:** Full W3C XSD 1.1 features (complex nested choice groups, external XSD imports) are not implemented in the stdlib validator; production use with official regulator XSDs should enable the optional `xmlschema`/`lxml` validation layer after compliance review.
- **Negative / Trade-off:** Live PostgreSQL connection pooling and concurrent multi-writer locking are untested in the offline environment and require a dedicated integration test stage with a running Postgres instance.
