# Operator & Developer Runbook

## 1. Environment Setup

Verify Python 3.9+ is available:

```bash
cd fund-nav-calculation-engine
python3 --version
```

No third-party packages are required to run calculations, tests, demos, or benchmarks.

## 2. Standard Operating Commands

### Full Validation (Tests + Demo + Benchmark)

```bash
python3 scripts/run_validation.py
```

Outputs generated in `reports/`:
- `reports/demo_nav_store.sqlite` — SQLite snapshot & audit journal database from the demo run
- `reports/demo_run_output.json` — Full v1/v2 snapshots, restatement diff, oracle checks, and audit trail
- `reports/benchmark_results.json` — Deterministic 500-position / 10-class benchmark timings and hashes
- `reports/validation_summary.json` — Consolidated pass/fail summary and runtime metadata

### Unit & Integration Tests Only

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

### Daily NAV Calculation & Publication Walkthrough

1. **Calculate Draft:**
   ```bash
   PYTHONPATH=src python3 -m fund_nav_engine -v calculate \
     --config config/default_config.json \
     --bundle data/fixtures/reference_scenario.json \
     --db reports/ops_nav.sqlite \
     --actor fund_accountant
   ```
2. **Inspect Diagnostics & Warnings:** Review `reconciliation.reconciled`, `diagnostics`, and `warnings` in the JSON output (e.g., deferred post-cutoff flows or stale-price overrides).
3. **Approve & Freeze:**
   ```bash
   PYTHONPATH=src python3 -m fund_nav_engine freeze \
     --db reports/ops_nav.sqlite \
     --snapshot-id <SNAPSHOT_ID> \
     --approver nav_controller
   ```
4. **Verify Integrity & Replay:**
   ```bash
   PYTHONPATH=src python3 -m fund_nav_engine verify \
     --db reports/ops_nav.sqlite \
     --snapshot-id <SNAPSHOT_ID>
   ```
5. **Publish Restatement (When Late Correction Arrives):**
   ```bash
   PYTHONPATH=src python3 -m fund_nav_engine restate \
     --db reports/ops_nav.sqlite \
     --prior-snapshot-id <FROZEN_V1_SNAPSHOT_ID> \
     --config config/default_config.json \
     --bundle data/fixtures/late_correction_bundle.json \
     --reason "Documented correction ticket / vendor restatement notice" \
     --actor nav_controller \
     --approver head_of_valuation
   ```

## 3. Diagnostics & Recovery Procedures

All CLI domain failures exit with code `2` and print a structured JSON error payload (`error_type`, `code`, `message`, `details`) to `stderr` — there is no silent success.

| Error Code | Symptom | Recovery Action |
|---|---|---|
| `NAV-1001` | Missing or invalid configuration | Verify `--config` path exists, valid JSON, `base_currency` is in `currency_precisions`, and `flow_cutoff_utc` is `HH:MM:SS`. |
| `NAV-1002` | Schema / validation failure | Inspect `details.field`; ensure numeric fields are strings (not floats), IDs are unique, currencies are configured, and share classes are non-empty. |
| `NAV-2001` | Missing valuation price | Obtain closing price for `details.instrument_id` or record an approved committee price override in the input bundle, then rerun. |
| `NAV-2002` | Stale price rejected | Refresh pricing feed; if instrument is genuinely illiquid, submit an override with `is_override: true` and `override_reason`, and run under `stale_price_policy: "require_override"`. |
| `NAV-2003` / `2004` / `2005` | FX missing, currency mismatch, stale FX, or conflicting FX | Verify price currency matches position currency; supply single non-stale FX fixing for `valuation_date` and rerun. |
| `NAV-3001`..`3006` | Flow timing or share-availability error | Verify `trade_date == valuation_date`, `settlement_date >= trade_date`, cutoff timestamp, and investor share balance. For late prior-day flows (`NAV-3004`), publish a restatement for the flow's `trade_date`. |
| `NAV-4001` | Ledger reconciliation break | Inspect `details.breaks` (account type, currency, expected vs ledger difference). Resolve upstream position/accrual/cash booking discrepancy before freezing NAV. |
| `NAV-5001` | Immutability violation | Frozen snapshots and audit journal rows cannot be edited or deleted. To correct a frozen NAV, always use `restate` to publish a new linked version. |
| `NAV-5002` | Restatement precondition failed | Confirm prior snapshot is `frozen`, `reason` is non-empty, and `fund_id`/`valuation_date` match the prior snapshot. |

## 4. Restart / Rerun Safety

- Re-running `calculate` with the exact same `bundle` and `config` against the same SQLite DB is idempotent: identical `snapshot_id` and `output_hash` returns the existing persisted snapshot without duplicating rows.
- Running `verify` reloads the exact stored `input_bundles` payload from SQLite, recalculates from scratch, and confirms cryptographic hash equality and full `prior_snapshot_id` lineage.
