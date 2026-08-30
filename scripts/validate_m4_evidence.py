#!/usr/bin/env python3
"""Read-only validation for the additive M4 and Final V1 evidence epoch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.historical_contracts import validate_validator_contract
from crypto_lab.reporting import PerformanceDiagnostics
from crypto_lab.reporting import ReportOutput
from crypto_lab.research import HoldoutLockSnapshot
from crypto_lab.research import MonteCarloResult
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import TrialJournal


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / "evidence/m4/m4-acceptance-001"
EXPECTED_LOCKS = {
    "ssot_sha256": "7bb2fc68d9b73b168a582d890a6f952fd0c4eb20fc0e31857903909f27dfaa8f",
    "runtime_lock_sha256": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "dependency_lock_sha256": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
}
EXPECTED_M3_REGISTRY = "d6124dd7d225818f0de212d74f7d4aae5e3bf08c9f8ff342435baac6228ba6de"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_manifest(directory: Path) -> bool:
    manifest = _json(directory / "evidence_manifest.json")
    if canonical_sha256(manifest["entries"]) != manifest["inventory_content_sha256"]:
        return False
    return all(
        (directory / item["path"]).is_file()
        and (directory / item["path"]).stat().st_size == item["byte_size"]
        and sha256_file(directory / item["path"]) == item["sha256"]
        for item in manifest["entries"]
    )


def validate(evidence: Path = DEFAULT_EVIDENCE) -> dict[str, Any]:
    evidence = Path(evidence)
    checks: dict[str, bool] = {}
    historical_contract = validate_validator_contract(
        Path(__file__).name,
        repository_root=ROOT,
    )
    checks["historical_contract_snapshot"] = historical_contract.acceptable
    required = (
        "baseline-attestation.json",
        "preserved-history-before.json",
        "preserved-history-after.json",
        "m3-downstream-validation.json",
        "research/trials.jsonl",
        "research/holdout_lock.json",
        "research/monte-carlo-result.json",
        "research/performance-diagnostics.json",
        "research/claim-gate-matrix.json",
        "research/reports/synthetic-contract.json",
        "research/reports/synthetic-contract.md",
        "research/reports/m3-qualification-only.json",
        "research/reports/m3-qualification-only.md",
        "final-v1/synthetic-fixtures.json",
        "final-v1/deterministic-replay.json",
        "final-v1/final-v1-end-to-end.json",
        "test-suite/test-results.json",
        "test-suite/independent-discovery.json",
        "failed-attempts.jsonl",
        "final-acceptance-summary.json",
        "final-content-manifest.json",
    )
    checks["required_artifacts"] = all((evidence / name).is_file() for name in required)
    if not checks["required_artifacts"]:
        return {"schema": "m4-evidence-validation-v1", "status": "FAIL", "checks": checks}

    baseline = _json(evidence / "baseline-attestation.json")
    checks["baseline_identity"] = (
        all(baseline.get(key) == value for key, value in EXPECTED_LOCKS.items())
        and baseline.get("m3_registry_identity") == EXPECTED_M3_REGISTRY
        and baseline.get("clean_worktree") is True
        and baseline.get("head") == baseline.get("origin_main")
        and baseline.get("network_enabled") is False
    )
    before = _json(evidence / "preserved-history-before.json")
    after = _json(evidence / "preserved-history-after.json")
    checks["historical_evidence_raw_and_releases_unchanged"] = (
        before["content_sha256"] == after["content_sha256"]
        and before["entries"] == after["entries"]
    )

    protocol_paths = sorted((evidence / "research/protocols").glob("*.json"))
    checks["strict_research_protocol"] = len(protocol_paths) == 1
    if protocol_paths:
        protocol = ResearchProtocol.from_json_bytes(protocol_paths[0].read_bytes())
        checks["strict_research_protocol"] = checks["strict_research_protocol"] and (
            protocol_paths[0].stem == protocol.protocol_id
            and (protocol_paths[0].with_suffix(".sha256")).read_text().strip()
            == protocol.protocol_id
        )
    records = TrialJournal(evidence / "research/trials.jsonl").read_records()
    terminal = {item.trial_id: item.state.value for item in records if item.finished_at_utc != "NOT_APPLICABLE"}
    checks["trial_history_complete_and_retains_failure"] = (
        terminal == {"m4-synthetic-failed": "FAILED", "m4-synthetic-completed": "COMPLETED"}
        and len(records) == 6
    )
    holdout = HoldoutLockSnapshot.from_json_bytes(
        (evidence / "research/holdout_lock.json").read_bytes(),
    )
    checks["holdout_consumed_once"] = len(holdout.entries) == 1
    monte_carlo = MonteCarloResult.from_json_bytes(
        (evidence / "research/monte-carlo-result.json").read_bytes(),
    )
    diagnostics = PerformanceDiagnostics.from_json_bytes(
        (evidence / "research/performance-diagnostics.json").read_bytes(),
    )
    checks["monte_carlo_and_diagnostics_complete"] = (
        monte_carlo.status.value == "COMPLETED"
        and diagnostics.monte_carlo_status.value == "COMPLETED"
        and diagnostics.completed_trade_count.status != "UNDEFINED"
    )
    synthetic_report = ReportOutput.from_json_bytes(
        (evidence / "research/reports/synthetic-contract.json").read_bytes(),
    )
    qualification_report = ReportOutput.from_json_bytes(
        (evidence / "research/reports/m3-qualification-only.json").read_bytes(),
    )
    checks["reports_never_make_real_fixture_claim"] = (
        synthetic_report.claim_evaluation.research_eligibility.value == "ELIGIBLE"
        and synthetic_report.json_payload["profitability_claim_is_real"] is False
        and qualification_report.claim_evaluation.research_eligibility.value == "INELIGIBLE"
        and qualification_report.json_payload["profitability_claim_is_real"] is False
    )
    claim_matrix = _json(evidence / "research/claim-gate-matrix.json")
    checks["claim_gate_separation"] = (
        claim_matrix["synthetic_contract_fixture"]["research_eligibility"] == "ELIGIBLE"
        and claim_matrix["synthetic_contract_fixture"]["real_profitability_claim"] is False
        and claim_matrix["m3_qualification_evidence"]["research_eligibility"] == "INELIGIBLE"
    )

    real_root = evidence / "final-v1/real-data"
    summaries = {
        name: _json(real_root / "attempt-summaries" / f"{name}.json")
        for name in ("spot-primary", "spot-replay", "perpetual-primary", "perpetual-replay")
    }
    checks["real_data_qualification_runs"] = all(
        item["state"] == "COMPLETED"
        and item["checker_outcome"] == "CHECK_PASS"
        and not item["failure_codes"]
        and _run_manifest(real_root / item["evidence_dir"])
        for item in summaries.values()
    )
    replay = _json(evidence / "final-v1/deterministic-replay.json")
    checks["fresh_process_deterministic_replay"] = all(
        item["status"] == "PASS"
        and item["primary_semantic_digest"] == item["replay_semantic_digest"]
        for item in replay.values()
    )
    synthetic = _json(evidence / "final-v1/synthetic-fixtures.json")
    checks["synthetic_spot_perpetual_and_native_funding"] = (
        synthetic["status"] == "PASS"
        and synthetic["executed_test_cases"] == 4
        and synthetic["native_funding_included"] is True
    )
    end_to_end = _json(evidence / "final-v1/final-v1-end-to-end.json")
    checks["final_v1_end_to_end"] = end_to_end["status"] == "PASS" and all(
        item["status"] == "PASS" for item in end_to_end["fixtures"]
    )
    tests = _json(evidence / "test-suite/test-results.json")
    discovery = _json(evidence / "test-suite/independent-discovery.json")
    accepted_test_inventory = (
        tests["unique_executable_test_cases"],
        tests["test_execution_occurrences"],
        discovery["executed_test_cases"],
    ) in {
        (167, 168, 167),  # immutable original M4 acceptance
        (206, 207, 206),  # V1 post-build Repair Epoch AUD-001..009
    }
    checks["complete_regression"] = (
        tests["status"] == "PASS"
        and accepted_test_inventory
        and tests["failures"] == tests["errors"] == tests["skipped"] == 0
        and discovery["status"] == "PASS"
    )
    checks["failed_attempts_preserved"] = bool(
        (evidence / "failed-attempts.jsonl").read_bytes().strip(),
    )
    summary = _json(evidence / "final-acceptance-summary.json")
    checks["acceptance_summary"] = (
        summary["status"] == "PASS"
        and summary["actual_owner_research_study_executed"] is False
        and summary["real_profitability_claim_made"] is False
        and summary["m0_m3_semantics_modified"] is False
    )
    manifest = _json(evidence / "final-content-manifest.json")
    manifest_ok = canonical_sha256(manifest["entries"]) == manifest["content_sha256"]
    for item in manifest["entries"]:
        path = evidence / item["path"]
        manifest_ok = (
            manifest_ok
            and path.is_file()
            and path.stat().st_size == item["byte_size"]
            and sha256_file(path) == item["sha256"]
        )
    checks["final_content_manifest"] = manifest_ok
    return {
        "schema": "m4-evidence-validation-v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "historical_contract": historical_contract.to_builtins(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate(args.evidence)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.write_text(payload, encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
