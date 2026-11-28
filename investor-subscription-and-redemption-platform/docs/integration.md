# Integration Boundaries — `Trading-Management`

## 1. Sibling Project Verification

Sibling projects at the `Trading-Management` repository root:
- `integrated-hedge-fund-management-platform/`
- `fund-nav-calculation-engine/`
- `hedge-fund-fee-accounting-engine/`
- `prime-broker-collateral-management/`
- `fund-performance-and-investor-reporting-dashboard/`
- `regulatory-reporting-engine/`

Per architectural policy, this platform remains independently runnable without cross-package code imports, and contains zero dependency on private employer or SAAT artifacts. Instead, `src/isrp/adapters.py` provides standalone, versioned JSON adapter contracts.

## 2. Runnable Adapter Interfaces (`src/isrp/adapters.py`)

| Adapter Function | Direction | Sibling Counterpart | Contract Identifier |
|---|---|---|---|
| `ingest_approved_nav_payload(service, payload)` | Inbound | `fund-nav-calculation-engine` | Approved NAV JSON payload (`nav_id`, `fund_id`, `share_class`, `dealing_date`, `currency`, `nav_per_share`, `version`, `approved_by`, `approved_at`) |
| `export_fee_accounting_feed(service)` | Outbound | `hedge-fund-fee-accounting-engine` | `isrp.fee_accounting_feed.v1` — immutable journal fee deltas keyed by `journal_entry_id`, `request_id`, `dealing_date`, `nav_version` |
| `export_regulatory_flow_report(service)` | Outbound | `regulatory-reporting-engine` | `isrp.regulatory_capital_flows.v1` — reconciled register positions, total inbound/outbound cash, `config_hash`, and `audit_chain_verified` flag |

## 3. Compatibility & Transport

- **Python Library Interface:** `PlatformService` is a synchronous typed Python API usable directly by batch jobs, CLI runners, or ASGI/WSGI web frameworks.
- **HTTP/JSON Interface:**
  - `src/isrp/api/fastapi_app.py`: FastAPI ASGI app (`create_fastapi_app(service)`) for environments with optional extra `.[api]` installed.
  - `src/isrp/api/http_server.py`: Zero-dependency stdlib HTTP server and `dispatch_http_request()` router exposing the identical route table and HTTP status codes.
- **Database Persistence:**
  - `SQLiteRepository` (`:memory:` or file-backed `.sqlite3`) works out-of-the-box on Python 3.9+.
  - `PostgresRepository` + `src/isrp/migrations/001_initial_schema.sql` targets PostgreSQL 15+ with `psycopg[binary]==3.2.3`.
