#!/usr/bin/env python3
"""Acquire and validate the locked OWNER_SMOKE_001 Binance data window.

The acquisition command is network-enabled setup work.  Build/verify commands
added to this runner remain offline and consume only preserved raw objects.
No command in this module starts a strategy or an Official Trial.
"""

from __future__ import annotations

import argparse
import calendar
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from crypto_lab.data_provenance import AggTradeSource  # noqa: E402
from crypto_lab.data_provenance import HttpObservation  # noqa: E402
from crypto_lab.data_provenance import HttpRawStore  # noqa: E402
from crypto_lab.data_provenance import KlineSource  # noqa: E402
from crypto_lab.data_provenance import ProvenanceError  # noqa: E402
from crypto_lab.data_provenance import canonical_json_bytes  # noqa: E402
from crypto_lab.data_provenance import parse_kline_rest_page  # noqa: E402
from crypto_lab.data_provenance import sha256_bytes  # noqa: E402
from crypto_lab.data_provenance import utc_now_text  # noqa: E402
from crypto_lab.data_provenance import verify_checksum  # noqa: E402
from crypto_lab.timestamps import utc_datetime_to_ms  # noqa: E402


WINDOW_START = datetime(2020, 12, 1, tzinfo=UTC)
WINDOW_END = datetime(2021, 7, 1, tzinfo=UTC)
WINDOW_START_MS = utc_datetime_to_ms(WINDOW_START)
WINDOW_END_MS = utc_datetime_to_ms(WINDOW_END)
SYMBOL = "BTCUSDT"
AGGTRADE_REPAIR_DAYS = (
    date(2020, 12, 21),
    date(2020, 12, 25),
    date(2021, 1, 11),
    date(2021, 2, 4),
    date(2021, 2, 8),
    date(2021, 2, 10),
    date(2021, 2, 11),
    date(2021, 3, 6),
    date(2021, 4, 1),
    date(2021, 4, 20),
    date(2021, 4, 22),
    date(2021, 4, 23),
    date(2021, 4, 25),
)
AGGTRADE_REPAIR_MONTHS = (
    date(2020, 12, 1),
    date(2021, 1, 1),
    date(2021, 2, 1),
    date(2021, 3, 1),
    date(2021, 4, 1),
)
RAW_ROOT = ROOT / "data/raw/data-provenance-duckdb-001"
INDEX_PATH = RAW_ROOT / "acquisition-index.json"
FAILED_ATTEMPTS_LOCAL = RAW_ROOT / "failed-attempts.jsonl"
_OBSERVATION_CACHE: dict[str, HttpObservation] | None = None
_OBSERVATION_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class ArchiveTask:
    category: str
    source_role: str
    source_kind: str
    url: str
    exact_filename: str
    expected_member: str
    range_start_ms: int
    range_end_ms: int
    cadence: str


@dataclass(frozen=True)
class RestTask:
    category: str
    source_role: str
    source_kind: str
    url: str
    range_start_ms: int
    range_end_ms: int
    page_index: int


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_json_bytes(value) + b"\n")
    temporary.replace(path)


def _append_failure(value: dict[str, Any]) -> None:
    FAILED_ATTEMPTS_LOCAL.parent.mkdir(parents=True, exist_ok=True)
    with FAILED_ATTEMPTS_LOCAL.open("ab") as stream:
        stream.write(canonical_json_bytes(value) + b"\n")


def _datetime_ms(value: datetime) -> int:
    return utc_datetime_to_ms(value)


def _days() -> list[date]:
    result: list[date] = []
    cursor = WINDOW_START.date()
    while cursor < WINDOW_END.date():
        result.append(cursor)
        cursor += timedelta(days=1)
    return result


def _months() -> list[date]:
    result: list[date] = []
    cursor = date(WINDOW_START.year, WINDOW_START.month, 1)
    end = date(WINDOW_END.year, WINDOW_END.month, 1)
    while cursor < end:
        result.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return result


def _day_bounds(day: date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return _datetime_ms(start), _datetime_ms(start + timedelta(days=1))


def _month_bounds(month: date) -> tuple[int, int]:
    start = datetime(month.year, month.month, 1, tzinfo=UTC)
    days = calendar.monthrange(month.year, month.month)[1]
    return _datetime_ms(start), _datetime_ms(start + timedelta(days=days))


def archive_tasks() -> tuple[ArchiveTask, ...]:
    tasks: list[ArchiveTask] = []
    definitions = (
        (
            "spot_execution",
            "SPOT_KLINE_ARCHIVE",
            KlineSource.SPOT_DAILY.value,
            "spot",
            "klines",
        ),
        (
            "usdm_execution",
            "USDM_EXECUTION_KLINE_ARCHIVE",
            KlineSource.USDM_EXECUTION_DAILY.value,
            "futures/um",
            "klines",
        ),
        (
            "usdm_mark",
            "USDM_MARK_KLINE_ARCHIVE",
            KlineSource.USDM_MARK_DAILY.value,
            "futures/um",
            "markPriceKlines",
        ),
    )
    for day in _days():
        start_ms, end_ms = _day_bounds(day)
        for category, role, source_kind, market_path, data_type in definitions:
            filename = f"{SYMBOL}-1m-{day.isoformat()}.zip"
            url = (
                "https://data.binance.vision/data/"
                f"{market_path}/daily/{data_type}/{SYMBOL}/1m/{filename}"
            )
            tasks.append(
                ArchiveTask(
                    category=category,
                    source_role=role,
                    source_kind=source_kind,
                    url=url,
                    exact_filename=filename,
                    expected_member=filename.removesuffix(".zip") + ".csv",
                    range_start_ms=start_ms,
                    range_end_ms=end_ms,
                    cadence="daily",
                ),
            )
    monthly_definitions = (
        (
            "spot_execution",
            "SPOT_KLINE_ARCHIVE",
            KlineSource.SPOT_MONTHLY.value,
            "spot",
            "klines",
            "1m",
        ),
        (
            "usdm_execution",
            "USDM_EXECUTION_KLINE_ARCHIVE",
            KlineSource.USDM_EXECUTION_MONTHLY.value,
            "futures/um",
            "klines",
            "1m",
        ),
        (
            "usdm_mark",
            "USDM_MARK_KLINE_ARCHIVE",
            KlineSource.USDM_MARK_MONTHLY.value,
            "futures/um",
            "markPriceKlines",
            "1m",
        ),
        (
            "usdm_funding",
            "USDM_FUNDING_ARCHIVE",
            "USDM_MONTHLY_FUNDING_ARCHIVE",
            "futures/um",
            "fundingRate",
            None,
        ),
    )
    for month in _months():
        start_ms, end_ms = _month_bounds(month)
        for category, role, source_kind, market_path, data_type, interval in monthly_definitions:
            if data_type == "fundingRate":
                filename = f"{SYMBOL}-fundingRate-{month:%Y-%m}.zip"
                url = (
                    "https://data.binance.vision/data/"
                    f"{market_path}/monthly/{data_type}/{SYMBOL}/{filename}"
                )
            else:
                filename = f"{SYMBOL}-1m-{month:%Y-%m}.zip"
                url = (
                    "https://data.binance.vision/data/"
                    f"{market_path}/monthly/{data_type}/{SYMBOL}/{interval}/{filename}"
                )
            tasks.append(
                ArchiveTask(
                    category=category,
                    source_role=role,
                    source_kind=source_kind,
                    url=url,
                    exact_filename=filename,
                    expected_member=filename.removesuffix(".zip") + ".csv",
                    range_start_ms=start_ms,
                    range_end_ms=end_ms,
                    cadence="monthly",
                ),
            )
    for day in AGGTRADE_REPAIR_DAYS:
        start_ms, end_ms = _day_bounds(day)
        filename = f"{SYMBOL}-aggTrades-{day.isoformat()}.zip"
        tasks.append(
            ArchiveTask(
                category="spot_aggtrades",
                source_role="SPOT_AGGTRADES_ARCHIVE",
                source_kind=AggTradeSource.SPOT_DAILY.value,
                url=(
                    "https://data.binance.vision/data/spot/daily/aggTrades/"
                    f"{SYMBOL}/{filename}"
                ),
                exact_filename=filename,
                expected_member=filename.removesuffix(".zip") + ".csv",
                range_start_ms=start_ms,
                range_end_ms=end_ms,
                cadence="daily",
            ),
        )
    for month in AGGTRADE_REPAIR_MONTHS:
        start_ms, end_ms = _month_bounds(month)
        filename = f"{SYMBOL}-aggTrades-{month:%Y-%m}.zip"
        tasks.append(
            ArchiveTask(
                category="spot_aggtrades",
                source_role="SPOT_AGGTRADES_ARCHIVE",
                source_kind=AggTradeSource.SPOT_MONTHLY.value,
                url=(
                    "https://data.binance.vision/data/spot/monthly/aggTrades/"
                    f"{SYMBOL}/{filename}"
                ),
                exact_filename=filename,
                expected_member=filename.removesuffix(".zip") + ".csv",
                range_start_ms=start_ms,
                range_end_ms=end_ms,
                cadence="monthly",
            ),
        )
    return tuple(tasks)


def rest_tasks() -> tuple[RestTask, ...]:
    tasks: list[RestTask] = []
    definitions = (
        (
            "spot_execution",
            "SPOT_REST_KLINES",
            KlineSource.SPOT_REST.value,
            "https://api.binance.com/api/v3/klines",
        ),
        (
            "usdm_execution",
            "USDM_REST_EXECUTION_KLINES",
            KlineSource.USDM_EXECUTION_REST.value,
            "https://fapi.binance.com/fapi/v1/klines",
        ),
        (
            "usdm_mark",
            "USDM_REST_MARK_KLINES",
            KlineSource.USDM_MARK_REST.value,
            "https://fapi.binance.com/fapi/v1/markPriceKlines",
        ),
    )
    chunk_ms = 1_000 * 60_000
    page = 0
    cursor = WINDOW_START_MS
    while cursor < WINDOW_END_MS:
        chunk_end = min(cursor + chunk_ms, WINDOW_END_MS)
        for category, role, source_kind, endpoint in definitions:
            query = urlencode(
                {
                    "symbol": SYMBOL,
                    "interval": "1m",
                    "startTime": cursor,
                    "endTime": chunk_end - 1,
                    "limit": 1000,
                },
            )
            tasks.append(
                RestTask(
                    category=category,
                    source_role=role,
                    source_kind=source_kind,
                    url=f"{endpoint}?{query}",
                    range_start_ms=cursor,
                    range_end_ms=chunk_end,
                    page_index=page,
                ),
            )
        cursor = chunk_end
        page += 1
    return tuple(tasks)


def static_tasks() -> tuple[tuple[str, str, str], ...]:
    return (
        (
            "BINANCE_PUBLIC_DATA_CONTRACT",
            "https://raw.githubusercontent.com/binance/binance-public-data/master/README.md",
            "public-data-readme",
        ),
        (
            "BINANCE_SPOT_API_CONTRACT",
            "https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md",
            "spot-rest-api-contract",
        ),
        (
            "BINANCE_ARCHIVE_UPDATE_MANIFEST",
            "https://raw.githubusercontent.com/binance/binance-public-data/master/updates/2022-08-08_kline_updates.zip",
            "kline-update-manifest",
        ),
        (
            "BINANCE_ARCHIVE_UPDATE_MANIFEST",
            "https://raw.githubusercontent.com/binance/binance-public-data/master/updates/2022-04-21_aggregate_trade_updates.zip",
            "aggtrade-update-manifest",
        ),
        (
            "SPOT_INSTRUMENT_METADATA",
            "https://api.binance.com/api/v3/exchangeInfo?symbol=BTCUSDT",
            "spot-exchange-info",
        ),
        (
            "USDM_INSTRUMENT_METADATA",
            "https://fapi.binance.com/fapi/v1/exchangeInfo",
            "usdm-exchange-info",
        ),
        (
            "USDM_FUNDING_METADATA",
            "https://fapi.binance.com/fapi/v1/fundingInfo",
            "usdm-funding-info",
        ),
    )


def _existing_by_url(store: HttpRawStore) -> dict[str, HttpObservation]:
    global _OBSERVATION_CACHE
    with _OBSERVATION_CACHE_LOCK:
        if _OBSERVATION_CACHE is not None:
            return _OBSERVATION_CACHE
    result: dict[str, HttpObservation] = {}
    for path in sorted(store.observations.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            raw_path = store.object_path(value["raw_object_sha256"])
            if not raw_path.is_file():
                continue
            observation = HttpObservation(
                observation_id=value["observation_id"],
                raw_object_sha256=value["raw_object_sha256"],
                exact_url=value["exact_url"],
                status_code=value["status_code"],
                response_headers=value["response_headers"],
                capture_started_at_utc=value["capture_started_at_utc"],
                capture_completed_at_utc=value["capture_completed_at_utc"],
                byte_length=value["byte_length"],
                source_role=value["source_role"],
                pagination_position=value["pagination_position"],
                local_object_path=str(raw_path),
                local_observation_path=str(path),
            )
            result[value["exact_url"]] = observation
        except Exception:
            continue
    with _OBSERVATION_CACHE_LOCK:
        if _OBSERVATION_CACHE is None:
            _OBSERVATION_CACHE = result
        return _OBSERVATION_CACHE


def _capture_with_retry(
    store: HttpRawStore,
    url: str,
    *,
    role: str,
    position: str,
    timeout_seconds: int = 120,
    accepted_statuses: tuple[int, ...] = (200,),
) -> HttpObservation:
    existing = _existing_by_url(store).get(url)
    if existing is not None and existing.status_code in accepted_statuses:
        return existing
    failures: list[dict[str, Any]] = []
    for attempt in range(1, 4):
        try:
            observation = store.capture(
                url,
                source_role=role,
                pagination_position=position,
                timeout_seconds=timeout_seconds,
                accepted_statuses=accepted_statuses,
            )
            with _OBSERVATION_CACHE_LOCK:
                if _OBSERVATION_CACHE is not None:
                    _OBSERVATION_CACHE[url] = observation
            return observation
        except ProvenanceError as exc:
            failure = {
                "attempt": attempt,
                "attempted_at_utc": utc_now_text(),
                "code": exc.code,
                "error": str(exc),
                "evidence": exc.evidence,
                "exact_url": url,
                "pagination_position": position,
                "source_role": role,
            }
            failures.append(failure)
            _append_failure(failure)
            if attempt < 3:
                time.sleep(5 * attempt)
    raise ProvenanceError(
        "SOURCE_INCOMPLETE",
        f"three acquisition attempts failed for {url}",
        evidence={"attempts": failures},
    )


def _observation_record(observation: HttpObservation) -> dict[str, Any]:
    result = asdict(observation)
    result["local_object_path"] = str(Path(result["local_object_path"]).relative_to(ROOT))
    result["local_observation_path"] = str(
        Path(result["local_observation_path"]).relative_to(ROOT),
    )
    return result


def _acquire_archive(store: HttpRawStore, task: ArchiveTask) -> dict[str, Any]:
    accepted_statuses = (
        (200, 404)
        if task.cadence == "daily" and task.category == "usdm_mark"
        else (200,)
    )
    archive = _capture_with_retry(
        store,
        task.url,
        role=task.source_role,
        position=f"{task.cadence}:{task.range_start_ms}:{task.range_end_ms}:archive",
        timeout_seconds=900 if task.category == "spot_aggtrades" and task.cadence == "monthly" else 180,
        accepted_statuses=accepted_statuses,
    )
    if archive.status_code == 404:
        return {
            "task": asdict(task),
            "archive": _observation_record(archive),
            "checksum": None,
            "publisher_checksum": None,
            "publisher_checksum_match": None,
            "archive_available": False,
            "official_absence_status": "HTTP_404_FROM_BINANCE_PUBLIC_DATA",
        }
    checksum = _capture_with_retry(
        store,
        task.url + ".CHECKSUM",
        role="PUBLISHER_CHECKSUM",
        position=f"{task.cadence}:{task.range_start_ms}:{task.range_end_ms}:checksum",
        timeout_seconds=120,
    )
    verified = verify_checksum(
        Path(archive.local_object_path),
        store.read(checksum.raw_object_sha256),
        exact_filename=task.exact_filename,
    )
    return {
        "task": asdict(task),
        "archive": _observation_record(archive),
        "checksum": _observation_record(checksum),
        "publisher_checksum": verified,
        "publisher_checksum_match": True,
        "archive_available": True,
        "official_absence_status": None,
    }


def _acquire_rest(store: HttpRawStore, task: RestTask) -> dict[str, Any]:
    observation = _capture_with_retry(
        store,
        task.url,
        role=task.source_role,
        position=f"chunk:{task.page_index}:{task.range_start_ms}:{task.range_end_ms}",
    )
    source_kind = KlineSource(task.source_kind)
    rows = parse_kline_rest_page(
        store.read(observation.raw_object_sha256),
        source_kind=source_kind,
        source_sha256=observation.raw_object_sha256,
    )
    if len(rows) > 1000:
        raise ProvenanceError("SOURCE_INCOMPLETE", "REST page exceeded locked limit")
    if any(
        row.open_time_ms < task.range_start_ms or row.open_time_ms >= task.range_end_ms
        for row in rows
    ):
        raise ProvenanceError("SOURCE_INCOMPLETE", "REST page returned data outside its fixed chunk")
    return {
        "task": asdict(task),
        "observation": _observation_record(observation),
        "parsed_row_count": len(rows),
        "first_open_time_ms": rows[0].open_time_ms if rows else None,
        "last_open_time_ms": rows[-1].open_time_ms if rows else None,
        "fixed_chunk_complete": True,
    }


def _acquire_static(store: HttpRawStore, task: tuple[str, str, str]) -> dict[str, Any]:
    role, url, position = task
    observation = _capture_with_retry(
        store,
        url,
        role=role,
        position=position,
        timeout_seconds=180,
    )
    return {
        "source_role": role,
        "position": position,
        "observation": _observation_record(observation),
    }


def _acquire_funding_rest(store: HttpRawStore) -> dict[str, Any]:
    query = urlencode(
        {
            "symbol": SYMBOL,
            "startTime": WINDOW_START_MS,
            "endTime": WINDOW_END_MS - 1,
            "limit": 1000,
        },
    )
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?{query}"
    observation = _capture_with_retry(
        store,
        url,
        role="USDM_REST_FUNDING_RATE",
        position=f"fixed-window:{WINDOW_START_MS}:{WINDOW_END_MS}",
    )
    payload = store.read(observation.raw_object_sha256)
    try:
        rows = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProvenanceError("DATA_SOURCE_INVALID", "funding REST JSON is malformed") from exc
    if not isinstance(rows, list) or len(rows) >= 1000:
        raise ProvenanceError("SOURCE_INCOMPLETE", "funding REST pagination is incomplete")
    return {
        "observation": _observation_record(observation),
        "parsed_row_count": len(rows),
        "pagination_complete": True,
    }


def _acquire_aggtrade_rest(store: HttpRawStore, index: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    ranges = (
        (
            "2020-12-21-conflict",
            utc_datetime_to_ms(datetime(2020, 12, 21, 13, 35, tzinfo=UTC)),
            utc_datetime_to_ms(datetime(2020, 12, 21, 18, 5, tzinfo=UTC)),
        ),
        (
            "2020-12-25-gap",
            utc_datetime_to_ms(datetime(2020, 12, 25, 1, 55, tzinfo=UTC)),
            utc_datetime_to_ms(datetime(2020, 12, 25, 3, 5, tzinfo=UTC)),
        ),
    )
    del index  # The REST stream has a documented 2022 aggregate-ID reindex and is time-anchored.
    for label, start_ms, terminal_time_ms in ranges:
        first_end_ms = min(start_ms + 3_600_000 - 1, terminal_time_ms - 1)
        first_query = urlencode(
            {
                "symbol": SYMBOL,
                "startTime": start_ms,
                "endTime": first_end_ms,
                "limit": 1000,
            },
        )
        first_url = f"https://api.binance.com/api/v3/aggTrades?{first_query}"
        first_observation = _capture_with_retry(
            store,
            first_url,
            role="SPOT_REST_AGG_TRADES",
            position=f"{label}:page:0:time-anchor:{start_ms}:{first_end_ms + 1}",
        )
        first_value = json.loads(store.read(first_observation.raw_object_sha256))
        if not isinstance(first_value, list):
            raise ProvenanceError("DATA_SOURCE_INVALID", "time-anchored aggTrades response is malformed")
        if not first_value:
            result.append(
                {
                    "label": label,
                    "page_index": 0,
                    "query_mode": "TIME_ANCHOR",
                    "from_id": None,
                    "first_id": None,
                    "last_id": None,
                    "parsed_row_count": 0,
                    "historical_data_available": False,
                    "interpretation": "EMPTY_REST_OBSERVATION_NOT_NO_TRADE_PROOF",
                    "observation": _observation_record(first_observation),
                },
            )
            continue
        first_ids = [int(item["a"]) for item in first_value]
        if first_ids != list(range(first_ids[0], first_ids[0] + len(first_ids))):
            raise ProvenanceError("SOURCE_INCOMPLETE", "time-anchored aggTrades page has an ID gap")
        result.append(
            {
                "label": label,
                "page_index": 0,
                "query_mode": "TIME_ANCHOR",
                "from_id": None,
                "first_id": first_ids[0],
                "last_id": first_ids[-1],
                "parsed_row_count": len(first_ids),
                "historical_data_available": True,
                "observation": _observation_record(first_observation),
            },
        )
        cursor = first_ids[-1] + 1
        last_timestamp = int(first_value[-1]["T"])
        page = 1
        while last_timestamp < terminal_time_ms:
            if page > 10_000:
                raise ProvenanceError("SOURCE_INCOMPLETE", "aggTrades pagination safety bound exceeded")
            query = urlencode({"symbol": SYMBOL, "fromId": cursor, "limit": 1000})
            url = f"https://api.binance.com/api/v3/aggTrades?{query}"
            observation = _capture_with_retry(
                store,
                url,
                role="SPOT_REST_AGG_TRADES",
                position=f"{label}:page:{page}:fromId:{cursor}",
            )
            value = json.loads(store.read(observation.raw_object_sha256))
            if not isinstance(value, list) or not value:
                raise ProvenanceError("SOURCE_INCOMPLETE", "aggTrades REST page is empty")
            ids = [int(item["a"]) for item in value]
            if ids[0] != cursor or ids != list(range(cursor, cursor + len(ids))):
                raise ProvenanceError("SOURCE_INCOMPLETE", "aggTrades REST pagination has an ID gap")
            result.append(
                {
                    "label": label,
                    "page_index": page,
                    "query_mode": "FROM_ID_CONTINUATION",
                    "from_id": cursor,
                    "first_id": ids[0],
                    "last_id": ids[-1],
                    "parsed_row_count": len(ids),
                    "historical_data_available": True,
                    "observation": _observation_record(observation),
                },
            )
            cursor = ids[-1] + 1
            last_timestamp = int(value[-1]["T"])
            page += 1
    return result


def acquire() -> int:
    store = HttpRawStore(RAW_ROOT)
    started = utc_now_text()
    archive_results: list[dict[str, Any]] = []
    rest_results: list[dict[str, Any]] = []
    static_results: list[dict[str, Any]] = []

    archives = archive_tasks()
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {executor.submit(_acquire_archive, store, task): task for task in archives}
        for future in as_completed(futures):
            task = futures[future]
            try:
                archive_results.append(future.result())
            except Exception as exc:
                _append_failure(
                    {
                        "attempted_at_utc": utc_now_text(),
                        "code": getattr(exc, "code", "UNEXPECTED_ACQUISITION_FAILURE"),
                        "error": str(exc),
                        "task": asdict(task),
                    },
                )
                raise

    rests = rest_tasks()
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_acquire_rest, store, task): task for task in rests}
        for future in as_completed(futures):
            task = futures[future]
            try:
                rest_results.append(future.result())
            except Exception as exc:
                _append_failure(
                    {
                        "attempted_at_utc": utc_now_text(),
                        "code": getattr(exc, "code", "UNEXPECTED_ACQUISITION_FAILURE"),
                        "error": str(exc),
                        "task": asdict(task),
                    },
                )
                raise

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_acquire_static, store, task): task for task in static_tasks()}
        for future in as_completed(futures):
            static_results.append(future.result())

    archive_results.sort(key=lambda item: item["task"]["url"])
    rest_results.sort(key=lambda item: item["task"]["url"])
    static_results.sort(key=lambda item: item["observation"]["exact_url"])
    index: dict[str, Any] = {
        "schema": "data-provenance-acquisition-index-v1",
        "epoch": "DATA_PROVENANCE_DUCKDB_REPAIR_001",
        "symbol": SYMBOL,
        "window": {
            "start_inclusive": WINDOW_START.isoformat().replace("+00:00", "Z"),
            "end_exclusive": WINDOW_END.isoformat().replace("+00:00", "Z"),
            "start_ms": WINDOW_START_MS,
            "end_ms": WINDOW_END_MS,
        },
        "started_at_utc": started,
        "completed_at_utc": None,
        "archive_pairs": archive_results,
        "rest_kline_pages": rest_results,
        "static_observations": static_results,
        "funding_rest": _acquire_funding_rest(store),
        "aggtrade_rest_pages": [],
    }
    index["aggtrade_rest_pages"] = _acquire_aggtrade_rest(store, index)
    index["completed_at_utc"] = utc_now_text()
    material = dict(index)
    material.pop("started_at_utc")
    material.pop("completed_at_utc")
    index["acquisition_identity"] = sha256_bytes(canonical_json_bytes(material))
    _atomic_json(INDEX_PATH, index)
    print(
        json.dumps(
            {
                "acquisition_identity": index["acquisition_identity"],
                "archive_pairs": len(archive_results),
                "rest_kline_pages": len(rest_results),
                "aggtrade_rest_pages": len(index["aggtrade_rest_pages"]),
                "status": "ACQUISITION_COMPLETE",
            },
            sort_keys=True,
        ),
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("acquire",))
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    if arguments.command == "acquire":
        return acquire()
    raise AssertionError(arguments.command)


if __name__ == "__main__":
    raise SystemExit(main())
