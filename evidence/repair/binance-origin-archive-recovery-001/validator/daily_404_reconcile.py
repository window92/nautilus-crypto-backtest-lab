#!/usr/bin/env python3
"""Read-only reconciliation of unavailable Binance Daily Mark delivery objects."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys

import duckdb


MINUTE_MS = 60_000
EXPECTED_DAILY_MINUTES = 1_440


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def source_grid(connection: duckdb.DuckDBPyConnection, source: str, start_ms: int, end_ms: int) -> dict[str, int | None]:
    row = connection.execute(
        """
        SELECT count(*), count(DISTINCT open_time_ms), min(open_time_ms), max(open_time_ms),
               count(*) FILTER (WHERE open_time_ms % 60000 <> 0),
               count(*) FILTER (WHERE close_time_ms <> open_time_ms + 59999),
               count(*) FILTER (WHERE symbol <> 'BTCUSDT' OR interval_name <> '1m'),
               count(*) FILTER (WHERE invalid_reasons <> '[]')
        FROM perpetual_mark_observations
        WHERE source_kind = ? AND open_time_ms >= ? AND open_time_ms < ?
        """,
        [source, start_ms, end_ms],
    ).fetchone()
    keys = (
        "row_count",
        "distinct_open_time_count",
        "min_open_time_ms",
        "max_open_time_ms",
        "off_grid_count",
        "invalid_close_time_count",
        "role_symbol_interval_mismatch_count",
        "invalid_reason_count",
    )
    return dict(zip(keys, row, strict=True))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    before_stat = args.database.stat()
    before_hash = sha256(args.database)
    connection = duckdb.connect(
        str(args.database),
        read_only=True,
        config={
            "allow_unsigned_extensions": "false",
            "autoload_known_extensions": "false",
            "autoinstall_known_extensions": "false",
        },
    )
    unavailable = connection.execute(
        """
        SELECT a.archive_observation_id, a.http_observation_id, a.raw_object_sha256,
               a.exact_filename, a.range_start_ms, a.range_end_ms, a.official_absence_status,
               h.exact_url, h.status_code, h.source_role, r.byte_length, r.local_path,
               r.content_verified
        FROM archive_observations a
        JOIN http_observations h ON h.observation_id = a.http_observation_id
        JOIN raw_objects r ON r.raw_object_sha256 = a.raw_object_sha256
        WHERE a.source_kind = 'USDM_DAILY_MARK_ARCHIVE'
          AND a.cadence = 'daily'
          AND NOT a.archive_available
        ORDER BY a.range_start_ms
        """
    ).fetchall()
    results = []
    for row in unavailable:
        (
            archive_id,
            http_id,
            raw_hash,
            filename,
            start_ms,
            end_ms,
            absence_status,
            exact_url,
            status_code,
            source_role,
            byte_length,
            local_path,
            content_verified,
        ) = row
        monthly = source_grid(connection, "USDM_MONTHLY_MARK_ARCHIVE", start_ms, end_ms)
        rest = source_grid(connection, "USDM_REST_FAPI_V1_MARK_PRICE_KLINES", start_ms, end_ms)
        mismatch = connection.execute(
            """
            WITH monthly AS (
              SELECT * FROM perpetual_mark_observations
              WHERE source_kind = 'USDM_MONTHLY_MARK_ARCHIVE'
                AND open_time_ms >= ? AND open_time_ms < ?
            ), rest AS (
              SELECT * FROM perpetual_mark_observations
              WHERE source_kind = 'USDM_REST_FAPI_V1_MARK_PRICE_KLINES'
                AND open_time_ms >= ? AND open_time_ms < ?
            )
            SELECT
              count(*) FILTER (WHERE m.open_time_ms IS NULL OR r.open_time_ms IS NULL),
              count(*) FILTER (WHERE m.open_time_ms IS NOT NULL AND r.open_time_ms IS NOT NULL AND (
                m.open_value <> r.open_value OR m.high_value <> r.high_value OR
                m.low_value <> r.low_value OR m.close_value <> r.close_value OR
                m.close_time_ms <> r.close_time_ms)),
              count(*) FILTER (WHERE m.open_time_ms IS NOT NULL AND r.open_time_ms IS NOT NULL AND (
                m.open_text <> r.open_text OR m.high_text <> r.high_text OR
                m.low_text <> r.low_text OR m.close_text <> r.close_text))
            FROM monthly m FULL OUTER JOIN rest r USING (open_time_ms)
            """,
            [start_ms, end_ms, start_ms, end_ms],
        ).fetchone()
        monthly_binding = connection.execute(
            """
            SELECT a.archive_observation_id, a.raw_object_sha256, a.exact_filename,
                   a.range_start_ms, a.range_end_ms, p.checksum_observation_id,
                   p.checksum_raw_object_sha256, p.publisher_sha256, p.local_match
            FROM archive_observations a
            JOIN publisher_checksums p ON p.archive_raw_object_sha256 = a.raw_object_sha256
            WHERE a.source_kind = 'USDM_MONTHLY_MARK_ARCHIVE'
              AND a.archive_available
              AND a.range_start_ms <= ? AND a.range_end_ms >= ?
            """,
            [start_ms, end_ms],
        ).fetchall()
        monthly_complete = (
            monthly["row_count"] == EXPECTED_DAILY_MINUTES
            and monthly["distinct_open_time_count"] == EXPECTED_DAILY_MINUTES
            and monthly["min_open_time_ms"] == start_ms
            and monthly["max_open_time_ms"] == end_ms - MINUTE_MS
            and not any(
                monthly[key]
                for key in (
                    "off_grid_count",
                    "invalid_close_time_count",
                    "role_symbol_interval_mismatch_count",
                    "invalid_reason_count",
                )
            )
        )
        rest_complete = (
            rest["row_count"] == EXPECTED_DAILY_MINUTES
            and rest["distinct_open_time_count"] == EXPECTED_DAILY_MINUTES
            and rest["min_open_time_ms"] == start_ms
            and rest["max_open_time_ms"] == end_ms - MINUTE_MS
            and not any(
                rest[key]
                for key in (
                    "off_grid_count",
                    "invalid_close_time_count",
                    "role_symbol_interval_mismatch_count",
                    "invalid_reason_count",
                )
            )
        )
        checksum_ok = len(monthly_binding) == 1 and bool(monthly_binding[0][-1])
        observations_agree = mismatch == (0, 0, 0)
        delivery_404_preserved = (
            status_code == 404
            and absence_status == "HTTP_404_FROM_BINANCE_PUBLIC_DATA"
            and source_role == "USDM_MARK_KLINE_ARCHIVE"
            and bool(content_verified)
        )
        qualified = monthly_complete and rest_complete and checksum_ok and observations_agree and delivery_404_preserved
        results.append(
            {
                "date_utc": iso(start_ms)[:10],
                "range_start_ms": start_ms,
                "range_start_utc": iso(start_ms),
                "range_end_ms": end_ms,
                "range_end_utc": iso(end_ms),
                "unavailable_daily_delivery": {
                    "archive_observation_id": archive_id,
                    "http_observation_id": http_id,
                    "raw_object_sha256": raw_hash,
                    "exact_filename": filename,
                    "exact_url": exact_url,
                    "status_code": status_code,
                    "source_role": source_role,
                    "official_absence_status": absence_status,
                    "raw_byte_length": byte_length,
                    "raw_local_path": local_path,
                    "raw_content_verified": bool(content_verified),
                },
                "monthly_official_grid": monthly,
                "rest_official_grid": rest,
                "monthly_archive_checksum_bindings": [
                    {
                        "archive_observation_id": binding[0],
                        "archive_raw_object_sha256": binding[1],
                        "exact_filename": binding[2],
                        "range_start_ms": binding[3],
                        "range_end_ms": binding[4],
                        "checksum_observation_id": binding[5],
                        "checksum_raw_object_sha256": binding[6],
                        "publisher_sha256": binding[7],
                        "local_match": bool(binding[8]),
                    }
                    for binding in monthly_binding
                ],
                "grid_missing_side_count": mismatch[0],
                "semantic_ohlc_or_close_time_mismatch_count": mismatch[1],
                "original_decimal_text_mismatch_count": mismatch[2],
                "monthly_complete": monthly_complete,
                "rest_complete": rest_complete,
                "publisher_checksum_verified": checksum_ok,
                "monthly_rest_exact_agreement": observations_agree,
                "daily_http_404_preserved": delivery_404_preserved,
                "proposed_classification": "REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE" if qualified else "BLOCKED",
            }
        )
    connection.close()
    after_stat = args.database.stat()
    after_hash = sha256(args.database)
    all_qualified = len(results) == 50 and all(item["proposed_classification"] != "BLOCKED" for item in results)
    payload = {
        "contract": "DAILY_MARK_404_RECONCILIATION_V1",
        "database_access": "read_only; DuckDB extensions disabled",
        "database_identity_before": {
            "path": str(args.database),
            "size_bytes": before_stat.st_size,
            "mtime_ns": before_stat.st_mtime_ns,
            "sha256": before_hash,
        },
        "database_identity_after": {
            "path": str(args.database),
            "size_bytes": after_stat.st_size,
            "mtime_ns": after_stat.st_mtime_ns,
            "sha256": after_hash,
        },
        "database_preserved_byte_for_byte": (
            before_hash == after_hash
            and before_stat.st_size == after_stat.st_size
            and before_stat.st_mtime_ns == after_stat.st_mtime_ns
        ),
        "unavailable_daily_object_count": len(results),
        "qualified_redundant_delivery_count": sum(
            item["proposed_classification"] == "REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE" for item in results
        ),
        "blocked_count": sum(item["proposed_classification"] == "BLOCKED" for item in results),
        "status": "PASS" if all_qualified and before_hash == after_hash else "FAIL",
        "dates": results,
    }
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({key: payload[key] for key in ("unavailable_daily_object_count", "qualified_redundant_delivery_count", "blocked_count", "status")}))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
