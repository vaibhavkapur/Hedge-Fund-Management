#!/usr/bin/env python3
"""Verify bit-for-bit logical reproducibility of baseline and multi-broker demo runs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from pb_collateral.cli import run_bundle


def main() -> int:
    policy = PROJECT_ROOT / "config" / "default_policy.json"
    financing = PROJECT_ROOT / "config" / "financing_rates.json"

    for fixture_name in ("reference_oracle.json", "demo_multi_broker_bundle.json"):
        fixture = PROJECT_ROOT / "data" / "fixtures" / fixture_name
        r1 = run_bundle(policy_path=policy, financing_path=financing, fixture_path=fixture)
        r2 = run_bundle(policy_path=policy, financing_path=financing, fixture_path=fixture)
        if r1 != r2:
            print(f"REPRODUCIBILITY FAILURE on {fixture_name}", file=sys.stderr)
            return 1
        print(
            f"OK: {fixture_name} reproducible across repeated runs "
            f"(run_ids={[x['run_id'] for x in r1['reconciliations']]})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
