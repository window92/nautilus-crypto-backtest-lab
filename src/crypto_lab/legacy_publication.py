"""Fail-closed classification for pre-v2 report/evidence publishers.

The legacy generators are retained only so their historical bytes and old
workflows remain understandable.  They are not an alternate publication path
for a current Result.  Current publication belongs to
``OfficialEvidenceResolver`` and therefore requires both additive ACTIVE
status and an ``OFFICIAL_SEAL_PASS`` package.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from crypto_lab.git_identity import require_repository_root
from crypto_lab.result_status import ResultStatusResolution
from crypto_lab.result_status import resolve_result_status


LEGACY_HISTORICAL_ONLY_PUBLICATION = "HISTORICAL_ONLY_NON_OFFICIAL"


def require_historical_only_result(
    run_directory: Path,
    *,
    repository_root: Path,
) -> ResultStatusResolution:
    """Accept only an additively classified non-ACTIVE historical Result.

    An ACTIVE Result is deliberately rejected even if it is sealed: legacy
    generators do not implement the current resolver/report contract.  A
    missing, malformed, or incomplete status authority is also a hard error.
    """

    try:
        resolution = resolve_result_status(
            run_directory,
            repository_root=repository_root,
        )
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "legacy historical publication status authority is invalid",
        ) from exc
    if resolution.is_active:
        raise RuntimeError(
            "legacy publisher is HISTORICAL_ONLY_NON_OFFICIAL; ACTIVE/current "
            "Results require OfficialEvidenceResolver with ACTIVE status and "
            "OFFICIAL_SEAL_PASS",
        )
    return resolution


def require_historical_only_replay(
    replay_evidence: dict[str, Any],
    *,
    repository_root: Path,
) -> ResultStatusResolution:
    """Resolve and classify the replay Run referenced by legacy evidence."""

    reference = replay_evidence.get("replay_run_ref")
    if not isinstance(reference, str) or not reference:
        raise RuntimeError("legacy replay evidence has no replay_run_ref")
    root = require_repository_root(repository_root)
    candidate = root / reference
    if candidate.is_symlink():
        raise RuntimeError("legacy replay Run must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise RuntimeError("legacy replay Run escapes or is absent") from exc
    return require_historical_only_result(
        resolved,
        repository_root=root,
    )


__all__ = [
    "LEGACY_HISTORICAL_ONLY_PUBLICATION",
    "require_historical_only_replay",
    "require_historical_only_result",
]
