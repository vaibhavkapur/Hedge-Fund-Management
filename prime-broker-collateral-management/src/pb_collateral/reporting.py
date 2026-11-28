"""Machine-readable JSON reporting and runtime metadata utilities."""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from pb_collateral import __schema_version__, __version__
from pb_collateral.models import ReconciliationResult, SubstitutionReviewResult
from pb_collateral.service import DiagnosticCounters


def _serialize(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    raise TypeError(f"Unsupported type for serialization: {type(obj).__name__}")


def runtime_metadata(config_hashes: Mapping[str, str]) -> Dict[str, Any]:
    return {
        "package_version": __version__,
        "schema_version": __schema_version__,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable": Path(sys.executable).name,
        "config_hashes": dict(sorted(config_hashes.items())),
    }


def serialize_reconciliation_report(
    *,
    scenario_name: str,
    results: Sequence[ReconciliationResult],
    substitutions: Sequence[SubstitutionReviewResult] = (),
    counters: DiagnosticCounters,
    metadata: Mapping[str, Any],
) -> Dict[str, Any]:
    payload = {
        "schema_version": __schema_version__,
        "scenario_name": scenario_name,
        "metadata": dict(metadata),
        "counters": counters.to_dict(),
        "reconciliations": [asdict(r) for r in results],
        "substitutions": [asdict(s) for s in substitutions],
    }
    # Round-trip through JSON to normalize Decimals/Dates/Enums cleanly
    return json.loads(json.dumps(payload, default=_serialize, sort_keys=True))


def write_json_report(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, default=_serialize, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
