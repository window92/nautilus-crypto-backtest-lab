#!/usr/bin/env python3
"""Run every discovered unittest once in deterministic reverse-ID order."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _ids(suite: unittest.TestSuite) -> list[str]:
    result: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(_ids(item))
        else:
            result.append(item.id())
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    os.environ.update(
        {
            "TZ": "UTC",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    time.tzset()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name in ("result.json", "output.txt"):
        if (output / name).exists():
            raise FileExistsError(f"refusing to overwrite reverse-order evidence: {output / name}")

    loader = unittest.defaultTestLoader
    discovered = _ids(loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT)))
    ordered = tuple(sorted(discovered, reverse=True))
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        loader.loadTestsFromNames(ordered),
    )
    failures = {test.id() for test, _traceback in result.failures}
    errors = {test.id() for test, _traceback in result.errors}
    skipped = {test.id() for test, _reason in result.skipped}
    rows = [
        {
            "ordinal": index,
            "test_id": test_id,
            "status": (
                "FAIL"
                if test_id in failures
                else "ERROR"
                if test_id in errors
                else "SKIPPED"
                if test_id in skipped
                else "PASS"
            ),
        }
        for index, test_id in enumerate(ordered, start=1)
    ]
    passed = bool(
        result.wasSuccessful()
        and not result.skipped
        and len(discovered) == len(set(discovered)) == result.testsRun
    )
    payload = {
        "schema": "v1-repair-reverse-unittest-order-v1",
        "status": "PASS" if passed else "FAIL",
        "order": "FULL_UNITTEST_ID_DESCENDING",
        "unique_discovered_test_cases": len(set(discovered)),
        "execution_occurrences": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "tests": rows,
    }
    (output / "output.txt").write_text(stream.getvalue(), encoding="utf-8")
    (output / "result.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {key: payload[key] for key in (
                "status",
                "unique_discovered_test_cases",
                "execution_occurrences",
                "failures",
                "errors",
                "skipped",
            )},
            sort_keys=True,
        ),
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
