"""Fail-closed validation of the locked M0 execution runtime before data loading."""

from __future__ import annotations

import importlib.metadata
import json
import locale as locale_module
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
from crypto_lab.status import FailureCode


T = TypeVar("T")
_PROJECT_DISTRIBUTION = "nautilus-crypto-backtest-lab"


class RuntimeLockMismatch(RuntimeError):
    def __init__(self, code: FailureCode | str, mismatches: list[str]) -> None:
        self.code = str(code)
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
