# Hedge Fund Fee Accounting Engine

An independently developed educational and research prototype for deterministic hedge fund management and performance fee accounting under explicit share-class terms, built with Python's standard-library `Decimal` arithmetic and immutable `SQLite` double-entry journaling.

> **Disclaimer / Scope:** Fee rules vary substantially by fund offering memorandum, side letters, and jurisdiction. This prototype is for educational and research purposes only and does **not** constitute legal, tax, or accounting advice or a universal calculation convention. Live capital, real investor records, and production credentials are excluded.

## Features

- **Share-class fee agreements & crystallization calendars**: Explicit fee rates, management fee bases (`BEGINNING_NAV`, `ENDING_PRE_FEE_NAV`, `AVERAGE_NAV`), day-count conventions (`ACT_365`, `ACT_360`, `THIRTY_360`, `MONTHLY_1_12`, `QUARTERLY_1_4`), and crystallization schedules (`MONTHLY`, `QUARTERLY`, `ANNUAL`, `CUSTOM_DATES`).
- **Exact `Decimal` arithmetic**: Rejects binary `float` inputs, computes at 28-digit intermediate precision, and quantizes monetary outputs to currency minor units (`USD`, `EUR`, `GBP`, `CHF` = 2 decimals; `JPY` = 0 decimals) with `ROUND_HALF_EVEN` (configurable).
- **High-water marks & hurdles**: Tracks per-lot HWM, loss carryforward, `HARD_NON_COMPOUNDING` hurdles, `HARD_COMPOUNDING` hurdles, and `SOFT_CATCH_UP` hurdles with explicit catch-up rates.
- **Mid-period capital flows & versioned corrections**: Handles mid-period subscriptions (new series/lots with pro-rata accrual & own HWM), mid-period redemptions (crystallization on redeemed units + proportional loss-carryforward adjustment), and NAV restatements recorded as immutable version `v+1` runs with reversing and corrected journal entries.
- **Immutable SQLite double-entry ledger**: Enforces debit/credit balance, fee-to-journal reconciliation, NAV bridge reconciliation, SQLite `BEFORE UPDATE`/`BEFORE DELETE` immutability triggers, approval workflow (`DRAFT` -> `APPROVED`/`REJECTED`/`SUPERSEDED`), and deterministic hash-verified replay.

## Quickstart & Setup

No external runtime packages are required (Python `>= 3.9` standard library only). From `hedge-fund-fee-accounting-engine/`:

```bash
# Run the complete unit, integration, boundary, and oracle test suite
python3 -m unittest discover -s tests -v

# Run the reproducible end-to-end demo and regenerate reports/
python3 scripts/run_demo.py

# Run all validation checks including clean-rerun determinism verification
bash scripts/validate_all.sh
```

### Example CLI Walkthrough

```bash
# Evaluate the reference 2-and-20 fixture against a local SQLite database
PYTHONPATH=src python3 -m hedge_fund_fee_engine \
  --db /tmp/fee_demo.sqlite \
  run-fixture data/fixtures/reference_basic_2_and_20.json \
  --out /tmp/ref_report.json
```

Example output:

```json
{
  "scenario_id": "REF-BASIC-2-AND-20",
  "all_passed": true,
  "run_count": 1,
  "comparisons_checked": 9,
  "discrepancies": 0
}
```

## Project Layout

```text
hedge-fund-fee-accounting-engine/
├── config/default_engine_config.json    # Pinned rounding, currency minor units, CoA
├── data/fixtures/                       # Hand-calculated synthetic scenarios & worked examples
├── docs/
│   ├── architecture.md                  # Component diagram, invariants, trade-offs
│   ├── contracts.md                     # Input/output schemas, units, error taxonomy
│   ├── integration.md                   # Sibling Trading-Management adapter boundaries
│   ├── runbook.md                       # Demo, diagnostics, failure recovery playbook
│   └── adr/0001-exact-decimal-and-immutable-journal.md
├── reports/
│   ├── acceptance.md                    # Acceptance criteria mapped to measured evidence
│   └── *.json, *.csv                    # Machine-readable outputs & runtime metadata
├── scripts/
│   ├── run_demo.py                      # End-to-end reproducible demo runner
│   └── validate_all.sh                  # Full validation & determinism check script
├── src/hedge_fund_fee_engine/           # Pure domain core, journal, SQLite store, CLI
├── tests/                               # Unit, integration, boundary & oracle tests
├── pyproject.toml                       # PEP 621 build manifest
└── requirements-lock.txt                # Pinned stdlib-only runtime specification
```

## Documentation and Acceptance Evidence

- **Acceptance Evidence**: [reports/acceptance.md](reports/acceptance.md) (maps every acceptance criterion and test matrix requirement to measured test/demo runs)
- **Architecture & Invariants**: [docs/architecture.md](docs/architecture.md)
- **Data Contracts & Errors**: [docs/contracts.md](docs/contracts.md)
- **Integration Boundaries**: [docs/integration.md](docs/integration.md)
- **Operator Runbook**: [docs/runbook.md](docs/runbook.md)
- **ADR 0001**: [docs/adr/0001-exact-decimal-and-immutable-journal.md](docs/adr/0001-exact-decimal-and-immutable-journal.md)

## Assumptions, Provenance, and Limitations

- **Fixture provenance**: All fixtures in `data/fixtures/` are 100% synthetic educational scenarios derived by hand from first-principles formulas; no real transactions, customer records, credentials, or proprietary/employer artifacts are present.
- **Dependency licenses**: Zero third-party runtime dependencies (Python Software Foundation License standard library only). Optional test runner `pytest` (MIT) is supported but not required; standard-library `unittest` runs the full suite out of the box.
- **Explicitly unsupported / out-of-scope features**:
  - Equalization credit / depreciation deposit share-class equalization methods (this prototype uses investor lot / series accounting).
  - Multi-tier hurdle waterfalls, clawbacks, lock-up gates, or side-pocket illiquid allocations.
  - Multi-currency FX hedging or cross-currency share-class conversions.
  - High-concurrency multi-writer production database deployment (SQLite is used for single-node offline reproducibility).
