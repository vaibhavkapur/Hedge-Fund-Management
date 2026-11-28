# Acceptance & Verification Report — Regulatory Reporting Engine

**Recorded UTC:** `2026-11-28T05:03:05Z`  
**Environment:** Python `3.9.6`, SQLite `3.51.0`, macOS arm64 (`offline_synthetic`, `random_seed=20260331`)  
**Raw Machine-Readable Evidence:** [run_metadata.json](run_metadata.json), [oracle_comparison.json](oracle_comparison.json), [benchmark_results.json](benchmark_results.json), [demo_outputs/demo_summary.json](demo_outputs/demo_summary.json)

> **Evidence Distinction:** All numerical outputs below were generated from **synthetic reference fixtures** (`data/fixtures/reference_snapshot_q1_2026.json`, `reference_snapshot_q1_2026_corrected.json`, `boundary_zero_nav_empty_positions.json`). Latency and throughput numbers are **measured observations** on the local environment for the synthetic fixture workload, not production SLA targets.

---

## 1. Project-Specific Acceptance Criteria (`plan.md` Section 5)

| # | Acceptance Criterion | Status | Measured Evidence |
|---|---|---|---|
| 1 | Every output field traces to source or declared derivation | **PASS** | `test_reference_oracle_exact_match_all_profiles` verified `16/16` (`US_SEC_PF_ILLUSTRATIVE:1.0.0`), `18/18` (`US_SEC_PF_ILLUSTRATIVE:2.0.0`), and `13/13` (`EU_AIFMD_ANNEX4_ILLUSTRATIVE:1.0.0`) output fields carry complete `FieldLineage` records (`lineage_coverage_ratio = 1.000000` in `demo_outputs/*_lineage.json`). |
| 2 | Schema validation passes for illustrative profile | **PASS** | `test_generated_xml_and_csv_pass_schema_validation_for_all_profiles` passed round-trip XML and CSV validation for all 3 profiles; `test_corrupted_xml_fails_schema_validation` and `test_corrupted_csv_fails_schema_validation` verified rejection of malformed/tampered outputs. |
| 3 | No automated submission | **PASS** | `test_automated_submission_structurally_prohibited` and `test_missing_or_malformed_configuration_fails_loudly` verified `REG-9001` blocks both `automated_submission_enabled=true` in config and calls to `engine.submit_to_regulator()`. All XML/CSV outputs carry `certifiedForFiling="false"` / `ILLUSTRATIVE_DRAFT_NOT_FOR_REGULATORY_FILING`. |
| 4 | All required test scenarios pass (or unavailable dependencies explicitly marked not run) | **PASS** | `31/31` unit, integration, boundary, and operational tests passed in `0.053 s` (`0` failures, `0` errors). Unavailable live PostgreSQL dependency explicitly marked **NOT RUN** in Section 4 below and in `run_metadata.json`. |
| 5 | README documents setup, demo, validation, assumptions, and known limitations | **PASS** | Verified in [README.md](../README.md), [docs/runbook.md](../docs/runbook.md), and [docs/architecture.md](../docs/architecture.md). |
| 6 | No real transactions, customer data, credentials or proprietary employer/SAAT artifacts | **PASS** | All identifiers use synthetic prefixes (`000000SYNTH...`, `US0000000010`); zero credentials, zero external network calls, zero employer/SAAT code. |

---

## 2. Test & Validation Matrix (`plan.md` Section 4)

| Test Area | Required Scenario | Test Cases | Measured Result |
|---|---|---|---|
| **Domain correctness** | Missing required data | `test_missing_required_data_fixture_rejected`, `test_missing_required_v2_source_field_raises_missing_data` | **PASS** (`REG-1002`) |
| **Domain correctness** | Invalid identifier | `test_valid_and_invalid_lei_and_isin`, `test_invalid_identifier_fixture_rejected`, `test_invalid_identifier_blocks_report_generation` | **PASS** (`REG-1003`) |
| **Domain correctness** | Changed schema | `test_changed_schema_detected_explicitly`, `test_schema_diff_identifies_breaking_v1_to_v2_changes` | **PASS** (`REG-2001`) |
| **Domain correctness** | Hand-calculated reference oracle | `test_reference_oracle_exact_match_all_profiles` & `oracle_comparison.json` | **PASS** (`0.00` deviation across 47 total fields) |
| **Integration & resilience** | Cross-report totals mismatch | `test_cross_report_totals_mismatch_raises_error`, `test_internal_asset_class_additivity_mismatch_detected`, `test_cross_report_reconciliation_passes_for_consistent_snapshot` | **PASS** (`REG-3001` on mismatch; 6/6 checks pass on consistent snapshot) |
| **Boundary & failure behavior** | Duplicate filing / version correction | `test_duplicate_filing_blocked_and_version_correction_succeeds`, `test_illegal_approval_transitions_blocked`, `test_database_level_immutability_enforced` | **PASS** (`REG-4001`, `REG-4002`, `REG-4003`; v1->v2->v3 chain + valid SHA-256 audit chains) |
| **Boundary & failure behavior** | Zero NAV / empty positions | `test_boundary_zero_nav_and_empty_positions` | **PASS** (safe `0.000000` leverage ratio) |
| **Reproducibility** | Same versioned inputs/config | `test_deterministic_reproducibility_and_replay`, `demo_summary.json` | **PASS** (byte-identical report IDs, SHA-256 hashes, XML, CSV) |
| **Operational review** | Missing config, unsupported inputs, restart/rerun | `test_missing_or_malformed_configuration_fails_loudly`, `test_unsupported_profile_and_unavailable_postgres_backend`, `test_restart_and_rerun_persistence_with_sqlite_file`, `test_cli_list_validate_generate_and_replay`, `test_independent_nav_engine_adapter` | **PASS** (`REG-1001`, `REG-1005`, `REG-1006`, SQLite restart replay verified) |

---

## 3. Measured Micro-Benchmark (Synthetic Workload)

Workload: `dual_profile_mapping_xml_csv_validation_and_reconciliation` (generates both `US_SEC_PF_ILLUSTRATIVE:1.0.0` and `EU_AIFMD_ANNEX4_ILLUSTRATIVE:1.0.0`, validates XML & CSV for each, and executes 6 cross-report reconciliation checks) over `200` iterations:

| Metric | Measured Value |
|---|---|
| Mean latency | `0.3820 ms` |
| p50 latency | `0.3775 ms` |
| p95 latency | `0.4126 ms` |
| p99 latency | `0.4535 ms` |
| Throughput | `2,618.04 bundles/sec` |

---

## 4. Explicitly Marked Untested / Not-Run Capabilities

- **Live PostgreSQL Persistence (`sql/schema_postgres.sql` + `psycopg`):** **NOT RUN** in this offline environment because no PostgreSQL server or `psycopg` package is installed. Verified error handling (`PostgresJournalStoreUnavailableError`, `REG-1006`) and SQLite trigger/schema parity instead.
- **External W3C XSD 1.1 Validation (`xmlschema` / `lxml`):** **NOT RUN** because optional `xml` extras are not installed offline; stdlib structural/type/scale/sequence XML & CSV validators were tested and passed.
- **Official Regulator Filing Portals & Certified Forms:** Explicitly **OUT OF SCOPE** and structurally blocked (`REG-9001`).
