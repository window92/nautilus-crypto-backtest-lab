#!/usr/bin/env python3
"""Run fail-closed acceptance for OWNER_STRATEGY_RESEARCH_001."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT_PYTHON = ROOT / ".venv/bin/python"
DATA_PYTHON = ROOT / ".data-venv/bin/python"
EXPECTED_LOCKS = {
    "SSOT.md": "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99",
    "runtime.lock.json": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "requirements.lock.txt": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
}
TARGETED_MODULES = (
    "tests.unit.test_owner_strategy_research_001",
    "tests.integration.test_weekly_tsmom_strategy",
    "tests.unit.test_native_research_metrics_readiness",
    "tests.adversarial.test_aud001_strategy_identity",
    "tests.adversarial.test_aud005_claim_report_resolver",
    "tests.adversarial.test_aud008_offline_enforcement",
    "tests.adversarial.test_aud009_owner_workflow",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def environment() -> dict[str, str]:
    return {
        **os.environ,
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": "/tmp/owner-strategy-research-001-pyc",
        "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}",
    }


def command(label: str, *arguments: object) -> dict[str, Any]:
    argv = [str(item) for item in arguments]
    completed = subprocess.run(
        argv,
        cwd=ROOT,
        env=environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "label": label,
        "command": argv,
        "returncode": completed.returncode,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def suite_ids(suite: unittest.TestSuite) -> list[str]:
    values: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            values.extend(suite_ids(item))
        else:
            values.append(item.id())
    return values


def unittest_result(result: dict[str, Any], *, expected_count: int) -> dict[str, Any]:
    output = result["stdout"] + result["stderr"]
    matches = re.findall(r"Ran ([0-9]+) tests? in ", output)
    count = int(matches[-1]) if matches else 0
    skipped = sum(int(item) for item in re.findall(r"skipped=([0-9]+)", output))
    expected_failures = sum(
        int(item) for item in re.findall(r"expected failures=([0-9]+)", output)
    )
    unexpected_successes = sum(
        int(item) for item in re.findall(r"unexpected successes=([0-9]+)", output)
    )
    passed = (
        result["returncode"] == 0
        and count == expected_count
        and skipped == expected_failures == unexpected_successes == 0
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "execution_occurrences": count,
        "expected_execution_occurrences": expected_count,
        "failures": 0 if result["returncode"] == 0 else None,
        "errors": 0 if result["returncode"] == 0 else None,
        "skips": skipped,
        "xfail": expected_failures,
        "unexpected_successes": unexpected_successes,
        "returncode": result["returncode"],
    }


def disabled_test_scan() -> dict[str, Any]:
    prohibited = {"skip", "skipIf", "skipUnless", "expectedFailure", "xfail"}
    hits: list[str] = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if ast.unparse(target).rsplit(".", 1)[-1] in prohibited:
                    hits.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        if any(
            marker in source
            for marker in ("pytest.mark.skip", "pytest.mark.xfail", "__unittest_skip__")
        ):
            hits.append(f"{path.relative_to(ROOT)}:TEXT_MARKER")
    return {
        "status": "PASS" if not hits else "FAIL",
        "disabled_skip_or_xfail_count": len(hits),
        "hits": hits,
    }


def runtime_preflight() -> dict[str, Any]:
    from crypto_lab.config import RuntimeLock
    from crypto_lab.runtime import verify_runtime_lock

    try:
        result = verify_runtime_lock(
            RuntimeLock.from_json_bytes((ROOT / "runtime.lock.json").read_bytes()),
            dependency_lock_path=ROOT / "requirements.lock.txt",
        )
    except Exception as exc:
        return {"status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"}
    return {"status": "PASS", "observed": result}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    output = arguments.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"fresh acceptance output required: {output}")
    output.mkdir(parents=True)
    os.environ.update(
        {
            "TZ": "UTC",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONHASHSEED": "0",
        },
    )
    time.tzset()
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    discovered = suite_ids(
        unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT)),
    )
    unique = len(set(discovered))
    discovery = {
        "status": "PASS" if unique == len(discovered) and unique > 0 else "FAIL",
        "unique_test_cases": unique,
        "discovered_occurrences": len(discovered),
        "test_ids": sorted(discovered),
    }
    full = command(
        "FULL_UNIQUE_DISCOVERY",
        PROJECT_PYTHON,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-t",
        ".",
        "-v",
    )
    independent = command(
        "INDEPENDENT_FRESH_DISCOVERY",
        PROJECT_PYTHON,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-t",
        ".",
        "-v",
    )
    reverse_root = output / "reverse"
    reverse_command = command(
        "REVERSE_DETERMINISTIC_ORDER",
        PROJECT_PYTHON,
        ROOT / "scripts/run_reverse_test_order.py",
        "--output-dir",
        reverse_root,
    )
    reverse = (
        json.loads((reverse_root / "result.json").read_text(encoding="utf-8"))
        if (reverse_root / "result.json").is_file()
        else {"status": "FAIL"}
    )
    targeted_ids = suite_ids(unittest.defaultTestLoader.loadTestsFromNames(TARGETED_MODULES))
    targeted = command(
        "OWNER_STRATEGY_RESEARCH_TARGETED",
        PROJECT_PYTHON,
        "-m",
        "unittest",
        "-v",
        *TARGETED_MODULES,
    )
    test_runs = {
        "full": unittest_result(full, expected_count=unique),
        "independent": unittest_result(independent, expected_count=unique),
        "reverse": {
            **reverse,
            "status": (
                "PASS"
                if reverse_command["returncode"] == 0
                and reverse.get("status") == "PASS"
                and reverse.get("unique_discovered_test_cases") == unique
                and reverse.get("execution_occurrences") == unique
                and reverse.get("failures")
                == reverse.get("errors")
                == reverse.get("skipped")
                == 0
                else "FAIL"
            ),
        },
        "targeted": unittest_result(targeted, expected_count=len(targeted_ids)),
    }

    checks = {
        "project_pip_check": command("PROJECT_PIP_CHECK", PROJECT_PYTHON, "-m", "pip", "check"),
        "data_tool_pip_check": command("DATA_TOOL_PIP_CHECK", DATA_PYTHON, "-m", "pip", "check"),
        "compileall": command(
            "COMPILEALL",
            PROJECT_PYTHON,
            "-m",
            "compileall",
            "-q",
            "src",
            "scripts",
            "tests",
        ),
        "git_diff_check": command("GIT_DIFF_CHECK", "git", "diff", "--check"),
        "instrument_repair_evidence": command(
            "INSTRUMENT_REPAIR_EVIDENCE",
            PROJECT_PYTHON,
            ROOT / "scripts/validate_instrument_repair_evidence.py",
        ),
        "replacement_evidence": command(
            "OWNER_SMOKE_REPLACEMENT_EVIDENCE",
            PROJECT_PYTHON,
            ROOT / "scripts/validate_owner_smoke_002_replacement_evidence.py",
        ),
        "native_metrics_evidence": command(
            "NATIVE_METRICS_EVIDENCE",
            PROJECT_PYTHON,
            ROOT / "scripts/validate_native_research_metrics_readiness_evidence.py",
        ),
        "result_evidence": command(
            "RESULT_EVIDENCE",
            PROJECT_PYTHON,
            ROOT / "scripts/generate_owner_strategy_research_001_evidence.py",
            "--validate-only",
        ),
    }
    disabled = disabled_test_scan()
    runtime = runtime_preflight()
    observed_locks = {name: sha256_file(ROOT / name) for name in EXPECTED_LOCKS}
    locks = {
        "status": "PASS" if observed_locks == EXPECTED_LOCKS else "FAIL",
        "expected": EXPECTED_LOCKS,
        "observed": observed_locks,
    }
    gates = {
        "discovery": discovery["status"],
        **{f"tests_{name}": value["status"] for name, value in test_runs.items()},
        "runtime_preflight": runtime["status"],
        "locked_identities": locks["status"],
        "disabled_tests": disabled["status"],
        **{name: value["status"] for name, value in checks.items()},
    }
    status = "PASS" if all(value == "PASS" for value in gates.values()) else "FAIL"
    execution_occurrences = sum(
        int(value.get("execution_occurrences", 0)) for value in test_runs.values()
    )
    result = {
        "schema": "owner-strategy-research-001-acceptance-v1",
        "epoch": "OWNER_STRATEGY_RESEARCH_001",
        "status": status,
        "started_at_utc": started,
        "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "research_purpose": "EXPLORATORY_OPERATIONAL_VALIDATION",
        "research_intent": "EXPLORATORY",
        "final_holdout_used": False,
        "real_profitability_claim": False,
        "optimization_performed": False,
        "failures": 0 if status == "PASS" else None,
        "errors": 0 if status == "PASS" else None,
        "skips": 0 if status == "PASS" else None,
        "xfail": 0 if status == "PASS" else None,
        "unique_tests": unique,
        "test_execution_occurrences": execution_occurrences,
        "discovery": discovery,
        "test_runs": test_runs,
        "runtime_preflight": runtime,
        "lock_integrity": locks,
        "disabled_tests": disabled,
        "commands": {
            name: {key: value[key] for key in ("label", "command", "returncode", "status")}
            for name, value in checks.items()
        },
        "gates": gates,
    }
    (output / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    logs = (
        ("FULL UNIQUE DISCOVERY", full),
        ("INDEPENDENT FRESH DISCOVERY", independent),
        ("REVERSE DETERMINISTIC ORDER", reverse_command),
        ("OWNER STRATEGY RESEARCH TARGETED", targeted),
        *((name.upper(), value) for name, value in checks.items()),
    )
    with (output / "test-output.txt").open("w", encoding="utf-8", newline="\n") as stream:
        for label, item in logs:
            stream.write(f"===== {label} =====\n")
            stream.write(item["stdout"])
            stream.write(item["stderr"])
            if not (item["stdout"] + item["stderr"]).endswith("\n"):
                stream.write("\n")
    print(
        json.dumps(
            {
                "status": status,
                "unique_tests": unique,
                "test_execution_occurrences": execution_occurrences,
                "gates": gates,
            },
            sort_keys=True,
        ),
    )
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
