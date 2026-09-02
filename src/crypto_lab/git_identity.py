"""Exact Git SourceRevision capture and verification shared by official boundaries."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from crypto_lab.config import SourceRevision


class GitIdentityError(RuntimeError):
    """The supplied or persisted SourceRevision does not match actual Git state."""


@dataclass(frozen=True)
class GitVerification:
    repository_root: Path
    actual: SourceRevision
    frozen_commit_tree_valid: bool
    frozen_commit_on_branch: bool
    allowed_worktree_outputs: tuple[str, ...]


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
        raise GitIdentityError(f"EVIDENCE_INCOMPLETE: {detail}")
    return process.stdout.strip()


def require_repository_root(
    repository_root: Path | None,
    *,
    expected_repository_identity: str | None = None,
    expected_git_commit: str | None = None,
    expected_git_tree: str | None = None,
    require_current_head: bool = False,
) -> Path:
    """Reject any attempt to infer repository authority from location heuristics.

    Official and authority-sensitive callers MUST pass an explicit absolute
    Git product root.  ``None``, a relative path, a missing path, a symlink
    root, a copied ``crypto_lab`` package tree, and a root whose Git
    toplevel or commit does not match the bound authority all fail closed.
    """

    if repository_root is None:
        raise ValueError("repository_root is required")
    if not isinstance(repository_root, Path):
        raise TypeError("repository_root must be pathlib.Path")
    if not repository_root.is_absolute():
        raise ValueError("repository_root must be an absolute path")
    lexical = Path(os.path.abspath(repository_root))
    if lexical != repository_root:
        raise ValueError("repository_root must be an exact normalized absolute path")
    cursor = Path(lexical.anchor)
    for component in lexical.parts[1:]:
        cursor /= component
        if cursor.is_symlink():
            raise ValueError("repository_root must not contain a symlink")
    if not repository_root.exists():
        raise ValueError("repository_root does not exist")
    if not repository_root.is_dir():
        raise ValueError("repository_root must be a directory")
    ssot = repository_root / "SSOT.md"
    if ssot.is_symlink() or not ssot.is_file():
        raise ValueError("repository_root is not the product repository")
    package_copy = repository_root / "sealing.py"
    src_package = repository_root / "src" / "crypto_lab" / "sealing.py"
    if package_copy.is_file() and not src_package.is_file():
        raise ValueError("repository_root is a copied package tree")
    try:
        actual = Path(
            _git(repository_root, "rev-parse", "--show-toplevel"),
        ).resolve(strict=True)
    except GitIdentityError as exc:
        raise ValueError("repository_root is not a Git repository") from exc
    requested = repository_root.resolve(strict=True)
    if actual != requested:
        raise ValueError(
            f"repository_root is not the Git repository root {actual}",
        )
    if expected_repository_identity is not None:
        if not isinstance(expected_repository_identity, str) or not expected_repository_identity:
            raise ValueError("expected_repository_identity must be a non-empty string")
        if _git(requested, "remote", "get-url", "origin") != expected_repository_identity:
            raise ValueError("repository_root origin does not match the expected authority")
    if expected_git_commit is not None:
        if (
            not isinstance(expected_git_commit, str)
            or len(expected_git_commit) != 40
        ):
            raise ValueError("expected_git_commit is not a 40-character SHA")
        try:
            resolved = _git(
                requested,
                "rev-parse",
                "--verify",
                f"{expected_git_commit}^{{commit}}",
            )
        except GitIdentityError as exc:
            raise ValueError(
                "repository_root does not contain the expected commit",
            ) from exc
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", expected_git_commit, "HEAD"],
            cwd=requested,
            check=False,
            capture_output=True,
            text=True,
        )
        if ancestor.returncode != 0:
            raise ValueError(
                "repository_root HEAD does not descend from the expected commit",
            )
        if expected_git_tree is not None:
            if not isinstance(expected_git_tree, str) or len(expected_git_tree) != 40:
                raise ValueError("expected_git_tree is not a 40-character SHA")
            if _git(requested, "rev-parse", f"{expected_git_commit}^{{tree}}") != expected_git_tree:
                raise ValueError("repository_root tree does not match the expected authority")
        if require_current_head:
            head = _git(requested, "rev-parse", "HEAD")
            if head != expected_git_commit or resolved != expected_git_commit:
                raise ValueError(
                    "repository_root HEAD does not match the expected commit",
                )
    elif expected_git_tree is not None:
        raise ValueError("expected_git_tree requires expected_git_commit")
    return requested


def _repository_root(repository: Path) -> Path:
    try:
        return require_repository_root(repository)
    except (TypeError, ValueError) as exc:
        raise GitIdentityError(
            f"EVIDENCE_INCOMPLETE: repository authority is invalid: {exc}",
        ) from exc


def _dirty_paths(repository: Path) -> tuple[str, ...]:
    process = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if process.returncode != 0:
        raise GitIdentityError("EVIDENCE_INCOMPLETE: git status failed")
    # Porcelain's leading XY columns are semantic.  Do not call strip() on the
    # complete output or the first record loses its leading status column.
    output = process.stdout.rstrip("\n")
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            raise GitIdentityError("EVIDENCE_INCOMPLETE: malformed Git status output")
        raw = line[3:]
        if " -> " in raw:
            raw = raw.rsplit(" -> ", maxsplit=1)[1]
        paths.append(raw.strip('"'))
    return tuple(paths)


def _allowed_relative_paths(
    repository: Path,
    allowed_paths: tuple[Path, ...],
) -> tuple[str, ...]:
    result: list[str] = []
    for path in allowed_paths:
        resolved = Path(path).resolve(strict=False)
        try:
            relative = resolved.relative_to(repository)
        except ValueError:
            continue
        result.append(relative.as_posix().rstrip("/") + "/")
    return tuple(result)


def worktree_is_clean(
    repository: Path,
    *,
    allowed_output_paths: tuple[Path, ...] = (),
) -> tuple[bool, tuple[str, ...]]:
    root = _repository_root(repository)
    dirty = _dirty_paths(root)
    allowed = _allowed_relative_paths(root, allowed_output_paths)
    unexpected = tuple(
        path
        for path in dirty
        if not any(path == prefix[:-1] or path.startswith(prefix) for prefix in allowed)
    )
    return not unexpected, unexpected


def capture_actual_source_revision(repository: Path) -> SourceRevision:
    """Capture exact origin URL, symbolic branch, HEAD, tree, and cleanliness."""

    from datetime import UTC
    from datetime import datetime

    root = _repository_root(repository)
    repository_identity = _git(root, "remote", "get-url", "origin")
    if not repository_identity:
        raise GitIdentityError("EVIDENCE_INCOMPLETE: origin repository identity is absent")
    branch_ref = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch_ref or branch_ref == "HEAD":
        raise GitIdentityError("EVIDENCE_INCOMPLETE: detached or ambiguous branch/ref")
    _git(root, "show-ref", "--verify", f"refs/heads/{branch_ref}")
    clean, _unexpected = worktree_is_clean(root)
    return SourceRevision(
        repository=repository_identity,
        branch_ref=branch_ref,
        git_commit=_git(root, "rev-parse", "HEAD"),
        git_tree=_git(root, "rev-parse", "HEAD^{tree}"),
        clean_worktree=clean,
        captured_at_utc=datetime.now(UTC),
    )


def verify_source_revision(
    source: SourceRevision,
    *,
    repository: Path,
    require_current_head: bool,
    require_clean: bool,
    allowed_output_paths: tuple[Path, ...] = (),
) -> GitVerification:
    """Reconcile every SourceRevision field with exact Git facts."""

    root = _repository_root(repository)
    actual = capture_actual_source_revision(root)
    mismatches: list[str] = []
    if source.repository != actual.repository:
        mismatches.append("repository")
    if require_current_head and source.branch_ref != actual.branch_ref:
        mismatches.append("branch_ref")
    resolved_tree = _git(root, "rev-parse", f"{source.git_commit}^{{tree}}")
    tree_valid = resolved_tree == source.git_tree
    if not tree_valid:
        mismatches.append("git_tree")
    lineage_ref = (
        f"refs/heads/{source.branch_ref}"
        if require_current_head
        else "HEAD"
    )
    branch_contains = subprocess.run(
        ["git", "merge-base", "--is-ancestor", source.git_commit, lineage_ref],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    ).returncode == 0
    if not branch_contains:
        mismatches.append(
            "branch_ref_commit_lineage"
            if require_current_head
            else "current_history_commit_lineage"
        )
    if require_current_head:
        if source.git_commit != actual.git_commit:
            mismatches.append("git_commit")
        if source.git_tree != actual.git_tree:
            mismatches.append("current_git_tree")
    clean, unexpected = worktree_is_clean(
        root,
        allowed_output_paths=allowed_output_paths,
    )
    if require_clean and (not source.clean_worktree or not clean):
        mismatches.append("clean_worktree")
    if mismatches:
        detail = ",".join(dict.fromkeys(mismatches))
        if unexpected:
            detail += "; unexpected_paths=" + ",".join(unexpected)
        raise GitIdentityError(f"EVIDENCE_INCOMPLETE: SourceRevision mismatch: {detail}")
    return GitVerification(
        repository_root=root,
        actual=actual,
        frozen_commit_tree_valid=tree_valid,
        frozen_commit_on_branch=branch_contains,
        allowed_worktree_outputs=tuple(str(path) for path in allowed_output_paths),
    )


__all__ = [
    "GitIdentityError",
    "GitVerification",
    "capture_actual_source_revision",
    "require_repository_root",
    "verify_source_revision",
    "worktree_is_clean",
]
