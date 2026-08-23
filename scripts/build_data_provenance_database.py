#!/usr/bin/env python3
"""Offline DuckDB materialization for DATA_PROVENANCE_DUCKDB_REPAIR_001."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import zipfile
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Iterator
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
PURE_SOURCE = ROOT / "src/crypto_lab"
if str(PURE_SOURCE) not in sys.path:
    sys.path.insert(0, str(PURE_SOURCE))

import duckdb  # noqa: E402
from data_provenance import AggTrade  # noqa: E402
from data_provenance import AggTradeSource  # noqa: E402
from data_provenance import CoverageDisposition  # noqa: E402
from data_provenance import DerivedSpotKline  # noqa: E402
from data_provenance import KlineObservation  # noqa: E402
from data_provenance import KlineSource  # noqa: E402
from data_provenance import MinuteDecision  # noqa: E402
from data_provenance import NoTradeProof  # noqa: E402
from data_provenance import ONE_MINUTE_MS  # noqa: E402
from data_provenance import ProvenanceError  # noqa: E402
from data_provenance import canonical_json_bytes  # noqa: E402
from data_provenance import compare_aggtrade_observations  # noqa: E402
from data_provenance import derive_spot_kline  # noqa: E402
from data_provenance import iter_aggtrade_archive  # noqa: E402
from data_provenance import iter_kline_archive  # noqa: E402
from data_provenance import parse_kline_rest_page  # noqa: E402
from data_provenance import prove_no_trade_interval  # noqa: E402
from data_provenance import reconcile_required_mark_roles  # noqa: E402
from data_provenance import reconcile_spot_minute  # noqa: E402
from data_provenance import sha256_bytes  # noqa: E402
from data_provenance import utc_now_text  # noqa: E402


RAW_ROOT = ROOT / "data/raw/data-provenance-duckdb-001"
INDEX_PATH = RAW_ROOT / "acquisition-index.json"
SCHEMA_PATH = ROOT / "evidence/repair/data-provenance-duckdb-001/duckdb-schema.sql"
DATA_TOOL_LOCK = ROOT / "data-tool.lock.json"
START_MS = 1_606_780_800_000
END_MS = 1_625_097_600_000
EXPECTED_MINUTES = (END_MS - START_MS) // ONE_MINUTE_MS
SPOT_PROFILE = "BINANCE_SPOT_CASH_LONG_ONLY"
PERP_PROFILE = "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING"
SPOT_INSTRUMENT = "BTCUSDT.BINANCE"
PERP_INSTRUMENT = "BTCUSDT-PERP.BINANCE"
NORMALIZER_VERSION = "binance-official-provenance-duckdb-v1"


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def object_path(digest: str) -> Path:
    return RAW_ROOT / "objects/sha256" / digest[:2] / f"{digest}.bin"


def configure_connection(path: str | Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(
        str(path),
        read_only=read_only,
        config={
            "allow_unsigned_extensions": "false",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        },
    )


def write_csv(path: Path, header: list[str], rows: Iterable[Iterable[Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
            count += 1
        stream.flush()
        os.fsync(stream.fileno())
    return count


def copy_csv(connection: duckdb.DuckDBPyConnection, table: str, path: Path) -> None:
    escaped = str(path.resolve()).replace("'", "''")
    connection.execute(
        f"COPY {table} FROM '{escaped}' (FORMAT CSV, HEADER TRUE, DELIMITER ',', QUOTE '\"', ESCAPE '\"')",
    )


def iter_minute_groups(events: Iterable[AggTrade]) -> Iterator[tuple[int, tuple[AggTrade, ...]]]:
    minute: int | None = None
    items: list[AggTrade] = []
    for event in events:
        event_minute = event.timestamp_ms - event.timestamp_ms % ONE_MINUTE_MS
        if minute is None:
            minute = event_minute
        if event_minute != minute:
            yield minute, tuple(items)
            minute = event_minute
            items = []
        items.append(event)
    if minute is not None:
        yield minute, tuple(items)


def observation_id(item: KlineObservation) -> str:
    return sha256_bytes(canonical_json_bytes(item.as_record()))


SPOT_HEADER = [
    "observation_row_id",
    "source_kind",
    "source_sha256",
    "row_number",
    "symbol",
    "interval_name",
    "open_time_ms",
    "open_text",
    "open_value",
    "high_text",
    "high_value",
    "low_text",
    "low_value",
    "close_text",
    "close_value",
    "base_volume_text",
    "base_volume_value",
    "close_time_ms",
    "quote_volume_text",
    "quote_volume_value",
    "trade_count",
    "taker_buy_base_text",
    "taker_buy_base_value",
    "taker_buy_quote_text",
    "taker_buy_quote_value",
    "ignore_text",
    "invalid_reasons",
]

PERP_HEADER = [
    "observation_row_id",
    "source_kind",
    "source_sha256",
    "row_number",
    "symbol",
    "interval_name",
    "open_time_ms",
    "open_text",
    "open_value",
    "high_text",
    "high_value",
    "low_text",
    "low_value",
    "close_text",
    "close_value",
    "volume_text",
    "volume_value",
    "close_time_ms",
    "quote_volume_text",
    "quote_volume_value",
    "trade_count",
    "taker_buy_base_text",
    "taker_buy_base_value",
    "taker_buy_quote_text",
    "taker_buy_quote_value",
    "invalid_reasons",
]

MARK_HEADER = [
    "observation_row_id",
    "source_kind",
    "source_sha256",
    "row_number",
    "symbol",
    "interval_name",
    "open_time_ms",
    "open_text",
    "open_value",
    "high_text",
    "high_value",
    "low_text",
    "low_value",
    "close_text",
    "close_value",
    "close_time_ms",
    "invalid_reasons",
]


def spot_row(item: KlineObservation) -> tuple[Any, ...]:
    return (
        observation_id(item),
        item.source_kind.value,
        item.source_sha256,
        item.row_number,
        item.symbol,
        item.interval,
        item.open_time_ms,
        item.open_text,
        item.open_text,
        item.high_text,
        item.high_text,
        item.low_text,
        item.low_text,
        item.close_text,
        item.close_text,
        item.base_volume_text,
        item.base_volume_text,
        item.close_time_ms,
        item.quote_volume_text,
        item.quote_volume_text,
        item.trade_count,
        item.taker_buy_base_text,
        item.taker_buy_base_text,
        item.taker_buy_quote_text,
        item.taker_buy_quote_text,
        item.ignore_text,
        canonical_text(list(item.invalid_reasons)),
    )


def perp_row(item: KlineObservation) -> tuple[Any, ...]:
    return (
        observation_id(item),
        item.source_kind.value,
        item.source_sha256,
        item.row_number,
        item.symbol,
        item.interval,
        item.open_time_ms,
        item.open_text,
        item.open_text,
        item.high_text,
        item.high_text,
        item.low_text,
        item.low_text,
        item.close_text,
        item.close_text,
        item.base_volume_text,
        item.base_volume_text,
        item.close_time_ms,
        item.quote_volume_text,
        item.quote_volume_text,
        item.trade_count,
        item.taker_buy_base_text,
        item.taker_buy_base_text,
        item.taker_buy_quote_text,
        item.taker_buy_quote_text,
        canonical_text(list(item.invalid_reasons)),
    )


def mark_row(item: KlineObservation) -> tuple[Any, ...]:
    return (
        observation_id(item),
        item.source_kind.value,
        item.source_sha256,
        item.row_number,
        item.symbol,
        item.interval,
        item.open_time_ms,
        item.open_text,
        item.open_text,
        item.high_text,
        item.high_text,
        item.low_text,
        item.low_text,
        item.close_text,
        item.close_text,
        item.close_time_ms,
        canonical_text(list(item.invalid_reasons)),
    )


def iter_kline_rows(index: dict[str, Any], category: str) -> Iterator[KlineObservation]:
    archive_items = sorted(
        (
            item
            for item in index["archive_pairs"]
            if item["task"]["category"] == category and item["archive_available"]
        ),
        key=lambda item: (item["task"]["source_kind"], item["task"]["range_start_ms"]),
    )
    for item in archive_items:
        task = item["task"]
        yield from iter_kline_archive(
            ROOT / item["archive"]["local_object_path"],
            source_kind=KlineSource(task["source_kind"]),
            source_sha256=item["archive"]["raw_object_sha256"],
            expected_member=task["expected_member"],
        )
    rest_items = sorted(
        (item for item in index["rest_kline_pages"] if item["task"]["category"] == category),
        key=lambda item: item["task"]["page_index"],
    )
    for item in rest_items:
        observation = item["observation"]
        yield from parse_kline_rest_page(
            object_path(observation["raw_object_sha256"]).read_bytes(),
            source_kind=KlineSource(item["task"]["source_kind"]),
            source_sha256=observation["raw_object_sha256"],
        )


def insert_raw_and_http(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    metadata_paths = sorted((RAW_ROOT / "http-observations").glob("*.json"))
    raw_rows: dict[str, tuple[Any, ...]] = {}
    http_rows: list[tuple[Any, ...]] = []
    for path in metadata_paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        digest = value["raw_object_sha256"]
        raw_path = object_path(digest)
        if hash_file(raw_path) != digest or raw_path.stat().st_size != value["byte_length"]:
            raise ProvenanceError("DATA_HASH_MISMATCH", f"raw object does not match {path}")
        raw_rows[digest] = (
            digest,
            raw_path.stat().st_size,
            str(raw_path.relative_to(ROOT)),
            True,
        )
        http_rows.append(
            (
                value["observation_id"],
                digest,
                value["exact_url"],
                urlsplit(value["exact_url"]).query,
                value["status_code"],
                canonical_text(value["response_headers"]),
                value["capture_started_at_utc"],
                value["capture_completed_at_utc"],
                value["source_role"],
                value["pagination_position"],
            ),
        )
    connection.executemany("INSERT INTO raw_objects VALUES (?, ?, ?, ?)", sorted(raw_rows.values()))
    connection.executemany("INSERT INTO http_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", http_rows)
    return {"raw_objects": len(raw_rows), "http_observations": len(http_rows)}


def insert_archive_inventory(
    connection: duckdb.DuckDBPyConnection,
    index: dict[str, Any],
) -> dict[str, int]:
    archives: list[tuple[Any, ...]] = []
    checksums: list[tuple[Any, ...]] = []
    for item in index["archive_pairs"]:
        task = item["task"]
        archive = item["archive"]
        material = {
            "http_observation_id": archive["observation_id"],
            "source_kind": task["source_kind"],
            "cadence": task["cadence"],
            "range_start_ms": task["range_start_ms"],
            "range_end_ms": task["range_end_ms"],
        }
        archive_identity = sha256_bytes(canonical_json_bytes(material))
        archives.append(
            (
                archive_identity,
                archive["observation_id"],
                archive["raw_object_sha256"],
                task["source_kind"],
                task["cadence"],
                task["exact_filename"],
                task["expected_member"],
                task["range_start_ms"],
                task["range_end_ms"],
                item["archive_available"],
                item["official_absence_status"],
            ),
        )
        if item["archive_available"]:
            checksum = item["checksum"]
            checksums.append(
                (
                    checksum["observation_id"],
                    archive["raw_object_sha256"],
                    checksum["raw_object_sha256"],
                    task["exact_filename"],
                    item["publisher_checksum"],
                    item["publisher_checksum_match"],
                ),
            )
    connection.executemany(
        "INSERT INTO archive_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        archives,
    )
    connection.executemany(
        "INSERT INTO publisher_checksums VALUES (?, ?, ?, ?, ?, ?)",
        checksums,
    )
    return {"archive_observations": len(archives), "publisher_checksums": len(checksums)}


def stage_klines(
    connection: duckdb.DuckDBPyConnection,
    index: dict[str, Any],
    staging: Path,
) -> dict[str, int]:
    paths = {
        "spot_kline_observations": staging / "spot-klines.csv",
        "perpetual_execution_observations": staging / "perpetual-execution.csv",
        "perpetual_mark_observations": staging / "perpetual-mark.csv",
    }
    counts = {
        "spot_kline_observations": write_csv(
            paths["spot_kline_observations"],
            SPOT_HEADER,
            (spot_row(item) for item in iter_kline_rows(index, "spot_execution")),
        ),
        "perpetual_execution_observations": write_csv(
            paths["perpetual_execution_observations"],
            PERP_HEADER,
            (perp_row(item) for item in iter_kline_rows(index, "usdm_execution")),
        ),
        "perpetual_mark_observations": write_csv(
            paths["perpetual_mark_observations"],
            MARK_HEADER,
            (mark_row(item) for item in iter_kline_rows(index, "usdm_mark")),
        ),
    }
    for table, path in paths.items():
        copy_csv(connection, table, path)
    return counts


def parse_funding_archive(path: Path, source_sha: str, source_kind: str) -> Iterator[tuple[Any, ...]]:
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].is_dir():
            raise ProvenanceError("DATA_SOURCE_INVALID", "funding archive member mismatch")
        with archive.open(members[0], "r") as binary:
            reader = csv.reader(io.TextIOWrapper(binary, encoding="utf-8", newline=""), strict=True)
            header = next(reader, None)
            if header != ["calc_time", "funding_interval_hours", "last_funding_rate"]:
                raise ProvenanceError("DATA_SOURCE_INVALID", "funding archive header mismatch")
            for row_number, row in enumerate(reader, start=2):
                if len(row) != 3:
                    raise ProvenanceError("DATA_SOURCE_INVALID", "funding archive row malformed")
                material = {
                    "source_kind": source_kind,
                    "source_sha256": source_sha,
                    "row_number": row_number,
                    "funding_time_ms": int(row[0]),
                    "funding_interval_hours": int(row[1]),
                    "funding_rate_text": row[2],
                }
                yield (
                    sha256_bytes(canonical_json_bytes(material)),
                    source_kind,
                    source_sha,
                    row_number,
                    "BTCUSDT",
                    int(row[0]),
                    int(row[1]),
                    row[2],
                    row[2],
                    None,
                    None,
                )


def insert_funding(
    connection: duckdb.DuckDBPyConnection,
    index: dict[str, Any],
) -> int:
    rows: list[tuple[Any, ...]] = []
    archive_items = sorted(
        (item for item in index["archive_pairs"] if item["task"]["category"] == "usdm_funding"),
        key=lambda item: item["task"]["range_start_ms"],
    )
    for item in archive_items:
        rows.extend(
            parse_funding_archive(
                ROOT / item["archive"]["local_object_path"],
                item["archive"]["raw_object_sha256"],
                item["task"]["source_kind"],
            ),
        )
    rest = index["funding_rest"]["observation"]
    value = json.loads(object_path(rest["raw_object_sha256"]).read_bytes())
    if not isinstance(value, list):
        raise ProvenanceError("DATA_SOURCE_INVALID", "funding REST response malformed")
    for row_number, item in enumerate(value, start=1):
        if not isinstance(item, dict) or item.get("symbol") != "BTCUSDT":
            raise ProvenanceError("DATA_ROLE_MISMATCH", "funding REST symbol mismatch")
        material = {
            "source_kind": "USDM_REST_FUNDING_RATE",
            "source_sha256": rest["raw_object_sha256"],
            "row_number": row_number,
            "funding_time_ms": int(item["fundingTime"]),
            "funding_rate_text": str(item["fundingRate"]),
        }
        mark_text = str(item.get("markPrice", ""))
        rows.append(
            (
                sha256_bytes(canonical_json_bytes(material)),
                "USDM_REST_FUNDING_RATE",
                rest["raw_object_sha256"],
                row_number,
                "BTCUSDT",
                int(item["fundingTime"]),
                None,
                str(item["fundingRate"]),
                str(item["fundingRate"]),
                mark_text or None,
                mark_text or None,
            ),
        )
    connection.executemany(
        "INSERT INTO funding_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def insert_metadata(
    connection: duckdb.DuckDBPyConnection,
    index: dict[str, Any],
) -> dict[str, str]:
    identities: dict[str, str] = {}
    role_map = {
        "SPOT_INSTRUMENT_METADATA": (SPOT_PROFILE, SPOT_INSTRUMENT),
        "USDM_INSTRUMENT_METADATA": (PERP_PROFILE, PERP_INSTRUMENT),
    }
    rows: list[tuple[Any, ...]] = []
    for item in index["static_observations"]:
        role = item["source_role"]
        if role not in role_map:
            continue
        observation = item["observation"]
        profile, instrument = role_map[role]
        raw = object_path(observation["raw_object_sha256"]).read_bytes()
        value = json.loads(raw)
        if role == "SPOT_INSTRUMENT_METADATA":
            definitions = value.get("symbols", [])
            if len(definitions) != 1 or definitions[0].get("symbol") != "BTCUSDT":
                raise ProvenanceError("INSTRUMENT_METADATA_INVALID", "Spot definition mismatch")
            definition: Any = definitions[0]
        else:
            definitions = [row for row in value.get("symbols", []) if row.get("symbol") == "BTCUSDT"]
            if len(definitions) != 1:
                raise ProvenanceError("INSTRUMENT_METADATA_INVALID", "USD-M definition mismatch")
            definition = definitions[0]
        material = {
            "market_profile": profile,
            "instrument_id": instrument,
            "source_sha256": observation["raw_object_sha256"],
            "definition": definition,
            "historical_exact": False,
        }
        identity = sha256_bytes(canonical_json_bytes(material))
        identities[profile] = identity
        rows.append(
            (
                identity,
                profile,
                "BTCUSDT",
                instrument,
                observation["raw_object_sha256"],
                observation["capture_completed_at_utc"],
                False,
                role,
                raw.decode("utf-8"),
                canonical_text(
                    [
                        "CURRENT_PUBLIC_METADATA_NOT_EXACT_HISTORICAL_2020_2021_RULES",
                        "NO_ACCOUNT_SPECIFIC_HISTORICAL_FEE_TIER_CLAIM",
                    ],
                ),
            ),
        )
    if set(identities) != {SPOT_PROFILE, PERP_PROFILE}:
        raise ProvenanceError("INSTRUMENT_METADATA_INVALID", "both profile metadata bindings required")
    connection.executemany("INSERT INTO instrument_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return identities


AGG_HEADER = [
    "source_sha256",
    "source_kind",
    "row_number",
    "symbol",
    "aggregate_trade_id",
    "price_text",
    "price_value",
    "quantity_text",
    "quantity_value",
    "first_trade_id",
    "last_trade_id",
    "timestamp_ms",
    "buyer_is_maker",
    "best_price_match",
]

DERIVED_HEADER = [
    "derivation_identity",
    "symbol",
    "open_time_ms",
    "close_time_ms",
    "open_text",
    "open_value",
    "high_text",
    "high_value",
    "low_text",
    "low_value",
    "close_text",
    "close_value",
    "base_volume_text",
    "base_volume_value",
    "quote_volume_text",
    "quote_volume_value",
    "trade_count",
    "taker_buy_base_text",
    "taker_buy_base_value",
    "taker_buy_quote_text",
    "taker_buy_quote_value",
    "first_aggregate_trade_id",
    "last_aggregate_trade_id",
    "first_underlying_trade_id",
    "last_underlying_trade_id",
    "primary_source_sha256",
    "source_sha256s_json",
    "comparison_json",
]


def agg_row(item: AggTrade) -> tuple[Any, ...]:
    return (
        item.source_sha256,
        item.source_kind.value,
        item.row_number,
        item.symbol,
        item.aggregate_trade_id,
        item.price_text,
        item.price_text,
        item.quantity_text,
        item.quantity_text,
        item.first_trade_id,
        item.last_trade_id,
        item.timestamp_ms,
        item.buyer_is_maker,
        item.best_price_match,
    )


def derived_row(item: DerivedSpotKline, comparison: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.derivation_identity,
        item.symbol,
        item.open_time_ms,
        item.close_time_ms,
        item.open_text,
        item.open_text,
        item.high_text,
        item.high_text,
        item.low_text,
        item.low_text,
        item.close_text,
        item.close_text,
        item.base_volume_text,
        item.base_volume_text,
        item.quote_volume_text,
        item.quote_volume_text,
        item.trade_count,
        item.taker_buy_base_text,
        item.taker_buy_base_text,
        item.taker_buy_quote_text,
        item.taker_buy_quote_text,
        item.first_aggregate_trade_id,
        item.last_aggregate_trade_id,
        item.first_underlying_trade_id,
        item.last_underlying_trade_id,
        sorted(item.source_sha256s)[0],
        canonical_text(list(item.source_sha256s)),
        canonical_text(comparison),
    )


def kline_lookup(
    connection: duckdb.DuckDBPyConnection,
    minutes: set[int],
) -> dict[int, dict[str, KlineObservation]]:
    query = connection.execute(
        """
        SELECT source_kind, source_sha256, row_number, symbol, open_time_ms,
               open_text, high_text, low_text, close_text, base_volume_text,
               close_time_ms, quote_volume_text, trade_count,
               taker_buy_base_text, taker_buy_quote_text, ignore_text, invalid_reasons
        FROM spot_kline_observations
        WHERE open_time_ms IN (SELECT unnest(?))
        ORDER BY open_time_ms, source_kind
        """,
        [sorted(minutes)],
    )
    result: dict[int, dict[str, KlineObservation]] = {}
    for row in query.fetchall():
        item = KlineObservation(
            source_kind=KlineSource(row[0]),
            source_sha256=row[1],
            row_number=row[2],
            symbol=row[3],
            interval="1m",
            open_time_ms=row[4],
            open_text=row[5],
            high_text=row[6],
            low_text=row[7],
            close_text=row[8],
            base_volume_text=row[9],
            close_time_ms=row[10],
            quote_volume_text=row[11],
            trade_count=row[12],
            taker_buy_base_text=row[13],
            taker_buy_quote_text=row[14],
            ignore_text=row[15],
            invalid_reasons=tuple(json.loads(row[16])),
        )
        result.setdefault(item.open_time_ms, {})[item.source_kind.value] = item
    return result


def comparison_for(
    derived: DerivedSpotKline,
    lookup: dict[int, dict[str, KlineObservation]],
) -> dict[str, Any]:
    observations = lookup.get(derived.open_time_ms, {})
    result: dict[str, Any] = {}
    fields = (
        "open",
        "high",
        "low",
        "close",
        "base_volume",
        "quote_volume",
        "trade_count",
        "taker_buy_base",
        "taker_buy_quote",
        "close_time",
    )
    derived_values = derived.numeric_material()
    for source, observation in sorted(observations.items()):
        observation_values = observation.numeric_material()
        result[source] = {
            "exact_match": derived_values == observation_values,
            "fields": {
                field: {
                    "derived": str(derived_values[index]),
                    "official_kline": str(observation_values[index]),
                    "match": derived_values[index] == observation_values[index],
                }
                for index, field in enumerate(fields)
            },
            "invalid_reasons": list(observation.invalid_reasons),
            "observation_identity": observation_id(observation),
        }
    return result


def consensus_derived(
    daily: DerivedSpotKline,
    monthly: DerivedSpotKline,
) -> DerivedSpotKline:
    if daily.numeric_material() != monthly.numeric_material():
        raise ProvenanceError(
            "SOURCE_CONFLICT",
            f"daily/monthly aggTrade derivations disagree at {daily.open_time_ms}",
        )
    material = daily.as_record()
    material.pop("derivation_identity")
    material["source_sha256s"] = sorted(set(daily.source_sha256s + monthly.source_sha256s))
    identity = sha256_bytes(canonical_json_bytes(material))
    return replace(
        daily,
        source_sha256s=tuple(material["source_sha256s"]),
        derivation_identity=identity,
    )


def stage_aggtrades_and_derivations(
    connection: duckdb.DuckDBPyConnection,
    index: dict[str, Any],
    staging: Path,
) -> tuple[dict[int, DerivedSpotKline], dict[str, Any]]:
    anomalies = set(anomaly_minutes(connection))
    context_minutes = {
        minute + offset
        for minute in anomalies
        for offset in (-ONE_MINUTE_MS, 0, ONE_MINUTE_MS)
        if START_MS <= minute + offset < END_MS
    }
    lookup = kline_lookup(connection, context_minutes)
    potential_no_trade_minutes = [
        minute
        for minute in anomalies
        if KlineSource.SPOT_REST.value not in lookup.get(minute, {})
        and KlineSource.SPOT_DAILY.value not in lookup.get(minute, {})
    ]
    potential_no_trade_ranges = consecutive_groups(potential_no_trade_minutes)
    agg_path = staging / "spot-aggtrades.csv"
    derived_path = staging / "derived-spot-klines.csv"
    monthly_target: dict[int, DerivedSpotKline] = {}
    daily_target: dict[int, DerivedSpotKline] = {}
    derived_all: dict[int, DerivedSpotKline] = {}
    agg_count = 0
    derived_count = 0
    written_events: set[tuple[str, int]] = set()
    boundary_events: dict[tuple[str, int, int], dict[str, AggTrade | None]] = {}
    archive_items = [
        item for item in index["archive_pairs"] if item["task"]["category"] == "spot_aggtrades"
    ]
    monthly_items = sorted(
        (item for item in archive_items if item["task"]["cadence"] == "monthly"),
        key=lambda item: item["task"]["range_start_ms"],
    )
    daily_items = sorted(
        (item for item in archive_items if item["task"]["cadence"] == "daily"),
        key=lambda item: item["task"]["range_start_ms"],
    )
    with agg_path.open("w", encoding="utf-8", newline="") as agg_stream, derived_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as derived_stream:
        agg_writer = csv.writer(agg_stream, lineterminator="\n")
        derived_writer = csv.writer(derived_stream, lineterminator="\n")
        agg_writer.writerow(AGG_HEADER)
        derived_writer.writerow(DERIVED_HEADER)

        def write_event_once(event: AggTrade) -> None:
            nonlocal agg_count
            key = (event.source_kind.value, event.aggregate_trade_id)
            if key not in written_events:
                agg_writer.writerow(agg_row(event))
                written_events.add(key)
                agg_count += 1

        def observe_boundaries(
            source_kind: AggTradeSource,
            item: dict[str, Any],
            events: tuple[AggTrade, ...],
        ) -> None:
            item_start = item["task"]["range_start_ms"]
            item_end = item["task"]["range_end_ms"]
            for start, end in potential_no_trade_ranges:
                if not (item_start <= start < item_end):
                    continue
                key = (source_kind.value, start, end)
                state = boundary_events.setdefault(key, {"before": None, "after": None})
                if events[-1].timestamp_ms < start:
                    state["before"] = events[-1]
                elif events[0].timestamp_ms >= end and state["after"] is None:
                    state["after"] = events[0]

        for item in monthly_items:
            monthly_events = iter_aggtrade_archive(
                ROOT / item["archive"]["local_object_path"],
                source_kind=AggTradeSource.SPOT_MONTHLY,
                source_sha256=item["archive"]["raw_object_sha256"],
                expected_member=item["task"]["expected_member"],
            )
            for minute, events in iter_minute_groups(monthly_events):
                observe_boundaries(AggTradeSource.SPOT_MONTHLY, item, events)
                if minute not in context_minutes:
                    continue
                derived = derive_spot_kline(events, minute_start_ms=minute)
                monthly_target[minute] = derived
                for event in events:
                    write_event_once(event)
        for item in daily_items:
            events = iter_aggtrade_archive(
                ROOT / item["archive"]["local_object_path"],
                source_kind=AggTradeSource.SPOT_DAILY,
                source_sha256=item["archive"]["raw_object_sha256"],
                expected_member=item["task"]["expected_member"],
            )
            for minute, group in iter_minute_groups(events):
                observe_boundaries(AggTradeSource.SPOT_DAILY, item, group)
                if minute not in context_minutes:
                    continue
                derived = derive_spot_kline(group, minute_start_ms=minute)
                daily_target[minute] = derived
                for event in group:
                    write_event_once(event)
        for state in boundary_events.values():
            for event in (state["before"], state["after"]):
                if event is not None:
                    write_event_once(event)
        if set(monthly_target) != set(daily_target):
            raise ProvenanceError(
                "SOURCE_CONFLICT",
                "daily/monthly aggTrade minute inventories differ on affected days",
            )
        for minute in sorted(monthly_target):
            combined = consensus_derived(daily_target[minute], monthly_target[minute])
            derived_all[minute] = combined
            derived_writer.writerow(derived_row(combined, comparison_for(combined, lookup)))
            derived_count += 1
        for stream in (agg_stream, derived_stream):
            stream.flush()
            os.fsync(stream.fileno())
    copy_csv(connection, "spot_agg_trades", agg_path)
    copy_csv(connection, "derived_spot_klines", derived_path)
    result = {
        "spot_agg_trades": agg_count,
        "derived_spot_klines": derived_count,
        "anomaly_minute_count": len(anomalies),
        "context_minute_count": len(context_minutes),
        "monthly_derived_minutes": len(monthly_target),
        "daily_monthly_target_minutes": len(monthly_target),
        "daily_monthly_target_exact_match": True,
        "monthly_archive_count_fully_sequence_validated": len(monthly_items),
        "daily_archive_count_fully_sequence_validated": len(daily_items),
        "potential_no_trade_range_count": len(potential_no_trade_ranges),
        "captured_boundary_event_count": sum(
            item is not None
            for state in boundary_events.values()
            for item in (state["before"], state["after"])
        ),
    }
    return derived_all, result


def fetch_spot_observation(
    connection: duckdb.DuckDBPyConnection,
    source_kind: KlineSource,
    minute: int,
) -> KlineObservation | None:
    row = connection.execute(
        """
        SELECT source_sha256, row_number, symbol, open_time_ms, open_text, high_text,
               low_text, close_text, base_volume_text, close_time_ms,
               quote_volume_text, trade_count, taker_buy_base_text,
               taker_buy_quote_text, ignore_text, invalid_reasons
        FROM spot_kline_observations
        WHERE source_kind = ? AND open_time_ms = ?
        """,
        [source_kind.value, minute],
    ).fetchone()
    if row is None:
        return None
    return KlineObservation(
        source_kind=source_kind,
        source_sha256=row[0],
        row_number=row[1],
        symbol=row[2],
        interval="1m",
        open_time_ms=row[3],
        open_text=row[4],
        high_text=row[5],
        low_text=row[6],
        close_text=row[7],
        base_volume_text=row[8],
        close_time_ms=row[9],
        quote_volume_text=row[10],
        trade_count=row[11],
        taker_buy_base_text=row[12],
        taker_buy_quote_text=row[13],
        ignore_text=row[14],
        invalid_reasons=tuple(json.loads(row[15])),
    )


def aggtrade_from_db(row: tuple[Any, ...]) -> AggTrade:
    return AggTrade(
        source_kind=AggTradeSource(row[0]),
        source_sha256=row[1],
        row_number=row[2],
        symbol=row[3],
        aggregate_trade_id=row[4],
        price_text=row[5],
        quantity_text=row[6],
        first_trade_id=row[7],
        last_trade_id=row[8],
        timestamp_ms=row[9],
        buyer_is_maker=row[10],
        best_price_match=row[11],
    )


def query_boundary_event(
    connection: duckdb.DuckDBPyConnection,
    source_kind: AggTradeSource,
    boundary_ms: int,
    *,
    before: bool,
) -> AggTrade | None:
    operator = "<" if before else ">="
    order = "DESC" if before else "ASC"
    row = connection.execute(
        f"""
        SELECT source_kind, source_sha256, row_number, symbol, aggregate_trade_id,
               price_text, quantity_text, first_trade_id, last_trade_id,
               timestamp_ms, buyer_is_maker, best_price_match
        FROM spot_agg_trades
        WHERE source_kind = ? AND timestamp_ms {operator} ?
        ORDER BY timestamp_ms {order}, aggregate_trade_id {order}
        LIMIT 1
        """,
        [source_kind.value, boundary_ms],
    ).fetchone()
    return aggtrade_from_db(row) if row is not None else None


def anomaly_minutes(connection: duckdb.DuckDBPyConnection) -> list[int]:
    rows = connection.execute(
        """
        WITH grid AS (
            SELECT range AS open_time_ms FROM range(?, ?, 60000)
        ),
        r AS (SELECT * FROM spot_kline_observations WHERE source_kind = ?),
        d AS (SELECT * FROM spot_kline_observations WHERE source_kind = ?),
        m AS (SELECT * FROM spot_kline_observations WHERE source_kind = ?),
        v AS (SELECT * FROM derived_spot_klines)
        SELECT g.open_time_ms
        FROM grid g
        LEFT JOIN r ON r.open_time_ms = g.open_time_ms
        LEFT JOIN d ON d.open_time_ms = g.open_time_ms
        LEFT JOIN m ON m.open_time_ms = g.open_time_ms
        LEFT JOIN v ON v.open_time_ms = g.open_time_ms
        WHERE r.observation_row_id IS NULL
           OR d.observation_row_id IS NULL
           OR m.observation_row_id IS NULL
           OR r.invalid_reasons <> '[]'
           OR d.invalid_reasons <> '[]'
           OR m.invalid_reasons <> '[]'
           OR NOT (
                r.open_value = d.open_value AND r.open_value = m.open_value
            AND r.high_value = d.high_value AND r.high_value = m.high_value
            AND r.low_value = d.low_value AND r.low_value = m.low_value
            AND r.close_value = d.close_value AND r.close_value = m.close_value
            AND r.base_volume_value = d.base_volume_value AND r.base_volume_value = m.base_volume_value
            AND r.quote_volume_value = d.quote_volume_value AND r.quote_volume_value = m.quote_volume_value
            AND r.trade_count = d.trade_count AND r.trade_count = m.trade_count
            AND r.taker_buy_base_value = d.taker_buy_base_value AND r.taker_buy_base_value = m.taker_buy_base_value
            AND r.taker_buy_quote_value = d.taker_buy_quote_value AND r.taker_buy_quote_value = m.taker_buy_quote_value
            AND r.close_time_ms = d.close_time_ms AND r.close_time_ms = m.close_time_ms
           )
           OR (
                v.derivation_identity IS NOT NULL
            AND r.observation_row_id IS NOT NULL
            AND NOT (
                v.open_value = r.open_value AND v.high_value = r.high_value
            AND v.low_value = r.low_value AND v.close_value = r.close_value
            AND v.base_volume_value = r.base_volume_value
            AND v.quote_volume_value = r.quote_volume_value
            AND v.trade_count = r.trade_count
            AND v.taker_buy_base_value = r.taker_buy_base_value
            AND v.taker_buy_quote_value = r.taker_buy_quote_value
            AND v.close_time_ms = r.close_time_ms
            )
           )
        ORDER BY g.open_time_ms
        """,
        [
            START_MS,
            END_MS,
            KlineSource.SPOT_REST.value,
            KlineSource.SPOT_DAILY.value,
            KlineSource.SPOT_MONTHLY.value,
        ],
    ).fetchall()
    return [row[0] for row in rows]


def consecutive_groups(values: Iterable[int]) -> list[tuple[int, int]]:
    ordered = sorted(set(values))
    if not ordered:
        return []
    result: list[tuple[int, int]] = []
    start = prior = ordered[0]
    for value in ordered[1:]:
        if value != prior + ONE_MINUTE_MS:
            result.append((start, prior + ONE_MINUTE_MS))
            start = value
        prior = value
    result.append((start, prior + ONE_MINUTE_MS))
    return result


def build_no_trade_proofs(
    connection: duckdb.DuckDBPyConnection,
    anomalies: list[int],
) -> tuple[
    dict[int, NoTradeProof],
    list[tuple[Any, ...]],
    dict[int, dict[str, Any]],
]:
    candidates: list[int] = []
    for minute in anomalies:
        rest = fetch_spot_observation(connection, KlineSource.SPOT_REST, minute)
        daily = fetch_spot_observation(connection, KlineSource.SPOT_DAILY, minute)
        trade_count = connection.execute(
            "SELECT count(*) FROM spot_agg_trades WHERE source_kind = ? AND timestamp_ms >= ? AND timestamp_ms < ?",
            [AggTradeSource.SPOT_DAILY.value, minute, minute + ONE_MINUTE_MS],
        ).fetchone()[0]
        if rest is None and daily is None and trade_count == 0:
            candidates.append(minute)
    mapping: dict[int, NoTradeProof] = {}
    rows: list[tuple[Any, ...]] = []
    failures: dict[int, dict[str, Any]] = {}
    for start, end in consecutive_groups(candidates):
        daily_before = query_boundary_event(
            connection,
            AggTradeSource.SPOT_DAILY,
            start,
            before=True,
        )
        daily_after = query_boundary_event(
            connection,
            AggTradeSource.SPOT_DAILY,
            end,
            before=False,
        )
        monthly_before = query_boundary_event(
            connection,
            AggTradeSource.SPOT_MONTHLY,
            start,
            before=True,
        )
        monthly_after = query_boundary_event(
            connection,
            AggTradeSource.SPOT_MONTHLY,
            end,
            before=False,
        )
        if any(
            item is None
            for item in (daily_before, daily_after, monthly_before, monthly_after)
        ):
            failure = {
                "code": "SOURCE_INCOMPLETE",
                "reason": "AGGTRADE_BOUNDARY_EVENT_UNAVAILABLE",
                "start_ms": start,
                "end_ms": end,
            }
            for minute in range(start, end, ONE_MINUTE_MS):
                failures[minute] = failure
            continue
        assert daily_before is not None
        assert daily_after is not None
        assert monthly_before is not None
        assert monthly_after is not None
        if (
            daily_before.material_tuple() != monthly_before.material_tuple()
            or daily_after.material_tuple() != monthly_after.material_tuple()
        ):
            failure = {
                "code": "SOURCE_CONFLICT",
                "reason": "DAILY_MONTHLY_AGGTRADE_BOUNDARIES_CONFLICT",
                "start_ms": start,
                "end_ms": end,
            }
            for minute in range(start, end, ONE_MINUTE_MS):
                failures[minute] = failure
            continue
        sources = tuple(
            sorted(
                {
                    daily_before.source_sha256,
                    daily_after.source_sha256,
                    monthly_before.source_sha256,
                    monthly_after.source_sha256,
                },
            ),
        )
        try:
            proof = prove_no_trade_interval(
                start_ms=start,
                end_ms=end,
                before=daily_before,
                after=daily_after,
                events_inside=(),
                rest_kline_present=False,
                daily_kline_present=False,
                archives_complete=True,
                official_sources=sources,
            )
        except ProvenanceError as exc:
            failure = {
                "code": exc.code,
                "reason": str(exc),
                "start_ms": start,
                "end_ms": end,
                "before_aggregate_trade_id": daily_before.aggregate_trade_id,
                "after_aggregate_trade_id": daily_after.aggregate_trade_id,
                "before_last_trade_id": daily_before.last_trade_id,
                "after_first_trade_id": daily_after.first_trade_id,
                "official_sources": list(sources),
            }
            for minute in range(start, end, ONE_MINUTE_MS):
                failures[minute] = failure
            continue
        rows.append(
            (
                proof.proof_identity,
                proof.symbol,
                proof.start_ms,
                proof.end_ms,
                proof.before.aggregate_trade_id,
                proof.after.aggregate_trade_id,
                proof.before.last_trade_id,
                proof.after.first_trade_id,
                sources[0],
                canonical_text(list(proof.official_sources)),
                "PROBABLE_VENUE_OUTAGE",
                None,
            ),
        )
        for minute in range(start, end, ONE_MINUTE_MS):
            mapping[minute] = proof
    return mapping, rows, failures


CANONICAL_EXECUTION_HEADER = [
    "market_profile",
    "instrument_id",
    "symbol",
    "open_time_ms",
    "close_time_ms",
    "disposition",
    "canonical_identity",
    "open_text",
    "open_value",
    "high_text",
    "high_value",
    "low_text",
    "low_value",
    "close_text",
    "close_value",
    "volume_text",
    "volume_value",
    "quote_volume_text",
    "quote_volume_value",
    "trade_count",
    "taker_buy_base_text",
    "taker_buy_base_value",
    "taker_buy_quote_text",
    "taker_buy_quote_value",
    "primary_source_sha256",
    "source_bindings_json",
]

COVERAGE_HEADER = [
    "market_profile",
    "symbol",
    "open_time_ms",
    "disposition",
    "canonical_identity",
    "proof_identity",
    "reason",
    "blocking",
]


def canonical_observation_row(
    profile: str,
    instrument: str,
    disposition: CoverageDisposition,
    observation: KlineObservation,
    source_bindings: list[str],
) -> tuple[Any, ...]:
    material = {
        "market_profile": profile,
        "instrument_id": instrument,
        "open_time_ms": observation.open_time_ms,
        "disposition": disposition.value,
        "material": list(observation.numeric_material()),
        "source_bindings": sorted(source_bindings),
    }
    identity = sha256_bytes(canonical_json_bytes(material))
    return (
        profile,
        instrument,
        observation.symbol,
        observation.open_time_ms,
        observation.open_time_ms + ONE_MINUTE_MS - 1,
        disposition.value,
        identity,
        observation.open_text,
        observation.open_text,
        observation.high_text,
        observation.high_text,
        observation.low_text,
        observation.low_text,
        observation.close_text,
        observation.close_text,
        observation.base_volume_text,
        observation.base_volume_text,
        observation.quote_volume_text,
        observation.quote_volume_text,
        observation.trade_count,
        observation.taker_buy_base_text,
        observation.taker_buy_base_text,
        observation.taker_buy_quote_text,
        observation.taker_buy_quote_text,
        observation.source_sha256,
        canonical_text(sorted(source_bindings)),
    )


def canonical_derived_row(item: DerivedSpotKline) -> tuple[Any, ...]:
    return (
        SPOT_PROFILE,
        SPOT_INSTRUMENT,
        item.symbol,
        item.open_time_ms,
        item.close_time_ms,
        CoverageDisposition.DERIVED_FROM_OFFICIAL_TRADES.value,
        item.derivation_identity,
        item.open_text,
        item.open_text,
        item.high_text,
        item.high_text,
        item.low_text,
        item.low_text,
        item.close_text,
        item.close_text,
        item.base_volume_text,
        item.base_volume_text,
        item.quote_volume_text,
        item.quote_volume_text,
        item.trade_count,
        item.taker_buy_base_text,
        item.taker_buy_base_text,
        item.taker_buy_quote_text,
        item.taker_buy_quote_text,
        sorted(item.source_sha256s)[0],
        canonical_text(list(item.source_sha256s)),
    )


def insert_conflict(
    connection: duckdb.DuckDBPyConnection,
    decision: MinuteDecision,
) -> None:
    if not decision.conflicts and not decision.superseded_observations:
        return
    material = {
        "market_profile": SPOT_PROFILE,
        "open_time_ms": decision.open_time_ms,
        "reason": decision.reason,
        "conflicts": list(decision.conflicts),
        "superseded": list(decision.superseded_observations),
        "resolution": decision.canonical_identity,
    }
    identity = sha256_bytes(canonical_json_bytes(material))
    connection.execute(
        "INSERT INTO source_conflicts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            identity,
            SPOT_PROFILE,
            "BTCUSDT",
            decision.open_time_ms,
            "UNRESOLVED_BLOCKING" if decision.blocking else "RESOLVED_SUPERSEDED",
            decision.reason,
            canonical_text(
                {
                    "conflicts": list(decision.conflicts),
                    "superseded": list(decision.superseded_observations),
                },
            ),
            decision.canonical_identity,
        ],
    )


def stage_spot_canonical(
    connection: duckdb.DuckDBPyConnection,
    staging: Path,
    derived: dict[int, DerivedSpotKline],
) -> dict[str, Any]:
    anomalies = anomaly_minutes(connection)
    anomaly_set = set(anomalies)
    proofs, proof_rows, proof_failures = build_no_trade_proofs(connection, anomalies)
    if proof_rows:
        connection.executemany(
            "INSERT INTO verified_no_trade_intervals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            proof_rows,
        )
    canonical_path = staging / "canonical-spot-execution.csv"
    coverage_path = staging / "spot-minute-coverage.csv"
    disposition_counts: dict[str, int] = {}
    blocking_minutes: list[int] = []
    with canonical_path.open("w", encoding="utf-8", newline="") as canonical_stream, coverage_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as coverage_stream:
        canonical_writer = csv.writer(canonical_stream, lineterminator="\n")
        coverage_writer = csv.writer(coverage_stream, lineterminator="\n")
        canonical_writer.writerow(CANONICAL_EXECUTION_HEADER)
        coverage_writer.writerow(COVERAGE_HEADER)
        query = connection.execute(
            """
            SELECT r.open_time_ms, r.source_sha256, r.observation_row_id,
                   r.row_number, r.symbol, r.open_text, r.high_text, r.low_text,
                   r.close_text, r.base_volume_text, r.close_time_ms,
                   r.quote_volume_text, r.trade_count, r.taker_buy_base_text,
                   r.taker_buy_quote_text, r.ignore_text, r.invalid_reasons,
                   d.observation_row_id, m.observation_row_id
            FROM spot_kline_observations r
            JOIN spot_kline_observations d ON d.open_time_ms = r.open_time_ms AND d.source_kind = ?
            JOIN spot_kline_observations m ON m.open_time_ms = r.open_time_ms AND m.source_kind = ?
            WHERE r.source_kind = ?
            ORDER BY r.open_time_ms
            """,
            [
                KlineSource.SPOT_DAILY.value,
                KlineSource.SPOT_MONTHLY.value,
                KlineSource.SPOT_REST.value,
            ],
        )
        while True:
            rows = query.fetchmany(5000)
            if not rows:
                break
            for row in rows:
                minute = row[0]
                if minute in anomaly_set:
                    continue
                observation = KlineObservation(
                    source_kind=KlineSource.SPOT_REST,
                    source_sha256=row[1],
                    row_number=row[3],
                    symbol=row[4],
                    interval="1m",
                    open_time_ms=minute,
                    open_text=row[5],
                    high_text=row[6],
                    low_text=row[7],
                    close_text=row[8],
                    base_volume_text=row[9],
                    close_time_ms=row[10],
                    quote_volume_text=row[11],
                    trade_count=row[12],
                    taker_buy_base_text=row[13],
                    taker_buy_quote_text=row[14],
                    ignore_text=row[15],
                    invalid_reasons=tuple(json.loads(row[16])),
                )
                canonical = canonical_observation_row(
                    SPOT_PROFILE,
                    SPOT_INSTRUMENT,
                    CoverageDisposition.REAL_OFFICIAL_BAR,
                    observation,
                    [row[2], row[17], row[18]],
                )
                canonical_writer.writerow(canonical)
                coverage_writer.writerow(
                    (
                        SPOT_PROFILE,
                        "BTCUSDT",
                        minute,
                        CoverageDisposition.REAL_OFFICIAL_BAR.value,
                        canonical[6],
                        None,
                        "REST_DAILY_MONTHLY_EXACT_AGREEMENT",
                        False,
                    ),
                )
                disposition_counts[CoverageDisposition.REAL_OFFICIAL_BAR.value] = (
                    disposition_counts.get(CoverageDisposition.REAL_OFFICIAL_BAR.value, 0) + 1
                )
        for minute in anomalies:
            rest = fetch_spot_observation(connection, KlineSource.SPOT_REST, minute)
            daily = fetch_spot_observation(connection, KlineSource.SPOT_DAILY, minute)
            monthly = fetch_spot_observation(connection, KlineSource.SPOT_MONTHLY, minute)
            item = derived.get(minute)
            independent_count = len(item.source_sha256s) if item is not None else 0
            decision = reconcile_spot_minute(
                minute_start_ms=minute,
                rest=rest,
                daily=daily,
                monthly=monthly,
                derived=item,
                no_trade_proof=proofs.get(minute),
                independent_trade_derivation_count=independent_count,
            )
            proof_failure = proof_failures.get(minute)
            if (
                proof_failure is not None
                and decision.disposition is CoverageDisposition.UNRESOLVED_GAP
            ):
                failure_disposition = (
                    CoverageDisposition.SOURCE_CONFLICT
                    if proof_failure["code"] == "SOURCE_CONFLICT"
                    else CoverageDisposition.SOURCE_INCOMPLETE
                )
                decision = replace(
                    decision,
                    disposition=failure_disposition,
                    reason=proof_failure["reason"],
                )
            canonical_identity = decision.canonical_identity
            if decision.disposition is CoverageDisposition.REAL_OFFICIAL_BAR:
                selected = rest or daily or monthly
                if selected is None:
                    raise ProvenanceError("UNRESOLVED_GAP", "accepted decision has no observation")
                bindings = [
                    observation_id(value)
                    for value in (rest, daily, monthly)
                    if value is not None and not value.invalid_reasons
                ]
                canonical = canonical_observation_row(
                    SPOT_PROFILE,
                    SPOT_INSTRUMENT,
                    decision.disposition,
                    selected,
                    bindings,
                )
                canonical_identity = canonical[6]
                canonical_writer.writerow(canonical)
            elif decision.disposition is CoverageDisposition.DERIVED_FROM_OFFICIAL_TRADES:
                if item is None:
                    raise ProvenanceError("UNRESOLVED_GAP", "derived decision has no derived bar")
                canonical_writer.writerow(canonical_derived_row(item))
                canonical_identity = item.derivation_identity
            coverage_writer.writerow(
                (
                    SPOT_PROFILE,
                    "BTCUSDT",
                    minute,
                    decision.disposition.value,
                    canonical_identity,
                    decision.no_trade_proof_identity,
                    decision.reason,
                    decision.blocking,
                ),
            )
            insert_conflict(
                connection,
                replace(decision, canonical_identity=canonical_identity),
            )
            disposition_counts[decision.disposition.value] = (
                disposition_counts.get(decision.disposition.value, 0) + 1
            )
            if decision.blocking:
                blocking_minutes.append(minute)
        for stream in (canonical_stream, coverage_stream):
            stream.flush()
            os.fsync(stream.fileno())
    copy_csv(connection, "canonical_execution_bars", canonical_path)
    copy_csv(connection, "minute_coverage", coverage_path)
    return {
        "anomaly_minutes": anomalies,
        "anomaly_count": len(anomalies),
        "disposition_counts": disposition_counts,
        "blocking_minutes": blocking_minutes,
        "verified_no_trade_proof_count": len(proof_rows),
        "verified_no_trade_minute_count": len(proofs),
        "no_trade_proof_failures": sorted(
            {
                canonical_text(value): value
                for value in proof_failures.values()
            }.values(),
            key=lambda value: (value["start_ms"], value["end_ms"]),
        ),
    }


def profiles_source_availability(index: dict[str, Any]) -> dict[str, set[int]]:
    missing: dict[str, set[int]] = {"usdm_mark": set()}
    for item in index["archive_pairs"]:
        if item["task"]["category"] == "usdm_mark" and not item["archive_available"]:
            missing["usdm_mark"].add(item["task"]["range_start_ms"])
    return missing


def write_perpetual_execution_decision(
    row: tuple[Any, ...],
    execution_writer: Any,
    coverage_writer: Any,
    blocking_execution: list[int],
) -> None:
    minute = row[0]
    if not row[17]:
        coverage_writer.writerow(
            (
                PERP_PROFILE,
                "BTCUSDT",
                minute,
                CoverageDisposition.SOURCE_CONFLICT.value,
                None,
                None,
                "PERPETUAL_EXECUTION_REST_DAILY_MONTHLY_NOT_EXACT",
                True,
            ),
        )
        blocking_execution.append(minute)
        return
    observation = KlineObservation(
        source_kind=KlineSource.USDM_EXECUTION_REST,
        source_sha256=row[2],
        row_number=row[3],
        symbol=row[4],
        interval="1m",
        open_time_ms=minute,
        open_text=row[5],
        high_text=row[6],
        low_text=row[7],
        close_text=row[8],
        base_volume_text=row[9],
        close_time_ms=row[10],
        quote_volume_text=row[11],
        trade_count=row[12],
        taker_buy_base_text=row[13],
        taker_buy_quote_text=row[14],
        ignore_text="0",
        invalid_reasons=(),
    )
    canonical = canonical_observation_row(
        PERP_PROFILE,
        PERP_INSTRUMENT,
        CoverageDisposition.REAL_OFFICIAL_BAR,
        observation,
        [row[1], row[15], row[16]],
    )
    execution_writer.writerow(canonical)
    coverage_writer.writerow(
        (
            PERP_PROFILE,
            "BTCUSDT",
            minute,
            CoverageDisposition.REAL_OFFICIAL_BAR.value,
            canonical[6],
            None,
            "REST_DAILY_MONTHLY_EXACT_AGREEMENT",
            False,
        ),
    )


def stage_perpetual_canonical(
    connection: duckdb.DuckDBPyConnection,
    index: dict[str, Any],
    staging: Path,
) -> dict[str, Any]:
    missing_daily = profiles_source_availability(index)["usdm_mark"]
    execution_path = staging / "canonical-perpetual-execution.csv"
    coverage_path = staging / "perpetual-minute-coverage.csv"
    mark_path = staging / "canonical-perpetual-mark.csv"
    mark_header = [
        "market_profile",
        "instrument_id",
        "symbol",
        "open_time_ms",
        "close_time_ms",
        "canonical_identity",
        "open_text",
        "open_value",
        "high_text",
        "high_value",
        "low_text",
        "low_value",
        "close_text",
        "close_value",
        "primary_source_sha256",
        "source_bindings_json",
    ]
    blocking_execution: list[int] = []
    blocking_mark: list[int] = []
    accepted_mark_without_daily = 0
    with execution_path.open("w", encoding="utf-8", newline="") as execution_stream, coverage_path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as coverage_stream, mark_path.open("w", encoding="utf-8", newline="") as mark_stream:
        execution_writer = csv.writer(execution_stream, lineterminator="\n")
        coverage_writer = csv.writer(coverage_stream, lineterminator="\n")
        mark_writer = csv.writer(mark_stream, lineterminator="\n")
        execution_writer.writerow(CANONICAL_EXECUTION_HEADER)
        coverage_writer.writerow(COVERAGE_HEADER)
        mark_writer.writerow(mark_header)
        execution_cursor = connection.execute(
            """
            SELECT grid.minute,
                   r.observation_row_id, r.source_sha256, r.row_number, r.symbol,
                   r.open_text, r.high_text, r.low_text, r.close_text, r.volume_text,
                   r.close_time_ms, r.quote_volume_text, r.trade_count,
                   r.taker_buy_base_text, r.taker_buy_quote_text,
                   d.observation_row_id, m.observation_row_id,
                   coalesce(
                       r.invalid_reasons = '[]' AND d.invalid_reasons = '[]' AND m.invalid_reasons = '[]'
                       AND r.open_value = d.open_value AND r.open_value = m.open_value
                       AND r.high_value = d.high_value AND r.high_value = m.high_value
                       AND r.low_value = d.low_value AND r.low_value = m.low_value
                       AND r.close_value = d.close_value AND r.close_value = m.close_value
                       AND r.volume_value = d.volume_value AND r.volume_value = m.volume_value
                       AND r.close_time_ms = d.close_time_ms AND r.close_time_ms = m.close_time_ms
                       AND r.quote_volume_value = d.quote_volume_value
                       AND r.quote_volume_value = m.quote_volume_value
                       AND r.trade_count = d.trade_count AND r.trade_count = m.trade_count
                       AND r.taker_buy_base_value = d.taker_buy_base_value
                       AND r.taker_buy_base_value = m.taker_buy_base_value
                       AND r.taker_buy_quote_value = d.taker_buy_quote_value
                       AND r.taker_buy_quote_value = m.taker_buy_quote_value,
                       false
                   ) AS valid
            FROM generate_series(?, ?, ?) AS grid(minute)
            LEFT JOIN perpetual_execution_observations r
              ON r.open_time_ms = grid.minute AND r.source_kind = ?
            LEFT JOIN perpetual_execution_observations d
              ON d.open_time_ms = grid.minute AND d.source_kind = ?
            LEFT JOIN perpetual_execution_observations m
              ON m.open_time_ms = grid.minute AND m.source_kind = ?
            ORDER BY grid.minute
            """,
            [
                START_MS,
                END_MS - ONE_MINUTE_MS,
                ONE_MINUTE_MS,
                KlineSource.USDM_EXECUTION_REST.value,
                KlineSource.USDM_EXECUTION_DAILY.value,
                KlineSource.USDM_EXECUTION_MONTHLY.value,
            ],
        )
        while True:
            execution_rows = execution_cursor.fetchmany(5000)
            if not execution_rows:
                break
            for row in execution_rows:
                write_perpetual_execution_decision(
                    row,
                    execution_writer,
                    coverage_writer,
                    blocking_execution,
                )
        mark_cursor = connection.execute(
            """
            SELECT grid.minute,
                   r.observation_row_id, r.open_text, r.high_text, r.low_text, r.close_text,
                   r.close_time_ms, d.observation_row_id, m.observation_row_id,
                   coalesce(
                       r.invalid_reasons = '[]' AND m.invalid_reasons = '[]'
                       AND r.open_value = m.open_value AND r.high_value = m.high_value
                       AND r.low_value = m.low_value AND r.close_value = m.close_value
                       AND r.close_time_ms = m.close_time_ms,
                       false
                   ) AS rest_monthly_valid,
                   d.observation_row_id IS NOT NULL AS daily_present,
                   coalesce(
                       d.invalid_reasons = '[]'
                       AND r.open_value = d.open_value AND r.high_value = d.high_value
                       AND r.low_value = d.low_value AND r.close_value = d.close_value
                       AND r.close_time_ms = d.close_time_ms,
                       false
                   ) AS daily_valid,
                   r.source_sha256
            FROM generate_series(?, ?, ?) AS grid(minute)
            LEFT JOIN perpetual_mark_observations r
              ON r.open_time_ms = grid.minute AND r.source_kind = ?
            LEFT JOIN perpetual_mark_observations d
              ON d.open_time_ms = grid.minute AND d.source_kind = ?
            LEFT JOIN perpetual_mark_observations m
              ON m.open_time_ms = grid.minute AND m.source_kind = ?
            ORDER BY grid.minute
            """,
            [
                START_MS,
                END_MS - ONE_MINUTE_MS,
                ONE_MINUTE_MS,
                KlineSource.USDM_MARK_REST.value,
                KlineSource.USDM_MARK_DAILY.value,
                KlineSource.USDM_MARK_MONTHLY.value,
            ],
        )
        while True:
            mark_rows = mark_cursor.fetchmany(5000)
            if not mark_rows:
                break
            for row in mark_rows:
                minute = row[0]
                day_start = minute - minute % 86_400_000
                daily_officially_absent = day_start in missing_daily
                mark_valid, reason = reconcile_required_mark_roles(
                    rest_monthly_valid=bool(row[9]),
                    daily_archive_available=not daily_officially_absent,
                    daily_row_present=bool(row[10]),
                    daily_row_valid=bool(row[11]),
                )
                if not mark_valid:
                    blocking_mark.append(minute)
                    continue
                bindings = [row[1], row[8]]
                bindings.append(row[7])
                material = {
                    "market_profile": PERP_PROFILE,
                    "instrument_id": PERP_INSTRUMENT,
                    "open_time_ms": minute,
                    "values": list(row[2:7]),
                    "source_bindings": sorted(bindings),
                    "reason": reason,
                }
                identity = sha256_bytes(canonical_json_bytes(material))
                mark_writer.writerow(
                    (
                        PERP_PROFILE,
                        PERP_INSTRUMENT,
                        "BTCUSDT",
                        minute,
                        minute + ONE_MINUTE_MS - 1,
                        identity,
                        row[2],
                        row[2],
                        row[3],
                        row[3],
                        row[4],
                        row[4],
                        row[5],
                        row[5],
                        row[12],
                        canonical_text(sorted(bindings)),
                    ),
                )
        for stream in (execution_stream, coverage_stream, mark_stream):
            stream.flush()
            os.fsync(stream.fileno())
    copy_csv(connection, "canonical_execution_bars", execution_path)
    copy_csv(connection, "minute_coverage", coverage_path)
    copy_csv(connection, "canonical_mark_bars", mark_path)
    return {
        "blocking_execution_minutes": blocking_execution,
        "blocking_mark_minutes": blocking_mark,
        "daily_mark_archive_absent_days": sorted(missing_daily),
        "accepted_mark_minutes_without_daily_archive_object": accepted_mark_without_daily,
    }


def reconcile_funding(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    archive_rows = connection.execute(
        """
        SELECT observation_row_id, source_sha256, funding_time_ms,
               funding_interval_hours, funding_rate_text, funding_rate_value
        FROM funding_observations
        WHERE source_kind = 'USDM_MONTHLY_FUNDING_ARCHIVE'
        ORDER BY funding_time_ms
        """,
    ).fetchall()
    rest_rows = connection.execute(
        """
        SELECT observation_row_id, source_sha256, funding_time_ms,
               funding_rate_text, funding_rate_value
        FROM funding_observations
        WHERE source_kind = 'USDM_REST_FUNDING_RATE'
        ORDER BY funding_time_ms
        """,
    ).fetchall()
    rest_by_time = {row[2]: row for row in rest_rows}
    blocking: list[int] = []
    events: list[tuple[Any, ...]] = []
    prior_time: int | None = None
    for archive in archive_rows:
        rest = rest_by_time.get(archive[2])
        if rest is None or archive[5] != rest[4]:
            blocking.append(archive[2])
            continue
        interval = archive[3]
        if interval is None or interval <= 0:
            blocking.append(archive[2])
            continue
        if prior_time is not None:
            expected_delta = interval * 3_600_000
            if abs((archive[2] - prior_time) - expected_delta) > 1_000:
                blocking.append(archive[2])
                continue
        prior_time = archive[2]
        material = {
            "instrument_id": PERP_INSTRUMENT,
            "funding_time_ms": archive[2],
            "funding_interval_hours": interval,
            "funding_rate": str(archive[5]),
            "source_bindings": sorted([archive[0], rest[0]]),
        }
        identity = sha256_bytes(canonical_json_bytes(material))
        events.append(
            (
                identity,
                PERP_PROFILE,
                PERP_INSTRUMENT,
                "BTCUSDT",
                archive[2],
                interval,
                archive[4],
                archive[4],
                archive[1],
                canonical_text(sorted([archive[0], rest[0]])),
            ),
        )
    extra_rest = sorted(set(rest_by_time) - {row[2] for row in archive_rows})
    blocking.extend(extra_rest)
    if events:
        connection.executemany(
            "INSERT INTO canonical_funding_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            events,
        )
    return {
        "archive_event_count": len(archive_rows),
        "rest_event_count": len(rest_rows),
        "canonical_event_count": len(events),
        "blocking_funding_times_ms": sorted(set(blocking)),
        "schedule_basis": "OFFICIAL_ARCHIVE_EXPLICIT_FUNDING_INTERVAL_HOURS_AND_REST_EVENT_MATCH",
    }


def validation_row(name: str, status: str, material: Any, checked_at: str) -> tuple[Any, ...]:
    payload = {"validation_name": name, "status": status, "material": material}
    return (
        sha256_bytes(canonical_json_bytes(payload)),
        name,
        status,
        canonical_text(material),
        checked_at,
    )


def table_row_counts(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    tables = [row[0] for row in connection.execute(
        "SELECT table_name FROM duckdb_tables() WHERE internal = false ORDER BY table_name",
    ).fetchall()]
    return {table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] for table in tables}


def min_max_timestamps(connection: duckdb.DuckDBPyConnection) -> dict[str, dict[str, int | None]]:
    mapping = {
        "spot_kline_observations": "open_time_ms",
        "spot_agg_trades": "timestamp_ms",
        "derived_spot_klines": "open_time_ms",
        "perpetual_execution_observations": "open_time_ms",
        "perpetual_mark_observations": "open_time_ms",
        "funding_observations": "funding_time_ms",
        "minute_coverage": "open_time_ms",
        "canonical_execution_bars": "open_time_ms",
        "canonical_mark_bars": "open_time_ms",
        "canonical_funding_events": "funding_time_ms",
    }
    result: dict[str, dict[str, int | None]] = {}
    for table, column in mapping.items():
        minimum, maximum = connection.execute(
            f'SELECT min("{column}"), max("{column}") FROM "{table}"',
        ).fetchone()
        result[table] = {"min": minimum, "max": maximum}
    return result


SEMANTIC_TABLES = (
    "archive_observations",
    "canonical_execution_bars",
    "canonical_funding_events",
    "canonical_mark_bars",
    "dataset_releases",
    "derived_spot_klines",
    "funding_observations",
    "http_observations",
    "instrument_metadata",
    "minute_coverage",
    "perpetual_execution_observations",
    "perpetual_mark_observations",
    "publisher_checksums",
    "raw_objects",
    "source_conflicts",
    "spot_agg_trades",
    "spot_kline_observations",
    "validation_results",
    "verified_no_trade_intervals",
)


def semantic_database_identity(connection: duckdb.DuckDBPyConnection) -> str:
    digest = hashlib.sha256()
    exclude_columns = {
        "dataset_releases": {"created_at_utc"},
        "validation_results": {"checked_at_utc"},
    }
    for table in SEMANTIC_TABLES:
        info = connection.execute(f"PRAGMA table_info('{table}')").fetchall()
        columns = [row[1] for row in info if row[1] not in exclude_columns.get(table, set())]
        order = ", ".join(f'"{column}"' for column in columns)
        cursor = connection.execute(f'SELECT {order} FROM "{table}" ORDER BY {order}')
        digest.update(canonical_json_bytes({"table": table, "columns": columns}) + b"\n")
        while True:
            rows = cursor.fetchmany(5000)
            if not rows:
                break
            for row in rows:
                digest.update(canonical_json_bytes(dict(zip(columns, row, strict=True))) + b"\n")
    return digest.hexdigest()


def identity_for_query(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    parameters: list[Any] | None = None,
) -> str:
    cursor = connection.execute(query, parameters or [])
    columns = [item[0] for item in cursor.description]
    digest = hashlib.sha256()
    while True:
        rows = cursor.fetchmany(5000)
        if not rows:
            break
        for row in rows:
            digest.update(canonical_json_bytes(dict(zip(columns, row, strict=True))) + b"\n")
    return digest.hexdigest()


def insert_dataset_releases(
    connection: duckdb.DuckDBPyConnection,
    metadata: dict[str, str],
    catalog_manifest: dict[str, Any],
    validation_status: str,
    created_at: str,
) -> list[str]:
    releases: list[str] = []
    coverage_identities = {
        profile: identity_for_query(
            connection,
            "SELECT * FROM minute_coverage WHERE market_profile = ? ORDER BY open_time_ms",
            [profile],
        )
        for profile in (SPOT_PROFILE, PERP_PROFILE)
    }
    reconciliation_identity = identity_for_query(
        connection,
        "SELECT * FROM source_conflicts ORDER BY conflict_identity",
    )
    derived_validation_identity = identity_for_query(
        connection,
        "SELECT validation_name, status, material_json FROM validation_results ORDER BY validation_name",
    )
    funding_identity = identity_for_query(
        connection,
        "SELECT * FROM canonical_funding_events ORDER BY funding_time_ms",
    )
    mark_identity = identity_for_query(
        connection,
        "SELECT * FROM canonical_mark_bars ORDER BY open_time_ms",
    )
    data_tool_identity = hash_file(DATA_TOOL_LOCK)
    raw_objects = [row[0] for row in connection.execute(
        "SELECT raw_object_sha256 FROM raw_objects ORDER BY raw_object_sha256",
    ).fetchall()]
    for profile, instrument, catalog_key in (
        (SPOT_PROFILE, SPOT_INSTRUMENT, "spot_catalog_identity"),
        (PERP_PROFILE, PERP_INSTRUMENT, "perpetual_catalog_identity"),
    ):
        status = "PASS" if validation_status == "PASS" else "DATASET_RELEASE_BLOCKED"
        material = {
            "market_profile": profile,
            "instrument_id": instrument,
            "source_objects": raw_objects,
            "normalized_time_range": {"start_ms": START_MS, "end_ms": END_MS},
            "execution_bar_interval": "1m",
            "available_signal_bar_intervals": ["1m"],
            "minute_coverage_identity": coverage_identities[profile],
            "source_reconciliation_identity": reconciliation_identity,
            "instrument_metadata_identity": metadata[profile],
            "funding_data_identity": "NOT_APPLICABLE" if profile == SPOT_PROFILE else funding_identity,
            "mark_data_identity": "NOT_APPLICABLE" if profile == SPOT_PROFILE else mark_identity,
            "normalizer_version": NORMALIZER_VERSION,
            "catalog_identity": catalog_manifest[catalog_key],
            "derived_validation_identity": derived_validation_identity,
            "data_tool_lock_identity": data_tool_identity,
            "completeness_result": status,
        }
        identity = sha256_bytes(canonical_json_bytes(material))
        connection.execute(
            "INSERT INTO dataset_releases VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                identity,
                profile,
                instrument,
                START_MS,
                END_MS,
                status,
                coverage_identities[profile],
                reconciliation_identity,
                metadata[profile],
                material["funding_data_identity"],
                material["mark_data_identity"],
                NORMALIZER_VERSION,
                catalog_manifest[catalog_key],
                derived_validation_identity,
                data_tool_identity,
                canonical_text(material),
                created_at,
            ],
        )
        releases.append(identity)
    return releases


def validate_database(
    connection: duckdb.DuckDBPyConnection,
    expected_semantic_identity: str | None = None,
) -> dict[str, Any]:
    coverage = dict(
        connection.execute(
            "SELECT market_profile, count(*) FROM minute_coverage GROUP BY market_profile",
        ).fetchall(),
    )
    duplicates = connection.execute(
        """
        SELECT count(*) FROM (
            SELECT market_profile, symbol, open_time_ms, count(*) AS n
            FROM minute_coverage GROUP BY ALL HAVING n <> 1
        )
        """,
    ).fetchone()[0]
    blockers = connection.execute(
        "SELECT count(*) FROM minute_coverage WHERE blocking",
    ).fetchone()[0]
    synthetic = connection.execute(
        """
        SELECT count(*) FROM canonical_execution_bars
        WHERE disposition NOT IN ('REAL_OFFICIAL_BAR', 'DERIVED_FROM_OFFICIAL_TRADES')
        """,
    ).fetchone()[0]
    no_trade_bars = connection.execute(
        """
        SELECT count(*) FROM minute_coverage c
        JOIN canonical_execution_bars b
          ON b.market_profile = c.market_profile AND b.open_time_ms = c.open_time_ms
        WHERE c.disposition = 'VERIFIED_NO_TRADE_INTERVAL'
        """,
    ).fetchone()[0]
    semantic = semantic_database_identity(connection)
    result = {
        "coverage_counts": coverage,
        "expected_minutes_per_profile": EXPECTED_MINUTES,
        "duplicate_coverage_rows": duplicates,
        "blocking_coverage_rows": blockers,
        "synthetic_or_forbidden_canonical_rows": synthetic,
        "verified_no_trade_exported_as_bars": no_trade_bars,
        "semantic_identity": semantic,
        "semantic_identity_matches_expected": (
            True if expected_semantic_identity is None else semantic == expected_semantic_identity
        ),
        "status": "PASS",
    }
    if (
        coverage != {SPOT_PROFILE: EXPECTED_MINUTES, PERP_PROFILE: EXPECTED_MINUTES}
        or duplicates
        or synthetic
        or no_trade_bars
        or (expected_semantic_identity is not None and semantic != expected_semantic_identity)
    ):
        result["status"] = "FAIL"
        raise ProvenanceError("DATASET_RELEASE_STALE", f"read-only validation failed: {result}")
    return result


def write_export(
    connection: duckdb.DuckDBPyConnection,
    export_dir: Path,
    name: str,
    query: str,
) -> dict[str, Any]:
    path = export_dir / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    cursor = connection.execute(query)
    columns = [item[0] for item in cursor.description]
    digest = hashlib.sha256()
    count = 0
    minimum: int | None = None
    maximum: int | None = None
    with path.open("wb") as stream:
        while True:
            rows = cursor.fetchmany(5000)
            if not rows:
                break
            for row in rows:
                payload = canonical_json_bytes(dict(zip(columns, row, strict=True))) + b"\n"
                stream.write(payload)
                digest.update(payload)
                count += 1
                for key in ("open_time_ms", "funding_time_ms"):
                    if key in columns:
                        value = int(row[columns.index(key)])
                        minimum = value if minimum is None else min(minimum, value)
                        maximum = value if maximum is None else max(maximum, value)
            stream.flush()
        os.fsync(stream.fileno())
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": digest.hexdigest(),
        "row_count": count,
        "min_timestamp_ms": minimum,
        "max_timestamp_ms": maximum,
        "size_bytes": path.stat().st_size,
    }


def export_canonical(connection: duckdb.DuckDBPyConnection, export_dir: Path) -> dict[str, Any]:
    outputs = {
        "spot_execution": write_export(
            connection,
            export_dir,
            "spot-execution-bars",
            f"SELECT * FROM canonical_execution_bars WHERE market_profile = '{SPOT_PROFILE}' ORDER BY open_time_ms",
        ),
        "perpetual_execution": write_export(
            connection,
            export_dir,
            "perpetual-execution-bars",
            f"SELECT * FROM canonical_execution_bars WHERE market_profile = '{PERP_PROFILE}' ORDER BY open_time_ms",
        ),
        "perpetual_mark": write_export(
            connection,
            export_dir,
            "perpetual-mark-bars",
            "SELECT * FROM canonical_mark_bars ORDER BY open_time_ms",
        ),
        "perpetual_funding": write_export(
            connection,
            export_dir,
            "perpetual-funding-events",
            "SELECT * FROM canonical_funding_events ORDER BY funding_time_ms",
        ),
        "minute_coverage": write_export(
            connection,
            export_dir,
            "minute-coverage",
            "SELECT * FROM minute_coverage ORDER BY market_profile, open_time_ms",
        ),
        "instrument_metadata": write_export(
            connection,
            export_dir,
            "instrument-metadata",
            "SELECT * FROM instrument_metadata ORDER BY market_profile",
        ),
    }
    material = {name: {key: value for key, value in item.items() if key != "path"} for name, item in outputs.items()}
    manifest = {
        "schema": "canonical-data-export-v1",
        "outputs": outputs,
        "semantic_export_identity": sha256_bytes(canonical_json_bytes(material)),
    }
    path = export_dir / "manifest.json"
    path.write_bytes(canonical_json_bytes(manifest) + b"\n")
    return manifest


def build(
    database_path: Path,
    staging: Path,
    export_dir: Path,
    role: str,
    catalog_manifest_path: Path | None,
) -> dict[str, Any]:
    if database_path.exists():
        raise ProvenanceError("DATASET_RELEASE_STALE", f"database target already exists: {database_path}")
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    if index.get("acquisition_identity") != "5e726b7790c3efe7ed7e2fe0f7d20853cdbc1b729968c09446560a4f1e9188a3":
        raise ProvenanceError("DATA_HASH_MISMATCH", "acquisition identity changed")
    catalog_manifest = (
        json.loads(catalog_manifest_path.read_text(encoding="utf-8"))
        if catalog_manifest_path is not None
        else None
    )
    database_path.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    checked_at = utc_now_text()
    schema_bytes = SCHEMA_PATH.read_bytes()
    schema_identity = sha256_bytes(schema_bytes)
    connection = configure_connection(database_path)
    result: dict[str, Any] = {}
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(schema_bytes.decode("utf-8"))
        connection.execute(
            "INSERT INTO schema_metadata VALUES (?, ?, ?, ?, ?, ?, ?)",
            [schema_identity, "data-provenance-duckdb-v1", checked_at, "1.4.5", False, False, False],
        )
        result.update(insert_raw_and_http(connection))
        result.update(insert_archive_inventory(connection, index))
        result.update(stage_klines(connection, index, staging))
        result["funding_observations"] = insert_funding(connection, index)
        metadata = insert_metadata(connection, index)
        derived, agg_results = stage_aggtrades_and_derivations(connection, index, staging)
        result.update(agg_results)
        spot = stage_spot_canonical(connection, staging, derived)
        perpetual = stage_perpetual_canonical(connection, index, staging)
        funding = reconcile_funding(connection)
        result["spot"] = spot
        result["perpetual"] = perpetual
        result["funding"] = funding
        blockers = (
            len(spot["blocking_minutes"])
            + len(perpetual["blocking_execution_minutes"])
            + len(perpetual["blocking_mark_minutes"])
            + len(funding["blocking_funding_times_ms"])
        )
        coverage_counts = dict(
            connection.execute(
                "SELECT market_profile, count(*) FROM minute_coverage GROUP BY market_profile",
            ).fetchall(),
        )
        duplicate_coverage = connection.execute(
            """
            SELECT count(*) FROM (
                SELECT market_profile, symbol, open_time_ms, count(*) AS n
                FROM minute_coverage GROUP BY ALL HAVING n <> 1
            )
            """,
        ).fetchone()[0]
        coverage_complete = (
            coverage_counts == {SPOT_PROFILE: EXPECTED_MINUTES, PERP_PROFILE: EXPECTED_MINUTES}
            and duplicate_coverage == 0
        )
        validation_status = "PASS" if blockers == 0 and coverage_complete else "BLOCKED"
        validation_material = {
            "blocking_count": blockers,
            "spot": spot,
            "perpetual": perpetual,
            "funding": funding,
            "no_synthetic_ohlc": True,
            "no_unofficial_source": True,
            "no_binary_float_material_calculation": True,
        }
        validations = [
            validation_row("FULL_DATASET_RELEASE_GATE", validation_status, validation_material, checked_at),
            validation_row(
                "DUCKDB_EXTENSION_AND_NETWORK_PROHIBITION",
                "PASS",
                {
                    "install_statements": 0,
                    "load_statements": 0,
                    "network_functions": 0,
                    "configuration": {
                        "allow_unsigned_extensions": False,
                        "autoinstall_known_extensions": False,
                        "autoload_known_extensions": False,
                    },
                },
                checked_at,
            ),
            validation_row(
                "ONE_COVERAGE_ROW_PER_UTC_MINUTE",
                "PASS" if coverage_complete else "FAIL",
                {
                    "actual_by_profile": coverage_counts,
                    "duplicate_rows": duplicate_coverage,
                    "expected_per_profile": EXPECTED_MINUTES,
                },
                checked_at,
            ),
        ]
        connection.executemany("INSERT INTO validation_results VALUES (?, ?, ?, ?, ?)", validations)
        releases: list[str] = []
        if catalog_manifest is not None:
            releases = insert_dataset_releases(
                connection,
                metadata,
                catalog_manifest,
                validation_status,
                checked_at,
            )
        semantic = semantic_database_identity(connection)
        counts = table_row_counts(connection)
        counts["rebuild_manifests"] = 1
        ranges = min_max_timestamps(connection)
        source_inventory = identity_for_query(
            connection,
            "SELECT raw_object_sha256, byte_length FROM raw_objects ORDER BY raw_object_sha256",
        )
        rebuild_material = {
            "database_role": role,
            "schema_identity": schema_identity,
            "semantic_identity": semantic,
            "source_inventory_identity": source_inventory,
            "row_counts": counts,
            "min_max_timestamps": ranges,
        }
        rebuild_identity = sha256_bytes(canonical_json_bytes(rebuild_material))
        connection.execute(
            "INSERT INTO rebuild_manifests VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                rebuild_identity,
                role,
                schema_identity,
                semantic,
                source_inventory,
                canonical_text(counts),
                canonical_text(ranges),
                checked_at,
            ],
        )
        connection.execute("COMMIT")
        connection.execute("CHECKPOINT")
        result.update(
            {
                "schema_identity": schema_identity,
                "semantic_identity": semantic,
                "source_inventory_identity": source_inventory,
                "rebuild_identity": rebuild_identity,
                "row_counts": counts,
                "min_max_timestamps": ranges,
                "dataset_release_ids": releases,
                "dataset_release_gate": validation_status,
            },
        )
    except BaseException:
        try:
            connection.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        connection.close()
    file_hash = hash_file(database_path)
    file_size = database_path.stat().st_size
    readonly = configure_connection(database_path, read_only=True)
    try:
        readonly_validation = validate_database(readonly, result["semantic_identity"])
        exports = export_canonical(readonly, export_dir)
    finally:
        readonly.close()
    result.update(
        {
            "database_path": str(database_path.relative_to(ROOT)),
            "database_file_sha256": file_hash,
            "database_size_bytes": file_size,
            "readonly_validation": readonly_validation,
            "exports": exports,
            "status": "BUILD_COMPLETE",
        },
    )
    result_path = export_dir / "build-result.json"
    result_path.write_bytes(canonical_json_bytes(result) + b"\n")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--role", choices=("PRIMARY", "INDEPENDENT_REBUILD"), required=True)
    parser.add_argument("--catalog-manifest", type=Path)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    result = build(
        ROOT / arguments.database if not arguments.database.is_absolute() else arguments.database,
        ROOT / arguments.staging if not arguments.staging.is_absolute() else arguments.staging,
        ROOT / arguments.export_dir if not arguments.export_dir.is_absolute() else arguments.export_dir,
        arguments.role,
        (
            ROOT / arguments.catalog_manifest
            if arguments.catalog_manifest is not None and not arguments.catalog_manifest.is_absolute()
            else arguments.catalog_manifest
        ),
    )
    print(
        json.dumps(
            {
                "database_file_sha256": result["database_file_sha256"],
                "dataset_release_gate": result["dataset_release_gate"],
                "semantic_identity": result["semantic_identity"],
                "status": result["status"],
            },
            sort_keys=True,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
