# Operator & Developer Runbook

## 1. Environment & Setup

- **Minimum offline runtime:** Python `>= 3.9` (standard library only, including `sqlite3`, `decimal`, `unittest`).
- **Optional web/database extras:**
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.txt
  ```

## 2. Running the Reproducible Demo

From `investor-subscription-and-redemption-platform/`:

```bash
python3 scripts/run_demo.py
```

Expected behavior:
- Loads `config/default_config.json` and `data/fixtures/valid_scenario.json`.
- Verifies baseline reconciliation matches `expected_summary` (`963.259281` shares, `150000.00` USD inbound, `26532.67` USD outbound, `1633.33` USD fees).
- Runs a maker-checker NAV restatement (`125.000000 -> 123.750000` on `2026-03-31`) posting an immutable `+8.000000` share adjustment (`971.259281` total shares).
- Writes versioned JSON output to `reports/demo_baseline_output.json`.

## 3. Running Full Validation & Tests

```bash
python3 scripts/run_validation.py
```

Or run `unittest` directly:
```bash
PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
```

If `pytest` is installed:
```bash
pytest -v
```

## 4. Running the Local HTTP Service

Zero-dependency stdlib server:
```bash
PYTHONPATH=src python3 -c "
from isrp.config import load_config
from isrp.repository import SQLiteRepository
from isrp.service import PlatformService
from isrp.api.http_server import create_http_server
svc = PlatformService(load_config('config/default_config.json'), SQLiteRepository('isrp_local.sqlite3'))
srv = create_http_server(svc, host='127.0.0.1', port=8080)
print('Listening on http://127.0.0.1:8080')
srv.serve_forever()
"
```

With optional FastAPI + Uvicorn installed, pass `create_fastapi_app(service)` to `uvicorn.run(...)`.

## 5. Diagnostics & Failure Recovery

| Failure / Diagnostic Signal | Root Cause | Recovery Procedure |
|---|---|---|
| `CONFIGURATION_ERROR` on startup | Missing or invalid `config/default_config.json` (e.g., float literal, unsupported rounding mode, missing fund/share class) | Validate JSON against `docs/contracts.md`, ensure all decimals are quoted strings, re-run startup check |
| `DUPLICATE_REQUEST` (409) | Re-submitted `idempotency_key` or `request_id` | Query existing request by `existing_request_id` in error details; do not force-insert duplicate orders |
| `CUTOFF_VIOLATION` (422) | Instruction submitted outside `[open_at, cutoff_at]` | Review created `ExceptionCase(CUTOFF_VIOLATION)`; re-submit instruction for the next open dealing window |
| `DELAYED_CASH` / `CASH_MISMATCH` exception | Wire arrived after `cash_due_date` or amount/currency differed | Inspect `cash_confirmations` and `exceptions`; for approved late wire, have an independent reviewer (`reviewer_id != created_by`) call `resolve_exception(..., RESOLVED)` before pricing/settlement; for amount mismatch, reconcile with treasury and reject/re-instruct |
| `REVIEWER_SEPARATION_VIOLATION` (403) | Same actor attempted maker and checker roles | Assign an independent checker/reviewer with distinct actor identity |
| `audit_chain_valid == false` | Out-of-band database row tampering or sequence gap in `audit_events` | Quarantine database file immediately, inspect `audit_chain_errors`, and restore from verified backup / replay from versioned source inputs |
| Process restart with SQLite file | Service restarted mid-lifecycle | Re-open the same `.sqlite3` file with `SQLiteRepository(path)`; run `verify_reconciliation()` and `verify_audit_trail()` to verify clean state before resuming |
