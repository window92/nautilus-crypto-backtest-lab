"""Cryptographic Host Acceptance attestation distinct from portable CI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crypto_lab.git_identity import require_repository_root
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file


ATTESTATION_SCHEMA = "host-acceptance-attestation-v1"
ATTESTATION_RELATIVE = Path(
    "evidence/audit/adversarial-remediation-002/host-acceptance/attestation.json",
)
SOURCE_FILES = (
    "SSOT.md",
    "AGENTS.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "runtime.lock.json",
    "runtime-bootstrap-authority.json",
    "requirements.lock.txt",
    "requirements.data.lock.txt",
    "data-tool.lock.json",
    ".github/workflows/ci.yml",
    "evidence/audit/adversarial-remediation-002/data-rebuild-validation.json",
)
SOURCE_TREES = (
    "src",
    "tests",
    "scripts",
    "schemas",
    "configs",
)
RELEASE_DIR = Path("data/releases")


def _regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _iter_source_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for relative in SOURCE_FILES:
        candidate = root / relative
        if _regular_file(candidate):
            files.append(candidate)
    for tree in SOURCE_TREES:
        base = root / tree
        if not base.is_dir() or base.is_symlink():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            if path.resolve() == (root / ATTESTATION_RELATIVE).resolve():
                continue
            files.append(path)
    release_root = root / RELEASE_DIR
    if release_root.is_dir() and not release_root.is_symlink():
        for path in sorted(release_root.glob("*.json")):
            if _regular_file(path):
                files.append(path)
    unique = []
    seen: set[Path] = set()
    for path in files:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return tuple(sorted(unique, key=lambda item: item.relative_to(root).as_posix()))


def product_source_inventory(repository_root: Path) -> list[dict[str, Any]]:
    """Return the portable product-source inventory hashed from working-tree bytes."""

    root = require_repository_root(repository_root)
    inventory: list[dict[str, Any]] = []
    for path in _iter_source_files(root):
        relative = path.relative_to(root).as_posix()
        inventory.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "byte_size": path.stat().st_size,
            },
        )
    return inventory


def product_source_identity(repository_root: Path) -> str:
    return canonical_sha256({"files": product_source_inventory(repository_root)})


def build_host_acceptance_attestation(
    repository_root: Path,
    *,
    data_identities: dict[str, Any],
    acceptance: dict[str, Any],
) -> dict[str, Any]:
    """Build an attestation that excludes its own bytes from the identity."""

    root = require_repository_root(repository_root)
    material = {
        "schema": ATTESTATION_SCHEMA,
        "status": "CURRENT",
        "product_source_identity": product_source_identity(root),
        "product_source_file_count": len(product_source_inventory(root)),
        "data_identities": data_identities,
        "acceptance": acceptance,
        "official_acceptance": True,
        "portable_ci_is_official_acceptance": False,
    }
    attestation = dict(material)
    attestation["attestation_identity"] = canonical_sha256(material)
    return attestation


def load_attestation(repository_root: Path) -> dict[str, Any]:
    root = require_repository_root(repository_root)
    path = root / ATTESTATION_RELATIVE
    if path.is_symlink():
        raise ValueError("host acceptance attestation must not be a symlink")
    if not path.is_file():
        raise FileNotFoundError("host acceptance attestation is missing")
    payload = path.read_bytes()
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("host acceptance attestation is not an object")
    if payload != canonical_json_bytes(value) + b"\n":
        raise ValueError("host acceptance attestation is not canonical")
    return value


def verify_host_acceptance_attestation(
    repository_root: Path,
    *,
    portable_only: bool = False,
) -> dict[str, Any]:
    """Verify the committed attestation still matches current product source.

    Portable CI uses ``portable_only=True`` and does not require host DuckDB or
    Raw corpus files.  It still fails if product-source bytes diverged.
    """

    root = require_repository_root(repository_root)
    attestation = load_attestation(root)
    material = dict(attestation)
    declared = material.pop("attestation_identity", None)
    if (
        attestation.get("schema") != ATTESTATION_SCHEMA
        or attestation.get("status") != "CURRENT"
        or attestation.get("portable_ci_is_official_acceptance") is not False
        or declared != canonical_sha256(material)
    ):
        raise ValueError("host acceptance attestation contract differs")
    current = product_source_identity(root)
    if current != attestation.get("product_source_identity"):
        raise ValueError("host acceptance attestation product-source identity is stale")
    if not portable_only:
        data = attestation.get("data_identities")
        if not isinstance(data, dict):
            raise ValueError("host acceptance data identities are missing")
        duckdb_path = data.get("official_primary_duckdb_path")
        duckdb_hash = data.get("official_primary_duckdb_sha256")
        if isinstance(duckdb_path, str) and isinstance(duckdb_hash, str):
            db = Path(duckdb_path)
            if not db.is_absolute():
                db = root / duckdb_path
            if db.is_symlink() or not db.is_file():
                raise ValueError("official DuckDB path is missing")
            if sha256_file(db) != duckdb_hash:
                raise ValueError("official DuckDB identity diverged")
    return {
        "status": "PASS",
        "portable_only": portable_only,
        "product_source_identity": current,
        "attestation_identity": declared,
        "official_acceptance": True,
        "portable_ci_is_official_acceptance": False,
    }


__all__ = [
    "ATTESTATION_RELATIVE",
    "ATTESTATION_SCHEMA",
    "build_host_acceptance_attestation",
    "load_attestation",
    "product_source_identity",
    "product_source_inventory",
    "verify_host_acceptance_attestation",
]
