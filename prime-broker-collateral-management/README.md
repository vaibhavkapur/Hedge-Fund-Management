# Prime Broker Collateral Management

An independently developed educational and research prototype for reconciling prime broker collateral, calculating encumbrance and exact financing accruals, producing margin shortage/call notices, and gating collateral substitutions prior to simulated settlement.

> **Disclaimer:** Prime broker margin terms, haircuts, spreads, and minimum transfer amounts in this repository are synthetic illustrative assumptions — **not** actual margin advice. Live capital, real customer records, and production credentials are strictly excluded.

## Key Capabilities

- **Broker Balance & Policy Normalization (`validation.py`):** Strict `Decimal`-only parsing (raw JSON floats rejected), ISO-4217 currency & ISO-8601 UTC timestamp validation, broker field alias normalization, and canonical SHA-256 content hashing.
- **Collateral Valuation, Encumbrance & Anti-Double-Counting (`valuation.py`, `fx.py`):** Exact FX conversion (direct, inverse, USD triangulation), policy haircut + FX mismatch add-on, deterministic asset-class concentration trims, failed/unsettled transfer quarantine, and global cross-broker lot deduplication.
- **Exact Financing Accruals (`financing.py`):** Debit financing, credit cash interest, and short-proceeds rebate calculations across `ACT_360`, `ACT_365F`, `ACT_ACT_ISDA` (leap-year/year-boundary split), and `THIRTY_360`.
- **Statement Reconciliation & Margin Call Workflow (`reconciliation.py`):** Detects position, cash, eligibility/haircut, financing accrual, and failed transfer breaks; applies MTA and call/recall rounding increments; flags shortfalls before settlement and recommends unencumbered funding lots.
- **Collateral Substitution & Pre-Settlement Gate (`substitution.py`):** Simulates releasing pledged lots and pledging replacement lots (including secondary concentration cap effects) and blocks settlement (`AUTO_FLAGGED_SHORTFALL`) whenever projected posted collateral falls below margin requirements.
- **Append-Only Versioned Journal (`journal.py`):** Trigger-enforced immutable `SQLiteJournalStore` (default, zero external dependencies) with idempotent duplicate detection, versioned corrections, and deterministic `run_id` replay, plus a `PostgresJournalAdapter` and PostgreSQL 14+ DDL (`src/pb_collateral/sql/postgres_schema.sql`).

## Project Layout

```text
prime-broker-collateral-management/
├── config/                     # Pinned policy, haircut, rounding, and financing schedules
├── data/fixtures/              # Hand-calculated reference oracle, multi-broker demo, invalid fixtures
├── docs/                       # Architecture, contracts, integration, runbook, ADR-001
├── reports/                    # Acceptance matrix and versioned machine-readable JSON outputs
├── scripts/                    # Reproducible demo, report generator, and reproducibility verifier
├── src/pb_collateral/          # Pure domain library, journal store, orchestration service, CLI
├── tests/                      # 25 unit, integration, boundary, and immutability tests
├── Makefile
├── pyproject.toml
├── requirements.txt
└── plan.md
```

## Quickstart & Validation

Requires **Python 3.9+** (zero third-party runtime dependencies for core execution).

```bash
cd prime-broker-collateral-management

# 1. Run all 25 unit, integration, boundary, and journal tests
PYTHONPATH=src python3 -m unittest discover -s tests -v

# 2. Run the reproducible multi-broker end-to-end demo
PYTHONPATH=src python3 scripts/run_demo.py

# 3. Verify bit-for-bit reproducibility across repeated runs
PYTHONPATH=src python3 scripts/verify_reproducibility.py

# 4. Regenerate baseline, demo, and micro-benchmark JSON reports
PYTHONPATH=src python3 scripts/generate_reports.py
```

## Example Demo Output (Observed on Synthetic Multi-Broker Fixture)

```text
[PB-ALPHA / ACC-FUND-USD | 2026-03-17] run_id=RUN-PB-ALPHA-2026-03-17-V1-21C15BD07F
  Posted Collateral : 1719500.00 USD
  Margin Requirement: 1650000.00 USD
  Surplus / Deficit : 69500.00 USD
  Margin Action     : EXCESS_RETURN (69000.00 USD) [flagged_pre_settle=False]
  Financing Accrual : -36.60 USD
  Reconcil. Breaks  : 1 (ELIGIBILITY_OR_HAIRCUT_BREAK)

[PB-BETA / ACC-FUND-EUR | 2026-03-17] run_id=RUN-PB-BETA-2026-03-17-V1-970973CB2A
  Posted Collateral : 685000.00 EUR
  Margin Requirement: 800000.00 EUR
  Surplus / Deficit : -115000.00 EUR
  Margin Action     : MARGIN_CALL (115000.00 EUR) [flagged_pre_settle=True]
  Financing Accrual : -22.92 EUR
  Reconcil. Breaks  : 1 (FAILED_TRANSFER_BREAK)

[Substitution SUB-APPROVE-001 @ PB-ALPHA] review_id=SUBREV-PB-ALPHA-2E0EC178B7
  Pre -> Post Posted: 1719500.00 -> 1769000.00 USD
  Settlement Allowed: True | Status: APPROVED
  Required Top-Up   : 0.00 USD

[Substitution SUB-BLOCK-SHORTFALL-002 @ PB-ALPHA] review_id=SUBREV-PB-ALPHA-70905A2F3C
  Pre -> Post Posted: 1719500.00 -> 693700.00 USD
  Settlement Allowed: False | Status: AUTO_FLAGGED_SHORTFALL
  Required Top-Up   : 956300.00 USD
```

## Documentation & Acceptance Evidence

- **Acceptance Evidence Matrix:** [reports/acceptance.md](reports/acceptance.md)
- **Raw Machine-Readable Reports:** [reports/baseline_reference_output.json](reports/baseline_reference_output.json), [reports/demo_run_output.json](reports/demo_run_output.json), [reports/benchmark_and_validation.json](reports/benchmark_and_validation.json)
- **Architecture & Invariants:** [docs/architecture.md](docs/architecture.md)
- **Data Contracts & Error Codes:** [docs/contracts.md](docs/contracts.md)
- **Integration & Persistence Boundaries:** [docs/integration.md](docs/integration.md)
- **Operator / Developer Runbook:** [docs/runbook.md](docs/runbook.md)
- **First ADR:** [docs/adr/ADR-001-domain-ledger-and-precision.md](docs/adr/ADR-001-domain-ledger-and-precision.md)

## Assumptions, Limitations & Provenance

- **Observed vs. Synthetic:** All test pass counts (`25/25`), reconciliation outputs, and micro-benchmark timings (`0.948 ms` mean for 200 synthetic positions) were measured on Python 3.9.6 using synthetic fixtures in `data/fixtures/`. They are not production SLA guarantees.
- **Untested Dependencies:** Live PostgreSQL persistence (`PostgresJournalAdapter`) was **not run** in the offline environment because PostgreSQL and `psycopg` are not installed; `SQLiteJournalStore` and PostgreSQL DDL validation were executed instead.
- **Dependencies & Licenses:** Core runtime uses Python Standard Library only (PSF License). Optional pinned dependencies in `pyproject.toml` are `psycopg[binary]==3.2.3` (LGPL-3.0) and `pytest==8.3.3` (MIT). All source code and fixtures are original to this project.
