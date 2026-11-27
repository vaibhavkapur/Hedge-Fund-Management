# Acceptance Criteria & Definition of Done Checklist

Every acceptance criterion from `plan.md` Section 6 is verified with committed code, automated tests, and retained raw evidence:

| Acceptance Criterion (`plan.md` §6) | Status | Verification Evidence |
|---|---|---|
| Close workflow flags unresolved breaks and missing data. | **DONE** | `tests/scenarios/test_missing_marks.py`, `tests/scenarios/test_reconciliation_mismatch.py`, `tests/scenarios/test_late_fills.py`, `reports/evidence/incident_drill_evidence.json` |
| Portfolio totals reconcile. | **DONE** | Exact `$0.00` Portfolio NAV vs Ledger NAV diff verified in `tests/unit/test_portfolio_and_risk.py`, `tests/integration/test_daily_close_e2e.py`, `reports/evidence/demo_summary.json` |
| Role separation and audit trail are enforced. | **DONE** | `tests/unit/test_rbac_and_audit.py`, `tests/scenarios/test_unauthorized_approval.py` (8-role RBAC + four-eyes maker-checker + SHA-256 hash chain + SQL tamper detection) |
| Reports refer to frozen snapshots. | **DONE** | `tests/unit/test_reconciliation_and_reporting.py` rejects unfrozen snapshots; every `ReportArtifact` embeds `snapshot_id` and `snapshot_content_hash` |
| Corrections are versioned and reviewable. | **DONE** | `tests/scenarios/test_report_revision.py` verifies `v1 -> v2`, mandatory reason, structured `ReportRevisionDiff`, dual-control approval, and `SUPERSEDED` lineage |
| A clean checkout can run the documented setup, tests and demo with permitted fixtures. | **DONE** | `scripts/setup.sh`, `scripts/validate.sh`, `pyproject.toml`, `uv.lock`, synthetic fixtures in `data/fixtures/` |
| Architecture, dependency assumptions, known limitations and runbook are committed. | **DONE** | `docs/architecture.md`, `docs/operating_model.md`, `docs/api_and_schema.md`, `docs/schema.sql`, `docs/dependency_and_ip_audit.md`, `docs/runbook.md`, `docs/unsupported_features.md`, `docs/adr/ADR-001..004` |
| Relevant CI checks pass and raw evaluation or test evidence is retained. | **DONE** | `ruff check` (0 errors), `pytest` (25/25 passed), raw JUnit XML (`reports/evidence/pytest_junit.xml`), `pytest_output.txt`, `demo_summary.json`, `incident_drill_evidence.json` |
| Security, data licensing, IP and any deployment approval requirements are reviewed. | **DONE** | `docs/dependency_and_ip_audit.md`, `config/fixtures_manifest.json`, `docs/unsupported_features.md`; zero secrets in code; order execution disabled by default (`POL-EXEC-001-DISABLED-BY-DEFAULT`) |
