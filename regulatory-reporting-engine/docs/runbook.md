# Operator & Developer Runbook — Regulatory Reporting Engine

## 1. Local Setup (Clean Checkout)

Requires **Python 3.9+** (`python3 --version`). No `pip install` or internet connection is required.

```bash
cd regulatory-reporting-engine
```

Optional editable install (if using a virtual environment with `setuptools`):

```bash
pip install -e ".[dev]"
```

## 2. Standard Validation Commands

### Full Unit & Integration Test Suite

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

### End-to-End Reproducible Demo

```bash
python3 scripts/run_demo.py
```

Outputs generated in `reports/demo_outputs/`:
- `us_pf_v1_draft.xml`, `us_pf_v1_draft.csv`, `us_pf_v1_lineage.json`
- `us_pf_v3_evolved_v2schema.xml`, `us_pf_v3_evolved_v2schema.csv`, `us_pf_v3_lineage.json`
- `eu_aifmd_v1_draft.xml`, `eu_aifmd_v1_draft.csv`, `eu_aifmd_v1_lineage.json`
- `demo_summary.json` and `demo_journal.db`

### Acceptance Suite, Oracle Check & Benchmark

```bash
python3 scripts/run_acceptance.py
```

Refreshes `reports/run_metadata.json`, `reports/oracle_comparison.json`, and `reports/benchmark_results.json`.

## 3. CLI Operations Walkthrough

```bash
# List available jurisdiction profiles and schema versions
PYTHONPATH=src python3 -m regulatory_reporting_engine.cli list-profiles

# Validate a governed snapshot before report generation
PYTHONPATH=src python3 -m regulatory_reporting_engine.cli validate-snapshot \
    data/fixtures/reference_snapshot_q1_2026.json

# Generate XML, CSV, and lineage bundle for a profile
PYTHONPATH=src python3 -m regulatory_reporting_engine.cli generate \
    data/fixtures/reference_snapshot_q1_2026.json \
    --profile US_SEC_PF_ILLUSTRATIVE \
    --schema-version 1.0.0 \
    --actor compliance_analyst \
    --out-dir reports/cli_demo

# Advance approval status (DRAFT -> REVIEWED -> APPROVED)
PYTHONPATH=src python3 -m regulatory_reporting_engine.cli transition \
    RPT-SYNTH_ALPHA_01-2026-Q1-US_SEC_PF_ILLUSTRATIVE-V1-E28146B3FA3E \
    --to-status REVIEWED \
    --actor senior_reviewer \
    --rationale "Lineage and cross-report totals verified"

# Deterministically replay a stored report from its immutable snapshot
PYTHONPATH=src python3 -m regulatory_reporting_engine.cli replay \
    RPT-SYNTH_ALPHA_01-2026-Q1-US_SEC_PF_ILLUSTRATIVE-V1-E28146B3FA3E

# Inspect breaking changes between schema versions
PYTHONPATH=src python3 -m regulatory_reporting_engine.cli schema-diff \
    --profile US_SEC_PF_ILLUSTRATIVE --from-version 1.0.0 --to-version 2.0.0
```

## 4. Diagnostics & Structured Counters

`RegulatoryReportingEngine.counters` tracks operational telemetry per session:
- `snapshots_ingested`
- `reports_generated`
- `corrections_created`
- `reconciliations_passed` / `reconciliations_failed`
- `status_transitions`
- `replays_verified`
- `submission_attempts_blocked`

Structured logs are emitted via Python `logging` under logger name `regulatory_reporting_engine`. All CLI failures exit with non-zero status (`1` for domain/config error, `2` for replay hash mismatch) and print a structured JSON error payload containing `error_type`, `code` (`REG-xxxx`), `message`, and `details` to `stderr`.

## 5. Failure Modes & Recovery Procedures

| Error Code | Symptom | Recovery Steps |
|---|---|---|
| `REG-1001` | Missing or malformed config | Verify `--config` path and JSON syntax; pass `--base-dir` if config lives outside `config/`. |
| `REG-1002` | Missing required snapshot/mapped field | Inspect `details.field` / `details.source_path`; obtain corrected upstream governed snapshot. Do **not** synthesize missing figures. |
| `REG-1003` | Invalid LEI / ISIN / ISO code | Inspect `details.standard` and `details.value`; remediate reference data in upstream golden source and ingest a new `snapshot_id`. |
| `REG-2001` | Schema evolution mismatch | Run `cli schema-diff` to inspect added/removed fields; publish a matching versioned mapping file under `config/mappings/` and target the new schema version via `create_correction()`. |
| `REG-3001` | Cross-report totals mismatch | Inspect `details.mismatches`; confirm both reports reference the same `snapshot_id` and that all asset classes are covered by profile bucket mappings. |
| `REG-4001` | Duplicate filing detected | Never delete prior rows. Use `engine.create_correction(prior_report_id, corrected_snapshot, correction_reason=..., actor=...)` to mint version `v(N+1)`. |
| `REG-4003` | Immutability violation | Database triggers prevented mutation of append-only records. Investigate tampering and verify hash chains via `store.verify_audit_chain(report_id)`. |
| `REG-9001` | Automated submission blocked | Expected safety control. Export approved drafts for manual review by qualified compliance personnel. |

## 6. CI Validation

See [`.github/workflows/regulatory-reporting-engine.yml`](../../.github/workflows/regulatory-reporting-engine.yml) — runs the full `unittest` suite, end-to-end demo, oracle comparison, and CLI smoke tests across Python 3.9–3.12 on every push and pull request.
