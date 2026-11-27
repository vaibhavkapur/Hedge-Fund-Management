#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Checking Python runtime (requires >= 3.9)"
python3 --version

echo "==> Regenerating deterministic synthetic fixtures (fixed seed 20260101)"
PYTHONPATH=src python3 data/fixtures/generate_fixtures.py

echo "==> Setup complete. Zero external runtime dependencies required."
