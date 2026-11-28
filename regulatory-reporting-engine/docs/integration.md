# Integration Boundaries — Regulatory Reporting Engine

## 1. Sibling Repositories in `Trading-Management`

Sibling project directories at the `Trading-Management` repository root:

- `integrated-hedge-fund-management-platform/` — Double-entry ledger, daily close, reconciliation, and control plane.
- `fund-nav-calculation-engine/` — Daily fund NAV calculation, ledger reconciliation, and immutable snapshotting.
- `hedge-fund-fee-accounting-engine/` — Management and performance fee accounting with immutable double-entry journaling.
- `prime-broker-collateral-management/` — Prime broker collateral reconciliation, financing accruals, and substitution gating.
- `investor-subscription-and-redemption-platform/` — Investor onboarding, capital flows, cash reconciliation, and share register.
- `fund-performance-and-investor-reporting-dashboard/` — Performance attribution, risk metrics, and versioned investor reporting.

### Adapter Pattern (No Cross-Package Code Imports)

To keep `regulatory-reporting-engine` independently runnable and decoupled from sibling build states, integration uses JSON data-contract adapters rather than importing sibling Python modules directly:

- **`adapt_fund_nav_engine_snapshot()` in `src/regulatory_reporting_engine/adapters.py`**
  - Consumes governed NAV snapshot JSON produced by `fund-nav-calculation-engine` (demonstrated with `data/fixtures/nav_engine_sample_output.json`).
  - Maps `gross_assets`, `total_liabilities`, `net_asset_value`, `cash_balance`, and `holdings[]` into canonical `GovernedSnapshot` records, deriving `LONG`/`SHORT` directions and counterparty/asset-class exposure buckets deterministically.
  - Enriches the NAV payload with regulatory-specific entity metadata (`fund_lei`, `manager_lei`, `domicile_country`, `period_id`, `period_start`, `liquidity_bucket_primary`) that belongs to the regulatory governance layer rather than the NAV calculation engine.
  - Verified by `TestOperationalAndAdapters.test_independent_nav_engine_adapter`.

### Future Sibling Integration Points (Out of Scope for Prototype)

- **Prime Broker Collateral (`prime-broker-collateral-management`):** Could supply counterparty collateral/rehypothecation schedules via a future adapter extending `ExposureRecord`.
- **Investor Capital Flows (`investor-subscription-and-redemption-platform`):** Could supply investor concentration / redemption-gate schedules for Form PF Section 1b-style profiles.

## 2. Optional External Stack Adapters

The core engine runs offline with zero external dependencies, while exposing clean extension seams for production-grade deployments (subject to separate legal/security review):

| Capability | Offline Prototype Default | Optional External Adapter | Status in Offline Env |
|---|---|---|---|
| Data contract models | Frozen dataclasses (`models.py`) | `pydantic>=2.6` (optional extra `pydantic`) | Stdlib verified; Pydantic not installed |
| Immutable journal store | `SqliteJournalStore` (`store.py`) | PostgreSQL via `sql/schema_postgres.sql` + `psycopg` (optional extra `postgres`) | SQLite verified; live Postgres **not run** |
| XML/CSV schema validation | Built-in `ProfileSchema` validator (`schemas.py`) | W3C XSD validation via `xmlschema`/`lxml` (optional extra `xml`) | Built-in validator verified; `xmlschema`/`lxml` not installed |

## 3. Clean-Room & Compliance Boundary

- Zero imports from private employer or SAAT repositories.
- Zero live broker, custodian, or regulatory portal (SEC EDGAR / ESMA / FCA) network integrations.
- Automated filing transmission is structurally disabled (`REG-9001`).
