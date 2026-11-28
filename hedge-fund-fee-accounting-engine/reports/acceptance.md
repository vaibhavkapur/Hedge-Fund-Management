# Acceptance Evidence — Hedge Fund Fee Accounting Engine

**Date Recorded:** 2026-11-28  
**Runtime Environment (Measured):** Python `3.9.6`, `macOS-26.5.1-arm64-arm-64bit` (`arm64`), `config_version = 1.0.0`, `schema_version = 1.0.0`, `ROUND_HALF_EVEN`, `intermediate_precision = 28`, `oracle_comparison_tolerance = 0.00` (see `reports/runtime_metadata.json`).

> **Observation vs. Synthetic Fixture Note:** All financial scenarios and worked examples evaluated below are **synthetic educational fixtures** hand-calculated from first principles (`data/fixtures/`). All test counts, replay hashes, reconciliation checks, and execution timings below are **measured observations** from executing `bash scripts/validate_all.sh` on this machine.

## 1. Project-Specific Acceptance Criteria Matrix

| # | Acceptance Criterion | Status | Measured Evidence |
|---|---|---|---|
| 1 | **Fee outputs match hand-calculated fixtures exactly** | PASS | `129 / 129` field comparisons across 4 multi-period scenarios (`9 + 31 + 28 + 32 = 100` checks) and 5 independent worked examples (`29` checks) matched with `0` discrepancies at `0.00` tolerance (`reports/demo_summary.json`, `reports/worked_examples_comparison.json`). |
| 2 | **No negative or duplicate accrual unless specified** | PASS | Verified by `reconcile_run` invariants (`no_negative_line_amounts`, `no_negative_fee_accrual`, `no_duplicate_accruals`), `test_no_negative_delta_when_intra_period_reversal_disabled`, `test_duplicate_accrual_run_blocked`, and SQLite `UNIQUE (agreement_id, valuation_date, version)`. |
| 3 | **All fee entries reconcile** | PASS | Every persisted run (`17 / 17` runs in `reports/demo_ledger.sqlite`) passed all 8 reconciliation checks: balanced double-entry journals (`imbalance = 0.00`), management fee sum match, performance fee sum match, crystallized fee sum match, and Gross-to-Net NAV bridge (`diff = 0.00`). |
| 4 | **All required test scenarios pass in the supported environment** | PASS | `python3 -m unittest discover -s tests -v`: **20 / 20 tests passed in 0.319s** (`OK`). |
| 5 | **README documents setup, demo, validation, assumptions, and known limitations** | PASS | Documented in `README.md`, `docs/architecture.md`, `docs/contracts.md`, `docs/integration.md`, `docs/runbook.md`, and `docs/adr/0001-exact-decimal-and-immutable-journal.md`. |
| 6 | **No real transactions, customer data, credentials, or proprietary employer/SAAT artifacts** | PASS | Verified: all source code, fixtures, logs, and reports use purely synthetic identifiers (`INV-001`, `INV-ALPHA`, `LOT-A1`, etc.) and zero credentials or proprietary code. |

## 2. Test and Validation Matrix Mapping

| Test Area | Required Scenario | Automated Tests & Raw Reports | Measured Result |
|---|---|---|---|
| **Domain correctness** | Loss and recovery below high-water mark | `tests/test_domain_hwm_loss_recovery.py`, `reports/scenario_loss_recovery.json` | **PASS** — 4 quarters evaluated, `31 / 31` oracle checks exact (`0` discrepancies); Q2 drawdown & Q3 partial recovery below `HWM=107.60` yielded `0.00` perf fee and preserved HWM; Q4 charged `13,754.72` only on excess above `107.60`. |
| **Integration and resilience** | Mid-period capital flows and fee corrections | `tests/test_flows_corrections_integration.py`, `reports/scenario_flows_corrections.json` | **PASS** — `28 / 28` oracle checks exact (`0` discrepancies); mid-quarter redemption crystallized `6,200.00`, new subscription created `LOT-B2` with own HWM (`108.00`), v2 NAV restatement emitted exact reversing + corrected journals, marked v1 `SUPERSEDED`, and SQLite triggers blocked `UPDATE`/`DELETE`. |
| **Boundary and failure behavior** | Hurdle and crystallization boundaries | `tests/test_hurdle_and_crystallization.py`, `tests/test_calendar_and_daycount.py`, `reports/scenario_hurdle_boundaries.json` | **PASS** — `32 / 32` oracle checks exact (`0` discrepancies); Q1 above HWM/below hurdle = `0.00`, Q2 interim accrual left HWM unchanged, Q3 pullback emitted `6,000.00` reversal, Q4 annual crystallization settled `10,000.00` payable and reset HWM to `112.00`. Exact hurdle boundary (`102.00` vs `102.01`) and skipped crystallization boundary rejection verified. |
| **Reproducibility** | Same versioned inputs/config produce identical outputs | `scripts/validate_all.sh`, `tests/test_journal_and_worked_examples.py`, `reports/demo_summary.json` | **PASS** — `17 / 17` persisted runs matched stored `output_hash` on `replay_run`; two consecutive clean runs of `scripts/run_demo.py` produced identical SHA-256 of `reports/demo_summary.json`: `76d2d09a2a2d9438684735394cbdbabcd30f979234e5c21a3307a6b5d7603ff1`. |
| **Operational review** | Missing config, unsupported inputs, restart/rerun | `tests/test_validation_and_contracts.py`, `tests/test_flows_corrections_integration.py`, `docs/runbook.md` | **PASS** — Missing config raises `CONFIG_NOT_FOUND`, 8/8 invalid fixture cases raise expected error codes, binary floats raise `FLOAT_NOT_ALLOWED`, and DB close/reopen replay succeeded for v1 and v2 runs. |

## 3. Milestone Completion Summary (M1–M4)

- **M1 — Foundation & Reference Fixtures**: Completed (`pyproject.toml`, `requirements-lock.txt`, `config/default_engine_config.json`, `models.py`, `calendar.py`, `validation.py`, `reference_basic_2_and_20.json`, `invalid_cases.json`, `docs/contracts.md`, `docs/architecture.md`, `docs/adr/0001-exact-decimal-and-immutable-journal.md`).
- **M2 — Core Domain Implementation**: Completed (`domain.py` pure I/O-free engine, `errors.py`, `loss_and_recovery_hwm.json`, `test_domain_hwm_loss_recovery.py`).
- **M3 — Integration & Evaluation**: Completed (`service.py`, `storage.py`, `adapters.py`, `reporting.py`, `mid_period_flows_and_correction.json`, `test_flows_corrections_integration.py`, raw outputs in `reports/`).
- **M4 — Hardening, Benchmarking & Delivery**: Completed (`journal.py`, `independent_worked_examples.json`, `hurdle_and_crystallization_boundaries.json`, `cli.py`, `scripts/run_demo.py`, `scripts/validate_all.sh`, `.github/workflows/ci.yml`, `docs/runbook.md`, `docs/integration.md`, `README.md`, `reports/acceptance.md`).

## 4. Untested / Explicitly Out-of-Scope Capabilities

- **Not run in this local environment**: GitHub Actions CI matrix across Python `3.11` and `3.12` (verified locally on Python `3.9.6`; workflow provided in `.github/workflows/ci.yml`).
- **Optional `pytest` runner**: Not installed in this offline environment; test suite is verified via standard-library `unittest` and written to be `pytest`-compatible.
- **Out of scope by design**: Equalization credits / depreciation deposits, tiered incentive waterfalls, clawbacks, multi-currency FX translation, live fund administration APIs, and multi-writer production database concurrency.

## 5. Raw Artifacts Generated

- `reports/demo_summary.json` — Aggregated scenario, worked-example, and 17-run replay verification summary
- `reports/runtime_metadata.json` — Runtime environment and pinned engine config metadata
- `reports/scenario_reference_basic.json` — Reference 2-and-20 calculation & journal output
- `reports/scenario_loss_recovery.json` — 4-quarter HWM loss and recovery raw output
- `reports/scenario_flows_corrections.json` — Mid-period flows & versioned correction raw output
- `reports/scenario_hurdle_boundaries.json` — Annual crystallization & hard hurdle raw output
- `reports/worked_examples_comparison.json` — 5 independent worked examples oracle comparison (`0.00` tolerance)
- `reports/worked_examples_journals.csv` — Flat double-entry journal export for worked examples
- `reports/demo_ledger.sqlite` — Immutable SQLite ledger persisted by `scripts/run_demo.py`
