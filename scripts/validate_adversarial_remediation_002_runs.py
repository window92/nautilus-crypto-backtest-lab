#!/usr/bin/env python3
"""Fail-closed R2 acceptance of six Development Runs and six replays.

This validator deliberately has no compatibility path for the superseded
audit workflow format.  A success means that the exact R2 plan, current
DatasetRelease v2 inventories, component results, Official seals, independent
financial checks, deterministic replays, daily marked-portfolio metrics,
research history and additive invalidation registry all agree.  It never
executes a strategy and never reads a Final Holdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.data import DatasetRawInventory
from crypto_lab.data import DatasetRelease
from crypto_lab.data import FULL_RAW_INVENTORY_NORMALIZER_VERSION
from crypto_lab.data import verify_dataset_raw_inventory
from crypto_lab.diagnostics import derive_performance_diagnostics
from crypto_lab.diagnostics import reconcile_diagnostic_resolution
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.m3 import ProfileQualificationState
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.owner import OwnerWorkflowInput
from crypto_lab.owner import OwnerWorkflowPurpose
from crypto_lab.reporting import OFFICIAL_ANNUALIZATION_DAYS
from crypto_lab.reporting import OFFICIAL_EQUITY_OBSERVATION_BASIS
from crypto_lab.reporting import OFFICIAL_RISK_MINIMUM_SAMPLE_COUNT
from crypto_lab.reporting import PerformanceDiagnostics
from crypto_lab.reporting import REQUIRED_SCIENTIFIC_LIMITATIONS
from crypto_lab.reporting import ReportOutput
from crypto_lab.research import HoldoutLockStore
from crypto_lab.research import PartitionRole
from crypto_lab.research import TrialJournal
from crypto_lab.research import TrialState
from crypto_lab.result_status import FinancialResultStatus
from crypto_lab.result_status import HistoricalRunStatus
from crypto_lab.result_status import R2_EXPECTED_HISTORICAL_RESULTS
from crypto_lab.result_status import RESULT_STATUS_V2_SCHEMA
from crypto_lab.result_status import load_historical_result_registry
from crypto_lab.result_status import require_active_result
from crypto_lab.result_status import resolve_result_status
from crypto_lab.sealing import OfficialSealOutcome
from crypto_lab.sealing import verify_official_seal
from crypto_lab.status import FailureCode


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_EPOCH = "adversarial-remediation-002"
EXPECTED_BRANCH = "fix/adversarial-audit-remediation-002"
EXPECTED_PLAN_SCHEMA = "adversarial-remediation-002-official-run-plan-v1"
EXPECTED_RESEARCH_FAMILY = "BTCUSDT_WEEKLY_TSMOM28_V1_ADVERSARIAL_REMEDIATION_002"
RESULT_STATUS_REF = Path(
    "evidence/audit/adversarial-remediation-002/historical-result-status.json",
)
PROFILE_ORDER = (
    MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
    MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
)
COMPONENT_PASS = "COMPONENT_CHECK_PASS"
SEAL_PASS = "OFFICIAL_SEAL_PASS"


@dataclass(frozen=True)
class R2ValidationFailure(ValueError):
    code: FailureCode
    stage: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code.value}:{self.stage}:{self.detail}"

    def to_builtins(self) -> dict[str, str]:
        return {
            "failure_code": self.code.value,
            "stage": self.stage,
            "detail": self.detail,
        }


def _reject(code: FailureCode, stage: str, detail: str) -> None:
    raise R2ValidationFailure(code, stage, detail)


def _strict_json(payload: bytes, *, stage: str) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, f"duplicate JSON field: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload,
            object_pairs_hook=pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {item}"),
            ),
        )
    except R2ValidationFailure:
        raise
    except Exception as exc:
        _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, f"invalid JSON: {exc}")
    if not isinstance(value, dict):
        _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, "one JSON object is required")
    return value


def _read_json(path: Path, *, stage: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, f"missing regular file: {path}")
        return _strict_json(path.read_bytes(), stage=stage)
    except R2ValidationFailure:
        raise
    except Exception as exc:
        _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, f"cannot read {path}: {exc}")


def _git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", "--no-replace-objects", *arguments),
        cwd=repository,
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        },
        check=False,
        capture_output=True,
    )


def _git_text(repository: Path, *arguments: str, stage: str = "git") -> str:
    process = _git(repository, *arguments)
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).decode("utf-8", errors="replace").strip()
        _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, stage, detail or "Git command failed")
    return process.stdout.decode("utf-8").strip()


def _safe_repository_path(
    repository: Path,
    reference: str | Path,
    *,
    stage: str,
    directory: bool = False,
) -> Path:
    relative = Path(reference)
    if (
        relative.is_absolute()
        or not relative.parts
        or ".." in relative.parts
        or relative.as_posix() != str(reference)
    ):
        _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, f"unsafe reference: {reference}")
    candidate = repository / relative
    cursor = repository
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, f"symlinked reference: {reference}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repository)
    except Exception as exc:
        _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, f"unresolved reference {reference}: {exc}")
    if directory and not resolved.is_dir():
        _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, f"directory required: {reference}")
    if not directory and not resolved.is_file():
        _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, f"regular file required: {reference}")
    return resolved


def _require_committed(
    repository: Path,
    path: Path,
    *,
    stage: str,
    commit: str = "HEAD",
) -> str:
    try:
        relative = path.resolve(strict=True).relative_to(repository).as_posix()
    except Exception as exc:
        _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, f"path is outside repository: {exc}")
    process = _git(repository, "show", f"{commit}:{relative}")
    if process.returncode != 0 or process.stdout != path.read_bytes():
        _reject(
            FailureCode.EVIDENCE_INCOMPLETE,
            stage,
            f"working bytes are not exact committed bytes at {commit}:{relative}",
        )
    return relative


def _plan_execution_shape(item: Any, *, sequence: int, epoch: str) -> None:
    stage = f"plan.execution[{sequence}]"
    expected_fields = {
        "command_argv",
        "component_validation_required",
        "deterministic_replay_required",
        "expected_copies",
        "final_holdout_used",
        "official_seal_required",
        "owner_fresh_child_count",
        "partition_role",
        "profile",
        "profitability_claim_authorized",
        "purpose",
        "result_summary",
        "run_id",
        "sequence",
        "trial_id",
        "workflow_input",
        "workflow_input_sha256",
    }
    if not isinstance(item, dict) or set(item) != expected_fields:
        _reject(FailureCode.RESEARCH_PROTOCOL_INVALID, stage, "execution item schema differs")
    try:
        profile = MarketProfile(str(item["profile"]))
        purpose = OwnerWorkflowPurpose(str(item["purpose"]))
    except ValueError as exc:
        _reject(FailureCode.RESEARCH_PROTOCOL_INVALID, stage, f"closed vocabulary mismatch: {exc}")
    expected_profile = PROFILE_ORDER[0 if sequence <= 3 else 1]
    expected_purpose = (
        OwnerWorkflowPurpose.BENCHMARK_STUDY
        if sequence in {1, 4}
        else OwnerWorkflowPurpose.OWNER_STUDY
    )
    digest = item.get("workflow_input_sha256")
    if (
        item.get("sequence") != sequence
        or profile is not expected_profile
        or purpose is not expected_purpose
        or item.get("partition_role") != PartitionRole.DEVELOPMENT.value
        or item.get("expected_copies") != ["PRIMARY", "REPLAY"]
        or item.get("owner_fresh_child_count") != 2
        or item.get("component_validation_required") != COMPONENT_PASS
        or item.get("official_seal_required") != SEAL_PASS
        or item.get("deterministic_replay_required") != "PASS"
        or item.get("final_holdout_used") is not False
        or item.get("profitability_claim_authorized") is not False
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or not str(item.get("trial_id", "")).startswith(f"{epoch}-")
        or not str(item.get("run_id", "")).startswith(f"{epoch}-")
    ):
        _reject(FailureCode.RESEARCH_PROTOCOL_INVALID, stage, "unsafe R2 execution disposition")
    command = item.get("command_argv")
    expected_command = [
        "/usr/bin/env",
        "-i",
        "PATH=/usr/bin:/bin",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        "TZ=UTC",
        str(ROOT / ".venv/bin/python"),
        "-I",
        "-P",
        "-S",
        "-B",
        "-X",
        "pycache_prefix=/dev/null",
        str(ROOT / "scripts/isolated_runtime_bootstrap.py"),
        "--authority",
        str(ROOT / "runtime-bootstrap-authority.json"),
        "--repository",
        str(ROOT),
        "--entrypoint",
        "crypto_lab.owner:main",
        "--",
        "--input",
        str(item.get("workflow_input")),
        "--repository",
        str(ROOT),
        "--output",
        str(item.get("result_summary")),
    ]
    if (
        command != expected_command
        or any(
            "FINAL_HOLDOUT" in value
            or "live" in value.lower()
            or value.startswith("PYTHONPATH=")
            or value.endswith("scripts/run_owner_workflow.py")
            for value in command
        )
    ):
        _reject(FailureCode.RESEARCH_PROTOCOL_INVALID, stage, "execution command is unsafe")


def validate_plan_payload(value: dict[str, Any], *, epoch: str = EXPECTED_EPOCH) -> None:
    """Validate the immutable plan without resolving any filesystem reference."""

    stage = "plan"
    expected_fields = {
        "data_rebuild_validation",
        "dataset_releases",
        "epoch",
        "execution",
        "execution_order",
        "final_holdout_used",
        "fresh_process_run_count",
        "frozen_at_utc",
        "live_trading_used",
        "owner_checkpoint_contract",
        "owner_executed",
        "plan_identity",
        "preparation_only",
        "primary_run_count",
        "profitability_claim_authorized",
        "protocol_ids",
        "qualification_registry",
        "replay_run_count",
        "research_family_id",
        "runtime_lock_sha256",
        "runtime_bootstrap_authority_sha256",
        "schema",
        "source",
        "workflow_count",
    }
    material = dict(value)
    declared = material.pop("plan_identity", None)
    execution = value.get("execution")
    source = value.get("source")
    checkpoint = value.get("owner_checkpoint_contract")
    if (
        set(value) != expected_fields
        or value.get("schema") != EXPECTED_PLAN_SCHEMA
        or value.get("epoch") != epoch
        or declared != canonical_sha256(material)
        or value.get("workflow_count") != 6
        or value.get("primary_run_count") != 6
        or value.get("replay_run_count") != 6
        or value.get("fresh_process_run_count") != 12
        or value.get("research_family_id") != EXPECTED_RESEARCH_FAMILY
        or value.get("execution_order")
        != "SPOT_BENCHMARK_CANDIDATE_A_CANDIDATE_B_THEN_PERPETUAL_SAME_ORDER"
        or value.get("final_holdout_used") is not False
        or value.get("live_trading_used") is not False
        or value.get("profitability_claim_authorized") is not False
        or value.get("preparation_only") is not True
        or value.get("owner_executed") is not False
        or not isinstance(execution, list)
        or len(execution) != 6
        or not isinstance(source, dict)
        or set(source) != {"branch", "head", "remote_ref", "remote_tip", "source_tree"}
        or source.get("branch") != EXPECTED_BRANCH
        or source.get("head") != source.get("remote_tip")
        or not isinstance(checkpoint, dict)
        or checkpoint.get("squash_rebase_force_push_forbidden") is not True
        or checkpoint.get("normal_push_required_after_each") is not True
    ):
        _reject(FailureCode.RESEARCH_PROTOCOL_INVALID, stage, "plan root contract differs")
    for sequence, item in enumerate(execution, start=1):
        _plan_execution_shape(item, sequence=sequence, epoch=epoch)
    if len({item["trial_id"] for item in execution}) != 6 or len(
        {item["run_id"] for item in execution},
    ) != 6:
        _reject(FailureCode.RESEARCH_PROTOCOL_INVALID, stage, "duplicate Run or Trial identity")
    protocol_ids = value.get("protocol_ids")
    if (
        not isinstance(protocol_ids, list)
        or len(protocol_ids) != 2
        or len(set(protocol_ids)) != 2
    ):
        _reject(FailureCode.RESEARCH_PROTOCOL_INVALID, stage, "two profile protocols are required")


def validate_component_payload(value: dict[str, Any], *, stage: str) -> dict[str, dict[str, Any]]:
    raw_checks = value.get("checks")
    if (
        value.get("outcome") != COMPONENT_PASS
        or value.get("failure_codes") != []
        or value.get("mutated_run_evidence") is not False
        or not isinstance(raw_checks, list)
        or not raw_checks
        or not all(isinstance(item, dict) and item.get("pass") is True for item in raw_checks)
    ):
        _reject(FailureCode.CHECKER_FAILURE, stage, "component result is not an exact clean pass")
    names = [item.get("name") for item in raw_checks]
    if not all(isinstance(name, str) and name for name in names) or len(names) != len(set(names)):
        _reject(FailureCode.CHECKER_FAILURE, stage, "component check names are malformed")
    return dict(zip(names, raw_checks, strict=True))


def validate_replay_payload(
    value: dict[str, Any],
    *,
    trial_id: str,
    primary_ref: str,
) -> tuple[str, str]:
    stage = f"replay:{trial_id}"
    material = dict(value)
    declared = material.pop("replay_identity", None)
    exact_fields = {
        "fresh_processes",
        "primary_child_diagnostic",
        "primary_child_returncode",
        "primary_component_validation",
        "primary_config_sha256",
        "primary_official_seal",
        "primary_run_ref",
        "primary_semantic_digest",
        "primary_state",
        "read_only_checker_revalidated",
        "replay_child_diagnostic",
        "replay_child_returncode",
        "replay_component_validation",
        "replay_config_sha256",
        "replay_identity",
        "replay_official_seal",
        "replay_run_ref",
        "replay_semantic_digest",
        "replay_state",
        "result",
        "schema",
        "trial_id",
    }
    required = {
        "fresh_processes": True,
        "primary_component_validation": COMPONENT_PASS,
        "primary_official_seal": SEAL_PASS,
        "read_only_checker_revalidated": True,
        "replay_component_validation": COMPONENT_PASS,
        "replay_official_seal": SEAL_PASS,
        "result": "PASS",
    }
    if (
        set(value) != exact_fields
        or value.get("schema") != "owner-deterministic-replay-v2"
        or declared != canonical_sha256(material)
        or value.get("trial_id") != trial_id
        or value.get("primary_run_ref") != primary_ref
        or any(value.get(name) != expected for name, expected in required.items())
        or value.get("primary_state") != "COMPLETED"
        or value.get("replay_state") != "COMPLETED"
        or value.get("primary_child_returncode") != 0
        or value.get("replay_child_returncode") != 0
        or value.get("primary_child_diagnostic") != "NOT_APPLICABLE"
        or value.get("replay_child_diagnostic") != "NOT_APPLICABLE"
        or value.get("primary_config_sha256") != value.get("replay_config_sha256")
        or value.get("primary_semantic_digest") != value.get("replay_semantic_digest")
        or not isinstance(value.get("replay_run_ref"), str)
        or not value.get("replay_run_ref")
    ):
        _reject(FailureCode.DETERMINISM_FAILURE, stage, "replay semantic contract differs")
    return str(value["replay_run_ref"]), str(declared)


def validate_performance_payload(
    value: dict[str, Any],
    *,
    run_id: str,
    scoring_start: Any,
    scoring_end_exclusive: Any,
) -> None:
    stage = f"performance:{run_id}"
    daily_returns = value.get("daily_returns")
    if (
        value.get("schema_version") != 2
        or value.get("run_id") != run_id
        or value.get("equity_observation_basis") != OFFICIAL_EQUITY_OBSERVATION_BASIS
        or value.get("valuation_frequency") != "DAILY_MARKED_PORTFOLIO_EQUITY_UTC"
        or str(value.get("annualization_days")) != str(OFFICIAL_ANNUALIZATION_DAYS)
        or value.get("minimum_risk_sample_count") != OFFICIAL_RISK_MINIMUM_SAMPLE_COUNT
        or value.get("intraday_drawdown_captured") is not False
        or value.get("scored_start") != scoring_start
        or value.get("scoring_end_exclusive") != scoring_end_exclusive
        or value.get("scientific_limitations") != list(REQUIRED_SCIENTIFIC_LIMITATIONS)
        or not isinstance(daily_returns, list)
        or value.get("daily_return_sample_count") != len(daily_returns)
    ):
        _reject(FailureCode.PERFORMANCE_METRICS_INVALID, stage, "official daily metric basis differs")


def validate_claim_payload(value: Mapping[str, Any], *, trial_id: str) -> None:
    stage = f"report:{trial_id}"
    limitations = value.get("qualification_limitations")
    trial_history = value.get("trial_history")
    required_claim_fields = {
        "claim_scope": "INSTRUMENT_ONLY",
        "drawdown_frequency": "DAILY_NOT_INTRADAY",
        "estimated_bar_execution": True,
        "estimated_fee_limitation": True,
        "historical_exchange_filter_claim": "NOT_FULLY_PROVEN",
        "historical_fee_tier_claim": "NOT_PROVEN",
        "historical_spread_claim": "NOT_MODELED",
        "liquidation_claim": "NOT_MODELED",
        "market_impact_claim": "NOT_MODELED",
        "perpetual_leverage": "FIXED_AT_ONE_IN_V1",
        "queue_position_claim": "NOT_MODELED",
        "terminal_position_disposition": "CAUSALLY_MARKED_NOT_ACTUALLY_CLOSED",
    }
    if (
        value.get("selected_trial_id") != trial_id
        or value.get("report_purpose") != "OFFICIAL_RESEARCH_REPORT"
        or value.get("research_intent") != "EXPLORATORY"
        or value.get("mechanical_integrity") != "PASS"
        or value.get("profitability_claim_is_real") is not False
        or value.get("live_trading_authorized") is not False
        or value.get("final_holdout_used") is not False
        or value.get("research_eligibility")
        not in {"EXPLORATORY_ONLY", "INELIGIBLE", "BLOCKED"}
        or value.get("development_only") is not True
        or any(value.get(name) != expected for name, expected in required_claim_fields.items())
        or not isinstance(trial_history, (list, tuple))
        or value.get("trial_count") != len(trial_history)
        or not trial_history
        or any(
            not isinstance(item, Mapping)
            or not str(item.get("trial_id", "")).startswith(f"{EXPECTED_EPOCH}-")
            for item in trial_history
        )
        or trial_id not in {str(item.get("trial_id")) for item in trial_history}
        or not isinstance(limitations, (list, tuple))
        or not set(REQUIRED_SCIENTIFIC_LIMITATIONS).issubset(limitations)
    ):
        _reject(FailureCode.CLAIM_INELIGIBLE, stage, "report exceeds Development-only authority")


def validate_rebuild_payload(
    value: dict[str, Any],
    *,
    releases: dict[MarketProfile, DatasetRelease],
) -> None:
    stage = "dataset-rebuild"
    if (
        value.get("schema")
        != "free-official-binance-deterministic-rebuild-validation-v2-full-raw-inventory"
        or value.get("status") != "PASS"
        or value.get("strategy_run") is not False
        or value.get("official_trial") is not False
        or value.get("network_used") is not False
        or value.get("primary_readonly_gate") != value.get("independent_readonly_gate")
    ):
        _reject(FailureCode.DATASET_RAW_INVENTORY_MISMATCH, stage, "rebuild root proof differs")
    comparison = value.get("comparison")
    materialized = value.get("materialized_release_artifacts")
    catalogs = value.get("nautilus_catalog_validation")
    gate = value.get("primary_readonly_gate")
    if not all(isinstance(item, dict) for item in (comparison, materialized, catalogs, gate)):
        _reject(FailureCode.DATASET_RAW_INVENTORY_MISMATCH, stage, "rebuild proof is incomplete")
    expected_ids = sorted(release.dataset_release_id for release in releases.values())
    inventory_results = gate.get("full_raw_inventory_results")
    if (
        sorted(comparison.get("dataset_release_ids", [])) != expected_ids
        or not isinstance(inventory_results, list)
        or len(inventory_results) != 2
    ):
        _reject(FailureCode.DATASET_RAW_INVENTORY_MISMATCH, stage, "release/DB inventory set differs")
    by_profile = {
        str(item.get("market_profile")): item
        for item in inventory_results
        if isinstance(item, dict)
    }
    for profile, release in releases.items():
        inventory = release.raw_inventory
        if not isinstance(inventory, DatasetRawInventory):
            _reject(FailureCode.DATASET_RAW_INVENTORY_MISMATCH, stage, "typed inventory missing")
        rebuilt = by_profile.get(profile.value)
        frozen = materialized.get(profile.value)
        catalog = catalogs.get(profile.value)
        if (
            not isinstance(rebuilt, dict)
            or rebuilt.get("dataset_release_id") != release.dataset_release_id
            or rebuilt.get("raw_inventory_identity") != inventory.raw_inventory_identity
            or rebuilt.get("raw_object_count") != inventory.raw_object_count
            or rebuilt.get("four_way_equality") is not True
            or not isinstance(frozen, dict)
            or frozen.get("dataset_release_id") != release.dataset_release_id
            or frozen.get("catalog_identity") != release.catalog_identity
            or frozen.get("raw_inventory_identity") != inventory.raw_inventory_identity
            or frozen.get("raw_inventory_object_count") != inventory.raw_object_count
            or not isinstance(catalog, dict)
            or catalog.get("status") != "PASS"
            or catalog.get("catalog_identity") != release.catalog_identity
        ):
            _reject(
                FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
                stage,
                f"four-way inventory proof differs for {profile.value}",
            )


def _validate_plan_files(
    repository: Path,
    plan: dict[str, Any],
) -> tuple[
    list[OwnerWorkflowInput],
    dict[MarketProfile, DatasetRelease],
    Path,
]:
    stage = "plan-files"
    source = plan["source"]
    head = str(source["head"])
    if sha256_file(repository / "runtime.lock.json") != plan.get("runtime_lock_sha256"):
        _reject(FailureCode.RUNTIME_LOCK_MISMATCH, stage, "RuntimeLock differs from plan")
    bootstrap_authority = repository / "runtime-bootstrap-authority.json"
    _require_committed(repository, bootstrap_authority, stage=stage, commit=head)
    if sha256_file(bootstrap_authority) != plan.get("runtime_bootstrap_authority_sha256"):
        _reject(
            FailureCode.RUNTIME_STARTUP_MISMATCH,
            stage,
            "Runtime bootstrap authority differs from plan",
        )
    if _git_text(repository, "rev-parse", head, stage=stage) != head:
        _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, stage, "plan source commit does not resolve")
    if _git_text(repository, "rev-parse", f"{head}^{{tree}}", stage=stage) != source["source_tree"]:
        _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, stage, "plan source tree differs")
    if _git(repository, "merge-base", "--is-ancestor", head, "HEAD").returncode != 0:
        _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, stage, "plan source is not an ancestor")

    registry_material = plan.get("qualification_registry")
    if not isinstance(registry_material, dict):
        _reject(FailureCode.DOWNSTREAM_CONTRACT_FAILURE, stage, "qualification binding missing")
    registry_path = _safe_repository_path(
        repository,
        str(registry_material.get("path")),
        stage=stage,
    )
    _require_committed(repository, registry_path, stage=stage, commit=head)
    if sha256_file(registry_path) != registry_material.get("sha256"):
        _reject(FailureCode.DOWNSTREAM_CONTRACT_FAILURE, stage, "qualification hash differs")
    registry = QualifiedProfileRegistry.from_json_bytes(registry_path.read_bytes())
    if (
        registry.schema_version != 2
        or registry.registry_content_sha256 != registry_material.get("registry_content_sha256")
        or [record.profile_id for record in registry.records] != list(PROFILE_ORDER)
        or any(
            record.qualification_state is not ProfileQualificationState.QUALIFIED
            or record.checker_result != COMPONENT_PASS
            or record.replay_result != "PASS"
            for record in registry.records
        )
    ):
        _reject(FailureCode.DOWNSTREAM_CONTRACT_FAILURE, stage, "qualification is not R2-current")

    release_material = plan.get("dataset_releases")
    if not isinstance(release_material, dict) or set(release_material) != {
        profile.value for profile in PROFILE_ORDER
    }:
        _reject(FailureCode.DATASET_RAW_INVENTORY_MISMATCH, stage, "release set differs")
    releases: dict[MarketProfile, DatasetRelease] = {}
    for profile in PROFILE_ORDER:
        binding = release_material[profile.value]
        if not isinstance(binding, dict):
            _reject(FailureCode.DATASET_RAW_INVENTORY_MISMATCH, stage, "release binding malformed")
        path = _safe_repository_path(repository, str(binding.get("path")), stage=stage)
        _require_committed(repository, path, stage=stage, commit=head)
        release = DatasetRelease.from_json_bytes(path.read_bytes())
        inventory = release.raw_inventory
        if (
            sha256_file(path) != binding.get("sha256")
            or release.schema_version != 2
            or not release.has_full_raw_inventory
            or release.normalizer_version != FULL_RAW_INVENTORY_NORMALIZER_VERSION
            or release.market_profile is not profile
            or not isinstance(inventory, DatasetRawInventory)
            or binding.get("dataset_release_id") != release.dataset_release_id
            or binding.get("catalog_identity") != release.catalog_identity
            or binding.get("raw_inventory_identity") != inventory.raw_inventory_identity
            or binding.get("raw_object_count") != inventory.raw_object_count
            or binding.get("schema_version") != 2
        ):
            _reject(
                FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
                stage,
                f"release binding differs for {profile.value}",
            )
        try:
            verify_dataset_raw_inventory(release, repository / "data")
        except Exception as exc:
            raw_code = getattr(exc, "code", FailureCode.DATA_HASH_MISMATCH.value)
            try:
                failure_code = FailureCode(raw_code)
            except ValueError:
                failure_code = FailureCode.DATA_HASH_MISMATCH
            _reject(failure_code, stage, f"Raw inventory verification failed: {exc}")
        releases[profile] = release

    rebuild_binding = plan.get("data_rebuild_validation")
    if not isinstance(rebuild_binding, dict):
        _reject(FailureCode.DATASET_RAW_INVENTORY_MISMATCH, stage, "rebuild binding missing")
    rebuild_path = _safe_repository_path(
        repository,
        str(rebuild_binding.get("path")),
        stage=stage,
    )
    _require_committed(repository, rebuild_path, stage=stage, commit=head)
    if sha256_file(rebuild_path) != rebuild_binding.get("sha256"):
        _reject(FailureCode.DATASET_RAW_INVENTORY_MISMATCH, stage, "rebuild hash differs")
    rebuild = _read_json(rebuild_path, stage="dataset-rebuild")
    validate_rebuild_payload(rebuild, releases=releases)

    workflows: list[OwnerWorkflowInput] = []
    records = {record.profile_id: record for record in registry.records}
    for item in plan["execution"]:
        trial_id = str(item["trial_id"])
        path = _safe_repository_path(
            repository,
            f"research/workflows/{trial_id}.json",
            stage=f"workflow:{trial_id}",
        )
        _require_committed(repository, path, stage=f"workflow:{trial_id}")
        if sha256_file(path) != item["workflow_input_sha256"]:
            _reject(FailureCode.RESEARCH_PROTOCOL_INVALID, stage, "workflow hash differs from plan")
        workflow = OwnerWorkflowInput.from_json_bytes(path.read_bytes())
        profile = workflow.protocol.market_profile
        claim_tokens = {
            token.strip()
            for token in workflow.protocol.claim_basis.split(";")
            if token.strip()
        }
        required_claim_tokens = {
            *REQUIRED_SCIENTIFIC_LIMITATIONS,
            "FINAL_HOLDOUT_USED_FALSE",
            "REAL_PROFITABILITY_CLAIM_FALSE",
            "LIVE_TRADING_AUTHORIZATION_FALSE",
        }
        if (
            workflow.trial_id != trial_id
            or workflow.run_id != item["run_id"]
            or workflow.workflow_purpose.value != item["purpose"]
            or workflow.partition_role is not PartitionRole.DEVELOPMENT
            or workflow.dataset_release_id != releases[profile].dataset_release_id
            or workflow.qualified_profile_record_id
            != records[profile].qualified_profile_record_id
            or workflow.protocol.protocol_id not in plan["protocol_ids"]
            or workflow.protocol.research_family_id != plan["research_family_id"]
            or not required_claim_tokens.issubset(claim_tokens)
        ):
            _reject(FailureCode.RESEARCH_PROTOCOL_INVALID, stage, "workflow differs from R2 plan")
        workflows.append(workflow)
    return workflows, releases, registry_path


def _validate_one_evidence(
    repository: Path,
    directory: Path,
    *,
    workflow: OwnerWorkflowInput,
    role: str,
    expected_release: DatasetRelease,
) -> dict[str, Any]:
    stage = f"{workflow.trial_id}:{role}"
    require_active_result(directory, repository_root=repository)
    component_path = directory / "component_validation.json"
    component = _read_json(component_path, stage=stage)
    checks = validate_component_payload(component, stage=stage)
    regenerated = check_evidence_directory(
        directory,
        repository_root=repository,
        official_source_required=True,
        source_revision_current_head_required=False,
    ).to_builtins()
    if regenerated != component:
        _reject(FailureCode.CHECKER_FAILURE, stage, "persisted component result is stale")
    seal = verify_official_seal(
        directory,
        repository_root=repository,
        source_revision_current_head_required=False,
    )
    if seal.outcome is not OfficialSealOutcome.OFFICIAL_SEAL_PASS:
        _reject(
            FailureCode.OFFICIAL_SEAL_FAILURE,
            stage,
            "Official seal failed: " + ",".join(seal.failure_codes),
        )
    status = _read_json(directory / "status.json", stage=stage)
    result = _read_json(directory / "nautilus_result.json", stage=stage)
    config = LabRunConfig.from_json_bytes((directory / "lab_run_config.json").read_bytes())
    release = DatasetRelease.from_json_bytes((directory / "dataset_release.json").read_bytes())
    common_checks = {
        "dataset_binding",
        "dataset_source_roles",
        "engine_half_open_scoring_window",
        "installed_runtime_payload_proof",
        "submitted_signal_bar_eligibility",
        "maker_taker_fee_exactly_once",
        "terminal_fill_boundary",
    }
    profile_checks = (
        {"spot_cash_reconciliation", "spot_cash_no_short_or_borrow"}
        if workflow.protocol.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else {
            "official_funding_exact_binding",
            "official_mark_valuation_binding",
            "perpetual_full_financial_reconciliation",
        }
    )
    if (
        not common_checks.issubset(checks)
        or not profile_checks.issubset(checks)
        or config.run_id != workflow.run_id
        or config.market_profile is not workflow.protocol.market_profile
        or config.dataset_release_id != workflow.dataset_release_id
        or config.research_protocol_id != workflow.protocol.protocol_id
        or config.scoring_start != workflow.scoring_start
        or config.scoring_end_exclusive != workflow.scoring_end_exclusive
        or release != expected_release
        or status.get("state") != "COMPLETED"
        or status.get("component_validation_outcome") != COMPONENT_PASS
        or status.get("official_publication_state") != "ROOT_ATTESTATION_READY"
        or result.get("engine_executed") is not True
        or result.get("engine_completed") is not True
        or result.get("engine_error") is not None
        or result.get("project_financial_ledger") is not False
        or result.get("project_fee_postings") != 0
        or result.get("project_funding_postings") != 0
        or result.get("execution_data_window", {}).get("engine_received_post_boundary_data")
        is not False
    ):
        _reject(FailureCode.DOWNSTREAM_CONTRACT_FAILURE, stage, "Run root contracts differ")
    reconciliation = checks[
        "spot_cash_reconciliation"
        if config.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else "perpetual_full_financial_reconciliation"
    ]
    if reconciliation.get("pass") is not True or reconciliation.get("errors") != []:
        _reject(
            FailureCode.PERPETUAL_RECONCILIATION_FAILURE
            if config.market_profile
            is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
            else FailureCode.SPOT_SHORT_OR_BORROW_DETECTED,
            stage,
            "independent financial reconciliation is not clean",
        )
    return {
        "role": role,
        "reference": directory.relative_to(repository).as_posix(),
        "component_validation": COMPONENT_PASS,
        "official_seal": SEAL_PASS,
        "semantic_digest": str(result.get("semantic_digest")),
        "reconciliation_check": reconciliation["name"],
    }


def _report_addition_commit(repository: Path, relative: str) -> str:
    commits = _git_text(
        repository,
        "log",
        "--format=%H",
        "--diff-filter=A",
        "--",
        relative,
        stage="report-history",
    ).splitlines()
    if len(commits) != 1:
        _reject(
            FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "report-history",
            f"one report addition commit required for {relative}",
        )
    return commits[0]


def _git_blob(repository: Path, commit: str, relative: str, *, stage: str) -> bytes:
    process = _git(repository, "show", f"{commit}:{relative}")
    if process.returncode != 0:
        _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, stage, f"missing {commit}:{relative}")
    return process.stdout


def _validate_report_and_metrics(
    repository: Path,
    *,
    workflow: OwnerWorkflowInput,
    primary: Path,
    qualification_registry: Path,
) -> dict[str, Any]:
    trial_id = workflow.trial_id
    stage = f"report:{trial_id}"
    performance_path = _safe_repository_path(
        repository,
        f"research/performance/{workflow.run_id}.json",
        stage=stage,
    )
    diagnostic_path = _safe_repository_path(
        repository,
        f"research/diagnostics/{workflow.run_id}.json",
        stage=stage,
    )
    report_path = _safe_repository_path(
        repository,
        f"research/reports/{trial_id}.json",
        stage=stage,
    )
    markdown_path = _safe_repository_path(
        repository,
        f"research/reports/{trial_id}.md",
        stage=stage,
    )
    for path in (performance_path, diagnostic_path, report_path, markdown_path):
        _require_committed(repository, path, stage=stage)

    performance_raw = _read_json(performance_path, stage=stage)
    validate_performance_payload(
        performance_raw,
        run_id=workflow.run_id,
        scoring_start=workflow.scoring_start.isoformat().replace("+00:00", "Z"),
        scoring_end_exclusive=workflow.scoring_end_exclusive.isoformat().replace("+00:00", "Z"),
    )
    performance = PerformanceDiagnostics.from_json_bytes(performance_path.read_bytes())
    derived = derive_performance_diagnostics(
        run_dir=primary,
        protocol=workflow.protocol,
        benchmark_directory=repository / "research/benchmarks",
        resolve_benchmark=(
            workflow.workflow_purpose is not OwnerWorkflowPurpose.BENCHMARK_STUDY
        ),
    )
    if performance != derived:
        _reject(FailureCode.PERFORMANCE_METRICS_INVALID, stage, "performance bytes are stale")
    reconcile_diagnostic_resolution(
        path=diagnostic_path,
        run_dir=primary,
        protocol=workflow.protocol,
        benchmark_directory=repository / "research/benchmarks",
    )

    report = ReportOutput.from_json_bytes(report_path.read_bytes())
    validate_claim_payload(report.json_payload, trial_id=trial_id)
    if (
        report.json_payload.get("research_eligibility")
        != report.claim_evaluation.research_eligibility.value
        or report.json_payload.get("research_intent")
        != report.claim_evaluation.research_intent.value
        or report.json_payload.get("mechanical_integrity")
        != report.claim_evaluation.mechanical_integrity.value
        or report.json_payload.get("claim_result") != report.claim_evaluation.to_builtins()
    ):
        _reject(FailureCode.CLAIM_INELIGIBLE, stage, "report claim projection is inconsistent")
    if report.markdown != markdown_path.read_text(encoding="utf-8"):
        _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, "report Markdown differs")
    relative = report_path.relative_to(repository).as_posix()
    commit = _report_addition_commit(repository, relative)
    if _git_blob(repository, commit, relative, stage=stage) != report_path.read_bytes():
        _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, stage, "report differs from publication commit")
    markdown_relative = markdown_path.relative_to(repository).as_posix()
    if _git_blob(repository, commit, markdown_relative, stage=stage) != markdown_path.read_bytes():
        _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, stage, "Markdown differs from publication commit")
    source_paths = {
        "history_anchors.jsonl": "research/history_anchors.jsonl",
        "holdout_lock.json": "research/holdout_lock.json",
        "protocol": f"research/protocols/{workflow.protocol.protocol_id}.json",
        "qualified_profile_registry": qualification_registry.relative_to(repository).as_posix(),
        "selected_deterministic_replay": f"research/replays/{trial_id}.json",
        "selected_diagnostics": f"research/diagnostics/{workflow.run_id}.json",
        "selected_performance": f"research/performance/{workflow.run_id}.json",
        "selected_run_manifest": (
            primary.relative_to(repository) / "evidence_manifest.json"
        ).as_posix(),
        "trials.jsonl": "research/trials.jsonl",
    }
    if set(report.source_evidence_hashes) != set(source_paths):
        _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, "report source inventory differs")
    for name, source in source_paths.items():
        observed = hashlib.sha256(_git_blob(repository, commit, source, stage=stage)).hexdigest()
        if observed != report.source_evidence_hashes[name]:
            _reject(FailureCode.EVIDENCE_INCOMPLETE, stage, f"historical source differs: {name}")
    return {
        "report_id": report.report_id,
        "report_commit": commit,
        "performance_diagnostics_id": performance.diagnostics_id,
        "daily_return_sample_count": performance.daily_return_sample_count,
        "final_holdout_used": False,
        "profitability_claim_authorized": False,
    }


def _validate_historical_status(repository: Path, path: Path) -> dict[str, Any]:
    stage = "historical-result-status"
    _require_committed(repository, path, stage=stage)
    registry = load_historical_result_registry(path)
    expected_paths = {
        material[role]
        for material in R2_EXPECTED_HISTORICAL_RESULTS.values()
        for role in ("primary_path", "replay_path")
    }
    if (
        registry.registry_schema != RESULT_STATUS_V2_SCHEMA
        or len(registry.records) != 12
        or {record.path for record in registry.records} != expected_paths
        or registry.final_holdout_authorized
        or registry.profitability_claim_authorized
    ):
        _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, stage, "R2 additive registry differs")
    candidate_count = 0
    benchmark_count = 0
    for record in registry.records:
        resolution = resolve_result_status(
            repository / record.path,
            repository_root=repository,
            registry_paths=(path,),
        )
        if resolution.is_active:
            _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, stage, "historical result remains active")
        if (
            record.historical_run_status is HistoricalRunStatus.REVOKED
            and record.financial_result_status is FinancialResultStatus.INVALIDATED
        ):
            candidate_count += 1
        elif (
            record.historical_run_status is HistoricalRunStatus.SUPERSEDED
            and record.financial_result_status is FinancialResultStatus.SUPERSEDED
        ):
            benchmark_count += 1
        else:
            _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, stage, "historical status vocabulary differs")
    if candidate_count != 8 or benchmark_count != 4:
        _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, stage, "Candidate/Benchmark status counts differ")
    return {
        "registry_identity": registry.registry_identity,
        "record_count": len(registry.records),
        "invalidated_candidate_copy_count": candidate_count,
        "superseded_benchmark_copy_count": benchmark_count,
    }


def _resolve_plan(plan: Path | None, *, epoch: str, plan_root: Path | None) -> Path:
    if plan is not None:
        if plan.is_symlink():
            _reject(FailureCode.EVIDENCE_INCOMPLETE, "plan", "plan must not be a symlink")
        path = plan.resolve(strict=True)
        if not path.is_file():
            _reject(FailureCode.EVIDENCE_INCOMPLETE, "plan", "plan must be a regular file")
        return path
    if plan_root is None:
        _reject(FailureCode.RESEARCH_PROTOCOL_INVALID, "plan", "--plan or --plan-root is required")
    if plan_root.is_symlink():
        _reject(FailureCode.EVIDENCE_INCOMPLETE, "plan", "plan root must not be a symlink")
    root = plan_root.resolve(strict=True)
    matches: list[Path] = []
    for candidate in root.rglob("execution-plan.json"):
        try:
            value = _strict_json(candidate.read_bytes(), stage="plan-discovery")
        except R2ValidationFailure:
            continue
        if value.get("schema") == EXPECTED_PLAN_SCHEMA and value.get("epoch") == epoch:
            matches.append(candidate)
    if len(matches) != 1:
        _reject(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            "plan",
            f"expected one plan for epoch {epoch}, found {len(matches)}",
        )
    return matches[0]


def validate(
    *,
    plan_path: Path | None,
    repository_root: Path = ROOT,
    epoch: str = EXPECTED_EPOCH,
    plan_root: Path | None = None,
    require_remote_tip: bool = True,
    result_status_path: Path | None = None,
) -> dict[str, Any]:
    repository = repository_root.resolve(strict=True)
    failures: list[R2ValidationFailure] = []
    runs: list[dict[str, Any]] = []
    plan_identity = "UNAVAILABLE"
    status_summary: dict[str, Any] = {}
    inventory_counts: dict[str, int] = {}
    source_commit = "UNAVAILABLE"
    try:
        resolved_plan = _resolve_plan(plan_path, epoch=epoch, plan_root=plan_root)
        plan_bytes = resolved_plan.read_bytes()
        plan = _strict_json(plan_bytes, stage="plan")
        if plan_bytes != canonical_json_bytes(plan) + b"\n":
            _reject(FailureCode.RESEARCH_PROTOCOL_INVALID, "plan", "plan JSON is not canonical")
        validate_plan_payload(plan, epoch=epoch)
        plan_identity = str(plan["plan_identity"])

        branch = _git_text(repository, "symbolic-ref", "--quiet", "--short", "HEAD")
        source_commit = _git_text(repository, "rev-parse", "HEAD")
        if branch != EXPECTED_BRANCH:
            _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, "git", "repair branch differs")
        if _git_text(repository, "status", "--porcelain=v1", "--untracked-files=all"):
            _reject(FailureCode.EVIDENCE_INCOMPLETE, "git", "validation requires a clean worktree")
        if require_remote_tip:
            remote_ref = str(plan["source"]["remote_ref"])
            if _git_text(repository, "rev-parse", remote_ref) != source_commit:
                _reject(FailureCode.TRIAL_HISTORY_INCOMPLETE, "git", "HEAD differs from remote tip")

        workflows, releases, registry_path = _validate_plan_files(repository, plan)
        inventory_counts = {
            profile.value: release.raw_inventory.raw_object_count
            for profile, release in releases.items()
            if isinstance(release.raw_inventory, DatasetRawInventory)
        }
        status_path = (
            repository / RESULT_STATUS_REF
            if result_status_path is None
            else (
                result_status_path
                if result_status_path.is_absolute()
                else repository / result_status_path
            )
        )
        status_summary = _validate_historical_status(repository, status_path.resolve(strict=True))

        journal = TrialJournal(repository / "research/trials.jsonl")
        records = journal.read_records()
        holdout = HoldoutLockStore(repository / "research/holdout_lock.json").read()
        if holdout.entries:
            _reject(FailureCode.HOLDOUT_HISTORY_VIOLATION, "research-history", "Holdout is not empty")
        by_trial = {
            workflow.trial_id: [record for record in records if record.trial_id == workflow.trial_id]
            for workflow in workflows
        }
        planned_trial_ids = set(by_trial)
        observed_epoch_trials = {
            record.trial_id
            for record in records
            if record.trial_id.startswith(f"{epoch}-")
        }
        if observed_epoch_trials != planned_trial_ids:
            _reject(
                FailureCode.TRIAL_HISTORY_INCOMPLETE,
                "research-history",
                "epoch journal contains a missing or undeclared trial",
            )
        all_directories: set[str] = set()
        previous_terminal_sequence = -1
        for workflow in workflows:
            lifecycle = by_trial[workflow.trial_id]
            if (
                [record.state for record in lifecycle]
                != [TrialState.PLANNED, TrialState.STARTED, TrialState.COMPLETED]
                or lifecycle[-1].partition_role is not PartitionRole.DEVELOPMENT
                or lifecycle[-1].result_exposed is not True
                or lifecycle[-1].failure_or_block_reason != "OFFICIAL_RUN_COMPLETED"
                or lifecycle[-1].journal_sequence <= previous_terminal_sequence
            ):
                _reject(
                    FailureCode.TRIAL_HISTORY_INCOMPLETE,
                    workflow.trial_id,
                    "journal lifecycle differs",
                )
            previous_terminal_sequence = lifecycle[-1].journal_sequence
            primary_ref = lifecycle[-1].result_ref
            primary = _safe_repository_path(
                repository,
                primary_ref,
                stage=workflow.trial_id,
                directory=True,
            )
            replay_path = _safe_repository_path(
                repository,
                f"research/replays/{workflow.trial_id}.json",
                stage=workflow.trial_id,
            )
            _require_committed(repository, replay_path, stage=workflow.trial_id)
            replay_payload = _read_json(replay_path, stage=workflow.trial_id)
            replay_ref, replay_identity = validate_replay_payload(
                replay_payload,
                trial_id=workflow.trial_id,
                primary_ref=primary_ref,
            )
            replay = _safe_repository_path(
                repository,
                replay_ref,
                stage=workflow.trial_id,
                directory=True,
            )
            references = {
                primary.relative_to(repository).as_posix(),
                replay.relative_to(repository).as_posix(),
            }
            if len(references) != 2 or references & all_directories:
                _reject(FailureCode.DETERMINISM_FAILURE, workflow.trial_id, "Run directory reused")
            all_directories.update(references)
            evidence = [
                _validate_one_evidence(
                    repository,
                    directory,
                    workflow=workflow,
                    role=role,
                    expected_release=releases[workflow.protocol.market_profile],
                )
                for role, directory in (("PRIMARY", primary), ("REPLAY", replay))
            ]
            if evidence[0]["semantic_digest"] != evidence[1]["semantic_digest"]:
                _reject(FailureCode.DETERMINISM_FAILURE, workflow.trial_id, "semantic digest differs")
            report = _validate_report_and_metrics(
                repository,
                workflow=workflow,
                primary=primary,
                qualification_registry=registry_path,
            )
            runs.append(
                {
                    "trial_id": workflow.trial_id,
                    "run_id": workflow.run_id,
                    "market_profile": workflow.protocol.market_profile.value,
                    "partition_role": PartitionRole.DEVELOPMENT.value,
                    "replay_identity": replay_identity,
                    "evidence": evidence,
                    **report,
                },
            )
        if len(runs) != 6 or len(all_directories) != 12:
            _reject(FailureCode.EVIDENCE_INCOMPLETE, "runs", "six primaries and replays required")
    except R2ValidationFailure as exc:
        failures.append(exc)
    except Exception as exc:
        failures.append(
            R2ValidationFailure(
                FailureCode.EVIDENCE_INCOMPLETE,
                "validator",
                f"{type(exc).__name__}: {exc}",
            ),
        )

    result = {
        "schema": "adversarial-remediation-002-research-validation-v1",
        "status": "PASS" if not failures else "FAIL",
        "source_commit": source_commit,
        "epoch": epoch,
        "plan_identity": plan_identity,
        "validated_primary_run_count": len(runs),
        "validated_evidence_directory_count": sum(len(item["evidence"]) for item in runs),
        "raw_inventory_counts": inventory_counts,
        "historical_result_status": status_summary,
        "final_holdout_used": False,
        "live_trading_used": False,
        "profitability_claim_authorized": False,
        "runs": sorted(runs, key=lambda item: item["trial_id"]),
        "failure_codes": list(dict.fromkeys(item.code.value for item in failures)),
        "failures": [item.to_builtins() for item in failures],
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
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--plan", type=Path)
    source.add_argument("--plan-root", type=Path)
    parser.add_argument("--epoch", default=EXPECTED_EPOCH)
    parser.add_argument("--repository", type=Path, default=ROOT)
    parser.add_argument("--result-status", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-unpublished-tip", action="store_true", help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    result = validate(
        plan_path=arguments.plan,
        repository_root=arguments.repository,
        epoch=arguments.epoch,
        plan_root=arguments.plan_root,
        require_remote_tip=not arguments.allow_unpublished_tip,
        result_status_path=arguments.result_status,
    )
    payload = canonical_json_bytes(result) + b"\n"
    if arguments.output is not None:
        output = arguments.output.resolve(strict=False)
        if output.exists() and output.read_bytes() != payload:
            raise FileExistsError(f"refusing to replace validation output: {output}")
        if not output.exists():
            _write_atomic(output, payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
