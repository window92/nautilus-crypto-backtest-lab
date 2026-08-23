#!/usr/bin/env python3
"""Run the complete offline acceptance for DATA_PROVENANCE_DUCKDB_REPAIR_001."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/repair/data-provenance-duckdb-001"
EXPECTED = {
    "SSOT.md": "f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354",
    "runtime.lock.json": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "requirements.lock.txt": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
}
TARGET_MODULES = (
    "tests.unit.test_data_provenance_repair",
    "tests.unit.test_data_provenance_duckdb_tool",
    "tests.qualification.test_data_provenance_sparse_market",
)


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise FileExistsError(f"refusing to replace acceptance evidence: {path}")
        return
    path.write_bytes(payload)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def flatten(suite: unittest.TestSuite) -> list[unittest.TestCase]:
    result: list[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(flatten(item))
        else:
            result.append(item)
    return result


def discover() -> unittest.TestSuite:
    return unittest.defaultTestLoader.discover(
        str(ROOT / "tests"),
        pattern="test*.py",
        top_level_dir=str(ROOT),
    )


def run_suite(label: str, suite: unittest.TestSuite) -> tuple[dict[str, Any], str]:
    expected_ids = [item.id() for item in flatten(suite)]
    stream = io.StringIO()
    started = time.monotonic()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    duration = time.monotonic() - started
    material = {
        "label": label,
        "status": "PASS" if result.wasSuccessful() and not result.skipped else "FAIL",
        "discovered_test_count": len(expected_ids),
        "unique_test_count": len(set(expected_ids)),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "duration_seconds": round(duration, 6),
    }
    return material, f"===== {label} =====\n{stream.getvalue()}"


def run_command(label: str, command: list[str]) -> tuple[dict[str, Any], str]:
    environment = {
        **os.environ,
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": "/tmp/data-provenance-acceptance-pyc",
        "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}",
    }
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    duration = time.monotonic() - started
    output = completed.stdout + completed.stderr
    return (
        {
            "label": label,
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "command": command,
            "exit_code": completed.returncode,
            "duration_seconds": round(duration, 6),
        },
        f"===== {label} =====\n$ {' '.join(command)}\n{output}",
    )


def verify_offline_reinstall() -> tuple[dict[str, Any], str]:
    commands: list[list[str]] = []
    outputs: list[str] = []
    return_codes: list[int] = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="duckdb-offline-reinstall-", dir="/tmp") as temporary:
        python = Path(temporary) / "bin/python"
        commands = [
            [sys.executable, "-m", "venv", temporary],
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--find-links",
                str(ROOT / ".data-wheelhouse"),
                "--require-hashes",
                "-r",
                str(ROOT / "requirements.data.lock.txt"),
            ],
            [str(python), "-c", "import duckdb; assert duckdb.__version__ == '1.4.5'"],
            [str(python), "-m", "pip", "check"],
        ]
        for command in commands:
            completed = subprocess.run(
                command,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            return_codes.append(completed.returncode)
            outputs.append(f"$ {' '.join(command)}\n{completed.stdout}{completed.stderr}")
            if completed.returncode != 0:
                break
    result = {
        "label": "DATA_TOOL_OFFLINE_REINSTALL",
        "status": "PASS" if return_codes and all(code == 0 for code in return_codes) else "FAIL",
        "commands": commands,
        "return_codes": return_codes,
        "duration_seconds": round(time.monotonic() - started, 6),
        "network_used": False,
    }
    return result, "===== DATA_TOOL_OFFLINE_REINSTALL =====\n" + "\n".join(outputs)


def main() -> int:
    os.environ["TZ"] = "UTC"
    if hasattr(time, "tzset"):
        time.tzset()
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    phases: list[dict[str, Any]] = []
    outputs: list[str] = []

    first_discovery = [item.id() for item in flatten(discover())]
    second_discovery = [item.id() for item in flatten(discover())]
    independent = {
        "label": "INDEPENDENT_DISCOVERY",
        "status": "PASS"
        if first_discovery == second_discovery
        and len(first_discovery) == len(set(first_discovery))
        else "FAIL",
        "first_count": len(first_discovery),
        "second_count": len(second_discovery),
        "identical_order_and_identity": first_discovery == second_discovery,
        "duplicates": len(first_discovery) - len(set(first_discovery)),
    }
    phases.append(independent)
    outputs.append(f"===== INDEPENDENT_DISCOVERY =====\n{json.dumps(independent, sort_keys=True)}\n")

    result, output = run_suite("FULL_DISCOVERY", discover())
    phases.append(result)
    outputs.append(output)

    reverse_cases = list(reversed(flatten(discover())))
    result, output = run_suite("REVERSE_DETERMINISTIC_ORDER", unittest.TestSuite(reverse_cases))
    phases.append(result)
    outputs.append(output)

    target_suite = unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromName(module) for module in TARGET_MODULES
    )
    result, output = run_suite("REPAIRED_ADVERSARIAL_AND_QUALIFICATION", target_suite)
    phases.append(result)
    outputs.append(output)

    commands = (
        (
            "RUNTIME_PREFLIGHT",
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "unittest",
                "-v",
                "tests.qualification.test_runtime_identity",
            ],
        ),
        (
            "PROJECT_PIP_CHECK",
            [str(ROOT / ".venv/bin/python"), "-m", "pip", "check"],
        ),
        (
            "DATA_TOOL_PIP_CHECK",
            [str(ROOT / ".data-venv/bin/python"), "-m", "pip", "check"],
        ),
        (
            "COMPILEALL",
            [str(ROOT / ".venv/bin/python"), "-m", "compileall", "-q", "src", "scripts", "tests"],
        ),
        ("GIT_WORKTREE_DIFF_CHECK", ["git", "diff", "--check"]),
        ("GIT_INDEX_DIFF_CHECK", ["git", "diff", "--cached", "--check"]),
        (
            "EVIDENCE_VALIDATOR_PRE_FINAL",
            [
                str(ROOT / ".data-venv/bin/python"),
                "scripts/validate_data_provenance_evidence.py",
                "--pre-final",
            ],
        ),
    )
    for label, command in commands:
        result, output = run_command(label, command)
        phases.append(result)
        outputs.append(output)

    result, output = verify_offline_reinstall()
    phases.append(result)
    outputs.append(output)

    lock_checks = {name: digest(ROOT / name) == expected for name, expected in EXPECTED.items()}
    identity = {
        "label": "LOCK_IDENTITIES",
        "status": "PASS" if all(lock_checks.values()) else "FAIL",
        "checks": lock_checks,
        "actual": {name: digest(ROOT / name) for name in EXPECTED},
    }
    phases.append(identity)
    outputs.append(f"===== LOCK_IDENTITIES =====\n{json.dumps(identity, sort_keys=True)}\n")

    disabled_pattern = re.compile(
        r"@unittest\.skip|pytest\.mark\.(?:skip|xfail)|@pytest\.mark\.(?:skip|xfail)|\bxfail\b|disabled\s*=",
    )
    disabled_hits = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if disabled_pattern.search(line):
                disabled_hits.append(f"{path.relative_to(ROOT)}:{number}:{line.strip()}")
    disabled = {
        "label": "NO_DISABLED_SKIP_XFAIL",
        "status": "PASS" if not disabled_hits else "FAIL",
        "hits": disabled_hits,
    }
    phases.append(disabled)
    outputs.append(f"===== NO_DISABLED_SKIP_XFAIL =====\n{json.dumps(disabled, sort_keys=True)}\n")

    historic = json.loads((EVIDENCE / "historical-integrity.json").read_text(encoding="utf-8"))
    raw = json.loads((EVIDENCE / "raw-object-integrity.json").read_text(encoding="utf-8"))
    integrity = {
        "label": "HISTORICAL_AND_RAW_INTEGRITY",
        "status": "PASS" if historic["status"] == raw["status"] == "PASS" else "FAIL",
        "historical_evidence_status": historic["status"],
        "raw_object_status": raw["status"],
        "raw_object_count": raw["object_count"],
    }
    phases.append(integrity)
    outputs.append(
        f"===== HISTORICAL_AND_RAW_INTEGRITY =====\n{json.dumps(integrity, sort_keys=True)}\n",
    )

    status = "PASS" if all(item["status"] == "PASS" for item in phases) else "FAIL"
    completed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    summary = {
        "schema": "data-provenance-acceptance-results-v1",
        "status": status,
        "started_at_utc": started_at,
        "completed_at_utc": completed_at,
        "environment": {
            "timezone": "UTC",
            "locale": "C.UTF-8",
            "project_python": str(ROOT / ".venv/bin/python"),
            "data_tool_python": str(ROOT / ".data-venv/bin/python"),
            "network_used": False,
        },
        "phases": phases,
        "strategy_or_official_trial_started": False,
    }
    write_once(EVIDENCE / "test-output.txt", "\n".join(outputs).encode("utf-8"))
    write_once(EVIDENCE / "test-results.json", canonical_bytes(summary))
    print(json.dumps({"status": status, "phase_count": len(phases)}, sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
