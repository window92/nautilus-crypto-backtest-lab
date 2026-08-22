#!/usr/bin/env python3
"""Validate executed Repair gates and assemble additive evidence without rerunning them."""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from scripts.validate_m4_evidence import validate as validate_final_v1


ROOT = Path(__file__).resolve().parents[1]
REPAIR = ROOT / "evidence/repair/v1-post-build-001"
BASELINE = "0507549ad60ae63d9ae1e93d6852a11d4660ce0c"
LOCKS = {
    "SSOT.md": "7bb2fc68d9b73b168a582d890a6f952fd0c4eb20fc0e31857903909f27dfaa8f",
    "runtime.lock.json": "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd",
    "requirements.lock.txt": "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47",
}


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _copy_tree(source: Path, target: Path) -> None:
    if target.exists():
        raise FileExistsError(f"refusing to overwrite Repair evidence: {target}")
    shutil.copytree(source, target)


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _run_projection(run_dir: Path) -> dict[str, Any]:
    result = _json(run_dir / "nautilus_result.json")
    config = _json(run_dir / "lab_run_config.json")
    fills = [
        {
            key: row[key]
            for key in (
                "instrument_id",
                "order_side",
                "order_type",
                "last_qty",
                "last_px",
                "commission",
                "currency",
                "liquidity_side",
                "ts_event",
                "ts_init",
            )
        }
        for row in _rows(run_dir / "fills.csv")
    ]
    positions = [
        {
            key: row[key]
            for key in (
                "row_type",
                "event_index",
                "ts_event",
                "instrument_id",
                "side",
                "signed_qty",
                "quantity",
                "avg_px_open",
                "realized_pnl",
            )
        }
        for row in _rows(run_dir / "positions.csv")
    ]
    accounts = [
        {
            key: row[key]
            for key in (
                "event_index",
                "ts_event",
                "account_type",
                "currency",
                "total",
                "locked",
                "free",
                "reported",
            )
        }
        for row in _rows(run_dir / "account.csv")
    ]
    funding = (
        [
            {
                key: row[key]
                for key in (
                    "adjustment_type",
                    "instrument_id",
                    "pnl_change",
                    "quantity_change",
                    "ts_event",
                )
            }
            for row in _rows(run_dir / "funding.csv")
        ]
        if (run_dir / "funding.csv").is_file()
        else []
    )
    return {
        "config_sha256": _json(run_dir / "status.json").get("config_sha256", config.get("config_sha256")),
        "semantic_digest": result["semantic_digest"],
        "fills": fills,
        "positions": positions,
        "accounts": accounts,
        "funding": funding,
        "mark_price_count": result["mark_price_count"],
        "funding_rate_count": result["funding_rate_count"],
        "terminal_portfolio": result["terminal_portfolio"],
        "project_fee_postings": result["project_fee_postings"],
        "project_funding_postings": result["project_funding_postings"],
        "project_financial_ledger": result["project_financial_ledger"],
        "oms_type": config["nautilus_venue_config"]["oms_type"],
        "terminal_policy": config["terminal_policy"],
        "checker": _json(run_dir / "checker.json")["outcome"],
    }


def _only_run(root: Path) -> Path:
    matches = tuple(path for path in root.iterdir() if path.is_dir())
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one Run evidence directory below {root}")
    return matches[0]


def _test_statuses(m4: dict[str, Any]) -> dict[str, str]:
    return {item["test_id"]: item["status"] for item in m4["unique_tests"]}


def _matching_tests(statuses: dict[str, str], fragment: str) -> tuple[str, ...]:
    matches = tuple(sorted(test_id for test_id in statuses if fragment in test_id))
    if not matches or any(statuses[test_id] != "PASS" for test_id in matches):
        raise RuntimeError(f"required Repair tests did not all pass: {fragment}")
    return matches


def _financial_equivalence(
    final_output: Path,
    replay_root: Path,
    statuses: dict[str, str],
) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    golden_tests = tuple(
        test_id
        for test_id, status in statuses.items()
        if status == "PASS"
        and (
            "tests.golden.test_m3_real_values" in test_id
            or "tests.golden.test_m3_profile_contracts" in test_id
            or "tests.qualification.test_m3_real_profiles" in test_id
        )
    )
    if len(golden_tests) != 12:
        raise RuntimeError("M3 golden/qualification test inventory changed unexpectedly")
    for profile in ("spot", "perpetual"):
        fresh = _only_run(final_output / f"final-v1/real-data/runs/{profile}-primary")
        historical = _only_run(
            ROOT / f"evidence/m4/m4-acceptance-001/final-v1/real-data/runs/{profile}-primary",
        )
        third_summary = _json(replay_root / f"{profile}.json")
        third = Path(third_summary["evidence_dir"]).resolve(strict=True)
        fresh_projection = _run_projection(fresh)
        historical_projection = _run_projection(historical)
        third_projection = _run_projection(third)
        equivalent = fresh_projection == historical_projection == third_projection
        if not equivalent:
            raise RuntimeError(f"M3 {profile} financial projection changed")
        if third_summary["state"] != "COMPLETED" or third_summary["checker_outcome"] != "CHECK_PASS":
            raise RuntimeError(f"M3 {profile} third replay did not complete")
        profiles[profile] = {
            "status": "PASS",
            "financial_projection_equal_historical_primary_and_third_replay": equivalent,
            "semantic_digest": fresh_projection["semantic_digest"],
            "fills": fresh_projection["fills"],
            "positions": fresh_projection["positions"],
            "funding": fresh_projection["funding"],
            "account_event_count": len(fresh_projection["accounts"]),
            "mark_price_count": fresh_projection["mark_price_count"],
            "funding_rate_count": fresh_projection["funding_rate_count"],
            "project_fee_postings": fresh_projection["project_fee_postings"],
            "project_funding_postings": fresh_projection["project_funding_postings"],
            "project_financial_ledger": fresh_projection["project_financial_ledger"],
            "oms_type": fresh_projection["oms_type"],
            "terminal_policy": fresh_projection["terminal_policy"],
            "checker": fresh_projection["checker"],
            "three_fresh_process_digests_equal": True,
        }
    return {
        "schema": "v1-repair-m3-financial-equivalence-v1",
        "status": "PASS",
        "profiles": profiles,
        "independent_golden_and_qualification_tests": sorted(golden_tests),
        "financial_contract_changed": False,
        "nautilus_financial_authority_unchanged": True,
    }


def _history_matrix(statuses: dict[str, str]) -> dict[str, Any]:
    attacks = {
        test_id.rsplit(".", 1)[-1]: "REJECTED"
        for test_id in sorted(statuses)
        if "test_aud003_004_authoritative_history" in test_id
    }
    if len(attacks) != 15 or any(
        statuses[test_id] != "PASS"
        for test_id in statuses
        if "test_aud003_004_authoritative_history" in test_id
    ):
        raise RuntimeError("authoritative history attack matrix is incomplete")
    return {
        "schema": "v1-repair-journal-holdout-replacement-matrix-v1",
        "status": "PASS",
        "attacks": attacks,
        "accepted": 0,
        "rejected": len(attacks),
    }


def _holdout_matrix(statuses: dict[str, str]) -> dict[str, Any]:
    test_id = next(
        test_id for test_id in statuses if "test_all_sixteen_spot_and_perpetual_relabels" in test_id
    )
    if statuses[test_id] != "PASS":
        raise RuntimeError("16-way Holdout mutation test failed")
    mutations = (
        "new_research_family",
        "new_hypothesis_id",
        "renamed_strategy",
        "new_seed",
        "new_branch",
        "different_dataset_release_label",
        "descendant_strategy",
        "new_protocol_id",
    )
    attempts = [
        {
            "profile": profile,
            "mutation": mutation,
            "outcome": "REJECTED",
            "failure_code": "HOLDOUT_ALREADY_CONSUMED",
            "authority": "M3_QUALIFIED_PROFILE_AND_RUN_EVIDENCE",
        }
        for profile in ("spot", "perpetual")
        for mutation in mutations
    ]
    return {
        "schema": "v1-repair-m3-holdout-mutation-matrix-v1",
        "status": "PASS",
        "evidence_test_id": test_id,
        "accepted_as_fresh": 0,
        "rejected": len(attempts),
        "attempts": attempts,
    }


def _manifest() -> dict[str, Any]:
    entries = [
        {
            "path": path.relative_to(REPAIR).as_posix(),
            "byte_size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(REPAIR.rglob("*"))
        if path.is_file() and path != REPAIR / "final-content-manifest.json"
    ]
    return {
        "schema": "v1-repair-final-content-manifest-v1",
        "entries": entries,
        "content_sha256": canonical_sha256(entries),
        "manifest_self_excluded": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-repository", type=Path, required=True)
    parser.add_argument("--m4-output", type=Path, required=True)
    parser.add_argument("--m3-output", type=Path, required=True)
    parser.add_argument("--m3-validation", type=Path, required=True)
    parser.add_argument("--final-output", type=Path, required=True)
    parser.add_argument("--reverse-output", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--owner-result", type=Path, required=True)
    parser.add_argument("--failed-final-staging", type=Path)
    parser.add_argument("--failed-owner-root", type=Path)
    parser.add_argument("--offline-entry-failure-journal", type=Path)
    parser.add_argument("--refresh-final-manifest", action="store_true")
    args = parser.parse_args()

    if args.refresh_final_manifest:
        _write(REPAIR / "final-content-manifest.json", _manifest())
        print(sha256_file(REPAIR / "final-content-manifest.json"))
        return 0

    candidate = args.candidate_repository.resolve(strict=True)
    m4_output = args.m4_output.resolve(strict=True)
    m3_output = args.m3_output.resolve(strict=True)
    m3_validation_path = args.m3_validation.resolve(strict=True)
    final_output = args.final_output.resolve(strict=True)
    reverse_output = args.reverse_output.resolve(strict=True)
    replay_root = args.replay_root.resolve(strict=True)
    owner_result_path = args.owner_result.resolve(strict=True)
    if (REPAIR / "after").exists() or (REPAIR / "final-content-manifest.json").exists():
        raise FileExistsError("Repair after-evidence already exists")

    m4 = _json(m4_output / "test-results.json")
    final_tests = _json(final_output / "test-suite/test-results.json")
    reverse = _json(reverse_output / "result.json")
    expected_counts = (206, 207, 206)
    for label, value in (("M0-M4", m4), ("Final V1", final_tests)):
        counts = (
            value["unique_executable_test_cases"],
            value["test_execution_occurrences"],
            value["independent_discovery_execution_occurrences"],
        )
        if value["status"] != "PASS" or counts != expected_counts:
            raise RuntimeError(f"{label} acceptance mismatch: {value['status']} {counts}")
        if value["failures"] or value["errors"] or value["skipped"]:
            raise RuntimeError(f"{label} contains a failed, errored, or skipped test")
    if (
        reverse["status"] != "PASS"
        or reverse["unique_discovered_test_cases"] != expected_counts[0]
        or reverse["execution_occurrences"] != expected_counts[0]
        or reverse["failures"]
        or reverse["errors"]
        or reverse["skipped"]
    ):
        raise RuntimeError("reverse deterministic order did not pass exactly")
    validation = validate_final_v1(final_output)
    if validation["status"] != "PASS":
        raise RuntimeError(f"Final V1 evidence validation failed: {validation}")
    final_e2e = _json(final_output / "final-v1/final-v1-end-to-end.json")
    if final_e2e["status"] != "PASS" or len(final_e2e["fixtures"]) != 5:
        raise RuntimeError("Final V1 five-fixture gate did not pass")
    m3_validation = _json(m3_validation_path)
    if m3_validation.get("status") != "PASS":
        raise RuntimeError("fresh M3 qualification evidence validation did not pass")
    statuses = _test_statuses(m4)
    for fragment in (
        "test_aud001_strategy_identity",
        "test_aud002_m3_holdout_exposure",
        "test_aud003_004_authoritative_history",
        "test_aud005_claim_report_resolver",
        "test_aud006_source_revision",
        "test_aud007_evidence_paths",
        "test_aud008_offline_enforcement",
        "test_aud009_owner_workflow",
    ):
        _matching_tests(statuses, fragment)

    owner_result = _json(owner_result_path)
    if not (
        owner_result.get("status") == "PASS"
        and owner_result.get("run_state") == "COMPLETED"
        and owner_result.get("checker_outcome") == "CHECK_PASS"
        and owner_result.get("claim_eligibility") == "INELIGIBLE"
        and owner_result.get("real_profitability_claim") is False
        and owner_result.get("final_holdout_used") is False
        and len(owner_result.get("commits", ())) == 5
    ):
        raise RuntimeError(f"public Owner workflow result is invalid: {owner_result}")
    journal_records = [
        json.loads(line)
        for line in (candidate / "research/trials.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    terminal = next(
        record
        for record in reversed(journal_records)
        if record["trial_id"] == owner_result["trial_id"]
    )
    owner_run = candidate / terminal["result_ref"]
    owner_engine = _json(owner_run / "nautilus_result.json")
    isolation = owner_engine["network_guard"]["process_isolation"]
    if not (
        isolation["current_process_probe_errno"] == 1
        and isolation["io_uring_probe_errno"] == 1
        and isolation["child_python_probe_errno"] == 1
        and isolation["child_native_probe_blocked"] is True
        and isolation["child_dns_probe_blocked"] is True
        and isolation["external_endpoint_contacted"] is False
    ):
        raise RuntimeError("Owner workflow did not retain complete Offline evidence")

    financial = _financial_equivalence(final_output, replay_root, statuses)
    holdout_matrix = _holdout_matrix(statuses)
    history_matrix = _history_matrix(statuses)
    candidate_source = _json(final_output / "baseline-attestation.json")
    history_before = _json(final_output / "preserved-history-before.json")
    history_after = _json(final_output / "preserved-history-after.json")
    historical_changes = _git(
        ROOT,
        "diff",
        "--name-only",
        BASELINE,
        "--",
        "evidence/m0",
        "evidence/m1",
        "evidence/m2",
        "evidence/m3",
        "evidence/m4",
        "data/releases",
    ).splitlines()
    if history_before != history_after or historical_changes:
        raise RuntimeError("historical evidence or release authority changed")
    observed_locks = {name: sha256_file(ROOT / name) for name in LOCKS}
    if observed_locks != LOCKS:
        raise RuntimeError(f"locked authority changed: {observed_locks}")

    _copy_tree(m4_output, REPAIR / "acceptance/m0-m4")
    _copy_tree(m3_output, REPAIR / "acceptance/m3-qualification")
    shutil.copy2(m3_validation_path, REPAIR / "acceptance/m3-validation.json")
    _copy_tree(final_output, REPAIR / "acceptance/final-v1")
    _copy_tree(reverse_output, REPAIR / "acceptance/reverse-order")
    _copy_tree(replay_root, REPAIR / "acceptance/third-replay")
    owner_target = REPAIR / "acceptance/owner-workflow"
    owner_target.mkdir(parents=True)
    shutil.copy2(owner_result_path, owner_target / "result.json")
    _copy_tree(owner_run, owner_target / "run-evidence")
    for source, relative in (
        (candidate / f"research/reports/{owner_result['trial_id']}.json", "report.json"),
        (candidate / f"research/reports/{owner_result['trial_id']}.md", "report.md"),
        (candidate / f"research/diagnostics/{owner_result['run_id']}.json", "diagnostics.json"),
        (candidate / f"research/workflows/{owner_result['trial_id']}.json", "workflow.json"),
        (candidate / "research/trials.jsonl", "trials.jsonl"),
        (candidate / "research/holdout_lock.json", "holdout_lock.json"),
        (candidate / "research/history_anchors.jsonl", "history_anchors.jsonl"),
    ):
        shutil.copy2(source, owner_target / relative)
    _write(
        owner_target / "ending-git-state.json",
        {
            "head": _git(candidate, "rev-parse", "HEAD"),
            "origin_main": _git(candidate, "rev-parse", "origin/main"),
            "status_porcelain": _git(candidate, "status", "--porcelain=v1"),
            "normal_pushes": len(owner_result["commits"]),
        },
    )

    _write(REPAIR / "matrices/holdout-mutations.json", holdout_matrix)
    _write(REPAIR / "matrices/authoritative-history.json", history_matrix)
    for finding, fragment, filename in (
        ("AUD-001", "test_aud001_strategy_identity", "strategy-identity.json"),
        ("AUD-005", "test_aud005_claim_report_resolver", "claim-report-forgery.json"),
        ("AUD-006", "test_aud006_source_revision", "source-revision.json"),
        ("AUD-007", "test_aud007_evidence_paths", "path-containment.json"),
    ):
        tests = _matching_tests(statuses, fragment)
        _write(
            REPAIR / f"matrices/{filename}",
            {
                "schema": "v1-repair-adversarial-test-matrix-v1",
                "finding": finding,
                "status": "PASS",
                "accepted_exploits": 0,
                "tests": [{"test_id": test_id, "outcome": "REJECTED"} for test_id in tests],
            },
        )
    _write(
        REPAIR / "matrices/offline-enforcement.json",
        {
            "schema": "v1-repair-offline-enforcement-matrix-v1",
            "status": "PASS",
            "mechanism": isolation["mechanism"],
            "current_process_socket": "BLOCKED_EPERM",
            "io_uring_network_bypass": "BLOCKED_EPERM",
            "child_python_socket": "BLOCKED_EPERM",
            "child_native_system_process": "BLOCKED",
            "child_dns_attempt": "BLOCKED",
            "inherited_socket_descriptors_closed_before_filter": isolation[
                "closed_inherited_socket_descriptors"
            ],
            "inherited_by_fork_exec": isolation["inherited_by_fork_exec"],
            "external_endpoint_contacted": isolation["external_endpoint_contacted"],
            "evidence": isolation,
            "tests": list(_matching_tests(statuses, "test_aud008_offline_enforcement")),
        },
    )
    _write(REPAIR / "financial/m3-financial-equivalence.json", financial)

    repair_tests = sorted(
        test_id for test_id in statuses if test_id.startswith("tests.adversarial.")
    )
    test_inventory = {
        "schema": "v1-repair-test-inventory-v1",
        "status": "PASS",
        "unique_test_cases": m4["unique_executable_test_cases"],
        "phase_execution_occurrences": m4["test_execution_occurrences"],
        "independent_discovery_occurrences": m4["independent_discovery_execution_occurrences"],
        "reverse_order_occurrences": reverse["execution_occurrences"],
        "repair_adversarial_unique_test_cases": len(repair_tests),
        "repeat_explanation": m4["repeated_executions"],
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "disabled_or_xfail": [],
        "repair_tests": repair_tests,
    }
    _write(REPAIR / "test-inventory.json", test_inventory)

    after = {
        "schema": "v1-repair-after-exploit-results-v1",
        "AUD-001": {"outcome": "REJECTED", "same_identity_divergence_accepted": False},
        "AUD-002": {"accepted_as_fresh": 0, "rejected": 16},
        "AUD-003": {"journal_replacement_truncation_reset": "REJECTED"},
        "AUD-004": {"holdout_replacement_rollback_reset": "REJECTED"},
        "AUD-005": {"winner_only_and_forged_official_report": "REJECTED"},
        "AUD-006": {"forged_repository_and_ref": "REJECTED_BEFORE_ENGINE"},
        "AUD-007": {"traversal_symlink_collision": "REJECTED_BEFORE_ESCAPE_WRITE"},
        "AUD-008": {"child_and_native_network_access": "BLOCKED"},
        "AUD-009": {
            "public_interface_only": "PASS",
            "final_holdout_used": False,
            "owner_study_executed": False,
            "real_profitability_claim": False,
        },
        "repair_tests_all_pass": all(statuses[test_id] == "PASS" for test_id in repair_tests),
    }
    _write(REPAIR / "after/exploit-results.json", after)
    _write(
        REPAIR / "finding-to-repair-matrix.json",
        {
            "schema": "v1-repair-finding-to-repair-matrix-v1",
            "scope": "AUD-001_THROUGH_AUD-009_ONLY",
            "status": "PASS",
            "findings": {
                "AUD-001": "qualification schedule boundary separated from mandatory registered Official Nautilus Strategy identity",
                "AUD-002": "authoritative M3/Dataset/Trial/Holdout content-and-interval exposure resolver",
                "AUD-003": "Git-anchored append-only Trial Journal reconciliation and authorized recovery",
                "AUD-004": "Git-anchored Holdout snapshot reconciliation",
                "AUD-005": "identity-only Official resolver deriving claim/report facts from complete evidence",
                "AUD-006": "exact origin repository, symbolic ref, commit, tree, lineage, and clean-state checks",
                "AUD-007": "pre-write component validation plus O_NOFOLLOW dirfd containment and collision refusal",
                "AUD-008": "unprivileged inherited seccomp-BPF TSYNC boundary with safe negative controls",
                "AUD-009": "strict public checkpointed CLI/API with terminal recovery and claim-ineligible exposed-data fixture",
            },
        },
    )
    _write(
        REPAIR / "historical-integrity.json",
        {
            "schema": "v1-repair-historical-integrity-v1",
            "status": "PASS",
            "baseline_commit": BASELINE,
            "locked_hashes": observed_locks,
            "final_acceptance_preserved_inventory_before_sha256": history_before["content_sha256"],
            "final_acceptance_preserved_inventory_after_sha256": history_after["content_sha256"],
            "inventories_equal": history_before == history_after,
            "historical_git_changes_from_baseline": historical_changes,
            "historical_evidence_modified": False,
        },
    )
    _write(
        REPAIR / "commands.json",
        {
            "schema": "v1-repair-executed-command-inventory-v1",
            "status": "PASS",
            "candidate_source": {
                "repository": candidate_source["repository"],
                "branch": candidate_source["branch"],
                "head": candidate_source["head"],
                "origin_main": candidate_source["origin_main"],
                "git_tree": candidate_source["git_tree"],
                "clean_worktree": candidate_source["clean_worktree"],
            },
            "commands": [
                {
                    "gate": "M3_SPOT_PERPETUAL_PRIMARY_AND_REPLAY_FRESH_PROCESSES",
                    "command": [
                        ".venv/bin/python",
                        "scripts/run_m3_qualifications.py",
                        "--output",
                        str(m3_output),
                    ],
                    "validation": [
                        ".venv/bin/python",
                        "scripts/validate_m3_evidence.py",
                        "--evidence",
                        str(m3_output),
                        "--output",
                        str(m3_validation_path),
                    ],
                    "returncodes": [0, 0],
                    "fresh_process_count": 4,
                },
                {
                    "gate": "M0_M4_AND_REPAIR_INDEPENDENT_DISCOVERY",
                    "command": [".venv/bin/python", "scripts/run_m4_acceptance.py", "--output-dir", str(m4_output), "--require-final-evidence"],
                    "returncode": 0,
                },
                {
                    "gate": "REVERSE_DETERMINISTIC_ORDER",
                    "command": [".venv/bin/python", "scripts/run_reverse_test_order.py", "--output-dir", str(reverse_output)],
                    "returncode": 0,
                },
                {
                    "gate": "FINAL_V1_FIVE_FIXTURES",
                    "command": [".venv/bin/python", "scripts/run_final_v1_acceptance.py", "--output", str(final_output)],
                    "returncode": 0,
                },
                {
                    "gate": "M3_THIRD_FRESH_PROCESS_REPLAY_EACH_PROFILE",
                    "command_template": [".venv/bin/python", "scripts/run_m3_child.py", "--profile", "{spot|perpetual}", "--evidence-root", str(replay_root)],
                    "returncodes": [0, 0],
                },
                {
                    "gate": "PUBLIC_OWNER_WORKFLOW_FIXTURE",
                    "command": [".venv/bin/python", "scripts/run_owner_workflow.py", "--input", "STRICT_FIXTURE_INPUT", "--repository", str(candidate), "--output", str(owner_result_path)],
                    "returncode": 0,
                },
            ],
            "network_used_by_official_runs": False,
        },
    )

    failures_dir = REPAIR / "failed-attempt-artifacts"
    failures_dir.mkdir(parents=True)
    failed_attempts = [
        {
            "attempt_id": "REPAIR-TARGETED-ENV-001",
            "state": "FAILED",
            "cause": "targeted unittest command omitted PYTHONPATH=src",
            "artifact": "TOOL_TRANSCRIPT_ONLY",
            "disposition": "rerun with strict PYTHONPATH passed",
        },
        {
            "attempt_id": "REPAIR-OFFLINE-EARLY-ACTIVATION-001",
            "state": "FAILED",
            "cause": "initial all-socket denial also blocked Tokio AF_UNIX signal socketpair",
            "artifact": "failed-attempt-artifacts/offline-entry-failure-trials.jsonl",
            "disposition": "allow only AF_UNIX socketpair; retain all network syscall denials",
        },
        {
            "attempt_id": "REPAIR-FINAL-CLEAN-COPY-001",
            "state": "FAILED",
            "cause": "first clean candidate used a one-commit Git repository and omitted historical fixture commit objects",
            "artifact": "failed-attempt-artifacts/final-clean-copy-failure",
            "disposition": "history-preserving clone used; complete Final V1 rerun passed",
        },
        {
            "attempt_id": "REPAIR-EVIDENCE-INSPECTION-JQ-001",
            "state": "FAILED_NON_GATE",
            "cause": "jq executable is not installed",
            "artifact": "TOOL_TRANSCRIPT_ONLY",
            "disposition": "read-only Python JSON inspection used; no dependency installed",
        },
        {
            "attempt_id": "REPAIR-EVIDENCE-ASSEMBLER-IMPORT-001",
            "state": "FAILED",
            "cause": "direct script execution did not initially place the repository root on sys.path",
            "artifact": "TOOL_TRANSCRIPT_ONLY",
            "disposition": "the assembler now establishes its repository import root explicitly before validation",
        },
        {
            "attempt_id": "REPAIR-MANIFEST-NESTED-EXCLUSION-001",
            "state": "FAILED",
            "cause": "the first Repair manifest excluded every file named final-content-manifest.json instead of only its own root manifest",
            "artifact": "TOOL_TRANSCRIPT_ONLY",
            "disposition": "restrict the self-exclusion to the root manifest and independently reconcile the complete file set",
        },
        {
            "attempt_id": "REPAIR-HISTORY-COMMITTED-REPLACEMENT-REVIEW-001",
            "state": "FAILED_AUDIT_GATE",
            "cause": "post-pass source review found that reconciliation did not yet compare replacement commits with every earlier Git anchor version or prove each prior state prefix",
            "artifact": "pre-prefix-hardening",
            "disposition": "added committed-descendant and longer-replacement attacks, prefix proofs, and a complete fresh acceptance rerun",
        },
        {
            "attempt_id": "REPAIR-OFFLINE-INHERITED-TCP-PROBE-001",
            "state": "FAILED_ENVIRONMENT_CONTROL",
            "cause": "the outer workspace sandbox denied creation of even a loopback AF_INET socket with EPERM before the Repair boundary activated",
            "artifact": "TOOL_TRANSCRIPT_ONLY",
            "disposition": "use an AF_UNIX socketpair to prove inherited descriptor closure; retain independent AF_INET syscall, child Python, native, and DNS seccomp probes",
        },
        {
            "attempt_id": "REPAIR-OWNER-TERMINAL-RECOVERY-BARE-HEAD-001",
            "state": "FAILED_FIXTURE",
            "cause": "the terminal-checkpoint recovery bare repository still pointed HEAD at master after publishing main, so its first clone had no checkout",
            "artifact": "TOOL_TRANSCRIPT_ONLY",
            "disposition": "bind the fixture bare HEAD explicitly to refs/heads/main before the recovery clone",
        },
        {
            "attempt_id": "REPAIR-M3-MANIFEST-EPOCH-INVENTORY-001",
            "state": "FAILED",
            "cause": "the first M3 authority validator incorrectly required the qualification epoch manifest to enumerate later additive M3 acceptance evidence",
            "artifact": "TOOL_TRANSCRIPT_ONLY",
            "disposition": "verify every immutable qualification-manifest entry and consume only manifest-declared registry/Run bytes while allowing later additive evidence epochs",
        },
    ]
    if args.offline_entry_failure_journal and args.offline_entry_failure_journal.is_file():
        shutil.copy2(
            args.offline_entry_failure_journal,
            failures_dir / "offline-entry-failure-trials.jsonl",
        )
    if args.failed_final_staging and args.failed_final_staging.is_dir():
        _copy_tree(args.failed_final_staging, failures_dir / "final-clean-copy-failure")
    if args.failed_owner_root and args.failed_owner_root.is_dir():
        target = failures_dir / "inherited-socket-first-failure"
        target.mkdir()
        for source in (
            args.failed_owner_root / "result.json",
            *args.failed_owner_root.glob("repo/runs/*/status.json"),
        ):
            if source.is_file():
                shutil.copy2(source, target / f"{source.parent.name}-{source.name}")
        failed_attempts.append(
            {
                "attempt_id": "REPAIR-OFFLINE-INHERITED-SOCKET-001",
                "state": "BLOCKED_EXPECTED",
                "cause": "initial seccomp activation found inherited socket descriptors and failed closed",
                "artifact": "failed-attempt-artifacts/inherited-socket-first-failure",
                "disposition": "dedicated Official child closes and records inherited descriptors before filter installation",
            },
        )
    with (REPAIR / "failed-attempts.jsonl").open("wb") as stream:
        for attempt in failed_attempts:
            stream.write(canonical_json_bytes(attempt) + b"\n")

    _write(
        REPAIR / "acceptance-summary.json",
        {
            "schema": "v1-post-build-repair-acceptance-summary-v1",
            "status": "PASS",
            "candidate_source_commit": candidate_source["head"],
            "candidate_source_tree": candidate_source["git_tree"],
            "m0_m4_unique_tests": expected_counts[0],
            "m0_m4_occurrences": expected_counts[1],
            "independent_discovery": expected_counts[2],
            "reverse_order": reverse["execution_occurrences"],
            "repair_adversarial_tests": len(repair_tests),
            "final_v1_fixture_count": len(final_e2e["fixtures"]),
            "m3_financial_equivalence": financial["status"],
            "m3_fresh_primary_and_replay_processes": 4,
            "holdout_mutations_rejected": holdout_matrix["rejected"],
            "owner_workflow": owner_result["status"],
            "final_holdout_used": False,
            "owner_study_executed": False,
            "real_profitability_claim": False,
            "strategy_research_started": False,
        },
    )
    _write(REPAIR / "final-content-manifest.json", _manifest())
    print(
        json.dumps(
            {
                "status": "PASS",
                "evidence": str(REPAIR),
                "manifest": sha256_file(REPAIR / "final-content-manifest.json"),
                "unique_tests": expected_counts[0],
                "repair_tests": len(repair_tests),
                "final_v1_fixtures": len(final_e2e["fixtures"]),
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
