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
from crypto_lab.data import DatasetRelease
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data"
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


def database_gate(path: Path) -> dict[str, Any]:
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
    finally:
        connection.close()
    expected_dispositions = {
        f"{SPOT_PROFILE}:DERIVED_FROM_OFFICIAL_TRADES": 1,
        f"{SPOT_PROFILE}:REAL_OFFICIAL_BAR": 304_595,
        f"{SPOT_PROFILE}:VERIFIED_NO_TRADE_INTERVAL": 684,
        f"{PERP_PROFILE}:REAL_OFFICIAL_BAR": 305_280,
    }
    if (
        table_count != 18
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


def materialize_catalog(source: Path, target: Path) -> dict[str, Any]:
    source_inventory = directory_inventory(source)
    if target.exists():
        if directory_inventory(target) != source_inventory:
            raise RuntimeError(f"content-addressed catalog collision: {target}")
    else:
        target.mkdir(parents=True)
        for item in source_inventory:
            preserve_file(source / item["path"], target / item["path"])
    return {
        "path": str(target.relative_to(ROOT)),
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
) -> dict[str, Any]:
    connection = configure_database(database_path)
    try:
        raw_paths = {
            digest: ROOT / local_path
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
            DATA_ROOT / "catalog" / release.catalog_identity,
        )
        for suffix in (
            f"{release.dataset_release_id}.json",
            f"{release.instrument_metadata_identity}.metadata.json",
        ):
            preserve_file(artifact_root / suffix, DATA_ROOT / "releases" / suffix)
        if profile == PERP_PROFILE:
            suffix = f"{release.funding_data_identity}.funding.json"
            preserve_file(artifact_root / suffix, DATA_ROOT / "releases" / suffix)
        for source in release.source_objects:
            preserve_file(
                raw_paths[source.sha256],
                DATA_ROOT / "raw/sha256" / source.sha256[:2] / f"{source.sha256}.blob",
            )
        frozen = DatasetRelease.from_json_bytes(
            (DATA_ROOT / "releases" / f"{release.dataset_release_id}.json").read_bytes(),
        )
        if frozen != release:
            raise RuntimeError("frozen DatasetRelease bytes differ from rebuilt release")
        result[profile] = {
            "dataset_release_id": release.dataset_release_id,
            "catalog_identity": release.catalog_identity,
            "catalog": catalog,
            "source_object_count": len(release.source_objects),
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
                or item["next_funding_ns"] is not None
            ):
                raise RuntimeError("Nautilus funding semantic mismatch")
    return {
        "official_source_event_count": len(rows),
        "runtime_update_count": len(projections),
        "native_binding": "NAUTILUS_2_0_0RC2_INTERVAL_BOUNDARY_REPEAT_ONCE",
        "schedule_invented": False,
    }


def resolve_and_compare_catalogs(
    primary: dict[str, Any],
    database_path: Path,
) -> dict[str, Any]:
    connection = configure_database(database_path)
    result: dict[str, Any] = {}
    try:
        for profile, release_material in sorted(primary["releases"].items()):
            release = DatasetRelease.from_json_bytes(canonical_json_bytes(release_material))
            resolved = release.resolve_runtime_data(DATA_ROOT)
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
    parser.add_argument("--primary-result", type=Path, required=True)
    parser.add_argument("--independent-result", type=Path, required=True)
    parser.add_argument("--primary-catalog-root", type=Path, required=True)
    parser.add_argument("--independent-catalog-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def rooted(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    arguments = parse_args()
    primary_result_path = rooted(arguments.primary_result)
    independent_result_path = rooted(arguments.independent_result)
    primary_catalog_root = rooted(arguments.primary_catalog_root)
    independent_catalog_root = rooted(arguments.independent_catalog_root)
    artifact_root = rooted(arguments.artifact_root)
    output = rooted(arguments.output)
    if duckdb.__version__ != EXPECTED_DUCKDB_VERSION:
        raise RuntimeError("DuckDB identity mismatch")
    primary = load_json(primary_result_path)
    rebuilt = load_json(independent_result_path)
    comparison = compare_build_results(primary, rebuilt)
    primary_database = ROOT / primary["database_path"]
    rebuilt_database = ROOT / rebuilt["database_path"]
    if hash_file(primary_database) != primary["database_file_sha256"]:
        raise RuntimeError("primary physical database identity changed")
    if hash_file(rebuilt_database) != rebuilt["database_file_sha256"]:
        raise RuntimeError("independent physical database identity changed")
    primary_gate = database_gate(primary_database)
    independent_gate = database_gate(rebuilt_database)
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
    )
    catalog_validation = resolve_and_compare_catalogs(primary, primary_database)
    result = {
        "schema": "free-official-binance-deterministic-rebuild-validation-v1",
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
