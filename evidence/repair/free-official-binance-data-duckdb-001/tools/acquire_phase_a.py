#!/usr/bin/env python3
"""Acquire only free official Binance evidence for Repair Epoch Phase A.

This is an evidence-local acquisition utility.  It does not import DuckDB, does
not create a DatasetRelease, and does not run Nautilus.  Every HTTP response
body is persisted in a content-addressed store before the caller parses it.
"""

from __future__ import annotations

import argparse
import calendar
import concurrent.futures
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit


ROOT = Path(__file__).resolve().parents[4]
RAW_ROOT = ROOT / "data/raw/free-official-binance-data-duckdb-001"
OBJECT_ROOT = RAW_ROOT / "objects/sha256"
OBSERVATION_ROOT = RAW_ROOT / "http-observations"
FAILURE_ROOT = RAW_ROOT / "failed-attempts"
INDEX_PATH = RAW_ROOT / "phase-a-acquisition.json"

SYMBOL = "BTCUSDT"
N1_START = datetime(2021, 1, 1, tzinfo=UTC)
N1_END = datetime(2021, 8, 1, tzinfo=UTC)
JULY_START = datetime(2021, 7, 1, tzinfo=UTC)
MARK_GAP_START = datetime(2020, 12, 17, 7, 32, tzinfo=UTC)
MARK_GAP_END = datetime(2020, 12, 17, 7, 56, tzinfo=UTC)
SPOT_TARGET_START = datetime(2021, 2, 11, 3, 40, tzinfo=UTC)
ONE_MINUTE_MS = 60_000

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CACHE_LOCK = threading.Lock()
_OBSERVATION_BY_URL: dict[str, dict[str, Any]] | None = None


def utc_now() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


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


def write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise RuntimeError(f"immutable path collision: {path}")
        return
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def write_json_once(path: Path, value: Any) -> None:
    write_once(path, canonical_json(value) + b"\n")


def replace_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="manifest-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def validate_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("only credential-free HTTPS locators are allowed")
    host = parsed.hostname or ""
    path = parsed.path
    query = dict(parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True))
    valid = False
    if host == "data.binance.vision":
        valid = path.startswith("/data/") and not query
    elif host == "data-api.binance.vision":
        if path == "/api/v3/klines":
            valid = set(query) == {"symbol", "interval", "startTime", "endTime", "limit"}
        elif path == "/api/v3/aggTrades":
            valid = set(query) in (
                {"symbol", "startTime", "endTime", "limit"},
                {"symbol", "fromId", "limit"},
            )
        elif path == "/api/v3/exchangeInfo":
            valid = set(query) == {"symbol"}
    elif host == "fapi.binance.com":
        if path in {"/fapi/v1/klines", "/fapi/v1/markPriceKlines"}:
            valid = set(query) == {"symbol", "interval", "startTime", "endTime", "limit"}
        elif path == "/fapi/v1/fundingRate":
            valid = set(query) == {"symbol", "startTime", "endTime", "limit"}
        elif path in {"/fapi/v1/fundingInfo", "/fapi/v1/exchangeInfo"}:
            valid = not query
    elif host == "raw.githubusercontent.com":
        valid = path.startswith("/binance/") and not query
    elif host == "api.github.com":
        valid = path.startswith("/repos/binance/") and not query
    if not valid:
        raise ValueError(f"URL outside Phase A official allowlist: {url}")


def object_path(digest: str) -> Path:
    if _SHA256.fullmatch(digest) is None:
        raise ValueError("invalid content digest")
    return OBJECT_ROOT / digest[:2] / f"{digest}.bin"


def _load_cache() -> dict[str, dict[str, Any]]:
    global _OBSERVATION_BY_URL
    with _CACHE_LOCK:
        if _OBSERVATION_BY_URL is not None:
            return _OBSERVATION_BY_URL
        result: dict[str, dict[str, Any]] = {}
        if OBSERVATION_ROOT.exists():
            for path in sorted(OBSERVATION_ROOT.glob("*.json")):
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    raw = object_path(value["raw_object_sha256"])
                    if raw.is_file() and raw.stat().st_size == value["byte_size"]:
                        result[value["exact_url"]] = value
                except Exception:
                    continue
        _OBSERVATION_BY_URL = result
        return result


@dataclass(frozen=True)
class RequestIdentity:
    source_role: str
    instrument: str
    interval: str
    requested_start_ms: int | None
    requested_end_ms: int | None
    pagination_position: str


def capture(
    url: str,
    identity: RequestIdentity,
    *,
    accepted_statuses: tuple[int, ...] = (200,),
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    validate_url(url)
    cached = _load_cache().get(url)
    if cached is not None and cached["http_status"] in accepted_statuses:
        raw = object_path(cached["raw_object_sha256"])
        payload = raw.read_bytes()
        if sha256_bytes(payload) != cached["raw_object_sha256"]:
            raise RuntimeError(f"cached object hash mismatch: {raw}")
        return cached

    started = utc_now()
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            "Accept-Encoding": "identity",
            "User-Agent": "nautilus-crypto-backtest-lab-free-official-phase-a/1",
        },
        method="GET",
    )
    response: Any
    try:
        response = urllib.request.urlopen(request, timeout=timeout_seconds)  # noqa: S310
    except urllib.error.HTTPError as error:
        response = error
    except Exception as exc:
        failure = {
            "attempted_at_utc": started,
            "error_class": type(exc).__name__,
            "error_text": str(exc),
            "exact_url": url,
            "request_identity": asdict(identity),
        }
        failure_id = sha256_bytes(canonical_json(failure))
        write_json_once(FAILURE_ROOT / f"{failure_id}.json", {"failure_id": failure_id, **failure})
        raise

    status = int(getattr(response, "status", response.getcode()))
    headers: dict[str, list[str]] = {}
    for name, value in response.headers.items():
        headers.setdefault(name.lower(), []).append(value)
    headers = {key: headers[key] for key in sorted(headers)}

    RAW_ROOT.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="capture-", dir=RAW_ROOT)
    digest = hashlib.sha256()
    size = 0
    try:
        with os.fdopen(descriptor, "wb") as stream:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                stream.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        raw_hash = digest.hexdigest()
        target = object_path(raw_hash)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.stat().st_size != size or sha256_bytes(target.read_bytes()) != raw_hash:
                raise RuntimeError("content-address collision")
            Path(temporary).unlink()
        else:
            os.chmod(temporary, 0o444)
            os.replace(temporary, target)
    except BaseException:
        if Path(temporary).exists():
            Path(temporary).unlink()
        raise
    finally:
        response.close()

    completed = utc_now()
    parsed = urlsplit(url)
    material = {
        "exact_url": url,
        "exact_query_parameters": [[key, value] for key, value in parse_qsl(parsed.query, keep_blank_values=True)],
        "retrieval_started_at_utc": started,
        "retrieval_completed_at_utc": completed,
        "http_status": status,
        "response_headers": headers,
        "byte_size": size,
        "raw_object_sha256": raw_hash,
        "raw_object_path": str(target.relative_to(ROOT)),
        **asdict(identity),
    }
    observation_id = sha256_bytes(canonical_json(material))
    value = {
        "schema": "free-official-binance-http-observation-v1",
        "observation_id": observation_id,
        **material,
    }
    write_json_once(OBSERVATION_ROOT / f"{observation_id}.json", value)
    with _CACHE_LOCK:
        assert _OBSERVATION_BY_URL is not None
        _OBSERVATION_BY_URL[url] = value
    if status not in accepted_statuses:
        raise RuntimeError(f"official source returned HTTP {status}: {url}")
    return value


def capture_retry(
    url: str,
    identity: RequestIdentity,
    *,
    accepted_statuses: tuple[int, ...] = (200,),
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    errors: list[str] = []
    for attempt in range(1, 4):
        try:
            return capture(
                url,
                identity,
                accepted_statuses=accepted_statuses,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            if attempt < 3:
                time.sleep(attempt * 2)
    raise RuntimeError(f"three acquisition attempts failed: {url}: {errors}")


@dataclass(frozen=True)
class ArchiveTask:
    label: str
    url: str
    filename: str
    source_role: str
    instrument: str
    interval: str
    start_ms: int
    end_ms: int
    allow_404: bool = False


def verify_publisher_checksum(archive: dict[str, Any], checksum: dict[str, Any], filename: str) -> str:
    checksum_bytes = object_path(checksum["raw_object_sha256"]).read_bytes()
    try:
        text = checksum_bytes.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise RuntimeError("publisher checksum is not ASCII") from exc
    match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", text)
    if match is None or match.group(2) != filename:
        raise RuntimeError(f"malformed checksum for {filename}")
    path = object_path(archive["raw_object_sha256"])
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != match.group(1):
        raise RuntimeError(f"publisher checksum mismatch for {filename}")
    return actual


def acquire_archive(task: ArchiveTask) -> dict[str, Any]:
    identity = RequestIdentity(
        source_role=task.source_role,
        instrument=task.instrument,
        interval=task.interval,
        requested_start_ms=task.start_ms,
        requested_end_ms=task.end_ms,
        pagination_position=f"{task.label}:archive",
    )
    statuses = (200, 404) if task.allow_404 else (200,)
    archive = capture_retry(task.url, identity, accepted_statuses=statuses, timeout_seconds=900)
    if archive["http_status"] == 404:
        return {
            "task": asdict(task),
            "archive": archive,
            "archive_available": False,
            "checksum": None,
            "publisher_checksum": None,
            "publisher_checksum_match": None,
        }
    checksum = capture_retry(
        task.url + ".CHECKSUM",
        RequestIdentity(
            source_role="BINANCE_PUBLISHER_CHECKSUM",
            instrument=task.instrument,
            interval=task.interval,
            requested_start_ms=task.start_ms,
            requested_end_ms=task.end_ms,
            pagination_position=f"{task.label}:checksum",
        ),
    )
    publisher = verify_publisher_checksum(archive, checksum, task.filename)
    return {
        "task": asdict(task),
        "archive": archive,
        "archive_available": True,
        "checksum": checksum,
        "publisher_checksum": publisher,
        "publisher_checksum_match": True,
    }


def ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def month_end(value: datetime) -> datetime:
    return value + timedelta(days=calendar.monthrange(value.year, value.month)[1])


def july_archive_tasks() -> list[ArchiveTask]:
    tasks: list[ArchiveTask] = []
    definitions = (
        ("spot", "klines", "SPOT_DAILY_KLINES", "spot:BTCUSDT"),
        ("futures/um", "klines", "USDM_DAILY_EXECUTION_KLINES", "usdm-perpetual:BTCUSDT"),
        ("futures/um", "markPriceKlines", "USDM_DAILY_MARK_KLINES", "usdm-perpetual:BTCUSDT"),
    )
    cursor = JULY_START
    while cursor < N1_END:
        next_day = cursor + timedelta(days=1)
        day = cursor.date().isoformat()
        for market, kind, role, instrument in definitions:
            filename = f"{SYMBOL}-1m-{day}.zip"
            tasks.append(
                ArchiveTask(
                    label=f"july:{kind}:{day}",
                    url=f"https://data.binance.vision/data/{market}/daily/{kind}/{SYMBOL}/1m/{filename}",
                    filename=filename,
                    source_role=role,
                    instrument=instrument,
                    interval="1m",
                    start_ms=ms(cursor),
                    end_ms=ms(next_day),
                    allow_404=kind == "markPriceKlines",
                ),
            )
        cursor = next_day

    month = JULY_START
    end = month_end(month)
    monthly = (
        ("spot", "klines", "1m", "SPOT_MONTHLY_KLINES", "spot:BTCUSDT", f"{SYMBOL}-1m-2021-07.zip"),
        ("futures/um", "klines", "1m", "USDM_MONTHLY_EXECUTION_KLINES", "usdm-perpetual:BTCUSDT", f"{SYMBOL}-1m-2021-07.zip"),
        ("futures/um", "markPriceKlines", "1m", "USDM_MONTHLY_MARK_KLINES", "usdm-perpetual:BTCUSDT", f"{SYMBOL}-1m-2021-07.zip"),
        ("futures/um", "fundingRate", "", "USDM_MONTHLY_FUNDING_RATE", "usdm-perpetual:BTCUSDT", f"{SYMBOL}-fundingRate-2021-07.zip"),
    )
    for market, kind, interval_path, role, instrument, filename in monthly:
        suffix = f"/{interval_path}" if interval_path else ""
        tasks.append(
            ArchiveTask(
                label=f"july:monthly:{kind}",
                url=f"https://data.binance.vision/data/{market}/monthly/{kind}/{SYMBOL}{suffix}/{filename}",
                filename=filename,
                source_role=role,
                instrument=instrument,
                interval="1m" if kind != "fundingRate" else "funding-event",
                start_ms=ms(month),
                end_ms=ms(end),
            ),
        )
    return tasks


def critical_archive_tasks() -> list[ArchiveTask]:
    result: list[ArchiveTask] = []
    feb_day = datetime(2021, 2, 11, tzinfo=UTC)
    feb_end = feb_day + timedelta(days=1)
    spot_daily = (
        ("klines", "1m", f"{SYMBOL}-1m-2021-02-11.zip", "SPOT_TARGET_DAILY_KLINES"),
        ("aggTrades", "", f"{SYMBOL}-aggTrades-2021-02-11.zip", "SPOT_TARGET_DAILY_AGGTRADES"),
        ("trades", "", f"{SYMBOL}-trades-2021-02-11.zip", "SPOT_TARGET_DAILY_TRADES"),
    )
    for kind, interval_path, filename, role in spot_daily:
        suffix = f"/{interval_path}" if interval_path else ""
        result.append(
            ArchiveTask(
                label=f"spot-target:{kind}",
                url=f"https://data.binance.vision/data/spot/daily/{kind}/{SYMBOL}{suffix}/{filename}",
                filename=filename,
                source_role=role,
                instrument="spot:BTCUSDT",
                interval="1m" if kind == "klines" else "event",
                start_ms=ms(feb_day),
                end_ms=ms(feb_end),
            ),
        )

    partial_day = datetime(2021, 4, 25, tzinfo=UTC)
    partial_end = partial_day + timedelta(days=1)
    partial_filename = f"{SYMBOL}-trades-2021-04-25.zip"
    result.append(
        ArchiveTask(
            label="spot-partial-2021-04-25:trades",
            url=f"https://data.binance.vision/data/spot/daily/trades/{SYMBOL}/{partial_filename}",
            filename=partial_filename,
            source_role="SPOT_PARTIAL_2021_04_25_DAILY_TRADES",
            instrument="spot:BTCUSDT",
            interval="event",
            start_ms=ms(partial_day),
            end_ms=ms(partial_end),
        ),
    )

    for no_trade_day in (datetime(2021, 3, 6, tzinfo=UTC), datetime(2021, 4, 20, tzinfo=UTC)):
        day_text = no_trade_day.date().isoformat()
        no_trade_filename = f"{SYMBOL}-trades-{day_text}.zip"
        result.append(
            ArchiveTask(
                label=f"spot-no-trade-{day_text}:trades",
                url=f"https://data.binance.vision/data/spot/daily/trades/{SYMBOL}/{no_trade_filename}",
                filename=no_trade_filename,
                source_role=f"SPOT_NO_TRADE_{day_text.replace('-', '_')}_DAILY_TRADES",
                instrument="spot:BTCUSDT",
                interval="event",
                start_ms=ms(no_trade_day),
                end_ms=ms(no_trade_day + timedelta(days=1)),
            ),
        )

    feb_month = datetime(2021, 2, 1, tzinfo=UTC)
    feb_month_end = month_end(feb_month)
    filename = f"{SYMBOL}-1m-2021-02.zip"
    result.append(
        ArchiveTask(
            label="spot-target:monthly-klines",
            url=f"https://data.binance.vision/data/spot/monthly/klines/{SYMBOL}/1m/{filename}",
            filename=filename,
            source_role="SPOT_TARGET_MONTHLY_KLINES",
            instrument="spot:BTCUSDT",
            interval="1m",
            start_ms=ms(feb_month),
            end_ms=ms(feb_month_end),
        ),
    )

    mark_day = datetime(2020, 12, 17, tzinfo=UTC)
    mark_day_end = mark_day + timedelta(days=1)
    filename = f"{SYMBOL}-1m-2020-12-17.zip"
    result.append(
        ArchiveTask(
            label="mark-gap:daily",
            url=f"https://data.binance.vision/data/futures/um/daily/markPriceKlines/{SYMBOL}/1m/{filename}",
            filename=filename,
            source_role="USDM_MARK_GAP_DAILY_ARCHIVE",
            instrument="usdm-perpetual:BTCUSDT",
            interval="1m",
            start_ms=ms(mark_day),
            end_ms=ms(mark_day_end),
            allow_404=True,
        ),
    )
    mark_month = datetime(2020, 12, 1, tzinfo=UTC)
    mark_month_end = month_end(mark_month)
    filename = f"{SYMBOL}-1m-2020-12.zip"
    result.append(
        ArchiveTask(
            label="mark-gap:monthly",
            url=f"https://data.binance.vision/data/futures/um/monthly/markPriceKlines/{SYMBOL}/1m/{filename}",
            filename=filename,
            source_role="USDM_MARK_GAP_MONTHLY_ARCHIVE",
            instrument="usdm-perpetual:BTCUSDT",
            interval="1m",
            start_ms=ms(mark_month),
            end_ms=ms(mark_month_end),
        ),
    )
    return result


def page_tasks(endpoint: str, role: str, start: datetime, end: datetime) -> list[tuple[str, RequestIdentity]]:
    result: list[tuple[str, RequestIdentity]] = []
    cursor = ms(start)
    terminal = ms(end)
    page = 0
    while cursor < terminal:
        page_end = min(cursor + 1000 * ONE_MINUTE_MS, terminal)
        query = urlencode(
            {
                "symbol": SYMBOL,
                "interval": "1m",
                "startTime": cursor,
                "endTime": page_end - 1,
                "limit": 1000,
            },
        )
        result.append(
            (
                f"{endpoint}?{query}",
                RequestIdentity(
                    source_role=role,
                    instrument="spot:BTCUSDT" if "/api/v3/" in endpoint else "usdm-perpetual:BTCUSDT",
                    interval="1m",
                    requested_start_ms=cursor,
                    requested_end_ms=page_end,
                    pagination_position=f"page:{page}:{cursor}:{page_end}",
                ),
            ),
        )
        cursor = page_end
        page += 1
    return result


def public_reference_tasks() -> list[tuple[str, RequestIdentity]]:
    urls = (
        ("https://raw.githubusercontent.com/binance/binance-public-data/master/README.md", "BINANCE_PUBLIC_DATA_README"),
        ("https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/faqs/market_data_only.md", "BINANCE_SPOT_MARKET_DATA_ONLY_FAQ"),
        ("https://api.github.com/repos/binance/binance-public-data/issues/475", "BINANCE_PUBLIC_DATA_ISSUE_475"),
        ("https://api.github.com/repos/binance/binance-public-data/issues/365", "BINANCE_PUBLIC_DATA_ISSUE_365"),
        ("https://api.github.com/repos/binance/binance-public-data/issues/483", "BINANCE_PUBLIC_DATA_ISSUE_483"),
        ("https://api.github.com/repos/binance/binance-public-data/issues/484", "BINANCE_PUBLIC_DATA_ISSUE_484"),
        ("https://data-api.binance.vision/api/v3/exchangeInfo?symbol=BTCUSDT", "SPOT_CURRENT_INSTRUMENT_METADATA"),
        ("https://fapi.binance.com/fapi/v1/exchangeInfo", "USDM_CURRENT_INSTRUMENT_METADATA"),
        ("https://fapi.binance.com/fapi/v1/fundingInfo", "USDM_CURRENT_FUNDING_INFO"),
    )
    return [
        (
            url,
            RequestIdentity(
                source_role=role,
                instrument="BTCUSDT" if "github" not in url else "NOT_APPLICABLE",
                interval="NOT_APPLICABLE",
                requested_start_ms=None,
                requested_end_ms=None,
                pagination_position="single-object",
            ),
        )
        for url, role in urls
    ]


def acquire_many(requests: Iterable[tuple[str, RequestIdentity]], workers: int = 8) -> list[dict[str, Any]]:
    request_list = list(requests)
    result: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(capture_retry, url, identity): (url, identity)
            for url, identity in request_list
        }
        for future in concurrent.futures.as_completed(futures):
            result.append(future.result())
    result.sort(key=lambda item: item["exact_url"])
    return result


def acquire() -> int:
    started = utc_now()
    archive_tasks = critical_archive_tasks() + july_archive_tasks()
    archive_results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(acquire_archive, task): task for task in archive_tasks}
        for future in concurrent.futures.as_completed(futures):
            archive_results.append(future.result())
    archive_results.sort(key=lambda item: item["task"]["url"])

    references = acquire_many(public_reference_tasks(), workers=5)

    spot_pages = acquire_many(
        page_tasks(
            "https://data-api.binance.vision/api/v3/klines",
            "SPOT_REST_KLINES_DATA_API",
            N1_START,
            N1_END,
        ),
        workers=8,
    )
    fapi_pages = acquire_many(
        page_tasks(
            "https://fapi.binance.com/fapi/v1/klines",
            "USDM_REST_EXECUTION_KLINES_JULY",
            JULY_START,
            N1_END,
        )
        + page_tasks(
            "https://fapi.binance.com/fapi/v1/markPriceKlines",
            "USDM_REST_MARK_KLINES_JULY",
            JULY_START,
            N1_END,
        ),
        workers=6,
    )

    mark_gap_query = urlencode(
        {
            "symbol": SYMBOL,
            "interval": "1m",
            "startTime": ms(MARK_GAP_START - timedelta(minutes=2)),
            "endTime": ms(MARK_GAP_END + timedelta(minutes=2)) - 1,
            "limit": 1000,
        },
    )
    mark_gap_rest = capture_retry(
        f"https://fapi.binance.com/fapi/v1/markPriceKlines?{mark_gap_query}",
        RequestIdentity(
            source_role="USDM_REST_MARK_GAP_PROBE",
            instrument="usdm-perpetual:BTCUSDT",
            interval="1m",
            requested_start_ms=ms(MARK_GAP_START - timedelta(minutes=2)),
            requested_end_ms=ms(MARK_GAP_END + timedelta(minutes=2)),
            pagination_position="target-gap-bracket",
        ),
    )

    funding_query = urlencode(
        {
            "symbol": SYMBOL,
            "startTime": ms(N1_START),
            "endTime": ms(N1_END) - 1,
            "limit": 1000,
        },
    )
    funding_rest = capture_retry(
        f"https://fapi.binance.com/fapi/v1/fundingRate?{funding_query}",
        RequestIdentity(
            source_role="USDM_REST_FUNDING_RATE_N1",
            instrument="usdm-perpetual:BTCUSDT",
            interval="funding-event",
            requested_start_ms=ms(N1_START),
            requested_end_ms=ms(N1_END),
            pagination_position="complete-candidate-window",
        ),
    )

    completed = utc_now()
    manifest = {
        "schema": "free-official-binance-phase-a-acquisition-v1",
        "epoch": "FREE_OFFICIAL_BINANCE_DATA_AND_DUCKDB_REPAIR_001",
        "started_at_utc": started,
        "completed_at_utc": completed,
        "owner_source_policy": "FREE_OFFICIAL_BINANCE_ONLY",
        "credentials_used": False,
        "third_party_provider_used": False,
        "candidate_window": {
            "shift_months": 1,
            "start_inclusive": N1_START.isoformat().replace("+00:00", "Z"),
            "end_exclusive": N1_END.isoformat().replace("+00:00", "Z"),
        },
        "archive_pairs": archive_results,
        "public_references": references,
        "spot_rest_pages": spot_pages,
        "fapi_july_pages": fapi_pages,
        "mark_gap_rest": mark_gap_rest,
        "funding_rest": funding_rest,
    }
    identity_material = dict(manifest)
    identity_material.pop("started_at_utc")
    identity_material.pop("completed_at_utc")
    manifest["acquisition_identity"] = sha256_bytes(canonical_json(identity_material))
    replace_json(INDEX_PATH, manifest)
    print(
        json.dumps(
            {
                "status": "ACQUISITION_COMPLETE",
                "acquisition_identity": manifest["acquisition_identity"],
                "archive_pair_count": len(archive_results),
                "public_reference_count": len(references),
                "spot_rest_page_count": len(spot_pages),
                "fapi_july_page_count": len(fapi_pages),
                "raw_store": str(RAW_ROOT.relative_to(ROOT)),
            },
            sort_keys=True,
        ),
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("acquire",))
    arguments = parser.parse_args()
    if arguments.command == "acquire":
        return acquire()
    raise AssertionError(arguments.command)


if __name__ == "__main__":
    raise SystemExit(main())
