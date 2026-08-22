#!/usr/bin/env python3
"""Run all completed-phase regressions and the complete M2 acceptance suite."""

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
EVIDENCE = ROOT / "evidence/m2/m2-acceptance-001"
EPOCH = os.environ.get("M2_ACCEPTANCE_EPOCH", "")
SUFFIX = f"-{EPOCH}" if EPOCH else ""
COMMAND = (
    (f"M2_ACCEPTANCE_EPOCH={EPOCH} " if EPOCH else "")
    + "TZ=UTC LC_ALL=C.UTF-8 PYTHONPATH=src .venv/bin/python "
    "scripts/run_m2_acceptance.py"
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
M2_MODULES = (
    "tests.unit.test_m2_raw_and_parsing",
    "tests.unit.test_m2_release_contract",
    "tests.golden.test_m2_data_contract",
    "tests.integration.test_m2_downstream_contract",
    "tests.qualification.test_m2_official_samples",
)


def test_ids(suite: unittest.TestSuite) -> list[str]:
    result: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(test_ids(item))
        else:
            result.append(item.id())
    return result


def run_phase(phase: str, modules: tuple[str, ...]):
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    ids = test_ids(suite)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    failed = {test.id(): traceback for test, traceback in result.failures}
    errors = {test.id(): traceback for test, traceback in result.errors}
    skipped = {test.id(): reason for test, reason in result.skipped}
    rows: list[dict[str, str]] = []
    for test_id in ids:
        if test_id in failed:
            status = "FAIL"
        elif test_id in errors:
            status = "ERROR"
        elif test_id in skipped:
            status = "SKIPPED"
        else:
            status = "PASS"
        rows.append({"phase": phase, "test_id": test_id, "status": status})
    return rows, result, stream.getvalue()


def preserve_failed_prior(path: Path, output_path: Path) -> None:
    if not path.is_file():
        return
    previous_bytes = path.read_bytes()
    previous = json.loads(previous_bytes)
    if previous.get("status") == "PASS":
        raise FileExistsError("refusing to overwrite accepted M2 test evidence")
    suffix = hashlib.sha256(previous_bytes).hexdigest()[:12]
    path.with_name(f"test-results-failed-{suffix}.json").write_bytes(previous_bytes)
    if output_path.is_file():
        output_path.with_name(f"test-output-failed-{suffix}.txt").write_bytes(
            output_path.read_bytes(),
        )


def main() -> int:
    phase_data = [
        ("M0_REGRESSION", *run_phase("M0_REGRESSION", M0_MODULES)),
        ("M1_REGRESSION", *run_phase("M1_REGRESSION", M1_MODULES)),
        ("M2", *run_phase("M2", M2_MODULES)),
    ]
    tests = [row for _, rows, _, _ in phase_data for row in rows]
    results = [result for _, _, result, _ in phase_data]
    output = "".join(
        f"{phase}\n\n{text}\n" for phase, _, _, text in phase_data
    )
    tests_run = sum(result.testsRun for result in results)
    failures = sum(len(result.failures) for result in results)
    errors = sum(len(result.errors) for result in results)
    skipped = sum(len(result.skipped) for result in results)
    successful = all(result.wasSuccessful() for result in results)
    phase_totals = {
        phase: {
            "tests_run": sum(item["phase"] == phase for item in tests),
            "passed": sum(
                item["phase"] == phase and item["status"] == "PASS"
                for item in tests
            ),
            "failed": sum(
                item["phase"] == phase and item["status"] in {"FAIL", "ERROR"}
                for item in tests
            ),
            "skipped": sum(
                item["phase"] == phase and item["status"] == "SKIPPED"
                for item in tests
            ),
        }
        for phase in ("M0_REGRESSION", "M1_REGRESSION", "M2")
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
    result_path = EVIDENCE / f"test-results{SUFFIX}.json"
    output_path = EVIDENCE / f"test-output{SUFFIX}.txt"
    preserve_failed_prior(result_path, output_path)
    output_path.write_text(output, encoding="utf-8")
    result_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    statuses = {item["test_id"]: item["status"] for item in tests}
    negative_contracts = {
        "corrupted_raw_bytes": "tests.unit.test_m2_raw_and_parsing.RawObjectTests.test_acquirer_stores_corrupted_download_before_failing_checksum",
        "locator_republication_conflict": "tests.unit.test_m2_raw_and_parsing.RawObjectTests.test_locator_replay_with_different_bytes_preserves_both_and_links_conflict",
        "missing_minute": "tests.unit.test_m2_raw_and_parsing.TimestampAndParsingTests.test_one_minute_completeness_passes_and_missing_minute_blocks",
        "conflicting_duplicate": "tests.unit.test_m2_raw_and_parsing.TimestampAndParsingTests.test_conflicting_duplicate_blocks",
        "malformed_nonmonotonic_ohlc_negative_volume": "tests.unit.test_m2_raw_and_parsing.TimestampAndParsingTests.test_malformed_nonmonotonic_invalid_ohlc_and_negative_volume_block_without_repair",
        "source_role_substitution": "tests.unit.test_m2_raw_and_parsing.RawObjectTests.test_prohibited_archive_role_substitution_is_rejected",
        "funding_missing": "tests.unit.test_m2_release_contract.FundingContractTests.test_removed_required_event_is_funding_missing",
        "funding_schedule_unproven": "tests.unit.test_m2_release_contract.FundingContractTests.test_unproven_schedule_is_funding_ambiguous",
        "funding_duplicate_conflict": "tests.unit.test_m2_release_contract.FundingContractTests.test_conflicting_funding_duplicate_blocks",
        "spot_mark_funding_forbidden": "tests.unit.test_m2_release_contract.DatasetReleaseTests.test_spot_forbids_mark_and_funding_roles",
        "perpetual_mark_funding_required": "tests.unit.test_m2_release_contract.DatasetReleaseTests.test_perpetual_requires_mark_and_funding_roles",
        "catalog_mutation_stale": "tests.unit.test_m2_release_contract.DatasetReleaseTests.test_catalog_rebuild_semantic_stability_and_mutation_staleness",
    }
    negative = {
        name: {"test_id": test_id, "status": statuses.get(test_id, "NOT_EXECUTED")}
        for name, test_id in negative_contracts.items()
    }
    negative_status = "PASS" if all(item["status"] == "PASS" for item in negative.values()) else "FAIL"
    negative_path = EVIDENCE / f"negative-fixture-results{SUFFIX}.json"
    if negative_path.exists():
        raise FileExistsError("refusing to overwrite negative-fixture evidence")
    negative_path.write_text(
        json.dumps(
            {
                "schema": "m2-negative-fixture-results-v1",
                "status": negative_status,
                "results": negative,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    sys.stdout.write(output)
    return 0 if successful and negative_status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
