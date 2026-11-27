#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p reports/evidence

echo "=== 1. Ruff lint check ==="
.venv/bin/ruff check src tests scripts

echo "=== 2. Pytest suite (unit, integration, scenarios) ==="
.venv/bin/pytest -v --junitxml=reports/evidence/pytest_junit.xml | tee reports/evidence/pytest_output.txt

echo "=== 3. Reproducible end-to-end demo ==="
.venv/bin/python scripts/demo.py

echo "=== 4. M4 Simulated Daily Close & 7-Scenario Incident Drill ==="
.venv/bin/python scripts/incident_drill.py

echo "=== 5. CLI health and audit verification ==="
.venv/bin/hfp health

echo "All validation checks passed."
