#!/usr/bin/env python3
"""Read-only validation of the complete M1 acceptance evidence bundle."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import sha256_file


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_RELATIVE = os.environ.get(
    "M1_EVIDENCE_RELATIVE",
    "evidence/m1/m1-acceptance-001",
)
EVIDENCE = ROOT / EVIDENCE_RELATIVE
SSOT_SHA256 = "7bb2fc68d9b73b168a582d890a6f952fd0c4eb20fc0e31857903909f27dfaa8f"
RUNTIME_LOCK_SHA256 = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
DEPENDENCY_LOCK_SHA256 = "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"


def _read(name: str) -> dict[str, object]:
    value = json.loads((EVIDENCE / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def validate() -> dict[str, object]:
    checks: dict[str, bool] = {}
    tests = _read("test-results.json")
    phase_totals = tests.get("phase_totals", {})
    checks["acceptance_tests_pass"] = (
        tests.get("status") == "PASS"
        and tests.get("failures") == 0
        and tests.get("errors") == 0
        and tests.get("skipped") == 0
    )
    checks["m0_regression_32_of_32"] = phase_totals.get("M0_REGRESSION") == {
        "failed": 0,
        "passed": 32,
        "skipped": 0,
        "tests_run": 32,
    }
    checks["m1_acceptance_all_pass"] = phase_totals.get("M1") == {
        "failed": 0,
        "passed": 29,
        "skipped": 0,
        "tests_run": 29,
    }
    checks["runtime_preflight_pass"] = _read("runtime-preflight.json").get("status") == "PASS"
    checks["execution_qualification_pass"] = _read("execution-qualification.json").get("status") == "PASS"
    checks["qualification_matrix_pass"] = _read("qualification-matrix.json").get("status") == "PASS"
    checks["native_funding_pass"] = _read("native-funding-g09.json").get("status") == "PASS"
    checks["native_spot_contract_pass"] = _read("native-spot-cash-g07.json").get("status") == "PASS"
    checks["native_mark_contract_pass"] = _read("native-mark-valuation-g11.json").get("status") == "PASS"

    summaries = _read("run-summaries.json").get("runs", [])
    expected_states = {
        "m1-evidence-g02-causal": ("COMPLETED", "CHECK_PASS", []),
        "m1-evidence-g03-negative": (
            "FAILED",
            "CHECK_FAIL",
            ["SAME_BAR_EXECUTION_DETECTED"],
        ),
        "m1-evidence-g06-replay": ("COMPLETED", "CHECK_PASS", []),
        "m1-evidence-g07-guard": (
            "BLOCKED",
            "CHECK_BLOCKED",
            ["SPOT_SHORT_OR_BORROW_DETECTED"],
        ),
        "m1-evidence-g08-lifecycle": ("COMPLETED", "CHECK_PASS", []),
        "m1-evidence-g09-funding": ("COMPLETED", "CHECK_PASS", []),
    }
    summary_by_id = {item["run_id"]: item for item in summaries}
    checks["representative_run_states"] = set(summary_by_id) == set(expected_states) and all(
        (
            summary_by_id[run_id]["state"],
            summary_by_id[run_id]["checker_outcome"],
            summary_by_id[run_id]["failure_codes"],
        )
        == expected
        for run_id, expected in expected_states.items()
    )
    inventory_ok = True
    for item in summaries:
        run_dir = ROOT / str(item["evidence_directory"])
        if not run_dir.is_dir():
            inventory_ok = False
            continue
        for entry in item["evidence_inventory"]:
            path = run_dir / entry["path"]
            if not path.is_file() or sha256_file(path) != entry["sha256"]:
                inventory_ok = False
    checks["run_evidence_inventories_match"] = inventory_ok

    checks["root_ssot_identity"] = sha256_file(ROOT / "SSOT.md") == SSOT_SHA256
    checks["root_runtime_lock_identity"] = sha256_file(ROOT / "runtime.lock.json") == RUNTIME_LOCK_SHA256
    checks["root_dependency_lock_identity"] = sha256_file(ROOT / "requirements.lock.txt") == DEPENDENCY_LOCK_SHA256
    tracked_identity_diff = _git(
        "diff",
        "--name-only",
        "HEAD",
        "--",
        "SSOT.md",
        "runtime.lock.json",
        "requirements.lock.txt",
        "evidence/m0",
        "evidence/m1/README.md",
        "evidence/m1/failed-attempts.jsonl",
        "evidence/m1/m1-blocker.json",
        "evidence/m1/native-funding-capability.json",
        "evidence/m1/qualification-inventory.json",
        "evidence/m1/targeted-g09-native-funding.txt",
        "evidence/m1/v2-migration-gate",
    )
    checks["adopted_contract_and_historical_evidence_unchanged"] = not tracked_identity_diff
    changed = _git("status", "--porcelain=v1").splitlines()
    m2_prefixes = (
        "src/crypto_lab/data.py",
        "data/raw/",
        "data/releases/",
        "data/catalog/",
    )
    checks["no_m2_paths_changed"] = not any(
        line[3:].startswith(m2_prefixes) for line in changed if len(line) >= 4
    )
    checks["no_official_or_real_data_run"] = (
        _read("qualification-matrix.json").get("official_run_executed") is False
        and _read("qualification-matrix.json").get("real_market_data_acquired") is False
    )
    return {
        "schema": "m1-evidence-validation-v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate()
    payload = canonical_json_bytes(result) + b"\n"
    if args.output is not None:
        args.output.write_bytes(payload)
    print(payload.decode("utf-8"), end="")
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
