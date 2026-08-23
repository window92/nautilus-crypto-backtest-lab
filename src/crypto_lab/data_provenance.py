"""Official Binance provenance and deterministic minute reconciliation.

This module owns no trading or financial state.  It preserves HTTP response
bodies before parsing, validates official Binance observations, derives Spot
bars only from complete official aggregate-trade events, and assigns one
coverage disposition per UTC minute.  It intentionally has no DuckDB or
Nautilus dependency so acquisition and validation remain separate concerns.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from decimal import InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any
from typing import Iterable
from typing import Iterator
from urllib.parse import parse_qsl
from urllib.parse import urlsplit


ONE_MINUTE_MS = 60_000
SPOT_SYMBOL = "BTCUSDT"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INTEGER = re.compile(r"0|[1-9][0-9]*\Z")


class ProvenanceError(ValueError):
    """A fail-closed official-data validation error."""

    def __init__(self, code: str, message: str, *, evidence: dict[str, Any] | None = None) -> None:
        self.code = code
        self.evidence = dict(evidence or {})
        super().__init__(f"{code}: {message}")


class CoverageDisposition(StrEnum):
    REAL_OFFICIAL_BAR = "REAL_OFFICIAL_BAR"
    DERIVED_FROM_OFFICIAL_TRADES = "DERIVED_FROM_OFFICIAL_TRADES"
    VERIFIED_NO_TRADE_INTERVAL = "VERIFIED_NO_TRADE_INTERVAL"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    SOURCE_INCOMPLETE = "SOURCE_INCOMPLETE"
    UNRESOLVED_GAP = "UNRESOLVED_GAP"


class KlineSource(StrEnum):
    SPOT_REST = "SPOT_REST_API_V3_KLINES"
    SPOT_DAILY = "SPOT_DAILY_ARCHIVE"
    SPOT_MONTHLY = "SPOT_MONTHLY_ARCHIVE"
    USDM_EXECUTION_REST = "USDM_REST_FAPI_V1_KLINES"
    USDM_EXECUTION_DAILY = "USDM_DAILY_EXECUTION_ARCHIVE"
    USDM_EXECUTION_MONTHLY = "USDM_MONTHLY_EXECUTION_ARCHIVE"
    USDM_MARK_REST = "USDM_REST_FAPI_V1_MARK_PRICE_KLINES"
    USDM_MARK_DAILY = "USDM_DAILY_MARK_ARCHIVE"
    USDM_MARK_MONTHLY = "USDM_MONTHLY_MARK_ARCHIVE"


class AggTradeSource(StrEnum):
    SPOT_REST = "SPOT_REST_API_V3_AGG_TRADES"
    SPOT_DAILY = "SPOT_DAILY_AGG_TRADES_ARCHIVE"
    SPOT_MONTHLY = "SPOT_MONTHLY_AGG_TRADES_ARCHIVE"


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_canonical_value(item) for item in value]
    return value


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_now_text() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_sha256(value: str, name: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ProvenanceError("DATA_HASH_MISMATCH", f"{name} is not a lowercase SHA-256")


def _decimal(value: str, name: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ProvenanceError("DATA_SOURCE_INVALID", f"invalid Decimal {name}") from exc
    if not result.is_finite():
        raise ProvenanceError("DATA_SOURCE_INVALID", f"non-finite Decimal {name}")
    return result


def _integer(value: str | int, name: str) -> int:
    text = str(value)
    if _INTEGER.fullmatch(text) is None:
        raise ProvenanceError("DATA_SOURCE_INVALID", f"invalid integer {name}")
    return int(text)


def _strict_json(payload: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ProvenanceError("DATA_SOURCE_INVALID", f"duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            payload,
            object_pairs_hook=pairs,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except ProvenanceError:
        raise
    except Exception as exc:
        raise ProvenanceError("DATA_SOURCE_INVALID", "malformed JSON response") from exc


def _exclusive_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ProvenanceError("DATA_HASH_MISMATCH", f"immutable path collision at {path}")
        return
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _exclusive_json(path: Path, value: Any) -> None:
    _exclusive_bytes(path, canonical_json_bytes(value) + b"\n")


def validate_official_url(url: str) -> None:
    """Fail closed on every network locator used by the repair."""

    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ProvenanceError("DATA_SOURCE_INVALID", "official source must use plain HTTPS")
    host = parsed.hostname or ""
    path = parsed.path
    query = dict(parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True))
    valid = False
    if host == "data.binance.vision":
        valid = path.startswith("/data/") and not parsed.query
    elif host in {"api.binance.com", "data-api.binance.vision"}:
        if path == "/api/v3/klines":
            valid = set(query) == {"symbol", "interval", "startTime", "endTime", "limit"}
        elif path == "/api/v3/aggTrades":
            valid = set(query) in (
                {"symbol", "fromId", "limit"},
                {"symbol", "startTime", "endTime", "limit"},
            )
        elif path == "/api/v3/exchangeInfo":
            valid = set(query) == {"symbol"}
    elif host == "fapi.binance.com":
        if path in {"/fapi/v1/klines", "/fapi/v1/markPriceKlines"}:
            valid = set(query) == {"symbol", "interval", "startTime", "endTime", "limit"}
        elif path == "/fapi/v1/fundingRate":
            valid = set(query) == {"symbol", "startTime", "endTime", "limit"}
        elif path == "/fapi/v1/fundingInfo":
            valid = not query
        elif path == "/fapi/v1/exchangeInfo":
            valid = not query
    elif host == "raw.githubusercontent.com":
        valid = path.startswith("/binance/")
    elif host == "api.github.com":
        valid = path.startswith("/repos/binance/")
    elif host == "developers.binance.com":
        valid = path.startswith("/docs/")
    if not valid:
        raise ProvenanceError("DATA_SOURCE_INVALID", f"source URL is outside the allowlist: {url}")


@dataclass(frozen=True)
class HttpObservation:
    observation_id: str
    raw_object_sha256: str
    exact_url: str
    status_code: int
    response_headers: dict[str, list[str]]
    capture_started_at_utc: str
    capture_completed_at_utc: str
    byte_length: int
    source_role: str
    pagination_position: str
    local_object_path: str
    local_observation_path: str

    def material_payload(self) -> dict[str, Any]:
        return {
            "raw_object_sha256": self.raw_object_sha256,
            "exact_url": self.exact_url,
            "status_code": self.status_code,
            "response_headers": self.response_headers,
            "capture_started_at_utc": self.capture_started_at_utc,
            "capture_completed_at_utc": self.capture_completed_at_utc,
            "byte_length": self.byte_length,
            "source_role": self.source_role,
            "pagination_position": self.pagination_position,
        }


class HttpRawStore:
    """Immutable body store with additive exact HTTP observation metadata."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.objects = self.root / "objects" / "sha256"
        self.observations = self.root / "http-observations"
        self.failures = self.root / "network-failures"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.observations.mkdir(parents=True, exist_ok=True)
        self.failures.mkdir(parents=True, exist_ok=True)

    def object_path(self, digest: str) -> Path:
        _require_sha256(digest, "raw object digest")
        return self.objects / digest[:2] / f"{digest}.bin"

    def read(self, digest: str) -> bytes:
        payload = self.object_path(digest).read_bytes()
        if sha256_bytes(payload) != digest:
            raise ProvenanceError("DATA_HASH_MISMATCH", f"raw object {digest} changed")
        return payload

    def capture(
        self,
        url: str,
        *,
        source_role: str,
        pagination_position: str,
        timeout_seconds: int = 120,
        accepted_statuses: tuple[int, ...] = (200,),
    ) -> HttpObservation:
        validate_official_url(url)
        started = utc_now_text()
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "*/*",
                "Accept-Encoding": "identity",
                "User-Agent": "nautilus-crypto-backtest-lab-data-repair/1",
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
                "pagination_position": pagination_position,
                "source_role": source_role,
            }
            identity = sha256_bytes(canonical_json_bytes(failure))
            _exclusive_json(self.failures / f"{identity}.json", failure)
            raise ProvenanceError(
                "SOURCE_INCOMPLETE",
                f"network acquisition failed for {url}",
                evidence={"failure_id": identity},
            ) from exc

        status = int(getattr(response, "status", response.getcode()))
        header_pairs = list(response.headers.items())
        headers: dict[str, list[str]] = {}
        for name, value in header_pairs:
            headers.setdefault(name.lower(), []).append(value)
        headers = {key: headers[key] for key in sorted(headers)}

        self.objects.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix="capture-", dir=self.root)
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
            target = self.object_path(raw_hash)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.stat().st_size != size or sha256_bytes(target.read_bytes()) != raw_hash:
                    raise ProvenanceError("DATA_HASH_MISMATCH", "immutable raw-object collision")
                Path(temporary_name).unlink()
            else:
                os.chmod(temporary_name, 0o444)
                os.replace(temporary_name, target)
        except BaseException:
            if Path(temporary_name).exists():
                Path(temporary_name).unlink()
            raise
        finally:
            response.close()

        completed = utc_now_text()
        material = {
            "raw_object_sha256": raw_hash,
            "exact_url": url,
            "status_code": status,
            "response_headers": headers,
            "capture_started_at_utc": started,
            "capture_completed_at_utc": completed,
            "byte_length": size,
            "source_role": source_role,
            "pagination_position": pagination_position,
        }
        observation_id = sha256_bytes(canonical_json_bytes(material))
        observation_path = self.observations / f"{observation_id}.json"
        _exclusive_json(observation_path, {"observation_id": observation_id, **material})
        observation = HttpObservation(
            observation_id=observation_id,
            local_object_path=str(target),
            local_observation_path=str(observation_path),
            **material,
        )
        if status not in accepted_statuses:
            raise ProvenanceError(
                "SOURCE_INCOMPLETE",
                f"official source returned HTTP {status}",
                evidence={
                    "http_observation_id": observation_id,
                    "raw_object_sha256": raw_hash,
                },
            )
        return observation


@dataclass(frozen=True)
class KlineObservation:
    source_kind: KlineSource
    source_sha256: str
    row_number: int
    symbol: str
    interval: str
    open_time_ms: int
    open_text: str
    high_text: str
    low_text: str
    close_text: str
    base_volume_text: str
    close_time_ms: int
    quote_volume_text: str
    trade_count: int
    taker_buy_base_text: str
    taker_buy_quote_text: str
    ignore_text: str
    invalid_reasons: tuple[str, ...]

    @property
    def open(self) -> Decimal:
        return _decimal(self.open_text, "open")

    @property
    def high(self) -> Decimal:
        return _decimal(self.high_text, "high")

    @property
    def low(self) -> Decimal:
        return _decimal(self.low_text, "low")

    @property
    def close(self) -> Decimal:
        return _decimal(self.close_text, "close")

    @property
    def base_volume(self) -> Decimal:
        return _decimal(self.base_volume_text, "base volume")

    @property
    def quote_volume(self) -> Decimal:
        return _decimal(self.quote_volume_text, "quote volume")

    @property
    def taker_buy_base(self) -> Decimal:
        return _decimal(self.taker_buy_base_text, "taker-buy base")

    @property
    def taker_buy_quote(self) -> Decimal:
        return _decimal(self.taker_buy_quote_text, "taker-buy quote")

    def numeric_material(self, *, include_close_time: bool = True) -> tuple[Any, ...]:
        values: tuple[Any, ...] = (
            self.open_time_ms,
            self.open,
            self.high,
            self.low,
            self.close,
            self.base_volume,
            self.quote_volume,
            self.trade_count,
            self.taker_buy_base,
            self.taker_buy_quote,
        )
        return values + ((self.close_time_ms,) if include_close_time else ())

    def semantically_equals(self, other: KlineObservation, *, include_close_time: bool = True) -> bool:
        return (
            self.symbol == other.symbol
            and self.interval == other.interval
            and self.numeric_material(include_close_time=include_close_time)
            == other.numeric_material(include_close_time=include_close_time)
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind.value,
            "source_sha256": self.source_sha256,
            "row_number": self.row_number,
            "symbol": self.symbol,
            "interval": self.interval,
            "open_time_ms": self.open_time_ms,
            "open_text": self.open_text,
            "high_text": self.high_text,
            "low_text": self.low_text,
            "close_text": self.close_text,
            "base_volume_text": self.base_volume_text,
            "close_time_ms": self.close_time_ms,
            "quote_volume_text": self.quote_volume_text,
            "trade_count": self.trade_count,
            "taker_buy_base_text": self.taker_buy_base_text,
            "taker_buy_quote_text": self.taker_buy_quote_text,
            "ignore_text": self.ignore_text,
            "invalid_reasons": list(self.invalid_reasons),
        }


def _kline_from_fields(
    fields: list[Any],
    *,
    source_kind: KlineSource,
    source_sha256: str,
    row_number: int,
    symbol: str,
) -> KlineObservation:
    _require_sha256(source_sha256, "kline source")
    if len(fields) != 12:
        raise ProvenanceError("DATA_SOURCE_INVALID", f"kline row {row_number} has {len(fields)} fields")
    text = [str(item) for item in fields]
    open_time = _integer(text[0], "open_time")
    close_time = _integer(text[6], "close_time")
    trade_count = _integer(text[8], "trade_count")
    decimals = [_decimal(text[index], name) for index, name in (
        (1, "open"),
        (2, "high"),
        (3, "low"),
        (4, "close"),
        (5, "base_volume"),
        (7, "quote_volume"),
        (9, "taker_buy_base"),
        (10, "taker_buy_quote"),
    )]
    open_, high, low, close, base_volume, quote_volume, taker_base, taker_quote = decimals
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
    if trade_count == 0 and base_volume != 0:
        reasons.append("TRADE_COUNT_VOLUME_MISMATCH")
    return KlineObservation(
        source_kind=source_kind,
        source_sha256=source_sha256,
        row_number=row_number,
        symbol=symbol,
        interval="1m",
        open_time_ms=open_time,
        open_text=text[1],
        high_text=text[2],
        low_text=text[3],
        close_text=text[4],
        base_volume_text=text[5],
        close_time_ms=close_time,
        quote_volume_text=text[7],
        trade_count=trade_count,
        taker_buy_base_text=text[9],
        taker_buy_quote_text=text[10],
        ignore_text=text[11],
        invalid_reasons=tuple(reasons),
    )


def parse_kline_rest_page(
    payload: bytes,
    *,
    source_kind: KlineSource,
    source_sha256: str,
    symbol: str = SPOT_SYMBOL,
) -> tuple[KlineObservation, ...]:
    value = _strict_json(payload)
    if not isinstance(value, list):
        raise ProvenanceError("DATA_SOURCE_INVALID", "kline REST response is not an array")
    result: list[KlineObservation] = []
    for row_number, row in enumerate(value, start=1):
        if not isinstance(row, list):
            raise ProvenanceError("DATA_SOURCE_INVALID", "kline REST row is not an array")
        result.append(
            _kline_from_fields(
                row,
                source_kind=source_kind,
                source_sha256=source_sha256,
                row_number=row_number,
                symbol=symbol,
            ),
        )
    _validate_observation_order(result)
    return tuple(result)


def iter_kline_archive(
    path: Path,
    *,
    source_kind: KlineSource,
    source_sha256: str,
    expected_member: str,
    symbol: str = SPOT_SYMBOL,
) -> Iterator[KlineObservation]:
    _require_sha256(source_sha256, "archive source")
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].filename != expected_member or members[0].is_dir():
            raise ProvenanceError("DATA_SOURCE_INVALID", "archive member identity mismatch")
        with archive.open(members[0], "r") as binary:
            text = io.TextIOWrapper(binary, encoding="utf-8", newline="")
            rows = csv.reader(text, strict=True)
            prior: int | None = None
            seen: set[int] = set()
            row_number = 0
            for raw in rows:
                row_number += 1
                if raw and raw[0] == "open_time":
                    if row_number != 1:
                        raise ProvenanceError("DATA_SOURCE_INVALID", "kline header appears mid-stream")
                    continue
                item = _kline_from_fields(
                    raw,
                    source_kind=source_kind,
                    source_sha256=source_sha256,
                    row_number=row_number,
                    symbol=symbol,
                )
                if item.open_time_ms in seen:
                    raise ProvenanceError("DATA_DUPLICATE_CONFLICT", "duplicate kline timestamp")
                if prior is not None and item.open_time_ms <= prior:
                    raise ProvenanceError("DATA_TIMESTAMP_INVALID", "non-monotonic kline archive")
                seen.add(item.open_time_ms)
                prior = item.open_time_ms
                yield item


def _validate_observation_order(items: Iterable[KlineObservation]) -> None:
    prior: int | None = None
    seen: set[int] = set()
    for item in items:
        if item.open_time_ms in seen:
            raise ProvenanceError("DATA_DUPLICATE_CONFLICT", "duplicate kline timestamp")
        if prior is not None and item.open_time_ms <= prior:
            raise ProvenanceError("DATA_TIMESTAMP_INVALID", "non-monotonic kline response")
        seen.add(item.open_time_ms)
        prior = item.open_time_ms


@dataclass(frozen=True)
class AggTrade:
    source_kind: AggTradeSource
    source_sha256: str
    row_number: int
    symbol: str
    aggregate_trade_id: int
    price_text: str
    quantity_text: str
    first_trade_id: int
    last_trade_id: int
    timestamp_ms: int
    buyer_is_maker: bool
    best_price_match: bool

    @property
    def price(self) -> Decimal:
        return _decimal(self.price_text, "aggTrade price")

    @property
    def quantity(self) -> Decimal:
        return _decimal(self.quantity_text, "aggTrade quantity")

    def material_tuple(self) -> tuple[Any, ...]:
        return (
            self.aggregate_trade_id,
            self.price,
            self.quantity,
            self.first_trade_id,
            self.last_trade_id,
            self.timestamp_ms,
            self.buyer_is_maker,
            self.best_price_match,
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind.value,
            "source_sha256": self.source_sha256,
            "row_number": self.row_number,
            "symbol": self.symbol,
            "aggregate_trade_id": self.aggregate_trade_id,
            "price_text": self.price_text,
            "quantity_text": self.quantity_text,
            "first_trade_id": self.first_trade_id,
            "last_trade_id": self.last_trade_id,
            "timestamp_ms": self.timestamp_ms,
            "buyer_is_maker": self.buyer_is_maker,
            "best_price_match": self.best_price_match,
        }


def _boolean(value: Any, name: str) -> bool:
    if type(value) is bool:
        return value
    text = str(value).lower()
    if text == "true":
        return True
    if text == "false":
        return False
    raise ProvenanceError("DATA_SOURCE_INVALID", f"invalid boolean {name}")


def _aggtrade_from_fields(
    fields: list[Any],
    *,
    source_kind: AggTradeSource,
    source_sha256: str,
    row_number: int,
    symbol: str,
) -> AggTrade:
    if len(fields) not in {7, 8}:
        raise ProvenanceError("DATA_SOURCE_INVALID", "aggTrade row must have seven or eight fields")
    aggregate_id = _integer(fields[0], "aggregate_trade_id")
    price_text = str(fields[1])
    quantity_text = str(fields[2])
    price = _decimal(price_text, "price")
    quantity = _decimal(quantity_text, "quantity")
    first_id = _integer(fields[3], "first_trade_id")
    last_id = _integer(fields[4], "last_trade_id")
    timestamp = _integer(fields[5], "timestamp")
    if price <= 0 or quantity <= 0 or first_id > last_id:
        raise ProvenanceError("DATA_SOURCE_INVALID", "invalid aggTrade value or trade-ID range")
    return AggTrade(
        source_kind=source_kind,
        source_sha256=source_sha256,
        row_number=row_number,
        symbol=symbol,
        aggregate_trade_id=aggregate_id,
        price_text=price_text,
        quantity_text=quantity_text,
        first_trade_id=first_id,
        last_trade_id=last_id,
        timestamp_ms=timestamp,
        buyer_is_maker=_boolean(fields[6], "buyer_is_maker"),
        best_price_match=_boolean(fields[7], "best_price_match") if len(fields) == 8 else True,
    )


def parse_aggtrade_rest_page(
    payload: bytes,
    *,
    source_sha256: str,
    symbol: str = SPOT_SYMBOL,
) -> tuple[AggTrade, ...]:
    value = _strict_json(payload)
    if not isinstance(value, list):
        raise ProvenanceError("DATA_SOURCE_INVALID", "aggTrades REST response is not an array")
    result: list[AggTrade] = []
    for row_number, item in enumerate(value, start=1):
        if not isinstance(item, dict) or set(item) != {"a", "p", "q", "f", "l", "T", "m", "M"}:
            raise ProvenanceError("DATA_SOURCE_INVALID", "aggTrades REST object schema mismatch")
        result.append(
            _aggtrade_from_fields(
                [item[key] for key in ("a", "p", "q", "f", "l", "T", "m", "M")],
                source_kind=AggTradeSource.SPOT_REST,
                source_sha256=source_sha256,
                row_number=row_number,
                symbol=symbol,
            ),
        )
    validate_aggtrade_sequence(result, require_contiguous=True)
    return tuple(result)


def iter_aggtrade_archive(
    path: Path,
    *,
    source_kind: AggTradeSource,
    source_sha256: str,
    expected_member: str,
    symbol: str = SPOT_SYMBOL,
) -> Iterator[AggTrade]:
    _require_sha256(source_sha256, "aggTrade archive source")
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].filename != expected_member or members[0].is_dir():
            raise ProvenanceError("DATA_SOURCE_INVALID", "aggTrade archive member mismatch")
        with archive.open(members[0], "r") as binary:
            text = io.TextIOWrapper(binary, encoding="utf-8", newline="")
            rows = csv.reader(text, strict=True)
            prior: AggTrade | None = None
            row_number = 0
            for raw in rows:
                row_number += 1
                if raw and raw[0] in {"agg_trade_id", "aggregate_trade_id"}:
                    if row_number != 1:
                        raise ProvenanceError("DATA_SOURCE_INVALID", "aggTrade header appears mid-stream")
                    continue
                item = _aggtrade_from_fields(
                    raw,
                    source_kind=source_kind,
                    source_sha256=source_sha256,
                    row_number=row_number,
                    symbol=symbol,
                )
                if prior is not None:
                    # Strict +1 adjacency proves both uniqueness and completeness
                    # without retaining a month-scale set of every observed ID.
                    _validate_adjacent_aggtrades(prior, item, require_contiguous=True)
                prior = item
                yield item


def _validate_adjacent_aggtrades(
    first: AggTrade,
    second: AggTrade,
    *,
    require_contiguous: bool,
) -> None:
    if first.symbol != second.symbol or first.source_kind is not second.source_kind:
        raise ProvenanceError("DATA_ROLE_MISMATCH", "aggTrade sequence role or symbol mismatch")
    if (second.timestamp_ms, second.aggregate_trade_id) <= (
        first.timestamp_ms,
        first.aggregate_trade_id,
    ):
        raise ProvenanceError("SOURCE_INCOMPLETE", "aggregate events are not strictly ordered")
    if second.first_trade_id <= first.last_trade_id:
        raise ProvenanceError("SOURCE_INCOMPLETE", "underlying trade-ID ranges overlap")
    if require_contiguous:
        if second.aggregate_trade_id != first.aggregate_trade_id + 1:
            raise ProvenanceError("SOURCE_INCOMPLETE", "aggregate trade-ID gap")
        if second.first_trade_id != first.last_trade_id + 1:
            raise ProvenanceError("SOURCE_INCOMPLETE", "underlying trade-ID gap")


def validate_aggtrade_sequence(
    events: Iterable[AggTrade],
    *,
    require_contiguous: bool,
) -> tuple[AggTrade, ...]:
    ordered = tuple(events)
    seen: set[int] = set()
    prior: AggTrade | None = None
    for item in ordered:
        if item.aggregate_trade_id in seen:
            raise ProvenanceError("SOURCE_INCOMPLETE", "duplicate aggregate trade ID")
        if prior is not None:
            _validate_adjacent_aggtrades(prior, item, require_contiguous=require_contiguous)
        seen.add(item.aggregate_trade_id)
        prior = item
    return ordered


@dataclass(frozen=True)
class DerivedSpotKline:
    symbol: str
    open_time_ms: int
    close_time_ms: int
    open_text: str
    high_text: str
    low_text: str
    close_text: str
    base_volume_text: str
    quote_volume_text: str
    trade_count: int
    taker_buy_base_text: str
    taker_buy_quote_text: str
    first_aggregate_trade_id: int
    last_aggregate_trade_id: int
    first_underlying_trade_id: int
    last_underlying_trade_id: int
    source_sha256s: tuple[str, ...]
    derivation_identity: str

    def numeric_material(self, *, include_close_time: bool = True) -> tuple[Any, ...]:
        values: tuple[Any, ...] = (
            self.open_time_ms,
            Decimal(self.open_text),
            Decimal(self.high_text),
            Decimal(self.low_text),
            Decimal(self.close_text),
            Decimal(self.base_volume_text),
            Decimal(self.quote_volume_text),
            self.trade_count,
            Decimal(self.taker_buy_base_text),
            Decimal(self.taker_buy_quote_text),
        )
        return values + ((self.close_time_ms,) if include_close_time else ())

    def matches(self, observation: KlineObservation, *, include_close_time: bool = True) -> bool:
        return (
            self.symbol == observation.symbol
            and self.numeric_material(include_close_time=include_close_time)
            == observation.numeric_material(include_close_time=include_close_time)
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "open_time_ms": self.open_time_ms,
            "close_time_ms": self.close_time_ms,
            "open_text": self.open_text,
            "high_text": self.high_text,
            "low_text": self.low_text,
            "close_text": self.close_text,
            "base_volume_text": self.base_volume_text,
            "quote_volume_text": self.quote_volume_text,
            "trade_count": self.trade_count,
            "taker_buy_base_text": self.taker_buy_base_text,
            "taker_buy_quote_text": self.taker_buy_quote_text,
            "first_aggregate_trade_id": self.first_aggregate_trade_id,
            "last_aggregate_trade_id": self.last_aggregate_trade_id,
            "first_underlying_trade_id": self.first_underlying_trade_id,
            "last_underlying_trade_id": self.last_underlying_trade_id,
            "source_sha256s": list(self.source_sha256s),
            "derivation_identity": self.derivation_identity,
        }


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value, "f")


def derive_spot_kline(events: Iterable[AggTrade], *, minute_start_ms: int) -> DerivedSpotKline:
    if minute_start_ms % ONE_MINUTE_MS:
        raise ProvenanceError("DATA_TIMESTAMP_INVALID", "derived minute is off grid")
    ordered = validate_aggtrade_sequence(events, require_contiguous=True)
    if not ordered:
        raise ProvenanceError("UNRESOLVED_GAP", "a bar cannot be derived without official trades")
    if any(
        item.timestamp_ms < minute_start_ms or item.timestamp_ms >= minute_start_ms + ONE_MINUTE_MS
        for item in ordered
    ):
        raise ProvenanceError("DATA_TIMESTAMP_INVALID", "aggTrade lies outside derived minute")
    if len({item.symbol for item in ordered}) != 1:
        raise ProvenanceError("DATA_ROLE_MISMATCH", "derived events contain multiple symbols")
    prices = [item.price for item in ordered]
    base_volume = sum((item.quantity for item in ordered), Decimal(0))
    quote_volume = sum((item.price * item.quantity for item in ordered), Decimal(0))
    trade_count = sum(item.last_trade_id - item.first_trade_id + 1 for item in ordered)
    taker_events = [item for item in ordered if not item.buyer_is_maker]
    taker_base = sum((item.quantity for item in taker_events), Decimal(0))
    taker_quote = sum((item.price * item.quantity for item in taker_events), Decimal(0))
    material = {
        "symbol": ordered[0].symbol,
        "open_time_ms": minute_start_ms,
        "close_time_ms": minute_start_ms + ONE_MINUTE_MS - 1,
        "open_text": _decimal_text(prices[0]),
        "high_text": _decimal_text(max(prices)),
        "low_text": _decimal_text(min(prices)),
        "close_text": _decimal_text(prices[-1]),
        "base_volume_text": _decimal_text(base_volume),
        "quote_volume_text": _decimal_text(quote_volume),
        "trade_count": trade_count,
        "taker_buy_base_text": _decimal_text(taker_base),
        "taker_buy_quote_text": _decimal_text(taker_quote),
        "first_aggregate_trade_id": ordered[0].aggregate_trade_id,
        "last_aggregate_trade_id": ordered[-1].aggregate_trade_id,
        "first_underlying_trade_id": ordered[0].first_trade_id,
        "last_underlying_trade_id": ordered[-1].last_trade_id,
        "source_sha256s": sorted({item.source_sha256 for item in ordered}),
    }
    identity = sha256_bytes(canonical_json_bytes(material))
    return DerivedSpotKline(
        derivation_identity=identity,
        source_sha256s=tuple(material.pop("source_sha256s")),
        **material,
    )


@dataclass(frozen=True)
class NoTradeProof:
    symbol: str
    start_ms: int
    end_ms: int
    before: AggTrade
    after: AggTrade
    official_sources: tuple[str, ...]
    proof_identity: str

    def as_record(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "before_aggregate_trade_id": self.before.aggregate_trade_id,
            "after_aggregate_trade_id": self.after.aggregate_trade_id,
            "before_last_trade_id": self.before.last_trade_id,
            "after_first_trade_id": self.after.first_trade_id,
            "official_sources": list(self.official_sources),
            "proof_identity": self.proof_identity,
        }


def prove_no_trade_interval(
    *,
    start_ms: int,
    end_ms: int,
    before: AggTrade,
    after: AggTrade,
    events_inside: Iterable[AggTrade],
    rest_kline_present: bool,
    daily_kline_present: bool,
    archives_complete: bool,
    official_sources: Iterable[str],
) -> NoTradeProof:
    if start_ms % ONE_MINUTE_MS or end_ms % ONE_MINUTE_MS or start_ms >= end_ms:
        raise ProvenanceError("DATA_TIMESTAMP_INVALID", "no-trade boundaries are invalid")
    if rest_kline_present or daily_kline_present:
        raise ProvenanceError("SOURCE_CONFLICT", "kline exists inside claimed no-trade interval")
    if tuple(events_inside):
        raise ProvenanceError("SOURCE_CONFLICT", "official trade exists inside no-trade interval")
    if not archives_complete:
        raise ProvenanceError("SOURCE_INCOMPLETE", "source or checksum evidence is incomplete")
    if before.symbol != after.symbol or before.timestamp_ms >= start_ms or after.timestamp_ms < end_ms:
        raise ProvenanceError("UNRESOLVED_GAP", "adjacent event boundaries do not bracket interval")
    if after.aggregate_trade_id != before.aggregate_trade_id + 1:
        raise ProvenanceError("SOURCE_INCOMPLETE", "aggregate IDs are not continuous across interval")
    if after.first_trade_id != before.last_trade_id + 1:
        raise ProvenanceError("SOURCE_INCOMPLETE", "underlying trade IDs are not continuous")
    sources = tuple(sorted(set(official_sources)))
    if not sources:
        raise ProvenanceError("SOURCE_INCOMPLETE", "no official source identity for proof")
    material = {
        "symbol": before.symbol,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "before": before.material_tuple(),
        "after": after.material_tuple(),
        "official_sources": sources,
    }
    return NoTradeProof(
        symbol=before.symbol,
        start_ms=start_ms,
        end_ms=end_ms,
        before=before,
        after=after,
        official_sources=sources,
        proof_identity=sha256_bytes(canonical_json_bytes(material)),
    )


@dataclass(frozen=True)
class MinuteDecision:
    open_time_ms: int
    disposition: CoverageDisposition
    canonical_source: str | None
    canonical_identity: str | None
    reason: str
    superseded_observations: tuple[str, ...]
    conflicts: tuple[str, ...]
    no_trade_proof_identity: str | None = None

    @property
    def blocking(self) -> bool:
        return self.disposition in {
            CoverageDisposition.SOURCE_CONFLICT,
            CoverageDisposition.SOURCE_INCOMPLETE,
            CoverageDisposition.UNRESOLVED_GAP,
        }

    def as_record(self) -> dict[str, Any]:
        return {
            "open_time_ms": self.open_time_ms,
            "disposition": self.disposition.value,
            "canonical_source": self.canonical_source,
            "canonical_identity": self.canonical_identity,
            "reason": self.reason,
            "superseded_observations": list(self.superseded_observations),
            "conflicts": list(self.conflicts),
            "no_trade_proof_identity": self.no_trade_proof_identity,
            "blocking": self.blocking,
        }


def _observation_identity(item: KlineObservation) -> str:
    return sha256_bytes(canonical_json_bytes(item.as_record()))


def reconcile_spot_minute(
    *,
    minute_start_ms: int,
    rest: KlineObservation | None,
    daily: KlineObservation | None,
    monthly: KlineObservation | None,
    derived: DerivedSpotKline | None,
    no_trade_proof: NoTradeProof | None,
    independent_trade_derivation_count: int = 0,
) -> MinuteDecision:
    observations = tuple(item for item in (rest, daily, monthly) if item is not None)
    if any(item.open_time_ms != minute_start_ms for item in observations):
        raise ProvenanceError("DATA_TIMESTAMP_INVALID", "observation belongs to a different minute")
    invalid = tuple(item for item in observations if item.invalid_reasons)
    valid = tuple(item for item in observations if not item.invalid_reasons)
    if derived is not None and derived.open_time_ms != minute_start_ms:
        raise ProvenanceError("DATA_TIMESTAMP_INVALID", "derived bar belongs to a different minute")

    if not observations and derived is None:
        if no_trade_proof is not None:
            return MinuteDecision(
                open_time_ms=minute_start_ms,
                disposition=CoverageDisposition.VERIFIED_NO_TRADE_INTERVAL,
                canonical_source=None,
                canonical_identity=None,
                reason="REST_AND_DAILY_ABSENT_COMPLETE_TRADE_ID_CONTINUITY",
                superseded_observations=(),
                conflicts=(),
                no_trade_proof_identity=no_trade_proof.proof_identity,
            )
        return MinuteDecision(
            open_time_ms=minute_start_ms,
            disposition=CoverageDisposition.UNRESOLVED_GAP,
            canonical_source=None,
            canonical_identity=None,
            reason="NO_ACCEPTED_BAR_AND_NO_COMPLETE_NO_TRADE_PROOF",
            superseded_observations=(),
            conflicts=(),
        )

    if monthly is not None and rest is None and daily is None and derived is None:
        monthly_id = _observation_identity(monthly)
        if no_trade_proof is not None:
            return MinuteDecision(
                open_time_ms=minute_start_ms,
                disposition=CoverageDisposition.VERIFIED_NO_TRADE_INTERVAL,
                canonical_source=None,
                canonical_identity=None,
                reason="MONTHLY_ONLY_ROW_CONTRADICTED_BY_COMPLETE_NO_TRADE_PROOF",
                superseded_observations=(monthly_id,),
                conflicts=("SOURCE_CONFLICT_SUPERSEDED_OBSERVATION",),
                no_trade_proof_identity=no_trade_proof.proof_identity,
            )
        return MinuteDecision(
            open_time_ms=minute_start_ms,
            disposition=CoverageDisposition.SOURCE_CONFLICT,
            canonical_source=None,
            canonical_identity=None,
            reason="MONTHLY_ONLY_ROW_HAS_NO_INDEPENDENT_OFFICIAL_SUPPORT",
            superseded_observations=(),
            conflicts=(monthly_id,),
        )

    if invalid:
        invalid_ids = tuple(_observation_identity(item) for item in invalid)
        only_close_time_invalid = all(item.invalid_reasons == ("INVALID_CLOSE_TIME",) for item in invalid)
        if (
            derived is not None
            and only_close_time_invalid
            and independent_trade_derivation_count >= 2
            and all(derived.matches(item, include_close_time=False) for item in invalid)
        ):
            differing_valid = tuple(item for item in valid if not derived.matches(item))
            return MinuteDecision(
                open_time_ms=minute_start_ms,
                disposition=CoverageDisposition.DERIVED_FROM_OFFICIAL_TRADES,
                canonical_source="OFFICIAL_AGG_TRADES",
                canonical_identity=derived.derivation_identity,
                reason="INVALID_KLINE_CLOSE_TIME_SUPERSEDED_BY_TWO_COMPLETE_OFFICIAL_TRADE_OBSERVATIONS",
                superseded_observations=invalid_ids
                + tuple(_observation_identity(item) for item in differing_valid),
                conflicts=(
                    "INVALID_CLOSE_TIME_SUPERSEDED_OBSERVATION",
                    *(("SOURCE_CONFLICT_SUPERSEDED_OBSERVATION",) if differing_valid else ()),
                ),
            )
        return MinuteDecision(
            open_time_ms=minute_start_ms,
            disposition=CoverageDisposition.SOURCE_CONFLICT,
            canonical_source=None,
            canonical_identity=None,
            reason="INVALID_OFFICIAL_KLINE_NOT_DECISIVELY_RESOLVED",
            superseded_observations=(),
            conflicts=invalid_ids,
        )

    if rest is not None and daily is not None and rest.semantically_equals(daily):
        rest_id = _observation_identity(rest)
        if derived is not None and not derived.matches(rest):
            return MinuteDecision(
                open_time_ms=minute_start_ms,
                disposition=CoverageDisposition.SOURCE_CONFLICT,
                canonical_source=None,
                canonical_identity=None,
                reason="REST_DAILY_CONTRADICT_COMPLETE_OFFICIAL_TRADES",
                superseded_observations=(),
                conflicts=(rest_id, derived.derivation_identity),
            )
        if monthly is not None and not rest.semantically_equals(monthly):
            monthly_id = _observation_identity(monthly)
            if derived is not None and derived.matches(rest):
                return MinuteDecision(
                    open_time_ms=minute_start_ms,
                    disposition=CoverageDisposition.REAL_OFFICIAL_BAR,
                    canonical_source="REST_DAILY_AGGTRADE_CONSENSUS",
                    canonical_identity=rest_id,
                    reason="MONTHLY_CONFLICT_SUPERSEDED_BY_THREE_WAY_OFFICIAL_CONSENSUS",
                    superseded_observations=(monthly_id,),
                    conflicts=("SOURCE_CONFLICT_SUPERSEDED_OBSERVATION",),
                )
            return MinuteDecision(
                open_time_ms=minute_start_ms,
                disposition=CoverageDisposition.SOURCE_CONFLICT,
                canonical_source=None,
                canonical_identity=None,
                reason="MONTHLY_CONFLICT_LACKS_EVENT_LEVEL_ARBITRATION",
                superseded_observations=(),
                conflicts=(rest_id, monthly_id),
            )
        return MinuteDecision(
            open_time_ms=minute_start_ms,
            disposition=CoverageDisposition.REAL_OFFICIAL_BAR,
            canonical_source="REST_DAILY" if derived is None else "REST_DAILY_AGGTRADE_CONSENSUS",
            canonical_identity=rest_id,
            reason="INDEPENDENT_OFFICIAL_KLINE_AGREEMENT",
            superseded_observations=(),
            conflicts=(),
        )

    if derived is not None:
        matching = tuple(item for item in valid if derived.matches(item))
        differing = tuple(item for item in valid if not derived.matches(item))
        if len(matching) >= 1 and independent_trade_derivation_count >= 1:
            return MinuteDecision(
                open_time_ms=minute_start_ms,
                disposition=CoverageDisposition.DERIVED_FROM_OFFICIAL_TRADES,
                canonical_source="OFFICIAL_AGG_TRADES_AND_MATCHING_KLINE",
                canonical_identity=derived.derivation_identity,
                reason="EVENT_LEVEL_EVIDENCE_AND_INDEPENDENT_KLINE_MATCH",
                superseded_observations=tuple(_observation_identity(item) for item in differing),
                conflicts=("SOURCE_CONFLICT_SUPERSEDED_OBSERVATION",) if differing else (),
            )
        if not observations and independent_trade_derivation_count >= 2:
            return MinuteDecision(
                open_time_ms=minute_start_ms,
                disposition=CoverageDisposition.DERIVED_FROM_OFFICIAL_TRADES,
                canonical_source="TWO_COMPLETE_OFFICIAL_AGGTRADE_OBJECTS",
                canonical_identity=derived.derivation_identity,
                reason="NO_KLINE_AND_TWO_INDEPENDENT_COMPLETE_TRADE_OBSERVATIONS",
                superseded_observations=(),
                conflicts=(),
            )

    identities = tuple(_observation_identity(item) for item in observations)
    return MinuteDecision(
        open_time_ms=minute_start_ms,
        disposition=CoverageDisposition.SOURCE_CONFLICT,
        canonical_source=None,
        canonical_identity=None,
        reason="NO_DECISIVE_TWO_SOURCE_OFFICIAL_CONSENSUS",
        superseded_observations=(),
        conflicts=identities,
    )


def reconcile_three_way_exact(
    *,
    rest: KlineObservation | None,
    daily: KlineObservation | None,
    monthly: KlineObservation | None,
) -> tuple[bool, str]:
    observations = tuple(item for item in (rest, daily, monthly) if item is not None)
    if len(observations) != 3:
        return False, "MISSING_OBSERVATION"
    if any(item.invalid_reasons for item in observations):
        return False, "INVALID_OBSERVATION"
    if rest is not None and daily is not None and monthly is not None:
        if rest.semantically_equals(daily) and rest.semantically_equals(monthly):
            return True, "REST_DAILY_MONTHLY_EXACT_AGREEMENT"
    return False, "SOURCE_CONFLICT"


def reconcile_required_mark_roles(
    *,
    rest_monthly_valid: bool,
    daily_archive_available: bool,
    daily_row_present: bool,
    daily_row_valid: bool,
) -> tuple[bool, str]:
    """Reconcile original Binance Mark bars without a price-role fallback.

    A missing redundant Daily packaging route is non-blocking only when the
    REST and Monthly representations are both complete and exactly agree.
    A present but invalid Daily row remains a source conflict; it is never
    silently treated as unavailable.
    """

    if not rest_monthly_valid:
        return False, "SOURCE_CONFLICT_MARK_OBSERVATIONS_NOT_EXACT"
    if not daily_archive_available or not daily_row_present:
        return True, "REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE"
    if not daily_row_valid:
        return False, "SOURCE_CONFLICT_MARK_OBSERVATIONS_NOT_EXACT"
    return True, "REST_DAILY_MONTHLY_MARK_EXACT_AGREEMENT"


def verify_checksum(payload_path: Path, checksum_payload: bytes, *, exact_filename: str) -> str:
    try:
        text = checksum_payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ProvenanceError("DATA_HASH_MISMATCH", "publisher checksum is not ASCII") from exc
    match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", text)
    if match is None or match.group(2) != exact_filename:
        raise ProvenanceError("DATA_HASH_MISMATCH", "publisher checksum format or filename mismatch")
    digest = hashlib.sha256()
    with Path(payload_path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != match.group(1):
        raise ProvenanceError(
            "DATA_HASH_MISMATCH",
            "publisher checksum mismatch",
            evidence={"publisher": match.group(1), "actual": actual},
        )
    return actual


def compare_aggtrade_observations(
    first: Iterable[AggTrade],
    second: Iterable[AggTrade],
) -> tuple[bool, str]:
    left = tuple(item.material_tuple() for item in first)
    right = tuple(item.material_tuple() for item in second)
    if left == right:
        return True, sha256_bytes(canonical_json_bytes(left))
    return False, sha256_bytes(canonical_json_bytes({"first": left, "second": right}))


__all__ = [
    "AggTrade",
    "AggTradeSource",
    "CoverageDisposition",
    "DerivedSpotKline",
    "HttpObservation",
    "HttpRawStore",
    "KlineObservation",
    "KlineSource",
    "MinuteDecision",
    "NoTradeProof",
    "ONE_MINUTE_MS",
    "ProvenanceError",
    "canonical_json_bytes",
    "compare_aggtrade_observations",
    "derive_spot_kline",
    "iter_aggtrade_archive",
    "iter_kline_archive",
    "parse_aggtrade_rest_page",
    "parse_kline_rest_page",
    "prove_no_trade_interval",
    "reconcile_spot_minute",
    "reconcile_required_mark_roles",
    "reconcile_three_way_exact",
    "sha256_bytes",
    "utc_now_text",
    "validate_aggtrade_sequence",
    "validate_official_url",
    "verify_checksum",
]
