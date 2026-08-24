#!/usr/bin/env python3
"""Run complete acceptance for the Instrument/funding-checker repair."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for item in (str(SRC), str(ROOT), str(SCRIPTS)):
    if item not in sys.path:
        sys.path.insert(0, item)

from run_free_official_binance_acceptance import DATA_REPAIR_MODULES  # noqa: E402
from run_free_official_binance_acceptance import OWNER_SMOKE_MODULES  # noqa: E402
from run_free_official_binance_acceptance import candidate_003_integrity  # noqa: E402
from run_free_official_binance_acceptance import command  # noqa: E402
from run_free_official_binance_acceptance import disabled_test_scan  # noqa: E402
from run_free_official_binance_acceptance import runtime_gate  # noqa: E402
from run_free_official_binance_acceptance import sha256_file  # noqa: E402
from run_free_official_binance_acceptance import suite_ids  # noqa: E402
from run_free_official_binance_acceptance import tracked_historical_integrity  # noqa: E402
from run_free_official_binance_acceptance import unittest_result  # noqa: E402
from run_free_official_binance_acceptance import write_json  # noqa: E402


PROJECT_PYTHON = ROOT / ".venv/bin/python"
DATA_PYTHON = ROOT / ".data-venv/bin/python"
DB_ROOT = ROOT / "data/duckdb/instrument-representation-funding-checker-001"
OLD_REPAIR_DB = ROOT / "data/duckdb/free-official-binance-data-duckdb-001/primary-v4.duckdb"
LOCK_HASHES = {
    "SSOT.md": "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99",
    "runtime.lock.json": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "requirements.lock.txt": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
    "data-tool.lock.json": "e55480bd46b63fb44133f42bb5486513b0ac3c868c63441935f998b743721a39",
    "requirements.data.lock.txt": "e0b23ac2e51b385e06fcb2e887a2fb13ae8f811b59594d05d13a6693eb691007",
}
TARGETED_MODULES = (
    "tests.unit.test_instrument_representation_funding_checker_repair",
    "tests.unit.test_free_official_binance_repair",
    "tests.unit.test_owner_smoke_002_sparse_preflight",
    "tests.unit.test_m3_contracts",
    "tests.integration.test_m3_dataset_interface",
    "tests.qualification.test_m1_native_funding",
    "tests.qualification.test_m3_real_profiles",
    *OWNER_SMOKE_MODULES,
    *DATA_REPAIR_MODULES,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    output = arguments.output_dir.resolve()
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
    discovery = {
        "status": "PASS" if unique == len(discovered) and unique > 0 else "FAIL",
        "unique_test_cases": unique,
        "discovered_occurrences": len(discovered),
        "test_ids": sorted(discovered),
    }
    full = command(
        "FULL_UNIQUE_DISCOVERY",
        [PROJECT_PYTHON, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
    )
    independent = command(
        "INDEPENDENT_FRESH_DISCOVERY",
        [PROJECT_PYTHON, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
    )
    reverse_root = output / "reverse"
    reverse_command = command(
        "REVERSE_DETERMINISTIC_ORDER",
        [PROJECT_PYTHON, ROOT / "scripts/run_reverse_test_order.py", "--output-dir", reverse_root],
    )
    reverse = (
        json.loads((reverse_root / "result.json").read_text(encoding="utf-8"))
        if (reverse_root / "result.json").is_file()
        else {"status": "FAIL"}
    )
    targeted = command(
        "INSTRUMENT_AND_FUNDING_TARGETED",
        [PROJECT_PYTHON, "-m", "unittest", "-v", *dict.fromkeys(TARGETED_MODULES)],
    )
    adversarial = command(
        "ADVERSARIAL_REPAIR_REGRESSION",
        [PROJECT_PYTHON, "-m", "unittest", "discover", "-s", "tests/adversarial", "-t", ".", "-v"],
    )
    runs = {
        "full": unittest_result("FULL_UNIQUE_DISCOVERY", full, unique),
        "independent": unittest_result("INDEPENDENT_FRESH_DISCOVERY", independent, unique),
        "targeted": unittest_result("INSTRUMENT_AND_FUNDING_TARGETED", targeted),
        "adversarial": unittest_result("ADVERSARIAL_REPAIR_REGRESSION", adversarial),
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
    }

    rebuild_command = command(
        "DETERMINISTIC_DUCKDB_AND_CATALOG_REBUILD",
        [
            DATA_PYTHON,
            ROOT / "scripts/validate_free_official_binance_rebuild.py",
            "--primary-result", DB_ROOT / "primary-v6-result.json",
            "--independent-result", DB_ROOT / "independent-v3-result.json",
            "--primary-catalog-root", ROOT / "data/catalog/instrument-representation-funding-checker-001/primary-v6",
            "--independent-catalog-root", ROOT / "data/catalog/instrument-representation-funding-checker-001/independent-v3",
            "--artifact-root", DB_ROOT / "release-artifacts",
            "--output", DB_ROOT / "deterministic-validation-v6.json",
        ],
    )
    rebuild = json.loads((DB_ROOT / "deterministic-validation-v6.json").read_text(encoding="utf-8"))
    raw_command = command(
        "RAW_SOURCE_REHASH",
        [
            DATA_PYTHON,
            ROOT / "scripts/validate_free_official_raw_objects.py",
            "--database", DB_ROOT / "primary-v6.duckdb",
        ],
    )
    raw = json.loads(raw_command["stdout"]) if raw_command["returncode"] == 0 else {"status": "FAIL"}
    continuity_command = command(
        "CANONICAL_NUMERIC_VALUE_CONTINUITY",
        [
            DATA_PYTHON,
            ROOT / "scripts/validate_instrument_representation_continuity.py",
            "--historical-database", OLD_REPAIR_DB,
            "--repaired-database", DB_ROOT / "primary-v6.duckdb",
            "--output", DB_ROOT / "value-continuity-v1.json",
        ],
    )
    continuity = json.loads((DB_ROOT / "value-continuity-v1.json").read_text(encoding="utf-8"))

    commands = {
        "project_pip_check": command("PROJECT_PIP_CHECK", [PROJECT_PYTHON, "-m", "pip", "check"]),
        "data_tool_pip_check": command("DATA_TOOL_PIP_CHECK", [DATA_PYTHON, "-m", "pip", "check"]),
        "compileall": command(
            "COMPILEALL",
            [PROJECT_PYTHON, "-m", "compileall", "-q", "src", "scripts", "tests"],
        ),
        "git_diff_check": command("GIT_DIFF_CHECK", ["git", "diff", "--check"]),
    }
    runtime = runtime_gate()
    disabled = disabled_test_scan()
    observed_hashes = {name: sha256_file(ROOT / name) for name in LOCK_HASHES}
    locks = {
        "status": "PASS" if observed_hashes == LOCK_HASHES else "FAIL",
        "expected": LOCK_HASHES,
        "observed": observed_hashes,
    }
    historical = {
        "tracked_evidence": tracked_historical_integrity(),
        "candidate_003": candidate_003_integrity(),
        "superseded_repair_database": {
            "status": "PASS" if OLD_REPAIR_DB.is_file() else "FAIL",
            "path": str(OLD_REPAIR_DB.relative_to(ROOT)),
            "size_bytes": OLD_REPAIR_DB.stat().st_size,
            "sha256": sha256_file(OLD_REPAIR_DB),
        },
    }
    gates = {
        "discovery": discovery["status"],
        **{f"tests_{name}": item["status"] for name, item in runs.items()},
        "runtime_preflight": runtime["status"],
        "locked_hashes": locks["status"],
        "disabled_tests": disabled["status"],
        "project_pip_check": commands["project_pip_check"]["status"],
        "data_tool_pip_check": commands["data_tool_pip_check"]["status"],
        "compileall": commands["compileall"]["status"],
        "git_diff_check": commands["git_diff_check"]["status"],
        "raw_source_rehash": raw.get("status", "FAIL"),
        "deterministic_rebuild": (
            "PASS" if rebuild_command["returncode"] == 0 and rebuild.get("status") == "PASS" else "FAIL"
        ),
        "numeric_value_continuity": (
            "PASS"
            if continuity_command["returncode"] == 0 and continuity.get("status") == "PASS"
            else "FAIL"
        ),
        "tracked_historical_integrity": historical["tracked_evidence"]["status"],
        "candidate_003_integrity": historical["candidate_003"]["status"],
        "superseded_database_preserved": historical["superseded_repair_database"]["status"],
    }
    status = "PASS" if all(value == "PASS" for value in gates.values()) else "FAIL"
    result = {
        "schema": "instrument-representation-funding-checker-acceptance-v1",
        "epoch": "NAUTILUS_INSTRUMENT_REPRESENTATION_AND_FUNDING_CHECKER_REPAIR_001",
        "status": status,
        "started_at_utc": started,
        "completed_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "network_used": False,
        "strategy_run": False,
        "official_trial": False,
        "unique_tests": unique,
        "test_execution_occurrences": sum(
            int(item.get("execution_occurrences", 0)) for item in runs.values()
        ),
        "discovery": discovery,
        "test_runs": runs,
        "runtime_preflight": runtime,
        "lock_integrity": locks,
        "disabled_tests": disabled,
        "commands": commands,
        "raw_object_validation": raw,
        "deterministic_rebuild_validation": rebuild,
        "numeric_value_continuity": continuity,
        "historical_integrity": historical,
        "gates": gates,
    }
    write_json(output / "result.json", result)
    logs = (
        ("FULL UNIQUE DISCOVERY", full),
        ("INDEPENDENT FRESH DISCOVERY", independent),
        ("REVERSE DETERMINISTIC ORDER", reverse_command),
        ("TARGETED INSTRUMENT/FUNDING", targeted),
        ("ADVERSARIAL", adversarial),
        ("DETERMINISTIC REBUILD", rebuild_command),
        ("RAW REHASH", raw_command),
        ("VALUE CONTINUITY", continuity_command),
        *((label.upper(), value) for label, value in commands.items()),
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
