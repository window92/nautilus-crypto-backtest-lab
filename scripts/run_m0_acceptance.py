#!/usr/bin/env python3
"""Run the complete M0 suite and preserve both passing and failing evidence."""

from __future__ import annotations

import io
import hashlib
import json
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_RELATIVE = os.environ.get("M0_EVIDENCE_RELATIVE", "evidence/m0")
EVIDENCE = ROOT / EVIDENCE_RELATIVE
COMMAND = (
    f"M0_EVIDENCE_RELATIVE={EVIDENCE_RELATIVE} "
    "TZ=UTC LC_ALL=C.UTF-8 PYTHONPATH=src .venv/bin/python "
    "scripts/run_m0_acceptance.py"
)


def _test_ids(suite: unittest.TestSuite) -> list[str]:
    result: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(_test_ids(item))
        else:
            result.append(item.id())
    return result


def main() -> int:
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"),
        top_level_dir=str(ROOT),
    )
    ids = _test_ids(suite)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    output = stream.getvalue()
    failed = {test.id(): traceback for test, traceback in result.failures}
    errors = {test.id(): traceback for test, traceback in result.errors}
    skipped = {test.id(): reason for test, reason in result.skipped}
    tests = []
    for test_id in ids:
        if test_id in failed:
            status = "FAIL"
        elif test_id in errors:
            status = "ERROR"
        elif test_id in skipped:
            status = "SKIPPED"
        else:
            status = "PASS"
        tests.append({"test_id": test_id, "status": status})
    evidence = {
        "command": COMMAND,
        "status": "PASS" if result.wasSuccessful() else "FAIL",
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "tests": tests,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    previous_result_path = EVIDENCE / "test-results.json"
    previous_output_path = EVIDENCE / "test-output.txt"
    if previous_result_path.is_file():
        previous_bytes = previous_result_path.read_bytes()
        previous = json.loads(previous_bytes)
        if previous.get("status") != "PASS":
            suffix = hashlib.sha256(previous_bytes).hexdigest()[:12]
            (EVIDENCE / f"test-results-failed-{suffix}.json").write_bytes(previous_bytes)
            if previous_output_path.is_file():
                (EVIDENCE / f"test-output-failed-{suffix}.txt").write_bytes(
                    previous_output_path.read_bytes(),
                )
    (EVIDENCE / "test-output.txt").write_text(output, encoding="utf-8")
    (EVIDENCE / "test-results.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sys.stdout.write(output)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
