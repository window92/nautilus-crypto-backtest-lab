#!/usr/bin/env python3
"""Fail-closed validator for Instrument/funding-checker repair evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/repair/instrument-representation-funding-checker-001"
REQUIRED = {
    "baseline-attestation.json",
    "before-repair-reproduction.json",
    "pinned-nautilus-precision-contract.json",
    "source-precision-audit.json",
    "representation-vs-order-grid.json",
    "instrument-metadata-before-after.json",
    "value-continuity.json",
    "full-nautilus-ingestion.json",
    "sentinel-fill-qualification.json",
    "order-grid-negative-controls.json",
    "funding-runtime-binding.json",
    "funding-mark-asof-validation.json",
    "checker-regression.json",
    "dataset-release-identities.json",
    "catalog-identities.json",
    "deterministic-rebuild.json",
    "test-results.json",
    "test-output.txt",
    "failed-attempts.jsonl",
    "replacement-owner-smoke-validation.json",
    "final-content-manifest.json",
    "owner-report/README.md",
}
EXPECTED_LOCKS = {
    "SSOT.md": "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99",
    "runtime.lock.json": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "requirements.lock.txt": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(relative: str) -> Any:
    return json.loads((EVIDENCE / relative).read_text(encoding="utf-8"))


def main() -> int:
    present = {
        str(path.relative_to(EVIDENCE))
        for path in EVIDENCE.rglob("*")
        if path.is_file()
    }
    failures: list[str] = []
    missing = sorted(REQUIRED - present)
    if missing:
        failures.append("missing:" + ",".join(missing))
    for relative in sorted(present):
        path = EVIDENCE / relative
        try:
            path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            failures.append(f"non_utf8:{relative}")
        if b"\r\n" in path.read_bytes():
            failures.append(f"crlf:{relative}")
        if relative.endswith(".json"):
            try:
                load(relative)
            except Exception as exc:
                failures.append(f"invalid_json:{relative}:{exc}")
    if failures:
        print(json.dumps({"status": "FAIL", "failures": failures}, sort_keys=True))
        return 1

    manifest = load("final-content-manifest.json")
    expected_inventory = manifest["files"]
    actual_inventory = {
        relative: {
            "size_bytes": (EVIDENCE / relative).stat().st_size,
            "sha256": sha256_file(EVIDENCE / relative),
        }
        for relative in sorted(present - {"final-content-manifest.json"})
    }
    if expected_inventory != actual_inventory:
        failures.append("manifest_inventory_mismatch")
    if manifest.get("status") != "PASS" or any(
        manifest.get(name) is not False
        for name in ("raw_archives_committed", "duckdb_payloads_committed", "catalog_payloads_committed")
    ):
        failures.append("manifest_contract_invalid")
    if manifest.get("secrets_present") is not False:
        failures.append("secret_attestation_invalid")

    for relative in sorted(REQUIRED):
        if not relative.endswith(".json") or relative == "final-content-manifest.json":
            continue
        value = load(relative)
        if relative not in {"source-precision-audit.json"} and value.get("status") != "PASS":
            failures.append(f"non_pass:{relative}")
    precision = load("source-precision-audit.json")
    if (
        precision.get("numeric_market_value_changed") is not False
        or precision["spot"]["execution_price_representation_precision"] != 2
        or precision["spot"]["instrument_size_representation_precision"] != 6
        or precision["perpetual"]["instrument_price_representation_precision"] != 8
        or precision["perpetual"]["bar_volume_and_order_quantity_representation_precision"] != 3
    ):
        failures.append("precision_audit_invalid")

    ingestion = load("full-nautilus-ingestion.json")
    spot = ingestion["spot"]
    perp = ingestion["perpetual"]
    if (
        spot["accepted_executable_bars"] != spot["expected_executable_bars"]
        or spot["expected_executable_bars"] != 304_596
        or perp["expected_executable_bars"] != perp["accepted_executable_bars"]
        or perp["expected_executable_bars"] != 305_280
        or perp["expected_mark_updates"] != perp["accepted_mark_updates"]
        or perp["expected_mark_updates"] != 305_280
        or any(
            item[key] != 0
            for item in (spot, perp)
            for key in (
                "precision_skipped_bars",
                "rejected_precision_events",
                "missing_market_state",
                "fatal_runtime_diagnostics",
            )
        )
    ):
        failures.append("full_ingestion_invalid")

    continuity = load("value-continuity.json")
    if (
        continuity.get("canonical_market_numeric_values_changed") is not False
        or continuity.get("raw_canonical_decimal_spellings_changed") is not False
        or continuity.get("rounding_or_truncation_used") is not False
        or not all(
            item["exact_rows_equal"]
            for item in continuity["canonical_table_comparisons"].values()
        )
    ):
        failures.append("market_value_continuity_invalid")

    regression = load("checker-regression.json")
    if (
        regression["records"]["spot"]["regenerated_current_outcome"] != "CHECK_FAIL"
        or regression["records"]["spot"]["market_state_check"]["no_market_rejection_count"] != 89
        or regression["records"]["perpetual"]["market_state_check"]["no_market_rejection_count"] != 180
        or any(
            item["historical_bytes_mutated"]
            for item in regression["records"].values()
        )
    ):
        failures.append("checker_regression_invalid")

    tests = load("test-results.json")
    if (
        tests.get("status") != "PASS"
        or tests.get("unique_tests") != 268
        or tests.get("test_execution_occurrences") != 960
        or any(value != "PASS" for value in tests["gates"].values())
        or any(
            run.get("failures", 0) not in {0, None}
            or run.get("errors", 0) not in {0, None}
            or run.get("skips", 0) not in {0, None}
            or run.get("xfail", 0) not in {0, None}
            for run in tests["test_runs"].values()
        )
    ):
        failures.append("test_results_invalid")

    observed_locks = {name: sha256_file(ROOT / name) for name in EXPECTED_LOCKS}
    if observed_locks != EXPECTED_LOCKS:
        failures.append("locked_identity_changed")
    forbidden_suffixes = {".duckdb", ".parquet", ".zip"}
    if any(path.suffix in forbidden_suffixes for path in EVIDENCE.rglob("*")):
        failures.append("payload_committed_to_evidence")
    failed_lines = [
        json.loads(line)
        for line in (EVIDENCE / "failed-attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if not failed_lines:
        failures.append("failed_attempts_missing")

    replacement = load("replacement-owner-smoke-validation.json")
    if (
        replacement.get("status") != "PASS"
        or replacement.get("spot", {}).get("checker") != "CHECK_PASS"
        or replacement.get("spot", {}).get("replay") != "PASS"
        or replacement.get("perpetual", {}).get("checker") != "CHECK_PASS"
        or replacement.get("perpetual", {}).get("replay") != "PASS"
        or replacement.get("final_holdout_used") is not False
        or replacement.get("real_profitability_claim") is not False
    ):
        failures.append("replacement_owner_smoke_invalid")

    status = "PASS" if not failures else "FAIL"
    print(
        json.dumps(
            {
                "schema": "instrument-repair-evidence-validation-v1",
                "status": status,
                "file_count": len(present),
                "failed_attempt_count": len(failed_lines),
                "failures": failures,
            },
            sort_keys=True,
        ),
    )
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
