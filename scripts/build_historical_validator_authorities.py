#!/usr/bin/env python3
"""Build deterministic v2 authorities for pinned historical validators.

The builder intentionally uses only the Python standard library and Git.  A
build specification must select every validator commit explicitly; neither
``HEAD`` nor a tag/branch is accepted as an implicit historical authority.
External directories are expanded to a complete, sorted, content-addressed
FILE inventory.  The resulting authority never trusts a directory locator.

This script does not execute validators and does not copy or modify historical
evidence.  Execution materializes the FILE bindings as a separately verified
repository view.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any


BUILD_SPEC_SCHEMA = "historical-validator-authority-build-spec-v1"
AUTHORITY_SCHEMA = "historical-validator-authorities-v2"
EXPECTED_RESULTS_SCHEMA = "historical-validator-expected-results-v1"
BUILDER_RELATIVE_PATH = "scripts/build_historical_validator_authorities.py"
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FILE_MODES = frozenset({"100644", "100755"})
_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "TZ": "UTC",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
}
_DATA_RUNTIME_VALIDATORS = frozenset(
    {
        "validate_data_provenance_evidence.py",
        "validate_free_official_binance_rebuild.py",
        "validate_free_official_raw_objects.py",
        "validate_instrument_representation_continuity.py",
    },
)
_CURRENT_ARGUMENTS = {
    "validate_free_official_binance_rebuild.py": (
        "--primary-result",
        "{repository}/data/duckdb/instrument-representation-funding-checker-001/primary-v6-result.json",
        "--independent-result",
        "{repository}/data/duckdb/instrument-representation-funding-checker-001/independent-v3-result.json",
        "--primary-catalog-root",
        "{repository}/data/catalog/instrument-representation-funding-checker-001/primary-v6",
        "--independent-catalog-root",
        "{repository}/data/catalog/instrument-representation-funding-checker-001/independent-v3",
        "--artifact-root",
        "{repository}/data/duckdb/instrument-representation-funding-checker-001/release-artifacts",
        "--output",
        "{repository}/data/duckdb/instrument-representation-funding-checker-001/deterministic-validation-v6.json",
    ),
    "validate_free_official_raw_objects.py": (
        "--database",
        "{repository}/data/duckdb/instrument-representation-funding-checker-001/primary-v6.duckdb",
    ),
    "validate_instrument_representation_continuity.py": (
        "--historical-database",
        "data/duckdb/free-official-binance-data-duckdb-001/primary-v4.duckdb",
        "--repaired-database",
        "data/duckdb/instrument-representation-funding-checker-001/primary-v6.duckdb",
        "--output",
        "data/duckdb/instrument-representation-funding-checker-001/value-continuity-v1.json",
    ),
}
_CURRENT_TRACKED_DEPENDENCIES = (
    "SSOT.md",
    "contracts/historical-contract-snapshots.json",
    "data-tool.lock.json",
    "evidence/m2/m2-acceptance-001/raw-object-inventory-addendum-001.json",
    "evidence/m2/m2-acceptance-001/raw-object-inventory.json",
    "evidence/repair/free-official-binance-data-duckdb-001/raw-object-inventory.json",
    "pyproject.toml",
    "requirements.data.lock.txt",
    "requirements.lock.txt",
    "runtime.lock.json",
)


class HistoricalAuthorityBuildError(ValueError):
    """A fail-closed deterministic authority build error."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _strict_object(value: object, required: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        observed = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise HistoricalAuthorityBuildError(
            f"{label} fields={observed!r}, expected={sorted(required)!r}",
        )
    return value


def _load_json(path: Path) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise HistoricalAuthorityBuildError(f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(
            _read_regular(path).decode("utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalAuthorityBuildError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HistoricalAuthorityBuildError(f"JSON root is not an object: {path}")
    return value


def _load_expected_results(
    path: Path,
    *,
    product_commit: str,
    historical_commits: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Load the independently observed output contract for every validator.

    Historical validation authority must bind what the pinned executable
    actually returned.  It must not manufacture an all-PASS expectation from
    the desired publication outcome.  The source-commit echo prevents an
    observation made with one historical executable from being assigned to a
    different validator snapshot.
    """

    document = _strict_object(
        _load_json(path),
        {"product_commit", "results", "schema"},
        label="historical validator expected results",
    )
    if document["schema"] != EXPECTED_RESULTS_SCHEMA:
        raise HistoricalAuthorityBuildError(
            "historical validator expected-results schema differs",
        )
    if document["product_commit"] != product_commit:
        raise HistoricalAuthorityBuildError(
            "historical validator expected results select a different Product commit",
        )
    results = document["results"]
    if not isinstance(results, dict) or set(results) != set(historical_commits):
        raise HistoricalAuthorityBuildError(
            "historical validator expected results do not equal the execution plan",
        )
    parsed: dict[str, dict[str, Any]] = {}
    for name in sorted(historical_commits):
        item = _strict_object(
            results[name],
            {
                "expected_exit_code",
                "expected_status",
                "expected_stderr_sha256",
                "expected_stdout_sha256",
                "source_commit",
            },
            label=f"historical validator expected result {name}",
        )
        if (
            item["source_commit"] != historical_commits[name]
            or type(item["expected_exit_code"]) is not int
            or item["expected_exit_code"] < 0
            or item["expected_exit_code"] > 255
            or item["expected_status"] not in {"PASS", "FAIL"}
            or not isinstance(item["expected_stdout_sha256"], str)
            or _SHA256.fullmatch(item["expected_stdout_sha256"]) is None
            or not isinstance(item["expected_stderr_sha256"], str)
            or _SHA256.fullmatch(item["expected_stderr_sha256"]) is None
        ):
            raise HistoricalAuthorityBuildError(
                f"historical validator expected result is invalid: {name}",
            )
        parsed[name] = dict(item)
    return parsed


def _safe_relative(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HistoricalAuthorityBuildError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or value in {".", ".."}
        or ".." in path.parts
        or any(ord(character) < 32 for character in value)
        or "\\" in value
    ):
        raise HistoricalAuthorityBuildError(f"unsafe {label}: {value!r}")
    return value


def _safe_external_target(value: object) -> str:
    relative = _safe_relative(value, label="external target")
    first = Path(relative).parts[0]
    if first in {".git", "scripts", "src"}:
        raise HistoricalAuthorityBuildError(
            f"external target overlaps executable/Git namespace: {relative}",
        )
    return relative


def _git(repository: Path, *arguments: str, check: bool = True) -> bytes:
    git = shutil.which("git") or "/usr/bin/git"
    process = subprocess.run(
        [git, "--no-replace-objects", *arguments],
        cwd=repository,
        env=_ENVIRONMENT,
        check=False,
        capture_output=True,
    )
    if check and process.returncode != 0:
        detail = (process.stderr or process.stdout).decode("utf-8", "replace").strip()
        raise HistoricalAuthorityBuildError(
            detail or f"Git command failed with exit code {process.returncode}",
        )
    return process.stdout


def _read_regular_with_identity(path: Path, *, collect: bool) -> tuple[bytes, str, int]:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise HistoricalAuthorityBuildError(f"cannot open regular file {path}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise HistoricalAuthorityBuildError(f"not a regular file: {path}")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            if collect:
                chunks.append(chunk)
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or size != after.st_size:
            raise HistoricalAuthorityBuildError(f"file changed while hashing: {path}")
        return b"".join(chunks), digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _read_regular(path: Path) -> bytes:
    return _read_regular_with_identity(path, collect=True)[0]


def _regular_identity(path: Path) -> tuple[str, int]:
    _payload, digest, size = _read_regular_with_identity(path, collect=False)
    return digest, size


def _repository_file(repository: Path, relative: str) -> tuple[Path, str, int]:
    lexical = Path(os.path.abspath(repository / relative))
    try:
        lexical.relative_to(repository)
    except ValueError as exc:
        raise HistoricalAuthorityBuildError(f"external locator escapes repository: {relative}") from exc
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise HistoricalAuthorityBuildError(f"external locator is missing: {relative}") from exc
    if resolved != lexical or lexical.is_symlink():
        raise HistoricalAuthorityBuildError(f"external locator traverses a symlink: {relative}")
    digest, size = _regular_identity(lexical)
    return lexical, digest, size


def _tree_inventory(repository: Path, locator: str) -> list[dict[str, Any]]:
    root = Path(os.path.abspath(repository / locator))
    try:
        root.relative_to(repository)
    except ValueError as exc:
        raise HistoricalAuthorityBuildError(f"external root escapes repository: {locator}") from exc
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise HistoricalAuthorityBuildError(f"external root is missing: {locator}") from exc
    if resolved != root or root.is_symlink() or not root.is_dir():
        raise HistoricalAuthorityBuildError(f"external root is not an exact directory: {locator}")

    records: list[dict[str, Any]] = []

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise HistoricalAuthorityBuildError(f"cannot enumerate {directory}: {exc}") from exc
        for entry in entries:
            metadata = entry.stat(follow_symlinks=False)
            path = Path(entry.path)
            if stat.S_ISLNK(metadata.st_mode):
                raise HistoricalAuthorityBuildError(f"symlink in external root: {path}")
            if stat.S_ISDIR(metadata.st_mode):
                visit(path)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise HistoricalAuthorityBuildError(f"special file in external root: {path}")
            relative = path.relative_to(root).as_posix()
            digest, size = _regular_identity(path)
            records.append(
                {
                    "path": relative,
                    "sha256": digest,
                    "size_bytes": size,
                },
            )

    visit(root)
    records.sort(key=lambda item: item["path"])
    if not records:
        raise HistoricalAuthorityBuildError(f"external root is empty: {locator}")
    return records


def external_root_identity(repository: Path, locator: str) -> dict[str, Any]:
    """Return the canonical pre-commit inventory expectation for one root."""

    repository = Path(repository).resolve(strict=True)
    locator = _safe_relative(locator, label="external root locator")
    inventory = _tree_inventory(repository, locator)
    return {
        "file_count": len(inventory),
        "inventory_identity": _canonical_sha256(inventory),
    }


def _legacy_validator_commits(
    repository: Path,
    legacy_manifest_path: Path,
) -> dict[str, str]:
    legacy = _load_json(legacy_manifest_path)
    declared = legacy.get("validators")
    snapshots = legacy.get("snapshots")
    if legacy.get("schema") != "historical-contract-snapshots-v1" or not isinstance(
        declared,
        dict,
    ) or not isinstance(snapshots, dict):
        raise HistoricalAuthorityBuildError("legacy declared-validator inventory is invalid")
    plan = sorted(declared)
    if len(plan) != 14 or len(plan) != len(set(plan)):
        raise HistoricalAuthorityBuildError("legacy plan is not the exact 14-validator inventory")
    result: dict[str, str] = {}
    for name in plan:
        snapshot_id = declared[name]
        snapshot = snapshots.get(snapshot_id) if isinstance(snapshot_id, str) else None
        commit = snapshot.get("git_commit") if isinstance(snapshot, dict) else None
        if not isinstance(commit, str):
            raise HistoricalAuthorityBuildError(
                f"legacy validator has no exact snapshot commit: {name}",
            )
        result[name] = _exact_commit(
            repository,
            commit,
            label=f"historical source_commit for {name}",
        )
    return result


def _declared_plan(repository: Path, legacy_manifest_path: Path) -> list[str]:
    return sorted(_legacy_validator_commits(repository, legacy_manifest_path))


def _profile_from_bootstrap_authority(
    path: Path,
    *,
    product_commit: str,
    product_tree: str,
    require_duckdb: bool,
) -> dict[str, Any]:
    authority = _load_json(path)
    required = {
        "allowed_targets",
        "bootstrap_sha256",
        "initial_sys_path",
        "product",
        "python",
        "schema",
        "site_packages",
    }
    _strict_object(authority, required, label=f"runtime bootstrap authority {path}")
    if authority["schema"] != "isolated-runtime-bootstrap-authority-v1":
        raise HistoricalAuthorityBuildError(f"runtime bootstrap schema differs: {path}")
    product = authority["product"]
    if not isinstance(product, dict) or (
        product.get("source_commit") != product_commit
        or product.get("source_tree") != product_tree
    ):
        raise HistoricalAuthorityBuildError(
            f"runtime bootstrap authority is not bound to Product commit: {path}",
        )
    site = authority["site_packages"]
    if isinstance(site, dict) and isinstance(site.get("roots"), list):
        distributions = [
            item
            for root in site["roots"]
            if isinstance(root, dict) and isinstance(root.get("distributions"), list)
            for item in root["distributions"]
        ]
    else:
        distributions = site.get("distributions") if isinstance(site, dict) else None
    if not isinstance(distributions, list):
        raise HistoricalAuthorityBuildError(f"runtime distribution inventory is invalid: {path}")
    dist_info = {
        item.get("dist_info_relative_path")
        for item in distributions
        if isinstance(item, dict)
    }
    has_nautilus = any(
        isinstance(name, str) and name.startswith("nautilus_trader-")
        for name in dist_info
    )
    has_duckdb = any(
        isinstance(name, str) and name.startswith("duckdb-")
        for name in dist_info
    )
    if not has_nautilus or (require_duckdb and not has_duckdb):
        raise HistoricalAuthorityBuildError(
            f"runtime authority lacks required Nautilus/DuckDB closure: {path}",
        )
    return {
        key: authority[key]
        for key in ("bootstrap_sha256", "initial_sys_path", "python", "site_packages")
    }


def _external_file_spec(
    repository: Path,
    locator: str,
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> dict[str, Any]:
    locator = _safe_relative(locator, label="current external file")
    _path, digest, size = _repository_file(repository, locator)
    if expected_sha256 is not None and digest != expected_sha256:
        raise HistoricalAuthorityBuildError(f"current external hash differs: {locator}")
    if expected_size is not None and size != expected_size:
        raise HistoricalAuthorityBuildError(f"current external size differs: {locator}")
    return {
        "locator": locator,
        "target": locator,
        "sha256": digest,
        "size_bytes": size,
    }


def _committed_json(repository: Path, commit: str, relative: str) -> dict[str, Any]:
    relative = _safe_relative(relative, label="committed JSON input")
    current = _read_regular(repository / relative)
    committed = _git(repository, "show", f"{commit}:{relative}")
    if current != committed:
        raise HistoricalAuthorityBuildError(f"committed JSON input has working-tree drift: {relative}")
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise HistoricalAuthorityBuildError(
                    f"duplicate JSON key in committed input {relative}: {key!r}",
                )
            result[key] = value
        return result

    try:
        value = json.loads(committed, object_pairs_hook=reject_duplicates)
    except json.JSONDecodeError as exc:
        raise HistoricalAuthorityBuildError(f"committed JSON input is invalid: {relative}") from exc
    if not isinstance(value, dict):
        raise HistoricalAuthorityBuildError(f"committed JSON input is not an object: {relative}")
    return value


def _current_external_inputs(
    repository: Path,
    product_commit: str,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Derive narrow external inputs for the five repo-data validators."""

    free_inventory = _committed_json(
        repository,
        product_commit,
        "evidence/repair/free-official-binance-data-duckdb-001/raw-object-inventory.json",
    )
    free_rows = free_inventory.get("raw_objects")
    if (
        not isinstance(free_rows, list)
        or free_inventory.get("unique_raw_object_count") != len(free_rows)
    ):
        raise HistoricalAuthorityBuildError("free-official Raw inventory is malformed")
    raw_files: list[dict[str, Any]] = []
    for row in free_rows:
        if not isinstance(row, dict):
            raise HistoricalAuthorityBuildError("free-official Raw record is malformed")
        digest = row.get("raw_object_sha256")
        byte_size = row.get("byte_size")
        if (
            not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or type(byte_size) is not int
            or byte_size < 0
        ):
            raise HistoricalAuthorityBuildError("free-official Raw identity is malformed")
        raw_files.append(
            _external_file_spec(
                repository,
                row.get("raw_object_path"),
                expected_sha256=digest,
                expected_size=byte_size,
            ),
        )
    instrument_root = "data/raw/instrument-representation-funding-checker-001/objects"
    for row in _tree_inventory(repository, instrument_root):
        raw_files.append(
            _external_file_spec(
                repository,
                f"{instrument_root}/{row['path']}",
                expected_sha256=row["sha256"],
                expected_size=row["size_bytes"],
            ),
        )
    raw_files.sort(key=lambda item: item["target"])
    if len({item["target"] for item in raw_files}) != len(raw_files):
        raise HistoricalAuthorityBuildError("current full Raw inventory has duplicate paths")

    m2_files: list[dict[str, Any]] = []
    for relative in (
        "evidence/m2/m2-acceptance-001/raw-object-inventory.json",
        "evidence/m2/m2-acceptance-001/raw-object-inventory-addendum-001.json",
    ):
        inventory = _committed_json(repository, product_commit, relative)
        rows = inventory.get("objects")
        if not isinstance(rows, list) or inventory.get("object_count") != len(rows):
            raise HistoricalAuthorityBuildError(f"M2 Raw inventory is malformed: {relative}")
        for row in rows:
            if not isinstance(row, dict):
                raise HistoricalAuthorityBuildError(f"M2 Raw record is malformed: {relative}")
            digest = row.get("sha256")
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                raise HistoricalAuthorityBuildError(f"M2 Raw digest is malformed: {relative}")
            byte_size = row.get("byte_size")
            if type(byte_size) is not int or byte_size < 0:
                raise HistoricalAuthorityBuildError(f"M2 Raw size is malformed: {relative}")
            m2_files.append(
                _external_file_spec(
                    repository,
                    f"data/raw/sha256/{digest[:2]}/{digest}.blob",
                    expected_sha256=digest,
                    expected_size=byte_size,
                ),
            )
    m2_files.sort(key=lambda item: item["target"])
    if len({item["target"] for item in m2_files}) != len(m2_files):
        raise HistoricalAuthorityBuildError("M2 Raw inventories contain duplicate paths")

    database = _external_file_spec(
        repository,
        "data/duckdb/instrument-representation-funding-checker-001/primary-v6.duckdb",
    )
    historical_database = _external_file_spec(
        repository,
        "data/duckdb/free-official-binance-data-duckdb-001/primary-v4.duckdb",
    )
    provenance_database = _external_file_spec(
        repository,
        "data/duckdb/binance-btcusdt-owner-smoke-001.duckdb",
    )
    rebuild_files = [
        database,
        *raw_files,
        *(
            _external_file_spec(repository, relative)
            for relative in (
                "data/duckdb/instrument-representation-funding-checker-001/primary-v6-result.json",
                "data/duckdb/instrument-representation-funding-checker-001/independent-v3-result.json",
                "data/duckdb/instrument-representation-funding-checker-001/independent-v3.duckdb",
            )
        ),
    ]
    rebuild_roots = []
    for locator in (
        "data/catalog/instrument-representation-funding-checker-001/primary-v6",
        "data/catalog/instrument-representation-funding-checker-001/independent-v3",
        "data/duckdb/instrument-representation-funding-checker-001/release-artifacts",
    ):
        rebuild_roots.append(
            {
                "locator": locator,
                "target": locator,
                **external_root_identity(repository, locator),
            },
        )
    return {
        "validate_data_provenance_evidence.py": {
            "external_files": [provenance_database],
            "external_roots": [],
        },
        "validate_free_official_binance_rebuild.py": {
            "external_files": sorted(rebuild_files, key=lambda item: item["target"]),
            "external_roots": rebuild_roots,
        },
        "validate_free_official_raw_objects.py": {
            "external_files": sorted([database, *raw_files], key=lambda item: item["target"]),
            "external_roots": [],
        },
        "validate_instrument_representation_continuity.py": {
            "external_files": sorted(
                [database, historical_database],
                key=lambda item: item["target"],
            ),
            "external_roots": [],
        },
        "validate_m2_evidence.py": {
            "external_files": m2_files,
            "external_roots": [],
        },
    }


def derive_current_product_build_spec(
    *,
    repository: Path,
    product_commit: str,
    legacy_manifest_path: Path,
    project_runtime_authority_path: Path,
    data_runtime_authority_path: Path,
    expected_results_path: Path,
    builder_path: Path | None = None,
) -> dict[str, Any]:
    """Derive v2 authorities for the validators which judged historical bytes.

    ``product_commit`` pins this builder, executor, bootstrap authorities, and
    the resulting authority document.  It MUST NOT replace the validator
    semantics recorded by the additive v1 validator-to-snapshot mapping.  Each
    validator therefore selects the exact historical snapshot commit declared
    for it, while v1 by itself remains diagnostic and non-executable.
    """

    repository_input = Path(repository)
    if repository_input.is_symlink():
        raise HistoricalAuthorityBuildError("repository is a symlink")
    repository = repository_input.resolve(strict=True)
    product_commit = _exact_commit(repository, product_commit, label="product_commit")
    head = _git(repository, "rev-parse", "HEAD^{commit}").decode().strip()
    if head != product_commit:
        raise HistoricalAuthorityBuildError("Product commit must be the exact repository HEAD")
    _assert_builder_identity(repository, product_commit, Path(builder_path or __file__))
    product_tree = _git(repository, "rev-parse", f"{product_commit}^{{tree}}").decode().strip()
    historical_commits = _legacy_validator_commits(repository, legacy_manifest_path)
    plan = sorted(historical_commits)
    expected_results = _load_expected_results(
        expected_results_path,
        product_commit=product_commit,
        historical_commits=historical_commits,
    )
    for name, source_commit in historical_commits.items():
        _git_file_identity(
            repository,
            source_commit,
            f"scripts/{name}",
            required=True,
        )
    profiles = {
        "data-runtime": _profile_from_bootstrap_authority(
            data_runtime_authority_path,
            product_commit=product_commit,
            product_tree=product_tree,
            require_duckdb=True,
        ),
        "project-runtime": _profile_from_bootstrap_authority(
            project_runtime_authority_path,
            product_commit=product_commit,
            product_tree=product_tree,
            require_duckdb=False,
        ),
    }
    external_inputs = _current_external_inputs(repository, product_commit)
    for arguments in _CURRENT_ARGUMENTS.values():
        for argument in arguments:
            prefix = "{repository}/"
            if not argument.startswith(prefix):
                continue
            relative = _safe_relative(argument[len(prefix) :], label="current validator input")
            path = repository / relative
            if path.is_symlink() or not path.exists() or path.resolve(strict=True) != path:
                raise HistoricalAuthorityBuildError(
                    f"canonical current validator input is absent or symlinked: {relative}",
                )
    validators: dict[str, dict[str, Any]] = {}
    for name in plan:
        source_commit = historical_commits[name]
        expected = expected_results[name]
        schema_dependencies = _git_files_under(repository, source_commit, "schemas")
        tracked_dependencies = sorted(
            {
                *(
                    relative
                    for relative in _CURRENT_TRACKED_DEPENDENCIES
                    if _git_file_identity(
                        repository,
                        source_commit,
                        relative,
                        required=False,
                    )
                    is not None
                ),
                *schema_dependencies,
            },
        )
        validators[name] = {
            "authority_id": f"historical-v2-{source_commit[:12]}-{name[:-3]}",
            "source_commit": source_commit,
            "entrypoint": f"scripts/{name}",
            # Historical validators were executable scripts with their own
            # main wrapper. Binding that exact file in both roles is honest;
            # a later batch wrapper must not be projected into an old tree.
            "wrapper": f"scripts/{name}",
            "closure_paths": tracked_dependencies,
            "arguments": list(_CURRENT_ARGUMENTS.get(name, ())),
            "external_files": list(
                external_inputs.get(name, {}).get("external_files", []),
            ),
            "external_roots": list(
                external_inputs.get(name, {}).get("external_roots", []),
            ),
            "interpreter_profile": (
                "data-runtime" if name in _DATA_RUNTIME_VALIDATORS else "project-runtime"
            ),
            "expected_exit_code": expected["expected_exit_code"],
            "expected_status": expected["expected_status"],
            "expected_stdout_sha256": expected["expected_stdout_sha256"],
            "expected_stderr_sha256": expected["expected_stderr_sha256"],
        }
    return {
        "schema": BUILD_SPEC_SCHEMA,
        "product_commit": product_commit,
        "execution_plan": plan,
        "runtime_profiles": profiles,
        "validators": validators,
    }


def _git_file_identity(
    repository: Path,
    commit: str,
    relative: str,
    *,
    required: bool,
) -> dict[str, Any] | None:
    listing = _git(repository, "ls-tree", "-z", commit, "--", relative)
    if not listing:
        if required:
            raise HistoricalAuthorityBuildError(
                f"required closure path is absent at {commit}: {relative}",
            )
        return None
    rows = [row for row in listing.split(b"\0") if row]
    if len(rows) != 1:
        raise HistoricalAuthorityBuildError(f"ambiguous Git path at {commit}: {relative}")
    prefix, separator, returned = rows[0].partition(b"\t")
    fields = prefix.decode("ascii", "strict").split()
    if separator != b"\t" or len(fields) != 3 or returned.decode("utf-8") != relative:
        raise HistoricalAuthorityBuildError(f"unexpected Git tree record: {relative}")
    mode, object_type, _object_id = fields
    if object_type != "blob" or mode not in _FILE_MODES:
        raise HistoricalAuthorityBuildError(f"unsupported Git object {mode} {object_type}: {relative}")
    payload = _git(repository, "show", f"{commit}:{relative}")
    return {
        "mode": mode,
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def _git_files_under(repository: Path, commit: str, relative: str) -> list[str]:
    listing = _git(repository, "ls-tree", "-r", "-z", "--name-only", commit, "--", relative)
    result: list[str] = []
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8")
        _safe_relative(path, label="Git closure path")
        result.append(path)
    return sorted(result)


def _is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    git = shutil.which("git") or "/usr/bin/git"
    return subprocess.run(
        [git, "--no-replace-objects", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repository,
        env=_ENVIRONMENT,
        check=False,
        capture_output=True,
    ).returncode == 0


def _exact_commit(repository: Path, value: object, *, label: str) -> str:
    if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
        raise HistoricalAuthorityBuildError(f"{label} must be an explicit 40-character commit")
    resolved = _git(repository, "rev-parse", f"{value}^{{commit}}").decode().strip()
    if resolved != value:
        raise HistoricalAuthorityBuildError(f"{label} is not the exact selected commit")
    return value


def _assert_builder_identity(repository: Path, product_commit: str, builder_path: Path) -> None:
    expected_path = repository / BUILDER_RELATIVE_PATH
    if builder_path.is_symlink() or builder_path.resolve(strict=True) != expected_path:
        raise HistoricalAuthorityBuildError("builder must execute from its canonical repository path")
    expected = _git_file_identity(
        repository,
        product_commit,
        BUILDER_RELATIVE_PATH,
        required=True,
    )
    assert expected is not None
    payload = _read_regular(builder_path)
    if (
        hashlib.sha256(payload).hexdigest() != expected["sha256"]
        or len(payload) != expected["size_bytes"]
    ):
        raise HistoricalAuthorityBuildError("executed builder differs from Product commit")


def _runtime_profiles(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or not value:
        raise HistoricalAuthorityBuildError("runtime_profiles must be a non-empty object")
    result: dict[str, dict[str, Any]] = {}
    required = {"bootstrap_sha256", "initial_sys_path", "python", "site_packages"}
    for name, profile in sorted(value.items()):
        if not isinstance(name, str) or not name:
            raise HistoricalAuthorityBuildError("runtime profile name is invalid")
        item = _strict_object(profile, required, label=f"runtime profile {name}")
        if not isinstance(item["bootstrap_sha256"], str) or _SHA256.fullmatch(
            item["bootstrap_sha256"],
        ) is None:
            raise HistoricalAuthorityBuildError(f"runtime profile hash is invalid: {name}")
        # Nested runtime identity is revalidated by the isolated bootstrap.
        # Canonical serialization here prevents host/dict iteration order from
        # changing the produced authority bytes.
        _canonical_bytes(item)
        result[name] = item
    return result


def _expand_external_bindings(
    repository: Path,
    source_commit: str,
    validator: dict[str, Any],
    *,
    external_file_cache: dict[str, tuple[str, int]],
    external_root_cache: dict[str, tuple[dict[str, Any], ...]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    explicit = validator["external_files"]
    roots = validator["external_roots"]
    if not isinstance(explicit, list) or not isinstance(roots, list):
        raise HistoricalAuthorityBuildError("external_files/external_roots must be lists")
    for index, raw in enumerate(explicit):
        item = _strict_object(
            raw,
            {"locator", "sha256", "size_bytes", "target"},
            label=f"external file {index}",
        )
        locator = _safe_relative(item["locator"], label="external locator")
        target = _safe_external_target(item["target"])
        if (
            not isinstance(item["sha256"], str)
            or _SHA256.fullmatch(item["sha256"]) is None
            or type(item["size_bytes"]) is not int
            or item["size_bytes"] < 0
        ):
            raise HistoricalAuthorityBuildError(f"external file expectation is invalid: {locator}")
        if locator not in external_file_cache:
            _path, digest, size = _repository_file(repository, locator)
            external_file_cache[locator] = (digest, size)
        digest, size = external_file_cache[locator]
        if digest != item["sha256"] or size != item["size_bytes"]:
            raise HistoricalAuthorityBuildError(f"external file expectation differs: {locator}")
        candidates.append(
            {
                "kind": "FILE",
                "locator": locator,
                "target": target,
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
            },
        )
    for index, raw in enumerate(roots):
        item = _strict_object(
            raw,
            {"file_count", "inventory_identity", "locator", "target"},
            label=f"external root {index}",
        )
        locator_root = _safe_relative(item["locator"], label="external root locator")
        target_root = _safe_external_target(item["target"])
        if locator_root not in external_root_cache:
            external_root_cache[locator_root] = tuple(
                _tree_inventory(repository, locator_root),
            )
        inventory = external_root_cache[locator_root]
        if (
            type(item["file_count"]) is not int
            or item["file_count"] < 1
            or not isinstance(item["inventory_identity"], str)
            or _SHA256.fullmatch(item["inventory_identity"]) is None
            or len(inventory) != item["file_count"]
            or _canonical_sha256(inventory) != item["inventory_identity"]
        ):
            raise HistoricalAuthorityBuildError(
                f"external root inventory expectation differs: {locator_root}",
            )
        for record in inventory:
            candidates.append(
                {
                    "kind": "FILE",
                    "locator": f"{locator_root}/{record['path']}",
                    "target": f"{target_root}/{record['path']}",
                    "sha256": record["sha256"],
                    "size_bytes": record["size_bytes"],
                },
            )

    candidates.sort(key=lambda item: item["target"])
    targets = [item["target"] for item in candidates]
    if len(targets) != len(set(targets)):
        raise HistoricalAuthorityBuildError("duplicate external target after root expansion")
    bindings: list[dict[str, Any]] = []
    for item in candidates:
        # A file already present in the selected historical tree is part of the
        # Git source tree, not an external input.  It may be omitted only when
        # the current bytes are exactly the historical bytes.  Overlaying a
        # different tracked file is always forbidden.
        tracked = _git_file_identity(
            repository,
            source_commit,
            item["target"],
            required=False,
        )
        if tracked is not None:
            if (
                tracked["sha256"] != item["sha256"]
                or tracked["size_bytes"] != item["size_bytes"]
            ):
                raise HistoricalAuthorityBuildError(
                    f"external target collides with different historical Git bytes: {item['target']}",
                )
            continue
        # Reject a target below a historical symlink even if the exact file is
        # absent.  The executor must never traverse a Git-controlled link.
        parts = Path(item["target"]).parts
        for end in range(1, len(parts)):
            prefix = Path(*parts[:end]).as_posix()
            listing = _git(repository, "ls-tree", source_commit, "--", prefix)
            if listing.startswith(b"120000 "):
                raise HistoricalAuthorityBuildError(
                    f"external target parent is a historical symlink: {prefix}",
                )
        bindings.append(item)
    return bindings


def _authority(
    repository: Path,
    name: str,
    value: object,
    *,
    product_commit: str,
    profiles: dict[str, dict[str, Any]],
    external_file_cache: dict[str, tuple[str, int]],
    external_root_cache: dict[str, tuple[dict[str, Any], ...]],
) -> dict[str, Any]:
    item = _strict_object(
        value,
        {
            "arguments",
            "authority_id",
            "closure_paths",
            "entrypoint",
            "expected_exit_code",
            "expected_stderr_sha256",
            "expected_status",
            "expected_stdout_sha256",
            "external_files",
            "external_roots",
            "interpreter_profile",
            "source_commit",
            "wrapper",
        },
        label=f"validator build specification {name}",
    )
    source_commit = _exact_commit(
        repository,
        item["source_commit"],
        label=f"source_commit for {name}",
    )
    if not _is_ancestor(repository, source_commit, product_commit):
        raise HistoricalAuthorityBuildError(
            f"validator source commit is not an ancestor of Product commit: {name}",
        )
    entrypoint_path = _safe_relative(item["entrypoint"], label="validator entrypoint")
    wrapper_path = _safe_relative(item["wrapper"], label="validator wrapper")
    if Path(entrypoint_path).name != name or not entrypoint_path.endswith(".py"):
        raise HistoricalAuthorityBuildError(f"validator name/entrypoint mismatch: {name}")
    if not wrapper_path.endswith(".py"):
        raise HistoricalAuthorityBuildError(f"validator wrapper is not Python: {name}")
    closure_paths = item["closure_paths"]
    if not isinstance(closure_paths, list) or not all(isinstance(path, str) for path in closure_paths):
        raise HistoricalAuthorityBuildError(f"closure_paths is invalid: {name}")
    mandatory = [
        path
        for root in ("src/crypto_lab", "scripts")
        for path in _git_files_under(repository, source_commit, root)
        if path.endswith(".py")
    ]
    if not mandatory:
        raise HistoricalAuthorityBuildError(f"crypto_lab Python closure is empty: {name}")
    selected = set(mandatory)
    selected.update((entrypoint_path, wrapper_path))
    selected.update(
        _safe_relative(path, label="closure path")
        for path in closure_paths
    )
    closure = [
        _git_file_identity(repository, source_commit, path, required=True)
        for path in sorted(selected)
    ]
    assert all(record is not None for record in closure)
    closure_records = [record for record in closure if record is not None]
    by_path = {record["path"]: record for record in closure_records}
    profile = item["interpreter_profile"]
    arguments = item["arguments"]
    if (
        not isinstance(item["authority_id"], str)
        or not item["authority_id"]
        or not isinstance(profile, str)
        or profile not in profiles
        or not isinstance(arguments, list)
        or not all(isinstance(argument, str) and "\0" not in argument for argument in arguments)
        or type(item["expected_exit_code"]) is not int
        or item["expected_exit_code"] < 0
        or item["expected_exit_code"] > 255
        or item["expected_status"] not in {"PASS", "FAIL"}
        or not isinstance(item["expected_stdout_sha256"], str)
        or _SHA256.fullmatch(item["expected_stdout_sha256"]) is None
        or not isinstance(item["expected_stderr_sha256"], str)
        or _SHA256.fullmatch(item["expected_stderr_sha256"]) is None
    ):
        raise HistoricalAuthorityBuildError(f"validator execution contract is invalid: {name}")
    tree = _git(repository, "rev-parse", f"{source_commit}^{{tree}}").decode().strip()
    authority = {
        "authority_id": item["authority_id"],
        "validator_name": name,
        "source_commit": source_commit,
        "source_tree": tree,
        "entrypoint": by_path[entrypoint_path],
        "wrapper": by_path[wrapper_path],
        "executable_closure": closure_records,
        "arguments": list(arguments),
        "external_bindings": _expand_external_bindings(
            repository,
            source_commit,
            item,
            external_file_cache=external_file_cache,
            external_root_cache=external_root_cache,
        ),
        "interpreter_profile": profile,
        "expected_exit_code": item["expected_exit_code"],
        "expected_status": item["expected_status"],
        "expected_stdout_sha256": item["expected_stdout_sha256"],
        "expected_stderr_sha256": item["expected_stderr_sha256"],
    }
    authority["bundle_identity"] = _canonical_sha256(authority)
    return authority


def build_manifest(
    *,
    repository: Path,
    build_spec_path: Path,
    legacy_manifest_path: Path,
    builder_path: Path | None = None,
) -> dict[str, Any]:
    """Build a canonical manifest without writing it to disk."""

    repository_input = Path(repository)
    if repository_input.is_symlink():
        raise HistoricalAuthorityBuildError("repository is a symlink")
    repository = repository_input.resolve(strict=True)
    root = Path(_git(repository, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != repository:
        raise HistoricalAuthorityBuildError("repository argument is not the exact Git root")
    spec = _strict_object(
        _load_json(build_spec_path),
        {"execution_plan", "product_commit", "runtime_profiles", "schema", "validators"},
        label="historical authority build specification",
    )
    if spec["schema"] != BUILD_SPEC_SCHEMA:
        raise HistoricalAuthorityBuildError("historical authority build specification schema differs")
    product_commit = _exact_commit(repository, spec["product_commit"], label="product_commit")
    head = _git(repository, "rev-parse", "HEAD^{commit}").decode().strip()
    if head != product_commit:
        raise HistoricalAuthorityBuildError("Product commit must be the exact repository HEAD")
    _assert_builder_identity(
        repository,
        product_commit,
        Path(builder_path or __file__),
    )
    expected_plan = _declared_plan(repository, legacy_manifest_path)
    plan = spec["execution_plan"]
    if (
        not isinstance(plan, list)
        or plan != expected_plan
        or len(plan) != len(set(plan))
        or len(plan) != 14
    ):
        raise HistoricalAuthorityBuildError(
            "execution plan must be the canonical exact 14-validator legacy inventory",
        )
    validators = spec["validators"]
    if not isinstance(validators, dict) or set(validators) != set(plan):
        raise HistoricalAuthorityBuildError("validator specifications do not equal execution plan")
    profiles = _runtime_profiles(spec["runtime_profiles"])
    external_file_cache: dict[str, tuple[str, int]] = {}
    external_root_cache: dict[str, tuple[dict[str, Any], ...]] = {}
    authorities = {
        name: _authority(
            repository,
            name,
            validators[name],
            product_commit=product_commit,
            profiles=profiles,
            external_file_cache=external_file_cache,
            external_root_cache=external_root_cache,
        )
        for name in plan
    }
    used_profiles = {authority["interpreter_profile"] for authority in authorities.values()}
    if used_profiles != set(profiles):
        raise HistoricalAuthorityBuildError(
            "runtime profiles must exactly equal profiles used by the execution plan",
        )
    return {
        "schema": AUTHORITY_SCHEMA,
        "execution_plan": plan,
        "runtime_profiles": profiles,
        "authorities": authorities,
    }


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    derive = commands.add_parser(
        "derive-current-spec",
        help="derive historical validator authorities under an explicit Product commit",
    )
    derive.add_argument("--repository", type=Path, required=True)
    derive.add_argument("--product-commit", required=True)
    derive.add_argument("--legacy-manifest", type=Path, required=True)
    derive.add_argument("--project-runtime-authority", type=Path, required=True)
    derive.add_argument("--data-runtime-authority", type=Path, required=True)
    derive.add_argument("--expected-results", type=Path, required=True)
    derive.add_argument("--output", type=Path, required=True)
    build = commands.add_parser("build", help="build v2 authorities from a committed spec")
    build.add_argument("--repository", type=Path, required=True)
    build.add_argument("--build-spec", type=Path, required=True)
    build.add_argument("--legacy-manifest", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "derive-current-spec":
        spec = derive_current_product_build_spec(
            repository=arguments.repository,
            product_commit=arguments.product_commit,
            legacy_manifest_path=arguments.legacy_manifest,
            project_runtime_authority_path=arguments.project_runtime_authority,
            data_runtime_authority_path=arguments.data_runtime_authority,
            expected_results_path=arguments.expected_results,
        )
        payload = _canonical_bytes(spec) + b"\n"
        _atomic_write(arguments.output, payload)
        print(
            json.dumps(
                {
                    "build_spec_sha256": hashlib.sha256(payload).hexdigest(),
                    "historical_validator_commits_selected_from_v1_mapping": True,
                    "external_binding_occurrence_count": sum(
                        len(value["external_files"])
                        + sum(root["file_count"] for root in value["external_roots"])
                        for value in spec["validators"].values()
                    ),
                    "output": str(arguments.output),
                    "product_commit": spec["product_commit"],
                    "validator_count": len(spec["execution_plan"]),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        return 0
    manifest = build_manifest(
        repository=arguments.repository,
        build_spec_path=arguments.build_spec,
        legacy_manifest_path=arguments.legacy_manifest,
    )
    payload = _canonical_bytes(manifest) + b"\n"
    _atomic_write(arguments.output, payload)
    print(
        json.dumps(
            {
                "authority_count": len(manifest["execution_plan"]),
                "external_binding_count": sum(
                    len(authority["external_bindings"])
                    for authority in manifest["authorities"].values()
                ),
                "manifest_sha256": hashlib.sha256(payload).hexdigest(),
                "output": str(arguments.output),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
