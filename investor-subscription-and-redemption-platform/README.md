# Investor Subscription and Redemption Platform

Educational and research prototype modeling reviewed investor capital flows, exact `Decimal` share issuance and redemption, incoming/outgoing cash reconciliation, an immutable share journal/register, maker-checker governance, NAV restatements, and a tamper-evident audit trail.

> **Disclaimer / Scope Boundary:** All investors, identity documents, bank accounts, dealing windows, NAV records, and cash confirmations in this repository are **100% synthetic**. Mock onboarding checks (`src/isrp/onboarding.py`) are deterministic educational rules and do not constitute legal KYC/AML compliance. No live capital, customer PII, production credentials, or proprietary employer/SAAT code is used or included.

## Features

- **Investor & Onboarding State Machine (`src/isrp/onboarding.py`):** Full investor lifecycle (`PROSPECT` -> `ONBOARDING_PENDING` -> `KYC_APPROVED`/`KYC_REJECTED` -> `ACTIVE` -> `SUSPENDED`/`CLOSED`) with deterministic mock KYC/AML/sanctions/eligibility/tax/bank checks.
- **Pure `Decimal` Pricing Engine (`src/isrp/pricing.py`):** Exact fee deduction, share issuance, redemption proceeds, explicit rounding residual bounds, and NAV restatement delta computation verified against an independent hand-calculated oracle (`data/fixtures/reference_oracle.json`).
- **Cash Reconciliation & Share Register (`src/isrp/reconciliation.py`):** Matches inbound/outbound cash confirmations against dealing windows and expected amounts, detects delayed cash and mismatches, locks pending redemption shares to prevent double-redemption, and proves exact share/cash reconciliation.
- **Maker-Checker, Cancellation & Exception Review (`src/isrp/workflow.py`, `src/isrp/service.py`):** Enforces four-eyes separation across request approval, exception review, and NAV restatement; blocks unapproved requests from pricing or settling; prevents cancelling settled entries; and records NAV restatements as new versioned immutable journal adjustments.
- **Tamper-Evident Audit Trail & Deterministic Replay (`src/isrp/audit.py`):** Append-only SHA-256 hash-chained audit events, structured diagnostic counters, and reproducible `logical_state_hash` snapshots.
- **Relational Storage & HTTP APIs (`src/isrp/repository.py`, `src/isrp/api/`):** Zero-dependency `SQLiteRepository` + stdlib JSON HTTP router for offline reproducibility, plus pinned FastAPI (`src/isrp/api/fastapi_app.py`) and PostgreSQL (`PostgresRepository` + `src/isrp/migrations/001_initial_schema.sql`) adapters.

## Quickstart

Requires **Python 3.9+** (standard library only for the core engine, SQLite storage, HTTP router, demo, and test suite).

```bash
cd investor-subscription-and-redemption-platform

# 1. Run the reproducible end-to-end demo (writes reports/demo_baseline_output.json)
python3 scripts/run_demo.py

# 2. Run the full automated validation suite and replay check (writes reports/validation_results.json)
python3 scripts/run_validation.py

# 3. Run unit/integration tests directly
PYTHONPATH=src:tests python3 -m unittest discover -s tests -v
```

Optional FastAPI, PostgreSQL, and `pytest` extras are pinned in `pyproject.toml` and `requirements.txt` (MIT/BSD/Apache-2.0/LGPL-3.0 open-source licenses):

```bash
pip install -r requirements.txt
pytest -v
```

## Example Demo Output

Running `python3 scripts/run_demo.py` against `data/fixtures/valid_scenario.json` (synthetic fixtures):

- **Baseline Reconciliation (`2026-03-31` & `2026-04-30` dealing windows):**
  - `INV-SYN-001`: Subscribed `100000.00` USD (1% fee `1000.00`, net `99000.00` @ NAV `125.000000` -> `792.000000` shares); redeemed `200.000000` shares @ NAV `133.330000` (gross `26666.00`, 0.5% fee `133.33`, net payout `26532.67` USD) -> **`592.000000` settled shares**.
  - `INV-SYN-002`: Subscribed `50000.00` USD (1% fee `500.00`, net `49500.00` @ NAV `133.330000`) -> **`371.259281` settled shares**.
  - **Total Register Shares:** `963.259281` (exact match to journal; discrepancy `0.000000`).
  - **Cash & Fees:** `150000.00` USD inbound, `26532.67` USD outbound, `1633.33` USD fees (exact match to cash confirmations; discrepancy `0.00`).
- **Versioned NAV Restatement (`2026-03-31` NAV restated `125.000000 -> 123.750000`):**
  - Supersedes NAV v1, publishes NAV v2, appends an immutable `+8.000000` share adjustment journal entry (`INV-SYN-001` becomes `600.000000` shares; total register `971.259281` shares), and maintains `reconciled=true` and `audit_chain_valid=true`.

## Repository Layout

- `src/isrp/`: Core domain, pricing, onboarding, workflow, reconciliation, audit, SQLite/PostgreSQL repositories, sibling adapters, and FastAPI/stdlib HTTP APIs.
- `tests/`: Unit, integration, oracle, boundary, and failure regression tests.
- `config/default_config.json`: Versioned rounding rules, supported currencies, fund/share-class fee schedules, and mock sanctions policy.
- `data/fixtures/`: Hand-calculated oracle (`reference_oracle.json`), valid E2E scenario (`valid_scenario.json`), and edge/failure cases (`edge_and_failure_scenarios.json`).
- `scripts/`: `run_demo.py` and `run_validation.py`.
- `docs/`: [architecture.md](docs/architecture.md), [ADR 0001](docs/adr/0001-decimal-immutable-journal-and-storage.md), [contracts.md](docs/contracts.md), [integration.md](docs/integration.md), and [runbook.md](docs/runbook.md).
- `reports/`: [acceptance.md](reports/acceptance.md), `demo_baseline_output.json`, and `validation_results.json`.

## Assumptions, Limitations & Untested Capabilities

- **Synthetic Fixtures Only:** Measured outputs in `reports/` reflect deterministic synthetic scenarios, not live fund operations.
- **Mock Onboarding:** KYC, AML, sanctions, tax, and bank checks are deterministic rule-based placeholders.
- **Offline Runtime vs. Optional Stack:** Core tests, SQLite persistence, and the HTTP router are verified directly on Python 3.9.6. Live PostgreSQL (`PostgresRepository`) and live FastAPI/Uvicorn execution are explicitly marked `NOT_RUN` in [reports/acceptance.md](reports/acceptance.md) when optional packages/servers are absent.
- **Single-Currency per Share Class:** Cross-currency FX conversion between investor wire currency and share-class currency is intentionally rejected (`CURRENCY_MISMATCH`).
- **Dependency Licenses & Fixture Provenance:** Core runtime uses Python PSF-licensed standard library only; optional extras (`fastapi` MIT, `uvicorn` BSD-3-Clause, `pydantic` MIT, `psycopg` LGPL-3.0, `pytest` MIT, `httpx` BSD-3-Clause) are pinned in `requirements.txt`. All fixtures in `data/fixtures/` were authored cleanly from scratch for this prototype.
