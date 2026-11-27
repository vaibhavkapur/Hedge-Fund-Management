# Integrated Hedge Fund Management Platform

This platform runs a synthetic fund through a daily close. It posts fills, marks, and fees to a double-entry ledger, reconciles the books against broker and custodian records, and freezes a portfolio snapshot for independent sign-off and reporting. Order execution is disabled. The prototype uses synthetic fixtures and provides no regulatory certification or independent audit.

## Getting started

From this directory, use Python with the `uv` module installed (`python3 -m uv --version`). Setup creates a Python 3.12 environment and installs the project and its validation tools.

```bash
./scripts/setup.sh
./scripts/validate.sh
```

Validation runs Ruff, the test suite, a daily close, and a seven-scenario incident drill. Inspect the [close summary](reports/evidence/demo_summary.json) and [incident drill results](reports/evidence/incident_drill_evidence.json). To explore the control plane and REST API locally, run `.venv/bin/hfp serve` and open `http://127.0.0.1:8080`.

## Capabilities

| Workstream | Implementation |
|---|---|
| **Fund / Account / Strategy Master** | Canonical Pydantic contracts (`src/hf_platform/domain/master.py`) with referential integrity checks, UTC timestamps, explicit currencies, and versioned master bundles. |
| **Double-Entry General Ledger** | Strict per-currency `debits == credits` invariant (`src/hf_platform/domain/ledger.py`, `src/hf_platform/services/ledger_service.py`), idempotent fill/MTM/fee posting, and trial balance verification. |
| **Consolidated Portfolio & Risk** | Exact `Decimal` multi-currency valuation, `$0.00` ledger NAV reconciliation, gross/net leverage, beta-adjusted exposure, parametric daily VaR (95%), equity/rates stress tests, concentration/counterparty breakdowns, strategy attribution, and SHA-256 content-hashed frozen snapshots. |
| **Daily Close & Multi-Way Reconciliation** | Deterministic 8-stage close checklist (`src/hf_platform/services/close_service.py`) integrating IBOR vs prime broker/custodian position, cash, and fill matching, 21:00 UTC late-fill cutoff quarantine, missing/stale mark blocking, and safe `DEGRADED_OUTAGE` halt on external feed failures. |
| **Exception Management & RBAC Approvals** | SLA-tracked exception lifecycle (`OPEN -> PENDING_APPROVAL -> RESOLVED / WAIVED / REJECTED`) with explicit RBAC matrix (`8` roles) and strict Maker-Checker (four-eyes) separation; `SYSTEM_ADMIN` and `AUDITOR` cannot approve financial controls. |
| **Reporting & Hash-Chained Audit Trail** | Reports strictly require **frozen** snapshots; revisions are versioned (`v1 -> v2`) with field-level diffs, mandatory reasons, and dual-control approval; all actions and denials are written to an append-only SHA-256 hash-chained audit log plus structured domain event stream. |
| **Control Plane API, CLI & UI** | Transport-agnostic REST router + HTTP server (`src/hf_platform/api/server.py`), zero-CDN web dashboard (`src/hf_platform/web/index.html`), and `hfp` CLI (`src/hf_platform/cli.py`). |

## Project layout

```text
integrated-hedge-fund-management-platform/
├── plan.md                        # Original implementation plan
├── pyproject.toml                 # Package metadata, dependencies, ruff & pytest config
├── uv.lock                        # Reproducible dependency lockfile
├── config/                        # Versioned platform settings & fixture provenance manifest (no secrets)
├── data/fixtures/                 # Synthetic master data, Day 1/2 feeds, and 7 fault fixtures
├── docs/                          # Architecture, ADRs, schema.sql, operating model, IP audit, runbook
├── src/hf_platform/               # Domain contracts, adapters, storage, services, API, CLI, Web UI
├── scripts/                       # setup.sh, validate.sh, demo.py, incident_drill.py
├── tests/                         # unit/, integration/, scenarios/ (all 7 failure scenarios)
└── reports/                       # M1-M4 milestone reports, acceptance checklist, raw evidence/
```

## Documentation

- [Architecture & Component Boundaries](docs/architecture.md)
- [Fund Operating Model & RBAC Matrix](docs/operating_model.md)
- [API, CLI & PostgreSQL-Compatible Schema](docs/api_and_schema.md) ([schema.sql](docs/schema.sql))
- [Dependency & IP Audit](docs/dependency_and_ip_audit.md)
- [Operator Runbook & Incident Drills](docs/runbook.md)
- [Unsupported Features & Unresolved Controls](docs/unsupported_features.md)
- [Architecture Decision Records](docs/adr/ADR-001-clean-room-contracts.md)
- [Acceptance Checklist & Evidence](reports/acceptance_checklist.md)
- [Original implementation plan](plan.md)
