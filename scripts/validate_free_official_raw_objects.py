#!/usr/bin/env python3
"""Re-hash every immutable raw object bound by a free-official release DB."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import duckdb

from crypto_lab.git_identity import require_repository_root

ALLOWED_HOSTS = {
    "api.github.com",
    "data-api.binance.vision",
    "data.binance.vision",
    "fapi.binance.com",
    "raw.githubusercontent.com",
    "www.binance.com",
}


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    repository = require_repository_root(args.repository)
    database = (
        args.database
        if args.database.is_absolute()
        else repository / args.database
    )
    if database.is_symlink():
        raise ValueError("database must not be a symlink")
    database = database.resolve(strict=True)
    database.relative_to(repository)
    connection = duckdb.connect(
        str(database),
        read_only=True,
        config={
            "allow_unsigned_extensions": "false",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        },
    )
    try:
        objects = connection.execute(
            "SELECT raw_object_sha256, byte_size, local_path FROM raw_objects "
            "ORDER BY raw_object_sha256",
        ).fetchall()
        locators = connection.execute(
            "SELECT DISTINCT exact_locator FROM source_observations ORDER BY exact_locator",
        ).fetchall()
    finally:
        connection.close()

    failures: list[dict[str, object]] = []
    total_bytes = 0
    for expected_sha256, expected_size, local_path in objects:
        path = (repository / str(local_path)).resolve()
        try:
            path.relative_to(repository)
        except ValueError:
            failures.append({"path": str(local_path), "reason": "PATH_ESCAPE"})
            continue
        if not path.is_file():
            failures.append({"path": str(local_path), "reason": "MISSING"})
            continue
        size = path.stat().st_size
        digest = hash_file(path)
        total_bytes += size
        if size != int(expected_size) or digest != expected_sha256:
            failures.append(
                {
                    "path": str(local_path),
                    "reason": "IDENTITY_MISMATCH",
                    "expected_size": int(expected_size),
                    "actual_size": size,
                    "expected_sha256": expected_sha256,
                    "actual_sha256": digest,
                },
            )
    bad_hosts = sorted(
        {
            urlsplit(str(locator)).hostname
            for (locator,) in locators
            if urlsplit(str(locator)).hostname not in ALLOWED_HOSTS
        },
        key=lambda item: "" if item is None else item,
    )
    result = {
        "schema": "free-official-binance-raw-rehash-v1",
        "status": "PASS" if not failures and not bad_hosts else "FAIL",
        "database_path": str(database.relative_to(repository)),
        "raw_object_count": len(objects),
        "raw_byte_occurrences_checked": total_bytes,
        "hash_or_size_failure_count": len(failures),
        "failures": failures,
        "source_locator_count": len(locators),
        "allowed_hosts": sorted(ALLOWED_HOSTS),
        "unauthorized_hosts": bad_hosts,
        "duckdb_version": duckdb.__version__,
        "extensions_loaded": False,
        "network_used": False,
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
