#!/usr/bin/env python3
"""Run and freeze the additive M0-M2 repair verification gates."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crypto_lab.config import RuntimeLock
from crypto_lab.data import DatasetRelease
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.runtime import verify_runtime_lock


EVIDENCE = ROOT / "evidence/m2/m2-repair-001"
VERIFICATION = EVIDENCE / "verification-final-004"
ACCEPTANCE = EVIDENCE / "acceptance-final-004/test-results.json"
EXPECTED_SSOT = "7bb2fc68d9b73b168a582d890a6f952fd0c4eb20fc0e31857903909f27dfaa8f"
EXPECTED_RUNTIME = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
EXPECTED_DEPENDENCIES = "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"
EXPECTED_RAW_AGGREGATE = "3254e66009be11b4ae90d628271c5c21f4573495174e2d1b17b236325528b967"
EXPECTED_HISTORY_AGGREGATE = "ed0845e6e70feae9324dd48f1ed4662fb832c1f8f9772d36e61af35ec42ca8ad"
EXPECTED_OLD_CATALOG_AGGREGATE = "a7eea0281d8257be7615389fc1a9c3bfbbbe67d74892d751edee30ab2ea576ce"


def write_once(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value if isinstance(value, bytes) else canonical_json_bytes(value) + b"\n"
    path.write_bytes(payload)


def file_fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        digest.update(f"{sha256_file(path)}  {relative}\n".encode())
    return digest.hexdigest()


def command_result(command: list[str], *, environment: dict[str, str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
    }


def test_ids(suite: unittest.TestSuite) -> list[str]:
    result: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(test_ids(item))
        else:
            result.append(item.id())
    return result


def run_independent_discovery() -> tuple[dict[str, Any], str]:
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"),
        top_level_dir=str(ROOT),
    )
    identities = test_ids(suite)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    failures = {test.id(): traceback for test, traceback in result.failures}
    errors = {test.id(): traceback for test, traceback in result.errors}
    skipped = {test.id(): reason for test, reason in result.skipped}
    rows = []
    for test_id in identities:
        status = (
            "FAIL"
            if test_id in failures
            else "ERROR"
            if test_id in errors
            else "SKIPPED"
            if test_id in skipped
            else "PASS"
        )
        rows.append({"test_id": test_id, "status": status})
    report = {
        "schema": "m2-repair-independent-discovery-v1",
        "command": (
            "TZ=UTC LC_ALL=C.UTF-8 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src "
            ".venv/bin/python -m unittest discover -v -s tests -t ."
        ),
        "status": "PASS" if result.wasSuccessful() else "FAIL",
        "unique_test_cases": len(set(identities)),
        "test_execution_occurrences": result.testsRun,
        "duplicate_discovered_ids": sorted(
            test_id for test_id in set(identities) if identities.count(test_id) > 1
        ),
        "passed": result.testsRun - len(failures) - len(errors) - len(skipped),
        "failures": len(failures),
        "errors": len(errors),
        "skipped": len(skipped),
        "tests": rows,
    }
    return report, stream.getvalue()


def main() -> int:
    locked_environment = dict(os.environ)
    locked_environment.update(
        {
            "TZ": "UTC",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "src",
        },
    )
    acceptance = json.loads(ACCEPTANCE.read_text())
    discovery, discovery_output = run_independent_discovery()
    write_once(VERIFICATION / "independent-discovery-output.txt", discovery_output.encode())
    write_once(VERIFICATION / "independent-discovery-results.json", discovery)

    acceptance_ids = {item["test_id"] for item in acceptance["unique_tests"]}
    discovery_ids = {item["test_id"] for item in discovery["tests"]}
    targeted = [
        item
        for item in acceptance["unique_tests"]
        if item["test_id"].startswith("tests.unit.test_m2_repair_contracts.")
        or item["test_id"].startswith(
            "tests.integration.test_m2_repair_downstream_contract.",
        )
    ]
    write_once(
        VERIFICATION / "targeted-repair-results.json",
        {
            "schema": "m2-repair-targeted-results-v1",
            "status": "PASS" if targeted and all(item["status"] == "PASS" for item in targeted) else "FAIL",
            "test_cases": len(targeted),
            "failures": sum(item["status"] == "FAIL" for item in targeted),
            "errors": sum(item["status"] == "ERROR" for item in targeted),
            "skipped": sum(item["status"] == "SKIPPED" for item in targeted),
            "tests": targeted,
        },
    )

    runtime_lock = RuntimeLock.from_json_bytes((ROOT / "runtime.lock.json").read_bytes())
    runtime_result = verify_runtime_lock(
        runtime_lock,
        dependency_lock_path=ROOT / "requirements.lock.txt",
    )
    with tempfile.TemporaryDirectory(prefix="m2-repair-pycache-") as pycache:
        compile_environment = dict(locked_environment)
        compile_environment["PYTHONPYCACHEPREFIX"] = pycache
        compile_result = command_result(
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "compileall",
                "-q",
                "src",
                "scripts",
                "tests",
            ],
            environment=compile_environment,
        )
    command_checks = {
        "runtime_preflight": {
            "command": "verify_runtime_lock(runtime.lock.json, requirements.lock.txt)",
            "status": "PASS",
            "result": runtime_result,
        },
        "pip_check": command_result(
            [str(ROOT / ".venv/bin/python"), "-m", "pip", "check"],
            environment=locked_environment,
        ),
        "python_compile": compile_result,
        "git_diff_check": command_result(
            ["git", "diff", "--check"],
            environment=locked_environment,
        ),
    }
    write_once(
        VERIFICATION / "runtime-and-static-checks.json",
        {
            "schema": "m2-repair-runtime-static-v1",
            "status": (
                "PASS"
                if all(item["status"] == "PASS" for item in command_checks.values())
                else "FAIL"
            ),
            "checks": command_checks,
        },
    )

    protected_inventory = json.loads(
        (EVIDENCE / "preserved-historical-evidence-inventory.json").read_text(),
    )["files"]
    protected_paths = [ROOT / item["path"] for item in protected_inventory]
    protected_exact = all(
        path.is_file()
        and sha256_file(path) == item["sha256"]
        and path.stat().st_size == item["byte_size"]
        for path, item in zip(protected_paths, protected_inventory, strict=True)
    )
    raw_paths = [path for path in (ROOT / "data/raw").rglob("*") if path.is_file()]
    old_catalog_paths = [ROOT / "data/catalog/.gitkeep"]
    for identity in (
        "462d5dd6a1750d37a6a865de72ed3dfeff5241fa55f7ae13bf91fba9f1d9e948",
        "e4bd4f5bab66eeace6a8020c19db0deff4f74d510765b20ad8ebaa9e84cdb75c",
    ):
        old_catalog_paths.extend(
            path for path in (ROOT / "data/catalog" / identity).rglob("*") if path.is_file()
        )

    release_identities = json.loads((EVIDENCE / "release-identities.json").read_text())
    release_files = {
        "spot": EVIDENCE / "spot-qualification-release.json",
        "perpetual": EVIDENCE / "perpetual-qualification-release.json",
        "m3_ready_perpetual": EVIDENCE / "m3-ready-perpetual-release.json",
    }
    releases: dict[str, DatasetRelease] = {}
    release_resolution: dict[str, Any] = {}
    for name, evidence_path in release_files.items():
        release = DatasetRelease.from_json_bytes(evidence_path.read_bytes())
        releases[name] = release
        resolved = release.resolve_runtime_data(ROOT / "data")
        manifest_path = ROOT / "data/releases" / f"{release.dataset_release_id}.json"
        release_resolution[name] = {
            "dataset_release_id": release.dataset_release_id,
            "canonical_material_identity": canonical_sha256(release.material_payload()),
            "tracked_manifest_exact": (
                manifest_path.is_file() and manifest_path.read_bytes() == release.to_json_bytes()
            ),
            "catalog_identity": release.catalog_identity,
            "resolved_catalog_identity": canonical_sha256(resolved.semantic_inventory),
            "resolved_instrument_id": str(resolved.instrument.id),
            "resolved_data_objects": len(resolved.data),
        }

    m3_release = releases["m3_ready_perpetual"]
    funding = json.loads(
        (ROOT / "data/releases" / f"{m3_release.funding_data_identity}.funding.json").read_text(),
    )
    market_mapping = json.loads((EVIDENCE / "market-limit-mapping.json").read_text())
    catalog_rebuild = json.loads((EVIDENCE / "catalog-rebuild-comparison.json").read_text())
    publisher = json.loads((EVIDENCE / "publisher-checksum-validation.json").read_text())
    defect_rows = [
        json.loads(line)
        for line in (ROOT / "research/defects.jsonl").read_text().splitlines()
        if line.strip()
    ]
    disabled_patterns = (
        "unittest.skip",
        "skipTest(",
        "pytest.mark.skip",
        "pytest.mark.xfail",
        "@xfail",
    )
    disabled_hits = []
    for path in (ROOT / "tests").rglob("*.py"):
        text = path.read_text()
        for pattern in disabled_patterns:
            if pattern in text:
                disabled_hits.append({"path": str(path.relative_to(ROOT)), "pattern": pattern})

    changed = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    changed_paths = sorted(line[3:] for line in changed)
    forbidden_changes = [
        path
        for path in changed_paths
        if path in {"SSOT.md", "runtime.lock.json", "requirements.lock.txt"}
        or path.startswith("evidence/m0/")
        or path.startswith("evidence/m1/")
        or path.startswith("evidence/m2/m2-acceptance-001/")
        or path.startswith("data/raw/")
        or path.startswith("src/crypto_lab/m3")
        or path.startswith("src/crypto_lab/m4")
    ]
    checks = {
        "locked_hashes": (
            sha256_file(ROOT / "SSOT.md") == EXPECTED_SSOT
            and sha256_file(ROOT / "runtime.lock.json") == EXPECTED_RUNTIME
            and sha256_file(ROOT / "requirements.lock.txt") == EXPECTED_DEPENDENCIES
        ),
        "combined_acceptance": (
            acceptance["status"] == "PASS"
            and acceptance["unique_test_cases"] == 106
            and acceptance["test_execution_occurrences"] == 107
            and acceptance["failures"] == 0
            and acceptance["errors"] == 0
            and acceptance["skipped"] == 0
            and acceptance["repeated_execution_count"] == 1
            and acceptance["canonical_owner_totals"]
            == {"M0_REGRESSION": 31, "M1_REGRESSION": 29, "M2": 46}
            and acceptance["additional_non_test_acceptance_check_count"] == 1
        ),
        "independent_discovery": (
            discovery["status"] == "PASS"
            and discovery["unique_test_cases"] == 106
            and discovery["test_execution_occurrences"] == 106
            and discovery["failures"] == 0
            and discovery["errors"] == 0
            and discovery["skipped"] == 0
            and not discovery["duplicate_discovered_ids"]
        ),
        "acceptance_discovery_exact_set": acceptance_ids == discovery_ids,
        "historical_evidence_exact": (
            protected_exact
            and len(protected_paths) == 210
            and file_fingerprint(protected_paths) == EXPECTED_HISTORY_AGGREGATE
        ),
        "raw_store_exact": (
            len(raw_paths) == 43 and file_fingerprint(raw_paths) == EXPECTED_RAW_AGGREGATE
        ),
        "old_catalogs_exact": file_fingerprint(old_catalog_paths) == EXPECTED_OLD_CATALOG_AGGREGATE,
        "new_releases_resolve": all(
            item["dataset_release_id"] == item["canonical_material_identity"]
            and item["tracked_manifest_exact"]
            and item["catalog_identity"] == item["resolved_catalog_identity"]
            for item in release_resolution.values()
        ),
        "m3_ready_release_contract": (
            m3_release.normalized_time_range.to_builtins()
            == {
                "start_inclusive": "2025-01-01T07:56:00Z",
                "end_exclusive": "2025-01-01T08:04:00Z",
            }
            and len(funding["events"]) == 1
            and funding["events"][0]["calc_time_ns"] == 1_735_718_400_000_000_000
            and funding["events"][0]["funding_rate"] == "0.00010000"
            and funding["events"][0]["funding_interval_hours"] == 8
        ),
        "publisher_checksums": (
            publisher["status"] == "PASS"
            and publisher["count"] == 5
            and all(item["status"] == "PASS" for item in publisher["results"])
        ),
        "catalog_rebuilds": (
            catalog_rebuild["status"] == "PASS"
            and catalog_rebuild["independent_rebuilds_per_release"] == 2
            and all(
                item["first_identity"] == item["second_identity"] == item["persisted_identity"]
                and item["first_second_semantic_equal"]
                and item["first_persisted_semantic_equal"]
                for item in catalog_rebuild["comparisons"].values()
            )
        ),
        "market_limit_contract": (
            market_mapping["spot"]["raw_MARKET_LOT_SIZE"]["maxQty"] == "107.65653775"
            and market_mapping["spot"]["effective_MARKET"]["maxQty"] == "107.65653000"
            and market_mapping["spot"]["nautilus"]["max_quantity"] == "107.65653"
            and market_mapping["perpetual"]["raw_MARKET_LOT_SIZE"]["maxQty"] == "120"
            and market_mapping["perpetual"]["effective_MARKET"]["maxQty"] == "120"
            and market_mapping["perpetual"]["nautilus"]["max_quantity"] == "120.000"
        ),
        "defect_log": (
            {item["finding_id"] for item in defect_rows} == {"F-01", "F-02", "F-03", "F-04", "F-05"}
            and all(item["terminal_disposition"] == "REPAIRED_VERIFIED" for item in defect_rows)
        ),
        "no_disabled_tests": not disabled_hits,
        "no_forbidden_changes": not forbidden_changes,
        "no_m3_execution": (
            json.loads((EVIDENCE / "repair-generation-summary.json").read_text())["m3_started"]
            is False
        ),
        "command_checks": all(item["status"] == "PASS" for item in command_checks.values()),
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    validation = {
        "schema": "m2-repair-evidence-validation-v1",
        "status": status,
        "checks": checks,
        "acceptance_only_ids": sorted(acceptance_ids - discovery_ids),
        "discovery_only_ids": sorted(discovery_ids - acceptance_ids),
        "release_resolution": release_resolution,
        "disabled_test_hits": disabled_hits,
        "forbidden_changes": forbidden_changes,
    }
    write_once(VERIFICATION / "evidence-validation.json", validation)
    write_once(
        VERIFICATION / "ending-integrity-precommit.json",
        {
            "schema": "m2-repair-ending-integrity-v1",
            "status": status,
            "captured_at_utc": datetime.now(UTC),
            "head_before_repair_commit": subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "origin_main_before_repair_commit": subprocess.run(
                ["git", "rev-parse", "origin/main"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "ssot_sha256": sha256_file(ROOT / "SSOT.md"),
            "runtime_lock_sha256": sha256_file(ROOT / "runtime.lock.json"),
            "dependency_lock_sha256": sha256_file(ROOT / "requirements.lock.txt"),
            "historical_evidence_aggregate": file_fingerprint(protected_paths),
            "raw_store_aggregate": file_fingerprint(raw_paths),
            "old_catalog_aggregate": file_fingerprint(old_catalog_paths),
            "changed_paths": changed_paths,
            "forbidden_changes": forbidden_changes,
            "commit_pending": True,
            "push_pending": True,
            "m3_started": False,
            "m4_started": False,
            "official_run": False,
        },
    )
    failed_attempts = [
        json.loads(line)
        for line in (EVIDENCE / "failed-attempts.jsonl").read_text().splitlines()
        if line.strip()
    ]
    evidence_inventory = [
        {
            "path": str(path.relative_to(ROOT)),
            "sha256": sha256_file(path),
            "byte_size": path.stat().st_size,
        }
        for path in sorted(EVIDENCE.rglob("*"))
        if path.is_file() and path.name != "final-acceptance-manifest.json"
    ]
    write_once(
        VERIFICATION / "final-acceptance-manifest.json",
        {
            "schema": "m0-m2-repair-final-acceptance-v1",
            "status": status,
            "findings": ["F-01", "F-02", "F-03", "F-04", "F-05"],
            "tests": {
                "unique_test_cases": acceptance["unique_test_cases"],
                "test_execution_occurrences": acceptance["test_execution_occurrences"],
                "additional_non_test_acceptance_checks": acceptance[
                    "additional_non_test_acceptance_check_count"
                ],
                "failures": acceptance["failures"],
                "errors": acceptance["errors"],
                "skipped": acceptance["skipped"],
                "canonical_owner_totals": acceptance["canonical_owner_totals"],
                "repeated_executions": acceptance["repeated_executions"],
            },
            "old_dataset_release_ids": release_identities["old"],
            "new_dataset_release_ids": release_identities["new"],
            "failed_attempt_records": len(failed_attempts),
            "failed_attempts_preserved": True,
            "historical_evidence_preserved": checks["historical_evidence_exact"],
            "raw_objects_preserved": checks["raw_store_exact"],
            "evidence_inventory": evidence_inventory,
            "intended_commit_message": "fix(m1-m2): repair dataset and provenance contracts",
            "m3_started": False,
            "m4_started": False,
        },
    )
    print(
        json.dumps(
            {
                "status": status,
                "unique_test_cases": acceptance["unique_test_cases"],
                "test_execution_occurrences": acceptance["test_execution_occurrences"],
                "discovery_test_cases": discovery["unique_test_cases"],
                "checks": checks,
            },
            sort_keys=True,
        ),
    )
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
