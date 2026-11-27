#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHONPATH=src SAAT_QUIET_HTTP=1 python3 scripts/run_demo_and_evidence.py --mode demo
