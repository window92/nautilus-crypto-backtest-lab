#!/usr/bin/env python3
"""Build the deterministic authority consumed by the isolated Official bootstrap.

The builder uses only the standard library.  It records the target interpreter,
the complete installed distribution payload described by each ``RECORD``, and
the complete committed ``src/crypto_lab`` Python closure.  It does not import
the Product package or any site-package module.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


ALLOWED_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "TZ": "UTC",
}
PINNED_SCRIPT_TARGETS = (
    "scripts/run_m3_child.py",
    "scripts/run_m3_qualifications.py",
)
_TOOLCHAIN_DISTRIBUTIONS = frozenset({"pip"})


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _locked_distributions(path: Path) -> dict[str, str]:
    payload = path.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    for match in re.finditer(
        r"(?m)^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s\\]+)",
        payload,
    ):
        name = _normalized_distribution_name(match.group(1))
        version = match.group(2)
        if name in result:
            raise ValueError(f"duplicate dependency lock distribution: {name}")
        result[name] = version
    if not result:
        raise ValueError(f"dependency lock contains no exact distributions: {path}")
    return result


def _installed_distribution_identity(dist_info: Path) -> tuple[str, str]:
    metadata = (dist_info / "METADATA").read_text(encoding="utf-8")
    fields: dict[str, str] = {}
    for line in metadata.splitlines():
        key, separator, value = line.partition(":")
        if separator and key in {"Name", "Version"} and key not in fields:
            fields[key] = value.strip()
    if set(fields) != {"Name", "Version"} or not all(fields.values()):
        raise ValueError(f"distribution METADATA lacks exact Name/Version: {dist_info}")
    return _normalized_distribution_name(fields["Name"]), fields["Version"]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _canonical_sha256(value: object) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _run(arguments: list[str], *, cwd: Path, environment: dict[str, str] | None = None) -> bytes:
    process = subprocess.run(
        arguments,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
    )
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).decode("utf-8", "replace").strip()
        raise RuntimeError(detail or f"command failed with {process.returncode}: {arguments!r}")
    return process.stdout


def _git(repository: Path, *arguments: str) -> bytes:
    git = shutil.which("git") or "/usr/bin/git"
    return _run(
        [git, "--no-replace-objects", *arguments],
        cwd=repository,
        environment={
            **ALLOWED_ENVIRONMENT,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
        },
    )


def _require_repository_root(repository: Path) -> Path:
    if not isinstance(repository, Path):
        raise TypeError("repository must be pathlib.Path")
    if not repository.is_absolute():
        raise ValueError("repository must be absolute")
    lexical = Path(os.path.abspath(repository))
    if lexical != repository:
        raise ValueError("repository must be an exact normalized absolute path")
    cursor = Path(lexical.anchor)
    for component in lexical.parts[1:]:
        cursor /= component
        if cursor.is_symlink():
            raise ValueError("repository path must not contain a symlink")
    if not lexical.is_dir():
        raise ValueError("repository does not exist")
    ssot = lexical / "SSOT.md"
    if ssot.is_symlink() or not ssot.is_file():
        raise ValueError("repository is not the Product repository")
    actual = Path(
        _git(lexical, "rev-parse", "--show-toplevel").decode().strip(),
    )
    if actual != lexical:
        raise ValueError("repository is not the exact Git root")
    return lexical


def _decode_record_hash(value: str) -> bytes:
    algorithm, separator, encoded = value.partition("=")
    if separator != "=" or algorithm != "sha256" or not encoded:
        raise ValueError(f"unsupported RECORD hash {value!r}")
    return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))


def _record_payload_path(site: Path, venv_root: Path, relative: str) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or not relative or raw.as_posix() != relative:
        raise ValueError(f"unsafe RECORD path {relative!r}")
    lexical = Path(os.path.abspath(site / raw))
    lexical.relative_to(venv_root)
    resolved = lexical.resolve(strict=True)
    if resolved != lexical or lexical.is_symlink() or not lexical.is_file():
        raise ValueError(f"RECORD payload is not an exact regular file: {relative}")
    return lexical


def _distribution(site: Path, venv_root: Path, dist_info: Path) -> dict[str, Any]:
    record = dist_info / "RECORD"
    record_bytes = record.read_bytes()
    rows = list(csv.reader(io.StringIO(record_bytes.decode("utf-8"), newline="")))
    if any(len(row) != 3 for row in rows):
        raise ValueError(f"invalid RECORD row shape: {record}")
    dist_relative = dist_info.relative_to(site).as_posix()
    package_roots = sorted(
        {
            Path(relative).parts[0]
            for relative, _encoded, _size in rows
            if relative
            and not relative.startswith("../")
            and Path(relative).parts[0] != dist_relative
            and "__pycache__" not in Path(relative).parts
            and Path(relative).suffix not in {".pyc", ".pyo"}
        },
    )
    if not package_roots:
        raise ValueError(f"distribution owns no import/package root: {dist_info}")
    material: list[dict[str, Any]] = []
    record_relative = f"{dist_relative}/RECORD"
    for relative, encoded, size_text in rows:
        path = Path(relative)
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if relative == record_relative and not encoded and not size_text:
            continue
        if not encoded or not size_text.isdigit():
            raise ValueError(f"unhashed material RECORD entry: {relative}")
        payload_path = _record_payload_path(site, venv_root, relative)
        payload = payload_path.read_bytes()
        digest = hashlib.sha256(payload).digest()
        if digest != _decode_record_hash(encoded) or len(payload) != int(size_text):
            raise ValueError(f"RECORD payload mismatch: {relative}")
        material.append(
            {"path": relative, "sha256": digest.hex(), "size_bytes": len(payload)},
        )
    material.sort(key=lambda item: item["path"])
    return {
        "dist_info_relative_path": dist_relative,
        "package_relative_paths": package_roots,
        "payload_file_count": len(material),
        "payload_identity": _canonical_sha256(material),
        "record_relative_path": record_relative,
        "record_sha256": _sha256_bytes(record_bytes),
    }


def _source_files(repository: Path, commit: str) -> list[dict[str, Any]]:
    names = sorted(
        line
        for line in _git(
            repository,
            "ls-tree",
            "-r",
            "--name-only",
            commit,
            "--",
            "src/crypto_lab",
            *PINNED_SCRIPT_TARGETS,
        ).decode().splitlines()
        if line.endswith(".py")
    )
    if not names:
        raise ValueError("committed Product Python closure is empty")
    records: list[dict[str, Any]] = []
    for relative in names:
        payload = _git(repository, "show", f"{commit}:{relative}")
        listing = _git(repository, "ls-tree", commit, "--", relative).decode().strip()
        mode = listing.split(maxsplit=1)[0]
        if mode not in {"100644", "100755"}:
            raise ValueError(f"unsupported source mode {mode}: {relative}")
        records.append(
            {
                "mode": mode,
                "path": relative,
                "sha256": _sha256_bytes(payload),
                "size_bytes": len(payload),
            },
        )
    return records


def _site_packages_authority(
    python: Path,
    dependency_lock_path: Path,
) -> dict[str, Any]:
    python = Path(os.path.abspath(python))
    venv_root = python.parent.parent
    site = venv_root / "lib/python3.12/site-packages"
    if venv_root.is_symlink() or not site.is_dir() or site.is_symlink():
        raise ValueError(f"invalid site-packages root: {site}")
    dependency_lock_path = dependency_lock_path.resolve(strict=True)
    locked = _locked_distributions(dependency_lock_path)
    top_level = sorted(path.name for path in site.iterdir() if path.name != "__pycache__")
    dist_info_paths = sorted(site.glob("*.dist-info"), key=lambda item: item.name)
    observed: dict[str, str] = {}
    for path in dist_info_paths:
        name, version = _installed_distribution_identity(path)
        if name in observed:
            raise ValueError(f"duplicate installed distribution: {name}")
        observed[name] = version
    execution_observed = {
        name: version
        for name, version in observed.items()
        if name not in _TOOLCHAIN_DISTRIBUTIONS
    }
    if execution_observed != locked:
        raise ValueError(
            "installed execution distributions differ from dependency lock: "
            f"installed={execution_observed!r}, locked={locked!r}",
        )
    distributions = [
        _distribution(site, venv_root, path)
        for path in dist_info_paths
    ]
    owned = sorted(
        value
        for item in distributions
        for value in (*item["package_relative_paths"], item["dist_info_relative_path"])
    )
    if owned != top_level:
        raise ValueError(f"distribution ownership does not equal site inventory: {top_level!r}")
    return {
        "dependency_lock_sha256": _sha256_file(dependency_lock_path),
        "distributions": distributions,
        "pyvenv_cfg_sha256": _sha256_file(venv_root / "pyvenv.cfg"),
        "root": str(site),
        "top_level_entries": top_level,
        "venv_root": str(venv_root),
    }


def build_authority(
    *,
    repository: Path,
    python: Path,
    source_commit: str,
    dependency_lock_path: Path | None = None,
    additional_runtimes: tuple[tuple[Path, Path], ...] = (),
) -> dict[str, Any]:
    repository = _require_repository_root(repository)
    source_commit = _git(
        repository,
        "rev-parse",
        f"{source_commit}^{{commit}}",
    ).decode().strip()
    python = Path(os.path.abspath(python))
    if python.is_symlink():
        real_python = python.resolve(strict=True)
    else:
        real_python = python.resolve(strict=True)
    venv_root = python.parent.parent
    initial = _run(
        [
            str(python),
            "-I",
            "-P",
            "-S",
            "-B",
            "-X",
            "pycache_prefix=/dev/null",
            "-c",
            "import json,sys;print(json.dumps(sys.path,separators=(',',':')))",
        ],
        cwd=repository,
        environment=ALLOWED_ENVIRONMENT,
    )
    initial_sys_path = json.loads(initial)
    dependency_lock_path = Path(
        dependency_lock_path or repository / "requirements.lock.txt",
    ).resolve(strict=True)
    primary_site = _site_packages_authority(python, dependency_lock_path)
    extra_sites = [
        _site_packages_authority(Path(extra_python), Path(extra_lock))
        for extra_python, extra_lock in additional_runtimes
    ]
    site_packages: dict[str, Any]
    if extra_sites:
        site_packages = {"roots": [primary_site, *extra_sites]}
    else:
        site_packages = {
            key: value
            for key, value in primary_site.items()
            if key not in {"pyvenv_cfg_sha256", "venv_root"}
        }
    git_path = Path(shutil.which("git") or "/usr/bin/git").resolve(strict=True)
    tree = _git(repository, "rev-parse", f"{source_commit}^{{tree}}").decode().strip()
    origin = _git(repository, "remote", "get-url", "origin").decode().strip()
    bootstrap = repository / "scripts/isolated_runtime_bootstrap.py"
    authority = {
        "schema": "isolated-runtime-bootstrap-authority-v1",
        "bootstrap_sha256": _sha256_file(bootstrap),
        "initial_sys_path": initial_sys_path,
        "python": {
            "executable_realpath": str(real_python),
            "executable_sha256": _sha256_file(real_python),
            "git_executable": str(git_path),
            "git_executable_sha256": _sha256_file(git_path),
            "pyvenv_cfg_sha256": _sha256_file(venv_root / "pyvenv.cfg"),
            "venv_executable": str(python),
        },
        "site_packages": site_packages,
        "product": {
            "package_prefix": "crypto_lab",
            "repository_identity": origin,
            "source_commit": source_commit,
            "source_files": _source_files(repository, source_commit),
            "source_root": "src",
            "source_tree": tree,
            "mutable_worktree": {
                "tracked_files": [
                    "research/history_anchors.jsonl",
                    "research/holdout_lock.json",
                    "research/trials.jsonl",
                ],
                "untracked_roots": [".owner-runtime", "runs"],
            },
        },
        "allowed_targets": {
            "entrypoints": ["crypto_lab.owner:main"],
            "scripts": list(PINNED_SCRIPT_TARGETS),
        },
    }
    return authority


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--dependency-lock", type=Path)
    parser.add_argument("--additional-python", type=Path, action="append", default=[])
    parser.add_argument(
        "--additional-dependency-lock",
        type=Path,
        action="append",
        default=[],
    )
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if len(arguments.additional_python) != len(arguments.additional_dependency_lock):
        parser.error("each --additional-python requires one --additional-dependency-lock")
    authority = build_authority(
        repository=arguments.repository,
        python=arguments.python,
        source_commit=arguments.source_commit,
        dependency_lock_path=arguments.dependency_lock,
        additional_runtimes=tuple(
            zip(
                arguments.additional_python,
                arguments.additional_dependency_lock,
                strict=True,
            ),
        ),
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(_canonical_bytes(authority) + b"\n")
    print(
        json.dumps(
            {
                "authority_sha256": _sha256_file(arguments.output),
                "product_source_commit": authority["product"]["source_commit"],
                "source_file_count": len(authority["product"]["source_files"]),
                "distribution_count": sum(
                    len(root["distributions"])
                    for root in authority["site_packages"].get(
                        "roots",
                        [authority["site_packages"]],
                    )
                ),
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
