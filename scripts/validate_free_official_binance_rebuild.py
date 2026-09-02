#!/usr/bin/env python3
"""Compare two free-official builds and materialize verified release artifacts."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import shutil
import stat
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import duckdb

from crypto_lab.config import MarketProfile
from crypto_lab.data import DatasetRawInventory
from crypto_lab.data import DatasetRelease
from crypto_lab.data import assert_official_active_raw_inventory
from crypto_lab.git_identity import require_repository_root
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256


EXPECTED_DUCKDB_VERSION = "1.4.5"
SPOT_PROFILE = MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY.value
PERP_PROFILE = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def write_once_or_verify(path: Path, value: Any) -> None:
    payload = canonical_json_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"immutable artifact collision: {path}")
        return
    descriptor, temporary = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        candidate = Path(temporary)
        if candidate.exists():
            candidate.unlink()


def configure_database(path: Path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(
        str(path),
        read_only=True,
        config={
            "allow_unsigned_extensions": "false",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        },
    )


def compare_build_results(primary: dict[str, Any], rebuilt: dict[str, Any]) -> dict[str, Any]:
    exact_keys = (
        "status",
        "schema_identity",
        "source_inventory_identity",
        "semantic_database_identity",
        "semantic_export_contract",
        "pre_manifest_table_hashes",
        "readonly_table_hashes",
        "row_counts",
        "readonly_row_counts",
        "dataset_release_ids",
        "releases",
        "catalogs",
        "spot",
        "perpetual",
        "funding",
        "window_contracts",
        "data_tool_lock_identity",
        "network_used_during_build",
        "strategy_run",
        "official_trial",
        "source_precision_audit",
        "instrument_metadata_identities",
        "market_state_acceptance",
        "superseded_dataset_releases",
        "full_raw_inventory_gate",
    )
    mismatches = [key for key in exact_keys if primary.get(key) != rebuilt.get(key)]
    if mismatches:
        raise RuntimeError(f"DETERMINISTIC_REBUILD_MISMATCH: {mismatches}")
    if primary["status"] != "PASS" or primary["readonly_reopen"] != "PASS":
        raise RuntimeError("primary build did not pass read-only validation")
    if rebuilt["status"] != "PASS" or rebuilt["readonly_reopen"] != "PASS":
        raise RuntimeError("independent build did not pass read-only validation")
    return {
        "status": "PASS",
        "exact_keys_compared": list(exact_keys),
        "semantic_database_identity": primary["semantic_database_identity"],
        "dataset_release_ids": primary["dataset_release_ids"],
        "catalog_identities": sorted(
            item["catalog_identity"] for item in primary["catalogs"].values()
        ),
        "physical_file_hashes_equal": (
            primary["database_file_sha256"] == rebuilt["database_file_sha256"]
        ),
        "primary_file_sha256": primary["database_file_sha256"],
        "independent_file_sha256": rebuilt["database_file_sha256"],
        "primary_size_bytes": primary["database_size_bytes"],
        "independent_size_bytes": rebuilt["database_size_bytes"],
    }


def _add_relational_source_hashes(
    connection: duckdb.DuckDBPyConnection,
    hashes: set[str],
    table: str,
) -> None:
    for (payload,) in connection.execute(
        f"SELECT DISTINCT source_sha256s_json FROM {table}",
    ).fetchall():
        values = json.loads(payload)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise RuntimeError(f"DATA_HASH_MISMATCH: malformed source inventory in {table}")
        hashes.update(values)


def independent_participation_projection(
    connection: duckdb.DuckDBPyConnection,
    profile: str,
) -> set[str]:
    """Derive Raw membership without consuming builder result fields."""

    hashes: set[str] = set()
    if profile == SPOT_PROFILE:
        _add_relational_source_hashes(connection, hashes, "spot_execution_bars_1m")
        hashes.update(
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT source_raw_object_sha256 FROM spot_agg_trades",
            ).fetchall()
        )
        for first, second in connection.execute(
            """
            SELECT DISTINCT raw_trade_source_sha256, aggtrade_source_sha256
            FROM verified_no_trade_intervals
            """,
        ).fetchall():
            hashes.update((first, second))
    else:
        _add_relational_source_hashes(connection, hashes, "perpetual_execution_bars_1m")
        _add_relational_source_hashes(connection, hashes, "perpetual_mark_bars_1m")
        _add_relational_source_hashes(connection, hashes, "perpetual_funding_events")
        hashes.update(
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT raw_object_sha256 FROM source_observations
                WHERE parsed_event_time_ms IS NULL AND semantic_row_sha256 IS NULL
                  AND validation_status = 'UNAVAILABLE'
                  AND delivery_classification = 'REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE'
                  AND source_role LIKE '%MARK%' AND instrument = 'BTCUSDT'
                """,
            ).fetchall()
        )
    observation_map = dict(
        connection.execute(
            "SELECT observation_id, raw_object_sha256 FROM source_observations",
        ).fetchall(),
    )
    for (payload,) in connection.execute(
        "SELECT source_observation_ids_json FROM source_conflicts WHERE market_profile = ?",
        [profile],
    ).fetchall():
        values = json.loads(payload)
        if not isinstance(values, list) or any(item not in observation_map for item in values):
            raise RuntimeError("DATA_HASH_MISMATCH: conflict observation projection is unresolved")
        hashes.update(observation_map[item] for item in values)
    hashes.update(
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT binding.source_raw_object_sha256
            FROM instrument_metadata_source_bindings AS binding
            JOIN instrument_metadata AS metadata
              ON metadata.instrument_metadata_identity = binding.instrument_metadata_identity
            WHERE metadata.market_profile = ?
            """,
            [profile],
        ).fetchall()
    )
    for archive_sha256, checksum_sha256 in connection.execute(
        "SELECT archive_raw_object_sha256, checksum_raw_object_sha256 FROM publisher_checksums",
    ).fetchall():
        if archive_sha256 in hashes:
            hashes.add(checksum_sha256)
    return hashes


def validate_full_raw_inventories(
    connection: duckdb.DuckDBPyConnection,
    *,
    repository_root: Path,
) -> list[dict[str, Any]]:
    """Independently validate release/member/semantic/blob four-way equality."""

    raw_rows = {
        row[0]: (int(row[1]), str(row[2]), bool(row[3]))
        for row in connection.execute(
            "SELECT raw_object_sha256, byte_size, local_path, content_verified FROM raw_objects",
        ).fetchall()
    }
    results: list[dict[str, Any]] = []
    union_declared: set[str] = set()
    for row in connection.execute(
        """
        SELECT dataset_release_id, market_profile, raw_inventory_identity,
               raw_inventory_object_count, semantic_release_json
        FROM dataset_releases ORDER BY market_profile
        """,
    ).fetchall():
        release_id, profile, inventory_identity, inventory_count, semantic_json = row
        release = DatasetRelease.from_json_bytes(str(semantic_json).encode("utf-8"))
        inventory = release.raw_inventory
        if (
            release.dataset_release_id != release_id
            or not isinstance(inventory, DatasetRawInventory)
            or inventory.raw_inventory_identity != inventory_identity
            or inventory.raw_object_count != int(inventory_count)
        ):
            raise RuntimeError(
                "DATASET_RAW_INVENTORY_MISMATCH: DatasetRelease Raw inventory DB binding differs",
            )
        declared = {item.raw_object_sha256 for item in inventory.raw_objects}
        members = {
            item[0]
            for item in connection.execute(
                """
                SELECT member_identity FROM release_members
                WHERE dataset_release_id = ? AND member_type = 'RAW_OBJECT'
                """,
                [release_id],
            ).fetchall()
        }
        projected = independent_participation_projection(connection, profile)
        verified: set[str] = set()
        for digest in sorted(projected):
            database_binding = raw_rows.get(digest)
            if database_binding is None:
                continue
            expected_size, local_path, content_verified = database_binding
            source = Path(local_path)
            if not source.is_absolute():
                source = repository_root / source
            if (
                content_verified
                and source.is_file()
                and not source.is_symlink()
                and source.stat().st_size == expected_size
                and hash_file(source) == digest
            ):
                verified.add(digest)
        if not declared == members == projected == verified:
            raise RuntimeError(
                "DATASET_RAW_INVENTORY_MISMATCH: "
                "release/member/participation/blob inventory differs "
                f"declared={len(declared)} members={len(members)} "
                f"projected={len(projected)} verified={len(verified)}",
            )
        union_declared.update(declared)
        inventory_by_hash = {item.raw_object_sha256: item for item in inventory.raw_objects}
        if any(
            item.byte_size != raw_rows[digest][0]
            for digest, item in inventory_by_hash.items()
        ):
            raise RuntimeError("DATA_HASH_MISMATCH: inventory byte size differs")
        acquisition_rows: dict[str, list[tuple[Any, ...]]] = {}
        for origin in connection.execute(
            """
            SELECT raw_object_sha256, observation_id, source_role, exact_locator,
                   exact_query_json, http_status, validation_status,
                   coalesce(delivery_classification, 'NOT_APPLICABLE')
            FROM source_observations
            WHERE parsed_event_time_ms IS NULL AND semantic_row_sha256 IS NULL
            ORDER BY raw_object_sha256, source_role, exact_locator, exact_query_json, observation_id
            """,
        ).fetchall():
            acquisition_rows.setdefault(origin[0], []).append(tuple(origin[1:]))
        for digest, item in inventory_by_hash.items():
            declared_origins = [
                (
                    origin.observation_id,
                    origin.source_role,
                    origin.exact_locator,
                    origin.exact_query_json,
                    origin.http_status,
                    origin.validation_status,
                    origin.delivery_classification,
                )
                for origin in item.origins
            ]
            if declared_origins != acquisition_rows.get(digest, []):
                raise RuntimeError("DATA_SOURCE_INVALID: inventory acquisition origins differ")
        declared_checksums = {
            (
                item.raw_object_sha256,
                binding.checksum_raw_object_sha256,
                binding.exact_filename,
                binding.publisher_sha256,
            )
            for item in inventory.raw_objects
            for binding in item.publisher_checksum_bindings
        }
        database_checksums = {
            tuple(item)
            for item in connection.execute(
                """
                SELECT archive_raw_object_sha256, checksum_raw_object_sha256,
                       exact_filename, publisher_sha256 FROM publisher_checksums
                """,
            ).fetchall()
            if item[0] in declared
        }
        if declared_checksums != database_checksums:
            raise RuntimeError("DATA_SOURCE_INVALID: inventory publisher checksums differ")
        results.append(
            {
                "market_profile": profile,
                "dataset_release_id": release_id,
                "raw_inventory_identity": inventory_identity,
                "raw_object_count": len(declared),
                "four_way_equality": True,
            },
        )
    extra_checksums = [
        row
        for row in connection.execute(
            """
            SELECT archive_raw_object_sha256, checksum_raw_object_sha256
            FROM publisher_checksums
            """,
        ).fetchall()
        if row[0] not in union_declared or row[1] not in union_declared
    ]
    assert_official_active_raw_inventory(set(raw_rows), union_declared, extra_checksums)
    return results


def database_gate(path: Path, *, repository_root: Path) -> dict[str, Any]:
    connection = configure_database(path)
    try:
        table_count = int(
            connection.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'main'",
            ).fetchone()[0],
        )
        double_columns = int(
            connection.execute(
                "SELECT count(*) FROM duckdb_columns() "
                "WHERE internal = false AND data_type IN ('DOUBLE', 'FLOAT', 'REAL')",
            ).fetchone()[0],
        )
        blockers = int(
            connection.execute(
                "SELECT count(*) FROM minute_dispositions WHERE blocking",
            ).fetchone()[0],
        )
        unresolved_conflicts = int(
            connection.execute(
                "SELECT count(*) FROM source_conflicts WHERE status = 'UNRESOLVED_BLOCKING'",
            ).fetchone()[0],
        )
        failed_validations = int(
            connection.execute(
                "SELECT count(*) FROM validation_results WHERE status <> 'PASS'",
            ).fetchone()[0],
        )
        bad_checksums = int(
            connection.execute(
                "SELECT count(*) FROM publisher_checksums WHERE NOT local_match",
            ).fetchone()[0],
        )
        unverified_raw = int(
            connection.execute(
                "SELECT count(*) FROM raw_objects WHERE NOT content_verified",
            ).fetchone()[0],
        )
        disposition_counts = {
            f"{profile}:{disposition}": int(count)
            for profile, disposition, count in connection.execute(
                "SELECT market_profile, disposition, count(*) FROM minute_dispositions "
                "GROUP BY market_profile, disposition ORDER BY market_profile, disposition",
            ).fetchall()
        }
        release_statuses = connection.execute(
            "SELECT market_profile, dataset_release_id, status FROM dataset_releases "
            "ORDER BY market_profile",
        ).fetchall()
        mark_grid = connection.execute(
            "SELECT count(*), count(DISTINCT open_time_ns), min(open_time_ns), max(open_time_ns) "
            "FROM perpetual_mark_bars_1m",
        ).fetchone()
        mark_gap_count = int(
            connection.execute(
                "SELECT count(*) FROM ("
                "SELECT open_time_ns - lag(open_time_ns) OVER (ORDER BY open_time_ns) AS delta "
                "FROM perpetual_mark_bars_1m"
                ") WHERE delta IS NOT NULL AND delta <> 60000000000",
            ).fetchone()[0],
        )
        orphan_no_trade = int(
            connection.execute(
                "SELECT count(*) FROM verified_no_trade_intervals n "
                "LEFT JOIN minute_dispositions d ON d.instrument_id = n.instrument_id "
                "AND d.open_time_ns = n.start_ns "
                "WHERE d.disposition IS DISTINCT FROM 'VERIFIED_NO_TRADE_INTERVAL'",
            ).fetchone()[0],
        )
        bar_during_no_trade = int(
            connection.execute(
                "SELECT count(*) FROM minute_dispositions d "
                "JOIN spot_execution_bars_1m b ON b.instrument_id = d.instrument_id "
                "AND b.open_time_ns = d.open_time_ns "
                "WHERE d.disposition = 'VERIFIED_NO_TRADE_INTERVAL'",
            ).fetchone()[0],
        )
        redundant_404_observations, redundant_404_objects = (
            connection.execute(
                "SELECT count(*), count(DISTINCT exact_locator) FROM source_observations "
                "WHERE http_status = 404 "
                "AND delivery_classification = 'REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE'",
            ).fetchone()
        )
        source_binding_orphans = int(
            connection.execute(
                "SELECT count(*) FROM release_members m LEFT JOIN raw_objects r "
                "ON r.raw_object_sha256 = m.source_raw_object_sha256 "
                "WHERE m.source_raw_object_sha256 IS NOT NULL AND r.raw_object_sha256 IS NULL",
            ).fetchone()[0],
        )
        acceptance_rows = connection.execute(
            "SELECT market_profile, expected_executable_bars, "
            "accepted_executable_bars, expected_mark_updates, accepted_mark_updates, "
            "precision_skipped_bars, rejected_precision_events, missing_market_state "
            "FROM nautilus_market_state_acceptance ORDER BY market_profile",
        ).fetchall()
        supersessions = connection.execute(
            "SELECT superseded_dataset_release_id, replacement_dataset_release_id, "
            "classification, defect_reason "
            "FROM dataset_release_supersessions ORDER BY superseded_dataset_release_id",
        ).fetchall()
        instrument_source_binding_count = int(
            connection.execute(
                "SELECT count(*) FROM instrument_metadata_source_bindings",
            ).fetchone()[0],
        )
        full_raw_inventory_results = validate_full_raw_inventories(
            connection,
            repository_root=repository_root,
        )
    finally:
        connection.close()
    expected_dispositions = {
        f"{SPOT_PROFILE}:DERIVED_FROM_OFFICIAL_TRADES": 1,
        f"{SPOT_PROFILE}:REAL_OFFICIAL_BAR": 304_595,
        f"{SPOT_PROFILE}:VERIFIED_NO_TRADE_INTERVAL": 684,
        f"{PERP_PROFILE}:REAL_OFFICIAL_BAR": 305_280,
    }
    if (
        table_count != 21
        or double_columns
        or blockers
        or unresolved_conflicts
        or failed_validations
        or bad_checksums
        or unverified_raw
        or disposition_counts != expected_dispositions
        or len(release_statuses) != 2
        or any(row[2] != "PASS" for row in release_statuses)
        or tuple(mark_grid[:2]) != (305_280, 305_280)
        or mark_gap_count
        or orphan_no_trade
        or bar_during_no_trade
        or int(redundant_404_objects) != 50
        or source_binding_orphans
        or len(acceptance_rows) != 2
        or any(
            int(row[1]) != int(row[2])
            or int(row[3]) != int(row[4])
            or any(int(value) != 0 for value in row[5:])
            for row in acceptance_rows
        )
        or len(supersessions) != 2
        or any(
            row[2] != "SUPERSEDED_INSTRUMENT_REPRESENTATION_INCOMPATIBLE_WITH_PINNED_NAUTILUS"
            or row[3] != "INSTRUMENT_REPRESENTATION_PREVENTED_EXECUTABLE_MARKET_STATE"
            for row in supersessions
        )
        or instrument_source_binding_count != 6
        or len(full_raw_inventory_results) != 2
    ):
        raise RuntimeError("read-only DuckDB acceptance gate failed")
    return {
        "status": "PASS",
        "table_count": table_count,
        "binary_float_financial_column_count": double_columns,
        "blocking_minute_count": blockers,
        "unresolved_conflict_count": unresolved_conflicts,
        "failed_validation_count": failed_validations,
        "checksum_failure_count": bad_checksums,
        "unverified_raw_object_count": unverified_raw,
        "disposition_counts": disposition_counts,
        "release_statuses": [list(row) for row in release_statuses],
        "mark_row_count": int(mark_grid[0]),
        "mark_distinct_minute_count": int(mark_grid[1]),
        "mark_min_open_time_ns": int(mark_grid[2]),
        "mark_max_open_time_ns": int(mark_grid[3]),
        "mark_grid_gap_count": mark_gap_count,
        "bar_during_verified_no_trade_count": bar_during_no_trade,
        "verified_no_trade_orphan_count": orphan_no_trade,
        "redundant_daily_mark_404_observation_count": int(redundant_404_observations),
        "redundant_daily_mark_404_unique_delivery_object_count": int(redundant_404_objects),
        "release_source_binding_orphan_count": source_binding_orphans,
        "nautilus_market_state_acceptance": [list(row) for row in acceptance_rows],
        "dataset_release_supersessions": [list(row) for row in supersessions],
        "instrument_metadata_source_binding_count": instrument_source_binding_count,
        "full_raw_inventory_results": full_raw_inventory_results,
    }


def directory_inventory(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": str(path.relative_to(root)),
            "size_bytes": path.stat().st_size,
            "sha256": hash_file(path),
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    ]


def preserve_file(source: Path, target: Path) -> None:
    digest = hash_file(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.stat().st_size != source.stat().st_size or hash_file(target) != digest:
            raise RuntimeError(f"content-addressed file collision: {target}")
        return
    try:
        os.link(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.copyfile(source, target)
    if hash_file(target) != digest:
        raise RuntimeError(f"materialized file hash mismatch: {target}")


def preserve_raw_independent_copy(source: Path, target: Path) -> None:
    """Atomically materialize Raw bytes without sharing an inode with the corpus."""

    if source.is_symlink() or not source.is_file():
        raise RuntimeError(f"Raw source must be a regular non-symlink file: {source}")
    digest = hash_file(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if (
            target.is_symlink()
            or not target.is_file()
            or target.stat().st_size != source.stat().st_size
            or hash_file(target) != digest
        ):
            raise RuntimeError(f"content-addressed Raw collision: {target}")
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
            for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                output_stream.write(chunk)
            output_stream.flush()
            os.fsync(output_stream.fileno())
            os.fchmod(output_stream.fileno(), stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            os.fsync(output_stream.fileno())
        os.replace(temporary, target)
        directory_descriptor = os.open(
            target.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if temporary.exists():
            temporary.unlink()
    if (
        target.is_symlink()
        or not target.is_file()
        or target.stat().st_size != source.stat().st_size
        or hash_file(target) != digest
        or (target.stat().st_dev, target.stat().st_ino)
        == (source.stat().st_dev, source.stat().st_ino)
    ):
        raise RuntimeError(f"independent Raw materialization failed: {target}")


def materialize_catalog(
    source: Path,
    target: Path,
    *,
    repository_root: Path,
) -> dict[str, Any]:
    source_inventory = directory_inventory(source)
    if target.exists():
        if directory_inventory(target) != source_inventory:
            raise RuntimeError(f"content-addressed catalog collision: {target}")
    else:
        target.mkdir(parents=True)
        for item in source_inventory:
            preserve_file(source / item["path"], target / item["path"])
    return {
        "path": str(target.relative_to(repository_root)),
        "physical_inventory_identity": canonical_sha256(source_inventory),
        "file_count": len(source_inventory),
        "size_bytes": sum(item["size_bytes"] for item in source_inventory),
    }


def materialize_releases(
    *,
    primary: dict[str, Any],
    database_path: Path,
    primary_catalog_root: Path,
    artifact_root: Path,
    repository_root: Path,
) -> dict[str, Any]:
    data_root = repository_root / "data"
    connection = configure_database(database_path)
    try:
        raw_paths = {
            digest: repository_root / local_path
            for digest, local_path in connection.execute(
                "SELECT raw_object_sha256, local_path FROM raw_objects",
            ).fetchall()
        }
    finally:
        connection.close()
    result: dict[str, Any] = {}
    for profile, release_material in sorted(primary["releases"].items()):
        release = DatasetRelease.from_json_bytes(canonical_json_bytes(release_material))
        profile_dir = "spot" if profile == SPOT_PROFILE else "perpetual"
        catalog = materialize_catalog(
            primary_catalog_root / profile_dir,
            data_root / "catalog" / release.catalog_identity,
            repository_root=repository_root,
        )
        for suffix in (
            f"{release.dataset_release_id}.json",
            f"{release.instrument_metadata_identity}.metadata.json",
            f"{release.derived_validation_identity}.market-state.json",
        ):
            preserve_file(artifact_root / suffix, data_root / "releases" / suffix)
        if profile == PERP_PROFILE:
            suffix = f"{release.funding_data_identity}.funding.json"
            preserve_file(artifact_root / suffix, data_root / "releases" / suffix)
        if not isinstance(release.raw_inventory, DatasetRawInventory):
            raise RuntimeError("typed full Raw inventory is required for materialization")
        for raw_object in release.raw_inventory.raw_objects:
            preserve_raw_independent_copy(
                raw_paths[raw_object.raw_object_sha256],
                data_root
                / "raw/sha256"
                / raw_object.raw_object_sha256[:2]
                / f"{raw_object.raw_object_sha256}.blob",
            )
        frozen = DatasetRelease.from_json_bytes(
            (data_root / "releases" / f"{release.dataset_release_id}.json").read_bytes(),
        )
        if frozen != release:
            raise RuntimeError("frozen DatasetRelease bytes differ from rebuilt release")
        result[profile] = {
            "dataset_release_id": release.dataset_release_id,
            "catalog_identity": release.catalog_identity,
            "catalog": catalog,
            "source_object_count": len(release.source_objects),
            "raw_inventory_identity": release.raw_inventory.raw_inventory_identity,
            "raw_inventory_object_count": release.raw_inventory.raw_object_count,
        }
    return result


def compare_execution_rows(
    connection: duckdb.DuckDBPyConnection,
    inventory: dict[str, Any],
    *,
    table: str,
) -> dict[str, Any]:
    projections = sorted(inventory["execution_bars"], key=lambda item: item["ts_event"])
    rows = connection.execute(
        f"SELECT available_at_ns, open_value, high_value, low_value, close_value, "
        f"{'base_volume_value' if table == 'spot_execution_bars_1m' else 'volume_value'} "
        f"FROM {table} ORDER BY open_time_ns",
    ).fetchall()
    if len(rows) != len(projections):
        raise RuntimeError(f"Nautilus execution inventory count mismatch for {table}")
    digest = hashlib.sha256()
    for row, item in zip(rows, projections, strict=True):
        expected = (
            int(row[0]),
            Decimal(row[1]),
            Decimal(row[2]),
            Decimal(row[3]),
            Decimal(row[4]),
            Decimal(row[5]),
        )
        actual = (
            int(item["ts_event"]),
            Decimal(item["open"]),
            Decimal(item["high"]),
            Decimal(item["low"]),
            Decimal(item["close"]),
            Decimal(item["volume"]),
        )
        if expected != actual or int(item["ts_init"]) != expected[0]:
            raise RuntimeError(f"Nautilus execution semantic mismatch for {table}")
        digest.update(canonical_json_bytes(list(actual)) + b"\n")
    return {"row_count": len(rows), "semantic_projection_sha256": digest.hexdigest()}


def compare_mark_rows(
    connection: duckdb.DuckDBPyConnection,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    projections = sorted(inventory["mark_price_updates"], key=lambda item: item["ts_event"])
    rows = connection.execute(
        "SELECT available_at_ns, close_value FROM perpetual_mark_bars_1m ORDER BY open_time_ns",
    ).fetchall()
    if len(rows) != len(projections):
        raise RuntimeError("Nautilus Mark inventory count mismatch")
    for row, item in zip(rows, projections, strict=True):
        if (
            int(row[0]) != int(item["ts_event"])
            or int(row[0]) != int(item["ts_init"])
            or Decimal(row[1]) != Decimal(item["value"])
            or item["instrument_id"] != "BTCUSDT-PERP.BINANCE"
        ):
            raise RuntimeError("Nautilus Mark semantic mismatch")
    return {
        "source_mark_bar_count": len(rows),
        "mark_price_update_count": len(projections),
        "binding": "ORIGINAL_OFFICIAL_MARK_BAR_CLOSE_AT_COMPLETION",
        "mark_reconstructed": False,
        "substitution_used": False,
    }


def compare_funding_rows(
    connection: duckdb.DuckDBPyConnection,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    projections = inventory["funding_rate_updates"]
    rows = connection.execute(
        "SELECT funding_time_ns, funding_interval_hours, funding_rate_value "
        "FROM perpetual_funding_events ORDER BY funding_time_ns",
    ).fetchall()
    if len(projections) != len(rows) * 2:
        raise RuntimeError("locked RC2 funding adapter cardinality mismatch")
    for index, row in enumerate(rows):
        pair = projections[index * 2:index * 2 + 2]
        for item in pair:
            if (
                int(item["ts_event"]) != int(row[0])
                or int(item["ts_init"]) != int(row[0])
                or int(item["interval"]) != int(row[1]) * 60
                or Decimal(item["rate"]) != Decimal(row[2])
                or int(item["next_funding_ns"]) != int(row[0])
            ):
                raise RuntimeError("Nautilus funding semantic mismatch")
    return {
        "official_source_event_count": len(rows),
        "runtime_update_count": len(projections),
        "native_binding": "NAUTILUS_2_0_0RC2_EXPLICIT_SOURCE_BOUNDARY_PAIR",
        "schedule_invented": False,
    }


def resolve_and_compare_catalogs(
    primary: dict[str, Any],
    database_path: Path,
    *,
    repository_root: Path,
) -> dict[str, Any]:
    data_root = repository_root / "data"
    connection = configure_database(database_path)
    result: dict[str, Any] = {}
    try:
        for profile, release_material in sorted(primary["releases"].items()):
            release = DatasetRelease.from_json_bytes(canonical_json_bytes(release_material))
            resolved = release.resolve_runtime_data(data_root)
            actual_identity = canonical_sha256(resolved.semantic_inventory)
            if actual_identity != release.catalog_identity:
                raise RuntimeError("fresh-process catalog identity mismatch")
            execution = compare_execution_rows(
                connection,
                resolved.semantic_inventory,
                table=(
                    "spot_execution_bars_1m"
                    if profile == SPOT_PROFILE
                    else "perpetual_execution_bars_1m"
                ),
            )
            profile_result = {
                "status": "PASS",
                "catalog_identity": actual_identity,
                "execution": execution,
                "release_time_range": release.normalized_time_range.to_builtins(),
                "instrument_id": release.instrument_id,
                "market_state_acceptance": resolved.market_state_acceptance,
            }
            if profile == SPOT_PROFILE:
                if resolved.semantic_inventory["mark_price_updates"] or resolved.semantic_inventory["funding_rate_updates"]:
                    raise RuntimeError("Spot catalog contains derivative roles")
                profile_result["verified_no_trade_exported_as_bar_count"] = 0
            else:
                profile_result["mark"] = compare_mark_rows(connection, resolved.semantic_inventory)
                profile_result["funding"] = compare_funding_rows(connection, resolved.semantic_inventory)
            result[profile] = profile_result
    finally:
        connection.close()
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--primary-result", type=Path, required=True)
    parser.add_argument("--independent-result", type=Path, required=True)
    parser.add_argument("--primary-catalog-root", type=Path, required=True)
    parser.add_argument("--independent-catalog-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def rooted(repository: Path, path: Path) -> Path:
    return path if path.is_absolute() else repository / path


def main() -> int:
    arguments = parse_args()
    repository = require_repository_root(arguments.repository)
    primary_result_path = rooted(repository, arguments.primary_result)
    independent_result_path = rooted(repository, arguments.independent_result)
    primary_catalog_root = rooted(repository, arguments.primary_catalog_root)
    independent_catalog_root = rooted(repository, arguments.independent_catalog_root)
    artifact_root = rooted(repository, arguments.artifact_root)
    output = rooted(repository, arguments.output)
    if duckdb.__version__ != EXPECTED_DUCKDB_VERSION:
        raise RuntimeError("DuckDB identity mismatch")
    primary = load_json(primary_result_path)
    rebuilt = load_json(independent_result_path)
    comparison = compare_build_results(primary, rebuilt)
    primary_database = repository / primary["database_path"]
    rebuilt_database = repository / rebuilt["database_path"]
    if hash_file(primary_database) != primary["database_file_sha256"]:
        raise RuntimeError("primary physical database identity changed")
    if hash_file(rebuilt_database) != rebuilt["database_file_sha256"]:
        raise RuntimeError("independent physical database identity changed")
    primary_gate = database_gate(primary_database, repository_root=repository)
    independent_gate = database_gate(rebuilt_database, repository_root=repository)
    if primary_gate != independent_gate:
        raise RuntimeError("independent read-only validation result mismatch")

    catalog_physical_comparison: dict[str, Any] = {}
    for profile, result in sorted(primary["catalogs"].items()):
        directory = "spot" if profile == SPOT_PROFILE else "perpetual"
        first = directory_inventory(primary_catalog_root / directory)
        second = directory_inventory(independent_catalog_root / directory)
        catalog_physical_comparison[profile] = {
            "primary_inventory_identity": canonical_sha256(first),
            "independent_inventory_identity": canonical_sha256(second),
            "physical_inventories_equal": first == second,
            "catalog_identity": result["catalog_identity"],
        }

    materialized = materialize_releases(
        primary=primary,
        database_path=primary_database,
        primary_catalog_root=primary_catalog_root,
        artifact_root=artifact_root,
        repository_root=repository,
    )
    catalog_validation = resolve_and_compare_catalogs(
        primary,
        primary_database,
        repository_root=repository,
    )
    result = {
        "schema": "free-official-binance-deterministic-rebuild-validation-v2-full-raw-inventory",
        "status": "PASS",
        "duckdb_version": duckdb.__version__,
        "comparison": comparison,
        "primary_readonly_gate": primary_gate,
        "independent_readonly_gate": independent_gate,
        "catalog_physical_comparison": catalog_physical_comparison,
        "materialized_release_artifacts": materialized,
        "nautilus_catalog_validation": catalog_validation,
        "strategy_run": False,
        "official_trial": False,
        "network_used": False,
    }
    write_once_or_verify(output, result)
    print(
        json.dumps(
            {
                "status": "PASS",
                "semantic_database_identity": comparison["semantic_database_identity"],
                "dataset_release_ids": comparison["dataset_release_ids"],
                "catalog_identities": comparison["catalog_identities"],
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
