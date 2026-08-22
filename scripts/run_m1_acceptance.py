#!/usr/bin/env python3
"""Run completed-phase M0 regression plus the complete M1 acceptance suite."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EVIDENCE_RELATIVE = os.environ.get(
    "M1_EVIDENCE_RELATIVE",
    "evidence/m1/m1-acceptance-001",
)
EVIDENCE = ROOT / EVIDENCE_RELATIVE
COMMAND = (
    f"M1_EVIDENCE_RELATIVE={EVIDENCE_RELATIVE} "
    "TZ=UTC LC_ALL=C.UTF-8 PYTHONPATH=src .venv/bin/python "
    "scripts/run_m1_acceptance.py"
)

M0_MODULES = (
    "tests.unit.test_config",
    "tests.unit.test_hashing",
    "tests.unit.test_runtime_lock",
    "tests.unit.test_source_revision",
    "tests.unit.test_status",
    "tests.golden.test_g18_config_hash",
    "tests.golden.test_g20_runtime_lock_mismatch",
    "tests.integration.test_m0_downstream_contract",
    "tests.qualification.test_latency_contract",
    "tests.qualification.test_runtime_identity",
    "tests.qualification.test_m1_native_funding",
)
M1_MODULES = (
    "tests.unit.test_m1_contracts",
    "tests.golden.test_m1_contracts",
    "tests.integration.test_m1_evidence_contract",
    "tests.qualification.test_m1_native_funding",
    "tests.qualification.test_m1_native_profiles",
)


def _test_ids(suite: unittest.TestSuite) -> list[str]:
    result: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(_test_ids(item))
        else:
            result.append(item.id())
    return result


def _run_phase(
    phase: str,
    modules: tuple[str, ...],
) -> tuple[list[dict[str, str]], unittest.TestResult, str]:
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    ids = _test_ids(suite)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    output = stream.getvalue()
    failed = {test.id(): traceback for test, traceback in result.failures}
    errors = {test.id(): traceback for test, traceback in result.errors}
    skipped = {test.id(): reason for test, reason in result.skipped}
    tests: list[dict[str, str]] = []
    for test_id in ids:
        if test_id in failed:
            status = "FAIL"
        elif test_id in errors:
            status = "ERROR"
        elif test_id in skipped:
            status = "SKIPPED"
        else:
            status = "PASS"
        tests.append({"phase": phase, "test_id": test_id, "status": status})
    return tests, result, output


def main() -> int:
    m0_tests, m0_result, m0_output = _run_phase("M0_REGRESSION", M0_MODULES)
    m1_tests, m1_result, m1_output = _run_phase("M1", M1_MODULES)
    tests = [*m0_tests, *m1_tests]
    output = "M0 REGRESSION\n\n" + m0_output + "\nM1 ACCEPTANCE\n\n" + m1_output
    results = (m0_result, m1_result)
    tests_run = sum(result.testsRun for result in results)
    failures = sum(len(result.failures) for result in results)
    errors = sum(len(result.errors) for result in results)
    skipped = sum(len(result.skipped) for result in results)
    successful = all(result.wasSuccessful() for result in results)

    phase_totals = {
        phase: {
            "tests_run": sum(test["phase"] == phase for test in tests),
            "passed": sum(
                test["phase"] == phase and test["status"] == "PASS"
                for test in tests
            ),
            "failed": sum(
                test["phase"] == phase and test["status"] in {"FAIL", "ERROR"}
                for test in tests
            ),
            "skipped": sum(
                test["phase"] == phase and test["status"] == "SKIPPED"
                for test in tests
            ),
        }
        for phase in ("M0_REGRESSION", "M1")
    }
    evidence = {
        "command": COMMAND,
        "status": "PASS" if successful else "FAIL",
        "tests_run": tests_run,
        "passed": tests_run - failures - errors - skipped,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "phase_totals": phase_totals,
        "tests": tests,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    result_path = EVIDENCE / "test-results.json"
    output_path = EVIDENCE / "test-output.txt"
    if result_path.is_file():
        previous_bytes = result_path.read_bytes()
        previous = json.loads(previous_bytes)
        if previous.get("status") != "PASS":
            suffix = hashlib.sha256(previous_bytes).hexdigest()[:12]
            (EVIDENCE / f"test-results-failed-{suffix}.json").write_bytes(previous_bytes)
            if output_path.is_file():
                (EVIDENCE / f"test-output-failed-{suffix}.txt").write_bytes(
                    output_path.read_bytes(),
                )
    output_path.write_text(output, encoding="utf-8")
    result_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sys.stdout.write(output)
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
