#!/usr/bin/env python3
"""Freeze the six R2 Development workflows and an explicit execution plan.

This script is deliberately preparation-only.  It never invokes the Owner
workflow, never writes below ``runs/`` or ``research/``, and never consumes a
Final Holdout.  Its output must live outside the repository (normally under
``/tmp``), so the first Owner checkpoint starts from a clean committed tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from crypto_lab.config import FeeAssumption
from crypto_lab.config import MarketProfile
from crypto_lab.config import MoneyAmount
from crypto_lab.config import NOT_APPLICABLE
from crypto_lab.data import DatasetRawInventory
from crypto_lab.data import DatasetRelease
from crypto_lab.data import FULL_RAW_INVENTORY_NORMALIZER_VERSION
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.git_identity import require_repository_root
from crypto_lab.m3 import ProfileQualificationState
from crypto_lab.m3 import QualifiedProfileRecord
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.owner import OwnerWorkflowInput
from crypto_lab.owner import OwnerWorkflowPurpose
from crypto_lab.owner import _qualified_profile_registry_candidates
from crypto_lab.paths import validate_safe_component
from crypto_lab.reporting import REQUIRED_SCIENTIFIC_LIMITATIONS
from crypto_lab.research import BenchmarkSpec
from crypto_lab.research import CandidateSpec
from crypto_lab.research import ClaimScope
from crypto_lab.research import InstrumentScope
from crypto_lab.research import MonteCarloSpec
from crypto_lab.research import PartitionRole
from crypto_lab.research import PurgeEmbargoRule
from crypto_lab.research import ResearchIntent
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import ResamplingMethod
from crypto_lab.research import SampleAdequacyRule
from crypto_lab.research import TERMINAL_TRIAL_STATES
from crypto_lab.research import TrialJournal
from crypto_lab.research import UtcInterval
from crypto_lab.research import benchmark_trial_candidate_id
from crypto_lab.strategies import BUY_AND_HOLD_REGISTRATION_ID
from crypto_lab.strategies import TSMOM_FULL_REGISTRATION_ID
from crypto_lab.strategies import TSMOM_VOL20_REGISTRATION_ID
from crypto_lab.strategies import locked_buy_and_hold_strategy_spec
from crypto_lab.strategies import locked_weekly_tsmom_strategy_spec


EXPECTED_BRANCH = "fix/adversarial-audit-remediation-002"
EPOCH_FRAGMENT = "adversarial-remediation-002"
DEFAULT_EPOCH = "adversarial-remediation-002"
STRATEGY_FAMILY = "BTCUSDT_WEEKLY_TSMOM28_V1"
RESEARCH_FAMILY = "BTCUSDT_WEEKLY_TSMOM28_V1_ADVERSARIAL_REMEDIATION_002"
WARMUP_START = datetime(2021, 1, 1, tzinfo=UTC)
DEVELOPMENT = UtcInterval(
    start_inclusive=datetime(2021, 2, 1, tzinfo=UTC),
    end_exclusive=datetime(2021, 8, 1, tzinfo=UTC),
)
FEE = FeeAssumption(
    maker_fee=Decimal("0.001"),
    taker_fee=Decimal("0.001"),
    explicit_zero_fee=False,
    reason="SSOT Appendix A qualification-only observable estimated fee",
    claim_class="ESTIMATED_FEE",
)
PROFILE_ORDER = (
    MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
    MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
)
DEVELOPMENT_CLAIM_BASIS = "; ".join(
    (
        "EXPLORATORY_OPERATIONAL_VALIDATION",
        "EXPOSED_DEVELOPMENT_DATA",
        "DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT",
        *REQUIRED_SCIENTIFIC_LIMITATIONS,
        "FINAL_HOLDOUT_USED_FALSE",
        "REAL_PROFITABILITY_CLAIM_FALSE",
        "LIVE_TRADING_AUTHORIZATION_FALSE",
    ),
)


def _git(repository: Path, *arguments: str, check: bool = True) -> str:
    process = subprocess.run(
        ["git", "--no-replace-objects", *arguments],
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
        text=True,
    )
    if check and process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "git command failed"
        raise RuntimeError(detail)
    return process.stdout.strip()


def _future(day: int) -> UtcInterval:
    return UtcInterval(
        start_inclusive=datetime(2099, 1, day, tzinfo=UTC),
        end_exclusive=datetime(2099, 1, day + 1, tzinfo=UTC),
    )


def _contained_committed_file(repository: Path, path: Path, *, label: str) -> Path:
    candidate = path if path.is_absolute() else repository / path
    lexical = Path(os.path.abspath(candidate))
    cursor = Path(lexical.anchor)
    for component in lexical.parts[1:]:
        cursor /= component
        if cursor.is_symlink():
            raise ValueError(f"{label} path must not contain a symlink")
    resolved = candidate.resolve(strict=True)
    try:
        relative = resolved.relative_to(repository).as_posix()
    except ValueError as exc:
        raise ValueError(f"{label} must be inside the repository") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    committed = subprocess.run(
        ["git", "--no-replace-objects", "show", f"HEAD:{relative}"],
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
    if committed.returncode != 0 or committed.stdout != resolved.read_bytes():
        raise RuntimeError(f"{label} is not byte-identical to Git HEAD")
    return resolved


def _require_clean_published_source(
    repository: Path,
    branch: str,
    remote_ref: str,
) -> dict[str, str]:
    actual_branch = _git(repository, "symbolic-ref", "--quiet", "--short", "HEAD")
    if actual_branch != branch:
        raise RuntimeError(f"branch mismatch: expected={branch} actual={actual_branch}")
    if _git(repository, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workflow preparation requires a clean committed worktree")
    head = _git(repository, "rev-parse", "HEAD")
    remote = _git(repository, "rev-parse", remote_ref)
    if head != remote:
        raise RuntimeError(f"workflow preparation requires HEAD == {remote_ref}")
    return {
        "branch": actual_branch,
        "head": head,
        "source_tree": _git(repository, "rev-parse", "HEAD^{tree}"),
        "remote_ref": remote_ref,
        "remote_tip": remote,
    }


def _require_no_active_trial(repository: Path) -> None:
    records = TrialJournal(repository / "research/trials.jsonl").read_records()
    latest = {record.trial_id: record for record in records}
    active = sorted(
        trial_id
        for trial_id, record in latest.items()
        if record.state not in TERMINAL_TRIAL_STATES
    )
    if active:
        raise RuntimeError("active or incomplete Official trial exists: " + ",".join(active))


def _require_external_fresh_output(repository: Path, path: Path) -> Path:
    lexical = Path(os.path.abspath(path))
    try:
        lexical.relative_to(repository)
    except ValueError:
        pass
    else:
        raise ValueError("workflow preparation output must remain outside the repository")
    if lexical.exists() or lexical.is_symlink():
        raise FileExistsError(f"refusing to overwrite workflow preparation: {lexical}")
    parent = lexical.parent.resolve(strict=True)
    if parent != lexical.parent:
        raise ValueError("workflow preparation path must not contain a symlink")
    if Path("/tmp") not in (parent, *parent.parents):
        raise ValueError("workflow preparation output must be under /tmp")
    return lexical


def _require_full_release(
    release: DatasetRelease,
    *,
    profile: MarketProfile,
) -> DatasetRawInventory:
    if (
        release.schema_version != 2
        or not release.has_full_raw_inventory
        or release.normalizer_version != FULL_RAW_INVENTORY_NORMALIZER_VERSION
        or release.market_profile is not profile
        or release.completeness_result.status != "PASS"
        or release.completeness_result.no_repairs is not True
        or release.normalized_time_range.start_inclusive != WARMUP_START
        or release.normalized_time_range.end_exclusive != DEVELOPMENT.end_exclusive
    ):
        raise RuntimeError(f"{profile.value} requires a complete v2 full-inventory release")
    inventory = release.raw_inventory
    if not isinstance(inventory, DatasetRawInventory) or inventory.raw_object_count <= 0:
        raise RuntimeError(f"{profile.value} full Raw inventory is empty")
    return inventory


def _require_current_registry(
    registry: QualifiedProfileRegistry,
    *,
    runtime_lock_sha256: str,
) -> dict[MarketProfile, QualifiedProfileRecord]:
    if registry.schema_version != 2:
        raise RuntimeError("R2 qualification registry must use schema version 2")
    records = {record.profile_id: record for record in registry.records}
    if set(records) != set(PROFILE_ORDER):
        raise RuntimeError("R2 qualification must contain exactly both V1 profiles")
    for profile in PROFILE_ORDER:
        record = records[profile]
        if (
            record.schema_version != 2
            or record.qualification_state is not ProfileQualificationState.QUALIFIED
            or record.checker_result != "COMPONENT_CHECK_PASS"
            or record.replay_result != "PASS"
            or record.runtime_lock_sha256 != runtime_lock_sha256
            or len(record.accepted_run_ids) != 2
            or len(record.evidence_references) != 2
        ):
            raise RuntimeError(f"{profile.value} is not current component-qualified evidence")
    return records


def _require_current_registry_locator(repository: Path, registry_path: Path) -> None:
    """Reject an otherwise valid older Registry at plan-preparation time."""

    candidates = _qualified_profile_registry_candidates(repository)
    if not candidates or registry_path != candidates[0]:
        raise RuntimeError(
            "qualification registry is not the current Git-committed authority",
        )


def _require_qualification_evidence(
    repository: Path,
    registry_path: Path,
    records: dict[MarketProfile, QualifiedProfileRecord],
) -> dict[str, Any]:
    root = registry_path.parent
    manifest_path = _contained_committed_file(
        repository,
        root / "qualification-manifest.json",
        label="qualification manifest",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if (
        manifest.get("schema") != "m3-acceptance-manifest-v1"
        or manifest.get("manifest_self_excluded") is not True
        or not isinstance(entries, list)
        or manifest.get("content_sha256") != canonical_sha256(entries)
    ):
        raise RuntimeError("qualification manifest is not an exact M3 acceptance inventory")
    declared: dict[str, tuple[int, str]] = {}
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "byte_size", "sha256"}:
            raise RuntimeError("qualification manifest entry is malformed")
        relative = str(item["path"])
        candidate = Path(relative)
        if (
            not relative
            or candidate.is_absolute()
            or ".." in candidate.parts
            or candidate.as_posix() != relative
            or relative in declared
        ):
            raise RuntimeError("qualification manifest path is unsafe or duplicated")
        declared[relative] = (int(item["byte_size"]), str(item["sha256"]))
    required_files: set[str] = {"qualified-profile-registry.json"}
    registry_relative = registry_path.relative_to(root).as_posix()
    if registry_relative not in declared:
        raise RuntimeError("qualification registry is outside its acceptance manifest")
    registry_size, registry_digest = declared[registry_relative]
    if (
        registry_path.stat().st_size != registry_size
        or sha256_file(registry_path) != registry_digest
    ):
        raise RuntimeError("qualification registry manifest binding differs")
    for record in records.values():
        for reference in record.evidence_references:
            run_reference = Path(reference)
            if (
                run_reference.is_absolute()
                or ".." in run_reference.parts
                or run_reference.as_posix() != reference
            ):
                raise RuntimeError("qualification evidence reference is unsafe")
            for name in (
                "lab_run_config.json",
                "dataset_release.json",
                "status.json",
                "component_validation.json",
                "evidence_manifest.json",
            ):
                relative = (run_reference / name).as_posix()
                required_files.add(relative)
                if relative not in declared:
                    raise RuntimeError(f"qualification evidence is outside its manifest: {relative}")
                path = _contained_committed_file(
                    repository,
                    root / relative,
                    label="qualification Run evidence",
                )
                size, digest = declared[relative]
                if path.stat().st_size != size or sha256_file(path) != digest:
                    raise RuntimeError(f"qualification manifest binding differs: {relative}")
            status = json.loads((root / run_reference / "status.json").read_text(encoding="utf-8"))
            component = json.loads(
                (root / run_reference / "component_validation.json").read_text(encoding="utf-8"),
            )
            if (
                status.get("state") != "COMPLETED"
                or status.get("component_validation_outcome") != "COMPONENT_CHECK_PASS"
                or component.get("outcome") != "COMPONENT_CHECK_PASS"
                or component.get("failure_codes") != []
            ):
                raise RuntimeError("qualification accepted Run is not component-valid")
    return {
        "manifest_sha256": sha256_file(manifest_path),
        "manifest_content_sha256": manifest["content_sha256"],
        "manifest_entry_count": len(entries),
        "required_consumed_file_count": len(required_files),
    }


def _require_rebuild_validation(
    value: dict[str, Any],
    *,
    releases: dict[MarketProfile, DatasetRelease],
    inventories: dict[MarketProfile, DatasetRawInventory],
) -> None:
    if (
        value.get("schema")
        != "free-official-binance-deterministic-rebuild-validation-v2-full-raw-inventory"
        or value.get("status") != "PASS"
        or value.get("strategy_run") is not False
        or value.get("official_trial") is not False
        or value.get("network_used") is not False
    ):
        raise RuntimeError("Dataset rebuild validation is not an accepted offline v2 proof")
    materialized = value.get("materialized_release_artifacts")
    catalog_validation = value.get("nautilus_catalog_validation")
    comparison = value.get("comparison")
    if not all(isinstance(item, dict) for item in (materialized, catalog_validation, comparison)):
        raise RuntimeError("Dataset rebuild validation fields are incomplete")
    expected_ids = sorted(release.dataset_release_id for release in releases.values())
    if sorted(comparison.get("dataset_release_ids", [])) != expected_ids:
        raise RuntimeError("Dataset rebuild comparison release identities differ")
    for profile in PROFILE_ORDER:
        key = profile.value
        observed = materialized.get(key)
        catalog = catalog_validation.get(key)
        if (
            not isinstance(observed, dict)
            or not isinstance(catalog, dict)
            or observed.get("dataset_release_id") != releases[profile].dataset_release_id
            or observed.get("catalog_identity") != releases[profile].catalog_identity
            or observed.get("raw_inventory_identity")
            != inventories[profile].raw_inventory_identity
            or observed.get("raw_inventory_object_count")
            != inventories[profile].raw_object_count
            or catalog.get("status") != "PASS"
            or catalog.get("catalog_identity") != releases[profile].catalog_identity
        ):
            raise RuntimeError(f"Dataset rebuild proof differs for {profile.value}")


def _require_new_workflow_identities(
    repository: Path,
    workflows: list[OwnerWorkflowInput],
) -> None:
    existing_trials = {
        record.trial_id
        for record in TrialJournal(repository / "research/trials.jsonl").read_records()
    }
    collisions: list[str] = []
    for workflow in workflows:
        if workflow.trial_id in existing_trials:
            collisions.append(f"journal:{workflow.trial_id}")
        exact_paths = (
            repository / "research/workflows" / f"{workflow.trial_id}.json",
            repository / "research/replays" / f"{workflow.trial_id}.json",
            repository / "research/reports" / f"{workflow.trial_id}.json",
            repository / "research/reports" / f"{workflow.trial_id}.md",
            repository / "research/performance" / f"{workflow.run_id}.json",
            repository / "research/diagnostics" / f"{workflow.run_id}.json",
        )
        collisions.extend(
            str(path.relative_to(repository)) for path in exact_paths if path.exists()
        )
        collisions.extend(
            str(path.relative_to(repository))
            for path in (repository / "runs").glob(f"{workflow.run_id}-*")
        )
        replay_root = repository / "runs/replays" / workflow.trial_id
        if replay_root.exists():
            collisions.append(str(replay_root.relative_to(repository)))
        if workflow.workflow_purpose is OwnerWorkflowPurpose.BENCHMARK_STUDY:
            benchmark = (
                repository
                / "research/benchmarks"
                / f"{workflow.protocol.required_benchmark.benchmark_id}.json"
            )
            if benchmark.exists():
                collisions.append(str(benchmark.relative_to(repository)))
    if collisions:
        raise RuntimeError("R2 workflow identity collision: " + ",".join(sorted(collisions)))


def _benchmark_id(*, epoch: str, suffix: str) -> str:
    """Return an additive benchmark evidence namespace for one frozen plan.

    ``research/benchmarks/<benchmark_id>.json`` is result-bearing evidence,
    not a replaceable current pointer.  A full requalification epoch must
    therefore receive a distinct, deterministic benchmark identity so the
    Owner never overwrites an earlier benchmark result.
    """

    validated_epoch = validate_safe_component(epoch, field="epoch")
    if suffix not in {"spot", "perpetual"}:
        raise ValueError("benchmark profile suffix is invalid")
    revision = validated_epoch.upper().replace("-", "_")
    return f"BUY_AND_HOLD_1X_R2_{suffix.upper()}_{revision}"


def _build_protocol_and_workflows(
    *,
    profile: MarketProfile,
    release: DatasetRelease,
    qualified_profile_record_id: str,
    frozen_at_utc: datetime,
    epoch: str,
    research_family_id: str,
) -> tuple[ResearchProtocol, tuple[OwnerWorkflowInput, ...]]:
    suffix = "spot" if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else "perpetual"
    benchmark_id = _benchmark_id(epoch=epoch, suffix=suffix)
    specs = (
        locked_weekly_tsmom_strategy_spec(TSMOM_FULL_REGISTRATION_ID, profile),
        locked_weekly_tsmom_strategy_spec(TSMOM_VOL20_REGISTRATION_ID, profile),
    )
    candidates = (
        CandidateSpec.create(
            candidate_label="TSMOM28_FULL_NOTIONAL",
            strategy_spec_id=specs[0].strategy_spec_id,
            parameter_values=dict(specs[0].parameters),
        ),
        CandidateSpec.create(
            candidate_label="TSMOM28_VOLATILITY_TARGET_20",
            strategy_spec_id=specs[1].strategy_spec_id,
            parameter_values=dict(specs[1].parameters),
        ),
    )
    benchmark_spec = locked_buy_and_hold_strategy_spec(profile, benchmark_id)
    parameter_names = sorted(set(specs[0].parameters) | set(specs[1].parameters))
    protocol = ResearchProtocol.create(
        frozen_at_utc=frozen_at_utc,
        research_family_id=research_family_id,
        hypothesis_id=(
            "r2-causal-weekly-tsmom-with-daily-marked-portfolio-metrics-"
            f"full-versus-vol20-{suffix}"
        ),
        research_intent=ResearchIntent.EXPLORATORY,
        market_profile=profile,
        instrument_scope=InstrumentScope.SINGLE_INSTRUMENT,
        instrument_ids=(release.instrument_id,),
        instrument_selection_basis=(
            "Owner-locked BTCUSDT; official Binance v2 full-Raw-inventory DatasetRelease; "
            "EXPOSED_DEVELOPMENT_DATA; DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT"
        ),
        universe_selection_rule=NOT_APPLICABLE,
        universe_as_of_rule=NOT_APPLICABLE,
        universe_membership_sha256=NOT_APPLICABLE,
        dataset_release_ids=(release.dataset_release_id,),
        strategy_family=STRATEGY_FAMILY,
        ordered_candidates=candidates,
        parameter_domain={
            name: tuple(
                dict.fromkeys(
                    spec.parameters[name]
                    for spec in specs
                    if name in spec.parameters
                ),
            )
            for name in parameter_names
        },
        search_budget=2,
        candidate_ordering="AS_LISTED",
        deterministic_generator="EXACT_TWO_OWNER_LOCKED_CANDIDATES_NO_POST_RESULT_ADDITION",
        random_seeds=(0,),
        primary_metric="DAILY_MARKED_PORTFOLIO_TOTAL_RETURN_EXPLORATORY_DIAGNOSTIC_ONLY",
        required_benchmark=BenchmarkSpec(
            benchmark_id=benchmark_id,
            definition=(
                f"registration_id={BUY_AND_HOLD_REGISTRATION_ID};"
                f"strategy_spec_id={benchmark_spec.strategy_spec_id};"
                f"qualification_epoch={epoch};"
                "enter LONG 1x from the first fully scoring-eligible signal interval after "
                "60s latency; hold through scoring_end_exclusive without synthetic close"
            ),
            scored_interval=DEVELOPMENT,
            cost_basis="SAME_INITIAL_EQUITY_DATASET_PROFILE_0.001_FEE_AND_TERMINAL_POLICY",
            frozen_before_result_exposure=True,
        ),
        selection_rule="NO_PUBLISHABLE_WINNER_SELECTION_EXPLORATORY_RESULTS_ONLY",
        tie_break_rule="NOT_APPLICABLE_NO_WINNER_SELECTION",
        development_interval=DEVELOPMENT,
        validation_interval=_future(1),
        oos_interval=_future(2),
        final_holdout_interval=_future(3),
        purge_embargo_rule=PurgeEmbargoRule(
            mode=NOT_APPLICABLE,
            reason="Causal completed-price signal has no forward label or training target",
            purge_seconds=0,
            embargo_seconds=0,
            max_forward_dependency_seconds=0,
        ),
        time_series_split="CHRONOLOGICAL",
        multiple_testing_treatment="HOLM_BONFERRONI",
        sample_adequacy_rule=SampleAdequacyRule(
            counted_observation=NOT_APPLICABLE,
            minimum_completed_trades=NOT_APPLICABLE,
            rationale=(
                "Development-only exploratory study; official daily marked portfolio samples "
                "are disclosed and invalid small-sample metrics remain undefined"
            ),
        ),
        monte_carlo_spec=MonteCarloSpec(
            resampling_method=ResamplingMethod.NOT_APPLICABLE,
            simulation_count=0,
            random_seed=0,
            block_length=NOT_APPLICABLE,
            quantile_method="R7_LINEAR_INTERPOLATION",
            decimal_places=8,
            not_applicable_reason=(
                "Exploratory exposed-development study makes no eligible confirmatory claim"
            ),
        ),
        intended_claim_scope=ClaimScope.INSTRUMENT_ONLY,
        claim_basis=DEVELOPMENT_CLAIM_BASIS,
        kill_criteria=(
            "DATASET_OR_CATALOG_IDENTITY_MISMATCH",
            "COMPONENT_VALIDATION_NOT_PASS",
            "OFFICIAL_SEAL_NOT_PASS",
            "DETERMINISTIC_REPLAY_NOT_PASS",
            "FINANCIAL_RECONCILIATION_NOT_PASS",
            "OFFLINE_BOUNDARY_NOT_PASS",
            "WARMUP_SCORING_ELIGIBILITY_VIOLATION",
        ),
        terminal_policy="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
    )
    common: dict[str, Any] = {
        "schema_version": 1,
        "protocol": protocol,
        "dataset_release_id": release.dataset_release_id,
        "qualified_profile_record_id": qualified_profile_record_id,
        "partition_role": PartitionRole.DEVELOPMENT,
        "warmup_start": WARMUP_START,
        "scoring_start": DEVELOPMENT.start_inclusive,
        "scoring_end_exclusive": DEVELOPMENT.end_exclusive,
        "initial_capital": MoneyAmount(amount=Decimal("10000"), currency="USDT"),
        "fee_assumption": FEE,
        "seed": 0,
    }
    benchmark = OwnerWorkflowInput(
        **common,
        workflow_purpose=OwnerWorkflowPurpose.BENCHMARK_STUDY,
        trial_id=f"{epoch}-{suffix}-benchmark-buy-and-hold-1x-development",
        candidate_id=benchmark_trial_candidate_id(
            protocol.required_benchmark,
            strategy_spec_id=benchmark_spec.strategy_spec_id,
        ),
        run_id=f"{epoch}-{suffix}-benchmark-run",
        registered_strategy_id=BUY_AND_HOLD_REGISTRATION_ID,
        strategy_spec=benchmark_spec,
    )
    candidate_workflows = tuple(
        OwnerWorkflowInput(
            **common,
            workflow_purpose=OwnerWorkflowPurpose.OWNER_STUDY,
            trial_id=f"{epoch}-{suffix}-candidate-{label}-development",
            candidate_id=candidate.candidate_id,
            run_id=f"{epoch}-{suffix}-candidate-{label}-run",
            registered_strategy_id=registration,
            strategy_spec=spec,
        )
        for label, registration, spec, candidate in (
            ("a", TSMOM_FULL_REGISTRATION_ID, specs[0], candidates[0]),
            ("b", TSMOM_VOL20_REGISTRATION_ID, specs[1], candidates[1]),
        )
    )
    return protocol, (benchmark, *candidate_workflows)


def _execution_item(
    repository: Path,
    workflow: OwnerWorkflowInput,
    *,
    input_path: Path,
    result_path: Path,
    sequence: int,
) -> dict[str, Any]:
    command = [
        "/usr/bin/env",
        "-i",
        "PATH=/usr/bin:/bin",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        "TZ=UTC",
        str(repository / ".venv/bin/python"),
        "-I",
        "-P",
        "-S",
        "-B",
        "-X",
        "pycache_prefix=/dev/null",
        str(repository / "scripts/isolated_runtime_bootstrap.py"),
        "--authority",
        str(repository / "runtime-bootstrap-authority.json"),
        "--repository",
        str(repository),
        "--entrypoint",
        "crypto_lab.owner:main",
        "--",
        "--input",
        str(input_path),
        "--repository",
        str(repository),
        "--output",
        str(result_path),
    ]
    return {
        "sequence": sequence,
        "profile": workflow.protocol.market_profile.value,
        "purpose": workflow.workflow_purpose.value,
        "partition_role": workflow.partition_role.value,
        "trial_id": workflow.trial_id,
        "run_id": workflow.run_id,
        "workflow_input": str(input_path),
        "workflow_input_sha256": hashlib.sha256(
            workflow.to_json_bytes() + b"\n",
        ).hexdigest(),
        "result_summary": str(result_path),
        "command_argv": command,
        "owner_fresh_child_count": 2,
        "expected_copies": ["PRIMARY", "REPLAY"],
        "component_validation_required": "COMPONENT_CHECK_PASS",
        "official_seal_required": "OFFICIAL_SEAL_PASS",
        "deterministic_replay_required": "PASS",
        "final_holdout_used": False,
        "profitability_claim_authorized": False,
    }


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("frozen-at-utc must be explicit UTC")
    if parsed > datetime.now(UTC):
        raise ValueError("frozen-at-utc must not be in the future")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--frozen-at-utc", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--qualification-registry", type=Path, required=True)
    parser.add_argument("--data-rebuild-validation", type=Path, required=True)
    parser.add_argument("--spot-release", type=Path, required=True)
    parser.add_argument("--perpetual-release", type=Path, required=True)
    parser.add_argument("--epoch", default=DEFAULT_EPOCH)
    parser.add_argument("--research-family-id", default=RESEARCH_FAMILY)
    parser.add_argument("--required-branch", default=EXPECTED_BRANCH)
    parser.add_argument("--required-remote-ref")
    arguments = parser.parse_args(argv)
    repository = require_repository_root(arguments.repository)

    epoch = validate_safe_component(arguments.epoch, field="epoch")
    if EPOCH_FRAGMENT not in epoch:
        raise ValueError(f"epoch must contain {EPOCH_FRAGMENT!r}")
    research_family_id = validate_safe_component(
        arguments.research_family_id,
        field="research_family_id",
    )
    frozen = _parse_utc(arguments.frozen_at_utc)
    output = _require_external_fresh_output(repository, arguments.output_dir)
    remote_ref = arguments.required_remote_ref or f"origin/{arguments.required_branch}"
    source = _require_clean_published_source(
        repository,
        arguments.required_branch,
        remote_ref,
    )
    _require_no_active_trial(repository)

    registry_path = _contained_committed_file(
        repository,
        arguments.qualification_registry,
        label="qualification registry",
    )
    _require_current_registry_locator(repository, registry_path)
    rebuild_validation_path = _contained_committed_file(
        repository,
        arguments.data_rebuild_validation,
        label="Dataset rebuild validation",
    )
    spot_path = _contained_committed_file(
        repository,
        arguments.spot_release,
        label="Spot DatasetRelease",
    )
    perpetual_path = _contained_committed_file(
        repository,
        arguments.perpetual_release,
        label="Perpetual DatasetRelease",
    )
    runtime_bootstrap_authority_path = _contained_committed_file(
        repository,
        repository / "runtime-bootstrap-authority.json",
        label="Runtime bootstrap authority",
    )
    runtime_lock_sha256 = sha256_file(repository / "runtime.lock.json")
    registry = QualifiedProfileRegistry.from_json_bytes(registry_path.read_bytes())
    profiles = _require_current_registry(
        registry,
        runtime_lock_sha256=runtime_lock_sha256,
    )
    qualification_evidence = _require_qualification_evidence(
        repository,
        registry_path,
        profiles,
    )
    releases = {
        MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY: DatasetRelease.from_json_bytes(
            spot_path.read_bytes(),
        ),
        MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING: (
            DatasetRelease.from_json_bytes(perpetual_path.read_bytes())
        ),
    }
    inventories = {
        profile: _require_full_release(releases[profile], profile=profile)
        for profile in PROFILE_ORDER
    }
    rebuild_validation = json.loads(rebuild_validation_path.read_text(encoding="utf-8"))
    if not isinstance(rebuild_validation, dict):
        raise RuntimeError("Dataset rebuild validation must be a JSON object")
    _require_rebuild_validation(
        rebuild_validation,
        releases=releases,
        inventories=inventories,
    )
    protocols: list[ResearchProtocol] = []
    workflows: list[OwnerWorkflowInput] = []
    for profile in PROFILE_ORDER:
        protocol, profile_workflows = _build_protocol_and_workflows(
            profile=profile,
            release=releases[profile],
            qualified_profile_record_id=profiles[profile].qualified_profile_record_id,
            frozen_at_utc=frozen,
            epoch=epoch,
            research_family_id=research_family_id,
        )
        protocols.append(protocol)
        workflows.extend(profile_workflows)
    if len(workflows) != 6 or any(
        workflow.partition_role is not PartitionRole.DEVELOPMENT
        for workflow in workflows
    ):
        raise RuntimeError("R2 preparation must contain exactly six Development workflows")
    if len({workflow.trial_id for workflow in workflows}) != 6 or len(
        {workflow.run_id for workflow in workflows},
    ) != 6:
        raise RuntimeError("R2 workflow trial/run identities must be unique")
    if any(
        EPOCH_FRAGMENT not in workflow.trial_id or EPOCH_FRAGMENT not in workflow.run_id
        for workflow in workflows
    ):
        raise RuntimeError("R2 workflow identity lacks the remediation epoch")
    _require_new_workflow_identities(repository, workflows)

    output.mkdir(mode=0o700)
    input_directory = output / "workflow-inputs"
    result_directory = output / "owner-results"
    input_directory.mkdir()
    result_directory.mkdir()

    execution: list[dict[str, Any]] = []
    for sequence, workflow in enumerate(workflows, start=1):
        path = input_directory / f"{workflow.trial_id}.json"
        path.write_bytes(workflow.to_json_bytes() + b"\n")
        execution.append(
            _execution_item(
                repository,
                workflow,
                input_path=path,
                result_path=result_directory / f"{workflow.trial_id}.json",
                sequence=sequence,
            ),
        )

    release_paths = {
        MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY: spot_path,
        MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING: perpetual_path,
    }
    manifest = {
        "schema": "adversarial-remediation-002-official-run-plan-v1",
        "epoch": epoch,
        "frozen_at_utc": frozen.isoformat().replace("+00:00", "Z"),
        "source": source,
        "runtime_lock_sha256": runtime_lock_sha256,
        "runtime_bootstrap_authority_sha256": sha256_file(
            runtime_bootstrap_authority_path,
        ),
        "qualification_registry": {
            "path": registry_path.relative_to(repository).as_posix(),
            "sha256": sha256_file(registry_path),
            "schema_version": registry.schema_version,
            "registry_content_sha256": registry.registry_content_sha256,
            **qualification_evidence,
        },
        "data_rebuild_validation": {
            "path": rebuild_validation_path.relative_to(repository).as_posix(),
            "sha256": sha256_file(rebuild_validation_path),
            "schema": rebuild_validation["schema"],
            "status": rebuild_validation["status"],
        },
        "dataset_releases": {
            profile.value: {
                "path": release_paths[profile].relative_to(repository).as_posix(),
                "sha256": sha256_file(release_paths[profile]),
                "dataset_release_id": releases[profile].dataset_release_id,
                "catalog_identity": releases[profile].catalog_identity,
                "raw_inventory_identity": inventories[profile].raw_inventory_identity,
                "raw_object_count": inventories[profile].raw_object_count,
                "schema_version": releases[profile].schema_version,
            }
            for profile in PROFILE_ORDER
        },
        "protocol_ids": [protocol.protocol_id for protocol in protocols],
        "research_family_id": research_family_id,
        "execution": execution,
        "workflow_count": 6,
        "primary_run_count": 6,
        "replay_run_count": 6,
        "fresh_process_run_count": 12,
        "execution_order": "SPOT_BENCHMARK_CANDIDATE_A_CANDIDATE_B_THEN_PERPETUAL_SAME_ORDER",
        "owner_checkpoint_contract": {
            "current_commits_per_workflow": 5,
            "current_total_lifecycle_commits": 30,
            "phases": [
                "FREEZE_INTENT",
                "START_HISTORY",
                "TERMINAL_PRIMARY_REPLAY_AND_HISTORY",
                "DERIVE_DIAGNOSTICS",
                "PUBLISH_REPORT",
            ],
            "normal_push_required_after_each": True,
            "squash_rebase_force_push_forbidden": True,
        },
        "preparation_only": True,
        "owner_executed": False,
        "final_holdout_used": False,
        "live_trading_used": False,
        "profitability_claim_authorized": False,
    }
    manifest["plan_identity"] = canonical_sha256(manifest)
    (output / "execution-plan.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
