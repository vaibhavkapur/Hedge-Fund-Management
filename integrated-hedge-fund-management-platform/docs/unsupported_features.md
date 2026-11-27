# Explicit Non-Goals, Administrator Interfaces & Unresolved Controls

## 1. Explicit Unsupported Features & Non-Goals (MVP Scope)

Per `plan.md` Sections 1, 7, and 8, the following are **explicitly not implemented or claimed** in this prototype:

1. **No Regulatory Compliance Claim**: This platform is an internal engineering prototype and does not constitute SEC Form PF, AIFMD, UCITS, CFTC, GAAP/IFRS audited financial statement, or tax compliance software.
2. **Does Not Replace External Fund Administrator or Auditor**: Official NAV strike, investor capital statements, AML/KYC investor onboarding, and independent financial audits remain the responsibility of the external fund administrator and audit firm.
3. **Order Execution Disabled**: Live or paper order routing is intentionally disabled (`order_execution_enabled = false`). Real-capital deployment requires independent risk, legal, security, and broker certification outside this project.
4. **Complex Derivative / Corporate Action Accounting**: Options Greeks/surfaces, OTC swaps/margining, multi-leg corporate actions (mergers, spin-offs, tax-lot FIFO/LIFO optimization, wash sales), and performance fee / high-water-mark equalization series are out of MVP scope.
5. **Production Identity Provider / SSO / HSM**: Local prototype uses master-defined `UserPrincipal` IDs rather than OIDC/SAML SSO, mTLS, or hardware-backed signing keys.

## 2. External Fund Administrator Interface Contract

The `FundAdminAdapter` (`src/hf_platform/adapters/interfaces.py`) defines the handoff boundary for delivering `PUBLISHED` `ADMINISTRATOR_EXTRACT` packages containing:
- Frozen `snapshot_id` and SHA-256 `snapshot_content_hash`
- Fund NAV, NAV per share, shares outstanding, cash balances, accrued fees, and position schedule
- Dual-control `generated_by` and `approved_by` provenance metadata
- Versioned supersession lineage (`supersedes_report_id` and `revision_diff`) for restatements

## 3. Unresolved Production Controls (Before Approved Paper / Sandbox Rollout)

| Control Area | Current Prototype State | Required Before Production / Sandbox Approval |
|---|---|---|
| **Database & Durability** | Local SQLite WAL (`SQLiteStore`) | Managed PostgreSQL using `docs/schema.sql`, PITR backups, restricted DB role revoking `UPDATE`/`DELETE` on `audit_log`. |
| **Authentication & Transport Security** | Principal ID parameter; local HTTP | OIDC/SSO JWT verification, TLS 1.3, mTLS for service adapters, secrets manager integration. |
| **Background IP & Linked Repos** | Clean-room adapter contracts | Legal/IP review and pinned-commit audit of linked personal repos before adapting any external code. |
| **Market Data & Broker Feeds** | Deterministic synthetic fixtures | Licensed vendor entitlements, SFTP/FIX/REST production adapters with retry/dead-letter queues and circuit breakers. |
| **SAAT Operating Parameters** | Synthetic multi-strategy equity/ETF fixture | Confirm actual SAAT asset class, strategy horizon, cutoff timezone, fee waterfall, and administrator file format. |
