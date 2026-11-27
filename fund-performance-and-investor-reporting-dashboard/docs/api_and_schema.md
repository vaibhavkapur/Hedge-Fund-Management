# REST API & Schema Reference

## Authentication & Headers

All `/api/v1/*` endpoints except `/api/v1/health` require:
```text
Authorization: Bearer <token>
```
Unauthenticated requests return `401 UNAUTHENTICATED`. Unauthorized role/fund/audience actions return `403 UNAUTHORIZED_ACCESS` or `403 SEPARATION_OF_DUTIES_VIOLATION` and write a `DENY` record to `audit_log`.

## Endpoints

| Method | Path | Permitted Roles | Description |
|---|---|---|---|
| `GET` | `/api/v1/health` | Public | Operational health, methodology version, `order_execution_enabled: false`. |
| `GET` | `/api/v1/whoami` | Any authenticated | Returns current actor ID, role, and fund entitlements. |
| `GET` | `/api/v1/funds` | Any authenticated | Lists funds entitled to the caller. |
| `POST` | `/api/v1/funds` | `ANALYST`, `ADMIN` | Registers or updates a fund definition. |
| `GET` | `/api/v1/funds/{fund_id}/snapshots` | `ANALYST`, `REVIEWER`, `APPROVER`, `ADMIN` | Lists reconciled NAV snapshots (`?start_date=&end_date=&as_of_utc=`). |
| `POST` | `/api/v1/funds/{fund_id}/snapshots` | `ANALYST`, `ADMIN` | Validates & ingests a NAV snapshot with optional `cash_flows` and `strategy_snapshots`. |
| `GET` | `/api/v1/funds/{fund_id}/analytics` | `ANALYST`, `REVIEWER`, `APPROVER`, `ADMIN` | Full reconciled analytics, benchmark, attribution, exposure, disclosures, and adapter diagnostics (`?currency=USD\|EUR\|GBP&start_date=&end_date=&as_of_utc=`). |
| `GET` | `/api/v1/reports` | All (filtered) | Lists reports; `VIEWER_INVESTOR` sees only `PUBLISHED`/`SUPERSEDED` `INVESTOR`-audience reports for entitled funds. |
| `POST` | `/api/v1/reports` | `ANALYST`, `ADMIN` | Creates a `DRAFT` versioned report with content-addressed `version_id` and SHA-256 digest. |
| `GET` | `/api/v1/reports/{version_id}` | Authorized | Fetches full report record & payload. |
| `POST` | `/api/v1/reports/{version_id}/submit` | `ANALYST`, `ADMIN` | Transitions `DRAFT` / `REJECTED` -> `SUBMITTED`. |
| `POST` | `/api/v1/reports/{version_id}/review` | `REVIEWER`, `ADMIN` | Dual-control review (`SUBMITTED` -> `REVIEWED` or `REJECTED`; author cannot review). |
| `POST` | `/api/v1/reports/{version_id}/publish` | `APPROVER`, `ADMIN` | Dual-control approval & publication (`REVIEWED` -> `PUBLISHED`; requires reconciled ledger; supersedes prior version if revision). |
| `POST` | `/api/v1/reports/{version_id}/revise` | `ANALYST`, `REVIEWER`, `APPROVER`, `ADMIN` | Creates `revision_number + 1` linked via `supersedes_version_id` with mandatory `revision_reason`. |
| `GET` | `/api/v1/reports/{version_id}/export` | Authorized | Exports report (`?format=json\|html\|csv\|markdown`). |
| `GET` | `/api/v1/audit` | `REVIEWER`, `APPROVER`, `ADMIN` | Reads append-only RBAC and workflow audit log (`?limit=100`). |

## Structured Error Envelope

```json
{
  "error": {
    "code": "LEDGER_RECONCILIATION_FAILED",
    "message": "NAV ledger bridge failed for SNAP-1...",
    "details": {
      "expected_closing_nav": 1009500.0,
      "actual_closing_nav": 1015000.0
    }
  }
}
```

## Relational Schema

The canonical PostgreSQL DDL is versioned in [`config/schema_postgres.sql`](../config/schema_postgres.sql) covering 8 tables:
`funds`, `nav_snapshots`, `cash_flows`, `fx_rates`, `benchmark_returns`, `strategy_snapshots`, `reports`, and `audit_log`.
