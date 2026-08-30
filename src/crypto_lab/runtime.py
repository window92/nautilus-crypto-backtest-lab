"""Fail-closed validation of the locked M0 execution runtime before data loading."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import importlib.metadata
import io
import json
import locale as locale_module
import marshal
import os
import platform as platform_module
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import unquote, urlparse

from crypto_lab.config import NAUTILUS_SOURCE_COMMIT
from crypto_lab.config import NAUTILUS_SOURCE_REPOSITORY
from crypto_lab.config import NAUTILUS_VERSION
from crypto_lab.config import NAUTILUS_WHEEL_FILENAME
from crypto_lab.config import NAUTILUS_WHEEL_SHA256
from crypto_lab.config import RuntimeLock
from crypto_lab.hashing import sha256_file
from crypto_lab.hashing import canonical_sha256
from crypto_lab.status import FailureCode


T = TypeVar("T")
_PROJECT_DISTRIBUTION = "nautilus-crypto-backtest-lab"
_GENERATED_DIST_INFO_FILES = frozenset({"INSTALLER", "REQUESTED", "direct_url.json"})


class RuntimeLockMismatch(RuntimeError):
    def __init__(self, code: FailureCode | str, mismatches: list[str]) -> None:
        try:
            self.code = FailureCode(code).value
        except ValueError as exc:
            raise ValueError(f"unknown SSOT failure code: {code!r}") from exc
        self.mismatches = tuple(mismatches)
        super().__init__(f"{self.code}: " + "; ".join(mismatches))


def _normalized_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked_versions(path: Path) -> dict[str, str]:
    logical_lines: list[str] = []
    pending = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            pending += stripped[:-1].strip() + " "
            continue
        logical_lines.append((pending + stripped).strip())
        pending = ""
    if pending:
        raise RuntimeLockMismatch(
            FailureCode.RUNTIME_LOCK_MISMATCH,
            ["dependency lock ends with an incomplete continuation"],
        )

    versions: dict[str, str] = {}
    for line in logical_lines:
        if line.startswith("--"):
            continue
        requirement = line.split(maxsplit=1)[0]
        if "==" not in requirement:
            raise RuntimeLockMismatch(
                FailureCode.RUNTIME_LOCK_MISMATCH,
                [f"dependency is not exactly pinned: {requirement}"],
            )
        name, version = requirement.split("==", maxsplit=1)
        versions[_normalized_distribution_name(name)] = version
    return versions


def _installed_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            normalized = _normalized_distribution_name(name)
            if normalized == _PROJECT_DISTRIBUTION:
                continue
            versions[normalized] = distribution.version
    return versions


def _direct_wheel_identity() -> tuple[str | None, str | None, str | None, Path | None]:
    distribution = importlib.metadata.distribution("nautilus_trader")
    raw = distribution.read_text("direct_url.json")
    if raw is None:
        return None, None, None, None
    document = json.loads(raw)
    url = document.get("url")
    hashes = document.get("archive_info", {}).get("hashes", {})
    digest = hashes.get("sha256")
    if not isinstance(url, str):
        return None, digest, None, None
    parsed = urlparse(url)
    filename = Path(unquote(parsed.path)).name
    local_path = Path(unquote(parsed.path)) if parsed.scheme == "file" else None
    return filename, digest, url, local_path


def _record_hash_bytes(value: str) -> bytes:
    algorithm, separator, encoded = value.partition("=")
    if separator != "=" or algorithm != "sha256" or not encoded:
        raise RuntimeLockMismatch(
            FailureCode.RUNTIME_LOCK_MISMATCH,
            [f"unsupported installed RECORD hash {value!r}"],
        )
    try:
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except Exception as exc:
        raise RuntimeLockMismatch(
            FailureCode.RUNTIME_LOCK_MISMATCH,
            [f"malformed installed RECORD hash {value!r}"],
        ) from exc


def _safe_record_path(site_packages_root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if not relative or candidate.is_absolute() or ".." in candidate.parts or "\\" in relative:
        raise RuntimeLockMismatch(
            FailureCode.RUNTIME_LOCK_MISMATCH,
            [f"unsafe installed RECORD path {relative!r}"],
        )
    root = site_packages_root.resolve()
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise RuntimeLockMismatch(
            FailureCode.RUNTIME_LOCK_MISMATCH,
            [f"installed RECORD path escapes site-packages: {relative!r}"],
        )
    return resolved


def _cache_verification_error(
    cache_path: Path,
    *,
    site_packages_root: Path,
    recorded_paths: set[str],
) -> str | None:
    """Return an error unless a cache is exactly compiled from hashed source."""

    try:
        source_path = Path(importlib.util.source_from_cache(str(cache_path))).resolve()
        source_relative = source_path.relative_to(site_packages_root).as_posix()
    except Exception:
        return f"installed cache has no safe source mapping: {cache_path.name}"
    if source_relative not in recorded_paths or not source_path.is_file() or source_path.is_symlink():
        return f"installed cache source is not a recorded regular file: {source_relative}"
    try:
        source_bytes = source_path.read_bytes()
        cache_bytes = cache_path.read_bytes()
        if len(cache_bytes) < 16 or cache_bytes[:4] != importlib.util.MAGIC_NUMBER:
            return f"installed cache header is invalid: {cache_path.name}"
        flags = int.from_bytes(cache_bytes[4:8], "little")
        if flags not in {0, 1, 3}:
            return f"installed cache flags are invalid: {cache_path.name}"
        if flags & 1:
            if cache_bytes[8:16] != importlib.util.source_hash(source_bytes):
                return f"installed cache source hash is invalid: {cache_path.name}"
        else:
            expected_mtime = int(source_path.stat().st_mtime) & 0xFFFFFFFF
            expected_size = len(source_bytes) & 0xFFFFFFFF
            if (
                int.from_bytes(cache_bytes[8:12], "little") != expected_mtime
                or int.from_bytes(cache_bytes[12:16], "little") != expected_size
            ):
                return f"installed cache source metadata is invalid: {cache_path.name}"
        optimization = 0
        if ".opt-" in cache_path.name:
            optimization = int(cache_path.name.rsplit(".opt-", maxsplit=1)[1].split(".", maxsplit=1)[0])
        expected_code = compile(
            source_bytes,
            str(source_path),
            "exec",
            dont_inherit=True,
            optimize=optimization,
        )
        if cache_bytes[16:] != marshal.dumps(expected_code):
            return f"installed cache bytecode differs from recorded source: {cache_path.name}"
    except Exception as exc:
        return f"installed cache verification failed for {cache_path.name}: {exc}"
    return None


def inspect_installed_distribution_files(
    *,
    site_packages_root: Path,
    record_relative_path: str,
    package_relative_path: str,
) -> dict[str, Any]:
    """Verify every installed RECORD entry and reject unrecorded payload files.

    ``__pycache__/*.pyc`` is the only unrecorded-file exception, and every such
    cache is recompiled and compared with its hashed source before acceptance.
    Wheel payload identity omits
    pip-owned INSTALLER/REQUESTED/direct_url.json and RECORD itself, whose
    content may legitimately vary with the installation location.
    """

    root = Path(site_packages_root).resolve()
    record_path = _safe_record_path(root, record_relative_path)
    if not record_path.is_file() or record_path.is_symlink():
        raise RuntimeLockMismatch(
            FailureCode.RUNTIME_LOCK_MISMATCH,
            ["installed distribution RECORD is missing or is a symlink"],
        )
    try:
        rows = list(csv.reader(io.StringIO(record_path.read_text(encoding="utf-8"))))
    except Exception as exc:
        raise RuntimeLockMismatch(
            FailureCode.RUNTIME_LOCK_MISMATCH,
            [f"installed distribution RECORD is unreadable: {exc}"],
        ) from exc
    if not rows or any(len(row) != 3 for row in rows):
        raise RuntimeLockMismatch(
            FailureCode.RUNTIME_LOCK_MISMATCH,
            ["installed distribution RECORD is empty or malformed"],
        )

    record_parent = Path(record_relative_path).parent
    recorded_hashed_paths = {
        relative
        for relative, digest_text, size_text in rows
        if digest_text and size_text
    }
    seen: set[str] = set()
    payload: list[dict[str, Any]] = []
    verified_hashed = 0
    allowed_cache_files = 0
    native_extensions = 0
    mismatches: list[str] = []
    for relative, digest_text, size_text in rows:
        if relative in seen:
            mismatches.append(f"duplicate installed RECORD path {relative!r}")
            continue
        seen.add(relative)
        try:
            path = _safe_record_path(root, relative)
        except RuntimeLockMismatch as exc:
            mismatches.extend(exc.mismatches)
            continue
        is_cache = "__pycache__" in Path(relative).parts and relative.endswith(".pyc")
        if not path.exists():
            if is_cache and not digest_text and not size_text:
                continue
            mismatches.append(f"installed RECORD file is missing: {relative}")
            continue
        if path.is_symlink() or not path.is_file():
            mismatches.append(f"installed RECORD path is not a regular file: {relative}")
            continue
        if not digest_text or not size_text:
            if relative == record_relative_path:
                continue
            if is_cache:
                cache_error = _cache_verification_error(
                    path,
                    site_packages_root=root,
                    recorded_paths=recorded_hashed_paths,
                )
                if cache_error is None:
                    allowed_cache_files += 1
                else:
                    mismatches.append(cache_error)
                continue
            mismatches.append(f"installed RECORD entry lacks hash/size: {relative}")
            continue
        try:
            expected_digest = _record_hash_bytes(digest_text)
            expected_size = int(size_text)
        except (RuntimeLockMismatch, ValueError) as exc:
            mismatches.extend(
                exc.mismatches
                if isinstance(exc, RuntimeLockMismatch)
                else [f"invalid installed RECORD size for {relative}"],
            )
            continue
        actual_size = path.stat().st_size
        actual_digest = hashlib.sha256(path.read_bytes()).digest()
        if actual_size != expected_size:
            mismatches.append(
                f"installed file size differs from RECORD: {relative} expected={expected_size} actual={actual_size}",
            )
        if actual_digest != expected_digest:
            mismatches.append(f"installed file hash differs from RECORD: {relative}")
        verified_hashed += 1
        if relative.endswith((".so", ".pyd", ".dylib")):
            native_extensions += 1
        generated = (
            Path(relative).parent == record_parent
            and Path(relative).name in _GENERATED_DIST_INFO_FILES
        )
        if not generated:
            payload.append(
                {
                    "path": relative,
                    "sha256": expected_digest.hex(),
                    "size": expected_size,
                },
            )

    package_root = _safe_record_path(root, package_relative_path)
    dist_info_root = _safe_record_path(root, str(record_parent))
    for scan_root in (package_root, dist_info_root):
        if not scan_root.is_dir() or scan_root.is_symlink():
            mismatches.append(f"installed payload root is missing or unsafe: {scan_root.name}")
            continue
        for path in scan_root.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                mismatches.append(f"unexpected installed symlink: {relative}")
            elif path.is_file() and relative not in seen:
                is_cache = "__pycache__" in path.parts and path.suffix == ".pyc"
                if is_cache:
                    cache_error = _cache_verification_error(
                        path,
                        site_packages_root=root,
                        recorded_paths=recorded_hashed_paths,
                    )
                    if cache_error is None:
                        allowed_cache_files += 1
                    else:
                        mismatches.append(cache_error)
                else:
                    mismatches.append(f"unrecorded installed payload file: {relative}")

    if native_extensions == 0:
        mismatches.append("installed Nautilus payload has no hashed native extension")
    if mismatches:
        raise RuntimeLockMismatch(FailureCode.RUNTIME_LOCK_MISMATCH, mismatches)
    ordered_payload = sorted(payload, key=lambda item: item["path"])
    return {
        "installed_record_sha256": sha256_file(record_path),
        "installed_payload_sha256": canonical_sha256(ordered_payload),
        "installed_payload_file_count": len(ordered_payload),
        "installed_record_hashed_file_count": verified_hashed,
        "installed_native_extension_count": native_extensions,
        "allowed_cache_file_count": allowed_cache_files,
        "cache_files_recompiled_and_verified": True,
        "installed_files_verified": True,
    }


def verify_installed_distribution_files(
    *,
    site_packages_root: Path,
    record_relative_path: str,
    package_relative_path: str,
    expected_payload_sha256: str,
    expected_payload_file_count: int,
) -> dict[str, Any]:
    """Verify installed bytes against both RECORD and an immutable lock identity."""

    evidence = inspect_installed_distribution_files(
        site_packages_root=site_packages_root,
        record_relative_path=record_relative_path,
        package_relative_path=package_relative_path,
    )
    mismatches: list[str] = []
    if evidence["installed_payload_sha256"] != expected_payload_sha256:
        mismatches.append("installed payload identity differs from runtime lock")
    if evidence["installed_payload_file_count"] != expected_payload_file_count:
        mismatches.append("installed payload file count differs from runtime lock")
    if mismatches:
        raise RuntimeLockMismatch(FailureCode.RUNTIME_LOCK_MISMATCH, mismatches)
    return evidence


def _inspect_installed_nautilus_files() -> dict[str, Any]:
    distribution = importlib.metadata.distribution("nautilus_trader")
    site_packages = Path(distribution.locate_file("")).resolve()
    candidates = sorted(site_packages.glob("nautilus_trader-*.dist-info/RECORD"))
    if len(candidates) != 1:
        raise RuntimeLockMismatch(
            FailureCode.RUNTIME_LOCK_MISMATCH,
            [f"expected one installed Nautilus RECORD, found {len(candidates)}"],
        )
    return inspect_installed_distribution_files(
        site_packages_root=site_packages,
        record_relative_path=candidates[0].relative_to(site_packages).as_posix(),
        package_relative_path="nautilus_trader",
    )


def collect_runtime_identity(
    *,
    dependency_lock_path: Path,
) -> dict[str, Any]:
    if hasattr(time, "tzset"):
        time.tzset()
    wheel_filename, wheel_sha256, wheel_url, wheel_path = _direct_wheel_identity()
    libc_name, libc_version = platform_module.libc_ver()
    lock_versions = _locked_versions(dependency_lock_path)
    installed_versions = _installed_versions()
    installed_files = _inspect_installed_nautilus_files()
    # The CPython venv bootstrap owns pip installation. Its exact version is
    # independently locked by runtime.lock.json and included in the complete
    # installed-distribution comparison here.
    lock_versions["pip"] = importlib.metadata.version("pip")
    evidence: dict[str, Any] = {
        "nautilus_version": importlib.metadata.version("nautilus_trader"),
        "installed_wheel_filename": wheel_filename,
        "installed_wheel_sha256": wheel_sha256,
        "installed_wheel_url": wheel_url,
        "python_implementation": platform_module.python_implementation(),
        "python_version": platform_module.python_version(),
        "python_abi": f"cp{sys.version_info.major}{sys.version_info.minor}",
        "platform": platform_module.platform(),
        "machine_architecture": platform_module.machine(),
        "libc_name": libc_name,
        "glibc_version": libc_version,
        "dependency_lock_sha256": sha256_file(dependency_lock_path),
        "dependency_versions": lock_versions,
        "installed_distributions": installed_versions,
        "pip_version": importlib.metadata.version("pip"),
        "timezone": os.environ.get("TZ"),
        "effective_timezone": list(time.tzname),
        "locale": os.environ.get("LC_ALL"),
        "effective_locale": locale_module.setlocale(locale_module.LC_ALL, None),
        **installed_files,
    }
    if wheel_path is not None and wheel_path.is_file():
        evidence["wheel_file_present"] = True
        evidence["wheel_file_size_bytes"] = wheel_path.stat().st_size
        evidence["wheel_file_sha256"] = sha256_file(wheel_path)
    else:
        evidence["wheel_file_present"] = False
    return evidence


def _glibc_at_least(actual: str, minimum: tuple[int, int]) -> bool:
    try:
        parts = tuple(int(part) for part in actual.split(".")[:2])
    except ValueError:
        return False
    return len(parts) == 2 and parts >= minimum


def verify_runtime_lock(
    lock: RuntimeLock,
    *,
    dependency_lock_path: Path,
) -> dict[str, Any]:
    current = collect_runtime_identity(
        dependency_lock_path=dependency_lock_path,
    )
    mismatches: list[str] = []
    wheel_mismatches: list[str] = []

    required_lock_values = {
        "nautilus_version": NAUTILUS_VERSION,
        "nautilus_source_repository": NAUTILUS_SOURCE_REPOSITORY,
        "nautilus_source_commit": NAUTILUS_SOURCE_COMMIT,
        "nautilus_wheel_filename": NAUTILUS_WHEEL_FILENAME,
        "nautilus_wheel_sha256": NAUTILUS_WHEEL_SHA256,
        "python_implementation": "CPython",
        "python_abi": "cp312",
        "machine_architecture": "x86_64",
        "timezone": "UTC",
        "locale": "C.UTF-8",
        "nautilus_provenance_status": "VERIFIED_SLSA_SOURCE_COMMIT",
        "runtime_implementation": "official Rust/PyO3 public Python API",
    }
    for field, expected in required_lock_values.items():
        actual = getattr(lock, field)
        if actual != expected:
            target = wheel_mismatches if field in {
                "nautilus_wheel_filename",
                "nautilus_wheel_sha256",
            } else mismatches
            target.append(f"runtime.lock {field}={actual!r}, expected {expected!r}")

    current_pairs = {
        "nautilus_version": lock.nautilus_version,
        "installed_wheel_filename": lock.nautilus_wheel_filename,
        "installed_wheel_sha256": lock.nautilus_wheel_sha256,
        "python_implementation": lock.python_implementation,
        "python_version": lock.python_version,
        "python_abi": lock.python_abi,
        "platform": lock.platform,
        "machine_architecture": lock.machine_architecture,
        "glibc_version": lock.glibc_version,
        "dependency_lock_sha256": lock.dependency_lock_sha256,
        "pip_version": lock.pip_version,
        "timezone": lock.timezone,
        "locale": lock.locale,
        "installed_payload_sha256": lock.nautilus_installed_payload_sha256,
        "installed_payload_file_count": lock.nautilus_installed_payload_file_count,
    }
    for field, expected in current_pairs.items():
        actual = current[field]
        if actual != expected:
            target = wheel_mismatches if field in {
                "installed_wheel_filename",
                "installed_wheel_sha256",
            } else mismatches
            target.append(f"current {field}={actual!r}, lock={expected!r}")

    if current["libc_name"] != "glibc" or not _glibc_at_least(current["glibc_version"], (2, 34)):
        mismatches.append(
            f"host libc={current['libc_name']} {current['glibc_version']}, requires glibc >=2.34",
        )
    if current["dependency_versions"] != current["installed_distributions"]:
        mismatches.append("installed distribution set differs from requirements.lock.txt")
    if lock.dependencies != current["dependency_versions"]:
        mismatches.append("runtime.lock dependencies differ from requirements.lock.txt")
    if current.get("wheel_file_present"):
        if current["wheel_file_sha256"] != lock.nautilus_wheel_sha256:
            wheel_mismatches.append("downloaded wheel bytes differ from runtime.lock SHA-256")
        if current["wheel_file_size_bytes"] != lock.nautilus_wheel_size_bytes:
            wheel_mismatches.append("downloaded wheel size differs from runtime.lock")

    all_mismatches = wheel_mismatches + mismatches
    if all_mismatches:
        code = (
            FailureCode.RUNTIME_WHEEL_HASH_MISMATCH
            if wheel_mismatches
            else FailureCode.RUNTIME_LOCK_MISMATCH
        )
        raise RuntimeLockMismatch(code, all_mismatches)
    return current


def validate_persisted_runtime_identity(
    lock: RuntimeLock,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Validate a Run's observed installed-runtime proof against its frozen lock."""

    required = {
        "installed_files_verified": True,
        "cache_files_recompiled_and_verified": True,
        "installed_payload_sha256": lock.nautilus_installed_payload_sha256,
        "installed_payload_file_count": lock.nautilus_installed_payload_file_count,
        "installed_wheel_filename": lock.nautilus_wheel_filename,
        "installed_wheel_sha256": lock.nautilus_wheel_sha256,
        "nautilus_version": lock.nautilus_version,
        "python_version": lock.python_version,
        "python_implementation": lock.python_implementation,
        "python_abi": lock.python_abi,
        "platform": lock.platform,
        "machine_architecture": lock.machine_architecture,
        "glibc_version": lock.glibc_version,
        "dependency_lock_sha256": lock.dependency_lock_sha256,
        "dependency_versions": lock.dependencies,
        "installed_distributions": lock.dependencies,
        "pip_version": lock.pip_version,
        "timezone": lock.timezone,
        "locale": lock.locale,
    }
    mismatches = [
        name
        for name, expected in required.items()
        if evidence.get(name) != expected
    ]
    record_sha = evidence.get("installed_record_sha256")
    if (
        not isinstance(record_sha, str)
        or len(record_sha) != 64
        or any(character not in "0123456789abcdef" for character in record_sha)
    ):
        mismatches.append("installed_record_sha256")
    for name in (
        "installed_record_hashed_file_count",
        "installed_native_extension_count",
    ):
        value = evidence.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            mismatches.append(name)
    cache_count = evidence.get("allowed_cache_file_count")
    if not isinstance(cache_count, int) or isinstance(cache_count, bool) or cache_count < 0:
        mismatches.append("allowed_cache_file_count")
    wheel_present = evidence.get("wheel_file_present")
    if not isinstance(wheel_present, bool):
        mismatches.append("wheel_file_present")
    elif wheel_present and (
        evidence.get("wheel_file_sha256") != lock.nautilus_wheel_sha256
        or evidence.get("wheel_file_size_bytes") != lock.nautilus_wheel_size_bytes
    ):
        mismatches.append("wheel_file_identity")
    if mismatches:
        raise RuntimeLockMismatch(
            FailureCode.RUNTIME_LOCK_MISMATCH,
            [f"persisted runtime identity mismatch: {name}" for name in dict.fromkeys(mismatches)],
        )
    return evidence


def run_after_runtime_preflight(
    lock: RuntimeLock,
    *,
    dependency_lock_path: Path,
    operation: Callable[[], T],
) -> T:
    """Run an operation only after the M0 execution-runtime gate passes."""

    verify_runtime_lock(
        lock,
        dependency_lock_path=dependency_lock_path,
    )
    return operation()
