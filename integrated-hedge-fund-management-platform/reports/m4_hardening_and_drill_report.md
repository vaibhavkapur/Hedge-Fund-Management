# M4 Validation Report — Hardening, Incident Drill & Delivery

- **Milestone**: M4 — Hardening and Delivery
- **Status**: Complete
- **Date**: 2026-11-27

## 1. Simulated Daily Close & 7-Scenario Incident Drill

Executed via `.venv/bin/python scripts/incident_drill.py` (raw evidence archived in [evidence/incident_drill_evidence.json](evidence/incident_drill_evidence.json)):

| # | Scenario (`plan.md` §5) | Injected Fault / Fixture | Observed Outcome | Status |
|---|---|---|---|---|
| 1 | **Unbalanced ledger** | `scenario_unbalanced_ledger.json` (USD DR 5000 vs CR 4500) | Rejected (`LEDGER_UNBALANCED`, diff `500.00`), not persisted, audited, chain verified | PASS |
| 2 | **Missing marks** | `scenario_missing_marks.json` (NVDA omitted, SAP 48h stale) | Close `BLOCKED` (2 `CRITICAL` exceptions) -> dual-control manual mark overrides -> `CLOSED` | PASS |
| 3 | **Unauthorized approval** | PM, SysAdmin, Auditor sign-offs + Controller self-approval | All 4 rejected (`AUTHORIZATION_ERROR` / `ROLE_SEPARATION_VIOLATION`), audited | PASS |
| 4 | **Late fills** | `scenario_late_fills.json` (`received_at_utc = 21:35Z > 21:00Z`) | Fill quarantined & deferred to T+1; `LATE_FILL` exception blocks close until approved | PASS |
| 5 | **Reconciliation mismatch** | `day2_reconciliation_breaks.json` (qty, cash, fill mismatch, unbooked fill) | 4 breaks classified (`$106,100.00` impact), close `BLOCKED`, snapshot kept unfrozen | PASS |
| 6 | **Report revision** | Restate AAPL close `198.50 -> 199.00` (+`$5,000.00` NAV) | New frozen `v2` snapshot; `v2` report diff captures exact NAV delta; `v1` marked `SUPERSEDED` | PASS |
| 7 | **Service outage** | `FaultState` market data & prime broker outages | Close halts safely in `DEGRADED_OUTAGE` (no partial freeze); clean idempotent recovery after restoration | PASS |

## 2. Baseline Performance Measurements

Measured across `n = 25` clean full Day-1 close + dual-control sign-off runs (7 fills, 7 positions, 2 accounts, multi-currency USD/EUR) on macOS arm64, Python 3.12.15, in-memory SQLite:
- **Mean**: `1.95 ms` | **Median**: `1.86 ms` | **StdDev**: `0.30 ms` | **P95**: `2.66 ms` | **Range**: `1.69–2.70 ms`
- *Disclaimer*: Synthetic prototype baseline only; not a production SLA or throughput claim.

## 3. Administrator Interfaces & Unresolved Production Controls

Documented explicitly in [docs/unsupported_features.md](../docs/unsupported_features.md) and [docs/runbook.md](../docs/runbook.md), including PostgreSQL migration (`docs/schema.sql`), OIDC/mTLS auth, background IP review of linked repos, and confirmation of actual SAAT asset classes and operating constraints.
