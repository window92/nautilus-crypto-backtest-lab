#!/usr/bin/env python3
"""Stdlib-only isolated bootstrap for Official and historical execution.

This module intentionally imports no project or site-package code.  Its JSON
authority is a separate, committed, content-addressed SourceRevision input; it
does not extend or reinterpret ``runtime.lock.json``.  The final Run checker
binds both these bootstrap bytes and the authority bytes from the Run's exact
source commit before an Official seal can pass.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import types
from pathlib import Path
from types import MappingProxyType
from typing import Any


FAILURE_CODE = "RUNTIME_STARTUP_MISMATCH"
BOOTSTRAP_STATE_MODULE = "_crypto_lab_verified_bootstrap"
_SCHEMA = "isolated-runtime-bootstrap-authority-v1"
_ATTESTATION_SCHEMA = "isolated-runtime-bootstrap-attestation-v1"
_ALLOWED_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "TZ": "UTC",
}
_HASH = frozenset("0123456789abcdef")


class BootstrapFailure(RuntimeError):
    """A structured failure before any Product or dependency import."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{FAILURE_CODE}:{reason}:{detail}")


def _fail(reason: str, detail: str) -> None:
    raise BootstrapFailure(reason, detail)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HASH


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


def _read_regular(path: Path) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        _fail("FILE_IDENTITY_MISMATCH", f"cannot open regular file {path}: {exc}")
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            _fail("FILE_IDENTITY_MISMATCH", f"not a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(_read_regular(path)).hexdigest()


def _regular_identity(path: Path) -> tuple[str, int]:
    """Hash a potentially large regular file without retaining it in memory."""

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        _fail("EXTERNAL_FILE_MISMATCH", f"cannot open regular file {path}: {exc}")
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail("EXTERNAL_FILE_MISMATCH", f"not a regular file: {path}")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
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
            _fail("EXTERNAL_FILE_MISMATCH", f"file changed while hashing: {path}")
        return digest.hexdigest(), size
    finally:
        os.close(descriptor)


def _strict_object(value: object, required: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        observed = sorted(value) if isinstance(value, dict) else type(value).__name__
        _fail("AUTHORITY_INVALID", f"{label} fields={observed!r}, expected={sorted(required)!r}")
    return value


def _safe_relative(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("AUTHORITY_INVALID", f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        _fail("AUTHORITY_INVALID", f"unsafe {label}: {value!r}")
    return value


def _verify_process_boundary() -> None:
    flags = sys.flags
    if not (
        flags.isolated == 1
        and flags.ignore_environment == 1
        and flags.no_site == 1
        and flags.no_user_site == 1
        and flags.safe_path
        and flags.dont_write_bytecode == 1
        and sys.pycache_prefix == "/dev/null"
    ):
        _fail(
            "FLAGS_INVALID",
            "requires -I -P -S -B -X pycache_prefix=/dev/null",
        )
    if any(name in sys.modules for name in ("site", "sitecustomize", "usercustomize")):
        _fail("STARTUP_CUSTOMIZATION_LOADED", "site/customization module loaded before bootstrap")
    observed = dict(os.environ)
    if observed != _ALLOWED_ENVIRONMENT:
        unexpected = sorted(set(observed) - set(_ALLOWED_ENVIRONMENT))
        changed = sorted(
            key
            for key in set(observed) & set(_ALLOWED_ENVIRONMENT)
            if observed[key] != _ALLOWED_ENVIRONMENT[key]
        )
        missing = sorted(set(_ALLOWED_ENVIRONMENT) - set(observed))
        _fail(
            "ENVIRONMENT_NOT_ALLOWLISTED",
            f"unexpected={unexpected}, changed={changed}, missing={missing}",
        )
    os.umask(0o077)
    try:
        descriptors = tuple(int(item.name) for item in Path("/proc/self/fd").iterdir())
    except (FileNotFoundError, ValueError):
        descriptors = ()
    for descriptor in descriptors:
        if descriptor > 2:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _load_authority(path: Path) -> tuple[dict[str, Any], str]:
    payload = _read_regular(path)

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                _fail("AUTHORITY_INVALID", f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("AUTHORITY_INVALID", str(exc))
    authority = _strict_object(
        value,
        {
            "allowed_targets",
            "bootstrap_sha256",
            "initial_sys_path",
            "product",
            "python",
            "schema",
            "site_packages",
        },
        label="bootstrap authority",
    )
    if authority["schema"] != _SCHEMA or not _is_sha256(authority["bootstrap_sha256"]):
        _fail("AUTHORITY_INVALID", "bootstrap schema/hash is invalid")
    return authority, hashlib.sha256(payload).hexdigest()


def _verify_bootstrap_identity(authority: dict[str, Any]) -> None:
    source = Path(__file__)
    if source.is_symlink() or not source.is_file():
        _fail("BOOTSTRAP_IDENTITY_MISMATCH", "bootstrap path is absent or a symlink")
    actual = _sha256_file(source)
    if actual != authority["bootstrap_sha256"]:
        _fail("BOOTSTRAP_IDENTITY_MISMATCH", f"actual={actual}")


def _verify_python(authority: dict[str, Any]) -> tuple[Path, Path]:
    expected = _strict_object(
        authority["python"],
        {
            "executable_realpath",
            "executable_sha256",
            "git_executable",
            "git_executable_sha256",
            "pyvenv_cfg_sha256",
            "venv_executable",
        },
        label="python",
    )
    if not all(
        _is_sha256(expected[name])
        for name in ("executable_sha256", "git_executable_sha256", "pyvenv_cfg_sha256")
    ):
        _fail("AUTHORITY_INVALID", "python hash field is malformed")
    executable = Path(sys.executable)
    if str(executable) != expected["venv_executable"]:
        _fail("EXECUTABLE_IDENTITY_MISMATCH", f"sys.executable={sys.executable!r}")
    real_executable = executable.resolve(strict=True)
    if str(real_executable) != expected["executable_realpath"]:
        _fail("EXECUTABLE_IDENTITY_MISMATCH", f"realpath={real_executable}")
    if _sha256_file(real_executable) != expected["executable_sha256"]:
        _fail("EXECUTABLE_IDENTITY_MISMATCH", "executable SHA-256 differs")
    venv_root = executable.parent.parent
    pyvenv = venv_root / "pyvenv.cfg"
    if pyvenv.is_symlink() or _sha256_file(pyvenv) != expected["pyvenv_cfg_sha256"]:
        _fail("EXECUTABLE_IDENTITY_MISMATCH", "pyvenv.cfg identity differs")
    git_executable = Path(expected["git_executable"])
    if (
        not git_executable.is_absolute()
        or git_executable.is_symlink()
        or _sha256_file(git_executable) != expected["git_executable_sha256"]
    ):
        _fail("EXECUTABLE_IDENTITY_MISMATCH", "Git executable identity differs")
    initial_path = authority["initial_sys_path"]
    if (
        not isinstance(initial_path, list)
        or not all(isinstance(item, str) and item for item in initial_path)
        or sys.path != initial_path
    ):
        _fail("STDLIB_PATH_MISMATCH", f"sys.path={sys.path!r}")
    allowed_stdlib = {
        "/usr/lib/python312.zip",
        "/usr/lib/python3.12",
        "/usr/lib/python3.12/lib-dynload",
    }
    if set(initial_path) != allowed_stdlib or len(initial_path) != len(allowed_stdlib):
        _fail("STDLIB_PATH_MISMATCH", f"untrusted stdlib paths {initial_path!r}")
    for item in initial_path:
        path = Path(item)
        if path.exists() and (path.is_symlink() or str(path.resolve()) != item):
            _fail("STDLIB_PATH_MISMATCH", f"symlinked stdlib path {item}")
    return venv_root, git_executable


def _decode_record_hash(value: str) -> bytes:
    algorithm, separator, encoded = value.partition("=")
    if separator != "=" or algorithm != "sha256" or not encoded:
        _fail("DISTRIBUTION_RECORD_MISMATCH", f"unsupported RECORD hash {value!r}")
    try:
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except ValueError as exc:
        _fail("DISTRIBUTION_RECORD_MISMATCH", str(exc))


def _non_cache_files(root: Path) -> set[str]:
    result: set[str] = set()
    if not root.is_dir() or root.is_symlink():
        _fail("SITE_PACKAGES_INVENTORY_MISMATCH", f"invalid distribution root {root}")
    for path in root.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = path.relative_to(root.parent).as_posix()
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink() or not path.is_file():
            _fail("SITE_PACKAGES_INVENTORY_MISMATCH", f"non-regular payload {relative}")
        result.add(relative)
    return result


def _owned_top_level_files(site: Path, relative: str) -> set[str]:
    root = site / relative
    if root.is_symlink():
        _fail("SITE_PACKAGES_INVENTORY_MISMATCH", f"symlinked distribution root {root}")
    if root.is_dir():
        return _non_cache_files(root)
    if root.is_file():
        if "__pycache__" in root.parts or root.suffix in {".pyc", ".pyo"}:
            _fail("SITE_PACKAGES_INVENTORY_MISMATCH", f"cache cannot be a package root {root}")
        return {relative}
    _fail("SITE_PACKAGES_INVENTORY_MISMATCH", f"invalid distribution root {root}")


def _record_payload_path(site: Path, venv_root: Path, relative: str) -> Path:
    """Resolve a RECORD locator, including legitimate ``../../../bin`` scripts."""

    raw = Path(relative)
    if raw.is_absolute() or not relative or raw.as_posix() != relative:
        _fail("DISTRIBUTION_RECORD_MISMATCH", f"unsafe RECORD path {relative!r}")
    lexical = Path(os.path.abspath(site / raw))
    try:
        lexical.relative_to(venv_root)
    except ValueError:
        _fail("DISTRIBUTION_RECORD_MISMATCH", f"RECORD path escapes venv: {relative}")
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        _fail("DISTRIBUTION_RECORD_MISMATCH", f"missing RECORD payload {relative}: {exc}")
    if resolved != lexical:
        _fail("DISTRIBUTION_RECORD_MISMATCH", f"symlinked RECORD payload {relative}")
    return lexical


def _verify_distribution(site: Path, venv_root: Path, value: object) -> dict[str, Any]:
    legacy_fields = {
        "dist_info_relative_path",
        "package_relative_path",
        "payload_file_count",
        "payload_identity",
        "record_relative_path",
        "record_sha256",
    }
    plural_fields = {
        "dist_info_relative_path",
        "package_relative_paths",
        "payload_file_count",
        "payload_identity",
        "record_relative_path",
        "record_sha256",
    }
    if not isinstance(value, dict) or frozenset(value) not in {
        frozenset(legacy_fields),
        frozenset(plural_fields),
    }:
        observed = sorted(value) if isinstance(value, dict) else type(value).__name__
        _fail("AUTHORITY_INVALID", f"distribution fields={observed!r}")
    item = value
    if "package_relative_paths" in item:
        raw_package_paths = item["package_relative_paths"]
        if (
            not isinstance(raw_package_paths, list)
            or not raw_package_paths
            or not all(isinstance(relative, str) for relative in raw_package_paths)
            or len(raw_package_paths) != len(set(raw_package_paths))
        ):
            _fail("AUTHORITY_INVALID", "distribution package paths are invalid")
    else:
        raw_package_paths = [item["package_relative_path"]]
    package_relatives = sorted(
        _safe_relative(relative, label="package path")
        for relative in raw_package_paths
    )
    dist_info_relative = _safe_relative(item["dist_info_relative_path"], label="dist-info path")
    record_relative = _safe_relative(item["record_relative_path"], label="RECORD path")
    if not all(_is_sha256(item[name]) for name in ("payload_identity", "record_sha256")):
        _fail("AUTHORITY_INVALID", "distribution hash is malformed")
    record_path = site / record_relative
    record_bytes = _read_regular(record_path)
    if hashlib.sha256(record_bytes).hexdigest() != item["record_sha256"]:
        _fail("DISTRIBUTION_RECORD_MISMATCH", f"RECORD differs: {record_relative}")
    material: list[dict[str, Any]] = []
    owned_record_paths: set[str] = set()
    try:
        rows = csv.reader(io.StringIO(record_bytes.decode("utf-8"), newline=""))
        for row in rows:
            if len(row) != 3:
                _fail("DISTRIBUTION_RECORD_MISMATCH", "RECORD row does not have three columns")
            relative, encoded_hash, size_text = row
            if "__pycache__" in Path(relative).parts or Path(relative).suffix in {".pyc", ".pyo"}:
                continue
            if any(
                relative == package_relative or relative.startswith(package_relative + "/")
                for package_relative in package_relatives
            ) or relative.startswith(dist_info_relative + "/"):
                # Package/dist-info entries themselves may never traverse.
                _safe_relative(relative, label="owned RECORD path")
                owned_record_paths.add(relative)
            if relative == record_relative and not encoded_hash and not size_text:
                continue
            if not encoded_hash or not size_text.isdigit():
                _fail("DISTRIBUTION_RECORD_MISMATCH", f"unhashed material file {relative}")
            payload = _read_regular(_record_payload_path(site, venv_root, relative))
            expected_digest = _decode_record_hash(encoded_hash)
            digest = hashlib.sha256(payload).digest()
            if digest != expected_digest or len(payload) != int(size_text):
                _fail("DISTRIBUTION_RECORD_MISMATCH", f"payload differs: {relative}")
            material.append(
                {"path": relative, "sha256": digest.hex(), "size_bytes": len(payload)},
            )
    except UnicodeDecodeError as exc:
        _fail("DISTRIBUTION_RECORD_MISMATCH", str(exc))
    actual_paths = _non_cache_files(site / dist_info_relative)
    for package_relative in package_relatives:
        actual_paths |= _owned_top_level_files(site, package_relative)
    if actual_paths != owned_record_paths:
        _fail(
            "SITE_PACKAGES_INVENTORY_MISMATCH",
            (
                f"extra={sorted(actual_paths-owned_record_paths)}, "
                f"missing={sorted(owned_record_paths-actual_paths)}"
            ),
        )
    material.sort(key=lambda row: row["path"])
    identity = _canonical_sha256(material)
    if identity != item["payload_identity"] or len(material) != item["payload_file_count"]:
        _fail("DISTRIBUTION_RECORD_MISMATCH", "material payload identity/count differs")
    return {
        "package_relative_paths": package_relatives,
        "dist_info_relative_path": dist_info_relative,
        "record_sha256": item["record_sha256"],
        "payload_identity": identity,
        "payload_file_count": len(material),
        "payload_files": material,
    }


def _verify_site_packages(
    authority: dict[str, Any],
    venv_root: Path,
) -> tuple[tuple[Path, ...], list[dict[str, Any]], dict[str, str]]:
    raw = authority["site_packages"]
    if isinstance(raw, dict) and set(raw) == {"roots"}:
        root_values = raw["roots"]
        if not isinstance(root_values, list) or not root_values:
            _fail("AUTHORITY_INVALID", "site_packages.roots must not be empty")
        root_fields = {
            "dependency_lock_sha256",
            "distributions",
            "pyvenv_cfg_sha256",
            "root",
            "top_level_entries",
            "venv_root",
        }
        expected_roots = [
            _strict_object(value, root_fields, label=f"site_packages.roots[{index}]")
            for index, value in enumerate(root_values)
        ]
    else:
        expected_roots = [
            _strict_object(
                raw,
                {"dependency_lock_sha256", "distributions", "root", "top_level_entries"},
                label="site_packages",
            ),
        ]

    sites: list[Path] = []
    verified: list[dict[str, Any]] = []
    payload_hashes: dict[str, str] = {}
    package_owners: dict[str, str] = {}
    for index, expected in enumerate(expected_roots):
        if not _is_sha256(expected["dependency_lock_sha256"]):
            _fail("AUTHORITY_INVALID", "dependency lock hash is malformed")
        site = Path(expected["root"])
        selected_venv = (
            venv_root
            if "venv_root" not in expected
            else Path(expected["venv_root"])
        )
        expected_root = selected_venv / "lib" / "python3.12" / "site-packages"
        if (
            site != expected_root
            or selected_venv.is_symlink()
            or site.is_symlink()
            or not site.is_dir()
        ):
            _fail("SITE_PACKAGES_INVENTORY_MISMATCH", f"site root={site}")
        if "pyvenv_cfg_sha256" in expected:
            if (
                not _is_sha256(expected["pyvenv_cfg_sha256"])
                or _sha256_file(selected_venv / "pyvenv.cfg")
                != expected["pyvenv_cfg_sha256"]
            ):
                _fail("EXECUTABLE_IDENTITY_MISMATCH", f"pyvenv.cfg differs: {selected_venv}")
        if index == 0 and selected_venv != venv_root:
            _fail("EXECUTABLE_IDENTITY_MISMATCH", "primary site is not the executing venv")
        top_level = sorted(
            path.name
            for path in site.iterdir()
            if path.name != "__pycache__"
        )
        stdlib_collisions = sorted(
            name for name in top_level if Path(name).stem in sys.stdlib_module_names
        )
        if stdlib_collisions:
            _fail(
                "SITE_PACKAGES_INVENTORY_MISMATCH",
                f"site payload shadows standard library: {stdlib_collisions!r}",
            )
        if expected["top_level_entries"] != top_level:
            _fail("SITE_PACKAGES_INVENTORY_MISMATCH", f"top-level entries={top_level!r}")
        distributions = expected["distributions"]
        if not isinstance(distributions, list) or not distributions:
            _fail("AUTHORITY_INVALID", "at least one distribution identity is required")
        verified_with_files = [
            _verify_distribution(site, selected_venv, value) for value in distributions
        ]
        owned = sorted(
            name
            for item in verified_with_files
            for name in (*item["package_relative_paths"], item["dist_info_relative_path"])
        )
        if owned != top_level:
            _fail(
                "SITE_PACKAGES_INVENTORY_MISMATCH",
                "distribution roots do not own exact site inventory",
            )
        for item in verified_with_files:
            for package in item["package_relative_paths"]:
                prior = package_owners.get(package)
                if prior is not None and package != "pip":
                    _fail(
                        "SITE_PACKAGES_INVENTORY_MISMATCH",
                        f"dependency root is shadowed across verified sites: {package}",
                    )
                package_owners[package] = str(site)
            for row in item["payload_files"]:
                path = str(_record_payload_path(site, selected_venv, row["path"]))
                if path in payload_hashes:
                    _fail("AUTHORITY_INVALID", f"duplicate runtime payload path: {path}")
                payload_hashes[path] = row["sha256"]
            verified.append(
                {
                    **{key: value for key, value in item.items() if key != "payload_files"},
                    "site_packages_root": str(site),
                },
            )
        sites.append(site)
    if len(sites) != len(set(sites)):
        _fail("AUTHORITY_INVALID", "duplicate site-packages root")
    return tuple(sites), verified, payload_hashes


def _git(git: Path, repository: Path, *arguments: str, check: bool = True) -> bytes:
    environment = {
        **_ALLOWED_ENVIRONMENT,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    process = subprocess.run(
        [str(git), "--no-replace-objects", *arguments],
        cwd=repository,
        env=environment,
        check=False,
        capture_output=True,
    )
    if check and process.returncode != 0:
        _fail(
            "PRODUCT_SOURCE_IDENTITY_MISMATCH",
            (process.stderr or process.stdout or b"Git command failed").decode("utf-8", "replace"),
        )
    return process.stdout


def _verify_external_view(
    repository: Path,
    git: Path,
    value: object,
) -> list[dict[str, Any]]:
    """Verify exact untracked isolated copies of external historical files."""

    if not isinstance(value, list):
        _fail("AUTHORITY_INVALID", "external_files must be a list")
    normalized: list[dict[str, Any]] = []
    targets: set[str] = set()
    for raw in value:
        item = _strict_object(
            raw,
            {"sha256", "size_bytes", "source", "target"},
            label="external file",
        )
        target_relative = _safe_relative(item["target"], label="external target")
        target_parts = Path(target_relative).parts
        if target_parts[0] in {".git", "scripts", "src"}:
            _fail("AUTHORITY_INVALID", f"external target overlaps executable namespace: {target_relative}")
        if target_relative in targets:
            _fail("AUTHORITY_INVALID", f"duplicate external target: {target_relative}")
        targets.add(target_relative)
        source_value = item["source"]
        if (
            not isinstance(source_value, str)
            or not Path(source_value).is_absolute()
            or not _is_sha256(item["sha256"])
            or type(item["size_bytes"]) is not int
            or item["size_bytes"] < 0
        ):
            _fail("AUTHORITY_INVALID", f"invalid external file: {target_relative}")
        source = Path(source_value)
        try:
            source_resolved = source.resolve(strict=True)
        except OSError as exc:
            _fail("EXTERNAL_FILE_MISMATCH", f"missing source {source}: {exc}")
        if (
            source.is_symlink()
            or source_resolved != source
            or not source.is_file()
            or source.is_relative_to(repository)
        ):
            _fail("EXTERNAL_FILE_MISMATCH", f"source is not an independent regular file: {source}")
        target = repository / target_relative
        parent = repository
        for part in target_parts[:-1]:
            parent = parent / part
            if parent.is_symlink() or not parent.is_dir():
                _fail("EXTERNAL_FILE_MISMATCH", f"invalid view parent: {parent}")
        try:
            target_resolved = target.resolve(strict=True)
        except OSError as exc:
            _fail("EXTERNAL_FILE_MISMATCH", f"missing view {target_relative}: {exc}")
        if target.is_symlink() or target_resolved != target or not target.is_file():
            _fail("EXTERNAL_FILE_MISMATCH", f"view is not a regular copy: {target_relative}")
        source_digest, source_size = _regular_identity(source)
        target_digest, target_size = _regular_identity(target)
        source_stat = source.stat(follow_symlinks=False)
        target_stat = target.stat(follow_symlinks=False)
        if (
            source_size != item["size_bytes"]
            or source_digest != item["sha256"]
            or target_size != item["size_bytes"]
            or target_digest != item["sha256"]
            or target_stat.st_mode & 0o222
            or (source_stat.st_dev, source_stat.st_ino)
            == (target_stat.st_dev, target_stat.st_ino)
        ):
            _fail("EXTERNAL_FILE_MISMATCH", f"source identity differs: {source}")
        tracked = subprocess.run(
            [str(git), "--no-replace-objects", "ls-files", "--error-unmatch", "--", target_relative],
            cwd=repository,
            env={**_ALLOWED_ENVIRONMENT, "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1"},
            check=False,
            capture_output=True,
        )
        if tracked.returncode == 0:
            _fail("EXTERNAL_FILE_MISMATCH", f"view target is tracked: {target_relative}")
        normalized.append(dict(item))
    normalized.sort(key=lambda item: item["target"])
    return normalized


def _verify_product(
    authority: dict[str, Any],
    repository: Path,
    git: Path,
) -> tuple[dict[str, bytes], dict[str, Any]]:
    product_value = authority["product"]
    base_fields = {
        "package_prefix",
        "repository_identity",
        "source_commit",
        "source_files",
        "source_root",
        "source_tree",
    }
    optional_fields = {"external_files", "mutable_worktree"}
    if (
        not isinstance(product_value, dict)
        or not base_fields.issubset(product_value)
        or set(product_value) - base_fields - optional_fields
    ):
        observed = sorted(product_value) if isinstance(product_value, dict) else type(product_value).__name__
        _fail("AUTHORITY_INVALID", f"product fields={observed!r}")
    expected = product_value
    root = Path(
        _git(git, repository, "rev-parse", "--show-toplevel").decode().strip(),
    ).resolve(strict=True)
    if root != repository or repository.is_symlink():
        _fail("PRODUCT_SOURCE_IDENTITY_MISMATCH", "repository is not the exact Git root")
    origin = _git(git, repository, "remote", "get-url", "origin").decode().strip()
    if origin != expected["repository_identity"]:
        _fail("PRODUCT_SOURCE_IDENTITY_MISMATCH", f"origin={origin!r}")
    commit = expected["source_commit"]
    tree = expected["source_tree"]
    if not (
        isinstance(commit, str)
        and len(commit) == 40
        and isinstance(tree, str)
        and len(tree) == 40
    ):
        _fail("AUTHORITY_INVALID", "source commit/tree is malformed")
    actual_tree = _git(git, repository, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    if actual_tree != tree:
        _fail("PRODUCT_SOURCE_IDENTITY_MISMATCH", "source tree differs")
    ancestor = subprocess.run(
        [str(git), "--no-replace-objects", "merge-base", "--is-ancestor", commit, "HEAD"],
        cwd=repository,
        env={**_ALLOWED_ENVIRONMENT, "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1"},
        check=False,
        capture_output=True,
    ).returncode == 0
    if not ancestor:
        _fail("PRODUCT_SOURCE_IDENTITY_MISMATCH", "source commit is not an ancestor of HEAD")
    external = _verify_external_view(
        repository,
        git,
        expected.get("external_files", []),
    )
    mutable_value = expected.get("mutable_worktree")
    if mutable_value is not None:
        mutable = _strict_object(
            mutable_value,
            {"tracked_files", "untracked_roots"},
            label="mutable_worktree",
        )
        tracked_values = mutable["tracked_files"]
        root_values = mutable["untracked_roots"]
        if (
            not isinstance(tracked_values, list)
            or not isinstance(root_values, list)
            or len(tracked_values) != len(set(tracked_values))
            or len(root_values) != len(set(root_values))
        ):
            _fail("AUTHORITY_INVALID", "mutable worktree lists are invalid")
        allowed_tracked = {
            _safe_relative(item, label="mutable tracked path")
            for item in tracked_values
        }
        allowed_roots = {
            _safe_relative(item, label="mutable untracked root")
            for item in root_values
        }
        if any(
            Path(item).parts[0] in {".git", "scripts", "src"}
            for item in (*allowed_tracked, *allowed_roots)
        ):
            _fail("AUTHORITY_INVALID", "mutable worktree overlaps executable namespace")
        tracked_dirty = {
            item
            for item in _git(git, repository, "diff", "--name-only", "HEAD", "--")
            .decode()
            .splitlines()
            if item
        }
        staged_dirty = {
            item
            for item in _git(git, repository, "diff", "--cached", "--name-only", "--")
            .decode()
            .splitlines()
            if item
        }
        ordinary = {
            item.decode("utf-8")
            for item in _git(
                git,
                repository,
                "ls-files",
                "-z",
                "--others",
                "--exclude-standard",
            ).split(b"\0")
            if item
        }
        expected_external = {item["target"] for item in external}
        unauthorized_tracked = tracked_dirty - allowed_tracked
        unauthorized_untracked = {
            item
            for item in ordinary - expected_external
            if not any(item == root or item.startswith(root + "/") for root in allowed_roots)
        }
        missing_external = expected_external - ordinary
        if staged_dirty or unauthorized_tracked or unauthorized_untracked or missing_external:
            _fail(
                "PRODUCT_SOURCE_IDENTITY_MISMATCH",
                (
                    f"staged={sorted(staged_dirty)}, "
                    f"tracked={sorted(unauthorized_tracked)}, "
                    f"untracked={sorted(unauthorized_untracked)}, "
                    f"missing_external={sorted(missing_external)}"
                ),
            )
    elif external:
        tracked_dirty = _git(git, repository, "diff", "--name-only", "HEAD", "--").decode()
        staged_dirty = _git(git, repository, "diff", "--cached", "--name-only", "--").decode()
        ordinary = set(
            item.decode("utf-8")
            for item in _git(
                git,
                repository,
                "ls-files",
                "-z",
                "--others",
                "--exclude-standard",
            ).split(b"\0")
            if item
        )
        ignored = set(
            item.decode("utf-8")
            for item in _git(
                git,
                repository,
                "ls-files",
                "-z",
                "--others",
                "--ignored",
                "--exclude-standard",
            ).split(b"\0")
            if item
        )
        observed_external = ordinary | ignored
        expected_external = {item["target"] for item in external}
        if tracked_dirty or staged_dirty or observed_external != expected_external:
            _fail(
                "PRODUCT_SOURCE_IDENTITY_MISMATCH",
                (
                    f"tracked={tracked_dirty.splitlines()}, staged={staged_dirty.splitlines()}, "
                    f"extra={sorted(observed_external-expected_external)}, "
                    f"missing={sorted(expected_external-observed_external)}"
                ),
            )
    else:
        dirty = _git(git, repository, "status", "--porcelain=v1", "--untracked-files=all").decode()
        if dirty:
            _fail("PRODUCT_SOURCE_IDENTITY_MISMATCH", "worktree is not clean")
    source_root = _safe_relative(expected["source_root"], label="source root")
    package_prefix = expected["package_prefix"]
    if not isinstance(package_prefix, str) or not package_prefix:
        _fail("AUTHORITY_INVALID", "package_prefix is invalid")
    records = expected["source_files"]
    if not isinstance(records, list) or not records:
        _fail("AUTHORITY_INVALID", "source file inventory is empty")
    source_map: dict[str, bytes] = {}
    normalized: list[dict[str, Any]] = []
    for value in records:
        item = _strict_object(
            value,
            {"mode", "path", "sha256", "size_bytes"},
            label="source file",
        )
        relative = _safe_relative(item["path"], label="source path")
        if item["mode"] not in {"100644", "100755"} or not _is_sha256(item["sha256"]):
            _fail("AUTHORITY_INVALID", f"source identity is malformed: {relative}")
        payload = _git(git, repository, "show", f"{commit}:{relative}")
        current = _git(git, repository, "show", f"HEAD:{relative}")
        worktree_path = repository / relative
        worktree_payload = _read_regular(worktree_path)
        worktree_mode = stat.S_IMODE(worktree_path.stat(follow_symlinks=False).st_mode)
        expected_mode = 0o755 if item["mode"] == "100755" else 0o644
        digest = hashlib.sha256(payload).hexdigest()
        if (
            payload != current
            or payload != worktree_payload
            or digest != item["sha256"]
            or len(payload) != item["size_bytes"]
            or (worktree_mode & 0o111) != (expected_mode & 0o111)
        ):
            _fail("PRODUCT_SOURCE_IDENTITY_MISMATCH", f"source differs: {relative}")
        source_map[relative] = payload
        normalized.append(dict(item))
    if len(source_map) != len(records):
        _fail("AUTHORITY_INVALID", "duplicate source path")
    package_root = f"{source_root}/{package_prefix.replace('.', '/')}"
    listing = _git(git, repository, "ls-tree", "-r", "--name-only", commit, "--", package_root)
    package_python = sorted(
        line for line in listing.decode().splitlines() if line.endswith(".py")
    )
    declared_python = sorted(
        relative
        for relative in source_map
        if relative.startswith(package_root + "/") and relative.endswith(".py")
    )
    if package_python != declared_python:
        _fail("PRODUCT_SOURCE_IDENTITY_MISMATCH", "Python package closure is incomplete")
    normalized.sort(key=lambda row: row["path"])
    return source_map, {
        "repository_identity": origin,
        "source_commit": commit,
        "source_tree": tree,
        "source_inventory_sha256": _canonical_sha256(normalized),
        "source_file_count": len(normalized),
        "source_root": source_root,
        "package_prefix": package_prefix,
        "external_file_count": len(external),
        "external_inventory_sha256": _canonical_sha256(
            [
                {
                    "target": item["target"],
                    "sha256": item["sha256"],
                    "size_bytes": item["size_bytes"],
                }
                for item in external
            ],
        ),
    }


def _install_pinned_loader(
    source_map: dict[str, bytes],
    product: dict[str, Any],
    repository: Path,
) -> Any:
    import importlib.abc
    import importlib.util

    prefix = product["package_prefix"]
    source_root = product["source_root"]
    modules: dict[str, tuple[str, bytes, bool]] = {}
    package_path = prefix.replace(".", "/")
    for relative, payload in source_map.items():
        if not relative.startswith(f"{source_root}/{package_path}/") or not relative.endswith(
            ".py",
        ):
            continue
        tail = relative[len(source_root) + 1 :]
        if tail.endswith("/__init__.py"):
            name = tail[: -len("/__init__.py")].replace("/", ".")
            is_package = True
        else:
            name = tail[:-3].replace("/", ".")
            is_package = False
        modules[name] = (relative, payload, is_package)

    class PinnedLoader(importlib.abc.InspectLoader):
        def __init__(self, fullname: str) -> None:
            self.fullname = fullname

        def is_package(self, fullname: str) -> bool:
            return modules[fullname][2]

        def get_filename(self, fullname: str) -> str:
            return str(repository / modules[fullname][0])

        def get_source(self, fullname: str) -> str:
            return modules[fullname][1].decode("utf-8")

        def create_module(self, spec: Any) -> None:
            return None

        def exec_module(self, module: Any) -> None:
            relative, payload, _package = modules[module.__name__]
            code = compile(payload, str(repository / relative), "exec", dont_inherit=True)
            exec(code, module.__dict__)

    class PinnedFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
            if fullname not in modules:
                return None
            loader = PinnedLoader(fullname)
            return importlib.util.spec_from_loader(
                fullname,
                loader,
                is_package=loader.is_package(fullname),
            )

    finder = PinnedFinder()
    sys.meta_path.insert(0, finder)
    return finder


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _install_state(attestation: dict[str, Any]) -> None:
    if BOOTSTRAP_STATE_MODULE in sys.modules:
        _fail("BOOTSTRAP_STATE_COLLISION", "bootstrap state module already exists")
    module = types.ModuleType(BOOTSTRAP_STATE_MODULE)
    canonical = _canonical_bytes(attestation)
    module.ATTESTATION = _freeze(attestation)
    module.ATTESTATION_JSON = canonical.decode("utf-8")
    module.ATTESTATION_SHA256 = hashlib.sha256(canonical).hexdigest()
    sys.modules[BOOTSTRAP_STATE_MODULE] = module


def _install_verified_site_imports(
    sites: tuple[Path, ...],
    payload_hashes: dict[str, str],
) -> None:
    """Load site sources/extensions only after a just-in-time RECORD check.

    The finder intentionally registers no sourceless bytecode loader, so stale
    or injected ``.pyc``/``.pyo`` files cannot become executable inputs.
    """

    import importlib.machinery

    def verify(filename: str) -> bytes:
        path = Path(filename)
        expected = payload_hashes.get(str(path))
        if expected is None:
            _fail("IMPORT_ORIGIN_MISMATCH", f"unowned site payload {path}")
        payload = _read_regular(path)
        if hashlib.sha256(payload).hexdigest() != expected:
            _fail("DISTRIBUTION_RECORD_MISMATCH", f"payload changed before import: {path}")
        return payload

    class VerifiedSourceLoader(importlib.machinery.SourceFileLoader):
        def get_code(self, fullname: str) -> Any:
            payload = verify(self.get_filename(fullname))
            return self.source_to_code(payload, self.get_filename(fullname))

    class VerifiedExtensionLoader(importlib.machinery.ExtensionFileLoader):
        def create_module(self, spec: Any) -> Any:
            verify(self.get_filename(spec.name))
            return super().create_module(spec)

        def exec_module(self, module: Any) -> None:
            verify(self.get_filename(module.__name__))
            super().exec_module(module)

    loader_details = (
        (VerifiedSourceLoader, importlib.machinery.SOURCE_SUFFIXES),
        (VerifiedExtensionLoader, importlib.machinery.EXTENSION_SUFFIXES),
    )
    file_finder_hook = importlib.machinery.FileFinder.path_hook(*loader_details)

    def verified_site_path_hook(path: str) -> Any:
        absolute = Path(os.path.abspath(path))
        if not any(absolute.is_relative_to(site) for site in sites):
            raise ImportError
        return file_finder_hook(path)

    sys.path_hooks.insert(0, verified_site_path_hook)
    for cached in tuple(sys.path_importer_cache):
        try:
            inside_site = any(
                Path(os.path.abspath(cached)).is_relative_to(site)
                for site in sites
            )
        except (TypeError, ValueError):
            inside_site = False
        if inside_site:
            sys.path_importer_cache.pop(cached, None)


def _verify_target(authority: dict[str, Any], entrypoint: str | None, script: str | None) -> None:
    targets = _strict_object(
        authority["allowed_targets"],
        {"entrypoints", "scripts"},
        label="allowed_targets",
    )
    if not (
        isinstance(targets["entrypoints"], list)
        and all(isinstance(item, str) for item in targets["entrypoints"])
        and isinstance(targets["scripts"], list)
        and all(isinstance(item, str) for item in targets["scripts"])
    ):
        _fail("AUTHORITY_INVALID", "target lists are invalid")
    if entrypoint is not None and entrypoint not in targets["entrypoints"]:
        _fail("TARGET_NOT_AUTHORIZED", entrypoint)
    if script is not None:
        safe_script = _safe_relative(script, label="target script")
        if safe_script not in targets["scripts"]:
            _fail("TARGET_NOT_AUTHORIZED", safe_script)


def _dispatch_entrypoint(target: str, arguments: list[str]) -> int:
    module_name, separator, callable_name = target.partition(":")
    if not separator or not module_name or not callable_name:
        _fail("TARGET_NOT_AUTHORIZED", f"malformed entrypoint {target!r}")
    import importlib

    module = importlib.import_module(module_name)
    origin = getattr(getattr(module, "__spec__", None), "origin", None)
    if not isinstance(origin, str):
        _fail("IMPORT_ORIGIN_MISMATCH", f"missing origin for {module_name}")
    function = getattr(module, callable_name, None)
    if not callable(function):
        _fail("TARGET_NOT_AUTHORIZED", f"callable missing: {target}")
    result = function(arguments)
    return 0 if result is None else int(result)


def _dispatch_script(
    script: str,
    source_map: dict[str, bytes],
    repository: Path,
    arguments: list[str],
) -> int:
    if script not in source_map:
        _fail("PRODUCT_SOURCE_IDENTITY_MISMATCH", f"script absent from closure: {script}")
    namespace = {
        "__builtins__": __builtins__,
        "__file__": str(repository / script),
        "__name__": "__main__",
        "__package__": None,
    }
    prior = sys.argv
    sys.argv = [str(repository / script), *arguments]
    try:
        code = compile(source_map[script], str(repository / script), "exec", dont_inherit=True)
        try:
            exec(code, namespace)
        except SystemExit as exc:
            if exc.code is None:
                return 0
            if isinstance(exc.code, int):
                return exc.code
            return 1
        return 0
    finally:
        sys.argv = prior


def run(arguments: argparse.Namespace) -> int:
    _verify_process_boundary()
    authority_input = Path(arguments.authority)
    repository_input = Path(arguments.repository)
    if authority_input.is_symlink() or repository_input.is_symlink():
        _fail("FILE_IDENTITY_MISMATCH", "authority/repository argument is a symlink")
    authority_path = authority_input.resolve(strict=True)
    repository = repository_input.resolve(strict=True)
    authority, authority_sha256 = _load_authority(authority_path)
    _verify_bootstrap_identity(authority)
    _verify_target(authority, arguments.entrypoint, arguments.script)
    venv_root, git = _verify_python(authority)
    sites, distributions, payload_hashes = _verify_site_packages(authority, venv_root)
    source_map, product = _verify_product(authority, repository, git)
    initial_path = tuple(sys.path)
    _install_verified_site_imports(sites, payload_hashes)
    # Standard-library paths remain authoritative.  The exact, RECORD-verified
    # site root is appended only after startup and stdlib verification.
    sys.path[:] = [*initial_path, *(str(site) for site in sites)]
    finder = _install_pinned_loader(source_map, product, repository)
    attestation = {
        "schema": _ATTESTATION_SCHEMA,
        "authority_sha256": authority_sha256,
        "bootstrap_sha256": authority["bootstrap_sha256"],
        "environment": dict(_ALLOWED_ENVIRONMENT),
        "initial_sys_path": list(initial_path),
        "effective_sys_path": list(sys.path),
        "python_executable": sys.executable,
        "python_executable_realpath": str(Path(sys.executable).resolve()),
        "python_flags": {
            "isolated": sys.flags.isolated,
            "ignore_environment": sys.flags.ignore_environment,
            "no_site": sys.flags.no_site,
            "safe_path": sys.flags.safe_path,
            "dont_write_bytecode": sys.flags.dont_write_bytecode,
            "pycache_prefix": sys.pycache_prefix,
        },
        "site_packages": [str(site) for site in sites],
        "bytecode_cache_policy": "IGNORE_PYC_PYO_AND_VERIFY_RECORD_AT_IMPORT",
        "distributions": distributions,
        "product": product,
        "target": arguments.entrypoint or arguments.script,
    }
    attestation["attestation_identity"] = _canonical_sha256(attestation)
    _install_state(attestation)
    try:
        if arguments.entrypoint is not None:
            return _dispatch_entrypoint(arguments.entrypoint, arguments.target_arguments)
        assert arguments.script is not None
        return _dispatch_script(
            arguments.script,
            source_map,
            repository,
            arguments.target_arguments,
        )
    finally:
        try:
            sys.meta_path.remove(finder)
        except ValueError:
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one target after isolated runtime verification",
    )
    parser.add_argument("--authority", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--entrypoint")
    target.add_argument("--script")
    parser.add_argument("target_arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.target_arguments[:1] == ["--"]:
            arguments.target_arguments = arguments.target_arguments[1:]
        return run(arguments)
    except BootstrapFailure as exc:
        payload = {
            "schema": "isolated-runtime-bootstrap-failure-v1",
            "status": "BLOCKED",
            "failure_code": FAILURE_CODE,
            "reason": exc.reason,
            "detail": exc.detail,
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 120


if __name__ == "__main__":
    raise SystemExit(main())
