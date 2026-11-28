# Acceptance & Validation Report — Investor Subscription and Redemption Platform

**Measured Run Environment:**
- **Python:** `3.9.6`
- **OS / Platform:** `macOS-26.5.1-arm64-arm-64bit`
- **SQLite:** `3.51.0`
- **Config SHA-256 (`config/default_config.json`):** `7b887373847783aac59a53ff71505a07bf279a3088794799c4e201d647f4dc70`
- **Deterministic Seed:** `20260331`
- **Raw Machine-Readable Evidence:** [`reports/validation_results.json`](validation_results.json), [`reports/demo_baseline_output.json`](demo_baseline_output.json)

> All measured values below come from deterministic executions against synthetic fixtures (`data/fixtures/`). No real investor PII, live capital, or production credentials were used.

## 1. Project-Specific Acceptance Criteria

| Acceptance Criterion | Status | Measured Evidence |
|---|---|---|
| Shares and cash reconcile exactly | **PASS** | `demo_baseline_output.json`: Baseline register `963.259281` == journal `963.259281` (`share_discrepancy = 0.000000`), inbound cash `150000.00` == journal `150000.00` (`0.00`), outbound cash `26532.67` == journal `26532.67` (`0.00`), fees `1633.33`. Post-restatement register `971.259281` == journal `971.259281` (`0.000000`). Verified by `test_end_to_end_reconciliation_and_sqlite_restart_recovery` and `test_all_reference_oracle_cases_match_exact_decimal`. |
| Unapproved requests cannot settle | **PASS** | `test_reviewer_separation_and_unapproved_request_cannot_settle` asserts `UnapprovedRequestError` on both `price_request()` and `settle_request()` prior to maker-checker approval, and `reconciliation.unapproved_settled_count == 0` in `demo_baseline_output.json`. |
| All changes auditable | **PASS** | Hash-chained append-only `audit_events` verified valid (`audit_chain_valid = true`, `27` events in demo). `test_audit_chain_tamper_detection_and_deterministic_replay` verifies tampering a single SQLite audit row causes `verify_audit_trail()` to fail. |
| Required test scenarios pass (unavailable deps marked not run) | **PASS** | `19 / 19` automated tests passed in `90.12 ms` (`validation_results.json`). Unavailable live PostgreSQL and FastAPI/Uvicorn runtimes are explicitly marked `NOT_RUN` in Section 3 below. |
| README documents setup, demo, validation, assumptions, and limitations | **PASS** | [`README.md`](../README.md) documents quickstart, demo/validation commands, synthetic data notice, measured baseline example, dependency licenses, fixture provenance, and limitations. |
| No real transactions, customer data, credentials, or employer/SAAT artifacts | **PASS** | All fixtures use explicit `INV-SYN-*`, `SYN-DOC-*`, `SYN-ACCT-*`, `WIRE-SYN-*` synthetic identifiers; no credentials or external proprietary imports exist. |

## 2. Test and Validation Matrix Mapping

| Test Area | Required Scenario | Automated Test(s) | Measured Result |
|---|---|---|---|
| Domain correctness | Duplicate request and delayed cash | `tests/test_domain_scenarios.py::test_duplicate_request_is_blocked_and_audited`, `test_delayed_cash_opens_exception_blocks_settlement_until_reviewed`, `tests/test_pricing_and_oracle.py::test_all_reference_oracle_cases_match_exact_decimal` | **PASS** — Duplicate idempotency key raises `DuplicateRequestError` & records exception; delayed cash flags `DELAYED_CASH`, blocks settlement until independent review, then settles `316.800000` shares cleanly; all 4 oracle cases match exactly. |
| Integration and resilience | Cutoff violation and insufficient shares | `tests/test_integration_scenarios.py::test_cutoff_violation_and_insufficient_shares`, `test_end_to_end_reconciliation_and_sqlite_restart_recovery`, `test_http_api_router_end_to_end` | **PASS** — Late submission raises `CutoffViolationError` with `CUTOFF_VIOLATION` exception; excess redemption and double-redemption against pending locks raise `InsufficientSharesError`; SQLite restart reload preserves identical `logical_state_hash`; HTTP router passes E2E. |
| Boundary and failure behavior | NAV restatement and reviewer separation | `tests/test_boundary_and_failure.py::test_nav_restatement_posts_immutable_adjustment_and_enforces_separation`, `test_reviewer_separation_and_unapproved_request_cannot_settle`, `test_pre_settlement_cancellation_works` | **PASS** — NAV restatement (`125.000000 -> 123.750000`) supersedes v1, appends `+8.000000` `NAV_RESTATEMENT_ADJUSTMENT` entry, and reconciles to `800.000000` shares; `maker_id == checker_id` raises `ReviewerSeparationError` across request review, exception resolution, and NAV restatement. |
| Reproducibility | Same versioned inputs/configuration and fixed seed | `scripts/run_validation.py` dual replay & `tests/test_boundary_and_failure.py::test_audit_chain_tamper_detection_and_deterministic_replay` | **PASS** — Run 1 & Run 2 baseline SHA-256 `04de11e3deb06dec4387e08e4b7e3237dfc2ade912c75b8794f48a78870bd189` and post-restatement SHA-256 `f7e919f61a950bb5ac1f1fc2f10431ee504c9d867ecbe5078e1427afde0cb24c` match identically. |
| Operational review | Missing configuration, unsupported inputs, restart/rerun | `tests/test_contracts_and_config.py::test_missing_config_raises_configuration_error`, `test_invalid_config_rejects_missing_keys_and_float_decimals`, `test_invalid_inputs_from_fixture_fail_explicitly`, `test_float_amount_and_naive_timestamp_rejected` | **PASS** — Explicit `ConfigurationError` and `ValidationError` raised with machine-readable error codes; zero silent successes; recovery procedures documented in [`docs/runbook.md`](../docs/runbook.md). |

## 3. Explicitly Untested / `NOT_RUN` Capabilities

| Capability | Status | Explanation |
|---|---|---|
| Live PostgreSQL persistence (`PostgresRepository` against a PostgreSQL 15+ server) | **NOT_RUN** | `psycopg` and a live PostgreSQL server are not installed in this offline validation environment. Relational persistence, foreign keys, and restart recovery were executed via `SQLiteRepository`; PostgreSQL DDL (`src/isrp/migrations/001_initial_schema.sql`) and DSN validation were verified structurally. |
| Live FastAPI / Uvicorn ASGI server (`src/isrp/api/fastapi_app.py`) | **NOT_RUN** | `fastapi`, `pydantic`, and `uvicorn` are not installed in this offline validation environment. Identical endpoint routing, JSON serialization, and HTTP error status codes (`200`/`201`/`403`/`404`/`409`/`422`) were verified via `isrp.api.http_server.dispatch_http_request`. |
| `pytest` test runner | **NOT_RUN** | `pytest` is not installed offline; all 19 tests were executed via Python stdlib `unittest` (`scripts/run_validation.py`) and follow `pytest`-compatible naming. |
