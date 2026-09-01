"""Public, strict, checkpointed workflow for one V1 Owner trial.

The workflow accepts identities and frozen contracts, never caller assertions
about profitability or evidence validity.  Every history mutation is fsynced,
anchored, committed, and normally pushed before the next material phase.  The
Official engine executes in a child process so its irreversible seccomp filter
does not change the orchestration process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import fields
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import FeeAssumption
from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.config import MoneyAmount
from crypto_lab.config import NamedSeed
from crypto_lab.config import RunPurpose
from crypto_lab.config import SourceRevision
from crypto_lab.config import StrictModel
from crypto_lab.data import DatasetRelease
from crypto_lab.data import FULL_RAW_INVENTORY_NORMALIZER_VERSION
from crypto_lab.data import M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION
from crypto_lab.data import RESEARCH_REBUILD_VALIDATION_REF
from crypto_lab.data import validate_research_dataset_rebuild_proof
from crypto_lab.diagnostics import DiagnosticResolution
from crypto_lab.diagnostics import derive_benchmark_evidence
from crypto_lab.diagnostics import derive_diagnostic_resolution
from crypto_lab.diagnostics import derive_performance_diagnostics
from crypto_lab.diagnostics import write_diagnostic_resolution
from crypto_lab.diagnostics import write_benchmark_evidence
from crypto_lab.status import FailureCode
from crypto_lab.diagnostics import write_performance_diagnostics
from crypto_lab.exposure import AuthoritativeExposureResolver
from crypto_lab.git_identity import capture_actual_source_revision
from crypto_lab.git_identity import worktree_is_clean
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.history import AuthoritativeResearchHistory
from crypto_lab.history import HistoryAnchorStore
from crypto_lab.m3 import QualifiedProfileRecord
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.official import OfficialEvidenceLocator
from crypto_lab.official import OfficialEvidenceResolver
from crypto_lab.paths import validate_safe_component
from crypto_lab.reporting import ReportOutput
from crypto_lab.reporting import REQUIRED_SCIENTIFIC_LIMITATIONS
from crypto_lab.research import PartitionRole
from crypto_lab.research import BenchmarkSpec
from crypto_lab.research import CandidateSpec
from crypto_lab.research import ClaimScope
from crypto_lab.research import InstrumentScope
from crypto_lab.research import MonteCarloSpec
from crypto_lab.research import PurgeEmbargoRule
from crypto_lab.research import ResearchIntent
from crypto_lab.research import ResearchError
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import ResamplingMethod
from crypto_lab.research import ResultExposure
from crypto_lab.research import SampleAdequacyRule
from crypto_lab.research import TERMINAL_TRIAL_STATES
from crypto_lab.research import TrialDefinition
from crypto_lab.research import TrialRecord
from crypto_lab.research import TrialState
from crypto_lab.research import UtcInterval
from crypto_lab.research import benchmark_trial_candidate_id
from crypto_lab.runner import OfficialLabRunRequest
from crypto_lab.runner import run_official_lab
from crypto_lab.sealing import OfficialSealOutcome
from crypto_lab.sealing import verify_official_seal
from crypto_lab.strategies import StrategySpec
from crypto_lab.strategies import resolve_registered_strategy_identity
from crypto_lab.status import RunState


class OwnerWorkflowPurpose(StrEnum):
    OWNER_STUDY = "OWNER_STUDY"
    BENCHMARK_STUDY = "BENCHMARK_STUDY"
    QUALIFICATION_INTERFACE_FIXTURE = "QUALIFICATION_INTERFACE_FIXTURE"


DEVELOPMENT_CLAIM_CONTROL_TOKENS = (
    *REQUIRED_SCIENTIFIC_LIMITATIONS,
    "FINAL_HOLDOUT_USED_FALSE",
    "REAL_PROFITABILITY_CLAIM_FALSE",
    "LIVE_TRADING_AUTHORIZATION_FALSE",
)


def _require_scientific_claim_contract(value: OwnerWorkflowInput) -> None:
    """Fail before Official construction when Development claims omit locked limits."""

    if (
        value.workflow_purpose is OwnerWorkflowPurpose.QUALIFICATION_INTERFACE_FIXTURE
        or value.partition_role is not PartitionRole.DEVELOPMENT
    ):
        return
    tokens = {
        token.strip()
        for token in value.protocol.claim_basis.split(";")
        if token.strip()
    }
    missing = tuple(token for token in DEVELOPMENT_CLAIM_CONTROL_TOKENS if token not in tokens)
    if missing:
        raise ResearchError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            "Development claim basis omits mandatory exact tokens: " + ",".join(missing),
        )


def _startup_state_to_builtins(value: Any) -> Any:
    """Reconstruct the canonical JSON shape from bootstrap-frozen state."""

    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("bootstrap state mappings require string keys")
        return {key: _startup_state_to_builtins(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_startup_state_to_builtins(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"unsupported bootstrap state value: {type(value).__name__}")


def _require_verified_startup() -> dict[str, Any]:
    """Reject every direct/forged call before Owner workflow side effects."""

    module = sys.modules.get("_crypto_lab_verified_bootstrap")
    attestation = getattr(module, "ATTESTATION", None)
    encoded = getattr(module, "ATTESTATION_JSON", None)
    declared_hash = getattr(module, "ATTESTATION_SHA256", None)
    try:
        parsed = json.loads(encoded)
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        identity_material = dict(parsed)
        attestation_identity = identity_material.pop("attestation_identity")
        identity_bytes = json.dumps(
            identity_material,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        product = parsed["product"]
        valid = bool(
            parsed["schema"] == "isolated-runtime-bootstrap-attestation-v1"
            and parsed["target"] == "crypto_lab.owner:main"
            and product["package_prefix"] == "crypto_lab"
            and isinstance(product["source_commit"], str)
            and len(product["source_commit"]) == 40
            and isinstance(product["source_tree"], str)
            and len(product["source_tree"]) == 40
            and isinstance(attestation_identity, str)
            and attestation_identity == hashlib.sha256(identity_bytes).hexdigest()
            and encoded.encode("utf-8") == canonical
            and declared_hash == hashlib.sha256(canonical).hexdigest()
            and _startup_state_to_builtins(attestation) == parsed
            and dict(parsed["environment"]) == _official_child_environment()
        )
    except Exception:
        valid = False
    if not valid:
        raise ResearchError(
            FailureCode.RUNTIME_STARTUP_MISMATCH,
            "Owner workflow requires the verified isolated bootstrap entrypoint",
        )
    return parsed


class OwnerWorkflowInput(StrictModel):
    """All material per-trial Owner inputs; no material field has a default."""

    schema_version: int
    workflow_purpose: OwnerWorkflowPurpose
    protocol: ResearchProtocol
    trial_id: str
    candidate_id: str
    run_id: str
    registered_strategy_id: str
    strategy_spec: StrategySpec
    dataset_release_id: str
    qualified_profile_record_id: str
    partition_role: PartitionRole
    warmup_start: datetime
    scoring_start: datetime
    scoring_end_exclusive: datetime
    initial_capital: MoneyAmount
    fee_assumption: FeeAssumption
    seed: int

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "Owner workflow schema must be 1")
        validate_safe_component(self.trial_id, field="trial_id")
        validate_safe_component(self.run_id, field="run_id")
        validate_safe_component(self.registered_strategy_id, field="registered_strategy_id")
        for name in ("dataset_release_id", "qualified_profile_record_id"):
            value = getattr(self, name)
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, f"{name} is not SHA-256")
        if not self.warmup_start <= self.scoring_start < self.scoring_end_exclusive:
            raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "invalid workflow scoring boundaries")
        if (
            self.workflow_purpose is OwnerWorkflowPurpose.QUALIFICATION_INTERFACE_FIXTURE
            and self.partition_role is PartitionRole.FINAL_HOLDOUT
        ):
            raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID,
                "the qualification interface fixture cannot designate or consume Final Holdout",
            )


@dataclass(frozen=True)
class OwnerWorkflowResult:
    status: str
    trial_id: str
    run_id: str
    run_state: str
    checker_outcome: str
    official_seal_outcome: str
    claim_eligibility: str
    real_profitability_claim: bool
    report_id: str
    history_anchor_sha256: str
    final_holdout_used: bool
    replay_result: str
    replay_identity: str
    commits: tuple[str, ...]

    def to_builtins(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "trial_id": self.trial_id,
            "run_id": self.run_id,
            "run_state": self.run_state,
            "checker_outcome": self.checker_outcome,
            "official_seal_outcome": self.official_seal_outcome,
            "claim_eligibility": self.claim_eligibility,
            "real_profitability_claim": self.real_profitability_claim,
            "report_id": self.report_id,
            "history_anchor_sha256": self.history_anchor_sha256,
            "final_holdout_used": self.final_holdout_used,
            "replay_result": self.replay_result,
            "replay_identity": self.replay_identity,
            "commits": list(self.commits),
        }


def _load_terminal_run(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    status = json.loads((path / "status.json").read_text(encoding="utf-8"))
    result = json.loads((path / "nautilus_result.json").read_text(encoding="utf-8"))
    if not isinstance(status, dict) or not isinstance(result, dict):
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "terminal Run evidence must be JSON objects")
    return status, result


def _revalidate_component(
    directory: Path,
    *,
    repository: Path,
    current_head_required: bool,
):
    persisted = json.loads(
        (directory / "component_validation.json").read_text(encoding="utf-8"),
    )
    regenerated = check_evidence_directory(
        directory,
        repository_root=repository,
        official_source_required=True,
        source_revision_current_head_required=current_head_required,
    )
    if regenerated.to_builtins() != persisted:
        raise ResearchError(
            FailureCode.EVIDENCE_INCOMPLETE,
            "component validation is stale or forged",
        )
    return regenerated


def _require_official_seal(
    directory: Path,
    *,
    repository: Path,
    current_head_required: bool,
) -> None:
    seal = verify_official_seal(
        directory,
        repository_root=repository,
        source_revision_current_head_required=current_head_required,
    )
    if seal.outcome is not OfficialSealOutcome.OFFICIAL_SEAL_PASS:
        raise ResearchError(
            FailureCode.OFFICIAL_SEAL_FAILURE,
            "Official Run root attestation is not valid",
        )


def _write_research_diagnostics(
    value: OwnerWorkflowInput,
    *,
    repository: Path,
    run_dir: Path,
    result_ref: str,
) -> tuple[DiagnosticResolution, tuple[Path, ...]]:
    """Derive benchmark/performance/diagnostic artifacts in dependency order."""

    protocol = value.protocol
    benchmark_directory = repository / "research/benchmarks"
    benchmark_path = benchmark_directory / f"{protocol.required_benchmark.benchmark_id}.json"
    performance_path = repository / "research/performance" / f"{value.run_id}.json"
    paths: list[Path] = []
    family = value.strategy_spec.parameters.get("strategy_family")
    if family in {
        "BTCUSDT_DAILY_PRICE_VS_SMA20_TREND",
        "BTCUSDT_WEEKLY_TSMOM28_V1",
        "BUY_AND_HOLD_1X_V1",
    }:
        performance = derive_performance_diagnostics(
            run_dir=run_dir,
            protocol=protocol,
            benchmark_directory=benchmark_directory,
            resolve_benchmark=(
                value.workflow_purpose is not OwnerWorkflowPurpose.BENCHMARK_STUDY
                and benchmark_path.is_file()
            ),
        )
        write_performance_diagnostics(performance, performance_path)
        paths.append(performance_path)
        if value.workflow_purpose is OwnerWorkflowPurpose.BENCHMARK_STUDY:
            benchmark = derive_benchmark_evidence(
                run_dir=run_dir,
                protocol=protocol,
                trial_id=value.trial_id,
                result_ref=result_ref,
                performance_path=performance_path,
            )
            write_benchmark_evidence(benchmark, benchmark_path)
            paths.append(benchmark_path)
    diagnostic = derive_diagnostic_resolution(
        run_dir=run_dir,
        protocol=protocol,
        benchmark_directory=benchmark_directory,
    )
    diagnostic_path = repository / "research/diagnostics" / f"{value.run_id}.json"
    write_diagnostic_resolution(diagnostic, diagnostic_path)
    paths.insert(0, diagnostic_path)
    return diagnostic, tuple(paths)


def _replay_material(
    *,
    trial_id: str,
    primary_ref: str,
    replay_ref: str,
    primary_status: dict[str, Any],
    replay_status: dict[str, Any],
    primary_result: dict[str, Any],
    replay_result: dict[str, Any],
    read_only_checker_revalidated: bool,
) -> dict[str, Any]:
    primary_config = str(primary_result.get("config_sha256"))
    replay_config = str(replay_result.get("config_sha256"))
    primary_semantic = str(primary_result.get("semantic_digest"))
    replay_semantic = str(replay_result.get("semantic_digest"))
    matched = bool(
        primary_status.get("state") == "COMPLETED"
        and replay_status.get("state") == "COMPLETED"
        and primary_status.get("component_validation_outcome") == "COMPONENT_CHECK_PASS"
        and replay_status.get("component_validation_outcome") == "COMPONENT_CHECK_PASS"
        and primary_config == replay_config
        and primary_semantic == replay_semantic
        and read_only_checker_revalidated
    )
    return {
        "schema": "owner-deterministic-replay-v2",
        "trial_id": trial_id,
        "primary_run_ref": primary_ref,
        "replay_run_ref": replay_ref,
        "primary_config_sha256": primary_config,
        "replay_config_sha256": replay_config,
        "primary_semantic_digest": primary_semantic,
        "replay_semantic_digest": replay_semantic,
        "primary_state": str(primary_status.get("state")),
        "replay_state": str(replay_status.get("state")),
        "primary_component_validation": str(
            primary_status.get("component_validation_outcome"),
        ),
        "replay_component_validation": str(
            replay_status.get("component_validation_outcome"),
        ),
        "primary_official_seal": "OFFICIAL_SEAL_PASS" if read_only_checker_revalidated else "FAIL",
        "replay_official_seal": "OFFICIAL_SEAL_PASS" if read_only_checker_revalidated else "FAIL",
        "fresh_processes": True,
        "read_only_checker_revalidated": read_only_checker_revalidated,
        "result": "PASS" if matched else "FAIL",
    }


def _read_replay_evidence(repository: Path, trial_id: str) -> dict[str, Any]:
    path = repository / "research/replays" / f"{trial_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    declared = payload.pop("replay_identity", None)
    if declared != canonical_sha256(payload):
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "deterministic replay identity mismatch")
    if payload.get("trial_id") != trial_id or payload.get("result") != "PASS":
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "deterministic replay did not pass")
    primary_dir = (repository / str(payload["primary_run_ref"])).resolve(strict=True)
    replay_dir = (repository / str(payload["replay_run_ref"])).resolve(strict=True)
    for directory in (primary_dir, replay_dir):
        try:
            directory.relative_to(repository)
        except ValueError as exc:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "replay Run escapes repository") from exc
    primary_status, primary_result = _load_terminal_run(primary_dir)
    replay_status, replay_result = _load_terminal_run(replay_dir)
    checker_revalidated = True
    for directory in (primary_dir, replay_dir):
        try:
            _revalidate_component(
                directory,
                repository=repository,
                current_head_required=False,
            )
            _require_official_seal(
                directory,
                repository=repository,
                current_head_required=False,
            )
        except ResearchError:
            checker_revalidated = False
    derived = _replay_material(
        trial_id=trial_id,
        primary_ref=str(payload["primary_run_ref"]),
        replay_ref=str(payload["replay_run_ref"]),
        primary_status=primary_status,
        replay_status=replay_status,
        primary_result=primary_result,
        replay_result=replay_result,
        read_only_checker_revalidated=checker_revalidated,
    )
    operational_fields = {
        "primary_child_returncode",
        "replay_child_returncode",
        "primary_child_diagnostic",
        "replay_child_diagnostic",
    }
    if derived != {key: item for key, item in payload.items() if key not in operational_fields}:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "deterministic replay evidence is stale")
    return {"replay_identity": declared, **payload}


def _git(repository: Path, *args: str, check: bool = True) -> str:
    process = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "git command failed"
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, detail)
    return process.stdout.strip()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if not handle.closed:
            handle.close()
        temporary.unlink(missing_ok=True)


def _relative(repository: Path, path: Path) -> str:
    resolved = Path(path).resolve(strict=False)
    try:
        return resolved.relative_to(repository).as_posix()
    except ValueError as exc:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "workflow path escapes repository") from exc


def _status_paths(repository: Path) -> tuple[str, ...]:
    process = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "git status failed")
    output = process.stdout.rstrip("\n")
    result: list[str] = []
    for line in output.splitlines():
        raw = line[3:]
        if " -> " in raw:
            raw = raw.rsplit(" -> ", maxsplit=1)[1]
        result.append(raw.strip('"'))
    return tuple(result)


def _official_child_environment() -> dict[str, str]:
    """Return the complete environment, not an overlay on caller state."""

    return {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }


def _checkpoint_push_environment() -> dict[str, str]:
    """Return the complete credential-broker environment for Git publication only."""

    try:
        account = pwd.getpwuid(os.getuid())
        home = Path(account.pw_dir)
        metadata = home.lstat()
    except (KeyError, OSError) as exc:
        raise ResearchError(
            FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "checkpoint publisher account home is unavailable",
        ) from exc
    if (
        not home.is_absolute()
        or home.is_symlink()
        or not home.is_dir()
        or metadata.st_uid != os.getuid()
    ):
        raise ResearchError(
            FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "checkpoint publisher account home identity is unsafe",
        )
    git_config = home / ".gitconfig"
    gh_config = home / ".config" / "gh"
    state_home = home / ".local" / "state"
    try:
        git_config_metadata = git_config.lstat()
        gh_config_metadata = gh_config.lstat()
        state_home_metadata = state_home.lstat()
    except OSError as exc:
        raise ResearchError(
            FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "checkpoint credential-broker locators are unavailable",
        ) from exc
    if (
        git_config.is_symlink()
        or not git_config.is_file()
        or git_config_metadata.st_uid != os.getuid()
        or gh_config.is_symlink()
        or not gh_config.is_dir()
        or gh_config_metadata.st_uid != os.getuid()
        or state_home.is_symlink()
        or not state_home.is_dir()
        or state_home_metadata.st_uid != os.getuid()
    ):
        raise ResearchError(
            FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "checkpoint credential-broker locator identity is unsafe",
        )
    return {
        **_official_child_environment(),
        "GH_CONFIG_DIR": str(gh_config),
        "GIT_CONFIG_GLOBAL": str(git_config),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "XDG_STATE_HOME": str(state_home),
    }


def _checkpoint_push_command(branch: str) -> list[str]:
    """Build the non-interactive push command with repository hooks disabled."""

    if (
        not branch
        or branch.startswith("-")
        or any(character.isspace() for character in branch)
    ):
        raise ResearchError(
            FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "checkpoint branch name is unsafe",
        )
    return [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "push",
        "origin",
        f"HEAD:refs/heads/{branch}",
    ]


def _official_child_command(repository: Path, workflow_path: Path) -> list[str]:
    """Enter the child only through the stdlib isolated bootstrap."""

    bootstrap = repository / "scripts/isolated_runtime_bootstrap.py"
    authority = repository / "runtime-bootstrap-authority.json"
    if bootstrap.is_symlink() or not bootstrap.is_file():
        raise ResearchError(
            FailureCode.EVIDENCE_INCOMPLETE,
            "Official isolated bootstrap is missing or is a symlink",
        )
    return [
        sys.executable,
        "-I",
        "-P",
        "-S",
        "-B",
        "-X",
        "pycache_prefix=/dev/null",
        str(bootstrap),
        "--authority",
        str(authority),
        "--repository",
        str(repository),
        "--entrypoint",
        "crypto_lab.owner:main",
        "--",
        "--child",
        "--input",
        str(workflow_path),
        "--repository",
        str(repository),
    ]


def _checkpoint(
    repository: Path,
    *,
    paths: tuple[Path, ...],
    message: str,
) -> str:
    """Commit exactly the authorized checkpoint and normal-push it to origin."""

    relatives = tuple(_relative(repository, path) for path in paths)
    dirty = _status_paths(repository)
    unexpected = tuple(
        item
        for item in dirty
        if not any(item == allowed or item.startswith(allowed.rstrip("/") + "/") for allowed in relatives)
    )
    if unexpected:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "unexpected files at workflow checkpoint: " + ",".join(unexpected),
        )
    _git(repository, "add", "--", *relatives)
    if subprocess.run(
        ["git", "diff", "--cached", "--quiet", "--exit-code"],
        cwd=repository,
        check=False,
    ).returncode == 0:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "empty authoritative checkpoint")
    _git(repository, "commit", "-m", message)
    branch = _git(repository, "symbolic-ref", "--quiet", "--short", "HEAD")
    push = subprocess.run(
        _checkpoint_push_command(branch),
        cwd=repository,
        env=_checkpoint_push_environment(),
        check=False,
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        detail = push.stderr.strip() or push.stdout.strip() or "git push failed"
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, detail)
    head = _git(repository, "rev-parse", "HEAD")
    remote = _git(repository, "rev-parse", f"refs/remotes/origin/{branch}")
    clean, unexpected_after = worktree_is_clean(repository)
    if head != remote or not clean:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "checkpoint publication did not leave HEAD=origin/ref and clean: "
            + ",".join(unexpected_after),
        )
    return head


def _history(repository: Path) -> AuthoritativeResearchHistory:
    return AuthoritativeResearchHistory(
        HistoryAnchorStore(
            repository_root=repository,
            journal_path=repository / "research/trials.jsonl",
            holdout_path=repository / "research/holdout_lock.json",
            anchor_path=repository / "research/history_anchors.jsonl",
            require_remote_tip=True,
        ),
    )


def _committed_workflow_inputs(repository: Path) -> dict[str, OwnerWorkflowInput]:
    """Read workflow authorizations from Git HEAD, never from replaceable bytes."""

    listing = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD", "--", "research/workflows"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if listing.returncode != 0:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "cannot enumerate committed workflows")
    resolved: dict[str, OwnerWorkflowInput] = {}
    for relative in listing.stdout.splitlines():
        if not relative.endswith(".json"):
            continue
        committed = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=repository,
            check=False,
            capture_output=True,
        )
        current_path = repository / relative
        if (
            committed.returncode != 0
            or not current_path.is_file()
            or current_path.is_symlink()
            or current_path.read_bytes() != committed.stdout
        ):
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                f"workflow authorization differs from Git HEAD: {relative}",
            )
        value = OwnerWorkflowInput.from_json_bytes(committed.stdout)
        expected = f"research/workflows/{value.trial_id}.json"
        if relative != expected or value.trial_id in resolved:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "workflow identity/path collision")
        resolved[value.trial_id] = value
    return resolved


def _trial_definition_from_record(record: TrialRecord) -> TrialDefinition:
    return TrialDefinition(
        **{
            field.name: getattr(record, field.name)
            for field in fields(TrialDefinition)
        },
    )


def _expected_committed_workflow_definition(
    repository: Path,
    value: OwnerWorkflowInput,
) -> TrialDefinition:
    protocol = _refreeze_protocol(value.protocol)
    if protocol != value.protocol:
        raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "committed workflow protocol changed")
    source = capture_actual_source_revision(repository)
    request = build_official_request(
        value,
        repository_root=repository,
        source_revision=source,
    )
    return _definition(value, request.lab_run_config)


def _reconcile_recoverable_history(
    repository: Path,
    history: AuthoritativeResearchHistory,
    workflows: dict[str, OwnerWorkflowInput],
) -> bool:
    """Accept only one exact crash extension authorized by a committed workflow."""

    current_anchors = history.anchors.read_anchors()
    committed_anchors = history.anchors.committed_anchors()
    # A process can be lost after the batched PLANNED/STARTED Journal fsync and
    # before the immediately following Anchor fsync.  Recover only that exact
    # suffix after independently deriving its TrialDefinition from the
    # committed workflow authorization.  Any other unanchored bytes remain a
    # structured durability failure; they are never interpreted heuristically.
    if current_anchors and current_anchors == committed_anchors:
        records = history.journal.read_records()
        base_count = current_anchors[-1].trial_record_count
        unanchored = records[base_count:]
        if unanchored:
            if (
                len(unanchored) != 2
                or unanchored[0].state is not TrialState.PLANNED
                or unanchored[1].state is not TrialState.STARTED
                or unanchored[0].trial_id != unanchored[1].trial_id
            ):
                raise ResearchError(
                    FailureCode.JOURNAL_DURABILITY_FAILURE,
                    "unanchored history is not one recoverable PLANNED/STARTED batch",
                )
            trial_id = unanchored[0].trial_id
            workflow = workflows.get(trial_id)
            if workflow is None:
                raise ResearchError(
                    FailureCode.JOURNAL_DURABILITY_FAILURE,
                    "unanchored Trial start has no committed workflow authorization",
                )
            expected = _expected_committed_workflow_definition(repository, workflow)
            if any(
                _trial_definition_from_record(record) != expected
                for record in unanchored
            ):
                raise ResearchError(
                    FailureCode.JOURNAL_DURABILITY_FAILURE,
                    "unanchored Trial start differs from committed workflow authorization",
                )
            recovered = history.anchors.recover_unanchored_trial_start(expected)
            if recovered is None:
                raise ResearchError(
                    FailureCode.JOURNAL_DURABILITY_FAILURE,
                    "recoverable Trial start did not produce its missing Anchor",
                )
            current_anchors = history.anchors.read_anchors()
    extension_present = current_anchors != committed_anchors
    history.reconcile()
    if not extension_present:
        return False
    if not committed_anchors or len(current_anchors) != len(committed_anchors) + 1:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "uncommitted history extension is not one recoverable start checkpoint",
        )
    base_count = committed_anchors[-1].trial_record_count
    records = history.journal.read_records()
    extension = records[base_count:]
    if (
        len(extension) != 2
        or extension[0].state is not TrialState.PLANNED
        or extension[1].state is not TrialState.STARTED
        or extension[0].trial_id != extension[1].trial_id
        or extension[0].started_at_utc != extension[1].started_at_utc
        or extension[0].recorded_at_utc != extension[1].recorded_at_utc
    ):
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "uncommitted history is not an exact PLANNED/STARTED crash extension",
        )
    trial_id = extension[0].trial_id
    workflow = workflows.get(trial_id)
    if workflow is None:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "uncommitted trial has no immutable workflow authorization",
        )
    expected = _expected_committed_workflow_definition(repository, workflow)
    if any(_trial_definition_from_record(record) != expected for record in extension):
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "uncommitted trial differs from its immutable workflow authorization",
        )
    anchor = current_anchors[-1]
    if (
        anchor.operation != f"TRIAL_STARTED:{trial_id}"
        or anchor.created_at_utc != extension[0].started_at_utc
        or anchor.source_git_commit != _git(repository, "rev-parse", "HEAD")
        or anchor.trial_record_count != base_count + 2
    ):
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "uncommitted start anchor differs from authorized crash state",
        )
    return True


def _qualified_profile_registry_candidates(repository: Path) -> tuple[Path, ...]:
    """Return current-to-historical registry locators in explicit authority order."""

    return (
        repository
        / "evidence/audit/adversarial-remediation-002/qualification-retry-012/qualified-profile-registry.json",
        repository
        / "evidence/audit/adversarial-remediation-002/qualification-retry-011/qualified-profile-registry.json",
        repository
        / "evidence/audit/adversarial-remediation-002/qualification-retry-010/qualified-profile-registry.json",
        repository
        / "evidence/audit/adversarial-remediation-002/qualification-retry-009/qualified-profile-registry.json",
        repository
        / "evidence/audit/adversarial-remediation-002/qualification-retry-008/qualified-profile-registry.json",
        repository
        / "evidence/audit/adversarial-remediation-002/qualification-retry-007/qualified-profile-registry.json",
        repository
        / "evidence/audit/adversarial-remediation-002/qualification/qualified-profile-registry.json",
        repository
        / "evidence/audit/comprehensive-remediation-001/qualification-runtime-proof/qualified-profile-registry.json",
        repository
        / "evidence/audit/comprehensive-remediation-001/qualification/qualified-profile-registry.json",
        repository / "evidence/m3/m3-acceptance-001/qualified-profile-registry.json",
    )


def _qualified_profile(
    repository: Path,
    value: OwnerWorkflowInput,
) -> tuple[QualifiedProfileRecord, Path]:
    candidates = _qualified_profile_registry_candidates(repository)
    registry_path = next((path for path in candidates if path.is_file()), None)
    if registry_path is None:
        raise ResearchError(
            FailureCode.DOWNSTREAM_CONTRACT_FAILURE,
            "Current Qualified Profile registry is unavailable",
        )
    registry = QualifiedProfileRegistry.from_json_bytes(registry_path.read_bytes())
    matches = [
        item
        for item in registry.records
        if item.qualified_profile_record_id == value.qualified_profile_record_id
    ]
    if len(matches) != 1:
        raise ResearchError(
            FailureCode.DOWNSTREAM_CONTRACT_FAILURE,
            "Qualified Profile must resolve exactly once in the current authority",
        )
    record = matches[0]
    if record.profile_id is not value.protocol.market_profile:
        raise ResearchError(FailureCode.DOWNSTREAM_CONTRACT_FAILURE, "Qualified Profile locator mismatch")
    if (
        record.schema_version != 2
        or record.checker_result != "COMPONENT_CHECK_PASS"
        or record.replay_result != "PASS"
    ):
        raise ResearchError(FailureCode.DOWNSTREAM_CONTRACT_FAILURE, "Qualified Profile is unavailable")
    if record.runtime_lock_sha256 != sha256_file(repository / "runtime.lock.json"):
        raise ResearchError(
            FailureCode.RUNTIME_LOCK_MISMATCH,
            "Qualified Profile was not produced by the active Runtime Lock",
        )
    return record, registry_path


def _release(repository: Path, value: OwnerWorkflowInput) -> DatasetRelease:
    path = repository / "data/releases" / f"{value.dataset_release_id}.json"
    release = DatasetRelease.from_json_bytes(path.read_bytes())
    if (
        release.dataset_release_id != value.dataset_release_id
        or release.market_profile is not value.protocol.market_profile
        or release.dataset_release_id not in value.protocol.dataset_release_ids
    ):
        raise ResearchError(FailureCode.DOWNSTREAM_CONTRACT_FAILURE, "Dataset Release locator mismatch")
    expected_normalizer = (
        M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION
        if value.workflow_purpose is OwnerWorkflowPurpose.QUALIFICATION_INTERFACE_FIXTURE
        else FULL_RAW_INVENTORY_NORMALIZER_VERSION
    )
    if (
        not release.has_full_raw_inventory
        or release.normalizer_version != expected_normalizer
    ):
        raise ResearchError(
            FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
            "Dataset Release is not eligible for this Owner workflow purpose",
        )
    return release


def _research_rebuild_validation(
    repository: Path,
    *,
    release: DatasetRelease,
    source_revision: SourceRevision,
) -> tuple[dict[str, Any], str]:
    ref = RESEARCH_REBUILD_VALIDATION_REF
    path = repository / ref
    try:
        if path.is_symlink() or not path.is_file() or path.resolve(strict=True).parent != path.parent:
            raise ValueError("proof is not a contained regular file")
        payload = path.read_bytes()
        if payload != canonical_json_bytes(json.loads(payload)) + b"\n":
            raise ValueError("proof is not canonical JSON")
        frozen = subprocess.run(
            ["git", "--no-replace-objects", "show", f"{source_revision.git_commit}:{ref}"],
            cwd=repository,
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "TZ": "UTC",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
            },
            check=True,
            capture_output=True,
        ).stdout
        if frozen != payload:
            raise ValueError("proof bytes differ from the Run Source Revision")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("proof root is not an object")
        validate_research_dataset_rebuild_proof(release, value)
    except Exception as exc:
        raise ResearchError(
            FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
            f"Dataset rebuild validation is unavailable or invalid: {exc}",
        ) from exc
    return value, ref


def _refreeze_protocol(protocol: ResearchProtocol) -> ResearchProtocol:
    values = {
        field.name: getattr(protocol, field.name)
        for field in fields(protocol)
        if field.name not in {"schema_version", "protocol_id", "frozen_at_utc"}
    }
    recreated = ResearchProtocol.create(
        frozen_at_utc=protocol.frozen_at_utc,
        **values,
    )
    if recreated != protocol:
        raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "ResearchProtocol did not refreeze exactly")
    return recreated


def _interval(protocol: ResearchProtocol, role: PartitionRole) -> Any:
    return {
        PartitionRole.DEVELOPMENT: protocol.development_interval,
        PartitionRole.VALIDATION: protocol.validation_interval,
        PartitionRole.OOS: protocol.oos_interval,
        PartitionRole.FINAL_HOLDOUT: protocol.final_holdout_interval,
    }[role]


def build_official_request(
    value: OwnerWorkflowInput,
    *,
    repository_root: Path,
    source_revision: Any,
) -> OfficialLabRunRequest:
    """Resolve Profile and Dataset identities into one immutable Official request."""

    _require_scientific_claim_contract(value)
    repository = Path(repository_root).resolve(strict=True)
    profile, registry_path = _qualified_profile(repository, value)
    release = _release(repository, value)
    template_dir = registry_path.parent / profile.evidence_references[0]
    template = LabRunConfig.from_json_bytes((template_dir / "lab_run_config.json").read_bytes())
    if template.market_profile is not profile.profile_id:
        raise ResearchError(FailureCode.DOWNSTREAM_CONTRACT_FAILURE, "Qualified config/profile mismatch")
    spec = value.strategy_spec
    expected_signal_type = (
        f"{release.instrument_id}-1-MINUTE-LAST-EXTERNAL"
        if value.registered_strategy_id
        in {"qualification_fixture_first_eligible_bar_v1", "buy_and_hold_1x_v1"}
        else f"{release.instrument_id}-1-DAY-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )
    if (
        spec.market_profile is not release.market_profile
        or spec.instrument_id != release.instrument_id
        or spec.signal_bar_types != (expected_signal_type,)
    ):
        raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "StrategySpec/Profile/Instrument mismatch")
    strategy_identity = resolve_registered_strategy_identity(
        value.registered_strategy_id,
        strategy_spec=spec,
        source_revision=source_revision,
    )
    if value.workflow_purpose is OwnerWorkflowPurpose.QUALIFICATION_INTERFACE_FIXTURE and (
        not strategy_identity.qualification_fixture_only
        or strategy_identity.profitability_claim_eligible
    ):
        raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID,
            "qualification workflow purpose requires a claim-ineligible qualification registration",
        )
    data_config = tuple(
        replace(
            item,
            catalog_path=str(repository / "data/catalog" / release.catalog_identity),
            instrument_id=release.instrument_id,
            start_time=release.normalized_time_range.start_inclusive,
            end_time=release.normalized_time_range.end_exclusive,
        )
        for item in template.nautilus_data_config
    )
    venue = replace(
        template.nautilus_venue_config,
        starting_balances=(value.initial_capital,),
        instrument_leverages=(
            ()
            if release.market_profile.value == "BINANCE_SPOT_CASH_LONG_ONLY"
            else tuple(
                replace(item, instrument_id=release.instrument_id)
                for item in template.nautilus_venue_config.instrument_leverages
            )
        ),
    )
    config = replace(
        template,
        run_id=value.run_id,
        run_purpose=RunPurpose.OFFICIAL,
        runtime_lock_sha256=sha256_file(repository / "runtime.lock.json"),
        dataset_release_id=release.dataset_release_id,
        strategy_spec_id=spec.strategy_spec_id,
        initial_capital=value.initial_capital,
        warmup_start=value.warmup_start,
        scoring_start=value.scoring_start,
        scoring_end_exclusive=value.scoring_end_exclusive,
        execution_bar_type=f"{release.instrument_id}-1-MINUTE-LAST-EXTERNAL",
        signal_bar_types=spec.signal_bar_types,
        nautilus_venue_config=venue,
        nautilus_data_config=data_config,
        fee_assumption=value.fee_assumption,
        funding_binding=release.funding_data_identity,
        mark_binding=release.mark_data_identity,
        random_seeds=(NamedSeed(name="fill_model", value=0), NamedSeed(name="protocol", value=value.seed)),
        research_protocol_id=value.protocol.protocol_id,
    )
    rebuild_validation: dict[str, Any] | None = None
    rebuild_validation_ref = "NOT_APPLICABLE"
    if value.workflow_purpose is not OwnerWorkflowPurpose.QUALIFICATION_INTERFACE_FIXTURE:
        rebuild_validation, rebuild_validation_ref = _research_rebuild_validation(
            repository,
            release=release,
            source_revision=source_revision,
        )
    return OfficialLabRunRequest(
        lab_run_config=config,
        source_revision=source_revision,
        strategy_spec=spec,
        dataset_release=release,
        registered_strategy_id=value.registered_strategy_id,
        qualified_profile_record_id=profile.qualified_profile_record_id,
        qualified_profile_registry_ref=registry_path.relative_to(repository).as_posix(),
        qualified_profile_registry_sha256=sha256_file(registry_path),
        dataset_rebuild_validation=rebuild_validation,
        dataset_rebuild_validation_ref=rebuild_validation_ref,
        evidence_root=repository / "runs",
        repository_root=repository,
    )


def _definition(value: OwnerWorkflowInput, config: LabRunConfig) -> TrialDefinition:
    protocol = value.protocol
    candidates = {candidate.candidate_id: candidate for candidate in protocol.ordered_candidates}
    expected_interval = _interval(protocol, value.partition_role)
    if value.workflow_purpose is OwnerWorkflowPurpose.BENCHMARK_STUDY:
        expected_candidate_id = benchmark_trial_candidate_id(
            protocol.required_benchmark,
            strategy_spec_id=value.strategy_spec.strategy_spec_id,
        )
        if (
            value.candidate_id != expected_candidate_id
            or value.strategy_spec.parameters.get("benchmark_id")
            != protocol.required_benchmark.benchmark_id
            or value.strategy_spec.parameters.get("strategy_family") != "BUY_AND_HOLD_1X_V1"
            or protocol.required_benchmark.scored_interval != expected_interval
        ):
            raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "benchmark differs from frozen protocol")
        candidate_id = expected_candidate_id
        parameter_hash = canonical_sha256(dict(value.strategy_spec.parameters))
    else:
        candidate = candidates.get(value.candidate_id)
        if candidate is None:
            raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "candidate is outside frozen protocol")
        if (
            candidate.strategy_spec_id != value.strategy_spec.strategy_spec_id
            or dict(candidate.parameter_values) != dict(value.strategy_spec.parameters)
        ):
            raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "trial changed frozen candidate inputs")
        candidate_id = candidate.candidate_id
        parameter_hash = canonical_sha256(dict(candidate.parameter_values))
    if (
        value.seed not in protocol.random_seeds
        or expected_interval.start_inclusive != value.scoring_start
        or expected_interval.end_exclusive != value.scoring_end_exclusive
    ):
        raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "trial changed frozen protocol inputs")
    return TrialDefinition(
        trial_id=value.trial_id,
        research_family_id=protocol.research_family_id,
        hypothesis_id=protocol.hypothesis_id,
        protocol_id=protocol.protocol_id,
        candidate_id=candidate_id,
        candidate_parameters_sha256=parameter_hash,
        run_id=value.run_id,
        config_sha256=config.config_sha256,
        strategy_spec_id=value.strategy_spec.strategy_spec_id,
        dataset_release_id=value.dataset_release_id,
        partition_role=value.partition_role,
        seed=value.seed,
        market_profile=protocol.market_profile,
        instrument_id=value.strategy_spec.instrument_id,
        scored_interval=expected_interval,
    )


def _validate_candidate_order(
    history: AuthoritativeResearchHistory,
    value: OwnerWorkflowInput,
) -> None:
    records = history.journal.read_records()
    candidate_ids = {candidate.candidate_id for candidate in value.protocol.ordered_candidates}
    benchmark_id = benchmark_trial_candidate_id(
        value.protocol.required_benchmark,
        strategy_spec_id=value.strategy_spec.strategy_spec_id,
    )
    all_protocol_records = [
        record
        for record in records
        if record.protocol_id == value.protocol.protocol_id
    ]
    protocol_records = [
        record for record in all_protocol_records if record.state is TrialState.STARTED
    ]
    terminal_by_trial = {
        record.trial_id: record
        for record in all_protocol_records
        if record.state in TERMINAL_TRIAL_STATES
    }
    candidate_records = [
        record for record in protocol_records if record.candidate_id in candidate_ids
    ]
    benchmark_records = [
        record for record in protocol_records if record.candidate_id not in candidate_ids
    ]
    valid_benchmark_records = [
        record
        for record in benchmark_records
        if record.candidate_id
        == benchmark_trial_candidate_id(
            value.protocol.required_benchmark,
            strategy_spec_id=record.strategy_spec_id,
        )
    ]
    benchmark_completed = any(
        terminal_by_trial.get(record.trial_id) is not None
        and terminal_by_trial[record.trial_id].state is TrialState.COMPLETED
        for record in valid_benchmark_records
    )
    if value.workflow_purpose is OwnerWorkflowPurpose.BENCHMARK_STUDY:
        if candidate_records or benchmark_completed:
            raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID,
                "benchmark retry is allowed only before candidates and until one completed attempt",
            )
        if value.candidate_id != benchmark_id:
            raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "benchmark journal identity diverged")
        return
    if (
        value.workflow_purpose is OwnerWorkflowPurpose.QUALIFICATION_INTERFACE_FIXTURE
        or value.protocol.strategy_family != "BTCUSDT_WEEKLY_TSMOM28_V1"
    ):
        started = [record for record in protocol_records if record.candidate_id in candidate_ids]
        if len(started) >= value.protocol.search_budget:
            raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "frozen search budget exhausted")
        expected = value.protocol.ordered_candidates[len(started)]
        if expected.candidate_id != value.candidate_id:
            raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "candidate order diverged from protocol")
        return
    if len(valid_benchmark_records) != len(benchmark_records) or not benchmark_completed:
        raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID,
            "one frozen benchmark attempt must complete before candidate result access",
        )

    expected = None
    for candidate in value.protocol.ordered_candidates[: value.protocol.search_budget]:
        attempts = [
            record for record in candidate_records if record.candidate_id == candidate.candidate_id
        ]
        completed = any(
            terminal_by_trial.get(record.trial_id) is not None
            and terminal_by_trial[record.trial_id].state is TrialState.COMPLETED
            for record in attempts
        )
        if not completed:
            expected = candidate
            break
    if expected is None:
        raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "frozen search budget exhausted")
    if expected.candidate_id != value.candidate_id:
        raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "candidate order diverged from protocol")


def _prospective_exposure(
    value: OwnerWorkflowInput,
    definition: TrialDefinition,
    *,
    source_revision: Any,
    at_utc: datetime,
) -> ResultExposure:
    return ResultExposure(
        trial_id=definition.trial_id,
        market_profile=definition.market_profile,
        instrument_id=definition.instrument_id,
        scored_interval=definition.scored_interval,
        research_family_id=definition.research_family_id,
        hypothesis_lineage=(definition.hypothesis_id,),
        strategy_lineage=(definition.strategy_spec_id,),
        dataset_release_id=definition.dataset_release_id,
        first_exposure_at_utc=at_utc,
        exposure_type=definition.partition_role.value,
        evidence_reference=f"runs/{definition.run_id}-{definition.config_sha256[:12]}",
        source_branch=source_revision.branch_ref,
        source_commit=source_revision.git_commit,
        seed=definition.seed,
        result_bearing=True,
    )


def qualification_workflow_fixture_input(
    *,
    repository_root: Path,
    frozen_at_utc: datetime,
    trial_id: str,
    run_id: str,
) -> OwnerWorkflowInput:
    """Build the fixed exposed-data interface fixture, never an Owner study."""

    repository = Path(repository_root).resolve(strict=True)
    registry_path = next(
        path
        for path in _qualified_profile_registry_candidates(repository)
        if path.is_file()
    )
    registry = QualifiedProfileRegistry.from_json_bytes(
        registry_path.read_bytes(),
    )
    profile = next(
        item
        for item in registry.records
        if item.profile_id is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
    )
    if profile.runtime_lock_sha256 != sha256_file(repository / "runtime.lock.json"):
        raise ResearchError(
            FailureCode.RUNTIME_LOCK_MISMATCH,
            "qualification fixture requires a profile qualified by the active Runtime Lock",
        )
    if profile.schema_version != 2 or profile.checker_result != "COMPONENT_CHECK_PASS":
        raise ResearchError(
            FailureCode.DOWNSTREAM_CONTRACT_FAILURE,
            "qualification fixture requires a current component-validated profile",
        )
    release_id = profile.dataset_release_id
    release = DatasetRelease.from_json_bytes(
        (repository / "data/releases" / f"{release_id}.json").read_bytes(),
    )
    bar_type = f"{release.instrument_id}-1-MINUTE-LAST-EXTERNAL"
    parameters = {
        "fixture_purpose": "PUBLIC_BOUNDARY_QUALIFICATION_ONLY",
        "network_access": "FORBIDDEN",
        "order_quantity": "0.00100",
        "order_side": "BUY",
        "profitability_claim": "INELIGIBLE",
        "trigger": "FIRST_SCORING_ELIGIBLE_BAR",
    }
    spec = StrategySpec(
        strategy_id="owner-public-interface-qualification-fixture",
        strategy_version="1",
        market_profile=release.market_profile,
        instrument_id=release.instrument_id,
        signal_bar_types=(bar_type,),
        parameters=parameters,
        indicator_definitions=(),
        warmup_requirement="NO_INDICATOR_WARMUP",
        sizing_rule="FIXED_EXPLICIT_QUALIFICATION_QUANTITY",
        entry_rule="FIRST_SCORING_ELIGIBLE_BAR_ONLY",
        exit_rule="NO_EXIT_QUALIFICATION_FIXTURE",
        conflict_rule="FIRST_ELIGIBLE_INTENT",
        terminal_behavior="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
        market_order_time_in_force="GTC",
    )
    candidate = CandidateSpec.create(
        candidate_label="qualification-interface-only",
        strategy_spec_id=spec.strategy_spec_id,
        parameter_values=parameters,
    )
    start = release.normalized_time_range.start_inclusive

    def interval(first_minute: int, last_minute: int) -> UtcInterval:
        return UtcInterval(
            start_inclusive=start + timedelta(minutes=first_minute),
            end_exclusive=start + timedelta(minutes=last_minute),
        )

    protocol = ResearchProtocol.create(
        frozen_at_utc=frozen_at_utc,
        research_family_id=f"qualification-interface-family-{trial_id}",
        hypothesis_id="public-interface-contract-only-no-economic-hypothesis",
        research_intent=ResearchIntent.EXPLORATORY,
        market_profile=release.market_profile,
        instrument_scope=InstrumentScope.SINGLE_INSTRUMENT,
        instrument_ids=(release.instrument_id,),
        instrument_selection_basis="M3 exposed qualification fixture only",
        universe_selection_rule="NOT_APPLICABLE",
        universe_as_of_rule="NOT_APPLICABLE",
        universe_membership_sha256="NOT_APPLICABLE",
        dataset_release_ids=(release.dataset_release_id,),
        strategy_family="REGISTERED_QUALIFICATION_INTERFACE_FIXTURE",
        ordered_candidates=(candidate,),
        parameter_domain={name: (item,) for name, item in parameters.items()},
        search_budget=1,
        candidate_ordering="AS_LISTED",
        deterministic_generator="NOT_APPLICABLE",
        random_seeds=(7,),
        primary_metric="NOT_APPLICABLE_QUALIFICATION_INTERFACE",
        required_benchmark=BenchmarkSpec(
            benchmark_id="QUALIFICATION_INTERFACE_NO_BENCHMARK",
            definition="No economic benchmark; contract interface fixture only",
            scored_interval=interval(5, 8),
            cost_basis="SAME_EXPLICIT_QUALIFICATION_FEE_BASIS",
            frozen_before_result_exposure=True,
        ),
        selection_rule="ONLY_PREDECLARED_QUALIFICATION_FIXTURE",
        tie_break_rule="NOT_APPLICABLE_SINGLE_CANDIDATE",
        development_interval=interval(0, 1),
        validation_interval=interval(1, 4),
        oos_interval=interval(4, 5),
        final_holdout_interval=interval(5, 8),
        purge_embargo_rule=PurgeEmbargoRule(
            mode="NOT_APPLICABLE",
            reason="No feature label target or training sample",
            purge_seconds=0,
            embargo_seconds=0,
            max_forward_dependency_seconds=0,
        ),
        time_series_split="CHRONOLOGICAL",
        multiple_testing_treatment="NOT_APPLICABLE",
        sample_adequacy_rule=SampleAdequacyRule(
            counted_observation="NOT_APPLICABLE",
            minimum_completed_trades="NOT_APPLICABLE",
            rationale="Qualification interface fixture makes no trade claim",
        ),
        monte_carlo_spec=MonteCarloSpec(
            resampling_method=ResamplingMethod.NOT_APPLICABLE,
            simulation_count=0,
            random_seed=7,
            block_length="NOT_APPLICABLE",
            quantile_method="R7_LINEAR_INTERPOLATION",
            decimal_places=8,
            not_applicable_reason="Qualification interface fixture makes no profitability claim",
        ),
        intended_claim_scope=ClaimScope.INSTRUMENT_ONLY,
        claim_basis="NO_PROFITABILITY_CLAIM_QUALIFICATION_INTERFACE_ONLY",
        kill_criteria=("MECHANICAL_INTEGRITY_NOT_PASS", "CHECKER_NOT_PASS"),
        terminal_policy="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
    )
    return OwnerWorkflowInput(
        schema_version=1,
        workflow_purpose=OwnerWorkflowPurpose.QUALIFICATION_INTERFACE_FIXTURE,
        protocol=protocol,
        trial_id=trial_id,
        candidate_id=candidate.candidate_id,
        run_id=run_id,
        registered_strategy_id="qualification_fixture_first_eligible_bar_v1",
        strategy_spec=spec,
        dataset_release_id=release.dataset_release_id,
        qualified_profile_record_id=profile.qualified_profile_record_id,
        partition_role=PartitionRole.VALIDATION,
        warmup_start=start,
        scoring_start=interval(1, 4).start_inclusive,
        scoring_end_exclusive=interval(1, 4).end_exclusive,
        initial_capital=MoneyAmount(amount=Decimal("1000.00"), currency="USDT"),
        fee_assumption=FeeAssumption(
            maker_fee=Decimal("0.001"),
            taker_fee=Decimal("0.001"),
            explicit_zero_fee=False,
            reason="SSOT Appendix A qualification-only observable estimated fee",
            claim_class="ESTIMATED_FEE",
        ),
        seed=7,
    )


def _child_run(
    input_path: Path,
    repository: Path,
    *,
    evidence_root: Path | None = None,
) -> int:
    _require_verified_startup()
    value = OwnerWorkflowInput.from_json_bytes(input_path.read_bytes())
    protocol_path = repository / "research/protocols" / f"{value.protocol.protocol_id}.json"
    if ResearchProtocol.from_json_bytes(protocol_path.read_bytes()) != value.protocol:
        raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "child protocol evidence mismatch")
    source = capture_actual_source_revision(repository)
    request = build_official_request(value, repository_root=repository, source_revision=source)
    if evidence_root is not None:
        resolved_root = Path(evidence_root).resolve(strict=False)
        try:
            relative_root = resolved_root.relative_to(repository)
        except ValueError as exc:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "child evidence root escapes repository") from exc
        if not relative_root.parts or relative_root.parts[0] != ".owner-runtime":
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE,
                "child staging evidence must remain under .owner-runtime",
            )
        request = replace(request, evidence_root=resolved_root)
    result = run_official_lab(request)
    return 0 if result.state is RunState.COMPLETED else 2


def _recover_started(
    repository: Path,
    history: AuthoritativeResearchHistory,
) -> tuple[str, ...]:
    workflows = _committed_workflow_inputs(repository)
    # A self-consistent uncommitted chain is not sufficient authority.  It is
    # recoverable only when its exact TrialDefinition was committed and pushed
    # as a workflow intent before the journal mutation.
    uncommitted_start = _reconcile_recoverable_history(
        repository,
        history,
        workflows,
    )
    records = history.journal.read_records()
    latest = {record.trial_id: record for record in records}
    incomplete = tuple(record for record in latest.values() if record.state is TrialState.STARTED)
    if len(incomplete) > 1:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "more than one active Official trial cannot be recovered deterministically",
        )

    if uncommitted_start:
        if len(incomplete) != 1:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                "authorized start extension does not resolve to one active trial",
            )
        _checkpoint(
            repository,
            paths=(history.anchors.journal_path, history.anchors.anchor_path),
            message=f"repair(owner): publish recovered start {incomplete[0].trial_id}",
        )

    if incomplete:
        record = incomplete[0]
        if record.trial_id not in workflows:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                "active trial has no committed workflow authorization",
            )
        expected = _expected_committed_workflow_definition(repository, workflows[record.trial_id])
        if _trial_definition_from_record(record) != expected:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                "active trial differs from its committed workflow authorization",
            )
        run_dir = repository / "runs" / f"{record.run_id}-{record.config_sha256[:12]}"
        allowed = (run_dir,) if run_dir.exists() else ()
        clean, unexpected = worktree_is_clean(repository, allowed_output_paths=allowed)
        if not clean:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                "recovery found unrelated dirty paths: " + ",".join(unexpected),
            )
        state = TrialState.ABORTED
        reason = "FAIL_CLOSED_RECOVERY_AFTER_INTERRUPTED_OFFICIAL_PROCESS"
        result_ref = "NOT_APPLICABLE"
        exposed = False
        status_path = run_dir / "status.json"
        if status_path.is_file():
            result_ref = _relative(repository, run_dir)
            exposed = True
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
                parsed = TrialState(str(status["state"]))
                regenerated = _revalidate_component(
                    run_dir,
                    repository=repository,
                    current_head_required=False,
                )
                if parsed is TrialState.COMPLETED:
                    _require_official_seal(
                        run_dir,
                        repository=repository,
                        current_head_required=False,
                    )
                complete_terminal = (
                    parsed in TERMINAL_TRIAL_STATES
                    and (run_dir / "evidence_manifest.json").is_file()
                    and (run_dir / "official_seal.json").is_file()
                )
            except Exception:
                complete_terminal = False
            if complete_terminal:
                state = parsed
                reason = "RECOVERED_VERIFIED_PERSISTED_TERMINAL_RUN_STATUS"
            else:
                reason = "ABORTED_INCOMPLETE_OR_UNVERIFIED_PERSISTED_RUN_EVIDENCE"
        elif run_dir.exists():
            result_ref = _relative(repository, run_dir)
            exposed = any(run_dir.iterdir())
        history.finish_trial(
            record.trial_id,
            state=state,
            at_utc=datetime.now(UTC),
            result_ref=result_ref,
            reason=reason,
            result_exposed=exposed,
        )
        touched = [history.anchors.journal_path, history.anchors.anchor_path]
        if run_dir.exists():
            touched.append(run_dir)
        _checkpoint(
            repository,
            paths=tuple(touched),
            message=f"repair(owner): preserve interrupted trial {record.trial_id}",
        )
        return (record.trial_id,)

    # A workflow intent committed before PLANNED/STARTED is itself a visible
    # attempt.  If execution never started, record a full ABORTED transition so
    # a retry cannot silently erase the interruption.
    pending = tuple(
        workflow
        for trial_id, workflow in workflows.items()
        if trial_id not in latest
    )
    if not pending:
        return ()
    if len(pending) > 1:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "more than one committed pending workflow requires manual review",
        )
    workflow = pending[0]
    clean, unexpected = worktree_is_clean(repository)
    if not clean:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "pending-workflow recovery found dirty paths: " + ",".join(unexpected),
        )
    definition = _expected_committed_workflow_definition(repository, workflow)
    started_at = datetime.now(UTC)
    history.start_trial(definition, at_utc=started_at)
    _checkpoint(
        repository,
        paths=(history.anchors.journal_path, history.anchors.anchor_path),
        message=f"repair(owner): record interrupted intent {workflow.trial_id}",
    )
    history.finish_trial(
        workflow.trial_id,
        state=TrialState.ABORTED,
        at_utc=datetime.now(UTC),
        result_ref="NOT_APPLICABLE",
        reason="FAIL_CLOSED_RECOVERY_BEFORE_OFFICIAL_PROCESS_START",
        result_exposed=False,
    )
    _checkpoint(
        repository,
        paths=(history.anchors.journal_path, history.anchors.anchor_path),
        message=f"repair(owner): terminalize interrupted intent {workflow.trial_id}",
    )
    return (workflow.trial_id,)


def _resume_completed_workflow(
    value: OwnerWorkflowInput,
    *,
    repository: Path,
    history: AuthoritativeResearchHistory,
) -> OwnerWorkflowResult | None:
    """Resume post-terminal evidence derivation without rerunning Nautilus."""

    authorized = _committed_workflow_inputs(repository).get(value.trial_id)
    if authorized is None:
        return None
    if authorized != value:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "requested retry differs from its committed workflow authorization",
        )
    latest = {record.trial_id: record for record in history.journal.read_records()}
    terminal = latest.get(value.trial_id)
    if terminal is None or terminal.state not in TERMINAL_TRIAL_STATES:
        return None
    if terminal.state is not TrialState.COMPLETED or terminal.result_ref == "NOT_APPLICABLE":
        raise ResearchError(FailureCode.DOWNSTREAM_CONTRACT_FAILURE,
            f"authorized workflow is already terminal as {terminal.state.value}",
        )
    run_dir = (repository / terminal.result_ref).resolve(strict=True)
    try:
        run_dir.relative_to(repository)
    except ValueError as exc:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "terminal Run escapes repository") from exc
    config = LabRunConfig.from_json_bytes((run_dir / "lab_run_config.json").read_bytes())
    expected_definition = _definition(value, config)
    if _trial_definition_from_record(terminal) != expected_definition:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "terminal trial differs from committed workflow authorization",
        )
    checker = _revalidate_component(
        run_dir,
        repository=repository,
        current_head_required=False,
    )
    _require_official_seal(
        run_dir,
        repository=repository,
        current_head_required=False,
    )
    replay = (
        _read_replay_evidence(repository, value.trial_id)
        if value.workflow_purpose in {
            OwnerWorkflowPurpose.OWNER_STUDY,
            OwnerWorkflowPurpose.BENCHMARK_STUDY,
        }
        else None
    )
    if replay is not None and replay["primary_run_ref"] != terminal.result_ref:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "replay primary reference differs from Journal")

    commits: list[str] = []
    if value.partition_role is PartitionRole.FINAL_HOLDOUT:
        source = SourceRevision.from_json_bytes((run_dir / "source_revision.json").read_bytes())
        exposure = _prospective_exposure(
            value,
            expected_definition,
            source_revision=source,
            at_utc=terminal.finished_at_utc,
        )
        resolver = AuthoritativeExposureResolver(repository_root=repository)
        matching = next(
            (entry for entry in history.holdout.read().entries if entry.exposure == exposure),
            None,
        )
        if matching is None:
            history.consume_holdout(
                exposure,
                exposure_resolver=resolver,
                at_utc=terminal.finished_at_utc,
            )
            commits.append(
                _checkpoint(
                    repository,
                    paths=(history.anchors.holdout_path, history.anchors.anchor_path),
                    message=f"research(owner): recover Holdout for {value.trial_id}",
                ),
            )
        else:
            resolver.reconcile_consumed(
                exposure,
                history=history,
                entry_id=matching.entry_id,
            )

    possible_paths = (
        repository / "research/diagnostics" / f"{value.run_id}.json",
        repository / "research/performance" / f"{value.run_id}.json",
        repository / "research/benchmarks" / f"{value.protocol.required_benchmark.benchmark_id}.json",
    )
    prior = {path: path.read_bytes() for path in possible_paths if path.is_file()}
    diagnostic, derived_paths = _write_research_diagnostics(
        value,
        repository=repository,
        run_dir=run_dir,
        result_ref=terminal.result_ref,
    )
    if any(path.read_bytes() != payload for path, payload in prior.items()):
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "persisted research diagnostics are forged")
    recovered_paths = tuple(path for path in derived_paths if path not in prior)
    if recovered_paths:
        commits.append(
            _checkpoint(
                repository,
                paths=recovered_paths,
                message=f"research(owner): recover diagnostics for {value.trial_id}",
            ),
        )

    anchor = history.anchors.reconcile_committed()
    locator = OfficialEvidenceLocator(
        schema_version=1,
        protocol_id=value.protocol.protocol_id,
        selected_trial_id=value.trial_id,
        expected_history_anchor_sha256=anchor.anchor_sha256,
        report_purpose=(
            "QUALIFICATION_WORKFLOW_FIXTURE"
            if value.workflow_purpose is OwnerWorkflowPurpose.QUALIFICATION_INTERFACE_FIXTURE
            else "OFFICIAL_RESEARCH_REPORT"
        ),
    )
    report_resolver = OfficialEvidenceResolver(repository_root=repository)
    expected_report = report_resolver.build_report(locator)
    report_path = repository / "research/reports" / f"{value.trial_id}.json"
    markdown_path = repository / "research/reports" / f"{value.trial_id}.md"
    if report_path.exists() or markdown_path.exists():
        if not report_path.is_file() or not markdown_path.is_file():
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "terminal report recovery is partial")
        persisted_report = ReportOutput.from_json_bytes(report_path.read_bytes())
        if (
            persisted_report != expected_report
            or markdown_path.read_text(encoding="utf-8") != expected_report.markdown
        ):
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "persisted report is stale or forged")
        report = persisted_report
    else:
        report = report_resolver.write_report(
            locator,
            json_path=report_path,
            markdown_path=markdown_path,
        )
        commits.append(
            _checkpoint(
                repository,
                paths=(report_path, markdown_path),
                message=f"research(owner): recover report for {value.trial_id}",
            ),
        )
    return OwnerWorkflowResult(
        status="PASS",
        trial_id=value.trial_id,
        run_id=value.run_id,
        run_state=terminal.state.value,
        checker_outcome=checker.outcome.value,
        official_seal_outcome="OFFICIAL_SEAL_PASS",
        claim_eligibility=report.claim_evaluation.research_eligibility.value,
        real_profitability_claim=bool(report.json_payload["profitability_claim_is_real"]),
        report_id=report.report_id,
        history_anchor_sha256=anchor.anchor_sha256,
        final_holdout_used=value.partition_role is PartitionRole.FINAL_HOLDOUT,
        replay_result="NOT_APPLICABLE" if replay is None else str(replay["result"]),
        replay_identity="NOT_APPLICABLE" if replay is None else str(replay["replay_identity"]),
        commits=tuple(commits),
    )


def execute_owner_workflow(
    value: OwnerWorkflowInput,
    *,
    repository_root: Path,
) -> OwnerWorkflowResult:
    """Execute a complete checkpointed workflow using public identities only."""

    _require_verified_startup()
    repository = Path(repository_root).resolve(strict=True)
    history = _history(repository)
    recovered = _recover_started(repository, history)
    if recovered:
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
            "interrupted trials were terminalized; submit a new trial_id/run_id: "
            + ",".join(recovered),
        )
    history.anchors.reconcile_committed()
    clean, unexpected = worktree_is_clean(repository)
    if not clean:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "Official workflow requires clean Git: " + ",".join(unexpected))
    resumed = _resume_completed_workflow(
        value,
        repository=repository,
        history=history,
    )
    if resumed is not None:
        return resumed
    protocol = _refreeze_protocol(value.protocol)
    if protocol.frozen_at_utc > datetime.now(UTC):
        raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "protocol freeze timestamp is in the future")
    _validate_candidate_order(history, value)
    source_before = capture_actual_source_revision(repository)
    provisional_request = build_official_request(
        value,
        repository_root=repository,
        source_revision=source_before,
    )
    definition = _definition(value, provisional_request.lab_run_config)
    if value.partition_role is PartitionRole.FINAL_HOLDOUT:
        AuthoritativeExposureResolver(repository_root=repository).require_fresh(
            _prospective_exposure(
                value,
                definition,
                source_revision=source_before,
                at_utc=datetime.now(UTC),
            ),
            history=history,
        )

    protocol_path = repository / "research/protocols" / f"{protocol.protocol_id}.json"
    workflow_path = repository / "research/workflows" / f"{value.trial_id}.json"
    if protocol_path.exists() and protocol_path.read_bytes().strip() != protocol.to_json_bytes():
        raise ResearchError(FailureCode.RESEARCH_PROTOCOL_INVALID, "protocol identity path collision")
    if workflow_path.exists():
        raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "retry requires a new trial_id")
    _atomic_write(protocol_path, protocol.to_json_bytes() + b"\n")
    _atomic_write(workflow_path, value.to_json_bytes() + b"\n")
    commits: list[str] = [
        _checkpoint(
            repository,
            paths=(protocol_path, workflow_path),
            message=f"research(owner): freeze intent {value.trial_id}",
        ),
    ]
    started_at = datetime.now(UTC)
    history.start_trial(definition, at_utc=started_at)
    commits.append(
        _checkpoint(
            repository,
            paths=(history.anchors.journal_path, history.anchors.anchor_path),
            message=f"research(owner): start {value.trial_id}",
        ),
    )

    command = _official_child_command(repository, workflow_path)
    replay_path: Path | None = None
    replay_evidence: dict[str, Any] | None = None
    run_name = f"{value.run_id}-{definition.config_sha256[:12]}"
    if value.workflow_purpose in {
        OwnerWorkflowPurpose.OWNER_STUDY,
        OwnerWorkflowPurpose.BENCHMARK_STUDY,
    }:
        staging = repository / ".owner-runtime" / value.trial_id
        primary_root = staging / "primary"
        replay_root = staging / "replay"
        if staging.exists():
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                "immutable Owner runtime staging collision; recovery review is required",
            )
        primary_command = [*command, "--child-evidence-root", str(primary_root)]
        replay_command = [*command, "--child-evidence-root", str(replay_root)]
        primary_child = subprocess.run(
            primary_command,
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            env=_official_child_environment(),
        )
        replay_child = subprocess.run(
            replay_command,
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            env=_official_child_environment(),
        )
        staged_primary = primary_root / run_name
        staged_replay = replay_root / run_name
        read_only_checker_revalidated = (
            primary_child.returncode == 0 and replay_child.returncode == 0
        )
        for staged_run in (staged_primary, staged_replay):
            if not (staged_run / "component_validation.json").is_file():
                read_only_checker_revalidated = False
                continue
            try:
                _revalidate_component(
                    staged_run,
                    repository=repository,
                    current_head_required=True,
                )
                _require_official_seal(
                    staged_run,
                    repository=repository,
                    current_head_required=True,
                )
            except Exception:
                read_only_checker_revalidated = False
        run_dir = repository / "runs" / run_name
        replay_dir = repository / "runs/replays" / value.trial_id / run_name
        if staged_primary.exists():
            run_dir.parent.mkdir(parents=True, exist_ok=True)
            if run_dir.exists():
                raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "primary Run evidence collision")
            shutil.move(str(staged_primary), str(run_dir))
        if staged_replay.exists():
            replay_dir.parent.mkdir(parents=True, exist_ok=True)
            if replay_dir.exists():
                raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "replay Run evidence collision")
            shutil.move(str(staged_replay), str(replay_dir))
        primary_ref = _relative(repository, run_dir) if run_dir.exists() else "NOT_APPLICABLE"
        replay_ref = _relative(repository, replay_dir) if replay_dir.exists() else "NOT_APPLICABLE"
        primary_status, primary_engine = (
            _load_terminal_run(run_dir)
            if run_dir.exists() and (run_dir / "status.json").is_file()
            else (
                {
                    "state": "ABORTED",
                    "component_validation_outcome": "COMPONENT_CHECK_BLOCKED",
                },
                {
                    "config_sha256": definition.config_sha256,
                    "semantic_digest": "UNAVAILABLE",
                },
            )
        )
        replay_status, replay_engine = (
            _load_terminal_run(replay_dir)
            if replay_dir.exists() and (replay_dir / "status.json").is_file()
            else (
                {
                    "state": "ABORTED",
                    "component_validation_outcome": "COMPONENT_CHECK_BLOCKED",
                },
                {
                    "config_sha256": definition.config_sha256,
                    "semantic_digest": "UNAVAILABLE",
                },
            )
        )
        replay_material = _replay_material(
            trial_id=value.trial_id,
            primary_ref=primary_ref,
            replay_ref=replay_ref,
            primary_status=primary_status,
            replay_status=replay_status,
            primary_result=primary_engine,
            replay_result=replay_engine,
            read_only_checker_revalidated=read_only_checker_revalidated,
        )
        replay_evidence = {
            **replay_material,
            "primary_child_returncode": primary_child.returncode,
            "replay_child_returncode": replay_child.returncode,
            "primary_child_diagnostic": (
                primary_child.stderr.strip() or primary_child.stdout.strip() or "NOT_APPLICABLE"
            )[:500],
            "replay_child_diagnostic": (
                replay_child.stderr.strip() or replay_child.stdout.strip() or "NOT_APPLICABLE"
            )[:500],
        }
        replay_evidence["replay_identity"] = canonical_sha256(replay_evidence)
        replay_path = repository / "research/replays" / f"{value.trial_id}.json"
        _atomic_write(replay_path, canonical_json_bytes(replay_evidence) + b"\n")
        child = primary_child
    else:
        child = subprocess.run(
            command,
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            env=_official_child_environment(),
        )
        run_dir = repository / "runs" / run_name
    status_path = run_dir / "status.json"
    if status_path.is_file():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        try:
            terminal_state = TrialState(str(status["state"]))
        except Exception as exc:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "invalid persisted terminal Run state") from exc
        if terminal_state not in TERMINAL_TRIAL_STATES:
            terminal_state = TrialState.ABORTED
        reason = ";".join(str(code) for code in status.get("failure_codes", ())) or "OFFICIAL_RUN_COMPLETED"
        if replay_evidence is not None and replay_evidence["result"] != "PASS":
            terminal_state = TrialState.FAILED
            reason = "DETERMINISTIC_REPLAY_MISMATCH_OR_FAILURE"
        result_ref = _relative(repository, run_dir)
        exposed = True
    else:
        terminal_state = TrialState.ABORTED
        reason = (
            "OFFICIAL_CHILD_DID_NOT_PERSIST_TERMINAL_STATUS:"
            + (child.stderr.strip() or child.stdout.strip() or f"exit={child.returncode}")[:500]
        )
        result_ref = _relative(repository, run_dir) if run_dir.exists() else "NOT_APPLICABLE"
        exposed = run_dir.exists() and any(run_dir.iterdir())
    history.finish_trial(
        value.trial_id,
        state=terminal_state,
        at_utc=datetime.now(UTC),
        result_ref=result_ref,
        reason=reason,
        result_exposed=exposed,
    )
    terminal_paths = [history.anchors.journal_path, history.anchors.anchor_path]
    if run_dir.exists():
        terminal_paths.append(run_dir)
    if replay_path is not None:
        terminal_paths.append(replay_path)
        replay_ref = str(replay_evidence["replay_run_ref"])
        if replay_ref != "NOT_APPLICABLE":
            terminal_paths.append(repository / replay_ref)
    commits.append(
        _checkpoint(
            repository,
            paths=tuple(terminal_paths),
            message=f"research(owner): retain terminal evidence for {value.trial_id}",
        ),
    )
    if terminal_state is not TrialState.COMPLETED:
        raise ResearchError(FailureCode.DOWNSTREAM_CONTRACT_FAILURE,
            f"Official Run terminal state is {terminal_state.value}; evidence was retained",
        )
    replay = (
        _read_replay_evidence(repository, value.trial_id)
        if value.workflow_purpose in {
            OwnerWorkflowPurpose.OWNER_STUDY,
            OwnerWorkflowPurpose.BENCHMARK_STUDY,
        }
        else None
    )

    if value.partition_role is PartitionRole.FINAL_HOLDOUT:
        source = type(source_before).from_json_bytes((run_dir / "source_revision.json").read_bytes())
        terminal_record = {
            record.trial_id: record for record in history.journal.read_records()
        }[value.trial_id]
        exposure = _prospective_exposure(
            value,
            definition,
            source_revision=source,
            at_utc=terminal_record.finished_at_utc,
        )
        history.consume_holdout(
            exposure,
            exposure_resolver=AuthoritativeExposureResolver(repository_root=repository),
            at_utc=terminal_record.finished_at_utc,
        )
        commits.append(
            _checkpoint(
                repository,
                paths=(history.anchors.holdout_path, history.anchors.anchor_path),
                message=f"research(owner): consume Holdout for {value.trial_id}",
            ),
        )

    checker = _revalidate_component(
        run_dir,
        repository=repository,
        current_head_required=False,
    )
    _require_official_seal(
        run_dir,
        repository=repository,
        current_head_required=False,
    )
    diagnostic, diagnostic_paths = _write_research_diagnostics(
        value,
        repository=repository,
        run_dir=run_dir,
        result_ref=result_ref,
    )
    commits.append(
        _checkpoint(
            repository,
            paths=tuple(diagnostic_paths),
            message=f"research(owner): derive diagnostics for {value.trial_id}",
        ),
    )

    anchor = history.anchors.reconcile_committed()
    locator = OfficialEvidenceLocator(
        schema_version=1,
        protocol_id=protocol.protocol_id,
        selected_trial_id=value.trial_id,
        expected_history_anchor_sha256=anchor.anchor_sha256,
        report_purpose=(
            "QUALIFICATION_WORKFLOW_FIXTURE"
            if value.workflow_purpose is OwnerWorkflowPurpose.QUALIFICATION_INTERFACE_FIXTURE
            else "OFFICIAL_RESEARCH_REPORT"
        ),
    )
    resolver = OfficialEvidenceResolver(repository_root=repository)
    report_path = repository / "research/reports" / f"{value.trial_id}.json"
    markdown_path = repository / "research/reports" / f"{value.trial_id}.md"
    report = resolver.write_report(
        locator,
        json_path=report_path,
        markdown_path=markdown_path,
    )
    commits.append(
        _checkpoint(
            repository,
            paths=(report_path, markdown_path),
            message=f"research(owner): publish authoritative report for {value.trial_id}",
        ),
    )
    return OwnerWorkflowResult(
        status="PASS",
        trial_id=value.trial_id,
        run_id=value.run_id,
        run_state=terminal_state.value,
        checker_outcome=checker.outcome.value,
        official_seal_outcome="OFFICIAL_SEAL_PASS",
        claim_eligibility=report.claim_evaluation.research_eligibility.value,
        real_profitability_claim=bool(report.json_payload["profitability_claim_is_real"]),
        report_id=report.report_id,
        history_anchor_sha256=anchor.anchor_sha256,
        final_holdout_used=value.partition_role is PartitionRole.FINAL_HOLDOUT,
        replay_result="NOT_APPLICABLE" if replay is None else str(replay["result"]),
        replay_identity="NOT_APPLICABLE" if replay is None else str(replay["replay_identity"]),
        commits=tuple(commits),
    )


def main(argv: list[str] | None = None) -> int:
    _require_verified_startup()
    parser = argparse.ArgumentParser(description="Run one strict checkpointed Owner workflow")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--child-evidence-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    repository = args.repository.resolve(strict=True)
    try:
        if args.child:
            return _child_run(
                args.input,
                repository,
                evidence_root=args.child_evidence_root,
            )
        value = OwnerWorkflowInput.from_json_bytes(args.input.read_bytes())
        result = execute_owner_workflow(value, repository_root=repository).to_builtins()
        exit_code = 0
    except Exception as exc:
        result = {
            "status": "BLOCKED",
            "error_type": type(exc).__name__,
            "detail": str(exc),
        }
        exit_code = 2
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        _atomic_write(args.output, payload.encode("utf-8"))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "OwnerWorkflowInput",
    "OwnerWorkflowPurpose",
    "OwnerWorkflowResult",
    "build_official_request",
    "execute_owner_workflow",
    "qualification_workflow_fixture_input",
]
