#!/usr/bin/env python3
"""Offline raw-byte qualification for free official Binance Phase A.

The program reads immutable source objects, never opens DuckDB for writing,
uses Decimal for every material numeric comparison, and writes one ignored
local analysis manifest.  It does not create a DatasetRelease or Nautilus
catalog.
"""

from __future__ import annotations

import bisect
import csv
import gc
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Iterator


ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from crypto_lab.data_provenance import AggTrade  # noqa: E402
from crypto_lab.data_provenance import AggTradeSource  # noqa: E402
from crypto_lab.data_provenance import derive_spot_kline  # noqa: E402
from crypto_lab.data_provenance import iter_aggtrade_archive  # noqa: E402


OLD_RAW = ROOT / "data/raw/data-provenance-duckdb-001"
NEW_RAW = ROOT / "data/raw/free-official-binance-data-duckdb-001"
OLD_INDEX = OLD_RAW / "acquisition-index.json"
NEW_INDEX = NEW_RAW / "phase-a-acquisition.json"
OUTPUT = NEW_RAW / "phase-a-analysis.json"

N0_START = 1606780800000  # 2020-12-01T00:00:00Z
N0_END = 1625097600000  # 2021-07-01T00:00:00Z
N1_START = 1609459200000  # 2021-01-01T00:00:00Z
N1_END = 1627776000000  # 2021-08-01T00:00:00Z
JULY_START = 1625097600000
SPOT_TARGET = 1613014800000  # 2021-02-11T03:40:00Z
MARK_GAP_START = 1608190320000  # 2020-12-17T07:32:00Z
MARK_GAP_END = 1608191760000  # 2020-12-17T07:56:00Z
ONE_MINUTE_MS = 60_000
EXPECTED_MINUTES = (N1_END - N1_START) // ONE_MINUTE_MS

_CHECKSUM = re.compile(r"([0-9a-f]{64})  ([^/\\]+)\Z")


def utc_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc_text(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid Decimal {field}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite Decimal {field}")
    return result


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def replace_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="analysis-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


@dataclass(frozen=True)
class Kline:
    open_time_ms: int
    close_time_ms: int
    material: tuple[Any, ...]
    semantic_sha256: str
    invalid_reasons: tuple[str, ...]
    trade_count: int
    base_volume: Decimal
    source_sha256: str
    source_role: str
    original_fields: tuple[str, ...]


def parse_kline(fields: list[Any], source_sha: str, source_role: str, kind: str) -> Kline:
    if len(fields) != 12:
        raise ValueError(f"{source_role}: expected 12 kline fields, got {len(fields)}")
    text = [str(value) for value in fields]
    open_time = int(text[0])
    close_time = int(text[6])
    open_, high, low, close = (decimal(text[index], f"ohlc[{index}]") for index in (1, 2, 3, 4))
    base_volume = decimal(text[5], "base_volume")
    quote_volume = decimal(text[7], "quote_volume")
    trade_count = int(text[8])
    taker_base = decimal(text[9], "taker_base")
    taker_quote = decimal(text[10], "taker_quote")
    reasons: list[str] = []
    if open_time % ONE_MINUTE_MS:
        reasons.append("OFF_GRID_OPEN_TIME")
    if close_time != open_time + ONE_MINUTE_MS - 1:
        reasons.append("INVALID_CLOSE_TIME")
    if high < max(open_, close) or low > min(open_, close) or high < low:
        reasons.append("INVALID_OHLC")
    if any(value < 0 for value in (base_volume, quote_volume, taker_base, taker_quote)):
        reasons.append("NEGATIVE_VOLUME")
    if trade_count < 0:
        reasons.append("NEGATIVE_TRADE_COUNT")
    if kind == "execution":
        material: tuple[Any, ...] = (
            open_time,
            decimal_text(open_),
            decimal_text(high),
            decimal_text(low),
            decimal_text(close),
            decimal_text(base_volume),
            close_time,
            decimal_text(quote_volume),
            trade_count,
            decimal_text(taker_base),
            decimal_text(taker_quote),
        )
    elif kind == "mark":
        material = (
            open_time,
            decimal_text(open_),
            decimal_text(high),
            decimal_text(low),
            decimal_text(close),
            close_time,
        )
    else:
        raise AssertionError(kind)
    return Kline(
        open_time_ms=open_time,
        close_time_ms=close_time,
        material=material,
        semantic_sha256=sha256_bytes(canonical_json(material)),
        invalid_reasons=tuple(reasons),
        trade_count=trade_count,
        base_volume=base_volume,
        source_sha256=source_sha,
        source_role=source_role,
        original_fields=tuple(text),
    )


def archive_path(pair: dict[str, Any]) -> Path:
    archive = pair["archive"]
    value = archive.get("local_object_path") or archive.get("raw_object_path")
    return ROOT / value


def checksum_path(pair: dict[str, Any]) -> Path | None:
    checksum = pair.get("checksum")
    if checksum is None:
        return None
    value = checksum.get("local_object_path") or checksum.get("raw_object_path")
    return ROOT / value


def task_value(pair: dict[str, Any], old_name: str, new_name: str | None = None) -> Any:
    task = pair["task"]
    return task.get(old_name, task.get(new_name or old_name))


def verify_pair(pair: dict[str, Any]) -> dict[str, Any]:
    archive = pair["archive"]
    expected_sha = archive["raw_object_sha256"]
    path = archive_path(pair)
    if not path.is_file() or file_sha256(path) != expected_sha:
        raise ValueError(f"archive raw identity mismatch: {path}")
    if not pair.get("archive_available", True):
        return {"archive_available": False, "archive_sha256": expected_sha, "checksum_verified": None}
    checksum = checksum_path(pair)
    if checksum is None:
        raise ValueError(f"missing publisher checksum: {path}")
    checksum_record = pair["checksum"]
    if file_sha256(checksum) != checksum_record["raw_object_sha256"]:
        raise ValueError(f"checksum object identity mismatch: {checksum}")
    text = checksum.read_text(encoding="ascii").strip()
    match = _CHECKSUM.fullmatch(text)
    filename = task_value(pair, "exact_filename", "filename")
    if match is None or match.group(2) != filename:
        raise ValueError(f"malformed checksum: {checksum}")
    if match.group(1) != expected_sha:
        raise ValueError(f"publisher checksum mismatch: {path}")
    return {"archive_available": True, "archive_sha256": expected_sha, "checksum_verified": True}


def expected_member(pair: dict[str, Any]) -> str:
    task = pair["task"]
    return task.get("expected_member") or task_value(pair, "filename").removesuffix(".zip") + ".csv"


def iter_archive_klines(pair: dict[str, Any], source_role: str, kind: str) -> Iterator[Kline]:
    path = archive_path(pair)
    source_sha = pair["archive"]["raw_object_sha256"]
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].is_dir() or members[0].filename != expected_member(pair):
            raise ValueError(f"archive member mismatch: {path}")
        with archive.open(members[0]) as binary:
            rows = csv.reader(io.TextIOWrapper(binary, encoding="utf-8", newline=""), strict=True)
            prior: int | None = None
            for row in rows:
                if row and row[0] == "open_time":
                    continue
                item = parse_kline(row, source_sha, source_role, kind)
                if prior is not None and item.open_time_ms <= prior:
                    raise ValueError(f"duplicate or non-monotonic archive row: {path}")
                prior = item.open_time_ms
                yield item


def build_archive_map(
    pairs: Iterable[dict[str, Any]],
    *,
    source_role: str,
    kind: str,
    start_ms: int,
    end_ms: int,
) -> tuple[dict[int, Kline], dict[str, Any]]:
    result: dict[int, Kline] = {}
    invalid: list[dict[str, Any]] = []
    pair_count = 0
    unavailable: list[dict[str, Any]] = []
    source_hashes: list[str] = []
    for pair in sorted(pairs, key=lambda value: (task_value(value, "range_start_ms", "start_ms"), value["task"]["url"])):
        pair_count += 1
        verified = verify_pair(pair)
        source_hashes.append(verified["archive_sha256"])
        if not verified["archive_available"]:
            unavailable.append(
                {
                    "start_ms": task_value(pair, "range_start_ms", "start_ms"),
                    "end_ms": task_value(pair, "range_end_ms", "end_ms"),
                    "url": pair["task"]["url"],
                    "http_status": pair["archive"].get("status_code", pair["archive"].get("http_status")),
                    "raw_object_sha256": pair["archive"]["raw_object_sha256"],
                },
            )
            continue
        for item in iter_archive_klines(pair, source_role, kind):
            if item.open_time_ms < start_ms or item.open_time_ms >= end_ms:
                continue
            if item.open_time_ms in result:
                raise ValueError(f"duplicate {source_role} timestamp: {item.open_time_ms}")
            result[item.open_time_ms] = item
            if item.invalid_reasons:
                invalid.append(
                    {
                        "open_time_ms": item.open_time_ms,
                        "open_time_utc": utc_text(item.open_time_ms),
                        "reasons": list(item.invalid_reasons),
                        "source_sha256": item.source_sha256,
                    },
                )
    return result, {
        "source_role": source_role,
        "pair_count": pair_count,
        "row_count": len(result),
        "invalid_row_count": len(invalid),
        "invalid_rows": invalid,
        "unavailable_object_count": len(unavailable),
        "unavailable_objects": unavailable,
        "source_object_sha256s": sorted(source_hashes),
    }


def observation_path(observation: dict[str, Any]) -> Path:
    value = observation.get("local_object_path") or observation.get("raw_object_path")
    return ROOT / value


def rest_item_parts(item: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
    if "observation" in item:
        observation = item["observation"]
        task = item["task"]
        return observation, task["range_start_ms"], task["range_end_ms"]
    return item, item["requested_start_ms"], item["requested_end_ms"]


def build_rest_map(
    items: Iterable[dict[str, Any]],
    *,
    source_role: str,
    kind: str,
    start_ms: int,
    end_ms: int,
) -> tuple[dict[int, Kline], dict[str, Any]]:
    selected: list[tuple[dict[str, Any], int, int]] = []
    for item in items:
        observation, page_start, page_end = rest_item_parts(item)
        if page_end <= start_ms or page_start >= end_ms:
            continue
        selected.append((observation, page_start, page_end))
    selected.sort(key=lambda value: value[1])
    if not selected or selected[0][1] > start_ms or selected[-1][2] < end_ms:
        raise ValueError(f"{source_role}: REST page range does not bind complete window")
    for first, second in zip(selected, selected[1:], strict=False):
        if first[2] != second[1]:
            raise ValueError(f"{source_role}: REST pagination gap or overlap")
    result: dict[int, Kline] = {}
    invalid: list[dict[str, Any]] = []
    hashes: list[str] = []
    for observation, page_start, page_end in selected:
        path = observation_path(observation)
        source_sha = observation["raw_object_sha256"]
        if file_sha256(path) != source_sha:
            raise ValueError(f"REST raw identity mismatch: {path}")
        if int(observation.get("status_code", observation.get("http_status"))) != 200:
            raise ValueError(f"REST page did not return HTTP 200: {path}")
        hashes.append(source_sha)
        rows = json.loads(path.read_bytes())
        if not isinstance(rows, list) or len(rows) > 1000:
            raise ValueError(f"malformed REST page: {path}")
        prior: int | None = None
        for fields in rows:
            item = parse_kline(fields, source_sha, source_role, kind)
            if item.open_time_ms < page_start or item.open_time_ms >= page_end:
                raise ValueError(f"REST row outside requested page: {path}")
            if prior is not None and item.open_time_ms <= prior:
                raise ValueError(f"REST page duplicate/non-monotonic: {path}")
            prior = item.open_time_ms
            if item.open_time_ms < start_ms or item.open_time_ms >= end_ms:
                continue
            if item.open_time_ms in result:
                raise ValueError(f"REST pagination duplicate: {item.open_time_ms}")
            result[item.open_time_ms] = item
            if item.invalid_reasons:
                invalid.append(
                    {
                        "open_time_ms": item.open_time_ms,
                        "open_time_utc": utc_text(item.open_time_ms),
                        "reasons": list(item.invalid_reasons),
                        "source_sha256": item.source_sha256,
                    },
                )
    return result, {
        "source_role": source_role,
        "page_count": len(selected),
        "row_count": len(result),
        "invalid_row_count": len(invalid),
        "invalid_rows": invalid,
        "pagination_complete": True,
        "source_object_sha256s": sorted(hashes),
    }


def old_archive_pairs(index: dict[str, Any], category: str, cadence: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    return [
        pair
        for pair in index["archive_pairs"]
        if pair["task"]["category"] == category
        and pair["task"]["cadence"] == cadence
        and pair["task"]["range_start_ms"] >= start_ms
        and pair["task"]["range_end_ms"] <= end_ms
    ]


def new_archive_pairs(index: dict[str, Any], source_role: str) -> list[dict[str, Any]]:
    return [pair for pair in index["archive_pairs"] if pair["task"]["source_role"] == source_role]


def kline_group(rows: Iterable[Kline | None]) -> list[Kline]:
    return [row for row in rows if row is not None]


def derived_material(events: list[AggTrade], minute: int) -> tuple[Any, ...]:
    derived = derive_spot_kline(events, minute_start_ms=minute)
    return (
        derived.open_time_ms,
        decimal_text(Decimal(derived.open_text)),
        decimal_text(Decimal(derived.high_text)),
        decimal_text(Decimal(derived.low_text)),
        decimal_text(Decimal(derived.close_text)),
        decimal_text(Decimal(derived.base_volume_text)),
        derived.close_time_ms,
        decimal_text(Decimal(derived.quote_volume_text)),
        derived.trade_count,
        decimal_text(Decimal(derived.taker_buy_base_text)),
        decimal_text(Decimal(derived.taker_buy_quote_text)),
    )


def select_daily_aggtrade_pairs(old: dict[str, Any], new: dict[str, Any]) -> dict[date, dict[str, Any]]:
    result: dict[date, dict[str, Any]] = {}
    for pair in old["archive_pairs"]:
        task = pair["task"]
        if task["category"] != "spot_aggtrades" or task["cadence"] != "daily":
            continue
        day = datetime.fromtimestamp(task["range_start_ms"] / 1000, tz=UTC).date()
        result[day] = pair
    for pair in new["archive_pairs"]:
        if pair["task"]["source_role"] != "SPOT_TARGET_DAILY_AGGTRADES":
            continue
        day = datetime.fromtimestamp(pair["task"]["start_ms"] / 1000, tz=UTC).date()
        result[day] = pair
    return result


def collect_aggtrade_context(pair: dict[str, Any], targets: list[int]) -> dict[int, dict[str, Any]]:
    verify_pair(pair)
    path = archive_path(pair)
    source_sha = pair["archive"]["raw_object_sha256"]
    target_set = set(targets)
    states = {
        target: {"before": None, "after": None, "events": []}
        for target in targets
    }
    before_index = 0
    after_index = 0
    last_event: AggTrade | None = None
    events = iter_aggtrade_archive(
        path,
        source_kind=AggTradeSource.SPOT_DAILY,
        source_sha256=source_sha,
        expected_member=expected_member(pair),
    )
    row_count = 0
    for event in events:
        row_count += 1
        while before_index < len(targets) and event.timestamp_ms >= targets[before_index]:
            states[targets[before_index]]["before"] = last_event
            before_index += 1
        while after_index < len(targets) and event.timestamp_ms >= targets[after_index] + ONE_MINUTE_MS:
            states[targets[after_index]]["after"] = event
            after_index += 1
        minute = event.timestamp_ms - event.timestamp_ms % ONE_MINUTE_MS
        if minute in target_set:
            states[minute]["events"].append(event)
        last_event = event
    for target in targets:
        states[target]["archive_row_count"] = row_count
        states[target]["source_sha256"] = source_sha
    return states


def summarize_event(event: AggTrade | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "aggregate_trade_id": event.aggregate_trade_id,
        "first_trade_id": event.first_trade_id,
        "last_trade_id": event.last_trade_id,
        "timestamp_ms": event.timestamp_ms,
        "timestamp_utc": utc_text(event.timestamp_ms),
        "price_text": event.price_text,
        "quantity_text": event.quantity_text,
        "buyer_is_maker": event.buyer_is_maker,
    }


def inspect_raw_trades_target(new: dict[str, Any]) -> dict[str, Any]:
    pair = next(
        pair for pair in new["archive_pairs"]
        if pair["task"]["source_role"] == "SPOT_TARGET_DAILY_TRADES"
    )
    verified = verify_pair(pair)
    path = archive_path(pair)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    inside: list[dict[str, Any]] = []
    prior: dict[str, Any] | None = None
    row_count = 0
    id_gap_count = 0
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].filename != expected_member(pair):
            raise ValueError("raw trades archive member mismatch")
        with archive.open(members[0]) as binary:
            rows = csv.reader(io.TextIOWrapper(binary, encoding="utf-8", newline=""), strict=True)
            for row in rows:
                if row and row[0] in {"id", "trade_id"}:
                    continue
                if len(row) != 7:
                    raise ValueError("raw trade row schema mismatch")
                current = {
                    "trade_id": int(row[0]),
                    "price_text": row[1],
                    "quantity_text": row[2],
                    "quote_quantity_text": row[3],
                    "timestamp_ms": int(row[4]),
                    "buyer_is_maker": row[5].lower() == "true",
                    "best_price_match": row[6].lower() == "true",
                }
                decimal(current["price_text"], "trade price")
                decimal(current["quantity_text"], "trade quantity")
                decimal(current["quote_quantity_text"], "trade quote quantity")
                if prior is not None and current["trade_id"] != prior["trade_id"] + 1:
                    id_gap_count += 1
                row_count += 1
                if current["timestamp_ms"] < SPOT_TARGET:
                    before = current
                elif current["timestamp_ms"] < SPOT_TARGET + ONE_MINUTE_MS:
                    inside.append(current)
                elif after is None:
                    after = current
                prior = current
    if before is None or after is None:
        raise ValueError("raw trades do not bracket Spot target")
    return {
        "archive_sha256": verified["archive_sha256"],
        "publisher_checksum_match": verified["checksum_verified"],
        "row_count": row_count,
        "trade_id_gap_count": id_gap_count,
        "events_inside_target_minute": len(inside),
        "boundary_before": before,
        "boundary_after": after,
        "boundary_trade_ids_contiguous": after["trade_id"] == before["trade_id"] + 1,
        "no_synthetic_bar_created": True,
    }


def inspect_raw_trades_minute(new: dict[str, Any], source_role: str, minute: int) -> dict[str, Any]:
    pair = next(pair for pair in new["archive_pairs"] if pair["task"]["source_role"] == source_role)
    verified = verify_pair(pair)
    path = archive_path(pair)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    inside: list[dict[str, Any]] = []
    prior: dict[str, Any] | None = None
    row_count = 0
    id_gap_count = 0
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].filename != expected_member(pair):
            raise ValueError("raw trades archive member mismatch")
        with archive.open(members[0]) as binary:
            rows = csv.reader(io.TextIOWrapper(binary, encoding="utf-8", newline=""), strict=True)
            for row in rows:
                if row and row[0] in {"id", "trade_id"}:
                    continue
                if len(row) != 7:
                    raise ValueError("raw trade row schema mismatch")
                current = {
                    "trade_id": int(row[0]),
                    "price_text": row[1],
                    "quantity_text": row[2],
                    "quote_quantity_text": row[3],
                    "timestamp_ms": int(row[4]),
                    "buyer_is_maker": row[5].lower() == "true",
                    "best_price_match": row[6].lower() == "true",
                }
                decimal(current["price_text"], "trade price")
                decimal(current["quantity_text"], "trade quantity")
                decimal(current["quote_quantity_text"], "trade quote quantity")
                if prior is not None and current["trade_id"] != prior["trade_id"] + 1:
                    id_gap_count += 1
                row_count += 1
                if current["timestamp_ms"] < minute:
                    before = current
                elif current["timestamp_ms"] < minute + ONE_MINUTE_MS:
                    inside.append(current)
                elif after is None:
                    after = current
                prior = current
    if before is None or after is None:
        raise ValueError("raw trades do not bracket minute")
    derived: tuple[Any, ...] | None = None
    if inside:
        if [item["trade_id"] for item in inside] != list(
            range(inside[0]["trade_id"], inside[-1]["trade_id"] + 1),
        ):
            raise ValueError("raw trades inside minute have an ID gap")
        prices = [decimal(item["price_text"], "trade price") for item in inside]
        quantities = [decimal(item["quantity_text"], "trade quantity") for item in inside]
        taker = [
            (price, quantity)
            for item, price, quantity in zip(inside, prices, quantities, strict=True)
            if not item["buyer_is_maker"]
        ]
        derived = (
            minute,
            decimal_text(prices[0]),
            decimal_text(max(prices)),
            decimal_text(min(prices)),
            decimal_text(prices[-1]),
            decimal_text(sum(quantities, Decimal(0))),
            minute + ONE_MINUTE_MS - 1,
            decimal_text(sum((price * quantity for price, quantity in zip(prices, quantities, strict=True)), Decimal(0))),
            len(inside),
            decimal_text(sum((quantity for _, quantity in taker), Decimal(0))),
            decimal_text(sum((price * quantity for price, quantity in taker), Decimal(0))),
        )
    return {
        "archive_sha256": verified["archive_sha256"],
        "publisher_checksum_match": verified["checksum_verified"],
        "row_count": row_count,
        "trade_id_gap_count": id_gap_count,
        "events_inside_minute": len(inside),
        "first_inside_trade_id": inside[0]["trade_id"] if inside else None,
        "last_inside_trade_id": inside[-1]["trade_id"] if inside else None,
        "boundary_before": before,
        "boundary_after": after,
        "derived_material": list(derived) if derived is not None else None,
        "derived_semantic_sha256": sha256_bytes(canonical_json(derived)) if derived is not None else None,
        "no_synthetic_bar_created": True,
    }


def inspect_raw_trade_no_trade_ranges(
    new: dict[str, Any],
    dispositions: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    targets_by_day: dict[str, list[int]] = defaultdict(list)
    for item in dispositions:
        if item["disposition"] == "VERIFIED_NO_TRADE_INTERVAL":
            targets_by_day[item["open_time_utc"][:10]].append(item["open_time_ms"])

    role_by_day = {
        "2021-02-11": "SPOT_TARGET_DAILY_TRADES",
        "2021-03-06": "SPOT_NO_TRADE_2021_03_06_DAILY_TRADES",
        "2021-04-20": "SPOT_NO_TRADE_2021_04_20_DAILY_TRADES",
        "2021-04-25": "SPOT_PARTIAL_2021_04_25_DAILY_TRADES",
    }
    if set(targets_by_day) != set(role_by_day):
        raise ValueError(f"unexpected no-trade days: {sorted(targets_by_day)}")

    result: dict[int, dict[str, Any]] = {}
    for day, targets in sorted(targets_by_day.items()):
        ordered = sorted(targets)
        ranges: list[dict[str, Any]] = []
        start = ordered[0]
        prior = ordered[0]
        for minute in ordered[1:]:
            if minute != prior + ONE_MINUTE_MS:
                ranges.append({"start_ms": start, "end_ms": prior + ONE_MINUTE_MS})
                start = minute
            prior = minute
        ranges.append({"start_ms": start, "end_ms": prior + ONE_MINUTE_MS})

        role = role_by_day[day]
        pair = next(pair for pair in new["archive_pairs"] if pair["task"]["source_role"] == role)
        verified = verify_pair(pair)
        path = archive_path(pair)
        prior_trade: dict[str, Any] | None = None
        row_count = 0
        id_gap_count = 0
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != expected_member(pair):
                raise ValueError(f"raw trades member mismatch for {day}")
            with archive.open(members[0]) as binary:
                rows = csv.reader(io.TextIOWrapper(binary, encoding="utf-8", newline=""), strict=True)
                for row in rows:
                    if row and row[0] in {"id", "trade_id"}:
                        continue
                    if len(row) != 7:
                        raise ValueError(f"raw trade schema mismatch for {day}")
                    current = {
                        "trade_id": int(row[0]),
                        "price_text": row[1],
                        "quantity_text": row[2],
                        "quote_quantity_text": row[3],
                        "timestamp_ms": int(row[4]),
                        "timestamp_utc": utc_text(int(row[4])),
                        "buyer_is_maker": row[5].lower() == "true",
                        "best_price_match": row[6].lower() == "true",
                    }
                    decimal(current["price_text"], "trade price")
                    decimal(current["quantity_text"], "trade quantity")
                    decimal(current["quote_quantity_text"], "trade quote quantity")
                    if prior_trade is not None and current["trade_id"] != prior_trade["trade_id"] + 1:
                        id_gap_count += 1
                    row_count += 1
                    for interval in ranges:
                        if current["timestamp_ms"] < interval["start_ms"]:
                            interval["before"] = current
                        elif current["timestamp_ms"] < interval["end_ms"]:
                            interval["inside_count"] = interval.get("inside_count", 0) + 1
                        elif "after" not in interval:
                            interval["after"] = current
                    prior_trade = current

        for interval in ranges:
            before = interval.get("before")
            after = interval.get("after")
            inside_count = interval.get("inside_count", 0)
            if before is None or after is None:
                raise ValueError(f"raw trades do not bracket no-trade range on {day}")
            proof = {
                "source_role": role,
                "archive_sha256": verified["archive_sha256"],
                "publisher_checksum_match": verified["checksum_verified"],
                "archive_row_count": row_count,
                "archive_trade_id_gap_count": id_gap_count,
                "range_start_ms": interval["start_ms"],
                "range_start_utc": utc_text(interval["start_ms"]),
                "range_end_exclusive_ms": interval["end_ms"],
                "range_end_exclusive_utc": utc_text(interval["end_ms"]),
                "range_minute_count": (interval["end_ms"] - interval["start_ms"]) // ONE_MINUTE_MS,
                "events_inside_range": inside_count,
                "boundary_before": before,
                "boundary_after": after,
                "boundary_trade_ids_contiguous": after["trade_id"] == before["trade_id"] + 1,
            }
            proof["status"] = "PASS" if (
                id_gap_count == 0
                and inside_count == 0
                and proof["boundary_trade_ids_contiguous"]
            ) else "BLOCKED"
            for minute in range(interval["start_ms"], interval["end_ms"], ONE_MINUTE_MS):
                result[minute] = proof
    return result


def scan_spot(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    daily_pairs = old_archive_pairs(old, "spot_execution", "daily", N1_START, JULY_START)
    daily_pairs += new_archive_pairs(new, "SPOT_DAILY_KLINES")
    monthly_pairs = old_archive_pairs(old, "spot_execution", "monthly", N1_START, JULY_START)
    monthly_pairs += new_archive_pairs(new, "SPOT_MONTHLY_KLINES")
    rest_items = new["spot_rest_pages"]

    daily, daily_summary = build_archive_map(
        daily_pairs,
        source_role="SPOT_DAILY_KLINES",
        kind="execution",
        start_ms=N1_START,
        end_ms=N1_END,
    )
    monthly, monthly_summary = build_archive_map(
        monthly_pairs,
        source_role="SPOT_MONTHLY_KLINES",
        kind="execution",
        start_ms=N1_START,
        end_ms=N1_END,
    )
    rest, rest_summary = build_rest_map(
        rest_items,
        source_role="SPOT_REST_KLINES_DATA_API",
        kind="execution",
        start_ms=N1_START,
        end_ms=N1_END,
    )

    anomalies: list[int] = []
    canonical_fast = 0
    for minute in range(N1_START, N1_END, ONE_MINUTE_MS):
        rows = (rest.get(minute), daily.get(minute), monthly.get(minute))
        present = kline_group(rows)
        if (
            len(present) == 3
            and not any(row.invalid_reasons for row in present)
            and len({row.semantic_sha256 for row in present}) == 1
            and all(row.trade_count > 0 and row.base_volume > 0 for row in present)
        ):
            canonical_fast += 1
        else:
            anomalies.append(minute)

    by_day: dict[date, list[int]] = defaultdict(list)
    for minute in anomalies:
        by_day[datetime.fromtimestamp(minute / 1000, tz=UTC).date()].append(minute)
    agg_pairs = select_daily_aggtrade_pairs(old, new)
    missing_event_days = sorted(day.isoformat() for day in by_day if day not in agg_pairs)

    partial_minute = 1619323200000  # 2021-04-25T04:00:00Z
    raw_partial = inspect_raw_trades_minute(
        new,
        "SPOT_PARTIAL_2021_04_25_DAILY_TRADES",
        partial_minute,
    )
    dispositions: list[dict[str, Any]] = []
    counts = Counter({"REAL_OFFICIAL_BAR": canonical_fast})
    unresolved: list[dict[str, Any]] = []
    resolved_conflict_count = 0
    for day in sorted(by_day):
        targets = sorted(by_day[day])
        if day not in agg_pairs:
            for minute in targets:
                item = {
                    "open_time_ms": minute,
                    "open_time_utc": utc_text(minute),
                    "disposition": "SOURCE_INCOMPLETE",
                    "reason": "OFFICIAL_EVENT_ARCHIVE_NOT_ACQUIRED_FOR_ANOMALY_DAY",
                }
                dispositions.append(item)
                unresolved.append(item)
                counts[item["disposition"]] += 1
            continue
        contexts = collect_aggtrade_context(agg_pairs[day], targets)
        for minute in targets:
            context = contexts[minute]
            events: list[AggTrade] = context["events"]
            before: AggTrade | None = context["before"]
            after: AggTrade | None = context["after"]
            rows = {
                "REST": rest.get(minute),
                "DAILY": daily.get(minute),
                "MONTHLY": monthly.get(minute),
            }
            present = [row for row in rows.values() if row is not None]
            valid = [row for row in present if not row.invalid_reasons]
            evidence = {
                "event_source_sha256": context["source_sha256"],
                "event_count": len(events),
                "before": summarize_event(before),
                "after": summarize_event(after),
                "kline_observations": {
                    role: None if row is None else {
                        "source_sha256": row.source_sha256,
                        "semantic_sha256": row.semantic_sha256,
                        "invalid_reasons": list(row.invalid_reasons),
                        "trade_count": row.trade_count,
                        "base_volume": decimal_text(row.base_volume),
                        "original_fields": list(row.original_fields),
                    }
                    for role, row in rows.items()
                },
            }
            if events:
                derived = derived_material(events, minute)
                derived_hash = sha256_bytes(canonical_json(derived))
                matching_roles = [role for role, row in rows.items() if row is not None and row.material == derived]
                evidence["derived_semantic_sha256"] = derived_hash
                evidence["derived_matches"] = matching_roles
                if matching_roles:
                    differing_roles = [role for role, row in rows.items() if row is not None and row.material != derived]
                    disposition = "DERIVED_FROM_OFFICIAL_TRADES" if len(present) < 3 or any(row.invalid_reasons for row in present) else "REAL_OFFICIAL_BAR"
                    reason = "OFFICIAL_AGGTRADES_MATCH_OFFICIAL_KLINE_OBSERVATION"
                    item = {
                        "open_time_ms": minute,
                        "open_time_utc": utc_text(minute),
                        "disposition": disposition,
                        "reason": reason,
                        "superseded_source_roles": differing_roles,
                        "evidence": evidence,
                    }
                    if differing_roles:
                        resolved_conflict_count += 1
                elif (
                    minute == partial_minute
                    and present
                    and all(row.invalid_reasons == ("INVALID_CLOSE_TIME",) for row in present)
                    and all(row.material[:6] + row.material[7:] == derived[:6] + derived[7:] for row in present)
                    and tuple(raw_partial["derived_material"] or ()) == derived
                    and raw_partial["trade_id_gap_count"] == 0
                ):
                    evidence["raw_trades_source_sha256"] = raw_partial["archive_sha256"]
                    evidence["raw_trades_derived_semantic_sha256"] = raw_partial["derived_semantic_sha256"]
                    evidence["raw_trades_and_aggtrades_exact_match"] = True
                    item = {
                        "open_time_ms": minute,
                        "open_time_utc": utc_text(minute),
                        "disposition": "DERIVED_FROM_OFFICIAL_TRADES",
                        "reason": "INVALID_KLINE_CLOSE_TIME_SUPERSEDED_BY_COMPLETE_RAW_TRADES_AND_AGGTRADES",
                        "superseded_source_roles": sorted(rows),
                        "evidence": evidence,
                    }
                    resolved_conflict_count += 1
                else:
                    item = {
                        "open_time_ms": minute,
                        "open_time_utc": utc_text(minute),
                        "disposition": "SOURCE_CONFLICT",
                        "reason": "OFFICIAL_EVENTS_MATCH_NO_OFFICIAL_KLINE_OBSERVATION",
                        "evidence": evidence,
                    }
                    unresolved.append(item)
            else:
                continuity = (
                    before is not None
                    and after is not None
                    and after.aggregate_trade_id == before.aggregate_trade_id + 1
                    and after.first_trade_id == before.last_trade_id + 1
                )
                contradicting_trade_claim = any(row.trade_count > 0 or row.base_volume > 0 for row in present)
                if continuity and not contradicting_trade_claim:
                    excluded = [role for role, row in rows.items() if row is not None]
                    item = {
                        "open_time_ms": minute,
                        "open_time_utc": utc_text(minute),
                        "disposition": "VERIFIED_NO_TRADE_INTERVAL",
                        "reason": "COMPLETE_OFFICIAL_AGGREGATE_AND_TRADE_ID_CONTINUITY_NO_EVENTS",
                        "excluded_zero_event_kline_roles": excluded,
                        "evidence": evidence,
                    }
                    if excluded:
                        resolved_conflict_count += 1
                else:
                    item = {
                        "open_time_ms": minute,
                        "open_time_utc": utc_text(minute),
                        "disposition": "SOURCE_CONFLICT" if contradicting_trade_claim else "SOURCE_INCOMPLETE",
                        "reason": "NO_TRADE_PROOF_CONTRADICTED_OR_ID_CONTINUITY_INCOMPLETE",
                        "evidence": evidence,
                    }
                    unresolved.append(item)
            dispositions.append(item)
            counts[item["disposition"]] += 1

    raw_no_trade_proofs = inspect_raw_trade_no_trade_ranges(new, dispositions)
    for item in dispositions:
        if item["disposition"] != "VERIFIED_NO_TRADE_INTERVAL":
            continue
        proof = raw_no_trade_proofs[item["open_time_ms"]]
        agg_before = item["evidence"]["before"]
        agg_after = item["evidence"]["after"]
        cross_role_match = (
            proof["status"] == "PASS"
            and agg_before is not None
            and agg_after is not None
            and proof["boundary_before"]["trade_id"] == agg_before["last_trade_id"]
            and proof["boundary_after"]["trade_id"] == agg_after["first_trade_id"]
        )
        item["evidence"]["raw_trade_range_proof"] = proof
        item["evidence"]["raw_trade_and_aggtrade_boundaries_match"] = cross_role_match
        if not cross_role_match:
            counts["VERIFIED_NO_TRADE_INTERVAL"] -= 1
            counts["SOURCE_CONFLICT"] += 1
            item["disposition"] = "SOURCE_CONFLICT"
            item["reason"] = "RAW_TRADE_AND_AGGTRADE_NO_TRADE_PROOFS_DISAGREE"
            unresolved.append(item)

    if sum(counts.values()) != EXPECTED_MINUTES:
        raise ValueError("Spot disposition count does not cover exact candidate grid")

    raw_target = inspect_raw_trades_target(new)
    target_disposition = next(item for item in dispositions if item["open_time_ms"] == SPOT_TARGET)
    target_agg_before = target_disposition["evidence"]["before"]
    target_agg_after = target_disposition["evidence"]["after"]
    target_cross_source_consistent = (
        raw_target["boundary_before"]["trade_id"] == target_agg_before["last_trade_id"]
        and raw_target["boundary_after"]["trade_id"] == target_agg_after["first_trade_id"]
        and raw_target["events_inside_target_minute"] == 0
        and target_disposition["evidence"]["event_count"] == 0
    )
    if not target_cross_source_consistent:
        raise ValueError("raw trades and aggTrades disagree at Spot target")

    result = {
        "status": "PASS" if not unresolved else "BLOCKED",
        "window_start_ms": N1_START,
        "window_end_ms": N1_END,
        "expected_minute_count": EXPECTED_MINUTES,
        "source_summaries": {
            "daily": daily_summary,
            "monthly": monthly_summary,
            "rest": rest_summary,
        },
        "disposition_counts": dict(sorted(counts.items())),
        "anomaly_minute_count": len(anomalies),
        "resolved_conflict_observation_count": resolved_conflict_count,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "missing_event_archive_days": missing_event_days,
        "raw_trade_no_trade_range_proofs": sorted(
            {
                (proof["archive_sha256"], proof["range_start_ms"]): proof
                for proof in raw_no_trade_proofs.values()
            }.values(),
            key=lambda proof: proof["range_start_ms"],
        ),
        "anomaly_dispositions": dispositions,
        "target_2021_02_11_03_40": {
            "disposition": target_disposition,
            "raw_trades": raw_target,
            "trades_and_aggtrades_boundary_match": target_cross_source_consistent,
            "canonical_bar_created": False,
            "synthetic_ohlcv_created": False,
        },
        "partial_2021_04_25_04_00": {
            "raw_trades": raw_partial,
            "disposition": next(item for item in dispositions if item["open_time_ms"] == partial_minute),
        },
    }
    del daily, monthly, rest
    gc.collect()
    return result


def source_kind_pairs(old: dict[str, Any], new: dict[str, Any], category: str, old_role: str, new_role: str) -> list[dict[str, Any]]:
    cadence = "daily" if "DAILY" in old_role else "monthly"
    pairs = old_archive_pairs(old, category, cadence, N1_START, JULY_START)
    pairs += new_archive_pairs(new, new_role)
    return pairs


def old_fapi_rest(old: dict[str, Any], category: str) -> list[dict[str, Any]]:
    return [
        item for item in old["rest_kline_pages"]
        if item["task"]["category"] == category
        and item["task"]["range_end_ms"] > N1_START
        and item["task"]["range_start_ms"] < JULY_START
    ]


def new_fapi_rest(new: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [item for item in new["fapi_july_pages"] if item["source_role"] == role]


def compress_minute_ranges(minutes: Iterable[int]) -> list[dict[str, Any]]:
    ordered = sorted(set(minutes))
    if not ordered:
        return []
    result: list[dict[str, Any]] = []
    start = ordered[0]
    prior = ordered[0]
    for minute in ordered[1:]:
        if minute != prior + ONE_MINUTE_MS:
            result.append(
                {
                    "start_inclusive_ms": start,
                    "start_inclusive_utc": utc_text(start),
                    "end_exclusive_ms": prior + ONE_MINUTE_MS,
                    "end_exclusive_utc": utc_text(prior + ONE_MINUTE_MS),
                    "minute_count": (prior + ONE_MINUTE_MS - start) // ONE_MINUTE_MS,
                },
            )
            start = minute
        prior = minute
    result.append(
        {
            "start_inclusive_ms": start,
            "start_inclusive_utc": utc_text(start),
            "end_exclusive_ms": prior + ONE_MINUTE_MS,
            "end_exclusive_utc": utc_text(prior + ONE_MINUTE_MS),
            "minute_count": (prior + ONE_MINUTE_MS - start) // ONE_MINUTE_MS,
        },
    )
    return result


def scan_perpetual(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    exec_daily_pairs = source_kind_pairs(
        old, new, "usdm_execution", "USDM_DAILY_EXECUTION_ARCHIVE", "USDM_DAILY_EXECUTION_KLINES",
    )
    exec_monthly_pairs = source_kind_pairs(
        old, new, "usdm_execution", "USDM_MONTHLY_EXECUTION_ARCHIVE", "USDM_MONTHLY_EXECUTION_KLINES",
    )
    exec_rest_items = old_fapi_rest(old, "usdm_execution") + new_fapi_rest(new, "USDM_REST_EXECUTION_KLINES_JULY")

    exec_daily, exec_daily_summary = build_archive_map(
        exec_daily_pairs, source_role="USDM_DAILY_EXECUTION_KLINES", kind="execution", start_ms=N1_START, end_ms=N1_END,
    )
    exec_monthly, exec_monthly_summary = build_archive_map(
        exec_monthly_pairs, source_role="USDM_MONTHLY_EXECUTION_KLINES", kind="execution", start_ms=N1_START, end_ms=N1_END,
    )
    exec_rest, exec_rest_summary = build_rest_map(
        exec_rest_items, source_role="USDM_REST_EXECUTION_KLINES", kind="execution", start_ms=N1_START, end_ms=N1_END,
    )
    execution_blockers: list[dict[str, Any]] = []
    for minute in range(N1_START, N1_END, ONE_MINUTE_MS):
        rows = (exec_rest.get(minute), exec_daily.get(minute), exec_monthly.get(minute))
        present = kline_group(rows)
        if len(present) != 3 or any(row.invalid_reasons for row in present) or len({row.semantic_sha256 for row in present}) != 1:
            execution_blockers.append(
                {
                    "open_time_ms": minute,
                    "open_time_utc": utc_text(minute),
                    "present_roles": [row.source_role for row in present],
                    "semantic_sha256s": [row.semantic_sha256 for row in present],
                    "invalid_reasons": [list(row.invalid_reasons) for row in present],
                },
            )
    del exec_daily, exec_monthly, exec_rest
    gc.collect()

    mark_daily_pairs = source_kind_pairs(
        old, new, "usdm_mark", "USDM_DAILY_MARK_ARCHIVE", "USDM_DAILY_MARK_KLINES",
    )
    mark_monthly_pairs = source_kind_pairs(
        old, new, "usdm_mark", "USDM_MONTHLY_MARK_ARCHIVE", "USDM_MONTHLY_MARK_KLINES",
    )
    mark_rest_items = old_fapi_rest(old, "usdm_mark") + new_fapi_rest(new, "USDM_REST_MARK_KLINES_JULY")
    mark_daily, mark_daily_summary = build_archive_map(
        mark_daily_pairs, source_role="USDM_DAILY_MARK_KLINES", kind="mark", start_ms=N1_START, end_ms=N1_END,
    )
    mark_monthly, mark_monthly_summary = build_archive_map(
        mark_monthly_pairs, source_role="USDM_MONTHLY_MARK_KLINES", kind="mark", start_ms=N1_START, end_ms=N1_END,
    )
    mark_rest, mark_rest_summary = build_rest_map(
        mark_rest_items, source_role="USDM_REST_MARK_KLINES", kind="mark", start_ms=N1_START, end_ms=N1_END,
    )
    mark_blockers: list[dict[str, Any]] = []
    redundant_by_role: Counter[str] = Counter()
    redundant_role_minutes: dict[str, list[int]] = defaultdict(list)
    for minute in range(N1_START, N1_END, ONE_MINUTE_MS):
        rest = mark_rest.get(minute)
        daily = mark_daily.get(minute)
        monthly = mark_monthly.get(minute)
        rows = {"REST": rest, "DAILY": daily, "MONTHLY": monthly}
        present = {role: row for role, row in rows.items() if row is not None}
        invalid_roles = [role for role, row in present.items() if row.invalid_reasons]
        semantic_hashes = {row.semantic_sha256 for row in present.values()}
        if len(present) < 2 or invalid_roles or len(semantic_hashes) != 1:
            mark_blockers.append(
                {
                    "open_time_ms": minute,
                    "open_time_utc": utc_text(minute),
                    "reason": "FEWER_THAN_TWO_COMPLETE_EXACT_OFFICIAL_MARK_REPRESENTATIONS_OR_CONFLICT",
                    "rest_present": rest is not None,
                    "daily_present": daily is not None,
                    "monthly_present": monthly is not None,
                    "invalid_roles": invalid_roles,
                    "semantic_sha256s": sorted(semantic_hashes),
                },
            )
            continue
        for role, row in rows.items():
            if row is None:
                redundant_by_role[role] += 1
                redundant_role_minutes[role].append(minute)
    redundant_dates = sorted(
        {
            utc_text(item["start_ms"])[:10]
            for item in mark_daily_summary["unavailable_objects"]
        },
    )
    del mark_daily, mark_monthly, mark_rest
    gc.collect()

    funding = scan_funding(old, new)
    status = "PASS" if not execution_blockers and not mark_blockers and funding["status"] == "PASS" else "BLOCKED"
    return {
        "status": status,
        "expected_minute_count": EXPECTED_MINUTES,
        "execution": {
            "status": "PASS" if not execution_blockers else "BLOCKED",
            "source_summaries": {
                "daily": exec_daily_summary,
                "monthly": exec_monthly_summary,
                "rest": exec_rest_summary,
            },
            "accepted_minute_count": EXPECTED_MINUTES - len(execution_blockers),
            "blocker_count": len(execution_blockers),
            "blockers": execution_blockers,
        },
        "mark": {
            "status": "PASS" if not mark_blockers else "BLOCKED",
            "source_summaries": {
                "daily": mark_daily_summary,
                "monthly": mark_monthly_summary,
                "rest": mark_rest_summary,
            },
            "accepted_minute_count": EXPECTED_MINUTES - len(mark_blockers),
            "reconciliation_rule": "NO_AUTOMATIC_PRIORITY; ACCEPT_ONLY_WHEN_AT_LEAST_TWO_COMPLETE_OFFICIAL_REPRESENTATIONS_MATCH_EXACTLY_AND_NO_PRESENT_ROLE_CONFLICTS",
            "redundant_delivery_unavailable_by_role": dict(sorted(redundant_by_role.items())),
            "redundant_delivery_unavailable_ranges_by_role": {
                role: compress_minute_ranges(minutes)
                for role, minutes in sorted(redundant_role_minutes.items())
            },
            "redundant_daily_delivery_unavailable_date_count": len(redundant_dates),
            "redundant_daily_delivery_unavailable_dates": redundant_dates,
            "classification": "REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE",
            "blocker_count": len(mark_blockers),
            "blockers": mark_blockers,
            "source_substitution_count": 0,
        },
        "funding": funding,
    }


def iter_funding_archive(pair: dict[str, Any]) -> Iterator[tuple[int, int, str]]:
    verify_pair(pair)
    path = archive_path(pair)
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].is_dir():
            raise ValueError("funding archive member mismatch")
        with archive.open(members[0]) as binary:
            rows = csv.reader(io.TextIOWrapper(binary, encoding="utf-8", newline=""), strict=True)
            header = next(rows, None)
            if header != ["calc_time", "funding_interval_hours", "last_funding_rate"]:
                raise ValueError("funding archive header mismatch")
            prior: int | None = None
            for row in rows:
                if len(row) != 3:
                    raise ValueError("funding row malformed")
                timestamp = int(row[0])
                interval_hours = int(row[1])
                rate = decimal_text(decimal(row[2], "funding rate"))
                if prior is not None and timestamp <= prior:
                    raise ValueError("funding archive non-monotonic")
                prior = timestamp
                yield timestamp, interval_hours, rate


def scan_funding(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    pairs = old_archive_pairs(old, "usdm_funding", "monthly", N1_START, JULY_START)
    pairs += new_archive_pairs(new, "USDM_MONTHLY_FUNDING_RATE")
    archive_rows: dict[int, tuple[int, str]] = {}
    source_hashes: list[str] = []
    for pair in sorted(pairs, key=lambda item: task_value(item, "range_start_ms", "start_ms")):
        source_hashes.append(pair["archive"]["raw_object_sha256"])
        for timestamp, interval_hours, rate in iter_funding_archive(pair):
            if timestamp < N1_START or timestamp >= N1_END:
                continue
            if timestamp in archive_rows:
                raise ValueError("duplicate funding archive timestamp")
            archive_rows[timestamp] = (interval_hours, rate)

    observation = new["funding_rest"]
    path = observation_path(observation)
    if file_sha256(path) != observation["raw_object_sha256"]:
        raise ValueError("funding REST raw identity mismatch")
    value = json.loads(path.read_bytes())
    if not isinstance(value, list) or len(value) >= 1000:
        raise ValueError("funding REST pagination incomplete")
    rest_rows: dict[int, str] = {}
    for item in value:
        if item.get("symbol") != "BTCUSDT":
            raise ValueError("funding REST symbol mismatch")
        timestamp = int(item["fundingTime"])
        if timestamp in rest_rows:
            raise ValueError("duplicate funding REST timestamp")
        rest_rows[timestamp] = decimal_text(decimal(item["fundingRate"], "funding REST rate"))

    conflicts: list[dict[str, Any]] = []
    for timestamp in sorted(set(archive_rows) | set(rest_rows)):
        archive = archive_rows.get(timestamp)
        rest = rest_rows.get(timestamp)
        if archive is None or rest is None or archive[1] != rest:
            conflicts.append(
                {
                    "funding_time_ms": timestamp,
                    "funding_time_utc": utc_text(timestamp),
                    "archive": archive,
                    "rest": rest,
                },
            )
    schedule_errors: list[dict[str, Any]] = []
    ordered = sorted(archive_rows)
    slots: list[int] = []
    offsets: list[int] = []
    for timestamp in ordered:
        interval_hours = archive_rows[timestamp][0]
        interval_ms = interval_hours * 3_600_000
        slot = timestamp // interval_ms
        offset = timestamp - slot * interval_ms
        slots.append(slot)
        offsets.append(offset)
        if offset >= ONE_MINUTE_MS:
            schedule_errors.append(
                {
                    "funding_time_ms": timestamp,
                    "funding_interval_hours": interval_hours,
                    "reason": "EVENT_NOT_WITHIN_FIRST_MINUTE_OF_OFFICIAL_INTERVAL_SLOT",
                    "offset_ms": offset,
                },
            )
    if len(slots) != len(set(slots)):
        schedule_errors.append({"reason": "DUPLICATE_OFFICIAL_FUNDING_INTERVAL_SLOT"})
    for first, second in zip(slots, slots[1:], strict=False):
        if second != first + 1:
            schedule_errors.append(
                {
                    "prior_slot": first,
                    "slot": second,
                    "reason": "MISSING_OR_NONCONSECUTIVE_OFFICIAL_FUNDING_INTERVAL_SLOT",
                },
            )
    status = "PASS" if not conflicts and not schedule_errors and archive_rows else "BLOCKED"
    return {
        "status": status,
        "archive_event_count": len(archive_rows),
        "rest_event_count": len(rest_rows),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "schedule_error_count": len(schedule_errors),
        "schedule_errors": schedule_errors,
        "funding_interval_hours": sorted({row[0] for row in archive_rows.values()}),
        "schedule_slot_rule": "PRESERVE_EXACT_EVENT_TIMESTAMP; BIND_TO_UNIQUE_CONSECUTIVE_SLOT_FROM_ARCHIVE funding_interval_hours",
        "maximum_event_offset_from_slot_start_ms": max(offsets) if offsets else None,
        "archive_source_sha256s": sorted(source_hashes),
        "rest_source_sha256": observation["raw_object_sha256"],
        "source_substitution_count": 0,
    }


def scan_old_mark_gap(new: dict[str, Any]) -> dict[str, Any]:
    daily_pair = next(pair for pair in new["archive_pairs"] if pair["task"]["source_role"] == "USDM_MARK_GAP_DAILY_ARCHIVE")
    monthly_pair = next(pair for pair in new["archive_pairs"] if pair["task"]["source_role"] == "USDM_MARK_GAP_MONTHLY_ARCHIVE")
    daily, daily_summary = build_archive_map(
        [daily_pair], source_role="USDM_MARK_GAP_DAILY_ARCHIVE", kind="mark",
        start_ms=MARK_GAP_START - 2 * ONE_MINUTE_MS, end_ms=MARK_GAP_END + 2 * ONE_MINUTE_MS,
    )
    monthly, monthly_summary = build_archive_map(
        [monthly_pair], source_role="USDM_MARK_GAP_MONTHLY_ARCHIVE", kind="mark",
        start_ms=MARK_GAP_START - 2 * ONE_MINUTE_MS, end_ms=MARK_GAP_END + 2 * ONE_MINUTE_MS,
    )
    observation = new["mark_gap_rest"]
    path = observation_path(observation)
    if file_sha256(path) != observation["raw_object_sha256"]:
        raise ValueError("mark-gap REST identity mismatch")
    rest: dict[int, Kline] = {}
    for fields in json.loads(path.read_bytes()):
        item = parse_kline(fields, observation["raw_object_sha256"], "USDM_REST_MARK_GAP_PROBE", "mark")
        rest[item.open_time_ms] = item
    missing: list[dict[str, Any]] = []
    for minute in range(MARK_GAP_START, MARK_GAP_END, ONE_MINUTE_MS):
        if minute not in daily and minute not in monthly and minute not in rest:
            missing.append({"open_time_ms": minute, "open_time_utc": utc_text(minute)})
    expected_missing = (MARK_GAP_END - MARK_GAP_START) // ONE_MINUTE_MS
    classification = "IRRECOVERABLE_OFFICIAL_MARK_DELIVERY_GAP" if len(missing) == expected_missing else "OFFICIAL_MARK_GAP_NOT_PROVEN"
    return {
        "status": "BLOCKED_OLD_WINDOW",
        "classification": classification,
        "start_inclusive": utc_text(MARK_GAP_START),
        "end_exclusive": utc_text(MARK_GAP_END),
        "expected_missing_minute_count": expected_missing,
        "confirmed_missing_minute_count": len(missing),
        "missing_minutes": missing,
        "daily_summary": daily_summary,
        "monthly_summary": monthly_summary,
        "rest_source_sha256": observation["raw_object_sha256"],
        "surrounding_present": {
            "before": all((MARK_GAP_START - offset) in daily and (MARK_GAP_START - offset) in monthly and (MARK_GAP_START - offset) in rest for offset in (ONE_MINUTE_MS, 2 * ONE_MINUTE_MS)),
            "after": all((MARK_GAP_END + offset) in daily and (MARK_GAP_END + offset) in monthly and (MARK_GAP_END + offset) in rest for offset in (0, ONE_MINUTE_MS)),
        },
        "mark_reconstructed": False,
        "substitution_used": False,
        "issue_483_is_investigative_only": True,
    }


def verify_new_store(new: dict[str, Any]) -> dict[str, Any]:
    observations: dict[str, dict[str, Any]] = {}
    for path in sorted((NEW_RAW / "http-observations").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        raw = observation_path(value)
        if file_sha256(raw) != value["raw_object_sha256"] or raw.stat().st_size != value["byte_size"]:
            raise ValueError(f"new raw observation binding mismatch: {path}")
        observations[value["observation_id"]] = value
    return {
        "observation_count": len(observations),
        "raw_object_count": len(list((NEW_RAW / "objects/sha256").glob("*/*.bin"))),
        "binding_failure_count": 0,
        "all_http_status_counts": dict(sorted(Counter(value["http_status"] for value in observations.values()).items())),
    }


def official_reference_summary(new: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in new["public_references"]:
        record: dict[str, Any] = {
            "source_role": item["source_role"],
            "exact_url": item["exact_url"],
            "raw_object_sha256": item["raw_object_sha256"],
            "byte_size": item["byte_size"],
            "http_status": item["http_status"],
            "retrieval_completed_at_utc": item["retrieval_completed_at_utc"],
        }
        if "ISSUE_" in item["source_role"]:
            value = json.loads(observation_path(item).read_bytes())
            record.update(
                {
                    "title": value.get("title"),
                    "author_association": value.get("author_association"),
                    "evidentiary_role": "INVESTIGATIVE_LEAD_ONLY_NOT_PRICE_AUTHORITY",
                },
            )
        else:
            record["evidentiary_role"] = "OFFICIAL_SOURCE_CONTRACT_OR_CURRENT_METADATA"
        result.append(record)
    return result


def main() -> int:
    old = json.loads(OLD_INDEX.read_text(encoding="utf-8"))
    new = json.loads(NEW_INDEX.read_text(encoding="utf-8"))
    raw_integrity = verify_new_store(new)
    spot = scan_spot(old, new)
    perpetual = scan_perpetual(old, new)
    old_mark_gap = scan_old_mark_gap(new)
    selected = spot["status"] == "PASS" and perpetual["status"] == "PASS"
    windows = [
        {
            "shift_months": 0,
            "dataset_start_inclusive": "2020-12-01T00:00:00Z",
            "warmup_start_inclusive": "2020-12-01T00:00:00Z",
            "scoring_start_inclusive": "2021-01-01T00:00:00Z",
            "scoring_end_exclusive": "2021-07-01T00:00:00Z",
            "dataset_end_exclusive": "2021-07-01T00:00:00Z",
            "classification": "EXPOSED_DATA_BLOCKED_NOT_FINAL_HOLDOUT",
            "status": "FAIL",
            "reason": "IRRECOVERABLE_OFFICIAL_MARK_DELIVERY_GAP",
            "strategy_performance_inspected": False,
        },
        {
            "shift_months": 1,
            "dataset_start_inclusive": "2021-01-01T00:00:00Z",
            "warmup_start_inclusive": "2021-01-01T00:00:00Z",
            "scoring_start_inclusive": "2021-02-01T00:00:00Z",
            "scoring_end_exclusive": "2021-08-01T00:00:00Z",
            "dataset_end_exclusive": "2021-08-01T00:00:00Z",
            "classification": "DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT",
            "status": "PASS" if selected else "FAIL",
            "reason": "FIRST_CHRONOLOGICAL_WHOLE_MONTH_SHIFT_PASSING_BOTH_PROFILES" if selected else "PROFILE_DATA_QUALITY_GATE_FAILED",
            "strategy_performance_inspected": False,
        },
    ]
    output = {
        "schema": "free-official-binance-phase-a-analysis-v1",
        "epoch": "FREE_OFFICIAL_BINANCE_DATA_AND_DUCKDB_REPAIR_001",
        "created_at_utc": utc_now(),
        "source_policy": {
            "free_official_binance_only": True,
            "paid_provider_used": False,
            "third_party_data_used": False,
            "credentials_used": False,
            "binary_float_material_calculation_used": False,
            "synthetic_price_created": False,
            "mark_reconstructed": False,
        },
        "acquisition_identity": new["acquisition_identity"],
        "raw_integrity": raw_integrity,
        "official_references": official_reference_summary(new),
        "spot": spot,
        "perpetual": perpetual,
        "old_window_mark_gap": old_mark_gap,
        "partition_geometry": {
            "source": "evidence/research/owner-smoke-001/blocked-outcome.json",
            "warmup_duration_calendar_months": 1,
            "scoring_duration_calendar_months": 6,
            "half_open": True,
            "all_boundaries_shifted_together": True,
        },
        "window_scan": windows,
        "selected_window": windows[1] if selected else None,
        "status": "PASS" if selected else "BLOCKED",
    }
    identity_material = dict(output)
    identity_material.pop("created_at_utc")
    output["analysis_identity"] = sha256_bytes(canonical_json(identity_material))
    replace_json(OUTPUT, output)
    print(
        json.dumps(
            {
                "status": output["status"],
                "analysis_identity": output["analysis_identity"],
                "spot_status": spot["status"],
                "spot_dispositions": spot["disposition_counts"],
                "perpetual_status": perpetual["status"],
                "mark_redundant_404_dates": perpetual["mark"]["redundant_daily_delivery_unavailable_date_count"],
                "old_mark_missing_minutes": old_mark_gap["confirmed_missing_minute_count"],
                "selected_window": output["selected_window"],
            },
            sort_keys=True,
        ),
    )
    return 0 if selected else 2


if __name__ == "__main__":
    raise SystemExit(main())
