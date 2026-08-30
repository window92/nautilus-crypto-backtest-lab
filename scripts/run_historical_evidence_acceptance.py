#!/usr/bin/env python3
"""Run every self-contained historical validator under its frozen contract."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.historical_contracts import HistoricalValidationState
from crypto_lab.historical_contracts import validate_validator_contract


ROOT = Path(__file__).resolve().parents[1]
VALIDATORS = (
    "validate_audit_qualification.py",
    "validate_audit_research_runs.py",
    "validate_m1_evidence.py",
    "validate_m2_evidence.py",
    "validate_m3_evidence.py",
    "validate_m4_evidence.py",
    "validate_data_provenance_evidence.py",
    "validate_instrument_repair_evidence.py",
    "validate_owner_smoke_002_replacement_evidence.py",
    "validate_native_research_metrics_readiness_evidence.py",
    "validate_owner_strategy_research_001_evidence.py",
)
DATA_TOOL_VALIDATORS = {"validate_data_provenance_evidence.py"}


def _parse_json_output(stdout: str) -> dict[str, Any] | None:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def run_acceptance() -> dict[str, Any]:
    environment = dict(os.environ)
    environment.update(
        {
            "TZ": "UTC",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(ROOT / "src"),
        },
    )
    results: dict[str, Any] = {}
    for name in VALIDATORS:
        contract = validate_validator_contract(name, repository_root=ROOT)
        interpreter = (
            ROOT / ".data-venv/bin/python"
            if name in DATA_TOOL_VALIDATORS
            else Path(sys.executable)
        )
        command = [str(interpreter), str(ROOT / "scripts" / name)]
        command_environment = dict(environment)
        if name in DATA_TOOL_VALIDATORS:
            command_environment["PYTHONPATH"] = os.pathsep.join(
                (
                    str(ROOT / "src"),
                    str(ROOT),
                    str(ROOT / ".venv/lib/python3.12/site-packages"),
                ),
            )
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=command_environment,
            capture_output=True,
            text=True,
            check=False,
        )
        validator_output = _parse_json_output(completed.stdout)
        validator_pass = bool(
            completed.returncode == 0
            and validator_output is not None
            and validator_output.get("status") == "PASS"
        )
        classification = (
            contract.state
            if validator_pass and contract.acceptable
            else HistoricalValidationState.EVIDENCE_CORRUPT
        )
        results[name] = {
            "command": command,
            "exit_code": completed.returncode,
            "validator_status": (
                None if validator_output is None else validator_output.get("status")
            ),
            "classification": classification.value,
            "contract": contract.to_builtins(),
            "stderr": completed.stderr,
            "stdout_was_json": validator_output is not None,
            "pass": validator_pass and contract.acceptable,
        }
    passed = sum(bool(item["pass"]) for item in results.values())
    return {
        "schema": "historical-evidence-acceptance-v1",
        "status": "PASS" if passed == len(results) else "FAIL",
        "validator_count": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
        "historical_evidence_mutated": False,
        "current_root_equality_required": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = run_acceptance()
    payload = canonical_json_bytes(result) + b"\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_bytes(payload)
    print(payload.decode("utf-8"), end="")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
