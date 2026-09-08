#!/usr/bin/env python3
"""Run M0-M3 suites with unique test reconciliation and non-test gates."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crypto_lab.config import RuntimeLock
from crypto_lab.data import DatasetRelease
from crypto_lab.hashing import sha256_file
from crypto_lab.m3 import PERPETUAL_QUALIFICATION_RELEASE_ID
from crypto_lab.m3 import SPOT_QUALIFICATION_RELEASE_ID
from crypto_lab.runtime import verify_runtime_lock
from scripts.run_m2_acceptance import M0_MODULES
from scripts.run_m2_acceptance import M1_MODULES
from scripts.run_m2_acceptance import M2_MODULES
from scripts.run_m2_acceptance import reconcile_test_executions
from scripts.run_m2_acceptance import test_ids
from scripts.validate_m3_evidence import validate as validate_m3_evidence


EVIDENCE = Path(os.environ.get("M3_ACCEPTANCE_DIR", ROOT / "evidence/m3/m3-acceptance-001"))
M3_MODULES = (
    "tests.unit.test_m3_contracts",
    "tests.golden.test_m3_profile_contracts",
    "tests.golden.test_m3_real_values",
    "tests.integration.test_m3_dataset_interface",
    "tests.integration.test_m3_m4_downstream_contract",
    "tests.qualification.test_m3_real_profiles",
)


def _run_phase(phase: str, modules: tuple[str, ...]):
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    ids = test_ids(suite)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    failures = {test.id() for test, _ in result.failures}
    errors = {test.id() for test, _ in result.errors}
    skipped = {test.id() for test, _ in result.skipped}
    rows = [
        {
            "phase": phase,
            "test_id": test_id,
            "status": (
                "FAIL" if test_id in failures else
                "ERROR" if test_id in errors else
                "SKIPPED" if test_id in skipped else "PASS"
            ),
        }
        for test_id in ids
    ]
    return rows, result, stream.getvalue()


def _command_check(check_id: str, command: list[str], *, env: dict[str, str] | None = None) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "check_id": check_id,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def non_test_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    try:
        runtime = verify_runtime_lock(
            RuntimeLock.from_json_bytes((ROOT / "runtime.lock.json").read_bytes()),
            dependency_lock_path=ROOT / "requirements.lock.txt",
        )
    except Exception as exc:
        checks.append({"check_id": "RUNTIME_PREFLIGHT", "status": "FAIL", "detail": str(exc)})
    else:
        checks.append({"check_id": "RUNTIME_PREFLIGHT", "status": "PASS", "result": runtime})
    env = {
        **os.environ,
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": "/tmp/nautilus-m3-pyc",
        "PYTHONPATH": str(ROOT / "src"),
    }
    checks.append(_command_check("PIP_CHECK", [str(ROOT / ".venv/bin/python"), "-m", "pip", "check"], env=env))
    compile_targets = [
        str(path)
        for path in sorted((ROOT / "src/crypto_lab").rglob("*.py"))
    ] + [
        str(ROOT / "scripts/run_m3_child.py"),
        str(ROOT / "scripts/run_m3_qualifications.py"),
        str(ROOT / "scripts/validate_m3_evidence.py"),
        str(ROOT / "scripts/run_m3_acceptance.py"),
    ]
    checks.append(
        _command_check(
            "PYTHON_COMPILATION",
            [str(ROOT / ".venv/bin/python"), "-m", "py_compile", *compile_targets],
            env=env,
        ),
    )
    checks.append(_command_check("GIT_DIFF_CHECK", ["git", "diff", "--check"]))
    evidence_result = validate_m3_evidence(EVIDENCE)
    checks.append(
        {
            "check_id": "M3_EVIDENCE_VALIDATION",
            "status": evidence_result["status"],
            "result": evidence_result,
        },
    )
    replay = json.loads((EVIDENCE / "deterministic-replay.json").read_text(encoding="utf-8"))
    checks.append(
        {
            "check_id": "DETERMINISTIC_REPLAY",
            "status": "PASS" if all(item["result"] == "PASS" for item in replay.values()) else "FAIL",
            "result": replay,
        },
    )
    controls = json.loads((EVIDENCE / "negative-controls.json").read_text(encoding="utf-8"))
    checks.append(
        {
            "check_id": "CHECKER_TAMPER_CONTROL",
            "status": (
                "PASS"
                if controls["DUPLICATE_FUNDING_SETTLEMENT"]["checker"]["outcome"] == "CHECK_FAIL"
                else "FAIL"
            ),
        },
    )
    release_checks: dict[str, bool] = {}
    for profile, release_id in (
        ("spot", SPOT_QUALIFICATION_RELEASE_ID),
        ("perpetual", PERPETUAL_QUALIFICATION_RELEASE_ID),
    ):
        release = DatasetRelease.from_json_bytes(
            (ROOT / "data/releases" / f"{release_id}.json").read_bytes(),
        )
        resolved = release.resolve_runtime_data(ROOT / "data")
        release_checks[profile] = (
            release.dataset_release_id == release_id
            and __import__("crypto_lab.hashing", fromlist=["canonical_sha256"]).canonical_sha256(
                resolved.semantic_inventory,
            ) == release.catalog_identity
        )
    checks.append(
        {
            "check_id": "DATASET_RELEASE_AND_CATALOG_VALIDATION",
            "status": "PASS" if all(release_checks.values()) else "FAIL",
            "profiles": release_checks,
        },
    )
    checks.append(
        {
            "check_id": "SSOT_RUNTIME_LOCK_INTEGRITY",
            "status": (
                "PASS"
                if sha256_file(ROOT / "SSOT.md") == "7bb2fc68d9b73b168a582d890a6f952fd0c4eb20fc0e31857903909f27dfaa8f"
                and sha256_file(ROOT / "runtime.lock.json") == "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
                and sha256_file(ROOT / "requirements.lock.txt") == "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"
                else "FAIL"
            ),
        },
    )
    return checks


def main() -> int:
    phases = [
        ("M0_REGRESSION", *_run_phase("M0_REGRESSION", M0_MODULES)),
        ("M1_REGRESSION", *_run_phase("M1_REGRESSION", M1_MODULES)),
        ("M2_REGRESSION", *_run_phase("M2_REGRESSION", M2_MODULES)),
        ("M3", *_run_phase("M3", M3_MODULES)),
    ]
    rows = [row for _, phase_rows, _, _ in phases for row in phase_rows]
    reconciliation = reconcile_test_executions(rows)
    unique = reconciliation["unique_tests"]
    failures = sum(len(result.failures) for _, _, result, _ in phases)
    errors = sum(len(result.errors) for _, _, result, _ in phases)
    skipped = sum(len(result.skipped) for _, _, result, _ in phases)
    checks = non_test_checks()
    disabled_markers = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        for marker in ("@unittest.skip", "pytest.mark.skip", "pytest.mark.xfail", "@pytest.mark.xfail"):
            if marker in text:
                disabled_markers.append({"path": str(path.relative_to(ROOT)), "marker": marker})
    checks.append(
        {
            "check_id": "NO_DISABLED_OR_XFAIL_TESTS",
            "status": "PASS" if not disabled_markers else "FAIL",
            "matches": disabled_markers,
        },
    )
    status = "PASS" if failures == errors == skipped == 0 and all(
        item["status"] == "PASS" for item in checks
    ) else "FAIL"
    result = {
        "schema": "m3-combined-acceptance-v1",
        "status": status,
        "baseline_unique_test_cases": 106,
        "unique_executable_test_cases": reconciliation["unique_test_cases"],
        "test_execution_occurrences": reconciliation["test_execution_occurrences"],
        "repeated_execution_count": reconciliation["repeated_execution_count"],
        "repeated_executions": reconciliation["repeated_executions"],
        "unique_tests": unique,
        "execution_occurrences": rows,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "additional_non_test_acceptance_check_count": len(checks),
        "additional_non_test_acceptance_checks": checks,
        "phase_execution_occurrences": {
            phase: len(phase_rows) for phase, phase_rows, _, _ in phases
        },
        "m3_unique_test_cases": sum(item["canonical_owner_phase"] == "M3" for item in unique),
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    result_path = EVIDENCE / "test-results.json"
    output_path = EVIDENCE / "test-output.txt"
    if result_path.exists():
        prior = json.loads(result_path.read_text(encoding="utf-8"))
        if prior.get("status") == "PASS":
            raise FileExistsError("refusing to overwrite accepted M3 test evidence")
        suffix = sha256_file(result_path)[:12]
        result_path.rename(EVIDENCE / f"test-results-failed-{suffix}.json")
        if output_path.exists():
            output_path.rename(EVIDENCE / f"test-output-failed-{suffix}.txt")
    output_path.write_text(
        "\n".join(f"{phase}\n\n{text.rstrip()}" for phase, _, _, text in phases) + "\n",
        encoding="utf-8",
    )
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in (
        "status", "unique_executable_test_cases", "test_execution_occurrences",
        "additional_non_test_acceptance_check_count", "failures", "errors", "skipped",
    )}, sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
