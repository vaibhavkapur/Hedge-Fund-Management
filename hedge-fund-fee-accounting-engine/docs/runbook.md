# Operator and Developer Runbook

## 1. Environment Requirements

- **Python**: `>= 3.9` (verified on Python `3.9.6`)
- **Runtime Dependencies**: None (Python standard library only: `decimal`, `sqlite3`, `dataclasses`, `unittest`, `json`, `csv`, `hashlib`)
- **Network Access**: Not required (100% offline execution)

## 2. Setup and Verification Commands

Run all commands from `hedge-fund-fee-accounting-engine/`:

```bash
# 1. Run full unit, integration, boundary, and worked-example test suite
python3 -m unittest discover -s tests -v

# 2. Run reproducible end-to-end demo (writes raw outputs to reports/)
python3 scripts/run_demo.py

# 3. Run full validation suite (tests + demo + determinism check)
bash scripts/validate_all.sh
```

## 3. CLI Usage Examples

```bash
# Run a single scenario fixture against an ephemeral SQLite ledger
PYTHONPATH=src python3 -m hedge_fund_fee_engine \
  --db /tmp/demo_fee_ledger.sqlite \
  run-fixture data/fixtures/reference_basic_2_and_20.json \
  --out /tmp/ref_output.json

# List persisted calculation runs
PYTHONPATH=src python3 -m hedge_fund_fee_engine \
  --db /tmp/demo_fee_ledger.sqlite \
  list-runs

# Deterministically replay a persisted run and verify its output hash
PYTHONPATH=src python3 -m hedge_fund_fee_engine \
  --db /tmp/demo_fee_ledger.sqlite \
  replay <RUN_ID>
```

## 4. Diagnostics and Audit Trail

- **Structured Service Logs**: `FeeAccountingService` emits structured JSON log entries under logger `hedge_fund_fee_engine.service` (`agreement_registered`, `period_run_completed`, `fee_correction_applied`, `run_replayed`, `run_approved`, `run_rejected`).
- **Diagnostic Counters**: Every `CalculationRunResult.diagnostics["counters"]` tracks `lots_evaluated`, `subscriptions_processed`, `redemptions_processed`, `crystallization_events`, and `reversals_processed`.
- **SQLite Ledger Inspection**:
  ```bash
  sqlite3 reports/demo_ledger.sqlite \
    "SELECT run_id, valuation_date, version, status, output_hash FROM calculation_runs;"
  ```

## 5. Failure Handling and Recovery Playbook

| Failure / Incident | Diagnostic Signal | Recovery Procedure |
|---|---|---|
| Missing or corrupt config JSON | `[CONFIG_NOT_FOUND]` or `[INVALID_CONFIG_JSON]` | Verify `--config` path points to `config/default_engine_config.json`; validate JSON syntax before retrying. |
| Duplicate accrual attempt | `[DUPLICATE_RUN]` on `(agreement_id, valuation_date, version)` | Never delete rows from SQLite. Inspect existing run via `list-runs` and `replay <run_id>`. If inputs were erroneous, issue a `FeeCorrection` with `version = prior_version + 1`. |
| Skipped crystallization boundary | `[SKIPPED_CRYSTALLIZATION_BOUNDARY]` | Split the multi-period input into individual valuation snapshots aligned with each scheduled crystallization boundary. |
| Restated NAV after posting | Audit/NAV restatement received | Call `FeeAccountingService.apply_correction(...)` with a `FeeCorrection(version=prior+1)`. The engine automatically posts reversing journal entries linked via `supersedes_entry_id`, computes corrected entries, marks the prior run `SUPERSEDED`, and preserves full audit lineage. |
| Process restart mid-workflow | Reopen persisted SQLite DB | Re-instantiate `FeeAccountingService.from_config_path(cfg, db_path)` and run `replay_run(run_id)` on the latest persisted run to confirm hash integrity before continuing to the next valuation period. |
