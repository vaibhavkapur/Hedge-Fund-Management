# Integration & Adapter Boundaries

## 1. Repository Context (`Trading-Management`)

Neighboring projects at the `Trading-Management` repository root:

- `integrated-hedge-fund-management-platform/`
- `fund-nav-calculation-engine/`
- `hedge-fund-fee-accounting-engine/`
- `investor-subscription-and-redemption-platform/`
- `fund-performance-and-investor-reporting-dashboard/`
- `regulatory-reporting-engine/`

To keep each project independently runnable without tight code coupling, `prime-broker-collateral-management` exposes clean JSON/typed adapter contracts so sibling projects can consume its outputs over versioned payloads:

- **Downstream to `fund-nav-calculation-engine`:** Consume `reconciliations[].financing.net_accrual_base` and `reconciliations[].valuation` cash/encumbered balances as daily accrual and encumbrance feeds.
- **Downstream to `regulatory-reporting-engine`:** Consume `reconciliations[].valuation.rehypothecated_collateral_base`, `encumbered_collateral_base`, and `margin_call` notices for Form PF / AIFMD / liquidity encumbrance schedules.

## 2. Persistence Adapter Boundary (`JournalStore`)

The domain layer has zero direct database calls. Persistence is isolated behind the `JournalStore` protocol in `src/pb_collateral/journal.py`:

| Adapter | Status in This Environment | Intended Use |
|---|---|---|
| `SQLiteJournalStore` | **Tested & Verified** | Default local offline append-only ledger (stdlib `sqlite3`) with `BEFORE UPDATE`/`BEFORE DELETE` immutability triggers |
| `PostgresJournalAdapter` | **Schema & Error Path Tested; Live DB NOT RUN** | PostgreSQL 14+ deployment using `src/pb_collateral/sql/postgres_schema.sql` (`NUMERIC(24,8)`, `JSONB`, PL/pgSQL immutability triggers) and optional `psycopg[binary]==3.2.3` |

## 3. External Broker Feed Integration

Prime broker statement feeds are heterogeneous in practice. Rather than hardcoding proprietary broker parsers, `validation.py` defines canonical alias maps (`ASSET_CLASS_ALIASES`, `ENCUMBRANCE_ALIASES`, `CASH_TYPE_ALIASES`). Any real broker CSV/FIX/SWIFT MT535 adapter should live as an independent pre-processor converting raw feeds into `schema_version = "1.0.0"` JSON payloads before invoking `normalize_broker_statement`.

## 4. Production Exclusions

No live prime broker APIs, SWIFT/DTCC/Euroclear settlement messaging, real credentials, or proprietary employer/SAAT code are referenced or enabled.
