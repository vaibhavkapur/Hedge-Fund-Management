# API, CLI & Schema Documentation

## 1. REST API Endpoints (`src/hf_platform/api/server.py`)

All JSON endpoints return structured errors on failure:
```json
{
  "error_code": "ROLE_SEPARATION_VIOLATION",
  "message": "Role separation violation: maker 'usr_ctrl_01' cannot self-approve...",
  "details": { "actor_id": "usr_ctrl_01", "maker_id": "usr_ctrl_01" }
}
```

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Serves the self-contained Control Plane Web UI (`web/index.html`). |
| `GET` | `/api/health` | Returns platform health (`HEALTHY`/`DEGRADED`), adapter statuses, execution kill-switch state, audit chain verification, and operational metrics. |
| `GET` | `/api/master` | Returns funds, accounts, strategies, instruments, counterparties, and RBAC users. |
| `GET` | `/api/funds/{fund_id}/snapshots?date=YYYY-MM-DD` | Lists portfolio snapshots (positions, cash, risk metrics, strategy attribution, SHA-256 content hash). |
| `GET` | `/api/funds/{fund_id}/trial-balance?date=YYYY-MM-DD` | Computes double-entry trial balance by ledger account and currency. |
| `GET` | `/api/funds/{fund_id}/reconciliations?date=YYYY-MM-DD` | Lists multi-way reconciliation runs and itemized breaks. |
| `GET` | `/api/funds/{fund_id}/close-runs?date=YYYY-MM-DD` | Lists daily close attempts and their 8-step checklists. |
| `POST` | `/api/funds/{fund_id}/close-runs` | Triggers a daily close run (`{"business_date": "2026-11-25", "actor_id": "usr_acct_01"}`). |
| `POST` | `/api/close-runs/{close_run_id}/signoff` | Executes independent dual-control sign-off (`{"actor_id": "usr_ctrl_01"}`). |
| `GET` | `/api/exceptions?fund_id=...&date=...` | Lists operational exceptions with SLA due timestamps and approval state. |
| `POST` | `/api/exceptions/{exception_id}/propose` | Proposes resolution (`{"actor_id": "...", "resolution_notes": "..."}`). |
| `POST` | `/api/exceptions/{exception_id}/decide` | Maker-checker decision (`{"actor_id": "...", "action": "APPROVE|WAIVE|REJECT", "decision_notes": "..."}`). |
| `GET` | `/api/funds/{fund_id}/reports?date=YYYY-MM-DD` | Lists versioned report artifacts and revision diffs. |
| `POST` | `/api/reports/generate` | Generates a report from a frozen snapshot (`{"snapshot_id": "...", "report_type": "NAV_SUMMARY", "actor_id": "..."}`). |
| `POST` | `/api/reports/{report_id}/approve` | Approves and publishes a pending report (`{"actor_id": "usr_ctrl_01"}`). |
| `POST` | `/api/reports/{report_id}/revise` | Creates a versioned revision referencing a new frozen snapshot (`{"new_snapshot_id": "...", "reason": "...", "actor_id": "..."}`). |
| `GET` | `/api/audit?limit=N` | Returns hash-chained audit entries and cryptographic chain verification result. |
| `GET` | `/api/events?limit=N` | Returns structured domain event log entries. |

## 2. CLI Commands (`hfp`)

```bash
hfp health
hfp close --fund-id FUND_SAAT_MS --date 2026-11-25 --actor usr_acct_01 --approver usr_ctrl_01 --signoff
hfp audit-verify
hfp serve --host 127.0.0.1 --port 8080
```

## 3. Relational Persistence Schema

The canonical PostgreSQL DDL is committed in [docs/schema.sql](schema.sql). The local prototype runs the identical table layout in SQLite (`SQLiteStore`), enabling deterministic clean-checkout testing without requiring an external PostgreSQL daemon. Key tables:
- Master: `funds`, `counterparties`, `accounts`, `strategies`, `instruments`, `users`
- Operational & Accounting: `fills`, `manual_marks`, `journal_entries`, `portfolio_snapshots`, `reconciliations`, `close_runs`, `exceptions`, `reports`
- Governance: `audit_log` (append-only, hash-chained on `sequence_number`), `domain_events`
