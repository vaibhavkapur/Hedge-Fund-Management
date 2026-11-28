# Acceptance Evidence Report — Fund NAV Calculation Engine

## 1. Execution Environment & Metadata

- **Runtime:** Python `3.9.6` on `macOS-26.5.1-arm64-arm-64bit`
- **Dependencies:** Python standard library only (`decimal`, `sqlite3`, `dataclasses`, `unittest`, `hashlib`, `json`, `argparse`)
- **Validation Command:** `python3 scripts/run_validation.py`
- **Raw Machine-Readable Artifacts:**
  - `reports/validation_summary.json`
  - `reports/demo_run_output.json`
  - `reports/benchmark_results.json`
  - `reports/demo_nav_store.sqlite`

> **Data Provenance Notice:** All measurements below are actual observed results executed locally on **synthetic deterministic fixtures** authored for this prototype (`data/fixtures/` and synthetic benchmark inputs). No live market data, real investor records, or production systems were used.

## 2. Project-Specific Acceptance Criteria Matrix (`plan.md` Section 5)

| Acceptance Criterion | Status | Evidence & Verification |
|---|---|---|
| NAV equals assets minus liabilities at currency precision | **PASS** | Verified in `calculate_nav` invariant assertion, `test_reference_scenario_matches_hand_calculated_oracle` (`2,141,700.00 - 48,325.00 = 2,093,375.00` USD), `test_late_correction_restatement_matches_oracle` (`2,161,700.00 - 48,575.00 = 2,113,125.00` USD), `test_zero_shares_dormant_full_redemption_and_seeding`, `test_three_way_penny_reconciliation_and_class_specific_accruals`, and `reports/demo_run_output.json` (`v1_nav_identity_exact: true`, `v2_nav_identity_exact: true`). |
| Published snapshots immutable | **PASS** | Enforced at database layer via SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers (`trg_prevent_frozen_snapshot_update`, `trg_prevent_frozen_snapshot_delete`, `trg_prevent_audit_update`, `trg_prevent_audit_delete`). Verified in `test_frozen_snapshot_immutability_and_audit_journal_protection` (application calls and raw SQL `UPDATE`/`DELETE` raise `ImmutabilityViolationError` `NAV-5001`). |
| Restatements link to prior versions | **PASS** | Enforced via `prior_snapshot_id` foreign key and `RestatementError` preconditions (`status == 'frozen'`, non-empty reason). Verified in `test_late_correction_restatement_matches_oracle`, `test_restatement_preconditions_and_multi_version_chain` (`v1 -> v2 -> v3` lineage), and `reports/demo_run_output.json` (`v2` `SNAP-FUND-ALPHA-01-2026-03-31-v2-bb0a0dd13ec0` links to `v1` `SNAP-FUND-ALPHA-01-2026-03-31-v1-8bcf72eb1caa`). |
| All required test scenarios pass in supported environment | **PASS** | `23 / 23` automated tests passed (`0` failures, `0` errors) in `0.031s` via `python3 scripts/run_validation.py`. |
| README documents setup, demo, validation, assumptions, and limitations | **PASS** | Documented in `README.md`, `docs/architecture.md`, `docs/contracts.md`, `docs/integration.md`, and `docs/runbook.md`, explicitly distinguishing measured local observations from synthetic fixtures and out-of-scope targets. |
| No real transactions, customer data, credentials or proprietary artifacts | **PASS** | Verified: all source files, fixtures, logs, and reports contain strictly synthetic identifiers (`FUND-ALPHA-01`, `EQ-US-ACME`, `EQ-EU-BETA`) and zero secrets or external proprietary dependencies. |

## 3. Test & Validation Matrix Coverage (`plan.md` Section 4)

| Test Area | Required Scenario | Automated Test(s) | Observed Result |
|---|---|---|---|
| Domain correctness | Hand-calculated reference oracle (v1 & v2), Missing valuation, FX mismatch, Late correction, Stale prices | `tests/test_domain_correctness.py` (7 tests) | **PASS** (exact `0.00` Decimal agreement against `reference_expected.json`) |
| Integration and resilience | Zero shares (dormant, full redemption, seeded, all-zero residual), Class-level allocation (penny reconciliation & class-specific accruals), Adapters | `tests/test_integration_and_allocation.py` (4 tests) | **PASS** |
| Boundary and failure behavior | Flow timing (exact cutoff, post-cutoff defer/reject, future/backdated, settlement before trade, over-redemption), Ledger reconciliation breaks, Immutability triggers, Restatement preconditions | `tests/test_boundaries_and_immutability.py` (7 tests) | **PASS** |
| Reproducibility | Order-invariant canonical hashing, persisted SQLite replay verification, synthetic benchmark hash stability | `tests/test_reproducibility_and_ops.py` (`test_reproducibility_order_invariance_and_hash_stability`, `test_restart_rerun_idempotency_and_replay_verification`) + `reports/benchmark_results.json` | **PASS** (100% identical SHA-256 hashes across shuffled inputs, DB restarts, and 5 benchmark runs) |
| Operational review | Missing/invalid config (`NAV-1001`), unsupported/invalid inputs & float rejection (`NAV-1002`), restart/rerun idempotency, CLI workflow & non-zero exit code `2` | `tests/test_reproducibility_and_ops.py` (5 tests) | **PASS** |

## 4. Measured Synthetic Benchmark Summary (`reports/benchmark_results.json`)

- **Workload:** 500 multi-currency equity positions (`USD`, `EUR`, `GBP`, `JPY`), 10 share classes (`USD`/`EUR`), 10 class-specific fee accruals, full general ledger reconciliation, 5 iterations.
- **Observed Latency:** min `12.244 ms`, mean `12.573 ms`, max `12.885 ms` (`39,768.4` positions/sec mean).
- **Determinism:** `all_output_hashes_identical: true` (`6484fd1ccef46e2df952931bf26d72682598f9f70188394213a6ee98aec76579`), `reconciled: true`, `net_assets: "16347046.90"`.

## 5. Explicitly Untested / Out-of-Scope Capabilities

| Capability | Status | Explanation |
|---|---|---|
| PostgreSQL persistence backend | **Not run / Out of scope for local validation** | SQLite stdlib backend with identical trigger-based immutability semantics is implemented and tested; no local PostgreSQL server or driver (`psycopg`) is installed in this environment. |
| Live market data, FX, custody, or TA feeds | **Excluded (out of scope)** | Per `plan.md` Section 1, live capital, production credentials, and live external feeds are explicitly excluded. |
| High-water-mark performance fee crystallization & investor equalization | **Out of scope** | Fee accruals are consumed as canonical `AccrualRecord` inputs (adapter contract provided for `hedge-fund-fee-accounting-engine`). |
| Multi-node concurrent write locking / RBAC authentication | **Out of scope** | Educational single-node prototype; production deployment requires separate security, operational, legal, and risk review. |
