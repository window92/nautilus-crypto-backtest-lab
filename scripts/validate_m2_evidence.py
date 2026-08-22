#!/usr/bin/env python3
"""Read-only validation of the additive M2 acceptance evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crypto_lab.data import DatasetRelease
from crypto_lab.hashing import sha256_file


EVIDENCE = ROOT / "evidence/m2/m2-acceptance-001"
EXPECTED_SSOT = "7bb2fc68d9b73b168a582d890a6f952fd0c4eb20fc0e31857903909f27dfaa8f"
EXPECTED_RUNTIME = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
EXPECTED_DEPENDENCIES = "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"


def load(name: str) -> Any:
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def validate() -> dict[str, Any]:
    required = {
        "baseline-attestation.json",
        "official-source-contract-references.json",
        "acquisition-manifest.json",
        "raw-object-inventory.json",
        "raw-object-inventory-addendum-001.json",
        "publisher-checksum-results.json",
        "timestamp-unit-evidence.json",
        "timestamp-endpoint-probes.json",
        "instrument-metadata-evidence.json",
        "spot-qualification-release.json",
        "perpetual-qualification-release.json",
        "completeness-results.json",
        "funding-schedule-proof.json",
        "mark-grid-proof.json",
        "catalog-rebuild-comparison.json",
        "negative-fixture-results-final.json",
        "test-output-final.txt",
        "test-results-final.json",
        "failed-attempts.jsonl",
        "qualification-summary.json",
    }
    missing = sorted(name for name in required if not (EVIDENCE / name).is_file())
    checks: dict[str, bool] = {"required_files_present": not missing}

    tests = load("test-results-final.json")
    checks["tests_95_pass"] = (
        tests.get("status") == "PASS"
        and tests.get("tests_run") == 95
        and tests.get("passed") == 95
        and tests.get("failures") == 0
        and tests.get("errors") == 0
        and tests.get("skipped") == 0
    )
    checks["phase_totals"] = tests.get("phase_totals") == {
        "M0_REGRESSION": {"failed": 0, "passed": 32, "skipped": 0, "tests_run": 32},
        "M1_REGRESSION": {"failed": 0, "passed": 29, "skipped": 0, "tests_run": 29},
        "M2": {"failed": 0, "passed": 34, "skipped": 0, "tests_run": 34},
    }
    negative = load("negative-fixture-results-final.json")
    checks["negative_fixtures"] = (
        negative.get("status") == "PASS"
        and len(negative.get("results", {})) == 12
        and all(item.get("status") == "PASS" for item in negative.get("results", {}).values())
    )

    raw = load("raw-object-inventory.json")
    addendum = load("raw-object-inventory-addendum-001.json")
    objects = [*raw.get("objects", []), *addendum.get("objects", [])]
    blobs_ok = True
    for item in objects:
        digest = item["sha256"]
        path = ROOT / "data/raw/sha256" / digest[:2] / f"{digest}.blob"
        blobs_ok = blobs_ok and path.is_file() and sha256_file(path) == digest
    checks["raw_inventory_21_content_addressed"] = (
        raw.get("object_count") == 18
        and addendum.get("object_count") == 3
        and len(objects) == 21
        and blobs_ok
    )
    publisher = load("publisher-checksum-results.json")
    checks["publisher_checksums_5"] = (
        publisher.get("status") == "PASS"
        and len(publisher.get("results", [])) == 5
        and all(
            item.get("publisher_sha256") == item.get("local_sha256")
            for item in publisher.get("results", [])
        )
    )

    summary = load("qualification-summary.json")
    release_checks = []
    for profile, evidence_name in (
        ("spot", "spot-qualification-release.json"),
        ("perpetual", "perpetual-qualification-release.json"),
    ):
        evidence_release = DatasetRelease.from_json_bytes((EVIDENCE / evidence_name).read_bytes())
        tracked_path = ROOT / summary["release_files"][profile]
        tracked_release = DatasetRelease.from_json_bytes(tracked_path.read_bytes())
        release_checks.append(
            evidence_release == tracked_release
            and evidence_release.dataset_release_id == summary[f"{profile}_dataset_release_id"]
        )
    checks["dataset_release_manifests"] = all(release_checks)
    checks["qualification_no_run"] = (
        summary.get("status") == "PASS"
        and summary.get("strategy_run") is False
        and summary.get("official_run") is False
        and summary.get("m3_started") is False
    )
    checks["funding_mark_catalog"] = all(
        load(name).get("status") == "PASS"
        for name in (
            "funding-schedule-proof.json",
            "mark-grid-proof.json",
            "catalog-rebuild-comparison.json",
            "timestamp-endpoint-probes.json",
        )
    )
    checks["locked_hashes"] = (
        sha256_file(ROOT / "SSOT.md") == EXPECTED_SSOT
        and sha256_file(ROOT / "runtime.lock.json") == EXPECTED_RUNTIME
        and sha256_file(ROOT / "requirements.lock.txt") == EXPECTED_DEPENDENCIES
    )
    failures = [json.loads(line) for line in (EVIDENCE / "failed-attempts.jsonl").read_text().splitlines()]
    checks["failed_attempts_retained"] = len(failures) >= 3
    changed_old_evidence = subprocess.run(
        ["git", "diff", "--name-only", "--", "evidence/m0", "evidence/m1"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    checks["m0_m1_evidence_unchanged"] = not changed_old_evidence
    status = "PASS" if all(checks.values()) else "FAIL"
    return {
        "schema": "m2-evidence-validation-v1",
        "status": status,
        "checks": checks,
        "missing_files": missing,
        "evidence_directory": str(EVIDENCE.relative_to(ROOT)),
    }


def main() -> int:
    result = validate()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
