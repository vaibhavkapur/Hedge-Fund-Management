#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

./scripts/setup.sh
./scripts/validate.sh
./scripts/demo.sh
echo "==> Full clean reproduction succeeded."
