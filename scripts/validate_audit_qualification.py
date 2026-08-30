#!/usr/bin/env python3
"""Validate repaired Spot/Perpetual qualification without historical defaults."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from crypto_lab.checker import check_evidence_directory
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.m3 import QualifiedProfileRegistry


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / "evidence/audit/comprehensive-remediation-001/qualification"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


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
                or relative.as_posix() in declared
                or not contained
                or path.is_symlink()
                or not path.is_file()
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
    checks["complete_content_addressed_inventory"] = safe_entries and declared == actual

    baseline = _json(root / "baseline.json")
    source_commit = str(baseline.get("head"))
    remote_ref = str(baseline.get("required_remote_ref"))
    published_ancestor = subprocess.run(
        ("git", "merge-base", "--is-ancestor", source_commit, remote_ref),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    commit_exists = subprocess.run(
        ("git", "cat-file", "-e", f"{source_commit}^{{commit}}"),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 0
    checks["clean_published_source"] = bool(
        baseline.get("clean_worktree") is True
        and baseline.get("run_purpose") == "QUALIFICATION"
        and baseline.get("official_run") is False
        and baseline.get("network_used") is False
        and commit_exists
        and baseline.get("remote_tip") == source_commit
        and published_ancestor.returncode == 0
    )

    registry = QualifiedProfileRegistry.from_json_bytes(
        (root / "qualified-profile-registry.json").read_bytes(),
    )
    active_runtime = sha256_file(ROOT / "runtime.lock.json")
    checks["active_runtime_qualified"] = bool(
        len(registry.records) == 2
        and all(record.runtime_lock_sha256 == active_runtime for record in registry.records)
    )
    summary = _json(root / "acceptance-summary.json")
    checks["no_research_holdout_or_claim_authority"] = bool(
        summary.get("official_run") is False
        and summary.get("research_run") is False
        and summary.get("profitability_claim") is False
        and summary.get("m4_started") is False
    )
    replay = _json(root / "deterministic-replay.json")
    checks["fresh_deterministic_replays"] = bool(
        set(replay) == {"spot", "perpetual"}
        and all(
            item.get("result") == "PASS"
            and item.get("fresh_processes") is True
            and item.get("semantic_digest_primary") == item.get("semantic_digest_replay")
            for item in replay.values()
        )
    )

    checker_revalidations: dict[str, Any] = {}
    for record in registry.records:
        for reference in record.evidence_references:
            relative = Path(reference)
            run_dir = root / relative
            persisted = _json(run_dir / "checker.json")
            regenerated = check_evidence_directory(run_dir)
            accepted = bool(
                not relative.is_absolute()
                and ".." not in relative.parts
                and run_dir.resolve(strict=True).is_relative_to(root)
                and regenerated.to_builtins() == persisted
                and regenerated.outcome.value == "CHECK_PASS"
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
        and duplicate.get("outcome") == "CHECK_FAIL"
        and "FUNDING_DOUBLE_COUNT" in duplicate.get("failure_codes", ())
        and controls.get("PERP_POST_BOUNDARY_OPEN", {}).get("funding_events_count") == 0
    )
    checks["negative_controls"] = controls_pass

    failed = sorted(name for name, passed in checks.items() if not passed)
    return {
        "schema": "comprehensive-audit-remediation-qualification-validation-v1",
        "status": "PASS" if not failed else "FAIL",
        "checks": checks,
        "failed_checks": failed,
        "qualified_profile_registry_identity": registry.registry_content_sha256,
        "checker_revalidations": checker_revalidations,
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
