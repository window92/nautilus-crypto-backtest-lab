#!/usr/bin/env python3
"""Run and persist the complete pre-Official OWNER_SMOKE gate set offline."""

from __future__ import annotations

import argparse
import io
import json
import os
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crypto_lab.config import RuntimeLock
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.runtime import verify_runtime_lock
from scripts.run_m4_acceptance import REPAIR_MODULES
from scripts.validate_m3_evidence import validate as validate_m3_evidence
from scripts.validate_m4_evidence import validate as validate_m4_evidence


EXPECTED_LOCKS = {
    "SSOT.md": "7bb2fc68d9b73b168a582d890a6f952fd0c4eb20fc0e31857903909f27dfaa8f",
    "runtime.lock.json": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "requirements.lock.txt": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
}
STRATEGY_MODULES = (
    "tests.golden.test_owner_smoke_sma20",
    "tests.integration.test_owner_smoke_daily_strategy",
    "tests.qualification.test_owner_smoke_registered_strategy",
    "tests.unit.test_m2_release_contract",
)
REQUIRED_STRATEGY_TESTS = {
    "tests.golden.test_owner_smoke_sma20.OwnerSmokeSma20GoldenTests.test_native_sma20_matches_independent_arithmetic_mean",
    "tests.golden.test_owner_smoke_sma20.OwnerSmokeSma20GoldenTests.test_first_signal_requires_exactly_twenty_completed_daily_closes",
    "tests.golden.test_owner_smoke_sma20.OwnerSmokeSma20GoldenTests.test_incomplete_or_non_utc_daily_bar_is_rejected",
    "tests.golden.test_owner_smoke_sma20.OwnerSmokeSma20GoldenTests.test_spot_equality_is_flat_and_never_short",
    "tests.golden.test_owner_smoke_sma20.OwnerSmokeSma20GoldenTests.test_perpetual_equality_is_flat",
    "tests.golden.test_owner_smoke_sma20.OwnerSmokeSma20GoldenTests.test_spot_short_target_is_rejected_before_any_submission",
    "tests.integration.test_owner_smoke_daily_strategy.OwnerSmokeDailyStrategyIntegrationTests.test_spot_daily_resampling_is_utc_complete_and_causal",
    "tests.integration.test_owner_smoke_daily_strategy.OwnerSmokeDailyStrategyIntegrationTests.test_perpetual_reversal_closes_flat_then_reopens_separately",
    "tests.integration.test_owner_smoke_daily_strategy.OwnerSmokeDailyStrategyIntegrationTests.test_deterministic_replay_matches_for_both_profiles",
    "tests.qualification.test_owner_smoke_registered_strategy.OwnerSmokeRegisteredStrategyQualificationTests.test_material_parameter_mutation_changes_identity_and_cannot_reuse_locked_spec",
    "tests.adversarial.test_aud001_strategy_identity.Aud001StrategyIdentityTests.test_previous_same_identity_different_fill_schedule_exploit_is_rejected",
    "tests.adversarial.test_aud001_strategy_identity.Aud001StrategyIdentityTests.test_registered_official_strategy_constructs_without_any_strategy_plan",
    "tests.golden.test_m1_contracts.M1GoldenContractTests.test_g07_spot_oversell_is_blocked_before_submission",
}


def _environment() -> dict[str, str]:
    return {
        **os.environ,
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": "/tmp/owner-smoke-pyc",
        "PYTHONPATH": f"{ROOT / 'src'}:{ROOT}",
    }


def _ids(suite: unittest.TestSuite) -> list[str]:
    values: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            values.extend(_ids(item))
        else:
            values.append(item.id())
    return values


def _run_suite(label: str, suite: unittest.TestSuite) -> tuple[dict[str, Any], str]:
    expected = _ids(suite)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    failures = {test.id() for test, _traceback in result.failures}
    errors = {test.id() for test, _traceback in result.errors}
    skipped = {test.id() for test, _reason in result.skipped}
    rows = [
        {
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
        for test_id in expected
    ]
    passed = bool(
        result.wasSuccessful()
        and not result.skipped
        and len(expected) == len(set(expected)) == result.testsRun
    )
    return (
        {
            "label": label,
            "status": "PASS" if passed else "FAIL",
            "unique_test_cases": len(set(expected)),
            "execution_occurrences": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
            "tests": rows,
        },
        stream.getvalue(),
    )


def _command(label: str, command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "label": label,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "evidence/research/owner-smoke-001/preflight",
    )
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite preflight evidence: {output}")

    os.environ.update({"TZ": "UTC", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
    time.tzset()
    started = datetime.now(UTC)
    branch = _command("GIT_BRANCH", ["git", "branch", "--show-current"])
    head = _command("GIT_HEAD", ["git", "rev-parse", "HEAD"])
    origin = _command("GIT_ORIGIN_MAIN", ["git", "rev-parse", "origin/main"])
    clean = _command("GIT_CLEAN", ["git", "status", "--porcelain=v1"])
    source_clean = bool(
        branch["stdout"].strip() == "main"
        and head["stdout"].strip() == origin["stdout"].strip()
        and not clean["stdout"].strip()
    )

    loader = unittest.defaultTestLoader
    strategy, strategy_output = _run_suite(
        "OWNER_SMOKE_UNIT_GOLDEN_INTEGRATION_QUALIFICATION",
        loader.loadTestsFromNames(STRATEGY_MODULES),
    )
    full_suite = loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    full, full_output = _run_suite("FULL_DISCOVERY", full_suite)
    discovered_again = loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    independent, independent_output = _run_suite(
        "INDEPENDENT_FRESH_DISCOVERY",
        discovered_again,
    )
    adversarial, adversarial_output = _run_suite(
        "REPAIRED_ADVERSARIAL",
        loader.loadTestsFromNames(REPAIR_MODULES),
    )

    inventory_path = ROOT / "evidence/repair/v1-post-build-001/test-inventory.json"
    historical = json.loads(inventory_path.read_text(encoding="utf-8"))
    historical_ids = set(historical["repair_tests"])
    # The immutable inventory lists the 39 adversarial IDs separately. Recover
    # all 206 baseline IDs from the accepted final M4 result.
    accepted_results = json.loads(
        (
            ROOT
            / "evidence/repair/v1-post-build-001/acceptance/m0-m4/test-results.json"
        ).read_text(
            encoding="utf-8",
        ),
    )
    baseline_ids = {item["test_id"] for item in accepted_results["unique_tests"]}
    full_status = {item["test_id"]: item["status"] for item in full["tests"]}
    baseline_206 = {
        "status": (
            "PASS"
            if len(baseline_ids) == 206
            and baseline_ids.issubset(full_status)
            and all(full_status[item] == "PASS" for item in baseline_ids)
            else "FAIL"
        ),
        "immutable_baseline_count": len(baseline_ids),
        "current_total_count": full["unique_test_cases"],
        "new_test_count": full["unique_test_cases"] - len(baseline_ids),
        "repair_adversarial_ids_preserved": len(historical_ids),
        "baseline_inventory_sha256": sha256_file(inventory_path),
    }
    required_strategy = {
        "status": (
            "PASS"
            if REQUIRED_STRATEGY_TESTS.issubset(full_status)
            and all(full_status[item] == "PASS" for item in REQUIRED_STRATEGY_TESTS)
            else "FAIL"
        ),
        "required_test_ids": sorted(REQUIRED_STRATEGY_TESTS),
    }

    with tempfile.TemporaryDirectory(prefix="owner-smoke-reverse-", dir="/tmp") as temporary:
        reverse_dir = Path(temporary)
        reverse_command = _command(
            "REVERSE_DETERMINISTIC_ORDER",
            [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "scripts/run_reverse_test_order.py"),
                "--output-dir",
                str(reverse_dir),
            ],
        )
        reverse_result = (
            json.loads((reverse_dir / "result.json").read_text(encoding="utf-8"))
            if (reverse_dir / "result.json").is_file()
            else {"status": "FAIL", "detail": "reverse result missing"}
        )
        reverse_output = (
            (reverse_dir / "output.txt").read_text(encoding="utf-8")
            if (reverse_dir / "output.txt").is_file()
            else ""
        )

    commands = [
        _command("PIP_CHECK", [str(ROOT / ".venv/bin/python"), "-m", "pip", "check"]),
        _command(
            "COMPILEALL",
            [str(ROOT / ".venv/bin/python"), "-m", "compileall", "-q", "src", "scripts", "tests"],
        ),
        _command("GIT_DIFF_CHECK", ["git", "diff", "--check"]),
    ]
    try:
        runtime = verify_runtime_lock(
            RuntimeLock.from_json_bytes((ROOT / "runtime.lock.json").read_bytes()),
            dependency_lock_path=ROOT / "requirements.lock.txt",
        )
        runtime_check = {"status": "PASS", "result": runtime}
    except Exception as exc:
        runtime_check = {"status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"}
    locks = {name: sha256_file(ROOT / name) for name in EXPECTED_LOCKS}
    lock_check = {
        "status": "PASS" if locks == EXPECTED_LOCKS else "FAIL",
        "expected": EXPECTED_LOCKS,
        "observed": locks,
    }
    m3 = validate_m3_evidence(ROOT / "evidence/m3/m3-acceptance-001")
    m4 = validate_m4_evidence(ROOT / "evidence/m4/m4-acceptance-001")
    discovery_match = {
        "status": (
            "PASS"
            if full["status"] == independent["status"] == "PASS"
            and {item["test_id"] for item in full["tests"]}
            == {item["test_id"] for item in independent["tests"]}
            else "FAIL"
        ),
        "first_count": full["unique_test_cases"],
        "independent_count": independent["unique_test_cases"],
    }
    checks = {
        "source_clean_head_equals_origin_main": "PASS" if source_clean else "FAIL",
        "strategy_tests": strategy["status"],
        "required_strategy_contract_tests": required_strategy["status"],
        "full_discovery": full["status"],
        "historical_206": baseline_206["status"],
        "independent_discovery": discovery_match["status"],
        "reverse_deterministic_order": (
            "PASS"
            if reverse_command["status"] == reverse_result.get("status") == "PASS"
            else "FAIL"
        ),
        "repaired_adversarial": adversarial["status"],
        "runtime_preflight": runtime_check["status"],
        "pip_check": commands[0]["status"],
        "compileall": commands[1]["status"],
        "git_diff_check": commands[2]["status"],
        "locked_hashes": lock_check["status"],
        "m3_evidence_validation": m3["status"],
        "m4_evidence_validation": m4["status"],
    }
    status = "PASS" if all(value == "PASS" for value in checks.values()) else "FAIL"
    test_payload = {
        "schema": "owner-smoke-preflight-tests-v1",
        "strategy": strategy,
        "required_strategy_contracts": required_strategy,
        "full": full,
        "independent": independent,
        "discovery_match": discovery_match,
        "historical_206": baseline_206,
        "adversarial": adversarial,
    }
    gate_payload = {
        "schema": "owner-smoke-preflight-gates-v1",
        "authorization_id": "OWNER_OPERATIONAL_SMOKE_001",
        "started_at_utc": started,
        "finished_at_utc": datetime.now(UTC),
        "network_used": False,
        "source": {
            "branch": branch["stdout"].strip(),
            "head": head["stdout"].strip(),
            "origin_main": origin["stdout"].strip(),
            "clean": source_clean,
        },
        "checks": checks,
        "runtime_preflight": runtime_check,
        "lock_integrity": lock_check,
        "commands": commands,
        "m3_validation": m3,
        "m4_validation": m4,
        "reverse_result": reverse_result,
        "status": status,
    }
    summary_material = {
        "schema": "owner-smoke-preflight-summary-v1",
        "authorization_id": "OWNER_OPERATIONAL_SMOKE_001",
        "status": status,
        "checks": checks,
        "full_test_count": full["unique_test_cases"],
        "historical_test_count": baseline_206["immutable_baseline_count"],
        "new_test_count": baseline_206["new_test_count"],
        "reverse_test_count": reverse_result.get("unique_discovered_test_cases", 0),
        "adversarial_test_count": adversarial["unique_test_cases"],
    }
    summary = {
        "preflight_identity": canonical_sha256(summary_material),
        **summary_material,
    }
    _write_once(output / "tests.json", canonical_json_bytes(test_payload) + b"\n")
    _write_once(output / "gates.json", canonical_json_bytes(gate_payload) + b"\n")
    _write_once(output / "summary.json", canonical_json_bytes(summary) + b"\n")
    _write_once(
        output / "test-output.txt",
        (
            "STRATEGY\n"
            + strategy_output
            + "\nFULL\n"
            + full_output
            + "\nINDEPENDENT\n"
            + independent_output
            + "\nADVERSARIAL\n"
            + adversarial_output
            + "\nREVERSE\n"
            + reverse_output
        ).encode("utf-8"),
    )
    print(json.dumps(summary, sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
