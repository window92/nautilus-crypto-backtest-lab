"""Execute historical validators from their pinned Git/runtime closure only."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crypto_lab.historical_contracts import HistoricalAuthorityError
from crypto_lab.historical_contracts import HistoricalValidatorAuthority
from crypto_lab.historical_contracts import validate_historical_validator_authority


_SAFE = re.compile(r"[A-Za-z0-9_.-]+")
_CHILD_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "TZ": "UTC",
}
_GIT_ENVIRONMENT = {
    **_CHILD_ENVIRONMENT,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
}


@dataclass(frozen=True)
class HistoricalExecutionResult:
    validator_name: str
    authority_id: str
    bundle_identity: str
    source_commit: str
    bootstrap_authority_sha256: str
    exit_code: int
    validator_status: str | None
    stdout_sha256: str
    stderr: str
    passed: bool

    def to_builtins(self) -> dict[str, Any]:
        return {
            "validator_name": self.validator_name,
            "authority_id": self.authority_id,
            "bundle_identity": self.bundle_identity,
            "source_commit": self.source_commit,
            "bootstrap_authority_sha256": self.bootstrap_authority_sha256,
            "exit_code": self.exit_code,
            "validator_status": self.validator_status,
            "stdout_sha256": self.stdout_sha256,
            "stderr": self.stderr,
            "pass": self.passed,
            "current_root_validator_executed": False,
        }


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repository: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", "--no-replace-objects", *arguments],
        cwd=repository,
        env=_GIT_ENVIRONMENT,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip() or "Git command failed"
        raise HistoricalAuthorityError("SNAPSHOT_MATERIALIZATION_FAILED", detail)
    return process


def _materialize_snapshot(
    source: Path,
    destination: Path,
    authority: HistoricalValidatorAuthority,
) -> None:
    clone = subprocess.run(
        [
            "git",
            "clone",
            "--no-local",
            "--no-hardlinks",
            "--no-checkout",
            str(source),
            str(destination),
        ],
        env=_GIT_ENVIRONMENT,
        check=False,
        capture_output=True,
        text=True,
    )
    if clone.returncode != 0:
        raise HistoricalAuthorityError(
            "SNAPSHOT_MATERIALIZATION_FAILED",
            clone.stderr.strip() or clone.stdout.strip(),
        )
    branch = f"historical-{authority.authority_id}"
    if _SAFE.fullmatch(branch) is None:
        branch = "historical-pinned-validator"
    _git(destination, "switch", "-c", branch, authority.source_commit)
    origin = _git(source, "remote", "get-url", "origin").stdout.strip()
    _git(destination, "remote", "set-url", "origin", origin)
    if _git(destination, "rev-parse", "HEAD").stdout.strip() != authority.source_commit:
        raise HistoricalAuthorityError("SNAPSHOT_MATERIALIZATION_FAILED", "snapshot HEAD differs")
    if _git(destination, "status", "--porcelain=v1", "--untracked-files=all").stdout:
        raise HistoricalAuthorityError("SNAPSHOT_MATERIALIZATION_FAILED", "snapshot is not clean")


def _stage_external_bindings(
    snapshot: Path,
    repository: Path,
    authority: HistoricalValidatorAuthority,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Create an exact per-file repository view without copying old evidence.

    Every view entry is a symlink to one content-addressed regular file in the
    original repository.  Executable namespaces and tracked historical paths
    are forbidden.  The isolated bootstrap verifies this view independently
    before importing Product code, and this module verifies it again after the
    validator exits.
    """

    substitutions: dict[str, str] = {}
    external_files: list[dict[str, Any]] = []
    for binding in authority.external_bindings:
        required = {"kind", "locator", "sha256", "size_bytes", "target"}
        if set(binding) != required or binding.get("kind") != "FILE":
            raise HistoricalAuthorityError(
                "EXTERNAL_BINDING_UNSUPPORTED",
                "only content-addressed FILE bindings are currently permitted",
            )
        source = repository / str(binding["locator"])
        target_relative = Path(str(binding["target"]))
        first = target_relative.parts[0] if target_relative.parts else ""
        if (
            source.is_symlink()
            or not source.is_file()
            or source.resolve(strict=True) != source
            or target_relative.is_absolute()
            or ".." in target_relative.parts
            or first in {".git", "scripts", "src"}
        ):
            raise HistoricalAuthorityError("EXTERNAL_BINDING_MISMATCH", str(binding))
        if source.stat().st_size != binding["size_bytes"]:
            raise HistoricalAuthorityError("EXTERNAL_BINDING_MISMATCH", str(source))
        if _sha256_file(source) != binding["sha256"]:
            raise HistoricalAuthorityError("EXTERNAL_BINDING_MISMATCH", str(source))
        target = snapshot / target_relative
        lexical_parent = snapshot
        for part in target_relative.parts[:-1]:
            lexical_parent = lexical_parent / part
            if lexical_parent.is_symlink():
                raise HistoricalAuthorityError(
                    "EXTERNAL_BINDING_MISMATCH",
                    f"view parent is a symlink: {lexical_parent}",
                )
            if lexical_parent.exists():
                if not lexical_parent.is_dir():
                    raise HistoricalAuthorityError(
                        "EXTERNAL_BINDING_MISMATCH",
                        f"view parent is not a directory: {lexical_parent}",
                    )
            else:
                lexical_parent.mkdir()
        if target.exists() or target.is_symlink():
            raise HistoricalAuthorityError("EXTERNAL_BINDING_MISMATCH", f"target exists: {target}")
        tracked = _git(
            snapshot,
            "ls-files",
            "--error-unmatch",
            "--",
            target_relative.as_posix(),
            check=False,
        )
        if tracked.returncode == 0:
            raise HistoricalAuthorityError(
                "EXTERNAL_BINDING_MISMATCH",
                f"view target is tracked by historical Git: {target_relative}",
            )
        os.symlink(str(source), target)
        token = "{binding:" + binding["target"] + "}"
        substitutions[token] = str(target)
        external_files.append(
            {
                "source": str(source),
                "target": binding["target"],
                "sha256": binding["sha256"],
                "size_bytes": binding["size_bytes"],
            },
        )
    _verify_external_bindings(snapshot, repository, authority)
    return substitutions, external_files


def _verify_external_bindings(
    snapshot: Path,
    repository: Path,
    authority: HistoricalValidatorAuthority,
) -> None:
    """Re-hash every source and require the exact staged symlink view."""

    for binding in authority.external_bindings:
        source = repository / str(binding["locator"])
        target = snapshot / str(binding["target"])
        if (
            source.is_symlink()
            or not source.is_file()
            or source.resolve(strict=True) != source
            or source.stat().st_size != binding["size_bytes"]
            or _sha256_file(source) != binding["sha256"]
            or not target.is_symlink()
            or os.readlink(target) != str(source)
        ):
            raise HistoricalAuthorityError(
                "EXTERNAL_BINDING_MISMATCH",
                str(binding["target"]),
            )


def _verify_tracked_snapshot_unchanged(snapshot: Path) -> None:
    tracked = _git(snapshot, "diff", "--name-only", "HEAD", "--").stdout.strip()
    staged = _git(snapshot, "diff", "--cached", "--name-only", "--").stdout.strip()
    if tracked or staged:
        raise HistoricalAuthorityError(
            "EXECUTABLE_CLOSURE_MISMATCH",
            f"validator modified tracked snapshot bytes: tracked={tracked!r}, staged={staged!r}",
        )


def _bound_arguments(
    authority: HistoricalValidatorAuthority,
    *,
    repository: Path,
    substitutions: dict[str, str],
) -> tuple[str, ...]:
    result: list[str] = []
    for argument in authority.arguments:
        if argument == "{repository}":
            result.append(str(repository))
        elif argument.startswith("{repository}/"):
            relative = argument[len("{repository}/") :]
            path = Path(relative)
            if (
                not relative
                or path.is_absolute()
                or ".." in path.parts
                or path.as_posix() != relative
            ):
                raise HistoricalAuthorityError(
                    "EXTERNAL_BINDING_MISMATCH",
                    f"unsafe repository-view argument {argument}",
                )
            result.append(str(repository / path))
        elif argument.startswith("{binding:"):
            if argument not in substitutions:
                raise HistoricalAuthorityError(
                    "EXTERNAL_BINDING_MISMATCH",
                    f"unknown binding argument {argument}",
                )
            result.append(substitutions[argument])
        else:
            result.append(argument)
    return tuple(result)


def _bootstrap_authority(
    authority: HistoricalValidatorAuthority,
    *,
    runtime_profile: dict[str, Any],
    snapshot: Path,
    external_files: list[dict[str, Any]],
) -> dict[str, Any]:
    required = {"bootstrap_sha256", "initial_sys_path", "python", "site_packages"}
    if set(runtime_profile) != required:
        raise HistoricalAuthorityError(
            "RUNTIME_PROFILE_INVALID",
            f"fields={sorted(runtime_profile)}, expected={sorted(required)}",
        )
    origin = _git(snapshot, "remote", "get-url", "origin").stdout.strip()
    return {
        "schema": "isolated-runtime-bootstrap-authority-v1",
        "bootstrap_sha256": runtime_profile["bootstrap_sha256"],
        "initial_sys_path": runtime_profile["initial_sys_path"],
        "python": runtime_profile["python"],
        "site_packages": runtime_profile["site_packages"],
        "product": {
            "repository_identity": origin,
            "source_commit": authority.source_commit,
            "source_tree": authority.source_tree,
            "source_root": "src",
            "package_prefix": "crypto_lab",
            "source_files": [dict(item) for item in authority.executable_closure],
            "external_files": external_files,
        },
        "allowed_targets": {
            "entrypoints": [],
            "scripts": [authority.entrypoint["path"]],
        },
    }


def execute_historical_validator(
    authority: HistoricalValidatorAuthority,
    *,
    repository_root: Path,
    runtime_profile: dict[str, Any],
    bootstrap_path: Path,
    timeout_seconds: int = 300,
) -> HistoricalExecutionResult:
    """Run exact historical bytes in an independent clone and isolated Python."""

    repository_input = Path(repository_root)
    if repository_input.is_symlink():
        raise HistoricalAuthorityError("EXECUTABLE_CLOSURE_MISMATCH", "repository is a symlink")
    repository = repository_input.resolve(strict=True)
    validation = validate_historical_validator_authority(
        authority,
        repository_root=repository,
    )
    if not validation.acceptable:
        raise HistoricalAuthorityError(
            "EXECUTABLE_CLOSURE_MISMATCH",
            json.dumps(validation.to_builtins(), sort_keys=True),
        )
    bootstrap_input = Path(bootstrap_path)
    if bootstrap_input.is_symlink():
        raise HistoricalAuthorityError("RUNTIME_PROFILE_INVALID", "bootstrap is a symlink")
    bootstrap = bootstrap_input.resolve(strict=True)
    if _sha256_file(bootstrap) != runtime_profile.get("bootstrap_sha256"):
        raise HistoricalAuthorityError("RUNTIME_PROFILE_INVALID", "bootstrap identity differs")
    with tempfile.TemporaryDirectory(prefix="crypto-lab-historical-") as temporary:
        root = Path(temporary)
        snapshot = root / "repository"
        _materialize_snapshot(repository, snapshot, authority)
        substitutions, external_files = _stage_external_bindings(
            snapshot,
            repository,
            authority,
        )
        validator_arguments = _bound_arguments(
            authority,
            repository=snapshot,
            substitutions=substitutions,
        )
        bootstrap_authority = _bootstrap_authority(
            authority,
            runtime_profile=runtime_profile,
            snapshot=snapshot,
            external_files=external_files,
        )
        authority_bytes = _canonical_bytes(bootstrap_authority)
        authority_path = root / "bootstrap-authority.json"
        authority_path.write_bytes(authority_bytes)
        interpreter = str(runtime_profile["python"]["venv_executable"])
        command = [
            interpreter,
            "-I",
            "-P",
            "-S",
            "-B",
            "-X",
            "pycache_prefix=/dev/null",
            str(bootstrap),
            "--authority",
            str(authority_path),
            "--repository",
            str(snapshot),
            "--script",
            authority.entrypoint["path"],
            "--",
            *validator_arguments,
        ]
        try:
            try:
                completed = subprocess.run(
                    command,
                    cwd=snapshot,
                    env=_CHILD_ENVIRONMENT,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
            except subprocess.TimeoutExpired as exc:
                raise HistoricalAuthorityError("VALIDATOR_TIMEOUT", str(exc)) from exc
        finally:
            _verify_external_bindings(snapshot, repository, authority)
            _verify_tracked_snapshot_unchanged(snapshot)
        try:
            output = json.loads(completed.stdout)
        except json.JSONDecodeError:
            output = None
        status = output.get("status") if isinstance(output, dict) else None
        passed = bool(
            completed.returncode == authority.expected_exit_code
            and status == authority.expected_status
        )
        return HistoricalExecutionResult(
            validator_name=authority.validator_name,
            authority_id=authority.authority_id,
            bundle_identity=authority.bundle_identity,
            source_commit=authority.source_commit,
            bootstrap_authority_sha256=hashlib.sha256(authority_bytes).hexdigest(),
            exit_code=completed.returncode,
            validator_status=status if isinstance(status, str) else None,
            stdout_sha256=hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
            stderr=completed.stderr,
            passed=passed,
        )


__all__ = ["HistoricalExecutionResult", "execute_historical_validator"]
