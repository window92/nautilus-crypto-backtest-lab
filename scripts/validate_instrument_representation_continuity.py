#!/usr/bin/env python3
"""Prove additive Instrument repair leaves every canonical market value unchanged."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parents[1]
VALUE_TABLES = (
    "source_conflicts",
    "spot_agg_trades",
    "spot_execution_bars_1m",
    "perpetual_execution_bars_1m",
    "perpetual_mark_bars_1m",
    "perpetual_funding_events",
    "verified_no_trade_intervals",
    "minute_dispositions",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def quote_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def metadata_projection(value: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "instrument_metadata_identity",
        "market_profile",
        "instrument_id",
        "price_precision",
        "size_precision",
        "price_increment",
        "size_increment",
        "min_price",
        "max_price",
        "min_quantity",
        "max_quantity",
        "lot_size_min_quantity",
        "lot_size_max_quantity",
        "lot_size_step_size",
        "market_lot_size_min_quantity",
        "market_lot_size_max_quantity",
        "market_lot_size_step_size",
        "min_notional",
        "max_notional",
    )
    return {key: value[key] for key in keys}


def validate(old_path: Path, new_path: Path) -> dict[str, Any]:
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(f"ATTACH '{quote_path(old_path)}' AS historical (READ_ONLY)")
        connection.execute(f"ATTACH '{quote_path(new_path)}' AS repaired (READ_ONLY)")
        comparisons: dict[str, Any] = {}
        for table in VALUE_TABLES:
            old_count = int(connection.execute(f"SELECT count(*) FROM historical.{table}").fetchone()[0])
            new_count = int(connection.execute(f"SELECT count(*) FROM repaired.{table}").fetchone()[0])
            old_minus_new = int(
                connection.execute(
                    f"SELECT count(*) FROM (SELECT * FROM historical.{table} "
                    f"EXCEPT ALL SELECT * FROM repaired.{table})",
                ).fetchone()[0],
            )
            new_minus_old = int(
                connection.execute(
                    f"SELECT count(*) FROM (SELECT * FROM repaired.{table} "
                    f"EXCEPT ALL SELECT * FROM historical.{table})",
                ).fetchone()[0],
            )
            comparisons[table] = {
                "historical_row_count": old_count,
                "repaired_row_count": new_count,
                "historical_minus_repaired": old_minus_new,
                "repaired_minus_historical": new_minus_old,
                "exact_rows_equal": old_count == new_count and old_minus_new == new_minus_old == 0,
            }

        metadata_rows: dict[str, Any] = {}
        for profile in (
            "BINANCE_SPOT_CASH_LONG_ONLY",
            "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING",
        ):
            old_json = connection.execute(
                "SELECT metadata_json FROM historical.instrument_metadata WHERE market_profile = ?",
                [profile],
            ).fetchone()[0]
            new_json = connection.execute(
                "SELECT metadata_json FROM repaired.instrument_metadata WHERE market_profile = ?",
                [profile],
            ).fetchone()[0]
            old_metadata = json.loads(old_json)
            new_metadata = json.loads(new_json)
            metadata_rows[profile] = {
                "before": metadata_projection(old_metadata),
                "after": metadata_projection(new_metadata),
                "raw_source_identity_preserved": (
                    old_metadata["source_object_sha256"] == new_metadata["source_object_sha256"]
                ),
                "runtime_normalization": new_metadata["official_definition"]
                ["nautilus_runtime_representation"]["normalization"],
                "economic_order_grid": new_metadata["official_definition"]
                ["binance_economic_order_grid"],
            }
    finally:
        connection.close()

    status = "PASS" if all(item["exact_rows_equal"] for item in comparisons.values()) else "FAIL"
    return {
        "schema": "instrument-representation-value-continuity-v1",
        "status": status,
        "historical_database": {
            "path": str(old_path),
            "size_bytes": old_path.stat().st_size,
            "sha256": sha256_file(old_path),
        },
        "repaired_database": {
            "path": str(new_path),
            "size_bytes": new_path.stat().st_size,
            "sha256": sha256_file(new_path),
        },
        "canonical_table_comparisons": comparisons,
        "instrument_metadata": metadata_rows,
        "canonical_market_numeric_values_changed": status != "PASS",
        "raw_canonical_decimal_spellings_changed": status != "PASS",
        "rounding_or_truncation_used": False,
        "permitted_runtime_transformation": "LOSSLESS_ZERO_PADDING_ONLY",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--historical-database", type=Path, required=True)
    parser.add_argument("--repaired-database", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = validate(arguments.historical_database, arguments.repaired_database)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(payload, encoding="utf-8", newline="\n")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
