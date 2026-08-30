#!/usr/bin/env python3
"""Fail-closed validator for NATIVE_RESEARCH_METRICS_READINESS_001."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from crypto_lab.hashing import canonical_sha256
from crypto_lab.historical_contracts import validate_validator_contract


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/repair/native-research-metrics-readiness-001"
HISTORICAL = ROOT / "evidence/research/owner-smoke-002-replacement-001"
EXPECTED_LOCKS = {
    "SSOT.md": "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99",
    "runtime.lock.json": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "requirements.lock.txt": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
}
REQUIRED = {
    "average-trade-disposition.json",
    "baseline-attestation.json",
    "calmar-disposition.json",
    "failed-attempts.jsonl",
    "final-content-manifest.json",
    "gross-pnl-disposition.json",
    "historical-integrity.json",
    "historical-run-reconciliation.json",
    "monte-carlo-readiness.json",
    "netting-snapshot-contract.json",
    "owner-report/README.md",
    "pinned-nautilus-apis.json",
    "qualification-results.json",
    "realized-pnl-semantics.json",
    "sample-adequacy-readiness.json",
    "test-output.txt",
    "test-results.json",
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
    failures: list[str] = []
    historical_contract = validate_validator_contract(
        Path(__file__).name,
        repository_root=ROOT,
    )
    present = {
        path.relative_to(EVIDENCE).as_posix()
        for path in EVIDENCE.rglob("*")
        if path.is_file()
    }
    missing = sorted(REQUIRED - present)
    if missing:
        failures.append("missing:" + ",".join(missing))
    for relative in sorted(present):
        path = EVIDENCE / relative
        content = path.read_bytes()
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            failures.append(f"non_utf8:{relative}")
        if b"\r\n" in content:
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
    inventory = {
        relative: {
            "sha256": sha256_file(EVIDENCE / relative),
            "size_bytes": (EVIDENCE / relative).stat().st_size,
        }
        for relative in sorted(present - {"final-content-manifest.json"})
    }
    if manifest.get("status") != "PASS" or manifest.get("files") != inventory:
        failures.append("manifest_inventory_mismatch")
    if manifest.get("file_count_excluding_manifest") != len(inventory):
        failures.append("manifest_file_count_mismatch")
    for name in (
        "strategy_rerun",
        "optimization_run",
        "final_holdout_used",
        "historical_run_evidence_modified",
        "project_trade_pairing_used",
        "project_pnl_engine_used",
        "raw_data_payloads_committed",
        "duckdb_payloads_committed",
        "catalog_payloads_committed",
        "secrets_present",
    ):
        if manifest.get(name) is not False:
            failures.append(f"manifest_prohibition_failed:{name}")

    reconciliation = load("historical-run-reconciliation.json")
    profiles = reconciliation.get("profiles", {})
    for profile, expected in (("spot", 13), ("perpetual", 27)):
        item = profiles.get(profile, {})
        if (
            item.get("native_completed_cycle_count") != expected
            or item.get("position_closed_callback_count") != expected
            or item.get("native_terminal_open_position_count") != 1
            or item.get("terminal_open_excluded_from_completed_sample") is not True
            or item.get("manual_fill_pairing_used") is not False
        ):
            failures.append(f"native_cardinality_invalid:{profile}")
    if (
        profiles["spot"].get("returns_timestamp_matches_position_closes") is not True
        or profiles["spot"].get("native_returns_basis") != "POSITION_RETURNS_FALLBACK"
        or profiles["perpetual"].get("returns_timestamps_are_utc_daily") is not True
        or profiles["perpetual"].get("returns_timestamp_position_close_overlap_count") != 0
        or profiles["perpetual"].get("native_returns_basis")
        != "PORTFOLIO_DAILY_ACCOUNT_RETURNS"
    ):
        failures.append("native_returns_basis_invalid")

    gross = load("gross-pnl-disposition.json")
    if (
        gross.get("status") != "UNDEFINED_NATIVE_GROSS_PNL_NOT_EXPOSED"
        or gross.get("net_plus_fees_plus_funding_reconstruction_used") is not False
    ):
        failures.append("gross_pnl_disposition_invalid")
    average = load("average-trade-disposition.json")
    if (
        average.get("status") != "PASS"
        or average["spot"].get("completed_native_units") != 13
        or average["perpetual"].get("completed_native_units") != 27
    ):
        failures.append("average_trade_disposition_invalid")
    calmar = load("calmar-disposition.json")
    if (
        calmar.get("status") != "PASS"
        or calmar["spot"].get("status") != "UNDEFINED"
        or calmar["perpetual"].get("status") != "NATIVE"
        or calmar.get("project_calmar_calculation") is not False
    ):
        failures.append("calmar_disposition_invalid")
    for relative in (
        "baseline-attestation.json",
        "pinned-nautilus-apis.json",
        "netting-snapshot-contract.json",
        "historical-run-reconciliation.json",
        "realized-pnl-semantics.json",
        "average-trade-disposition.json",
        "sample-adequacy-readiness.json",
        "monte-carlo-readiness.json",
        "calmar-disposition.json",
        "qualification-results.json",
    ):
        if load(relative).get("status") != "PASS":
            failures.append(f"non_pass:{relative}")

    tests = load("test-results.json")
    if (
        tests.get("status") != "PASS"
        or tests.get("unique_tests", 0) <= 0
        or tests.get("test_execution_occurrences", 0) < tests.get("unique_tests", 0) * 3
        or any(value != "PASS" for value in tests.get("gates", {}).values())
        or any(
            run.get("failures", 0) not in {0, None}
            or run.get("errors", 0) not in {0, None}
            or run.get("skips", run.get("skipped", 0)) != 0
            or run.get("xfail", 0) != 0
            for run in tests.get("test_runs", {}).values()
        )
    ):
        failures.append("test_results_invalid")

    historical_inventory = load("historical-integrity.json")
    current_historical = {
        path.relative_to(HISTORICAL).as_posix(): {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
        for path in sorted(HISTORICAL.rglob("*"))
        if path.is_file()
    }
    if (
        historical_inventory.get("files") != current_historical
        or historical_inventory.get("inventory_identity")
        != canonical_sha256(current_historical)
    ):
        failures.append("historical_inventory_changed")
    changed_historical = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", str(HISTORICAL.relative_to(ROOT))],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if changed_historical:
        failures.append("historical_evidence_modified")
    if not historical_contract.acceptable:
        failures.append("historical_contract_snapshot_invalid")
    failed_attempts = [
        json.loads(line)
        for line in (EVIDENCE / "failed-attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    if not failed_attempts:
        failures.append("failed_attempts_missing")
    if any(path.suffix in {".duckdb", ".parquet", ".zip"} for path in EVIDENCE.rglob("*")):
        failures.append("forbidden_payload_in_evidence")

    status = "PASS" if not failures else "FAIL"
    print(
        json.dumps(
            {
                "schema": "native-research-metrics-readiness-evidence-validation-v1",
                "status": status,
                "file_count": len(present),
                "failed_attempt_count": len(failed_attempts),
                "failures": failures,
                "historical_contract": historical_contract.to_builtins(),
            },
            sort_keys=True,
        ),
    )
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
