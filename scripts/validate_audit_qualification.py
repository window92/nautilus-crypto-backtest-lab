#!/usr/bin/env python3
"""Validate repaired Spot/Perpetual qualification without historical defaults."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import RuntimeLock
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.m3 import QualificationDownstreamBundle
from crypto_lab.runtime import validate_persisted_runtime_identity


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_PASS = "COMPONENT_CHECK_PASS"
COMPONENT_FAIL = "COMPONENT_CHECK_FAIL"
DEFAULT_EVIDENCE = (
    ROOT / "evidence/audit/comprehensive-remediation-001/qualification-runtime-proof"
)


def _json(path: Path) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{path} contains duplicate JSON key {key!r}")
            value[key] = item
        return value

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
    )
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", "--no-replace-objects", *arguments),
        cwd=ROOT,
        check=False,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        },
    )


def _git_file_sha256(commit: str, path: str) -> str | None:
    completed = _git("show", f"{commit}:{path}")
    return hashlib.sha256(completed.stdout).hexdigest() if completed.returncode == 0 else None


def validate(evidence: Path) -> dict[str, Any]:
    root = evidence.resolve(strict=True)
    checks: dict[str, bool] = {}
    manifest = _json(root / "qualification-manifest.json")
    entries = manifest.get("entries")
    safe_entries = bool(
        manifest.get("schema") == "m3-acceptance-manifest-v1"
        and isinstance(entries, list)
        and manifest.get("content_sha256") == canonical_sha256(entries)
    )
    declared: set[str] = set()
    if safe_entries:
        for item in entries:
            if not isinstance(item, dict) or set(item) != {"path", "byte_size", "sha256"}:
                safe_entries = False
                break
            relative = Path(str(item["path"]))
            path = root / relative
            try:
                path.resolve(strict=True).relative_to(root)
                contained = True
            except (FileNotFoundError, ValueError):
                contained = False
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not relative.parts
                or str(item["path"]) != relative.as_posix()
                or relative.as_posix() in declared
                or not contained
                or path.is_symlink()
                or not path.is_file()
                or not isinstance(item["byte_size"], int)
                or item["byte_size"] < 0
                or not isinstance(item["sha256"], str)
                or len(item["sha256"]) != 64
                or path.stat().st_size != item["byte_size"]
                or sha256_file(path) != item["sha256"]
            ):
                safe_entries = False
                break
            declared.add(relative.as_posix())
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "qualification-manifest.json"
    }
    unexpected_symlink = any(path.is_symlink() for path in root.rglob("*"))
    checks["complete_content_addressed_inventory"] = bool(
        safe_entries
        and manifest.get("manifest_self_excluded") is True
        and declared == actual
        and not unexpected_symlink
    )

    baseline = _json(root / "baseline.json")
    source_commit = str(baseline.get("head"))
    remote_ref = str(baseline.get("required_remote_ref"))
    published_ancestor = _git("merge-base", "--is-ancestor", source_commit, remote_ref)
    commit_exists = _git("cat-file", "-e", f"{source_commit}^{{commit}}").returncode == 0
    resolved_tree = _git("rev-parse", f"{source_commit}^{{tree}}")
    source_tree = resolved_tree.stdout.decode("ascii", errors="replace").strip()
    checks["clean_published_source"] = bool(
        baseline.get("schema") == "m3-baseline-v1"
        and baseline.get("clean_worktree") is True
        and baseline.get("run_purpose") == "QUALIFICATION"
        and baseline.get("official_run") is False
        and baseline.get("network_used") is False
        and baseline.get("m4_started") is False
        and commit_exists
        and baseline.get("remote_tip") == source_commit
        and baseline.get("git_tree") == source_tree
        and baseline.get("ssot_sha256") == _git_file_sha256(source_commit, "SSOT.md")
        and baseline.get("runtime_lock_sha256")
        == _git_file_sha256(source_commit, "runtime.lock.json")
        and baseline.get("dependency_lock_sha256")
        == _git_file_sha256(source_commit, "requirements.lock.txt")
        and published_ancestor.returncode == 0
    )

    registry = QualifiedProfileRegistry.from_json_bytes(
        (root / "qualified-profile-registry.json").read_bytes(),
    )
    checks["r2_current_registry_contract"] = bool(
        registry.schema_version == 2
        and all(
            record.schema_version == 2
            and record.checker_result == COMPONENT_PASS
            and record.replay_result == "PASS"
            for record in registry.records
        )
    )
    active_runtime = sha256_file(ROOT / "runtime.lock.json")
    checks["active_runtime_qualified"] = bool(
        len(registry.records) == 2
        and all(record.runtime_lock_sha256 == active_runtime for record in registry.records)
    )
    summary = _json(root / "acceptance-summary.json")
    expected_runs = {
        "spot_primary": registry.records[0].accepted_run_ids[0],
        "spot_replay": registry.records[0].accepted_run_ids[1],
        "perp_primary": registry.records[1].accepted_run_ids[0],
        "perp_replay": registry.records[1].accepted_run_ids[1],
    }
    checks["no_research_holdout_or_claim_authority"] = bool(
        summary.get("schema") == "m3-acceptance-summary-v1"
        and summary.get("profiles") == {"spot": "QUALIFIED", "perpetual": "QUALIFIED"}
        and summary.get("accepted_runs") == expected_runs
        and summary.get("registry_content_sha256") == registry.registry_content_sha256
        and summary.get("run_purpose") == "QUALIFICATION"
        and summary.get("required_remote_ref") == remote_ref
        and summary.get("official_run") is False
        and summary.get("research_run") is False
        and summary.get("profitability_claim") is False
        and summary.get("m4_started") is False
    )
    replay = _json(root / "deterministic-replay.json")
    checks["fresh_deterministic_replays"] = bool(
        set(replay) == {"spot", "perpetual"}
        and replay.get("spot", {}).get("primary_run_id") == expected_runs["spot_primary"]
        and replay.get("spot", {}).get("replay_run_id") == expected_runs["spot_replay"]
        and replay.get("perpetual", {}).get("primary_run_id") == expected_runs["perp_primary"]
        and replay.get("perpetual", {}).get("replay_run_id") == expected_runs["perp_replay"]
        and all(
            item.get("schema") == "m3-deterministic-replay-v1"
            and item.get("result") == "PASS"
            and item.get("fresh_processes") is True
            and item.get("semantic_sequence_compared") is True
            and item.get("semantic_digest_primary") == item.get("semantic_digest_replay")
            for item in replay.values()
        )
    )

    attempt_labels = {
        "spot-primary": (registry.records[0], 0),
        "spot-replay": (registry.records[0], 1),
        "perpetual-primary": (registry.records[1], 0),
        "perpetual-replay": (registry.records[1], 1),
    }
    attempt_summaries: dict[str, dict[str, Any]] = {}
    attempt_summaries_pass = True
    for label, (record, index) in attempt_labels.items():
        attempt = _json(root / "attempt-summaries" / f"{label}.json")
        attempt_summaries[label] = attempt
        attempt_summaries_pass = bool(
            attempt_summaries_pass
            and attempt.get("run_id") == record.accepted_run_ids[index]
            and attempt.get("evidence_dir") == record.evidence_references[index]
            and attempt.get("state") == "COMPLETED"
            and attempt.get("component_validation_outcome") == COMPONENT_PASS
            and attempt.get("checker_outcome") == COMPONENT_PASS
            and attempt.get("failure_codes") == []
            and attempt.get("runtime_startup_verified") is True
        )
    checks["accepted_attempt_bindings"] = attempt_summaries_pass

    checker_revalidations: dict[str, Any] = {}
    runtime_proof_revalidations: dict[str, Any] = {}
    source_revalidations: dict[str, Any] = {}
    downstream_revalidations: dict[str, Any] = {}
    for record in registry.records:
        profile_prefix = (
            "spot"
            if record.profile_id.value == "BINANCE_SPOT_CASH_LONG_ONLY"
            else "perpetual"
        )
        downstream_path = root / "downstream" / f"{record.profile_id.value}.json"
        try:
            downstream = QualificationDownstreamBundle.from_json_bytes(
                downstream_path.read_bytes(),
            )
            downstream_payload = downstream.to_builtins()
            downstream_pass = bool(
                downstream.schema_version == 2
                and downstream.profile_record == record
                and downstream.run_result.get("run_id") == record.accepted_run_ids[0]
                and downstream_payload["run_result"]
                == attempt_summaries[f"{profile_prefix}-primary"]
                and downstream.run_result.get("component_validation_outcome") == COMPONENT_PASS
                and downstream.mechanical_integrity.checker_result == COMPONENT_PASS
                and tuple(downstream.mechanical_integrity.run_ids) == record.accepted_run_ids
                and downstream_payload["evidence_manifest"]
                == _json(root / record.evidence_references[0] / "evidence_manifest.json")
            )
            downstream_detail = None
        except Exception as exc:
            downstream_pass = False
            downstream_detail = f"{type(exc).__name__}: {exc}"
        downstream_revalidations[record.profile_id.value] = {
            "pass": downstream_pass,
            "detail": downstream_detail,
        }
        for reference in record.evidence_references:
            relative = Path(reference)
            run_dir = root / relative
            persisted = _json(run_dir / "checker.json")
            regenerated = check_evidence_directory(run_dir)
            persisted_source = _json(run_dir / "source_revision.json")
            source_pass = bool(
                record.source_revision.git_commit == source_commit
                and record.source_revision.git_tree == source_tree
                and record.source_revision.branch_ref == baseline.get("branch")
                and record.source_revision.clean_worktree is True
                and persisted_source.get("repository") == record.source_revision.repository
                and persisted_source.get("branch_ref") == baseline.get("branch")
                and persisted_source.get("git_commit") == source_commit
                and persisted_source.get("git_tree") == source_tree
                and persisted_source.get("clean_worktree") is True
            )
            source_revalidations[reference] = {
                "pass": source_pass,
                "git_commit": persisted_source.get("git_commit"),
                "git_tree": persisted_source.get("git_tree"),
            }
            runtime_proof_pass = False
            runtime_proof_detail: str | None = None
            try:
                result = _json(run_dir / "nautilus_result.json")
                identity_path = run_dir / "runtime_identity.json"
                lock = RuntimeLock.from_json_bytes((run_dir / "runtime.lock.json").read_bytes())
                validate_persisted_runtime_identity(lock, _json(identity_path))
                runtime_proof_pass = bool(
                    result.get("runtime_identity_verified") is True
                    and result.get("evidence_bindings", {}).get("runtime_identity_sha256")
                    == sha256_file(identity_path)
                )
                if not runtime_proof_pass:
                    runtime_proof_detail = "runtime identity flag or binding mismatch"
            except Exception as exc:
                runtime_proof_detail = f"{type(exc).__name__}: {exc}"
            runtime_proof_revalidations[reference] = {
                "pass": runtime_proof_pass,
                "detail": runtime_proof_detail,
            }
            accepted = bool(
                not relative.is_absolute()
                and ".." not in relative.parts
                and run_dir.resolve(strict=True).is_relative_to(root)
                and regenerated.to_builtins() == persisted
                and regenerated.outcome.value == COMPONENT_PASS
            )
            checker_revalidations[reference] = {
                "pass": accepted,
                "checker_outcome": regenerated.outcome.value,
                "failure_codes": list(regenerated.failure_codes),
            }
    checks["current_checker_revalidation"] = bool(
        len(checker_revalidations) == 4
        and all(item["pass"] for item in checker_revalidations.values())
    )
    checks["persisted_runtime_payload_proof"] = bool(
        len(runtime_proof_revalidations) == 4
        and all(item["pass"] for item in runtime_proof_revalidations.values())
    )
    checks["source_revision_bindings"] = bool(
        len(source_revalidations) == 4
        and all(item["pass"] for item in source_revalidations.values())
    )
    checks["downstream_v2_bindings"] = bool(
        len(downstream_revalidations) == 2
        and all(item["pass"] for item in downstream_revalidations.values())
    )

    controls = _json(root / "negative-controls.json")
    expected = {
        "SPOT_SHORT": "SPOT_SHORT_OR_BORROW_DETECTED",
        "PERP_DIRECT_CROSS_ZERO": "CROSS_ZERO_ORDER_REJECTED",
        "PERP_CONCURRENT_ORDER": "CONCURRENT_STRATEGY_ORDER_REJECTED",
        "PERP_ABOVE_MARKET_MAX": "INSTRUMENT_METADATA_INVALID",
        "PROHIBITED_MARK_FALLBACK": "MARK_ROLE_INVALID",
        "NETWORK_ATTEMPT": "NETWORK_DURING_OFFICIAL_RUN",
    }
    controls_pass = set(controls) == {
        *expected,
        "PERP_POST_BOUNDARY_OPEN",
        "DUPLICATE_FUNDING_SETTLEMENT",
    }
    for name, code in expected.items():
        item = controls.get(name, {})
        observed = set(item.get("failure_codes", ())) | set(
            item.get("guard_failure_codes", ()),
        )
        controls_pass = controls_pass and code in observed
    duplicate = controls.get("DUPLICATE_FUNDING_SETTLEMENT", {}).get("checker", {})
    controls_pass = bool(
        controls_pass
        and duplicate.get("outcome") == COMPONENT_FAIL
        and "FUNDING_DOUBLE_COUNT" in duplicate.get("failure_codes", ())
        and controls.get("PERP_POST_BOUNDARY_OPEN", {}).get("funding_events_count") == 0
    )
    checks["negative_controls"] = controls_pass

    failed = sorted(name for name, passed in checks.items() if not passed)
    return {
        "schema": "adversarial-remediation-qualification-validation-v2",
        "status": "PASS" if not failed else "FAIL",
        "checks": checks,
        "failed_checks": failed,
        "qualified_profile_registry_identity": registry.registry_content_sha256,
        "checker_revalidations": checker_revalidations,
        "runtime_proof_revalidations": runtime_proof_revalidations,
        "source_revalidations": source_revalidations,
        "downstream_revalidations": downstream_revalidations,
        "final_holdout_used": False,
        "profitability_claim_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = validate(arguments.evidence)
    payload = canonical_json_bytes(result) + b"\n"
    if arguments.output is not None:
        output = arguments.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and output.read_bytes() != payload:
            raise FileExistsError(f"refusing to replace qualification validation: {output}")
        output.write_bytes(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
