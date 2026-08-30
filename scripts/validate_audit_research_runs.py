#!/usr/bin/env python3
"""Fail-closed validation of the final six audit-remediation Development Runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.config import RuntimeLock
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.history import HistoryAnchorStore
from crypto_lab.official import OfficialEvidenceLocator
from crypto_lab.official import OfficialEvidenceResolver
from crypto_lab.owner import OwnerWorkflowInput
from crypto_lab.profile_authority import validate_persisted_profile_authority
from crypto_lab.reporting import ReportOutput
from crypto_lab.research import HoldoutLockStore
from crypto_lab.research import PartitionRole
from crypto_lab.research import TrialJournal
from crypto_lab.research import TrialState
from crypto_lab.result_status import revoked_result_for_directory
from crypto_lab.runtime import validate_persisted_runtime_identity
from crypto_lab.timestamps import utc_datetime_to_ns


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FREEZE = (
    ROOT
    / "evidence/audit/comprehensive-remediation-001/official-workflow-freeze-003"
)
EXPECTED_EPOCH = "comprehensive-audit-remediation-003"
EXPECTED_FAMILY = "BTCUSDT_WEEKLY_TSMOM28_V1_AUDIT_REMEDIATION_003"
EXPECTED_PROFILE_COUNTS = {
    MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY: 3,
    MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING: 3,
}


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _contained_reference(reference: str) -> Path:
    relative = Path(reference)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"unsafe repository reference: {reference}")
    resolved = (ROOT / relative).resolve(strict=True)
    resolved.relative_to(ROOT)
    return resolved


def _git(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", *arguments),
        cwd=ROOT,
        check=False,
        capture_output=True,
    )


def _git_blob(commit: str, relative: str) -> bytes:
    process = _git("show", f"{commit}:{relative}")
    if process.returncode != 0:
        raise ValueError(f"missing committed source evidence: {commit}:{relative}")
    return process.stdout


def _report_addition_commit(relative: str) -> str:
    process = _git("log", "--format=%H", "--diff-filter=A", "--", relative)
    commits = process.stdout.decode("utf-8").splitlines() if process.returncode == 0 else []
    if len(commits) != 1:
        raise ValueError(f"expected one report addition commit for {relative}, found {len(commits)}")
    return commits[0]


def _named_checks(checker: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = checker.get("checks")
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("checker checks are malformed")
    names = [str(item.get("name")) for item in raw]
    if len(names) != len(set(names)):
        raise ValueError("checker contains duplicate check names")
    return dict(zip(names, raw, strict=True))


def _validate_report_snapshot(
    *,
    workflow: OwnerWorkflowInput,
    primary_ref: str,
    authority: dict[str, Any],
) -> tuple[ReportOutput, str]:
    relative = f"research/reports/{workflow.trial_id}.json"
    report_path = ROOT / relative
    markdown_path = report_path.with_suffix(".md")
    report = ReportOutput.from_json_bytes(report_path.read_bytes())
    if markdown_path.read_text(encoding="utf-8") != report.markdown:
        raise ValueError("report Markdown differs from its content-addressed JSON")
    payload = report.json_payload
    holdout = payload.get("holdout_state", {})
    if (
        payload.get("selected_trial_id") != workflow.trial_id
        or payload.get("report_purpose") != "OFFICIAL_RESEARCH_REPORT"
        or payload.get("research_intent") != "EXPLORATORY"
        or payload.get("research_eligibility") != "INELIGIBLE"
        or payload.get("profitability_claim_is_real") is not False
        or payload.get("mechanical_integrity") != "PASS"
        or holdout.get("entry_count") != 0
        or report.claim_evaluation.eligible_confirmatory_profitability_claim is not False
        or report.claim_evaluation.mechanical_integrity.value != "PASS"
    ):
        raise ValueError("report claim or Holdout disposition is unsafe")

    commit = _report_addition_commit(relative)
    if _git_blob(commit, relative) != report_path.read_bytes():
        raise ValueError("report bytes differ from their addition commit")
    markdown_relative = markdown_path.relative_to(ROOT).as_posix()
    if _git_blob(commit, markdown_relative) != markdown_path.read_bytes():
        raise ValueError("report Markdown differs from its addition commit")
    sources = {
        "history_anchors.jsonl": "research/history_anchors.jsonl",
        "holdout_lock.json": "research/holdout_lock.json",
        "protocol": f"research/protocols/{workflow.protocol.protocol_id}.json",
        "qualified_profile_registry": str(authority["qualified_profile_registry_ref"]),
        "selected_deterministic_replay": f"research/replays/{workflow.trial_id}.json",
        "selected_diagnostics": f"research/diagnostics/{workflow.run_id}.json",
        "selected_performance": f"research/performance/{workflow.run_id}.json",
        "selected_run_manifest": f"{primary_ref}/evidence_manifest.json",
        "trials.jsonl": "research/trials.jsonl",
    }
    if set(report.source_evidence_hashes) != set(sources):
        raise ValueError("report source-evidence inventory is incomplete")
    for name, source in sources.items():
        observed = hashlib.sha256(_git_blob(commit, source)).hexdigest()
        if observed != report.source_evidence_hashes[name]:
            raise ValueError(f"report historical source snapshot mismatch: {name}")
    return report, commit


def _validate_run(
    *,
    workflow: OwnerWorkflowInput,
    terminal_ref: str,
) -> dict[str, Any]:
    replay_path = ROOT / "research/replays" / f"{workflow.trial_id}.json"
    replay = _json_object(replay_path)
    replay_material = dict(replay)
    replay_identity = replay_material.pop("replay_identity", None)
    if (
        replay_identity != canonical_sha256(replay_material)
        or replay.get("schema") != "owner-deterministic-replay-v1"
        or replay.get("trial_id") != workflow.trial_id
        or replay.get("primary_run_ref") != terminal_ref
        or replay.get("result") != "PASS"
        or replay.get("fresh_processes") is not True
        or replay.get("read_only_checker_revalidated") is not True
        or replay.get("primary_child_returncode") != 0
        or replay.get("replay_child_returncode") != 0
        or replay.get("primary_child_diagnostic") != "NOT_APPLICABLE"
        or replay.get("replay_child_diagnostic") != "NOT_APPLICABLE"
        or replay.get("primary_state") != "COMPLETED"
        or replay.get("replay_state") != "COMPLETED"
        or replay.get("primary_checker") != "CHECK_PASS"
        or replay.get("replay_checker") != "CHECK_PASS"
        or replay.get("primary_config_sha256") != replay.get("replay_config_sha256")
        or replay.get("primary_semantic_digest") != replay.get("replay_semantic_digest")
    ):
        raise ValueError("deterministic replay contract is not clean and exact")

    primary = _contained_reference(terminal_ref)
    replay_dir = _contained_reference(str(replay.get("replay_run_ref")))
    if not primary.is_dir() or not replay_dir.is_dir():
        raise ValueError("primary or replay evidence reference is not a directory")
    if revoked_result_for_directory(primary, repository_root=ROOT) is not None:
        raise ValueError("final primary Run is revoked")
    if revoked_result_for_directory(replay_dir, repository_root=ROOT) is not None:
        raise ValueError("final replay Run is revoked")

    summaries: list[dict[str, Any]] = []
    primary_checks: dict[str, dict[str, Any]] | None = None
    authority: dict[str, Any] | None = None
    for role, directory in (("PRIMARY", primary), ("REPLAY", replay_dir)):
        persisted = _json_object(directory / "checker.json")
        regenerated = check_evidence_directory(
            directory,
            repository_root=ROOT,
            official_source_required=True,
            source_revision_current_head_required=False,
        )
        if regenerated.to_builtins() != persisted:
            raise ValueError(f"{role} persisted checker differs from current read-only checker")
        checks = _named_checks(persisted)
        if (
            persisted.get("outcome") != "CHECK_PASS"
            or persisted.get("failure_codes") != []
            or persisted.get("mutated_run_evidence") is not False
            or not all(item.get("pass") is True for item in checks.values())
        ):
            raise ValueError(f"{role} checker is not an unqualified CHECK_PASS")
        required = {
            "engine_half_open_scoring_window",
            "installed_runtime_payload_proof",
            "qualified_profile_authority",
            "immutable_input_bindings",
            "source_revision",
            "official_process_network_isolation",
            "causal_fills",
            "maker_taker_fee_exactly_once",
        }
        if not required.issubset(checks):
            raise ValueError(f"{role} checker omits a required audit gate")
        window = checks["engine_half_open_scoring_window"]
        window_detail = window.get("window", {})
        config = LabRunConfig.from_json_bytes((directory / "lab_run_config.json").read_bytes())
        scoring_end_exclusive_ns = utc_datetime_to_ns(config.scoring_end_exclusive)
        if (
            window.get("callback_window_pass") is not True
            or window_detail.get("status") != "PASS"
            or window_detail.get("engine_received_post_boundary_data") is not False
            or window_detail.get("dropped_after_scoring_count") != 0
            or window_detail.get("point_events_at_scoring_end_included") is not False
            or window_detail.get("completed_interval_observations_at_scoring_end_included") is not True
            or window_detail.get("scoring_end_exclusive_ns")
            != scoring_end_exclusive_ns
            or window_detail.get("latest_qualified_valuation_observation_ns")
            != scoring_end_exclusive_ns
        ):
            raise ValueError(f"{role} half-open engine data window is not exact")
        result = _json_object(directory / "nautilus_result.json")
        if (
            result.get("engine_executed") is not True
            or result.get("engine_completed") is not True
            or result.get("engine_error") is not None
            or result.get("runtime_identity_verified") is not True
            or result.get("qualified_profile_authority_verified") is not True
            or result.get("project_financial_ledger") is not False
            or result.get("project_fee_postings") != 0
            or result.get("project_funding_postings") != 0
            or result.get("mark_fallback_accepted") is not False
        ):
            raise ValueError(f"{role} native engine/runtime disposition is invalid")
        runtime_identity = _json_object(directory / "runtime_identity.json")
        runtime_lock = RuntimeLock.from_json_bytes((directory / "runtime.lock.json").read_bytes())
        validate_persisted_runtime_identity(runtime_lock, runtime_identity)
        if (
            result.get("evidence_bindings", {}).get("runtime_identity_sha256")
            != sha256_file(directory / "runtime_identity.json")
        ):
            raise ValueError(f"{role} runtime identity is not bound into native evidence")
        authority_value = validate_persisted_profile_authority(
            _json_object(directory / "qualification_authority.json"),
            repository_root=ROOT,
            expected_profile_id=config.market_profile.value,
            expected_runtime_lock_sha256=config.runtime_lock_sha256,
        )
        if config.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
            reconciliation = checks.get("spot_cash_reconciliation", {})
            if (
                reconciliation.get("pass") is not True
                or reconciliation.get("errors") != []
                or reconciliation.get("reconciled_fill_count")
                != reconciliation.get("fill_count")
                or checks.get("spot_cash_no_short_or_borrow", {}).get("pass") is not True
            ):
                raise ValueError(f"{role} Spot cash reconciliation is incomplete")
        else:
            funding = checks.get("official_funding_exact_binding", {})
            mark = checks.get("official_mark_valuation_binding", {})
            native_funding = checks.get("native_funding_output_integrity", {})
            if (
                funding.get("pass") is not True
                or funding.get("mark_binding")
                != "LATEST_CAUSAL_AT_OR_BEFORE_FUNDING_TIMESTAMP"
                or funding.get("processed_checkpoint_count") != funding.get("source_event_count")
                or funding.get("native_settlement_count")
                != funding.get("applicable_open_position_boundaries")
                or funding.get("native_settlement_count")
                + funding.get("no_position_boundaries")
                != funding.get("source_event_count")
                or funding.get("mark_age_ns_min", -1) < 0
                or funding.get("mark_age_ns_max", 0)
                > funding.get("maximum_mark_staleness_ns", -1)
                or native_funding.get("actual_settlements")
                != funding.get("native_settlement_count")
                or native_funding.get("expected_settlements")
                != "DERIVED_BY_OFFICIAL_EXACT_BINDING"
                or mark.get("mark_fallback_accepted") is not False
                or mark.get("expected_mark_callback_count")
                != funding.get("source_mark_event_count")
            ):
                raise ValueError(f"{role} Perpetual Funding/Mark binding is incomplete")
        summaries.append(
            {
                "role": role,
                "reference": directory.relative_to(ROOT).as_posix(),
                "checker_outcome": persisted["outcome"],
                "semantic_digest": result["semantic_digest"],
                "runtime_identity_sha256": sha256_file(directory / "runtime_identity.json"),
            },
        )
        if role == "PRIMARY":
            primary_checks = checks
            authority = authority_value

    assert primary_checks is not None and authority is not None
    report, report_commit = _validate_report_snapshot(
        workflow=workflow,
        primary_ref=terminal_ref,
        authority=authority,
    )
    profile_summary: dict[str, Any]
    if workflow.protocol.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        reconciliation = primary_checks["spot_cash_reconciliation"]
        profile_summary = {
            "spot_reconciled_fill_count": reconciliation["reconciled_fill_count"],
            "spot_reconciliation_errors": reconciliation["errors"],
        }
    else:
        funding = primary_checks["official_funding_exact_binding"]
        profile_summary = {
            "funding_source_event_count": funding["source_event_count"],
            "funding_native_settlement_count": funding["native_settlement_count"],
            "funding_no_position_boundary_count": funding["no_position_boundaries"],
        }
    return {
        "trial_id": workflow.trial_id,
        "run_id": workflow.run_id,
        "market_profile": workflow.protocol.market_profile.value,
        "partition_role": workflow.partition_role.value,
        "primary_run_ref": terminal_ref,
        "replay_run_ref": replay["replay_run_ref"],
        "replay_identity": replay_identity,
        "report_id": report.report_id,
        "report_commit": report_commit,
        "evidence": summaries,
        **profile_summary,
    }


def validate(
    freeze_directory: Path = DEFAULT_FREEZE,
    *,
    require_remote_tip: bool = True,
) -> dict[str, Any]:
    failures: list[str] = []
    run_summaries: list[dict[str, Any]] = []
    latest_anchor_sha256 = "UNAVAILABLE"
    source_commit = "UNAVAILABLE"
    try:
        freeze = freeze_directory.resolve(strict=True)
        freeze.relative_to(ROOT)
        manifest = _json_object(freeze / "manifest.json")
        material = dict(manifest)
        declared_identity = material.pop("manifest_identity", None)
        workflow_files = manifest.get("workflow_files")
        registry = _contained_reference(str(manifest.get("qualified_profile_registry")))
        expected_files = {
            path.name for path in freeze.iterdir() if path.is_file() and path.name != "manifest.json"
        }
        if (
            declared_identity != canonical_sha256(material)
            or manifest.get("schema") != "comprehensive-audit-remediation-workflows-v1"
            or manifest.get("epoch") != EXPECTED_EPOCH
            or manifest.get("research_family_id") != EXPECTED_FAMILY
            or manifest.get("workflow_count") != 6
            or manifest.get("profile_count") != 2
            or manifest.get("partition_role") != "DEVELOPMENT"
            or manifest.get("final_holdout_used") is not False
            or manifest.get("profitability_claim_authorized") is not False
            or manifest.get("optimization_performed") is not False
            or not isinstance(workflow_files, list)
            or len(workflow_files) != 6
            or len(set(workflow_files)) != 6
            or set(workflow_files) != expected_files
            or sha256_file(registry) != manifest.get("qualified_profile_registry_sha256")
            or sha256_file(ROOT / "runtime.lock.json") != manifest.get("runtime_lock_sha256")
        ):
            raise ValueError("frozen workflow manifest is incomplete or has drifted")

        workflows: list[OwnerWorkflowInput] = []
        for name in workflow_files:
            if Path(str(name)).name != name or not str(name).endswith(".json"):
                raise ValueError("unsafe frozen workflow filename")
            path = freeze / str(name)
            workflow = OwnerWorkflowInput.from_json_bytes(path.read_bytes())
            committed_path = ROOT / "research/workflows" / f"{workflow.trial_id}.json"
            if (
                path.read_bytes() != committed_path.read_bytes()
                or workflow.partition_role is not PartitionRole.DEVELOPMENT
                or workflow.protocol.research_family_id != EXPECTED_FAMILY
                or workflow.trial_id != str(name)[:-5]
            ):
                raise ValueError(f"frozen/committed workflow mismatch: {name}")
            workflows.append(workflow)
        profiles = {
            profile: sum(item.protocol.market_profile is profile for item in workflows)
            for profile in EXPECTED_PROFILE_COUNTS
        }
        if profiles != EXPECTED_PROFILE_COUNTS:
            raise ValueError("final epoch does not contain three workflows per profile")

        journal = TrialJournal(ROOT / "research/trials.jsonl")
        records = journal.read_records()
        by_trial = {
            workflow.trial_id: [item for item in records if item.trial_id == workflow.trial_id]
            for workflow in workflows
        }
        terminal_records = []
        for workflow in workflows:
            trial = by_trial[workflow.trial_id]
            if (
                [item.state for item in trial]
                != [TrialState.PLANNED, TrialState.STARTED, TrialState.COMPLETED]
                or trial[-1].partition_role is not PartitionRole.DEVELOPMENT
                or trial[-1].result_exposed is not True
                or trial[-1].failure_or_block_reason != "OFFICIAL_RUN_COMPLETED"
                or trial[-1].research_family_id != EXPECTED_FAMILY
            ):
                raise ValueError(f"journal lifecycle is not exact: {workflow.trial_id}")
            terminal_records.append(trial[-1])
            try:
                run_summaries.append(
                    _validate_run(workflow=workflow, terminal_ref=trial[-1].result_ref),
                )
            except Exception as exc:
                failures.append(f"{workflow.trial_id}:{type(exc).__name__}:{exc}")

        holdout = HoldoutLockStore(ROOT / "research/holdout_lock.json").read()
        if holdout.entries:
            failures.append("final_holdout_lock_is_not_empty")
        anchors = HistoryAnchorStore(
            repository_root=ROOT,
            journal_path=ROOT / "research/trials.jsonl",
            holdout_path=ROOT / "research/holdout_lock.json",
            anchor_path=ROOT / "research/history_anchors.jsonl",
            require_remote_tip=require_remote_tip,
        )
        latest_anchor = anchors.reconcile_committed()
        latest_anchor_sha256 = latest_anchor.anchor_sha256

        if len(run_summaries) == 6 and not failures:
            latest_terminal = max(terminal_records, key=lambda item: item.journal_sequence)
            locator = OfficialEvidenceLocator(
                schema_version=1,
                protocol_id=latest_terminal.protocol_id,
                selected_trial_id=latest_terminal.trial_id,
                expected_history_anchor_sha256=latest_anchor.anchor_sha256,
                report_purpose="OFFICIAL_RESEARCH_REPORT",
            )
            rebuilt = OfficialEvidenceResolver(
                repository_root=ROOT,
                require_remote_tip=require_remote_tip,
            ).build_report(locator)
            persisted = ReportOutput.from_json_bytes(
                (ROOT / "research/reports" / f"{latest_terminal.trial_id}.json").read_bytes(),
            )
            if rebuilt.to_builtins() != persisted.to_builtins():
                failures.append("latest_authoritative_report_rebuild_mismatch")
        head = _git("rev-parse", "HEAD")
        if head.returncode != 0:
            failures.append("git_head_unavailable")
        else:
            source_commit = head.stdout.decode("utf-8").strip()
    except Exception as exc:
        failures.append(f"validator:{type(exc).__name__}:{exc}")

    result = {
        "schema": "comprehensive-audit-remediation-research-validation-v1",
        "audit_id": "COMPREHENSIVE_AUDIT_REMEDIATION_001",
        "status": "PASS" if not failures else "FAIL",
        "source_commit": source_commit,
        "epoch": EXPECTED_EPOCH,
        "research_family_id": EXPECTED_FAMILY,
        "workflow_count": 6,
        "validated_run_count": len(run_summaries),
        "validated_evidence_directory_count": sum(
            len(item.get("evidence", ())) for item in run_summaries
        ),
        "latest_history_anchor_sha256": latest_anchor_sha256,
        "holdout_entry_count": 0,
        "final_holdout_used": False,
        "profitability_claim_authorized": False,
        "runs": sorted(run_summaries, key=lambda item: item["trial_id"]),
        "failures": failures,
    }
    result["validation_identity"] = canonical_sha256(result)
    return result


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze-directory", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = validate(arguments.freeze_directory)
    payload = canonical_json_bytes(result) + b"\n"
    if arguments.output is not None:
        output = arguments.output if arguments.output.is_absolute() else ROOT / arguments.output
        if output.exists() and output.read_bytes() != payload:
            raise FileExistsError(f"refusing to replace audit research validation: {output}")
        if not output.exists():
            _write_atomic(output, payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
