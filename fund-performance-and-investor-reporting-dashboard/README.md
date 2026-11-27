# Fund Performance and Investor Reporting Dashboard

This dashboard uses synthetic net asset values (NAV), cash flows, and exchange rates to calculate gross and net fund returns, explain strategy contributions, and publish versioned investor reports. All sample performance is simulated and unaudited. Order execution is disabled.

## Getting started

Run the following commands from this directory with Python 3.9 or later. The local SQLite demo uses the Python standard library.

```bash
./scripts/setup.sh
./scripts/demo.sh
./scripts/validate.sh
```

The demo exports a revised investor report in HTML, JSON, CSV, and Markdown to [the demo exports directory](reports/demo_exports/). To explore the dashboard and REST API, start the server and open `http://127.0.0.1:8080`.

```bash
PYTHONPATH=src python3 -m fund_reporting.api.http_server --host 127.0.0.1 --port 8080
```

## Capabilities

1. **NAV & Cash-Flow Snapshot API** (`src/fund_reporting/domain/`, `src/fund_reporting/api/`): Canonical contracts with UTC `as_of_utc` / `availability_utc` timestamps, ISO-4217 currencies, strict ledger bridge reconciliation, SHA-256 ledger hashing, and point-in-time snapshot queries.
2. **Return & Risk Metric Library** (`src/fund_reporting/metrics/`): Daily sub-period Time-Weighted Returns (TWR) invariant to external cash flows (`START_OF_DAY` and `END_OF_DAY`), Modified Dietz cross-checks, Gross vs. Net fee drag, drawdown & recovery tracking, annualized volatility, Sharpe/Sortino/Calmar, historical VaR/CVaR, benchmark comparison, point-in-time multi-currency FX conversion, and Carino-linked multi-period strategy attribution.
3. **Strategy & Exposure Dashboard** (`src/fund_reporting/web/`): Zero-CDN interactive dashboard with SVG wealth/drawdown/exposure/attribution charts, Gross/Net toggle, USD/EUR/GBP reporting currency conversion, live RBAC role switcher, prominent non-live banners, and upstream adapter diagnostics.
4. **Versioned Report Export** (`src/fund_reporting/reporting/`): Content-addressed immutable `version_id` and SHA-256 digests, revision chaining (`supersedes_version_id`, `revision_reason`), and multi-format exports (`JSON`, `HTML`, `CSV`, `Markdown`).
5. **Review & Access Controls** (`src/fund_reporting/security/`): Role-Based Access Control (`VIEWER_INVESTOR`, `ANALYST`, `REVIEWER`, `APPROVER`, `ADMIN`), dual-control separation of duties (authors cannot review or approve their own reports), investor audience/status isolation, and an append-only audit log.

## Documentation

- [docs/architecture.md](docs/architecture.md) — Component boundaries, architecture diagram, and foundation adapter contracts
- [docs/ADRs.md](docs/ADRs.md) — Architecture Decision Records (stack, TWR/Carino linking, RBAC, IP boundary)
- [docs/assumptions_and_methodology.md](docs/assumptions_and_methodology.md) — Mathematical formulas, fee conventions, edge cases, and flags
- [docs/api_and_schema.md](docs/api_and_schema.md) — REST API specification and relational schema
- [docs/runbook.md](docs/runbook.md) — Operator controls, incident handling, and safe fallbacks
- [docs/unsupported_features.md](docs/unsupported_features.md) — Explicit scope boundaries and non-goals
- [reports/acceptance_evidence.md](reports/acceptance_evidence.md) — Acceptance criteria traceability matrix
- [reports/validation_report.md](reports/validation_report.md) — Latest automated test, scenario, and hand-calculated verification evidence
- [Original implementation plan](plan.md)
