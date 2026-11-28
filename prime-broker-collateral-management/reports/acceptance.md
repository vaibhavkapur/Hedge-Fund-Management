# Acceptance Criteria Evidence — Prime Broker Collateral Management

**Environment Observed:** Python 3.9.6 (CPython), macOS (offline sandbox)  
**Test Suite Observed:** `25/25` passed (`0` failed, `0` errors, `0` skipped) in `19.0 ms` (recorded in `reports/benchmark_and_validation.json`)  
**Fixture Provenance:** All broker statements, collateral policies, haircuts, FX fixings, and financing rates are 100% synthetic educational fixtures created for this prototype. No live capital, customer records, credentials, or proprietary artifacts are included.

## 1. Project-Specific Acceptance Criteria Matrix

| Criterion | Status | Automated Test / Artifact Evidence | Observed Result |
|---|---|---|---|
| **No collateral double-counting** | **PASS** | `tests/test_validation.py::test_duplicate_lot_inside_single_statement_rejected`<br>`tests/test_domain_core.py::test_global_double_counting_across_brokers_raises`<br>`tests/test_domain_core.py::test_failed_transfer_quarantined_and_prevents_double_counting`<br>`tests/test_substitution_and_journal.py::test_substitution_rejects_externally_encumbered_lot_double_counting` | Intra-statement duplicate lots rejected (`ValidationError`), cross-broker duplicate active lots rejected (`DoubleCountingError`), externally encumbered substitution pledges rejected (`DoubleCountingError`), and failed/unsettled inbound transfers quarantined (`0.00` counted value). |
| **Balances and accruals reconcile** | **PASS** | `tests/test_domain_core.py::test_reference_oracle_matches_hand_calculation_exactly`<br>`tests/test_day_count_and_boundaries.py::test_financing_accrual_across_leap_year_boundary_reconciles_exact_cents`<br>`reports/baseline_reference_output.json` | Reference oracle (`RUN-PB-ALPHA-2026-03-16-V1-B18F061C76`) matches hand-calculated posted collateral (`1520000.00 USD`), unencumbered buffer (`344000.00 USD`), and 3-day ACT/360 net financing accrual (`-73.54 USD`) with `0` breaks. Leap-year `ACT_ACT_ISDA` split (`2024-12-30` to `2025-01-02`) reconciles exactly to `-492.25 USD`. |
| **Shortfalls flagged before simulated settlement** | **PASS** | `tests/test_domain_core.py::test_failed_transfer_quarantined_and_prevents_double_counting`<br>`tests/test_integration_scenarios.py::test_haircut_increase_turns_surplus_into_margin_call`<br>`tests/test_substitution_and_journal.py::test_substitution_approved_and_shortfall_blocked_before_settlement`<br>`reports/demo_run_output.json` | Failed transfer at `PB-BETA` flags `115000.00 EUR` `MARGIN_CALL` (`flagged_before_settlement=True`). Haircut increase at `PB-ALPHA` flags `90000.00 USD` `MARGIN_CALL`. Substitution `SUB-BLOCK-SHORTFALL-002` blocks settlement (`settlement_permitted=False`, `AUTO_FLAGGED_SHORTFALL`, `required_top_up=956300.00 USD`) and leaves pre-settlement positions untouched. |
| **All required test scenarios pass (or unavailable dependencies marked not run)** | **PASS** | `tests/` (all 5 test modules), `scripts/verify_reproducibility.py`, `reports/benchmark_and_validation.json` | All 25 automated tests pass; reproducibility script verifies bit-for-bit identical outputs across repeated runs. Live PostgreSQL database execution is explicitly marked **NOT RUN** below. |
| **README documents setup, demo, validation, assumptions, and limitations** | **PASS** | `README.md`, `docs/architecture.md`, `docs/contracts.md`, `docs/integration.md`, `docs/runbook.md`, `docs/adr/ADR-001-domain-ledger-and-precision.md` | All documentation artifacts complete and linked from `README.md`; measured observations are explicitly distinguished from synthetic fixture assumptions and targets. |
| **No real transactions, customer data, credentials, or proprietary artifacts** | **PASS** | Source, `config/`, `data/fixtures/`, `tests/`, `reports/` | Verified synthetic identifiers only (`PB-ALPHA`, `PB-BETA`, `ACC-MAIN-01`, `LOT-*`). |

## 2. Plan Test & Validation Matrix Coverage

| Test Area | Required Scenario | Test Evidence | Status |
|---|---|---|---|
| Domain correctness | Duplicate statement | `test_duplicate_statement_idempotent_skip_and_conflict_rejection` | PASS |
| Domain correctness | FX change | `test_fx_change_revalues_collateral_and_triggers_margin_call` | PASS |
| Domain correctness | Failed transfer | `test_failed_transfer_quarantined_and_prevents_double_counting` | PASS |
| Integration & resilience | Ineligible collateral | `test_ineligible_collateral_gets_100pct_haircut_and_reconciliation_break` | PASS |
| Integration & resilience | Haircut increase | `test_haircut_increase_turns_surplus_into_margin_call` | PASS |
| Boundary & failure behavior | Financing day-count boundaries | `tests/test_day_count_and_boundaries.py` (6 tests: reverse date, zero-day, leap-year `ACT_ACT_ISDA` year-boundary split, Feb 28->Mar 1 leap year, `30/360` month-end clamping) | PASS |
| Reproducibility | Same versioned inputs/config | `scripts/verify_reproducibility.py` & `test_end_to_end_demo_bundle_reproducibility` | PASS |
| Operational review | Missing config, unsupported inputs, restart/rerun | `test_missing_configuration_raises_explicit_error`, `test_invalid_fixtures_raise_explicit_errors`, `test_sqlite_journal_immutability_and_persistent_replay` | PASS |

## 3. Observed Synthetic Micro-Benchmark (Not a Production SLA)

Measured via `scripts/generate_reports.py` (`reports/benchmark_and_validation.json`) on 50 iterations of 200 synthetic positions in memory:

- **Mean latency:** `0.948 ms` per 200-position valuation
- **p50 / p95 latency:** `0.945 ms` / `0.992 ms`
- **Synthetic throughput:** `~210,935` positions/sec
- **Deterministic posted collateral output:** `18407100.00 USD`

*(Note: This is an in-memory single-process synthetic measurement excluding network/database I/O and is not a production throughput guarantee.)*

## 4. Explicitly Untested / Not-Run Capabilities

| Capability | Status | Reason & Mitigation |
|---|---|---|
| Live PostgreSQL 14+ journal integration (`PostgresJournalAdapter`) | **NOT RUN** | Neither PostgreSQL (`psql`) nor optional driver `psycopg[binary]==3.2.3` is available in this offline environment. The PostgreSQL DDL (`src/pb_collateral/sql/postgres_schema.sql`) and explicit missing-driver error path are verified by unit tests, and trigger-enforced append-only persistence is fully verified locally via `SQLiteJournalStore`. |
| Real prime broker feed parsers (FIX / SWIFT MT535 / broker SFTP CSVs) | **OUT OF SCOPE** | Excluded per `plan.md`; canonical alias normalization handles JSON fixtures. |
