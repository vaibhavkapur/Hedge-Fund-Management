# Architecture Decision Records (ADRs)

## ADR-001: Zero-External-Dependency Core with PostgreSQL Contract
- **Status:** Accepted
- **Context:** `plan.md` requires a clean checkout to run setup, tests, and demo reproducibly on local machines and approved paper/sandbox environments, while targeting PostgreSQL for future deployments.
- **Decision:** Implement core domain logic, metrics, HTTP API, SQLite persistence, and the interactive SVG dashboard using only Python 3.9+ standard library and vanilla ES modules. Isolate PostgreSQL behind `FundRepository` (`PostgresRepository` + `config/schema_postgres.sql`) and optional dependency extra `[postgres]`.
- **Consequences:** Zero network/packaging fragility in CI or air-gapped sandboxes; seamless migration to PostgreSQL once an approved database instance is provisioned.

## ADR-002: Clean Adapter Boundaries for Linked Personal Repositories
- **Status:** Accepted
- **Context:** `plan.md` Section 2 lists three personal prototype repositories (`08-quantum-risk-engine`, `02-hybrid-quantum-portfolio-optimizer`, `09-transaction-cost-analysis`) and explicitly states: *"Confirm access, licenses, background IP and SAAT ownership terms before copying or adapting any personal repository... If access or licensing is unresolved, implement against clean, documented contracts instead."*
- **Decision:** Define typed port interfaces (`RiskEngineAdapter`, `PortfolioOptimizerAdapter`, `TransactionCostAdapter`) in `src/fund_reporting/adapters/contracts.py` with local deterministic implementations in `synthetic_adapters.py`. No external repository code is copied.
- **Consequences:** Preserves IP hygiene and enables drop-in replacement once IP/licensing and pinned commit audits are approved.

## ADR-003: Daily Sub-Period TWR + Carino Logarithmic Attribution Linking
- **Status:** Accepted
- **Context:** External investor subscriptions and redemptions distort naive NAV percentage changes, and additive daily strategy contributions do not sum to compounded multi-period returns without a smoothing link.
- **Decision:** Compute daily Time-Weighted Returns using explicit `cash_flow_timing` (`START_OF_DAY` vs `END_OF_DAY`), verify against Modified Dietz, and apply Carino logarithmic linking (`k_t / K`) so multi-period strategy contributions sum identically to cumulative fund gross return (`residual == 0`).
- **Consequences:** Exact mathematical reconciliation across arbitrary cash-flow schedules and holding periods.

## ADR-004: Content-Addressed Immutable Report Versions & Dual Control
- **Status:** Accepted
- **Context:** Investor reporting requires auditable provenance, immutable published artifacts, and controlled revisions when post-close ledger restatements occur.
- **Decision:** Assign every report a content-addressed `version_id` (`<series>-v<rev>-<sha256[:8]>`) and store its canonical SHA-256 digest. Enforce dual-control separation of duties (report author cannot review or approve/publish) and block in-place updates once `PUBLISHED`. Revisions create `rev + 1` linked via `supersedes_version_id` and transition the prior version to `SUPERSEDED` upon publication without mutating its payload or hash.
- **Consequences:** Full non-repudiation and clear audit lineage for regulators and investors.

## ADR-005: Hard Environment Isolation & Mandatory Disclosures
- **Status:** Accepted
- **Context:** Mixing simulated/paper results with live performance or presenting unaudited claims as verified track record is a critical compliance failure (`plan.md` non-goals).
- **Decision:** Enforce `validate_environment_homogeneity` at ingestion, persistence, and analytics boundaries (`MixedEnvironmentError`). Automatically attach `DisclosureBlock` banners and `SIMULATED_DATA`/`PAPER_DATA`/`UNAUDITED_DATA` flags to every API response and exported report, and reject `data_status=AUDITED` on `SIMULATED` or `PAPER` funds.
- **Consequences:** Impossible to silently blend environments or strip non-live disclaimers from exports.
