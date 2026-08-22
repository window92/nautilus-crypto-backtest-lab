#!/usr/bin/env python3
"""Run M0-M4 regressions with unique-ID reconciliation and V1 gates."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crypto_lab.config import RuntimeLock
from crypto_lab.hashing import sha256_file
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.runtime import verify_runtime_lock
from scripts.run_m2_acceptance import M0_MODULES
from scripts.run_m2_acceptance import M1_MODULES
from scripts.run_m2_acceptance import M2_MODULES
from scripts.run_m2_acceptance import reconcile_test_executions
from scripts.run_m2_acceptance import test_ids
from scripts.run_m3_acceptance import M3_MODULES
from scripts.validate_m3_evidence import validate as validate_m3_evidence


M3_EVIDENCE = ROOT / "evidence/m3/m3-acceptance-001"
M4_EVIDENCE = ROOT / "evidence/m4/m4-acceptance-001"
M4_MODULES = (
    "tests.unit.test_m4_protocol",
    "tests.unit.test_m4_journal_holdout",
    "tests.golden.test_m4_monte_carlo_diagnostics",
    "tests.golden.test_m4_claim_reporting",
    "tests.integration.test_m4_public_contract",
)
REPAIR_MODULES = (
    "tests.adversarial.test_aud001_strategy_identity",
    "tests.adversarial.test_aud002_m3_holdout_exposure",
    "tests.adversarial.test_aud003_004_authoritative_history",
    "tests.adversarial.test_aud005_claim_report_resolver",
    "tests.adversarial.test_aud006_source_revision",
    "tests.adversarial.test_aud007_evidence_paths",
    "tests.adversarial.test_aud008_offline_enforcement",
    "tests.adversarial.test_aud009_owner_workflow",
)
EXPECTED_LOCKS = {
    "SSOT.md": "7bb2fc68d9b73b168a582d890a6f952fd0c4eb20fc0e31857903909f27dfaa8f",
    "runtime.lock.json": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "requirements.lock.txt": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
}
EXPECTED_M3_REGISTRY = "d6124dd7d225818f0de212d74f7d4aae5e3bf08c9f8ff342435baac6228ba6de"


def _environment() -> dict[str, str]:
    return {
        **os.environ,
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": "/tmp/nautilus-m4-pyc",
        "PYTHONPATH": str(ROOT / "src"),
        "PYTEST_ADDOPTS": "-p no:cacheprovider",
    }


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


def _command_check(check_id: str, command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=_environment(),
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


def _independent_discovery(expected_ids: set[str]) -> tuple[dict[str, Any], str]:
    discovered = set(
        test_ids(unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))),
    )
    command = [
        str(ROOT / ".venv/bin/python"),
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-t",
        ".",
        "-v",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    output = completed.stdout + completed.stderr
    match = re.search(r"Ran (\d+) tests?", output)
    count = int(match.group(1)) if match else -1
    missing = sorted(expected_ids - discovered)
    extra = sorted(discovered - expected_ids)
    result = {
        "schema": "m4-independent-discovery-v1",
        "status": (
            "PASS"
            if completed.returncode == 0 and count == len(discovered) and not missing and not extra
            else "FAIL"
        ),
        "command": command,
        "returncode": completed.returncode,
        "unique_executable_test_cases": len(discovered),
        "executed_test_cases": count,
        "missing_from_discovery": missing,
        "unexpected_in_discovery": extra,
        "output_sha256": __import__("hashlib").sha256(output.encode()).hexdigest(),
    }
    return result, output


def _non_test_checks(*, require_final_evidence: bool) -> list[dict[str, Any]]:
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
    checks.append(
        _command_check(
            "PIP_CHECK",
            [str(ROOT / ".venv/bin/python"), "-m", "pip", "check"],
        ),
    )
    compile_targets = [str(path) for path in sorted((ROOT / "src/crypto_lab").rglob("*.py"))]
    compile_targets.extend(str(path) for path in sorted((ROOT / "scripts").glob("*.py")))
    checks.append(
        _command_check(
            "PYTHON_COMPILATION",
            [str(ROOT / ".venv/bin/python"), "-m", "py_compile", *compile_targets],
        ),
    )
    checks.append(_command_check("GIT_DIFF_CHECK", ["git", "diff", "--check"]))
    m3_validation = validate_m3_evidence(M3_EVIDENCE)
    checks.append(
        {
            "check_id": "M3_EVIDENCE_VALIDATION",
            "status": m3_validation["status"],
            "result": m3_validation,
        },
    )
    registry = QualifiedProfileRegistry.from_json_bytes(
        (M3_EVIDENCE / "qualified-profile-registry.json").read_bytes(),
    )
    checks.append(
        {
            "check_id": "M3_QUALIFIED_PROFILE_REGISTRY",
            "status": (
                "PASS"
                if registry.registry_content_sha256 == EXPECTED_M3_REGISTRY
                and all(item.qualification_state.value == "QUALIFIED" for item in registry.records)
                else "FAIL"
            ),
            "registry_content_sha256": registry.registry_content_sha256,
        },
    )
    checks.append(
        {
            "check_id": "SSOT_RUNTIME_DEPENDENCY_LOCK_INTEGRITY",
            "status": (
                "PASS"
                if all(sha256_file(ROOT / name) == identity for name, identity in EXPECTED_LOCKS.items())
                else "FAIL"
            ),
            "observed": {name: sha256_file(ROOT / name) for name in EXPECTED_LOCKS},
        },
    )
    historical_diff = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--",
            "evidence/m0",
            "evidence/m1",
            "evidence/m2",
            "evidence/m3",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    checks.append(
        {
            "check_id": "HISTORICAL_EVIDENCE_UNMODIFIED",
            "status": "PASS" if not historical_diff else "FAIL",
            "changed_paths": historical_diff,
        },
    )
    disabled: list[dict[str, str]] = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        for marker in (
            "@unittest.skip",
            "pytest.mark.skip",
            "pytest.mark.xfail",
            "@pytest.mark.xfail",
        ):
            if marker in source:
                disabled.append({"path": str(path.relative_to(ROOT)), "marker": marker})
    checks.append(
        {
            "check_id": "NO_DISABLED_SKIP_OR_XFAIL_TESTS",
            "status": "PASS" if not disabled else "FAIL",
            "matches": disabled,
        },
    )
    if require_final_evidence:
        from scripts.validate_m4_evidence import validate as validate_m4_evidence

        result = validate_m4_evidence(M4_EVIDENCE)
        checks.append(
            {
                "check_id": "M4_FINAL_EVIDENCE_VALIDATION",
                "status": result["status"],
                "result": result,
            },
        )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--require-final-evidence", action="store_true")
    args = parser.parse_args()
    os.environ["TZ"] = "UTC"
    os.environ["LANG"] = "C.UTF-8"
    os.environ["LC_ALL"] = "C.UTF-8"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    time.tzset()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("test-results.json", "test-output.txt", "independent-discovery.json"):
        if (output_dir / name).exists():
            raise FileExistsError(f"refusing to overwrite acceptance output: {output_dir / name}")

    phases = [
        ("M0_REGRESSION", *_run_phase("M0_REGRESSION", M0_MODULES)),
        ("M1_REGRESSION", *_run_phase("M1_REGRESSION", M1_MODULES)),
        ("M2_REGRESSION", *_run_phase("M2_REGRESSION", M2_MODULES)),
        ("M3_REGRESSION", *_run_phase("M3_REGRESSION", M3_MODULES)),
        ("M4", *_run_phase("M4", M4_MODULES)),
        ("REPAIR_ADVERSARIAL", *_run_phase("REPAIR_ADVERSARIAL", REPAIR_MODULES)),
    ]
    rows = [row for _, phase_rows, _, _ in phases for row in phase_rows]
    reconciliation = reconcile_test_executions(rows)
    unique = reconciliation["unique_tests"]
    expected_ids = {item["test_id"] for item in unique}
    discovery, discovery_output = _independent_discovery(expected_ids)
    checks = _non_test_checks(require_final_evidence=args.require_final_evidence)
    checks.append(
        {
            "check_id": "INDEPENDENT_FULL_DISCOVERY",
            "status": discovery["status"],
            "result": discovery,
        },
    )
    failures = sum(len(result.failures) for _, _, result, _ in phases)
    errors = sum(len(result.errors) for _, _, result, _ in phases)
    skipped = sum(len(result.skipped) for _, _, result, _ in phases)
    status = (
        "PASS"
        if failures == errors == skipped == 0
        and all(item["status"] == "PASS" for item in checks)
        else "FAIL"
    )
    result = {
        "schema": "m4-combined-acceptance-v1",
        "status": status,
        "completed_m0_m3_baseline_unique_test_cases": 127,
        "unique_executable_test_cases": reconciliation["unique_test_cases"],
        "test_execution_occurrences": reconciliation["test_execution_occurrences"],
        "independent_discovery_execution_occurrences": discovery["executed_test_cases"],
        "repeated_execution_count": reconciliation["repeated_execution_count"],
        "repeated_executions": reconciliation["repeated_executions"],
        "unique_tests": unique,
        "execution_occurrences": rows,
        "phase_execution_occurrences": {
            phase: len(phase_rows) for phase, phase_rows, _, _ in phases
        },
        "repair_adversarial_unique_test_cases": sum(
            item["canonical_owner_phase"] == "REPAIR_ADVERSARIAL"
            for item in unique
        ),
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "additional_non_test_acceptance_check_count": len(checks),
        "additional_non_test_acceptance_checks": checks,
    }
    (output_dir / "test-output.txt").write_text(
        "\n".join(f"{phase}\n\n{text.rstrip()}" for phase, _, _, text in phases) + "\n",
        encoding="utf-8",
    )
    (output_dir / "test-results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "independent-discovery-output.txt").write_text(
        discovery_output,
        encoding="utf-8",
    )
    (output_dir / "independent-discovery.json").write_text(
        json.dumps(discovery, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: result[key] for key in (
        "status",
        "unique_executable_test_cases",
        "test_execution_occurrences",
        "independent_discovery_execution_occurrences",
        "additional_non_test_acceptance_check_count",
        "failures",
        "errors",
        "skipped",
    )}, sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
