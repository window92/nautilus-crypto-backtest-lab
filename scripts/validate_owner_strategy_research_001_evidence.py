#!/usr/bin/env python3
"""Fail-closed validator for OWNER_STRATEGY_RESEARCH_001 evidence."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from crypto_lab.hashing import canonical_sha256
from crypto_lab.historical_contracts import validate_validator_contract


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/research/owner-strategy-research-001"
EXPECTED_LOCKS = {
    "SSOT.md": "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99",
    "runtime.lock.json": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "requirements.lock.txt": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
}
EXPECTED_RESULTS = {
    "spot_candidate_a": ("spot", "CANDIDATE_A", 26, 3),
    "spot_candidate_b": ("spot", "CANDIDATE_B", 26, 3),
    "spot_benchmark": ("spot", "BENCHMARK", 1, 0),
    "perpetual_candidate_a": ("perpetual", "CANDIDATE_A", 26, 6),
    "perpetual_candidate_b": ("perpetual", "CANDIDATE_B", 26, 6),
    "perpetual_benchmark": ("perpetual", "BENCHMARK", 1, 0),
}
REQUIRED = {
    "authoritative-history.json",
    "baseline-attestation.json",
    "data-and-profile-identities.json",
    "deterministic-replay.json",
    "deterministic-replay/README.md",
    "evidence-inventory.json",
    "failed-attempts.jsonl",
    "final-content-manifest.json",
    "frozen-input-manifest.json",
    "frozen-protocols/perpetual.json",
    "frozen-protocols/spot.json",
    "historical-sma20-comparison.json",
    "mechanical-integrity.json",
    "mechanical-integrity/README.md",
    "multiple-testing.json",
    "native-research-metrics.json",
    "owner-authorization.json",
    "owner-report/README.md",
    "perpetual-report/README.md",
    "research-basis.json",
    "research-eligibility.json",
    "spot-report/README.md",
    "strategy-identities.json",
    "test-output.txt",
    "test-results.json",
    *{f"run-results/{key}.json" for key in EXPECTED_RESULTS},
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
        content = (EVIDENCE / relative).read_bytes()
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
    if (
        manifest.get("file_count_excluding_manifest") != len(inventory)
        or manifest.get("content_identity") != canonical_sha256(inventory)
    ):
        failures.append("manifest_identity_mismatch")
    for name in ("final_holdout_used", "real_profitability_claim", "optimization_performed"):
        if manifest.get(name) is not False:
            failures.append(f"manifest_prohibition_failed:{name}")

    evidence_inventory = load("evidence-inventory.json")
    if (
        evidence_inventory.get("status") != "PASS"
        or evidence_inventory.get("raw_data_copied_to_git") is not False
        or evidence_inventory.get("duckdb_or_catalog_payload_copied_to_git") is not False
    ):
        failures.append("evidence_inventory_invalid")

    eligibility = load("research-eligibility.json")
    if (
        eligibility.get("status") != "INELIGIBLE_FOR_REAL_PROFITABILITY_CLAIM"
        or eligibility.get("research_purpose") != "EXPLORATORY_OPERATIONAL_VALIDATION"
        or eligibility.get("research_intent") != "EXPLORATORY"
        or eligibility.get("final_holdout_used") is not False
        or eligibility.get("real_profitability_claim") is not False
        or eligibility.get("optimization_performed") is not False
        or eligibility.get("winner_selected") is not False
        or eligibility.get("next_family_or_holdout_authorized") is not False
    ):
        failures.append("research_eligibility_invalid")
    multiple = load("multiple-testing.json")
    if (
        multiple.get("status") != "PASS"
        or multiple.get("policy") != "HOLM_BONFERRONI"
        or multiple.get("candidate_budget") != 2
        or multiple.get("candidate_profile_trial_count") != 4
        or multiple.get("benchmark_trial_count") != 2
        or multiple.get("candidate_addition_after_results") is not False
        or multiple.get("publishable_winner_selected") is not False
    ):
        failures.append("multiple_testing_invalid")

    replay = load("deterministic-replay.json")
    mechanical = load("mechanical-integrity.json")
    native_metrics = load("native-research-metrics.json")
    for key, (profile, kind, signal_count, completed_count) in EXPECTED_RESULTS.items():
        value = load(f"run-results/{key}.json")
        metrics = value.get("metrics", {})
        if (
            value.get("status") != "PASS"
            or value.get("profile_group") != profile
            or value.get("trial_kind") != kind
            or value.get("final_holdout_used") is not False
            or value.get("real_profitability_claim") is not False
            or value.get("optimization_performed") is not False
            or value.get("research_eligibility")
            != "INELIGIBLE_FOR_REAL_PROFITABILITY_CLAIM"
            or metrics.get("signals_or_benchmark_entries") != signal_count
            or metrics.get("native_completed_units") != completed_count
            or metrics.get("orders", 0) <= 0
            or metrics.get("fills") != metrics.get("orders")
            or metrics.get("gross_pnl") != "UNDEFINED_NATIVE_GROSS_PNL_NOT_EXPOSED"
            or metrics.get("terminal_position", {}).get("open") is not True
            or metrics.get("sample_adequacy") != "NOT_APPLICABLE"
            or metrics.get("monte_carlo_status") != "NOT_APPLICABLE"
            or metrics.get("checker") != "CHECK_PASS"
            or metrics.get("replay") != "PASS"
        ):
            failures.append(f"run_result_invalid:{key}")
        if kind != "BENCHMARK" and (
            metrics.get("completed_daily_bars") != 212
            or metrics.get("weekly_decisions") != 26
        ):
            failures.append(f"candidate_schedule_invalid:{key}")
        market = value.get("market_state_acceptance", {})
        if any(
            market.get(name) != 0
            for name in (
                "precision_skipped_bars",
                "rejected_precision_events",
                "missing_market_state",
                "fatal_runtime_diagnostics",
            )
        ):
            failures.append(f"market_state_invalid:{key}")
        network = value.get("offline_enforcement", {})
        if (
            network.get("attempts") != []
            or network.get("process_isolation", {}).get("external_endpoint_contacted") is not False
        ):
            failures.append(f"offline_invalid:{key}")
        if replay.get("results", {}).get(key, {}).get("result") != "PASS":
            failures.append(f"replay_invalid:{key}")
        if mechanical.get("results", {}).get(key, {}).get("checker") != "CHECK_PASS":
            failures.append(f"checker_invalid:{key}")
        if key not in native_metrics.get("results", {}):
            failures.append(f"native_metrics_missing:{key}")
        for role, binding in value.get("source_bindings", {}).items():
            path = ROOT / binding.get("path", "")
            if (
                not path.is_file()
                or path.stat().st_size != binding.get("size_bytes")
                or sha256_file(path) != binding.get("sha256")
            ):
                failures.append(f"source_binding_invalid:{key}:{role}")

    history = load("authoritative-history.json")
    if (
        history.get("status") != "PASS"
        or history.get("terminal_attempt_count") != 7
        or history.get("completed_attempt_count") != 6
        or history.get("failed_attempt_count") != 1
    ):
        failures.append("authoritative_history_invalid")
    sma = load("historical-sma20-comparison.json")
    if (
        sma.get("status") != "DISCLOSED_EXPOSED_BENCHMARK_ONLY"
        or sma.get("rerun") is not False
        or sma.get("spot_net_pnl") != "-751.78721000 USDT"
        or sma.get("perpetual_net_pnl") != "-3010.78713375 USDT"
    ):
        failures.append("historical_sma20_disclosure_invalid")

    tests = load("test-results.json")
    if (
        tests.get("status") != "PASS"
        or tests.get("unique_tests", 0) <= 0
        or tests.get("test_execution_occurrences", 0) < tests.get("unique_tests", 0) * 3
        or any(tests.get(name) != 0 for name in ("failures", "errors", "skips", "xfail"))
        or any(value != "PASS" for value in tests.get("gates", {}).values())
    ):
        failures.append("test_results_invalid")
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
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", "621caa3d71106f85f10015c54d0e31e75e0d42cd", "HEAD"],
        cwd=ROOT,
        check=False,
    ).returncode:
        failures.append("baseline_commit_not_ancestor")

    status = "PASS" if not failures else "FAIL"
    print(
        json.dumps(
            {
                "schema": "owner-strategy-research-001-evidence-validation-v1",
                "status": status,
                "file_count": len(present),
                "run_result_count": len(EXPECTED_RESULTS),
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
