# Operator & Developer Runbook

## 1. Local Setup

Requires **Python 3.9+** (tested on Python 3.9.6). Core runtime has zero third-party dependencies.

```bash
cd prime-broker-collateral-management
```

## 2. Standard Commands

### Run Full Test Suite (Unit, Integration, Boundary, Immutability)
```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

### Run End-to-End Multi-Broker Demo
```bash
PYTHONPATH=src python3 scripts/run_demo.py --output reports/demo_run_output.json
```

### Run CLI with Persistent SQLite Journal & Replay
```bash
PYTHONPATH=src python3 -m pb_collateral.cli run \
  --policy config/default_policy.json \
  --financing config/financing_rates.json \
  --fixture data/fixtures/demo_multi_broker_bundle.json \
  --journal-db /tmp/pb_collateral_journal.db \
  --output /tmp/cli_demo.json

# Replay a stored run by its deterministic run_id
PYTHONPATH=src python3 -m pb_collateral.cli replay \
  --journal-db /tmp/pb_collateral_journal.db \
  --run-id <RUN_ID_FROM_OUTPUT>
```

### Generate All Machine-Readable Reports & Micro-Benchmark
```bash
PYTHONPATH=src python3 scripts/generate_reports.py
```

### Verify Logical Reproducibility
```bash
PYTHONPATH=src python3 scripts/verify_reproducibility.py
```

## 3. Diagnostics & Structured Counters

Each service run records `DiagnosticCounters` in the JSON report and emits structured JSON events:

- `duplicate_statements_skipped`: Identical statement payload re-submitted (safe idempotent skip).
- `corrections_versioned`: Superseding statement version accepted and appended.
- `breaks_detected`: Non-zero indicates broker vs. internal or policy valuation discrepancies requiring review.
- `shortfalls_flagged` / `margin_calls_issued`: Pre-settlement margin deficits requiring funding action.
- `failed_transfers_quarantined`: Unsettled/failed inbound lots excluded from posted collateral.
- `substitutions_blocked`: Substitution requests blocked prior to settlement due to projected shortfall.

## 4. Failure Modes & Recovery Procedures

| Symptom / Error | Cause | Recovery Action |
|---|---|---|
| `DUPLICATE_STATEMENT` | Same `statement_id` arrived with altered payload, or `version` did not increment | Do **not** mutate journal rows. Assign a new `statement_id`, increment `version`, set `supersedes_statement_id`, and re-submit. |
| `COLLATERAL_DOUBLE_COUNTING` | Same `lot_id` reported active at two prime brokers or pledged twice | Quarantine the conflicting lot, verify settlement status at CSD/custodian, update `transfers[]` (`FAILED`/`PENDING`), and rerun. |
| `MISSING_CONFIGURATION` | Broker policy, FX pair, or financing rate schedule missing | Add the versioned entry to `config/default_policy.json`, `config/financing_rates.json`, or `fx_rates[]`, and rerun. Never silently fall back to `1.0` FX or `0%` haircut. |
| `DAY_COUNT_BOUNDARY_ERROR` | `end_date < start_date` or invalid zero-day accrual window | Correct accrual period boundaries and re-run financing reconciliation. |
| `PERSISTENCE_ERROR` on `PostgresJournalAdapter` | `psycopg` not installed or Postgres unreachable | Install `psycopg[binary]==3.2.3` and verify DSN, or use `SQLiteJournalStore` for offline execution. |
| Process Restart Mid-Batch | Process killed after partial broker batch | Re-run the exact same batch against the persistent SQLite journal. Already-recorded statements with identical `content_hash` are skipped idempotently; remaining statements complete cleanly. |

## 5. Known Limitations

- Illustrative haircuts, spreads, and MTA terms are synthetic educational parameters, not legal or prime brokerage margin advice.
- Concentration caps use a deterministic single-pass proportional trim against pre-cap posted collateral rather than an iterative mixed-integer collateral optimizer.
- Live PostgreSQL execution was not run in the offline sandbox environment (DDL and error paths are verified; SQLite journal provides full local verification).
