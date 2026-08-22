"""Safe atomic paths for immutable Run evidence."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path


SAFE_COMPONENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)


class EvidencePathError(ValueError):
    """An evidence path is unsafe, ambiguous, escaping, or colliding."""


def validate_safe_component(value: str, *, field: str = "run_id") -> str:
    if not isinstance(value, str) or not value:
        raise EvidencePathError(f"CONFIG_INVALID: {field} must be a non-empty string")
    if value in {".", ".."} or not SAFE_COMPONENT_PATTERN.fullmatch(value):
        raise EvidencePathError(
            f"CONFIG_INVALID: {field} violates [A-Za-z0-9][A-Za-z0-9_-]{{0,127}}",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise EvidencePathError(f"CONFIG_INVALID: {field} contains a control character")
    if "/" in value or "\\" in value or "\x00" in value:
        raise EvidencePathError(f"CONFIG_INVALID: {field} contains a path separator")
    return value


def atomic_create_run_directory(
    evidence_root: Path,
    *,
    run_id: str,
    config_sha256: str,
    containment_root: Path | None = None,
) -> Path:
    """Validate first, then atomically create one contained non-colliding directory."""

    safe_run_id = validate_safe_component(run_id)
    if not re.fullmatch(r"[0-9a-f]{64}", config_sha256):
        raise EvidencePathError("CONFIG_INVALID: config_sha256 must be lowercase SHA-256")
    # No filesystem write occurs before all caller-controlled components pass.
    # Open/create every evidence-root component with O_NOFOLLOW so a symlink at
    # the root or in one of its parents cannot redirect creation.  Official
    # callers additionally bind the root to their exact Git repository.
    root = Path(evidence_root)
    absolute_root = Path(os.path.abspath(root))
    if containment_root is None:
        boundary = Path(absolute_root.anchor)
        relative_parts = absolute_root.parts[1:]
    else:
        boundary = Path(containment_root).resolve(strict=True)
        if not boundary.is_dir():
            raise EvidencePathError("EVIDENCE_INCOMPLETE: containment root is not a directory")
        try:
            lexical_relative = absolute_root.relative_to(boundary)
        except ValueError as exc:
            raise EvidencePathError(
                "EVIDENCE_INCOMPLETE: evidence root escapes its containment root",
            ) from exc
        relative_parts = lexical_relative.parts

    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    current_fd = os.open(boundary, flags)
    try:
        for component in relative_parts:
            if component in {"", ".", ".."}:
                raise EvidencePathError("EVIDENCE_INCOMPLETE: ambiguous evidence-root component")
            try:
                os.mkdir(component, mode=0o700, dir_fd=current_fd)
                os.fsync(current_fd)
            except FileExistsError:
                pass
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except OSError as exc:
                raise EvidencePathError(
                    "EVIDENCE_INCOMPLETE: evidence root contains a symlink or non-directory",
                ) from exc
            os.close(current_fd)
            current_fd = next_fd

        root_resolved = Path(f"/proc/self/fd/{current_fd}").resolve(strict=True)
        if containment_root is not None:
            try:
                root_resolved.relative_to(boundary)
            except ValueError as exc:
                raise EvidencePathError(
                    "EVIDENCE_INCOMPLETE: resolved evidence root escapes containment",
                ) from exc
        name = f"{safe_run_id}-{config_sha256[:12]}"
        try:
            existing = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            kind = "symlink" if stat.S_ISLNK(existing.st_mode) else "collision"
            raise EvidencePathError(f"EVIDENCE_INCOMPLETE: Run evidence path {kind}")
        try:
            os.mkdir(name, mode=0o700, dir_fd=current_fd)
            os.fsync(current_fd)
        except FileExistsError as exc:
            raise EvidencePathError("EVIDENCE_INCOMPLETE: Run evidence path collision") from exc
    finally:
        os.close(current_fd)

    name = f"{safe_run_id}-{config_sha256[:12]}"
    candidate = root_resolved / name
    candidate_resolved = candidate.resolve(strict=False)
    if candidate_resolved.parent != root_resolved:
        raise EvidencePathError("EVIDENCE_INCOMPLETE: resolved Run path escapes evidence root")
    created = candidate.resolve(strict=True)
    if created.parent != root_resolved or not created.is_dir():
        raise EvidencePathError("EVIDENCE_INCOMPLETE: created Run path failed containment")
    return created


__all__ = [
    "EvidencePathError",
    "SAFE_COMPONENT_PATTERN",
    "atomic_create_run_directory",
    "validate_safe_component",
]
