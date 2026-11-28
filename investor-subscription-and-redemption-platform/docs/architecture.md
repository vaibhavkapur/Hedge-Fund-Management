# Architecture — Investor Subscription and Redemption Platform

## 1. Component Architecture

The platform separates source records, pure `Decimal` pricing calculations, workflow state machines, immutable share journals, hash-chained audit logs, persistence adapters, and HTTP transport.

```mermaid
flowchart LR
    API["HTTP API (FastAPI / stdlib http_server)"] --> SVC["PlatformService"]
    CLI["CLI Scripts (run_demo / run_validation)"] --> SVC
    ADP["Sibling Adapters (NAV / Fee / Reg Reporting)"] --> SVC

    SVC --> ONB["onboarding.py (Investor State Machine + Mock KYC/AML)"]
    SVC --> WF["workflow.py (Request State Machine + Maker-Checker + Cutoff)"]
    SVC --> PRC["pricing.py (Pure Decimal Share/Fee/Restatement Math)"]
    SVC --> REC["reconciliation.py (Cash Matching + Share Register + Proofs)"]
    SVC --> AUD["audit.py (SHA-256 Hash-Chained Audit + Diagnostics)"]

    SVC --> REPO["Repository Protocol"]
    REPO --> SQLITE["SQLiteRepository (Offline Tested)"]
    REPO --> PG["PostgresRepository (DDL + psycopg Adapter)"]
```

## 2. State Machines

### Investor Onboarding Lifecycle

```mermaid
stateDiagram-v2
    [*] --> PROSPECT
    PROSPECT --> ONBOARDING_PENDING : run_onboarding()
    ONBOARDING_PENDING --> KYC_APPROVED : all mock checks pass
    ONBOARDING_PENDING --> KYC_REJECTED : any mock check fails
    KYC_REJECTED --> ONBOARDING_PENDING : re-evaluate
    KYC_APPROVED --> ACTIVE : activate_investor()
    ACTIVE --> SUSPENDED : suspend_investor()
    SUSPENDED --> ACTIVE : activate_investor()
    ACTIVE --> CLOSED
    SUSPENDED --> CLOSED
    KYC_REJECTED --> CLOSED
```

### Capital Flow Request Lifecycle

```mermaid
stateDiagram-v2
    [*] --> PENDING_APPROVAL : submit_request() (idempotent, active KYC, cutoff, balance checked)
    PENDING_APPROVAL --> APPROVED : review_request(APPROVED, checker != maker)
    PENDING_APPROVAL --> REJECTED : review_request(REJECTED, checker != maker)
    PENDING_APPROVAL --> CANCELLED : cancel_request()
    APPROVED --> CASH_CONFIRMED : record_cash_confirmation(MATCHED)
    APPROVED --> PRICED : price_request() (redemptions or pre-priced)
    APPROVED --> EXCEPTION_REVIEW : delayed/mismatched cash
    EXCEPTION_REVIEW --> CASH_CONFIRMED : resolve_exception(RESOLVED, reviewer != creator)
    EXCEPTION_REVIEW --> REJECTED : resolve_exception(REJECTED)
    CASH_CONFIRMED --> PRICED : price_request()
    PRICED --> SETTLED : settle_request() (approved + matched cash + no open exceptions)
    CASH_CONFIRMED --> SETTLED : settle_request() (if already priced)
    SETTLED --> RESTATED : restate_nav() (new immutable journal adjustment)
```

## 3. Core Invariants

1. **Exact Decimal Arithmetic:** All cash, fee, NAV, and share values use `decimal.Decimal` with explicit quantization (`2dp` cash, `6dp` shares/NAV, `ROUND_HALF_UP`). Floating-point inputs are rejected at the contract boundary.
2. **Unapproved Requests Cannot Settle:** `price_request()` and `settle_request()` both invoke `ensure_request_approved()`, enforcing presence of `checker_id != maker_id` and `approved_at`.
3. **Maker-Checker Separation:** Enforced across request review, exception resolution, and NAV restatement.
4. **Immutable Append-Only Ledger & Corrections as New Versions:** `share_journal` rows and `audit_events` are append-only. Settled requests cannot be mutated or cancelled; NAV restatements create a new `NAVRecord` version, mark the prior version `SUPERSEDED`, and append a `NAV_RESTATEMENT_ADJUSTMENT` entry referencing `supersedes_entry_id`.
5. **Exact Share & Cash Reconciliation:** `verify_platform_reconciliation()` asserts zero discrepancy between the aggregated share register, the sum of immutable journal deltas, and confirmed inbound/outbound cash for settled requests, and verifies no negative share balances exist.
6. **Tamper-Evident Audit Chain:** Every state transition and blocked failure appends an `AuditEvent` linked via `SHA-256(sequence, event_id, timestamp, actor_id, action, entity_type, entity_id, payload, prev_hash)`.

## 4. Key Trade-offs

- **Offline Core vs. Optional Web/DB Stack:** Core domain, SQLite repository, and stdlib HTTP router have zero third-party runtime dependencies so every test and demo runs deterministically in air-gapped Python 3.9+ environments. FastAPI and PostgreSQL adapters are included with pinned optional dependencies (`pyproject.toml`, `requirements.txt`, `001_initial_schema.sql`).
- **Rounding Residual Accounting:** Subscription share issuance (`net_cash / nav_per_share`) can produce sub-cent rounding residuals when `net_cash` is not evenly divisible by NAV. Rather than hiding dust, `PricingCalculation` records `rounding_residual` explicitly and rejects residuals exceeding `rounding.max_valuation_residual` (`0.01` default).
- **Single-Writer Sequential Audit Chain:** The hash-chained audit sequence is linear and deterministic, which simplifies verification and replay in an educational prototype; high-concurrency production deployments would require serialized sequence allocation or database advisory locking in PostgreSQL.
