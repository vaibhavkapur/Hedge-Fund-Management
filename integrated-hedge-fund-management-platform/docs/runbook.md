# Operator Runbook & Incident Response Procedures

## 1. Routine End-of-Day Close Operation

1. **Pre-Close Health Check**: Run `hfp health` or inspect `/api/health`. Confirm `status == "HEALTHY"`, `order_execution_enabled == false`, and `audit_integrity.verified == true`.
2. **Execute Daily Close**: Fund Accountant or Operations Analyst triggers `POST /api/funds/{fund_id}/close-runs` (or `hfp close --date YYYY-MM-DD`).
3. **Review Checklist**: Verify all steps 1–7 report `PASSED` and `nav_reconciliation_diff_usd == "0.00"`.
4. **Dual-Control Sign-Off**: Independent `CONTROLLER` or `COMPLIANCE_OFFICER` reviews the frozen snapshot and executes `POST /api/close-runs/{id}/signoff`.
5. **Report Publication & Admin Handoff**: Generate required reports (`NAV_SUMMARY`, `RISK_EXPOSURE`, `ADMINISTRATOR_EXTRACT`), obtain independent approval (`PUBLISHED`), and dispatch via `deliver_to_administrator`.

## 2. Incident Response Playbooks

### 2.1 External Service Outage (`DEGRADED_OUTAGE`)
- **Detection**: Close run halts in `DEGRADED_OUTAGE`; `SERVICE_OUTAGE_DETECTED` event emitted; `/api/health` reports `DEGRADED`.
- **Safe Fallback**: **Never** bypass reconciliation or freeze a partial snapshot during an outage. The system preserves prior state cleanly with zero corrupted ledger postings.
- **Recovery**:
  1. Confirm vendor / prime broker feed restoration via `/api/health` (`status == "HEALTHY"`).
  2. Operations Analyst proposes resolution on the `SERVICE_OUTAGE` exception with incident ticket ID; Controller approves.
  3. Re-run daily close (`attempt + 1`). Fill and ledger postings are idempotent.

### 2.2 Missing or Stale Market Marks (`BLOCKED`)
- **Detection**: Close blocks at Step 2 (`validate_market_data`) with `CRITICAL` `MISSING_MARK` or `STALE_MARK` exceptions.
- **Recovery**:
  1. Verify official exchange closing print from secondary authorized source.
  2. Operations/Accountant proposes manual mark override; independent Risk Manager or Controller approves (`apply_manual_mark_override`), automatically recording maker-checker provenance and closing the exception.
  3. Re-run daily close.

### 2.3 Late Fills After 21:00 UTC Cutoff (`BLOCKED`)
- **Detection**: Step 1 (`ingest_fills`) quarantines fills with `received_at_utc > 21:00:00Z`, marks `is_late=True`, sets `deferred_to_date = T+1`, and opens a `HIGH` `LATE_FILL` exception.
- **Recovery**:
  1. Operations verifies execution vs receipt timestamps with broker.
  2. Propose resolution confirming T+1 booking (or initiate formal restatement if material).
  3. Independent Controller approves; re-run T+0 close.

### 2.4 Prime Broker Reconciliation Mismatch (`BLOCKED`)
- **Detection**: Step 4 classifies breaks (`POSITION_QUANTITY`, `POSITION_PRICE`, `CASH_BALANCE`, `FILL_MISMATCH`, `MISSING_INTERNAL_FILL`, `MISSING_EXTERNAL_FILL`) and blocks Step 6 if material.
- **Recovery**:
  1. Investigate break against prime broker confirmation / ticket.
  2. Either book corrective balanced journal entry (`ledger.post`) or propose a documented temporary T+1 settlement waiver.
  3. Obtain independent Controller/Compliance approval and re-run close.

### 2.5 Post-Close Restatement / Report Revision
- **Procedure**:
  1. Apply approved corrective mark override or balanced adjusting journal entry.
  2. Generate and freeze a **new** immutable snapshot (`v2`); never mutate `v1`.
  3. Call `revise_report` with mandatory `reason`; inspect the computed `revision_diff`.
  4. Obtain independent Controller/Compliance approval to publish `v2`, which automatically marks `v1` as `SUPERSEDED`.

### 2.6 Audit Chain Integrity Failure
- **Detection**: `hfp audit-verify` or `/api/audit` raises `AuditIntegrityError`.
- **Action**: Immediately freeze all operational workflows, escalate to CTO, Compliance, and Security Engineering, and preserve SQLite/PostgreSQL disk snapshots for forensic review.

## 3. Running the Automated Incident Drill

```bash
.venv/bin/python scripts/incident_drill.py
```
Executes all 7 failure scenarios plus a 25-iteration baseline latency benchmark and writes raw evidence to `reports/evidence/incident_drill_evidence.json`.
