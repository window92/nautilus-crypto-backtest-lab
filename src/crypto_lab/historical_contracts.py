"""Fail-closed identities for legacy contracts and executable validators.

Version 1 snapshots are retained only as historical input-integrity records.
They never qualify execution because they do not bind validator or wrapper
bytes. Version 2 authorities bind a Git tree and executable closure.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from crypto_lab.git_identity import require_repository_root
from crypto_lab.status import FailureCode


_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_MODE = frozenset({"100644", "100755"})
_V1_SCHEMA = "historical-contract-snapshots-v1"
_V2_SCHEMA = "historical-validator-authorities-v2"


class HistoricalValidationState(StrEnum):
    EVIDENCE_CORRUPT = "EVIDENCE_CORRUPT"
    LEGACY_CONTRACT_ONLY = "LEGACY_CONTRACT_ONLY"
    HISTORICAL_EXECUTABLE_SNAPSHOT_VALID = "HISTORICAL_EXECUTABLE_SNAPSHOT_VALID"
    HISTORICAL_EXECUTABLE_UNAVAILABLE = "HISTORICAL_EXECUTABLE_UNAVAILABLE"
    # Parse-compatible vocabulary for already-persisted v1 evidence only.
    HISTORICAL_SNAPSHOT_VALID = "HISTORICAL_SNAPSHOT_VALID"
    CURRENT_ROOT_DIFFERS_VALIDLY = "CURRENT_ROOT_DIFFERS_VALIDLY"


class HistoricalAuthorityError(RuntimeError):
    """Structured identity failure before a historical validator executes."""

    code = FailureCode.HISTORICAL_VALIDATOR_IDENTITY_MISMATCH.value

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{self.code}:{reason}:{detail}")


@dataclass(frozen=True)
class HistoricalContractValidation:
    """Legacy v1 input snapshot validation; never executable authority."""

    snapshot_id: str
    git_commit: str
    state: HistoricalValidationState
    snapshot_is_ancestor: bool
    snapshot_files_match: bool
    current_root_matches_snapshot: bool
    files: dict[str, dict[str, Any]]

    @property
    def acceptable(self) -> bool:
        return False

    @property
    def legacy_snapshot_integrity_valid(self) -> bool:
        return bool(self.snapshot_is_ancestor and self.snapshot_files_match)

    def to_builtins(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "git_commit": self.git_commit,
            "classification": self.state.value,
            "snapshot_is_ancestor": self.snapshot_is_ancestor,
            "snapshot_files_match": self.snapshot_files_match,
            "current_root_matches_snapshot": self.current_root_matches_snapshot,
            "legacy_snapshot_integrity_valid": self.legacy_snapshot_integrity_valid,
            "acceptable": False,
            "executable_validator_bound": False,
            "files": self.files,
            "historical_evidence_bytes_mutated": False,
        }


@dataclass(frozen=True)
class HistoricalValidatorAuthority:
    authority_id: str
    validator_name: str
    source_commit: str
    source_tree: str
    entrypoint: dict[str, Any]
    wrapper: dict[str, Any]
    executable_closure: tuple[dict[str, Any], ...]
    arguments: tuple[str, ...]
    external_bindings: tuple[dict[str, Any], ...]
    interpreter_profile: str
    expected_exit_code: int
    expected_status: str
    expected_stdout_sha256: str
    expected_stderr_sha256: str
    bundle_identity: str

    def identity_material(self) -> dict[str, Any]:
        return {
            "authority_id": self.authority_id,
            "validator_name": self.validator_name,
            "source_commit": self.source_commit,
            "source_tree": self.source_tree,
            "entrypoint": dict(self.entrypoint),
            "wrapper": dict(self.wrapper),
            "executable_closure": [dict(item) for item in self.executable_closure],
            "arguments": list(self.arguments),
            "external_bindings": [dict(item) for item in self.external_bindings],
            "interpreter_profile": self.interpreter_profile,
            "expected_exit_code": self.expected_exit_code,
            "expected_status": self.expected_status,
            "expected_stdout_sha256": self.expected_stdout_sha256,
            "expected_stderr_sha256": self.expected_stderr_sha256,
        }

    def to_builtins(self) -> dict[str, Any]:
        return {**self.identity_material(), "bundle_identity": self.bundle_identity}


@dataclass(frozen=True)
class HistoricalExecutableValidation:
    authority: HistoricalValidatorAuthority
    state: HistoricalValidationState
    source_commit_is_ancestor: bool
    source_tree_matches: bool
    closure_matches: bool
    verified_files: tuple[dict[str, Any], ...]

    @property
    def acceptable(self) -> bool:
        return self.state is HistoricalValidationState.HISTORICAL_EXECUTABLE_SNAPSHOT_VALID

    def to_builtins(self) -> dict[str, Any]:
        return {
            "authority": self.authority.to_builtins(),
            "classification": self.state.value,
            "source_commit_is_ancestor": self.source_commit_is_ancestor,
            "source_tree_matches": self.source_tree_matches,
            "closure_matches": self.closure_matches,
            "verified_files": [dict(item) for item in self.verified_files],
            "acceptable": self.acceptable,
            "current_root_validator_executed": False,
        }


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _git(repository_root: Path, *arguments: str, text: bool = False) -> Any:
    return subprocess.run(
        ["git", "--no-replace-objects", *arguments],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=text,
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        },
    ).stdout


def _load_json(path: Path) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise HistoricalAuthorityError(
                    "AUTHORITY_SCHEMA_INVALID",
                    f"duplicate JSON key {key!r}",
                )
            value[key] = item
        return value

    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_keys,
    )
    if not isinstance(value, dict):
        raise ValueError("historical authority document must be an object")
    return value


def _load_v1_manifest(path: Path) -> dict[str, Any]:
    value = _load_json(path)
    if value.get("schema") != _V1_SCHEMA:
        raise ValueError("historical contract manifest schema is invalid")
    return value


def snapshot_for_validator(
    validator_name: str,
    *,
    repository_root: Path,
    manifest_path: Path | None = None,
) -> str:
    repository_root = require_repository_root(repository_root)
    manifest = _load_v1_manifest(
        manifest_path
        or repository_root / "contracts/historical-contract-snapshots.json",
    )
    validators = manifest.get("validators")
    if not isinstance(validators, dict) or validator_name not in validators:
        raise ValueError(f"validator {validator_name!r} has no historical snapshot binding")
    snapshot_id = validators[validator_name]
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("historical validator snapshot id is invalid")
    return snapshot_id


def validate_historical_contract(
    snapshot_id: str,
    *,
    repository_root: Path,
    manifest_path: Path | None = None,
) -> HistoricalContractValidation:
    """Validate v1 inputs while refusing to treat them as executable proof."""

    repository_root = require_repository_root(repository_root)
    manifest = _load_v1_manifest(
        manifest_path
        or repository_root / "contracts/historical-contract-snapshots.json",
    )
    snapshots = manifest.get("snapshots")
    if not isinstance(snapshots, dict) or snapshot_id not in snapshots:
        raise ValueError(f"unknown historical contract snapshot {snapshot_id!r}")
    snapshot = snapshots[snapshot_id]
    if not isinstance(snapshot, dict):
        raise ValueError("historical snapshot must be an object")
    commit = snapshot.get("git_commit")
    expected_files = snapshot.get("files")
    if (
        not isinstance(commit, str)
        or _COMMIT.fullmatch(commit) is None
        or not isinstance(expected_files, dict)
        or not expected_files
    ):
        raise ValueError("historical snapshot identity is malformed")
    ancestor = subprocess.run(
        ["git", "--no-replace-objects", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=repository_root,
        check=False,
        capture_output=True,
    ).returncode == 0
    files: dict[str, dict[str, Any]] = {}
    snapshot_matches = True
    current_matches = True
    for relative, expected in sorted(expected_files.items()):
        if (
            not isinstance(relative, str)
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or not isinstance(expected, str)
            or _SHA256.fullmatch(expected) is None
        ):
            raise ValueError("historical snapshot file binding is malformed")
        try:
            historical_bytes = _git(repository_root, "show", f"{commit}:{relative}")
        except subprocess.CalledProcessError:
            historical_sha = None
        else:
            historical_sha = _sha256_bytes(historical_bytes)
        current_path = repository_root / relative
        current_sha = _sha256_bytes(current_path.read_bytes()) if current_path.is_file() else None
        historical_ok = historical_sha == expected
        current_ok = current_sha == expected
        snapshot_matches = snapshot_matches and historical_ok
        current_matches = current_matches and current_ok
        files[relative] = {
            "expected_sha256": expected,
            "historical_git_sha256": historical_sha,
            "current_root_sha256": current_sha,
            "historical_snapshot_match": historical_ok,
            "current_root_match": current_ok,
        }
    state = (
        HistoricalValidationState.LEGACY_CONTRACT_ONLY
        if ancestor and snapshot_matches
        else HistoricalValidationState.EVIDENCE_CORRUPT
    )
    return HistoricalContractValidation(
        snapshot_id=snapshot_id,
        git_commit=commit,
        state=state,
        snapshot_is_ancestor=ancestor,
        snapshot_files_match=snapshot_matches,
        current_root_matches_snapshot=current_matches,
        files=files,
    )


def validate_validator_contract(
    validator_name: str,
    *,
    repository_root: Path,
    manifest_path: Path | None = None,
) -> HistoricalContractValidation:
    return validate_historical_contract(
        snapshot_for_validator(
            validator_name,
            repository_root=repository_root,
            manifest_path=manifest_path,
        ),
        repository_root=repository_root,
        manifest_path=manifest_path,
    )


def _strict_keys(value: object, required: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        observed = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise HistoricalAuthorityError(
            "AUTHORITY_SCHEMA_INVALID",
            f"{label} fields={observed!r}, expected={sorted(required)!r}",
        )
    return value


def _file_identity(value: object, *, label: str) -> dict[str, Any]:
    item = _strict_keys(value, {"mode", "path", "sha256", "size_bytes"}, label=label)
    path = item["path"]
    if (
        not isinstance(path, str)
        or not path
        or Path(path).is_absolute()
        or ".." in Path(path).parts
        or Path(path).as_posix() != path
        or item["mode"] not in _MODE
        or not isinstance(item["sha256"], str)
        or _SHA256.fullmatch(item["sha256"]) is None
        or type(item["size_bytes"]) is not int
        or item["size_bytes"] < 0
    ):
        raise HistoricalAuthorityError("AUTHORITY_SCHEMA_INVALID", f"invalid {label}")
    return dict(item)


def _external_binding(value: object) -> dict[str, Any]:
    item = _strict_keys(
        value,
        {"kind", "locator", "sha256", "size_bytes", "target"},
        label="external binding",
    )
    locator = item["locator"]
    target = item["target"]
    if (
        item["kind"] != "FILE"
        or not isinstance(locator, str)
        or not locator
        or Path(locator).is_absolute()
        or ".." in Path(locator).parts
        or Path(locator).as_posix() != locator
        or not isinstance(target, str)
        or not target
        or Path(target).is_absolute()
        or ".." in Path(target).parts
        or Path(target).as_posix() != target
        or not isinstance(item["sha256"], str)
        or _SHA256.fullmatch(item["sha256"]) is None
        or type(item["size_bytes"]) is not int
        or item["size_bytes"] < 0
    ):
        raise HistoricalAuthorityError("AUTHORITY_SCHEMA_INVALID", "invalid external binding")
    return dict(item)


def _parse_authority(name: str, value: object) -> HistoricalValidatorAuthority:
    item = _strict_keys(
        value,
        {
            "authority_id",
            "arguments",
            "bundle_identity",
            "entrypoint",
            "executable_closure",
            "expected_exit_code",
            "expected_stderr_sha256",
            "expected_status",
            "expected_stdout_sha256",
            "external_bindings",
            "interpreter_profile",
            "source_commit",
            "source_tree",
            "validator_name",
            "wrapper",
        },
        label=f"authority {name}",
    )
    closure_raw = item["executable_closure"]
    if not isinstance(closure_raw, list) or not closure_raw:
        raise HistoricalAuthorityError("AUTHORITY_SCHEMA_INVALID", "executable closure is empty")
    closure = tuple(_file_identity(entry, label="executable closure file") for entry in closure_raw)
    if len({entry["path"] for entry in closure}) != len(closure):
        raise HistoricalAuthorityError("AUTHORITY_SCHEMA_INVALID", "duplicate closure path")
    entrypoint = _file_identity(item["entrypoint"], label="entrypoint")
    wrapper = _file_identity(item["wrapper"], label="wrapper")
    closure_by_path = {entry["path"]: entry for entry in closure}
    if closure_by_path.get(entrypoint["path"]) != entrypoint:
        raise HistoricalAuthorityError(
            "AUTHORITY_SCHEMA_INVALID",
            "entrypoint not exactly in closure",
        )
    if closure_by_path.get(wrapper["path"]) != wrapper:
        raise HistoricalAuthorityError("AUTHORITY_SCHEMA_INVALID", "wrapper not exactly in closure")
    external_raw = item["external_bindings"]
    arguments = item["arguments"]
    if not isinstance(external_raw, list):
        raise HistoricalAuthorityError("AUTHORITY_SCHEMA_INVALID", "external bindings are invalid")
    external = tuple(
        sorted(
            (_external_binding(entry) for entry in external_raw),
            key=lambda row: row["target"],
        ),
    )
    if len({entry["target"] for entry in external}) != len(external):
        raise HistoricalAuthorityError(
            "AUTHORITY_SCHEMA_INVALID",
            "duplicate external binding target",
        )
    if not isinstance(arguments, list) or not all(
        isinstance(argument, str) and "\0" not in argument for argument in arguments
    ):
        raise HistoricalAuthorityError(
            "AUTHORITY_SCHEMA_INVALID",
            "validator arguments are invalid",
        )
    if (
        item["validator_name"] != name
        or not isinstance(item["authority_id"], str)
        or not item["authority_id"]
        or not isinstance(item["source_commit"], str)
        or _COMMIT.fullmatch(item["source_commit"]) is None
        or not isinstance(item["source_tree"], str)
        or _COMMIT.fullmatch(item["source_tree"]) is None
        or not isinstance(item["interpreter_profile"], str)
        or not item["interpreter_profile"]
        or type(item["expected_exit_code"]) is not int
        or item["expected_exit_code"] < 0
        or item["expected_exit_code"] > 255
        or item["expected_status"] not in {"PASS", "FAIL"}
        or not isinstance(item["expected_stdout_sha256"], str)
        or _SHA256.fullmatch(item["expected_stdout_sha256"]) is None
        or not isinstance(item["expected_stderr_sha256"], str)
        or _SHA256.fullmatch(item["expected_stderr_sha256"]) is None
        or not isinstance(item["bundle_identity"], str)
        or _SHA256.fullmatch(item["bundle_identity"]) is None
    ):
        raise HistoricalAuthorityError("AUTHORITY_SCHEMA_INVALID", f"invalid authority {name}")
    authority = HistoricalValidatorAuthority(
        authority_id=item["authority_id"],
        validator_name=name,
        source_commit=item["source_commit"],
        source_tree=item["source_tree"],
        entrypoint=entrypoint,
        wrapper=wrapper,
        executable_closure=tuple(sorted(closure, key=lambda entry: entry["path"])),
        arguments=tuple(arguments),
        external_bindings=external,
        interpreter_profile=item["interpreter_profile"],
        expected_exit_code=item["expected_exit_code"],
        expected_status=item["expected_status"],
        expected_stdout_sha256=item["expected_stdout_sha256"],
        expected_stderr_sha256=item["expected_stderr_sha256"],
        bundle_identity=item["bundle_identity"],
    )
    if _canonical_sha256(authority.identity_material()) != authority.bundle_identity:
        raise HistoricalAuthorityError("BUNDLE_IDENTITY_MISMATCH", name)
    return authority


def load_historical_authority_manifest(path: Path) -> dict[str, Any]:
    value = _strict_keys(
        _load_json(path),
        {"authorities", "execution_plan", "runtime_profiles", "schema"},
        label="historical authority manifest",
    )
    if value["schema"] != _V2_SCHEMA:
        raise HistoricalAuthorityError("AUTHORITY_SCHEMA_INVALID", "v2 schema required")
    plan = value["execution_plan"]
    authorities_raw = value["authorities"]
    profiles = value["runtime_profiles"]
    if (
        not isinstance(plan, list)
        or not all(isinstance(name, str) and name for name in plan)
        or len(plan) != len(set(plan))
        or not isinstance(authorities_raw, dict)
        or set(plan) != set(authorities_raw)
        or not isinstance(profiles, dict)
        or not profiles
    ):
        raise HistoricalAuthorityError(
            "EXECUTION_PLAN_MISMATCH",
            "plan must enumerate every authority exactly once",
        )
    authorities = {name: _parse_authority(name, authorities_raw[name]) for name in plan}
    used_profiles = {authority.interpreter_profile for authority in authorities.values()}
    if used_profiles != set(profiles) or not all(
        isinstance(profiles[name], dict) for name in used_profiles
    ):
        raise HistoricalAuthorityError(
            "AUTHORITY_SCHEMA_INVALID",
            "runtime profiles do not exactly match the execution plan",
        )
    return {
        "schema": _V2_SCHEMA,
        "execution_plan": tuple(plan),
        "authorities": authorities,
        "runtime_profiles": profiles,
    }


def validate_historical_validator_authority(
    authority: HistoricalValidatorAuthority,
    *,
    repository_root: Path,
) -> HistoricalExecutableValidation:
    try:
        repository = require_repository_root(repository_root)
    except (TypeError, ValueError) as exc:
        raise HistoricalAuthorityError(
            "EXECUTABLE_CLOSURE_MISMATCH",
            f"repository authority is invalid: {exc}",
        ) from exc
    ancestor = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "merge-base",
            "--is-ancestor",
            authority.source_commit,
            "HEAD",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
    ).returncode == 0
    try:
        tree = _git(
            repository,
            "rev-parse",
            f"{authority.source_commit}^{{tree}}",
            text=True,
        ).strip()
    except subprocess.CalledProcessError:
        tree = ""
    tree_matches = tree == authority.source_tree
    verified: list[dict[str, Any]] = []
    closure_matches = True
    for item in authority.executable_closure:
        try:
            payload = _git(repository, "show", f"{authority.source_commit}:{item['path']}")
            listing = _git(
                repository,
                "ls-tree",
                authority.source_commit,
                "--",
                item["path"],
                text=True,
            ).strip()
            mode = listing.split(maxsplit=1)[0] if listing else None
        except subprocess.CalledProcessError:
            payload = b""
            mode = None
        actual = {
            "path": item["path"],
            "mode": mode,
            "sha256": _sha256_bytes(payload) if mode is not None else None,
            "size_bytes": len(payload) if mode is not None else None,
        }
        matches = actual == item
        closure_matches = closure_matches and matches
        verified.append({**actual, "match": matches})
    state = (
        HistoricalValidationState.HISTORICAL_EXECUTABLE_SNAPSHOT_VALID
        if ancestor and tree_matches and closure_matches
        else HistoricalValidationState.EVIDENCE_CORRUPT
    )
    return HistoricalExecutableValidation(
        authority=authority,
        state=state,
        source_commit_is_ancestor=ancestor,
        source_tree_matches=tree_matches,
        closure_matches=closure_matches,
        verified_files=tuple(verified),
    )


__all__ = [
    "HistoricalAuthorityError",
    "HistoricalContractValidation",
    "HistoricalExecutableValidation",
    "HistoricalValidationState",
    "HistoricalValidatorAuthority",
    "load_historical_authority_manifest",
    "snapshot_for_validator",
    "validate_historical_contract",
    "validate_historical_validator_authority",
    "validate_validator_contract",
]
