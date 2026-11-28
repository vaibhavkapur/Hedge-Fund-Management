#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

echo "==> 1. Running unit, integration, boundary, and worked-example tests..."
python3 -m unittest discover -s tests -v

echo "==> 2. Running reproducible demo & generating reports..."
python3 scripts/run_demo.py

echo "==> 3. Verifying reproducibility across clean second run..."
HASH_1="$(python3 -c 'import hashlib, pathlib; print(hashlib.sha256(pathlib.Path("reports/demo_summary.json").read_bytes()).hexdigest())')"
python3 scripts/run_demo.py > /dev/null
HASH_2="$(python3 -c 'import hashlib, pathlib; print(hashlib.sha256(pathlib.Path("reports/demo_summary.json").read_bytes()).hexdigest())')"

if [ "${HASH_1}" != "${HASH_2}" ]; then
  echo "ERROR: Reproducibility check failed (${HASH_1} != ${HASH_2})" >&2
  exit 1
fi

echo "==> Validation succeeded! Deterministic demo_summary.json SHA-256: ${HASH_1}"
