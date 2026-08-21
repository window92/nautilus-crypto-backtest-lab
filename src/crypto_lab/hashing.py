"""Canonical JSON and SHA-256 primitives for frozen material payloads."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import msgspec


class CanonicalJSONError(ValueError):
    """Raised when a value cannot enter an SSOT canonical JSON payload."""


def _normalize(value: Any, path: str = "$") -> Any:
    if isinstance(value, msgspec.Struct):
        return {
            field.name: _normalize(getattr(value, field.name), f"{path}.{field.name}")
            for field in msgspec.structs.fields(type(value))
        }
    if isinstance(value, Enum):
        return _normalize(value.value, path)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CanonicalJSONError(f"{path}: Decimal must be finite")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalJSONError(f"{path}: timestamp must be timezone-aware UTC")
        if value.utcoffset().total_seconds() != 0:
            raise CanonicalJSONError(f"{path}: timestamp must use UTC")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJSONError(f"{path}: JSON object keys must be strings")
            normalized[key] = _normalize(item, f"{path}.{key}")
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalize(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, float) and not math.isfinite(value):
        raise CanonicalJSONError(f"{path}: float must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise CanonicalJSONError(f"{path}: unsupported canonical JSON type {type(value).__name__}")


def to_canonical_builtins(value: Any) -> Any:
    """Return JSON-compatible values with Decimal and UTC timestamp semantics preserved."""

    return _normalize(value)


def canonical_json_bytes(value: Any) -> bytes:
    """Encode exact canonical JSON bytes: UTF-8, sorted keys, no insignificant spaces."""

    normalized = to_canonical_builtins(value)
    try:
        text = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:  # pragma: no cover - guarded by normalization
        raise CanonicalJSONError(str(exc)) from exc
    return text.encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
