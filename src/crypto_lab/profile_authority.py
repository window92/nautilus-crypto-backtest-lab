"""Content-addressed Qualified Profile authority for Official Runs."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ProfileAuthorityError(ValueError):
    """A Qualified Profile authority is unsafe, stale, or inconsistent."""


def _require_sha256(value: str, *, field: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ProfileAuthorityError(f"{field} must be lowercase SHA-256")


def _contained_registry(repository_root: Path, registry_ref: str) -> Path:
    root = Path(repository_root).resolve(strict=True)
    if not isinstance(registry_ref, str) or not registry_ref:
        raise ProfileAuthorityError("qualified profile registry reference is unsafe")
    relative = Path(registry_ref)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "\\" in registry_ref
        or "\x00" in registry_ref
    ):
        raise ProfileAuthorityError("qualified profile registry reference is unsafe")
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ProfileAuthorityError("qualified profile registry path contains a symlink")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, ValueError) as exc:
        raise ProfileAuthorityError("qualified profile registry escapes or is missing") from exc
    if not resolved.is_file():
        raise ProfileAuthorityError("qualified profile registry is not a regular file")
    return resolved


def resolve_profile_authority(
    *,
    repository_root: Path,
    registry_ref: str,
    registry_sha256: str,
    qualified_profile_record_id: str,
    expected_profile_id: str,
    expected_runtime_lock_sha256: str,
) -> dict[str, Any]:
    """Resolve one exact Qualified Profile record from one immutable registry."""

    for field, value in (
        ("registry_sha256", registry_sha256),
        ("qualified_profile_record_id", qualified_profile_record_id),
        ("expected_runtime_lock_sha256", expected_runtime_lock_sha256),
    ):
        _require_sha256(value, field=field)
    path = _contained_registry(repository_root, registry_ref)
    if sha256_file(path) != registry_sha256:
        raise ProfileAuthorityError("qualified profile registry SHA-256 mismatch")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProfileAuthorityError("qualified profile registry is unreadable") from exc
    records = document.get("records") if isinstance(document, dict) else None
    if (
        not isinstance(records, list)
        or document.get("schema_version") != 2
        or document.get("registry_content_sha256")
        != canonical_sha256({"schema_version": 2, "records": records})
    ):
        raise ProfileAuthorityError("qualified profile registry identity is invalid")
    matches = [
        item
        for item in records
        if isinstance(item, dict)
        and item.get("qualified_profile_record_id") == qualified_profile_record_id
    ]
    if len(matches) != 1:
        raise ProfileAuthorityError("Qualified Profile record must resolve exactly once")
    record = matches[0]
    material = copy.deepcopy(record)
    material.pop("qualified_profile_record_id", None)
    source = material.get("source_revision")
    if not isinstance(source, dict):
        raise ProfileAuthorityError("Qualified Profile source revision is invalid")
    source.pop("captured_at_utc", None)
    if canonical_sha256(material) != qualified_profile_record_id:
        raise ProfileAuthorityError("Qualified Profile record identity is invalid")
    if (
        record.get("schema_version") != 2
        or record.get("profile_id") != expected_profile_id
        or record.get("runtime_lock_sha256") != expected_runtime_lock_sha256
        or record.get("qualification_state") != "QUALIFIED"
        or record.get("checker_result") != "COMPONENT_CHECK_PASS"
        or record.get("replay_result") != "PASS"
        or not isinstance(record.get("accepted_run_ids"), list)
        or len(record["accepted_run_ids"]) != 2
        or len(set(record["accepted_run_ids"])) != 2
        or not isinstance(record.get("evidence_references"), list)
        or len(record["evidence_references"]) != 2
    ):
        raise ProfileAuthorityError("Qualified Profile record is not eligible for this Run")
    return {
        "schema": "qualified-profile-authority-v2",
        "qualified_profile_record_id": qualified_profile_record_id,
        "qualified_profile_registry_ref": registry_ref,
        "qualified_profile_registry_sha256": registry_sha256,
        "qualified_profile_registry_content_sha256": document["registry_content_sha256"],
        "profile_id": expected_profile_id,
        "runtime_lock_sha256": expected_runtime_lock_sha256,
    }


def validate_persisted_profile_authority(
    value: dict[str, Any],
    *,
    repository_root: Path,
    expected_profile_id: str,
    expected_runtime_lock_sha256: str,
) -> dict[str, Any]:
    """Re-resolve persisted authority and reject missing or surplus fields."""

    if not isinstance(value, dict):
        raise ProfileAuthorityError("Qualified Profile authority must be a JSON object")
    try:
        resolved = resolve_profile_authority(
            repository_root=repository_root,
            registry_ref=value["qualified_profile_registry_ref"],
            registry_sha256=value["qualified_profile_registry_sha256"],
            qualified_profile_record_id=value["qualified_profile_record_id"],
            expected_profile_id=expected_profile_id,
            expected_runtime_lock_sha256=expected_runtime_lock_sha256,
        )
    except (KeyError, TypeError) as exc:
        raise ProfileAuthorityError("Qualified Profile authority fields are incomplete") from exc
    if value != resolved:
        raise ProfileAuthorityError("Qualified Profile authority contains stale or surplus fields")
    return resolved


__all__ = [
    "ProfileAuthorityError",
    "resolve_profile_authority",
    "validate_persisted_profile_authority",
]
