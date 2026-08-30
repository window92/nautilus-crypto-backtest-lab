"""Read-only binding of historical validators to immutable Git snapshots."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")


class HistoricalValidationState(StrEnum):
    EVIDENCE_CORRUPT = "EVIDENCE_CORRUPT"
    HISTORICAL_SNAPSHOT_VALID = "HISTORICAL_SNAPSHOT_VALID"
    CURRENT_ROOT_DIFFERS_VALIDLY = "CURRENT_ROOT_DIFFERS_VALIDLY"


@dataclass(frozen=True)
class HistoricalContractValidation:
    snapshot_id: str
    git_commit: str
    state: HistoricalValidationState
    snapshot_is_ancestor: bool
    snapshot_files_match: bool
    current_root_matches_snapshot: bool
    files: dict[str, dict[str, Any]]

    @property
    def acceptable(self) -> bool:
        return self.state in {
            HistoricalValidationState.HISTORICAL_SNAPSHOT_VALID,
            HistoricalValidationState.CURRENT_ROOT_DIFFERS_VALIDLY,
        }

    def to_builtins(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "git_commit": self.git_commit,
            "classification": self.state.value,
            "snapshot_is_ancestor": self.snapshot_is_ancestor,
            "snapshot_files_match": self.snapshot_files_match,
            "current_root_matches_snapshot": self.current_root_matches_snapshot,
            "acceptable": self.acceptable,
            "files": self.files,
            "historical_evidence_bytes_mutated": False,
        }


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git(repository_root: Path, *arguments: str, text: bool = False) -> Any:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=text,
    ).stdout


def _load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != "historical-contract-snapshots-v1":
        raise ValueError("historical contract manifest schema is invalid")
    return value


def snapshot_for_validator(
    validator_name: str,
    *,
    repository_root: Path,
    manifest_path: Path | None = None,
) -> str:
    manifest = _load_manifest(
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
    """Verify historical bytes from Git, then classify the current-root delta."""

    repository_root = Path(repository_root).resolve()
    manifest = _load_manifest(
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
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
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
        current_sha = (
            _sha256_bytes(current_path.read_bytes()) if current_path.is_file() else None
        )
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

    if not ancestor or not snapshot_matches:
        state = HistoricalValidationState.EVIDENCE_CORRUPT
    elif current_matches:
        state = HistoricalValidationState.HISTORICAL_SNAPSHOT_VALID
    else:
        state = HistoricalValidationState.CURRENT_ROOT_DIFFERS_VALIDLY
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
    snapshot_id = snapshot_for_validator(
        validator_name,
        repository_root=repository_root,
        manifest_path=manifest_path,
    )
    return validate_historical_contract(
        snapshot_id,
        repository_root=repository_root,
        manifest_path=manifest_path,
    )


__all__ = [
    "HistoricalContractValidation",
    "HistoricalValidationState",
    "snapshot_for_validator",
    "validate_historical_contract",
    "validate_validator_contract",
]
