# Fund NAV Calculation Engine

An independently developed educational and research prototype for calculating, reconciling, freezing, and restating daily fund Net Asset Value (NAV) using exact `Decimal` arithmetic and SQLite-enforced immutable snapshots.

> **Disclaimer / Scope Boundary:** This is an educational fund accounting prototype using synthetic offline fixtures only. Official fund valuation, fee methodology, and published NAV require qualified accounting oversight. Live capital, real customer data, production credentials, and proprietary employer/SAAT artifacts are excluded.

## Capabilities

- **Canonical Contracts & Validation:** Typed, immutable data models for positions, prices, FX rates, liabilities, accruals, share classes, capital flows, and general ledger balances; rejects binary `float` inputs to eliminate floating-point drift.
- **Exact Multi-Currency NAV Engine:** Computes pre-flow and closing gross assets, liabilities, net assets, share-class allocation with deterministic penny reconciliation, and NAV per share using configurable currency minor-unit precisions (`USD`/`EUR`/`GBP` 2dp, `JPY` 0dp, `KWD` 3dp) and `ROUND_HALF_EVEN`.
- **Exception & Boundary Controls:** Handles missing valuations, FX mismatches/stale FX, stale-price policies (`error`, `require_override`, `warn_and_use_last`), capital-flow cutoff timing, zero-share classes (dormant, seeded, or fully redeemed), and general-ledger reconciliation checks.
- **Immutable Snapshots & Auditable Restatements:** Persists snapshots and inputs in SQLite with `BEFORE UPDATE` and `BEFORE DELETE` triggers that physically block mutation or deletion of `frozen` snapshots and `audit_journal` rows. Restatements are created as new versions linked via `prior_snapshot_id` with full version-to-version accounting diffs and cryptographic SHA-256 replay verification.

## Quickstart

### Prerequisites

- Python 3.9+ (uses Python standard library only; zero required third-party runtime dependencies).

### Setup

From the project directory (`fund-nav-calculation-engine`):

```bash
cd fund-nav-calculation-engine
python3 --version
```

Optional editable install (if using a virtual environment):

```bash
python3 -m pip install -e .
```

### Run the Reproducible Demo

```bash
PYTHONPATH=src python3 -m fund_nav_engine demo
```

This runs the reference multi-currency scenario (`v1`), approves and freezes it in SQLite (`reports/demo_nav_store.sqlite`), publishes an auditable late-price-correction restatement (`v2`), replays both snapshots to verify SHA-256 hashes, compares outputs against the independent hand-calculated oracle (`data/fixtures/reference_expected.json`), and writes machine-readable output to `reports/demo_run_output.json`.

### Run Full Validation (Tests + Demo + Synthetic Benchmark)

```bash
python3 scripts/run_validation.py
```

Or run the test suite directly:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

### CLI Commands

```bash
# Calculate a draft NAV and persist to SQLite
PYTHONPATH=src python3 -m fund_nav_engine calculate \
  --config config/default_config.json \
  --bundle data/fixtures/reference_scenario.json \
  --db reports/local_nav.sqlite

# Approve and freeze a draft snapshot
PYTHONPATH=src python3 -m fund_nav_engine freeze \
  --db reports/local_nav.sqlite \
  --snapshot-id SNAP-FUND-ALPHA-01-2026-03-31-v1-8bcf72eb1caa \
  --approver nav_controller

# Publish a restatement linked to the prior frozen snapshot
PYTHONPATH=src python3 -m fund_nav_engine restate \
  --db reports/local_nav.sqlite \
  --prior-snapshot-id SNAP-FUND-ALPHA-01-2026-03-31-v1-8bcf72eb1caa \
  --config config/default_config.json \
  --bundle data/fixtures/late_correction_bundle.json \
  --reason "Vendor corrected EQ-US-ACME closing price"

# Replay stored inputs and verify cryptographic integrity & lineage
PYTHONPATH=src python3 -m fund_nav_engine verify \
  --db reports/local_nav.sqlite \
  --snapshot-id SNAP-FUND-ALPHA-01-2026-03-31-v2-bb0a0dd13ec0
```

## Example Output (Reference Demo)

| Metric | v1 (Frozen) | v2 (Restated) | Delta |
|---|---:|---:|---:|
| Gross Assets (USD) | `2,141,700.00` | `2,161,700.00` | `+20,000.00` |
| Total Liabilities (USD) | `48,325.00` | `48,575.00` | `+250.00` |
| Net Assets (USD) | `2,093,375.00` | `2,113,125.00` | `+19,750.00` |
| `CLS-A-USD` NAV / Share (USD) | `103.400000` | `104.400000` | `+1.000000` |
| `CLS-B-EUR` NAV / Share (EUR) | `103.300000` | `104.300000` | `+1.000000` |
| Ledger Reconciliation | Passed (9/9) | Passed (9/9) | — |
| Post-Cutoff Deferred Flows | 1 (`FLOW-03`) | 1 (`FLOW-03`) | — |

## Project Layout

- `src/fund_nav_engine/` — Core domain models, validation, pure calculator, ledger reconciliation, SQLite immutable store, service layer, adapters, and CLI.
- `config/` — Versioned valuation configuration (`default_config.json`).
- `data/fixtures/` — Deterministic hand-calculated oracle and edge/failure fixtures (synthetic data only).
- `tests/` — Unit, integration, boundary/failure, reproducibility, and operational test suites.
- `scripts/` — End-to-end validation runner (`run_validation.py`).
- `docs/` — Architecture & ADR-001, data contracts, integration boundaries, and operational runbook.
- `reports/` — Acceptance traceability report and versioned machine-readable demo/benchmark outputs.

## Documentation & Evidence

- [Architecture & ADR-001](docs/architecture.md)
- [Data Contracts & Error Catalog](docs/contracts.md)
- [Integration Boundaries](docs/integration.md)
- [Operator & Developer Runbook](docs/runbook.md)
- [Acceptance Criteria & Measured Evidence](reports/acceptance.md)
- [Implementation Plan](plan.md)

## Assumptions, Limitations & Provenance

- **Assumptions:** Forward-priced end-of-day dealing (confirmed pre-cutoff subscriptions receivable increase closing gross assets; redemptions payable increase closing liabilities); pro-rata share-class allocation based on opening net asset weights with deterministic penny allocation; cash positions valued at par `1` when unpriced.
- **Known Limitations:** Educational prototype only; no PostgreSQL driver tested locally (SQLite stdlib backend verified); no tax-lot / equalization / high-water-mark performance-fee crystallization inside the NAV engine (fee amounts are supplied as canonical `AccrualRecord` inputs); no live market data feeds or custody connectivity.
- **Dependencies & Licenses:** Zero required third-party runtime dependencies (Python Software Foundation License stdlib only). Optional dev tools (`pytest`, `mypy`, `ruff`) use MIT licenses and are not required to execute tests or demos.
- **Fixture Provenance:** All fixtures in `data/fixtures/` and benchmark inputs are 100% synthetic, deterministic, and authored specifically for this repository. No real fund, investor, or proprietary data is included.
