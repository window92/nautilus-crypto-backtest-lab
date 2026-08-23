#!/usr/bin/env python3
"""Run the complete offline acceptance set for the free-official data repair."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
PROJECT_PYTHON = ROOT / ".venv/bin/python"
DATA_PYTHON = ROOT / ".data-venv/bin/python"
DB_ROOT = ROOT / "data/duckdb/free-official-binance-data-duckdb-001"
EXPECTED_HASHES = {
    "SSOT.md": "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99",
    "runtime.lock.json": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "requirements.lock.txt": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
    "data-tool.lock.json": "e55480bd46b63fb44133f42bb5486513b0ac3c868c63441935f998b743721a39",
    "requirements.data.lock.txt": "e0b23ac2e51b385e06fcb2e887a2fb13ae8f811b59594d05d13a6693eb691007",
}
OWNER_SMOKE_MODULES = (
    "tests.golden.test_owner_smoke_sma20",
    "tests.integration.test_owner_smoke_daily_strategy",
    "tests.qualification.test_owner_smoke_registered_strategy",
    "tests.qualification.test_data_provenance_sparse_market",
)
DATA_REPAIR_MODULES = (
    "tests.unit.test_data_provenance_repair",
    "tests.unit.test_data_provenance_duckdb_tool",
    "tests.unit.test_free_official_binance_repair",
    "tests.unit.test_m2_raw_and_parsing",
    "tests.unit.test_m2_repair_contracts",
    "tests.unit.test_m2_release_contract",
    "tests.qualification.test_m2_official_samples",
    "tests.qualification.test_data_provenance_sparse_market",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def environment() -> dict[str, str]:
    return {
        **os.environ,
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}:{ROOT / '.venv/lib/python3.12/site-packages'}",
    }


def command(label: str, arguments: Iterable[object]) -> dict[str, Any]:
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
    result: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(suite_ids(item))
        else:
            result.append(item.id())
    return result


def unittest_result(label: str, result: dict[str, Any], expected: int | None = None) -> dict[str, Any]:
    output = result["stdout"] + result["stderr"]
    matches = re.findall(r"Ran ([0-9]+) tests? in ", output)
    count = int(matches[-1]) if matches else 0
    skipped = sum(int(item) for item in re.findall(r"skipped=([0-9]+)", output))
    expected_failures = sum(int(item) for item in re.findall(r"expected failures=([0-9]+)", output))
    unexpected_successes = sum(int(item) for item in re.findall(r"unexpected successes=([0-9]+)", output))
    passed = bool(
        result["returncode"] == 0
        and count > 0
        and (expected is None or count == expected)
        and skipped == expected_failures == unexpected_successes == 0
    )
    return {
        "label": label,
        "status": "PASS" if passed else "FAIL",
        "execution_occurrences": count,
        "expected_execution_occurrences": expected,
        "failures": 0 if result["returncode"] == 0 else None,
        "errors": 0 if result["returncode"] == 0 else None,
        "skips": skipped,
        "xfail": expected_failures,
        "unexpected_successes": unexpected_successes,
        "returncode": result["returncode"],
    }


def disabled_test_scan() -> dict[str, Any]:
    hits: list[str] = []
    prohibited_decorators = {"skip", "skipIf", "skipUnless", "expectedFailure", "xfail"}
    for path in sorted((ROOT / "tests").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    name = ast.unparse(decorator.func if isinstance(decorator, ast.Call) else decorator)
                    if name.rsplit(".", 1)[-1] in prohibited_decorators:
                        hits.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
        if "pytest.mark.skip" in source or "pytest.mark.xfail" in source or "__unittest_skip__" in source:
            hits.append(f"{path.relative_to(ROOT)}:TEXT_MARKER")
    return {
        "status": "PASS" if not hits else "FAIL",
        "disabled_skip_or_xfail_count": len(hits),
        "hits": hits,
    }


def candidate_003_integrity() -> dict[str, Any]:
    root = ROOT / "evidence/repair/binance-origin-archive-recovery-001"
    inventory = json.loads((root / "evidence-inventory.json").read_text(encoding="utf-8"))
    failures = []
    for item in inventory["files"]:
        path = root / item["phase_relative_path"]
        if (
            not path.is_file()
            or path.stat().st_size != int(item["size_bytes"])
            or sha256_file(path) != item["sha256"]
        ):
            failures.append(item["phase_relative_path"])
    return {
        "status": "PASS" if not failures and inventory["status"] == "PASS" else "FAIL",
        "expected_file_count": inventory["file_count_excluding_inventory"],
        "validated_file_count": len(inventory["files"]),
        "failure_count": len(failures),
        "failures": failures,
        "inventory_sha256": sha256_file(root / "evidence-inventory.json"),
    }


def tracked_historical_integrity() -> dict[str, Any]:
    listed = command("TRACKED_EVIDENCE_LIST", ["git", "ls-files", "evidence"])
    changed = command("TRACKED_EVIDENCE_DIFF", ["git", "diff", "--name-only", "HEAD", "--", "evidence"])
    paths = [item for item in listed["stdout"].splitlines() if item]
    changes = [item for item in changed["stdout"].splitlines() if item]
    return {
        "status": "PASS" if listed["returncode"] == changed["returncode"] == 0 and not changes else "FAIL",
        "tracked_historical_file_count": len(paths),
        "tracked_modification_count": len(changes),
        "tracked_modifications": changes,
        "tracked_index_identity": hashlib.sha256("\n".join(paths).encode()).hexdigest(),
    }


def runtime_gate() -> dict[str, Any]:
    from crypto_lab.config import RuntimeLock
    from crypto_lab.runtime import verify_runtime_lock

    try:
        observed = verify_runtime_lock(
            RuntimeLock.from_json_bytes((ROOT / "runtime.lock.json").read_bytes()),
            dependency_lock_path=ROOT / "requirements.lock.txt",
        )
    except Exception as exc:
        return {"status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"}
    return {"status": "PASS", "observed": observed}


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"fresh acceptance output required: {output}")
    output.mkdir(parents=True)
    os.environ.update({"TZ": "UTC", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
    time.tzset()
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    discovered = suite_ids(
        unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT)),
    )
    unique = len(set(discovered))
    discovery_shape = {
        "status": "PASS" if unique == len(discovered) and unique > 0 else "FAIL",
        "unique_test_cases": unique,
        "discovered_occurrences": len(discovered),
        "test_ids": sorted(discovered),
    }

    full_command = command(
        "FULL_DISCOVERY",
        [PROJECT_PYTHON, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
    )
    independent_command = command(
        "INDEPENDENT_FRESH_DISCOVERY",
        [PROJECT_PYTHON, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
    )
    reverse_dir = output / "reverse"
    reverse_command = command(
        "REVERSE_DETERMINISTIC_ORDER",
        [PROJECT_PYTHON, ROOT / "scripts/run_reverse_test_order.py", "--output-dir", reverse_dir],
    )
    reverse = (
        json.loads((reverse_dir / "result.json").read_text(encoding="utf-8"))
        if (reverse_dir / "result.json").is_file()
        else {"status": "FAIL"}
    )
    owner_command = command(
        "OWNER_SMOKE_UNIT_CONTRACTS_ONLY",
        [PROJECT_PYTHON, "-m", "unittest", "-v", *OWNER_SMOKE_MODULES],
    )
    repair_command = command(
        "DATA_REPAIR_TARGETED",
        [PROJECT_PYTHON, "-m", "unittest", "-v", *DATA_REPAIR_MODULES],
    )
    adversarial_command = command(
        "REPAIRED_ADVERSARIAL",
        [PROJECT_PYTHON, "-m", "unittest", "discover", "-s", "tests/adversarial", "-t", ".", "-v"],
    )
    test_runs = {
        "full": unittest_result("FULL_DISCOVERY", full_command, unique),
        "independent": unittest_result("INDEPENDENT_FRESH_DISCOVERY", independent_command, unique),
        "reverse": {
            **reverse,
            "status": (
                "PASS"
                if reverse_command["returncode"] == 0
                and reverse.get("status") == "PASS"
                and reverse.get("unique_discovered_test_cases") == unique
                and reverse.get("execution_occurrences") == unique
                and reverse.get("failures") == reverse.get("errors") == reverse.get("skipped") == 0
                else "FAIL"
            ),
        },
        "owner_smoke_contracts": unittest_result("OWNER_SMOKE_UNIT_CONTRACTS_ONLY", owner_command),
        "data_repair_targeted": unittest_result("DATA_REPAIR_TARGETED", repair_command),
        "repaired_adversarial": unittest_result("REPAIRED_ADVERSARIAL", adversarial_command),
    }

    validator_command = command(
        "DUCKDB_AND_NAUTILUS_REBUILD_VALIDATION",
        [
            DATA_PYTHON,
            ROOT / "scripts/validate_free_official_binance_rebuild.py",
            "--primary-result", DB_ROOT / "primary-v4-result.json",
            "--independent-result", DB_ROOT / "independent-v4-result.json",
            "--primary-catalog-root", ROOT / "data/catalog/free-official-binance-data-duckdb-001/primary-v4",
            "--independent-catalog-root", ROOT / "data/catalog/free-official-binance-data-duckdb-001/independent-v4",
            "--artifact-root", DB_ROOT / "release-artifacts",
            "--output", DB_ROOT / "deterministic-validation-v4.json",
        ],
    )
    raw_command = command(
        "RAW_OBJECT_REHASH",
        [
            DATA_PYTHON,
            ROOT / "scripts/validate_free_official_raw_objects.py",
            "--database", DB_ROOT / "primary-v4.duckdb",
        ],
    )
    raw_validation = json.loads(raw_command["stdout"]) if raw_command["returncode"] == 0 else {"status": "FAIL"}
    rebuild_validation = json.loads((DB_ROOT / "deterministic-validation-v4.json").read_text(encoding="utf-8"))

    commands = {
        "project_pip_check": command("PROJECT_PIP_CHECK", [PROJECT_PYTHON, "-m", "pip", "check"]),
        "data_tool_pip_check": command("DATA_TOOL_PIP_CHECK", [DATA_PYTHON, "-m", "pip", "check"]),
        "compileall": command(
            "COMPILEALL",
            [PROJECT_PYTHON, "-m", "compileall", "-q", "src", "scripts", "tests"],
        ),
        "git_diff_check": command("GIT_DIFF_CHECK", ["git", "diff", "--check"]),
    }
    hashes = {name: sha256_file(ROOT / name) for name in EXPECTED_HASHES}
    lock_gate = {
        "status": "PASS" if hashes == EXPECTED_HASHES else "FAIL",
        "expected": EXPECTED_HASHES,
        "observed": hashes,
    }
    old_db = ROOT / "data/duckdb/binance-btcusdt-owner-smoke-001.duckdb"
    historical_database = {
        "status": (
            "PASS"
            if old_db.stat().st_size == 1_236_807_680
            and sha256_file(old_db) == "932e97c446c713e8525f43b8111aced2e914b9579eba10823df7c6b0b51887b6"
            else "FAIL"
        ),
        "size_bytes": old_db.stat().st_size,
        "sha256": sha256_file(old_db),
    }
    history = {
        "tracked_evidence": tracked_historical_integrity(),
        "candidate_003": candidate_003_integrity(),
        "historical_duckdb": historical_database,
    }
    disabled = disabled_test_scan()
    runtime = runtime_gate()

    gates = {
        "discovery_shape": discovery_shape["status"],
        **{f"tests_{name}": value["status"] for name, value in test_runs.items()},
        "disabled_skip_xfail_scan": disabled["status"],
        "runtime_preflight": runtime["status"],
        "locked_hashes": lock_gate["status"],
        "project_pip_check": commands["project_pip_check"]["status"],
        "data_tool_pip_check": commands["data_tool_pip_check"]["status"],
        "compileall": commands["compileall"]["status"],
        "git_diff_check": commands["git_diff_check"]["status"],
        "raw_object_rehash": raw_validation.get("status", "FAIL"),
        "deterministic_duckdb_rebuild": (
            "PASS" if validator_command["returncode"] == 0 and rebuild_validation["status"] == "PASS" else "FAIL"
        ),
        "tracked_historical_evidence": history["tracked_evidence"]["status"],
        "candidate_003_integrity": history["candidate_003"]["status"],
        "historical_duckdb_integrity": history["historical_duckdb"]["status"],
    }
    status = "PASS" if all(value == "PASS" for value in gates.values()) else "FAIL"
    result = {
        "schema": "free-official-binance-data-duckdb-acceptance-v1",
        "epoch": "FREE_OFFICIAL_BINANCE_DATA_AND_DUCKDB_REPAIR_001",
        "status": status,
        "started_at_utc": started,
        "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "network_used": False,
        "strategy_run": False,
        "official_trial": False,
        "optimization_run": False,
        "profitability_inspected": False,
        "discovery": discovery_shape,
        "test_runs": test_runs,
        "unique_tests": unique,
        "full_discovery_execution_occurrences": sum(
            int(test_runs[name].get("execution_occurrences", 0))
            for name in ("full", "independent", "reverse")
        ),
        "disabled_tests": disabled,
        "runtime_preflight": runtime,
        "lock_integrity": lock_gate,
        "commands": commands,
        "raw_object_validation": raw_validation,
        "deterministic_rebuild_validation": rebuild_validation,
        "historical_integrity": history,
        "gates": gates,
    }
    write_json(output / "result.json", result)
    logs = (
        ("FULL DISCOVERY", full_command)
        , ("INDEPENDENT DISCOVERY", independent_command)
        , ("REVERSE", reverse_command)
        , ("OWNER SMOKE CONTRACT TESTS", owner_command)
        , ("DATA REPAIR TARGETED", repair_command)
        , ("ADVERSARIAL", adversarial_command)
        , ("DUCKDB / NAUTILUS VALIDATOR", validator_command)
        , ("RAW OBJECT REHASH", raw_command)
        , *((label.upper(), value) for label, value in commands.items())
    )
    with (output / "test-output.txt").open("w", encoding="utf-8", newline="\n") as stream:
        for label, item in logs:
            stream.write(f"===== {label} =====\n")
            stream.write(item["stdout"])
            stream.write(item["stderr"])
            if not (item["stdout"] + item["stderr"]).endswith("\n"):
                stream.write("\n")
    print(json.dumps({"status": status, "unique_tests": unique, "gates": gates}, sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
