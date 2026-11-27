# Operational Runbook & Controls

## 1. Operational Safety Posture

- **Order Execution Disabled:** `config/default_config.json` enforces `order_execution_enabled: false`. This service is strictly read/analytics/reporting and holds no broker execution credentials.
- **Credential Hygiene:** API tokens and database DSNs must be supplied via environment variables (`SAAT_DEMO_TOKEN_*` for local demo; enterprise IAM/OIDC + secret manager for sandbox/production). No production secrets exist in source control.
- **Data Entitlements:** Synthetic fixtures in `data/fixtures/` carry explicit provenance in `data/fixtures/manifest.json` (`contains_live_or_client_data: false`).

## 2. Standard Operating Procedures

### Clean Setup & Validation
```bash
./scripts/setup.sh
./scripts/validate.sh
./scripts/demo.sh
```

### Publishing an Investor Report (Dual Control)
1. **Analyst** verifies ledger reconciliation (`GET /api/v1/funds/{id}/analytics` -> `reconciliation_summary.all_snapshots_reconciled == true` and `nav_bridge_residual == 0`).
2. **Analyst** creates a `DRAFT` report (`POST /api/v1/reports`) and submits it (`POST /api/v1/reports/{vid}/submit`).
3. **Reviewer** (distinct actor ID from author) inspects disclosures, quality flags, and reconciliation bridge, then approves review (`POST /api/v1/reports/{vid}/review`).
4. **Approver** (distinct actor ID from author) approves and publishes (`POST /api/v1/reports/{vid}/publish`).
5. **Investor** downloads immutable versioned exports (`HTML`, `JSON`, `CSV`, `Markdown`).

### Post-Publication Restatement (Report Revision)
1. Ingest the corrected NAV snapshot with `revision = N + 1` and current `availability_utc`.
2. Call `POST /api/v1/reports/{old_vid}/revise` with a clear `revision_reason`.
3. Route the new revision through `submit -> review -> publish`.
4. Confirm `{old_vid}` automatically transitions to `SUPERSEDED` while retaining its original `content_sha256` and payload unchanged.

## 3. Incident Response & Safe Fallbacks

| Failure Code | Cause | Operator Response |
|---|---|---|
| `MISSING_NAV` | Missing trading-day valuation or non-positive NAV | Do **not** forward-fill. Obtain reconciled valuation from fund administrator/ledger and ingest before publishing. |
| `LEDGER_RECONCILIATION_FAILED` | Closing NAV disagrees with opening + cash flows + PnL - fees | Quarantine snapshot; inspect fee accruals and cash-flow timing (`START_OF_DAY` vs `END_OF_DAY`). |
| `MIXED_ENVIRONMENT_PROHIBITED` | Attempt to combine `SIMULATED`, `PAPER`, and `LIVE` records | Reject ingestion immediately; verify upstream pipeline routing tags. |
| `MISSING_FX_RATE` | Reporting currency conversion requested without point-in-time FX fix | Ingest official FX fixing for missing date or restrict report to fund base currency. |
| `SEPARATION_OF_DUTIES_VIOLATION` | Report author attempted to review/publish own report | Assign an independent `REVIEWER` and `APPROVER`; review `audit_log`. |
| `UNAUTHORIZED_ACCESS` | Repeated `DENY` events in `audit_log` | Inspect `GET /api/v1/audit`, revoke compromised tokens, and confirm fund entitlement scopes. |

## 4. Baseline Measurement & Observability

- Health check: `GET /api/v1/health` returns `status`, `methodology_version`, `order_execution_enabled`, `fund_count`, and `require_dual_control`.
- Audit trail: Every ALLOW and DENY decision is recorded in `audit_log` with UTC timestamp, actor, role, action, resource, and details.
- Baseline validation latency and test counts are captured automatically in `reports/validation_report.json` by `./scripts/validate.sh`.
