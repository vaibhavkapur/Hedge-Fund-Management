# Fund Operating Model, Responsibilities & Control Gates

## 1. Fund Structure & Data Ownership

The reference prototype models **SAAT Multi-Strategy Master Fund LP (`FUND_SAAT_MS`)** operating across:
- **2 Prime Broker / Custodian Accounts**: `ACCT_PB_ALPHA_EQ` (USD + EUR margin account) and `ACCT_PB_BETA_MAC` (USD custody account).
- **2 Strategies (Sub-Books)**: `STRAT_EQ_LS` (Systematic Equity Long/Short) and `STRAT_MACRO_ETF` (Global Macro & Rates ETF).
- **Explicit Data Ownership**:
  - **Operations (`OPERATIONS_ANALYST`)**: Trade/fill ingestion checks, prime broker reconciliation triage, exception investigation, administrator package dispatch.
  - **Fund Accounting (`FUND_ACCOUNTANT`)**: General ledger entries, EOD close preparation, NAV reconciliation, draft report preparation and restatements.
  - **Risk (`RISK_MANAGER`)**: Market mark override review, exposure/VaR monitoring, risk limit breach disposition.
  - **Controller (`CONTROLLER`)**: Independent four-eyes approval of exceptions, daily close sign-off, and official report publication.
  - **Compliance (`COMPLIANCE_OFFICER`)**: Independent secondary approval authority for close sign-off, waivers, and report publication.
  - **Portfolio Management (`PORTFOLIO_MANAGER`)**: Read-only visibility into consolidated portfolio, exposure, and attribution (no ledger posting, mark override, or close approval rights).
  - **Auditor (`AUDITOR`)**: Strictly read-only access to all snapshots, trial balances, reports, and hash-chained audit logs.
  - **System Admin (`SYSTEM_ADMIN`)**: Master configuration/bootstrap only; explicitly prohibited from financial approvals, ledger postings, or close sign-offs.

## 2. RBAC & Maker-Checker Separation Matrix

| Action | Allowed Roles | Four-Eyes Separation Enforced? |
|---|---|---|
| `master.write` | `CONTROLLER`, `OPERATIONS_ANALYST`, `SYSTEM_ADMIN` | N/A |
| `ledger.post` | `FUND_ACCOUNTANT`, `CONTROLLER` | Audited |
| `close.run` | `OPERATIONS_ANALYST`, `FUND_ACCOUNTANT`, `CONTROLLER` | Sets `signoff_preparer_id` |
| `close.signoff` | `CONTROLLER`, `COMPLIANCE_OFFICER` | **Yes** (`actor_id != signoff_preparer_id`) |
| `mark.override_propose` | `OPERATIONS_ANALYST`, `FUND_ACCOUNTANT`, `RISK_MANAGER` | Sets proposer |
| `mark.override_approve` | `CONTROLLER`, `RISK_MANAGER`, `COMPLIANCE_OFFICER` | **Yes** (`approver_id != proposer_id`) |
| `exception.propose_resolution` | `OPERATIONS_ANALYST`, `FUND_ACCOUNTANT`, `RISK_MANAGER` | Sets `proposed_by` |
| `exception.approve` | `CONTROLLER`, `RISK_MANAGER`, `COMPLIANCE_OFFICER` | **Yes** (`actor_id != proposed_by`) |
| `report.generate` / `report.revise` | `FUND_ACCOUNTANT`, `OPERATIONS_ANALYST`, `CONTROLLER` | Sets `generated_by` |
| `report.approve` | `CONTROLLER`, `COMPLIANCE_OFFICER` | **Yes** (`actor_id != generated_by`) |
| `report.deliver` | `OPERATIONS_ANALYST`, `FUND_ACCOUNTANT`, `CONTROLLER` | Requires `PUBLISHED` report |
| `execution.submit_order` | **NONE** (Kill-switch disabled by policy) | Hard blocked (`ExecutionDisabledError`) |

## 3. Eight-Stage Daily Close Checklist & Hard Gates

1. **`ingest_fills`**: Fetches internal fills; any fill with `received_at_utc > 21:00:00Z` is quarantined (`is_late=True`), deferred to `T+1`, and opens a `HIGH` `LATE_FILL` exception. On-time fills post idempotently to the ledger.
2. **`validate_market_data`**: Verifies every open position has a non-missing EOD mark within `max_mark_age_hours` (24h) and every active currency has an FX rate to USD. Missing/stale marks open `CRITICAL` exceptions and immediately block the close.
3. **`verify_trial_balance`**: Confirms `total_debit == total_credit` per currency across all posted journal entries.
4. **`reconcile_external_feeds`**: Executes 3-way matching (positions, cash, fills) against prime broker/custodian feeds using configured tolerances (`0.0001` qty, `5.0 bps` price, `$10.00` cash). Material breaks (`>= $1,000 USD`) open `HIGH`/`CRITICAL` exceptions.
5. **`compute_portfolio_and_risk`**: Valuates consolidated positions, accrues daily management fees (`150 bps / 365`), posts EOD MTM journal entries, verifies `Portfolio NAV - Ledger NAV == $0.00`, and evaluates risk limits.
6. **`check_exception_gates`**: Blocks snapshot freeze if any `HIGH` or `CRITICAL` exception for the business date remains unresolved/unwaived.
7. **`freeze_snapshot`**: Freezes the `PortfolioSnapshot`, computing and locking its SHA-256 `content_hash`.
8. **`dual_control_signoff`**: Transitions close run to `PENDING_SIGNOFF` until an independent `CONTROLLER` or `COMPLIANCE_OFFICER` signs off (`CLOSED`).
