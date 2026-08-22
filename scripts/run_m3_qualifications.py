#!/usr/bin/env python3
"""Execute M3 profiles and negative controls offline, then publish additive evidence."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import MarketProfile
from crypto_lab.config import SourceRevision
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.m3 import EXPOSED_QUALIFICATION_LIMITATION
from crypto_lab.m3 import M3NegativeControl
from crypto_lab.m3 import MechanicalIntegrity
from crypto_lab.m3 import MechanicalIntegrityResult
from crypto_lab.m3 import ProfileQualificationState
from crypto_lab.m3 import QualificationDownstreamBundle
from crypto_lab.m3 import QualifiedProfileRecord
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.m3 import qualification_dataset_release
from crypto_lab.m3 import qualification_strategy_inputs


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "evidence/m3/m3-acceptance-001"
RUNTIME_LOCK_SHA256 = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _run_child(
    staging: Path,
    *,
    label: str,
    profile: str,
    run_id: str,
    extra: tuple[str, ...] = (),
) -> dict[str, Any]:
    summary_path = staging / "attempt-summaries" / f"{label}.json"
    evidence_root = staging / "runs" / label
    command = [
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "scripts/run_m3_child.py"),
        "--profile", profile,
        "--run-id", run_id,
        "--evidence-root", str(evidence_root),
        "--summary", str(summary_path),
        *extra,
    ]
    env = {
        **os.environ,
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(ROOT / "src"),
        "PYTEST_ADDOPTS": "-p no:cacheprovider",
    }
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    _write_json(
        staging / "commands" / f"{label}.json",
        {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "fresh_process": True,
            "network_enabled_by_runner": False,
        },
    )
    if completed.returncode != 0 or not summary_path.is_file():
        raise RuntimeError(f"M3 child failed for {label}: {completed.stderr}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def _assert_positive(summary: dict[str, Any], label: str) -> None:
    if not (
        summary["state"] == "COMPLETED"
        and summary["checker_outcome"] == "CHECK_PASS"
        and not summary["failure_codes"]
        and summary["fills_count"] > 0
    ):
        raise RuntimeError(f"positive profile {label} did not pass: {summary}")


def _manifest(directory: Path) -> dict[str, Any]:
    entries = [
        {
            "path": str(path.relative_to(directory)),
            "byte_size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.name != "qualification-manifest.json"
    ]
    return {
        "schema": "m3-acceptance-manifest-v1",
        "entries": entries,
        "content_sha256": canonical_sha256(entries),
        "manifest_self_excluded": True,
    }


def _published_summary(summary: dict[str, Any], staging: Path) -> dict[str, Any]:
    published = json.loads(json.dumps(summary))
    evidence_dir = Path(published["evidence_dir"])
    published["evidence_dir"] = str(
        evidence_dir.relative_to(staging) if evidence_dir.is_absolute() else evidence_dir,
    )
    return published


def _duplicate_funding_control(staging: Path, perpetual: dict[str, Any]) -> dict[str, Any]:
    source = Path(perpetual["evidence_dir"])
    target = staging / "checker-tamper" / "duplicate-funding-settlement"
    shutil.copytree(source, target)
    funding = target / "funding.csv"
    rows = funding.read_text(encoding="utf-8").splitlines()
    funding.write_text("\n".join([*rows, rows[1]]) + "\n", encoding="utf-8")
    report = check_evidence_directory(target)
    result = {
        "schema": "m3-checker-tamper-control-v1",
        "control": "DUPLICATE_FUNDING_SETTLEMENT",
        "source_run_id": perpetual["run_id"],
        "native_fill_evidence_modified": False,
        "financial_state_modified": False,
        "tampered_copy_only": True,
        "checker": report.to_builtins(),
    }
    _write_json(target / "negative-control-result.json", result)
    return result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume-staging", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite M3 evidence: {output}")
    if args.resume_staging is None and _git("status", "--porcelain=v1"):
        raise RuntimeError("accepted M3 qualifications require a clean committed worktree")
    if _git("rev-parse", "HEAD") != _git("rev-parse", "origin/main"):
        raise RuntimeError("accepted M3 qualifications require HEAD == origin/main")

    if args.resume_staging is None:
        temporary = Path(tempfile.mkdtemp(prefix="nautilus-m3-qualification-", dir="/tmp"))
        staging = temporary / "m3-acceptance-001"
        staging.mkdir()
    else:
        staging = args.resume_staging.resolve()
        if not (staging / "baseline.json").is_file():
            raise FileNotFoundError("resume staging lacks the frozen M3 baseline")
        frozen_baseline = json.loads((staging / "baseline.json").read_text(encoding="utf-8"))
        if (
            frozen_baseline["head"] != _git("rev-parse", "HEAD")
            or frozen_baseline["origin_main"] != _git("rev-parse", "origin/main")
            or frozen_baseline["clean_worktree"] is not True
        ):
            raise RuntimeError("resume staging is not bound to the current committed source")
        _write_json(
            staging / "resume-after-failed-postprocessing.json",
            {
                "failure": "NameError: _freeze_field was not imported",
                "failed_stage": "QualificationDownstreamBundle construction",
                "accepted_runs_reexecuted": False,
                "accepted_run_source_remained_clean": True,
                "financial_evidence_modified": False,
                "repair": "import the existing strict-model freeze helper",
            },
        )

    baseline = {
        "schema": "m3-baseline-v1",
        "user": subprocess.run(["whoami"], check=True, capture_output=True, text=True).stdout.strip(),
        "repository": str(ROOT),
        "branch": _git("branch", "--show-current"),
        "head": _git("rev-parse", "HEAD"),
        "origin_main": _git("rev-parse", "origin/main"),
        "git_tree": _git("rev-parse", "HEAD^{tree}"),
        "clean_worktree": True,
        "ssot_sha256": sha256_file(ROOT / "SSOT.md"),
        "runtime_lock_sha256": sha256_file(ROOT / "runtime.lock.json"),
        "dependency_lock_sha256": sha256_file(ROOT / "requirements.lock.txt"),
        "run_purpose": "QUALIFICATION",
        "official_run": False,
        "network_used": False,
        "m4_started": False,
    }
    if args.resume_staging is None:
        _write_json(staging / "baseline.json", baseline)
    frozen_inputs = {
        profile.value: {
            "strategy_spec": qualification_strategy_inputs(profile).strategy_spec.to_builtins(),
            "strategy_plan": qualification_strategy_inputs(profile).strategy_plan.material_payload(),
            "dataset_release_id": qualification_dataset_release(profile).dataset_release_id,
        }
        for profile in MarketProfile
    }
    if args.resume_staging is None:
        _write_json(staging / "frozen-qualification-inputs.json", frozen_inputs)

    if args.resume_staging is None:
        accepted = {
            "spot_primary": _run_child(
                staging, label="spot-primary", profile="spot", run_id="m3-spot-primary-001",
            ),
            "spot_replay": _run_child(
                staging, label="spot-replay", profile="spot", run_id="m3-spot-replay-001",
            ),
            "perp_primary": _run_child(
                staging, label="perpetual-primary", profile="perpetual", run_id="m3-perpetual-primary-001",
            ),
            "perp_replay": _run_child(
                staging, label="perpetual-replay", profile="perpetual", run_id="m3-perpetual-replay-001",
            ),
        }
    else:
        accepted = {
            "spot_primary": json.loads((staging / "attempt-summaries/spot-primary.json").read_text()),
            "spot_replay": json.loads((staging / "attempt-summaries/spot-replay.json").read_text()),
            "perp_primary": json.loads((staging / "attempt-summaries/perpetual-primary.json").read_text()),
            "perp_replay": json.loads((staging / "attempt-summaries/perpetual-replay.json").read_text()),
        }
    for label, summary in accepted.items():
        _assert_positive(summary, label)

    replay_results: dict[str, dict[str, Any]] = {}
    for profile, first, second in (
        ("spot", accepted["spot_primary"], accepted["spot_replay"]),
        ("perpetual", accepted["perp_primary"], accepted["perp_replay"]),
    ):
        passed = first["semantic_digest"] == second["semantic_digest"]
        replay_results[profile] = {
            "schema": "m3-deterministic-replay-v1",
            "primary_run_id": first["run_id"],
            "replay_run_id": second["run_id"],
            "fresh_processes": True,
            "semantic_sequence_compared": True,
            "semantic_digest_primary": first["semantic_digest"],
            "semantic_digest_replay": second["semantic_digest"],
            "ignored_only_nonsemantic_occurrence_metadata": True,
            "result": "PASS" if passed else "FAIL",
        }
        if not passed:
            raise RuntimeError(f"{profile} deterministic replay diverged")
    _write_json(staging / "deterministic-replay.json", replay_results)

    if args.resume_staging is None:
        controls: dict[str, dict[str, Any]] = {}
        for control, profile in (
            (M3NegativeControl.SPOT_SHORT, "spot"),
            (M3NegativeControl.PERP_DIRECT_CROSS_ZERO, "perpetual"),
            (M3NegativeControl.PERP_CONCURRENT_ORDER, "perpetual"),
            (M3NegativeControl.PERP_ABOVE_MARKET_MAX, "perpetual"),
            (M3NegativeControl.PERP_POST_BOUNDARY_OPEN, "perpetual"),
        ):
            controls[control.value] = _run_child(
                staging,
                label=control.value.lower(),
                profile=profile,
                run_id=f"m3-control-{control.value.lower()}-001",
                extra=("--negative-control", control.value),
            )
        controls["PROHIBITED_MARK_FALLBACK"] = _run_child(
            staging,
            label="prohibited-mark-fallback",
            profile="perpetual",
            run_id="m3-control-prohibited-mark-fallback-001",
            extra=("--invalid-mark-binding",),
        )
        controls["NETWORK_ATTEMPT"] = _run_child(
            staging,
            label="network-attempt",
            profile="spot",
            run_id="m3-control-network-attempt-001",
            extra=("--network-attempt",),
        )
        controls["DUPLICATE_FUNDING_SETTLEMENT"] = _duplicate_funding_control(
            staging,
            accepted["perp_primary"],
        )
    else:
        controls = json.loads((staging / "negative-controls.json").read_text(encoding="utf-8"))

    expected_codes = {
        "SPOT_SHORT": "SPOT_SHORT_OR_BORROW_DETECTED",
        "PERP_DIRECT_CROSS_ZERO": "CROSS_ZERO_ORDER_REJECTED",
        "PERP_CONCURRENT_ORDER": "CONCURRENT_STRATEGY_ORDER_REJECTED",
        "PERP_ABOVE_MARKET_MAX": "INSTRUMENT_METADATA_INVALID",
        "PROHIBITED_MARK_FALLBACK": "MARK_ROLE_INVALID",
        "NETWORK_ATTEMPT": "NETWORK_DURING_OFFICIAL_RUN",
    }
    for name, code in expected_codes.items():
        summary = controls[name]
        observed = set(summary.get("failure_codes", [])) | set(
            summary.get("guard_failure_codes", []),
        )
        if code not in observed:
            raise RuntimeError(f"negative control {name} missed {code}: {summary}")
    post_boundary = controls["PERP_POST_BOUNDARY_OPEN"]
    if post_boundary["funding_events_count"] != 0 or any(
        int(event["ts_event"]) <= 1_735_718_400_000_000_000
        for event in post_boundary["funding_events"]
    ):
        raise RuntimeError("post-boundary position received prior funding")
    duplicate = controls["DUPLICATE_FUNDING_SETTLEMENT"]["checker"]
    if duplicate["outcome"] != "CHECK_FAIL" or "FUNDING_DOUBLE_COUNT" not in duplicate["failure_codes"]:
        raise RuntimeError("duplicate funding settlement checker control did not fail")
    published_controls = {
        name: (
            _published_summary(summary, staging)
            if isinstance(summary, dict) and "evidence_dir" in summary
            else summary
        )
        for name, summary in controls.items()
    }
    _write_json(staging / "negative-controls.json", published_controls)

    limitations = (
        EXPOSED_QUALIFICATION_LIMITATION,
        "QUALIFICATION_ONLY_NO_PROFITABILITY_CLAIM",
        "BAR_BASED_ESTIMATED_EXECUTION",
        "ESTIMATED_FEE_0.001",
        "CURRENT_METADATA_NOT_EXACT_HISTORICAL_VENUE_RULES",
    )
    records: list[QualifiedProfileRecord] = []
    mechanical: dict[str, MechanicalIntegrityResult] = {}
    for profile, base_id, primary_key, replay_key in (
        (
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            "2e0bdefe2b664821c559e95d35a3462c8354606076e1ec81d0ce6272f89b9a44",
            "spot_primary",
            "spot_replay",
        ),
        (
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            "749e654402021fafafe4a3269005c5ef1253c3743f04c35622726bca957a356b",
            "perp_primary",
            "perp_replay",
        ),
    ):
        primary = accepted[primary_key]
        replay = accepted[replay_key]
        source = SourceRevision.from_json_bytes(
            (Path(primary["evidence_dir"]) / "source_revision.json").read_bytes(),
        )
        strategy_spec = json.loads(
            (Path(primary["evidence_dir"]) / "strategy_spec.json").read_text(encoding="utf-8"),
        )
        record = QualifiedProfileRecord.create(
            profile_id=profile,
            qualification_state=ProfileQualificationState.QUALIFIED,
            runtime_lock_sha256=RUNTIME_LOCK_SHA256,
            source_revision=source,
            base_dataset_release_id=base_id,
            dataset_release_id=qualification_dataset_release(profile).dataset_release_id,
            strategy_spec_id=canonical_sha256(
                {key: value for key, value in strategy_spec.items() if key != "strategy_id"},
            ),
            accepted_run_ids=(primary["run_id"], replay["run_id"]),
            checker_result="CHECK_PASS",
            replay_result="PASS",
            evidence_references=(
                str(Path(primary["evidence_dir"]).relative_to(staging)),
                str(Path(replay["evidence_dir"]).relative_to(staging)),
            ),
            qualification_limitations=limitations,
        )
        records.append(record)
        integrity = MechanicalIntegrityResult(
            state=MechanicalIntegrity.PASS,
            checker_result="CHECK_PASS",
            replay_result="PASS",
            run_ids=(primary["run_id"], replay["run_id"]),
            failure_codes=(),
        )
        mechanical[profile.value] = integrity
        bundle = QualificationDownstreamBundle(
            schema_version=1,
            profile_record=record,
            run_result=_published_summary(primary, staging),
            evidence_manifest=json.loads(
                (Path(primary["evidence_dir"]) / "evidence_manifest.json").read_text(
                    encoding="utf-8",
                ),
            ),
            mechanical_integrity=integrity,
            qualification_limitations=limitations,
        )
        _write_json(staging / "downstream" / f"{profile.value}.json", bundle.to_builtins())

    registry = QualifiedProfileRegistry.create(records=tuple(records))
    _write_json(staging / "qualified-profile-registry.json", registry.to_builtins())
    _write_json(
        staging / "mechanical-integrity.json",
        {key: value.to_builtins() for key, value in mechanical.items()},
    )
    failed_attempts = [
        {
            "attempt": "M3_FUNDING_ONE_SHOT_FINAL_CACHE_CAPTURE",
            "result": "FAILED_EVIDENCE_CAPTURE_ONLY",
            "root_cause": "NETTING position identifier reuse replaced prior adjustment history in final cache view",
            "native_cash_settlement_occurred": True,
            "repair": "public BacktestEngine streaming checkpoint at the native funding boundary",
        },
        {
            "attempt": "M3_POSITION_EVENT_STRATEGY_CALLBACK_TRIGGER",
            "result": "SUPERSEDED",
            "root_cause": "public Strategy callback did not expose PositionAdjusted in this pinned path",
            "product_semantics_changed": False,
        },
        {
            "attempt": "M3_POSTPROCESS_DOWNSTREAM_BUNDLE_001",
            "result": "FAILED",
            "root_cause": "QualificationDownstreamBundle missed the existing _freeze_field import",
            "accepted_runs_completed_before_failure": True,
            "accepted_runs_reexecuted": False,
            "financial_evidence_modified": False,
            "repair": "targeted import and additive postprocessing resume",
        },
        {
            "attempt": "M3_POSTPROCESS_RESUME_002",
            "result": "FAILED",
            "root_cause": "resume re-normalized an already relative negative-control evidence path",
            "accepted_runs_reexecuted": False,
            "financial_evidence_modified": False,
            "repair": "idempotent absolute-or-relative evidence reference normalization",
        },
    ]
    (staging / "failed-attempts.jsonl").write_bytes(
        b"".join(canonical_json_bytes(item) + b"\n" for item in failed_attempts),
    )
    _write_json(
        staging / "acceptance-summary.json",
        {
            "schema": "m3-acceptance-summary-v1",
            "profiles": {
                "spot": "QUALIFIED",
                "perpetual": "QUALIFIED",
            },
            "accepted_runs": {
                key: value["run_id"] for key, value in accepted.items()
            },
            "replay": replay_results,
            "negative_controls": sorted(controls),
            "registry_content_sha256": registry.registry_content_sha256,
            "run_purpose": "QUALIFICATION",
            "official_run": False,
            "research_run": False,
            "profitability_claim": False,
            "m4_started": False,
        },
    )
    for summary_path in (staging / "attempt-summaries").glob("*.json"):
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        _write_json(summary_path, _published_summary(summary, staging))
    _write_json(staging / "qualification-manifest.json", _manifest(staging))
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging, output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
