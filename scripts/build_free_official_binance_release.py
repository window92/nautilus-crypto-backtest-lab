#!/usr/bin/env python3
"""Build one immutable free-official Binance DuckDB and Nautilus catalog.

The builder is deliberately offline.  It consumes only the already preserved
Binance response/archive bytes, verifies every binding before parsing, writes a
fresh DuckDB transactionally, checkpoints it, closes it, and reopens it
read-only.  DuckDB is only a derived validation/query store; no financial
engine behavior is implemented here.
"""

from __future__ import annotations

import argparse
import csv
import gc
import hashlib
import importlib.util
import io
import json
import os
import re
import stat
import sys
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import duckdb  # noqa: E402
from crypto_lab.config import MarketProfile  # noqa: E402
from crypto_lab.data import CoverageDisposition  # noqa: E402
from crypto_lab.data import DatasetRawInventory  # noqa: E402
from crypto_lab.data import FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR  # noqa: E402
from crypto_lab.data import FULL_RAW_INVENTORY_NORMALIZER_VERSION  # noqa: E402
from crypto_lab.data import FundingEvent  # noqa: E402
from crypto_lab.data import MinuteDisposition  # noqa: E402
from crypto_lab.data import NormalizedBar  # noqa: E402
from crypto_lab.data import PublisherChecksumBinding  # noqa: E402
from crypto_lab.data import RawInventoryObject  # noqa: E402
from crypto_lab.data import RawInventoryOrigin  # noqa: E402
from crypto_lab.data import SourceObjectBinding  # noqa: E402
from crypto_lab.data import SourceRole  # noqa: E402
from crypto_lab.data import TimeRange  # noqa: E402
from crypto_lab.data import bind_lossless_instrument_representation  # noqa: E402
from crypto_lab.data import build_dataset_release  # noqa: E402
from crypto_lab.data import build_nautilus_catalog  # noqa: E402
from crypto_lab.data import minute_coverage_identity  # noqa: E402
from crypto_lab.data import parse_spot_instrument_metadata  # noqa: E402
from crypto_lab.data import parse_usdm_instrument_metadata  # noqa: E402
from crypto_lab.data import prove_funding_schedule_from_official_objects  # noqa: E402
from crypto_lab.hashing import canonical_json_bytes  # noqa: E402
from crypto_lab.hashing import canonical_sha256  # noqa: E402
from crypto_lab.data_acceptance import market_state_acceptance_identity  # noqa: E402
from crypto_lab.data_acceptance import qualify_executable_market_state  # noqa: E402


OLD_RAW = ROOT / "data/raw/data-provenance-duckdb-001"
NEW_RAW = ROOT / "data/raw/free-official-binance-data-duckdb-001"
OLD_INDEX_PATH = OLD_RAW / "acquisition-index.json"
NEW_INDEX_PATH = NEW_RAW / "phase-a-acquisition.json"
ANALYSIS_PATH = NEW_RAW / "phase-a-analysis.json"
POST_ADOPTION_PATH = NEW_RAW / "post-adoption-acquisition.json"
ANALYZER_PATH = (
    ROOT
    / "evidence/repair/free-official-binance-data-duckdb-001/tools/analyze_phase_a.py"
)
SCHEMA_PATH = ROOT / "schemas/free_official_binance_duckdb.sql"
DATA_TOOL_LOCK_PATH = ROOT / "data-tool.lock.json"
OWNER_ADOPTION_PATH = (
    ROOT / "evidence/repair/free-official-binance-data-duckdb-001/owner-adoption.json"
)
RAW_INVENTORY_PATH = (
    ROOT / "evidence/repair/free-official-binance-data-duckdb-001/raw-object-inventory.json"
)

START_MS = 1_609_459_200_000
END_MS = 1_627_776_000_000
JULY_START_MS = 1_625_097_600_000
ONE_MINUTE_MS = 60_000
ONE_MINUTE_NS = 60_000_000_000
EXPECTED_MINUTES = (END_MS - START_MS) // ONE_MINUTE_MS
EXPECTED_ANALYSIS_IDENTITY = "bf7c4d476702a6438e2940d85548943ca1b2b926f74ba64380e20bd0490c654d"
EXPECTED_ACQUISITION_IDENTITY = "6031d1f37a7e2687ba07988c6d2c9c74d241da368fd3baa4bfd5ffd31f1d8b40"
EXPECTED_SSOT_IDENTITY = "ab78a388ee6727cb5409504e8e63e2543ad4827ed2cb3c2a2cb7489d66532947"
EXPECTED_DUCKDB_VERSION = "1.4.5"
SEMANTIC_EXPORT_CONTRACT = "DUCKDB_1_4_5_SORTED_CSV_HEADER_LF_EXACT_TYPES_V1"
SPOT_PROFILE = MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
PERP_PROFILE = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
SPOT_INSTRUMENT = "BTCUSDT.BINANCE"
PERP_INSTRUMENT = "BTCUSDT-PERP.BINANCE"
FEE_RATE = Decimal("0.001")
FEE_BASIS = "SSOT Appendix A qualification-only observable estimated fee"
SPOT_METADATA_URL = "https://data-api.binance.vision/api/v3/exchangeInfo?symbol=BTCUSDT"
SPOT_HISTORICAL_GRID_URL = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
    "?articleCode=6925d618ab6b47e2936cc4614eaad64b"
)
SPOT_HISTORICAL_GRID_SHA256 = "89e0fb77408dde49f342bfb7929f7adb08af6c73e26eaf203143641ede99ea9a"
SPOT_HISTORICAL_GRID_SIZE = 1_844_272
SPOT_HISTORICAL_GRID_CAPTURED_AT = "2026-08-23T20:08:26Z"
SPOT_HISTORICAL_GRID_RAW_PATH = (
    ROOT
    / "data/raw/instrument-representation-funding-checker-001/objects/sha256/89"
    / f"{SPOT_HISTORICAL_GRID_SHA256}.bin"
)
SPOT_HISTORICAL_GRID_FILENAME = "binance-spot-btcusdt-step-size-2021-08-26.json"
HISTORICAL_GRID_URL = (
    "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query"
    "?articleCode=81e6795b0bae49828cbd52479094a987"
)
HISTORICAL_GRID_SHA256 = "ca85fd9286601514fdb00610aef15f7ee43b5e0657c865ad18e97c0763e8b5d1"
HISTORICAL_GRID_SIZE = 17_790
HISTORICAL_GRID_CAPTURED_AT = "2026-08-23T19:35:20Z"
HISTORICAL_GRID_RAW_PATH = (
    ROOT
    / "data/raw/instrument-representation-funding-checker-001/objects/sha256/ca"
    / f"{HISTORICAL_GRID_SHA256}.bin"
)
HISTORICAL_GRID_FILENAME = "binance-usdm-btcusdt-tick-size-2022-02-15.json"
SUPERSEDED_SPOT_RELEASE = "95e04adb076be05eba0a970aa0978f1a4d1f41ad3caf04e9cd5859dd408ac099"
SUPERSEDED_PERP_RELEASE = "9c8a5f679f38852119d1d2054b0711965f0a6d89d5dd0e0ebedaa8d8df66b503"
SPOT_ACCEPTANCE_CONFIG = (
    ROOT / "runs/owner-smoke-002-spot-run-retry-001-8a09aee98d9f/lab_run_config.json"
)
PERP_ACCEPTANCE_CONFIG = (
    ROOT / "runs/owner-smoke-002-perpetual-run-11882dd8dabb/lab_run_config.json"
)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
CSV_NULL = chr(92) + "N"


def canonical_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decimal_text(value: Any) -> str:
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if not number.is_finite():
        raise ValueError("non-finite Decimal")
    if number == 0:
        return "0"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def utc_text(timestamp_ms: int) -> str:
    return datetime_from_ms(timestamp_ms).isoformat().replace("+00:00", "Z")


def datetime_from_ms(timestamp_ms: int) -> datetime:
    return EPOCH + timedelta(milliseconds=timestamp_ms)


def datetime_ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise RuntimeError("timestamp must be exact UTC")
    delta = value - EPOCH
    return (
        delta.days * 86_400_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def iso8601_ns(value: str) -> int:
    return datetime_ns(datetime.fromisoformat(value.replace("Z", "+00:00")))


def deterministic_timestamp() -> str:
    adoption = json.loads(OWNER_ADOPTION_PATH.read_text(encoding="utf-8"))
    return str(adoption["adopted_at_utc"])


def configure_database(path: Path, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(
        str(path),
        read_only=read_only,
        config={
            "allow_unsigned_extensions": "false",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        },
    )


def load_analyzer() -> Any:
    name = "free_official_phase_a_analyzer"
    spec = importlib.util.spec_from_file_location(name, ANALYZER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen Phase-A analyzer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def csv_writer(path: Path, header: tuple[str, ...]) -> tuple[Any, csv.writer]:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("w", encoding="utf-8", newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(header)
    return stream, writer


def close_csv(stream: Any) -> None:
    stream.flush()
    os.fsync(stream.fileno())
    stream.close()


def copy_csv(connection: duckdb.DuckDBPyConnection, table: str, path: Path) -> None:
    escaped = str(path.resolve()).replace("'", "''")
    connection.execute(
        f"COPY {table} FROM '{escaped}' (FORMAT CSV, HEADER TRUE, NULLSTR '{CSV_NULL}', DELIMITER ',', QUOTE '\"', ESCAPE '\"')",
    )


def null(value: Any) -> Any:
    return CSV_NULL if value is None else value


@dataclass(frozen=True)
class RawBinding:
    sha256: str
    byte_size: int
    local_path: Path


class SourceRegistry:
    """Verify and normalize the two immutable official raw stores."""

    def __init__(self) -> None:
        self.raw: dict[str, RawBinding] = {}
        self.observations: list[dict[str, Any]] = []
        self.by_sha: dict[str, list[dict[str, Any]]] = {}

    def load(self) -> None:
        inventory = json.loads(RAW_INVENTORY_PATH.read_text(encoding="utf-8"))
        allowed_hosts = set(inventory["authorized_hosts"])
        allowed_hashes = {
            str(item["raw_object_sha256"])
            for item in inventory["raw_objects"]
        }
        allowed_observation_ids = {
            str(observation_id)
            for item in inventory["raw_objects"]
            for observation_id in item["observation_ids"]
        }
        loaded_observation_ids: set[str] = set()
        for root in (OLD_RAW, NEW_RAW):
            for metadata_path in sorted((root / "http-observations").glob("*.json")):
                item = json.loads(metadata_path.read_text(encoding="utf-8"))
                observation_id = str(item["observation_id"])
                if observation_id not in allowed_observation_ids:
                    continue
                digest = str(item["raw_object_sha256"])
                if digest not in allowed_hashes:
                    raise RuntimeError("authorized observation has an unauthorized raw hash")
                host = urlsplit(str(item.get("exact_url", ""))).hostname
                if host not in allowed_hosts:
                    raise RuntimeError(f"unauthorized source host entered registry: {host}")
                if observation_id in loaded_observation_ids:
                    raise RuntimeError(f"duplicate authorized observation identity: {observation_id}")
                loaded_observation_ids.add(observation_id)
                relative = item.get("local_object_path") or item.get("raw_object_path")
                if relative is None:
                    path = root / "objects/sha256" / digest[:2] / f"{digest}.bin"
                else:
                    path = Path(str(relative))
                    if not path.is_absolute():
                        path = ROOT / path
                size = int(item.get("byte_length", item.get("byte_size")))
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or path.stat().st_size != size
                    or hash_file(path) != digest
                ):
                    raise RuntimeError(f"raw observation binding failed: {metadata_path}")
                prior = self.raw.get(digest)
                binding = RawBinding(digest, size, path)
                if prior is not None and (prior.byte_size != size or hash_file(prior.local_path) != digest):
                    raise RuntimeError(f"content-address collision: {digest}")
                self.raw[digest] = binding
                normalized = self._normalize_observation(item)
                self.observations.append(normalized)
                self.by_sha.setdefault(digest, []).append(normalized)
        if loaded_observation_ids != allowed_observation_ids:
            missing = sorted(allowed_observation_ids - loaded_observation_ids)
            raise RuntimeError(f"authorized raw inventory observations are missing: {missing[:5]}")
        if set(self.raw) != allowed_hashes:
            missing = sorted(allowed_hashes - set(self.raw))
            raise RuntimeError(f"authorized raw inventory objects are missing: {missing[:5]}")
        self.observations.sort(key=lambda item: item["observation_id"])
        for values in self.by_sha.values():
            values.sort(key=lambda item: (item["exact_locator"], item["observation_id"]))

    @staticmethod
    def _normalize_observation(item: dict[str, Any]) -> dict[str, Any]:
        captured = item.get("capture_completed_at_utc") or item.get("retrieval_completed_at_utc")
        return {
            "observation_id": item["observation_id"],
            "raw_object_sha256": item["raw_object_sha256"],
            "exact_locator": item.get("exact_url", ""),
            "exact_query_json": canonical_text(
                item.get("exact_query_parameters", item.get("exact_query", "")),
            ),
            "http_status": int(item.get("status_code", item.get("http_status"))),
            "response_headers_json": canonical_text(item.get("response_headers", {})),
            "captured_at_utc": captured,
            "source_role": item.get("source_role", "UNSPECIFIED_OFFICIAL_OBSERVATION"),
            "instrument": item.get("instrument", "BTCUSDT"),
            "requested_interval": item.get("interval", "NOT_APPLICABLE"),
            "requested_start_ms": item.get("requested_start_ms"),
            "requested_end_ms": item.get("requested_end_ms"),
            "pagination_identity": item.get("pagination_position", "NOT_APPLICABLE"),
        }

    def locator(self, digest: str) -> str:
        values = self.by_sha.get(digest)
        if not values:
            raise RuntimeError(f"no raw observation locator for {digest}")
        return str(values[0]["exact_locator"])

    def observation(self, digest: str) -> dict[str, Any]:
        values = self.by_sha.get(digest)
        if not values:
            raise RuntimeError(f"no raw observation for {digest}")
        return values[0]

    def bytes(self, digest: str) -> bytes:
        binding = self.raw[digest]
        if binding.local_path.is_symlink() or not binding.local_path.is_file():
            raise RuntimeError(f"raw object path changed after registry verification: {digest}")
        payload = binding.local_path.read_bytes()
        if len(payload) != binding.byte_size or sha256_bytes(payload) != digest:
            raise RuntimeError(f"raw object changed after registry verification: {digest}")
        return payload


def register_historical_order_grid_authority(registry: SourceRegistry) -> dict[str, Any]:
    """Bind the preserved official Binance tick-size announcement.

    The current exchangeInfo object remains authoritative for the quantity
    filters.  This separate official observation proves the historical
    BTCUSDT price tick without treating Futures ``pricePrecision`` as a tick.
    """

    if (
        HISTORICAL_GRID_RAW_PATH.is_symlink()
        or not HISTORICAL_GRID_RAW_PATH.is_file()
        or HISTORICAL_GRID_RAW_PATH.stat().st_size != HISTORICAL_GRID_SIZE
        or hash_file(HISTORICAL_GRID_RAW_PATH) != HISTORICAL_GRID_SHA256
    ):
        raise RuntimeError("official historical order-grid object identity mismatch")
    payload = HISTORICAL_GRID_RAW_PATH.read_bytes()
    parsed = json.loads(payload.decode("utf-8"))
    if parsed.get("success") is not True or parsed.get("code") != "000000":
        raise RuntimeError("official Binance CMS response is not successful")
    article = parsed.get("data")
    if not isinstance(article, dict) or article.get("code") != "81e6795b0bae49828cbd52479094a987":
        raise RuntimeError("official Binance CMS article identity mismatch")
    if article.get("title") != "Updates on the Tick Size for BTC USDⓈ-M Perpetual Futures Contracts":
        raise RuntimeError("official Binance CMS article title mismatch")
    body = str(article.get("body", ""))
    text_nodes = [
        json.loads(f'"{item}"')
        for item in re.findall(r'"text":"((?:[^"\\]|\\.)*)"', body)
    ]
    btc_rows = [
        text_nodes[index : index + 3]
        for index, value in enumerate(text_nodes)
        if value == "BTCUSDT"
    ]
    if (
        not any("2022-02-15 03:30 AM (UTC)" in item for item in text_nodes)
        or "Before" not in text_nodes
        or "After" not in text_nodes
        or ["BTCUSDT", "0.01", "0.1"] not in btc_rows
    ):
        raise RuntimeError("official Binance BTCUSDT before/after tick table is unresolved")
    observation_id = canonical_sha256(
        {
            "raw_object_sha256": HISTORICAL_GRID_SHA256,
            "exact_locator": HISTORICAL_GRID_URL,
            "captured_at_utc": HISTORICAL_GRID_CAPTURED_AT,
            "source_role": SourceRole.USDM_PERPETUAL_HISTORICAL_ORDER_GRID.value,
        },
    )
    observation = {
        "observation_id": observation_id,
        "raw_object_sha256": HISTORICAL_GRID_SHA256,
        "exact_locator": HISTORICAL_GRID_URL,
        "exact_query_json": canonical_text(
            {"articleCode": "81e6795b0bae49828cbd52479094a987"},
        ),
        "http_status": 200,
        "response_headers_json": canonical_text(
            {
                "content-length": str(HISTORICAL_GRID_SIZE),
                "content-type": "application/json;charset=UTF-8",
                "date": "Sun, 23 Aug 2026 19:35:20 GMT",
                "etag": 'W/"0211f60b56f1235d97d0d2b855040e1e9"',
            },
        ),
        "captured_at_utc": HISTORICAL_GRID_CAPTURED_AT,
        "source_role": SourceRole.USDM_PERPETUAL_HISTORICAL_ORDER_GRID.value,
        "instrument": "BTCUSDT",
        "requested_interval": "NOT_APPLICABLE",
        "requested_start_ms": None,
        "requested_end_ms": None,
        "pagination_identity": "NOT_APPLICABLE",
    }
    if HISTORICAL_GRID_SHA256 in registry.raw:
        raise RuntimeError("historical order-grid raw object was registered twice")
    registry.raw[HISTORICAL_GRID_SHA256] = RawBinding(
        HISTORICAL_GRID_SHA256,
        HISTORICAL_GRID_SIZE,
        HISTORICAL_GRID_RAW_PATH,
    )
    registry.observations.append(observation)
    registry.observations.sort(key=lambda item: item["observation_id"])
    registry.by_sha[HISTORICAL_GRID_SHA256] = [observation]
    return observation


def register_spot_historical_order_grid_authority(registry: SourceRegistry) -> dict[str, Any]:
    """Bind Binance's official before/after BTCUSDT Spot step-size table."""

    if (
        SPOT_HISTORICAL_GRID_RAW_PATH.is_symlink()
        or not SPOT_HISTORICAL_GRID_RAW_PATH.is_file()
        or SPOT_HISTORICAL_GRID_RAW_PATH.stat().st_size != SPOT_HISTORICAL_GRID_SIZE
        or hash_file(SPOT_HISTORICAL_GRID_RAW_PATH) != SPOT_HISTORICAL_GRID_SHA256
    ):
        raise RuntimeError("official Spot historical order-grid object identity mismatch")
    parsed = json.loads(SPOT_HISTORICAL_GRID_RAW_PATH.read_text(encoding="utf-8"))
    if parsed.get("success") is not True or parsed.get("code") != "000000":
        raise RuntimeError("official Binance Spot CMS response is not successful")
    article = parsed.get("data")
    if not isinstance(article, dict) or article.get("code") != "6925d618ab6b47e2936cc4614eaad64b":
        raise RuntimeError("official Binance Spot CMS article identity mismatch")
    if article.get("title") != "Updates on Tick Size and Step Size for Spot Trading Pairs":
        raise RuntimeError("official Binance Spot CMS article title mismatch")
    body = str(article.get("body", ""))
    text_nodes = [
        json.loads(f'"{item}"')
        for item in re.findall(r'"text":"((?:[^"\\]|\\.)*)"', body)
    ]
    btc_rows = [
        text_nodes[index : index + 3]
        for index, value in enumerate(text_nodes)
        if value == "BTCUSDT"
    ]
    if (
        not any("2021-08-26 06:00 AM (UTC)" in item for item in text_nodes)
        or "Step Size (Before)" not in text_nodes
        or "Updated Step Size" not in text_nodes
        or ["BTCUSDT", "0.000001", "0.00001"] not in btc_rows
    ):
        raise RuntimeError("official Binance BTCUSDT before/after Spot step table is unresolved")
    observation_id = canonical_sha256(
        {
            "raw_object_sha256": SPOT_HISTORICAL_GRID_SHA256,
            "exact_locator": SPOT_HISTORICAL_GRID_URL,
            "captured_at_utc": SPOT_HISTORICAL_GRID_CAPTURED_AT,
            "source_role": SourceRole.SPOT_HISTORICAL_ORDER_GRID.value,
        },
    )
    observation = {
        "observation_id": observation_id,
        "raw_object_sha256": SPOT_HISTORICAL_GRID_SHA256,
        "exact_locator": SPOT_HISTORICAL_GRID_URL,
        "exact_query_json": canonical_text(
            {"articleCode": "6925d618ab6b47e2936cc4614eaad64b"},
        ),
        "http_status": 200,
        "response_headers_json": canonical_text(
            {
                "capture_transport": "CURL_FAIL_ON_NON_2XX_BODY_PRESERVED_BEFORE_PARSE",
            },
        ),
        "captured_at_utc": SPOT_HISTORICAL_GRID_CAPTURED_AT,
        "source_role": SourceRole.SPOT_HISTORICAL_ORDER_GRID.value,
        "instrument": "BTCUSDT",
        "requested_interval": "NOT_APPLICABLE",
        "requested_start_ms": None,
        "requested_end_ms": None,
        "pagination_identity": "NOT_APPLICABLE",
    }
    if SPOT_HISTORICAL_GRID_SHA256 in registry.raw:
        raise RuntimeError("Spot historical order-grid raw object was registered twice")
    registry.raw[SPOT_HISTORICAL_GRID_SHA256] = RawBinding(
        SPOT_HISTORICAL_GRID_SHA256,
        SPOT_HISTORICAL_GRID_SIZE,
        SPOT_HISTORICAL_GRID_RAW_PATH,
    )
    registry.observations.append(observation)
    registry.observations.sort(key=lambda item: item["observation_id"])
    registry.by_sha[SPOT_HISTORICAL_GRID_SHA256] = [observation]
    return observation


SOURCE_OBSERVATION_HEADER = (
    "observation_id",
    "raw_object_sha256",
    "exact_locator",
    "exact_query_json",
    "http_status",
    "response_headers_json",
    "captured_at_utc",
    "source_role",
    "instrument",
    "requested_interval",
    "requested_start_ms",
    "requested_end_ms",
    "pagination_identity",
    "parsed_event_time_ms",
    "semantic_row_sha256",
    "original_row_json",
    "validation_status",
    "delivery_classification",
)


def source_observation_row(
    registry: SourceRegistry,
    *,
    source_sha256: str,
    source_role: str,
    event_time_ms: int,
    semantic_sha256: str,
    original_fields: tuple[str, ...],
    status: str = "PASS",
    classification: str | None = None,
) -> tuple[Any, ...]:
    raw = registry.observation(source_sha256)
    identity = canonical_sha256(
        {
            "source_raw_object_sha256": source_sha256,
            "source_role": source_role,
            "event_time_ms": event_time_ms,
            "semantic_row_sha256": semantic_sha256,
            "original_fields": original_fields,
        },
    )
    return (
        identity,
        source_sha256,
        raw["exact_locator"],
        raw["exact_query_json"],
        raw["http_status"],
        raw["response_headers_json"],
        raw["captured_at_utc"],
        source_role,
        raw["instrument"],
        raw["requested_interval"],
        null(raw["requested_start_ms"]),
        null(raw["requested_end_ms"]),
        raw["pagination_identity"],
        event_time_ms,
        semantic_sha256,
        canonical_text(list(original_fields)),
        status,
        null(classification),
    )


def raw_source_observation_row(item: dict[str, Any]) -> tuple[Any, ...]:
    status = "UNAVAILABLE" if item["http_status"] == 404 else "RAW_PRESERVED"
    classification = (
        "REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE"
        if item["http_status"] == 404 and "MARK" in item["source_role"]
        else None
    )
    return (
        item["observation_id"],
        item["raw_object_sha256"],
        item["exact_locator"],
        item["exact_query_json"],
        item["http_status"],
        item["response_headers_json"],
        item["captured_at_utc"],
        item["source_role"],
        item["instrument"],
        item["requested_interval"],
        item["requested_start_ms"],
        item["requested_end_ms"],
        item["pagination_identity"],
        None,
        None,
        None,
        status,
        classification,
    )


def material_identity(
    *,
    market_profile: str,
    instrument_id: str,
    disposition: str,
    material: tuple[Any, ...],
    source_sha256s: Iterable[str],
) -> str:
    return canonical_sha256(
        {
            "market_profile": market_profile,
            "instrument_id": instrument_id,
            "disposition": disposition,
            "material": list(material),
            "source_raw_object_sha256s": sorted(set(source_sha256s)),
        },
    )


def normalized_bar(
    *,
    source_role: SourceRole,
    instrument_id: str,
    material: tuple[Any, ...],
    canonical_identity: str,
) -> NormalizedBar:
    return NormalizedBar(
        source_role=source_role,
        instrument_id=instrument_id,
        interval_start_ns=int(material[0]) * 1_000_000,
        interval_end_exclusive_ns=(int(material[0]) + ONE_MINUTE_MS) * 1_000_000,
        available_at_ns=(int(material[0]) + ONE_MINUTE_MS) * 1_000_000,
        open=Decimal(str(material[1])),
        high=Decimal(str(material[2])),
        low=Decimal(str(material[3])),
        close=Decimal(str(material[4])),
        volume=Decimal(str(material[5])) if len(material) > 5 else Decimal("0"),
        source_row_number=0,
        source_row_sha256=canonical_identity,
    )


def execution_csv_row(
    *,
    canonical_identity: str,
    instrument_id: str,
    disposition: str,
    material: tuple[Any, ...],
    source_sha256s: Iterable[str],
) -> tuple[Any, ...]:
    sources = sorted(set(source_sha256s))
    return (
        canonical_identity,
        instrument_id,
        int(material[0]) * 1_000_000,
        (int(material[0]) + ONE_MINUTE_MS) * 1_000_000,
        (int(material[0]) + ONE_MINUTE_MS) * 1_000_000,
        disposition,
        str(material[1]), str(material[1]),
        str(material[2]), str(material[2]),
        str(material[3]), str(material[3]),
        str(material[4]), str(material[4]),
        str(material[5]), str(material[5]),
        str(material[7]), str(material[7]),
        int(material[8]),
        str(material[9]), str(material[9]),
        str(material[10]), str(material[10]),
        sources[0],
        canonical_text(sources),
    )


EXECUTION_HEADER = (
    "canonical_bar_identity", "instrument_id", "open_time_ns", "end_exclusive_ns",
    "available_at_ns", "disposition", "open_text", "open_value", "high_text", "high_value",
    "low_text", "low_value", "close_text", "close_value", "base_volume_text",
    "base_volume_value", "quote_volume_text", "quote_volume_value", "trade_count",
    "taker_buy_base_text", "taker_buy_base_value", "taker_buy_quote_text",
    "taker_buy_quote_value", "primary_source_sha256", "source_sha256s_json",
)

PERP_EXECUTION_HEADER = tuple(
    "volume_text" if item == "base_volume_text" else "volume_value" if item == "base_volume_value" else item
    for item in EXECUTION_HEADER
    if item != "disposition"
)

MARK_HEADER = (
    "canonical_bar_identity", "instrument_id", "open_time_ns", "end_exclusive_ns",
    "available_at_ns", "open_text", "open_value", "high_text", "high_value", "low_text",
    "low_value", "close_text", "close_value", "primary_source_sha256", "source_sha256s_json",
)

MINUTE_HEADER = (
    "market_profile", "instrument_id", "open_time_ns", "disposition",
    "canonical_bar_identity", "proof_identity", "source_reconciliation_identity", "reason", "blocking",
)

CONFLICT_HEADER = (
    "conflict_identity", "market_profile", "instrument_id", "open_time_ns", "status", "reason",
    "source_observation_ids_json", "resolution_identity",
)

NO_TRADE_HEADER = (
    "proof_identity", "instrument_id", "start_ns", "end_exclusive_ns", "before_trade_id",
    "after_trade_id", "before_aggregate_id", "after_aggregate_id", "raw_trade_source_sha256",
    "aggtrade_source_sha256", "proof_json",
)

AGGTRADE_HEADER = (
    "source_raw_object_sha256", "aggregate_trade_id", "source_role", "symbol", "row_number",
    "price_text", "price_value", "quantity_text", "quantity_value", "first_trade_id",
    "last_trade_id", "event_time_ms", "buyer_is_maker", "best_price_match",
)

FUNDING_HEADER = (
    "event_identity", "instrument_id", "funding_time_ns", "funding_interval_hours",
    "funding_rate_text", "funding_rate_value", "primary_source_sha256", "source_sha256s_json",
)


def parsed_observation_identity(row: tuple[Any, ...]) -> str:
    return str(row[0])


def write_kline_observations(
    writer: csv.writer,
    registry: SourceRegistry,
    rows: dict[int, Any],
    *,
    source_role: str,
    superseded: dict[int, set[str]],
    superseded_key: str | None = None,
    delivery_classification: dict[int, str] | None = None,
) -> dict[int, str]:
    identities: dict[int, str] = {}
    for minute in sorted(rows):
        item = rows[minute]
        is_superseded = (superseded_key or source_role) in superseded.get(minute, set())
        classification = (
            "SOURCE_CONFLICT_SUPERSEDED_OBSERVATION" if is_superseded else None
        )
        if delivery_classification is not None:
            classification = delivery_classification.get(minute, classification)
        record = source_observation_row(
            registry,
            source_sha256=item.source_sha256,
            source_role=source_role,
            event_time_ms=minute,
            semantic_sha256=item.semantic_sha256,
            original_fields=item.original_fields,
            status="SUPERSEDED" if is_superseded else "PASS",
            classification=classification,
        )
        writer.writerow(record)
        identities[minute] = parsed_observation_identity(record)
    return identities


def canonical_consensus(
    rows: dict[str, Any],
    *,
    minimum: int,
    required_semantic_sha256: str | None = None,
) -> tuple[Any, list[str]]:
    valid = {role: item for role, item in rows.items() if item is not None and not item.invalid_reasons}
    if required_semantic_sha256 is not None:
        accepted = {
            role: item for role, item in valid.items()
            if item.semantic_sha256 == required_semantic_sha256
        }
    else:
        counts = Counter(item.semantic_sha256 for item in valid.values())
        if not counts:
            raise RuntimeError("no valid official observation")
        winner, count = counts.most_common(1)[0]
        if list(counts.values()).count(count) != 1:
            raise RuntimeError("official observations have no unique semantic consensus")
        accepted = {role: item for role, item in valid.items() if item.semantic_sha256 == winner}
    if len(accepted) < minimum:
        raise RuntimeError(f"insufficient official consensus: {sorted(accepted)}")
    selected_role = sorted(accepted)[0]
    return accepted[selected_role], sorted(accepted)


def process_spot(
    *,
    analyzer: Any,
    old: dict[str, Any],
    new: dict[str, Any],
    analysis: dict[str, Any],
    registry: SourceRegistry,
    source_writer: csv.writer,
    execution_writer: csv.writer,
    minute_writer: csv.writer,
    conflict_writer: csv.writer,
    no_trade_writer: csv.writer,
    aggtrade_writer: csv.writer,
) -> dict[str, Any]:
    daily_pairs = analyzer.old_archive_pairs(old, "spot_execution", "daily", START_MS, JULY_START_MS)
    daily_pairs += analyzer.new_archive_pairs(new, "SPOT_DAILY_KLINES")
    monthly_pairs = analyzer.old_archive_pairs(old, "spot_execution", "monthly", START_MS, JULY_START_MS)
    monthly_pairs += analyzer.new_archive_pairs(new, "SPOT_MONTHLY_KLINES")
    daily, daily_summary = analyzer.build_archive_map(
        daily_pairs,
        source_role="SPOT_DAILY_KLINES",
        kind="execution",
        start_ms=START_MS,
        end_ms=END_MS,
    )
    monthly, monthly_summary = analyzer.build_archive_map(
        monthly_pairs,
        source_role="SPOT_MONTHLY_KLINES",
        kind="execution",
        start_ms=START_MS,
        end_ms=END_MS,
    )
    rest, rest_summary = analyzer.build_rest_map(
        new["spot_rest_pages"],
        source_role="SPOT_REST_KLINES_DATA_API",
        kind="execution",
        start_ms=START_MS,
        end_ms=END_MS,
    )

    anomaly_items = {
        int(item["open_time_ms"]): item
        for item in analysis["spot"]["anomaly_dispositions"]
    }
    superseded: dict[int, set[str]] = {}
    for minute, item in anomaly_items.items():
        roles = set(item.get("superseded_source_roles", ()))
        roles.update(item.get("excluded_zero_event_kline_roles", ()))
        superseded[minute] = roles
    observation_ids = {
        "REST": write_kline_observations(
            source_writer,
            registry,
            rest,
            source_role="SPOT_REST_KLINES_DATA_API",
            superseded=superseded,
            superseded_key="REST",
        ),
        "DAILY": write_kline_observations(
            source_writer,
            registry,
            daily,
            source_role="SPOT_DAILY_KLINES",
            superseded=superseded,
            superseded_key="DAILY",
        ),
        "MONTHLY": write_kline_observations(
            source_writer,
            registry,
            monthly,
            source_role="SPOT_MONTHLY_KLINES",
            superseded=superseded,
            superseded_key="MONTHLY",
        ),
    }

    agg_pairs = analyzer.select_daily_aggtrade_pairs(old, new)
    anomaly_days = sorted(
        {
            datetime_from_ms(minute).date()
            for minute in anomaly_items
        },
    )
    aggtrade_count = 0
    anomaly_agg_pairs: list[dict[str, Any]] = []
    for day in anomaly_days:
        pair = agg_pairs.get(day)
        if pair is None:
            raise RuntimeError(f"missing official aggTrade archive for anomaly day {day}")
        anomaly_agg_pairs.append(pair)
        analyzer.verify_pair(pair)
        source_sha = pair["archive"]["raw_object_sha256"]
        for event in analyzer.iter_aggtrade_archive(
            analyzer.archive_path(pair),
            source_kind=analyzer.AggTradeSource.SPOT_DAILY,
            source_sha256=source_sha,
            expected_member=analyzer.expected_member(pair),
        ):
            minute = event.timestamp_ms - event.timestamp_ms % ONE_MINUTE_MS
            if minute not in anomaly_items:
                continue
            aggtrade_writer.writerow(
                (
                    source_sha,
                    event.aggregate_trade_id,
                    event.source_kind.value,
                    event.symbol,
                    event.row_number,
                    event.price_text,
                    event.price_text,
                    event.quantity_text,
                    event.quantity_text,
                    event.first_trade_id,
                    event.last_trade_id,
                    event.timestamp_ms,
                    event.buyer_is_maker,
                    event.best_price_match,
                ),
            )
            aggtrade_count += 1

    bars: list[NormalizedBar] = []
    dispositions: list[MinuteDisposition] = []
    reconciliation_ids: list[str] = []
    counts: Counter[str] = Counter()
    conflict_count = 0
    no_trade_count = 0
    partial = analysis["spot"]["partial_2021_04_25_04_00"]["raw_trades"]
    for minute in range(START_MS, END_MS, ONE_MINUTE_MS):
        item = anomaly_items.get(minute)
        disposition = (
            item["disposition"] if item is not None else CoverageDisposition.REAL_OFFICIAL_BAR.value
        )
        rows = {"REST": rest.get(minute), "DAILY": daily.get(minute), "MONTHLY": monthly.get(minute)}
        evidence_material = {
            role: None if row is None else {
                "source_sha256": row.source_sha256,
                "semantic_sha256": row.semantic_sha256,
                "invalid_reasons": list(row.invalid_reasons),
            }
            for role, row in rows.items()
        }
        reason = item["reason"] if item is not None else "REST_DAILY_MONTHLY_EXACT_AGREEMENT"
        if disposition == CoverageDisposition.VERIFIED_NO_TRADE_INTERVAL.value:
            assert item is not None
            raw_proof = item["evidence"]["raw_trade_range_proof"]
            agg_before = item["evidence"]["before"]
            agg_after = item["evidence"]["after"]
            proof_material = {
                "instrument_id": SPOT_INSTRUMENT,
                "minute_start_ms": minute,
                "minute_end_exclusive_ms": minute + ONE_MINUTE_MS,
                "raw_trade_range_proof": raw_proof,
                "aggtrade_before": agg_before,
                "aggtrade_after": agg_after,
                "kline_observations": evidence_material,
            }
            proof_identity = canonical_sha256(proof_material)
            reconciliation_identity = canonical_sha256(
                {"disposition": disposition, "proof_identity": proof_identity, **proof_material},
            )
            no_trade_writer.writerow(
                (
                    proof_identity,
                    SPOT_INSTRUMENT,
                    minute * 1_000_000,
                    (minute + ONE_MINUTE_MS) * 1_000_000,
                    raw_proof["boundary_before"]["trade_id"],
                    raw_proof["boundary_after"]["trade_id"],
                    agg_before["aggregate_trade_id"],
                    agg_after["aggregate_trade_id"],
                    raw_proof["archive_sha256"],
                    item["evidence"]["event_source_sha256"],
                    canonical_text(proof_material),
                ),
            )
            minute_writer.writerow(
                (
                    SPOT_PROFILE.value,
                    SPOT_INSTRUMENT,
                    minute * 1_000_000,
                    disposition,
                    null(None),
                    proof_identity,
                    reconciliation_identity,
                    reason,
                    False,
                ),
            )
            dispositions.append(
                MinuteDisposition(
                    open_time_ns=minute * 1_000_000,
                    disposition=CoverageDisposition.VERIFIED_NO_TRADE_INTERVAL,
                    canonical_bar_identity="NOT_APPLICABLE",
                    proof_identity=proof_identity,
                    source_reconciliation_identity=reconciliation_identity,
                ),
            )
            no_trade_count += 1
            canonical_identity = None
        elif disposition in {
            CoverageDisposition.REAL_OFFICIAL_BAR.value,
            CoverageDisposition.DERIVED_FROM_OFFICIAL_TRADES.value,
        }:
            if disposition == CoverageDisposition.DERIVED_FROM_OFFICIAL_TRADES.value:
                if int(partial["derived_material"][0]) != minute:
                    raise RuntimeError("unexpected derived minute outside locked partial interval")
                material = tuple(partial["derived_material"])
                source_sha256s = [
                    partial["archive_sha256"],
                    item["evidence"]["event_source_sha256"],
                ]
            else:
                if item is None:
                    selected, accepted_roles = canonical_consensus(rows, minimum=3)
                else:
                    required = item.get("evidence", {}).get("derived_semantic_sha256")
                    selected, accepted_roles = canonical_consensus(
                        rows,
                        minimum=1 if required is not None else 2,
                        required_semantic_sha256=required,
                    )
                material = selected.material
                source_sha256s = [rows[role].source_sha256 for role in accepted_roles]
                event_source = None if item is None else item.get("evidence", {}).get("event_source_sha256")
                if event_source is not None:
                    source_sha256s.append(event_source)
            canonical_identity = material_identity(
                market_profile=SPOT_PROFILE.value,
                instrument_id=SPOT_INSTRUMENT,
                disposition=disposition,
                material=material,
                source_sha256s=source_sha256s,
            )
            reconciliation_identity = canonical_sha256(
                {
                    "minute_start_ms": minute,
                    "disposition": disposition,
                    "canonical_bar_identity": canonical_identity,
                    "official_observations": evidence_material,
                    "source_sha256s": sorted(set(source_sha256s)),
                    "reason": reason,
                },
            )
            execution_writer.writerow(
                execution_csv_row(
                    canonical_identity=canonical_identity,
                    instrument_id=SPOT_INSTRUMENT,
                    disposition=disposition,
                    material=material,
                    source_sha256s=source_sha256s,
                ),
            )
            bar = normalized_bar(
                source_role=SourceRole.SPOT_EXECUTION_1M,
                instrument_id=SPOT_INSTRUMENT,
                material=material,
                canonical_identity=canonical_identity,
            )
            bars.append(bar)
            minute_writer.writerow(
                (
                    SPOT_PROFILE.value,
                    SPOT_INSTRUMENT,
                    minute * 1_000_000,
                    disposition,
                    canonical_identity,
                    null(None),
                    reconciliation_identity,
                    reason,
                    False,
                ),
            )
            dispositions.append(
                MinuteDisposition(
                    open_time_ns=minute * 1_000_000,
                    disposition=CoverageDisposition(disposition),
                    canonical_bar_identity=canonical_identity,
                    proof_identity="NOT_APPLICABLE",
                    source_reconciliation_identity=reconciliation_identity,
                ),
            )
        else:
            raise RuntimeError(f"blocking Spot disposition survived Phase A: {minute} {disposition}")

        if item is not None:
            superseded_roles = sorted(superseded.get(minute, set()))
            if superseded_roles:
                conflict_observations = [
                    observation_ids[role][minute]
                    for role in superseded_roles
                    if minute in observation_ids.get(role, {})
                ]
                conflict_identity = canonical_sha256(
                    {
                        "minute_start_ms": minute,
                        "superseded_roles": superseded_roles,
                        "observations": conflict_observations,
                        "resolution_identity": reconciliation_identity,
                    },
                )
                conflict_writer.writerow(
                    (
                        conflict_identity,
                        SPOT_PROFILE.value,
                        SPOT_INSTRUMENT,
                        minute * 1_000_000,
                        "RESOLVED_SUPERSEDED",
                        reason,
                        canonical_text(conflict_observations),
                        reconciliation_identity,
                    ),
                )
                conflict_count += 1
        reconciliation_ids.append(reconciliation_identity)
        counts[disposition] += 1

    if len(dispositions) != EXPECTED_MINUTES or len(bars) + no_trade_count != EXPECTED_MINUTES:
        raise RuntimeError("Spot canonical/disposition grid is incomplete")
    if dict(sorted(counts.items())) != analysis["spot"]["disposition_counts"]:
        raise RuntimeError("Spot disposition counts diverged from adopted Phase-A evidence")
    source_reconciliation_identity = canonical_sha256(
        {
            "profile": SPOT_PROFILE.value,
            "phase_a_analysis_identity": analysis["analysis_identity"],
            "minute_reconciliation_identities": reconciliation_ids,
        },
    )
    result = {
        "bars": bars,
        "dispositions": dispositions,
        "source_reconciliation_identity": source_reconciliation_identity,
        "counts": dict(sorted(counts.items())),
        "conflict_count": conflict_count,
        "no_trade_count": no_trade_count,
        "aggtrade_row_count": aggtrade_count,
        "source_summaries": {"daily": daily_summary, "monthly": monthly_summary, "rest": rest_summary},
        "binding_pairs": monthly_pairs,
        "used_raw_sha256s": sorted(
            summary_raw_hashes(daily_summary, monthly_summary, rest_summary)
            | pair_raw_hashes(daily_pairs)
            | pair_raw_hashes(monthly_pairs)
            | pair_raw_hashes(anomaly_agg_pairs)
            | pair_raw_hashes(
                pair
                for pair in new["archive_pairs"]
                if pair["task"].get("source_role") in {
                    "SPOT_TARGET_DAILY_TRADES",
                    "SPOT_NO_TRADE_2021_03_06_DAILY_TRADES",
                    "SPOT_NO_TRADE_2021_04_20_DAILY_TRADES",
                    "SPOT_PARTIAL_2021_04_25_DAILY_TRADES",
                }
            )
        ),
    }
    del daily, monthly, rest
    gc.collect()
    return result


def process_perpetual_market_data(
    *,
    analyzer: Any,
    old: dict[str, Any],
    new: dict[str, Any],
    analysis: dict[str, Any],
    registry: SourceRegistry,
    source_writer: csv.writer,
    execution_writer: csv.writer,
    mark_writer: csv.writer,
    minute_writer: csv.writer,
) -> dict[str, Any]:
    exec_daily_pairs = analyzer.source_kind_pairs(
        old,
        new,
        "usdm_execution",
        "USDM_DAILY_EXECUTION_ARCHIVE",
        "USDM_DAILY_EXECUTION_KLINES",
    )
    exec_monthly_pairs = analyzer.source_kind_pairs(
        old,
        new,
        "usdm_execution",
        "USDM_MONTHLY_EXECUTION_ARCHIVE",
        "USDM_MONTHLY_EXECUTION_KLINES",
    )
    exec_rest_items = analyzer.old_fapi_rest(old, "usdm_execution") + analyzer.new_fapi_rest(
        new,
        "USDM_REST_EXECUTION_KLINES_JULY",
    )
    exec_daily, exec_daily_summary = analyzer.build_archive_map(
        exec_daily_pairs,
        source_role="USDM_DAILY_EXECUTION_KLINES",
        kind="execution",
        start_ms=START_MS,
        end_ms=END_MS,
    )
    exec_monthly, exec_monthly_summary = analyzer.build_archive_map(
        exec_monthly_pairs,
        source_role="USDM_MONTHLY_EXECUTION_KLINES",
        kind="execution",
        start_ms=START_MS,
        end_ms=END_MS,
    )
    exec_rest, exec_rest_summary = analyzer.build_rest_map(
        exec_rest_items,
        source_role="USDM_REST_EXECUTION_KLINES",
        kind="execution",
        start_ms=START_MS,
        end_ms=END_MS,
    )
    empty: dict[int, set[str]] = {}
    write_kline_observations(
        source_writer,
        registry,
        exec_rest,
        source_role="USDM_REST_EXECUTION_KLINES",
        superseded=empty,
    )
    write_kline_observations(
        source_writer,
        registry,
        exec_daily,
        source_role="USDM_DAILY_EXECUTION_KLINES",
        superseded=empty,
    )
    write_kline_observations(
        source_writer,
        registry,
        exec_monthly,
        source_role="USDM_MONTHLY_EXECUTION_KLINES",
        superseded=empty,
    )
    execution_bars: list[NormalizedBar] = []
    execution_state: dict[int, tuple[str, str]] = {}
    for minute in range(START_MS, END_MS, ONE_MINUTE_MS):
        rows = {
            "REST": exec_rest.get(minute),
            "DAILY": exec_daily.get(minute),
            "MONTHLY": exec_monthly.get(minute),
        }
        selected, roles = canonical_consensus(rows, minimum=3)
        source_sha256s = [rows[role].source_sha256 for role in roles]
        material = selected.material
        identity = material_identity(
            market_profile=PERP_PROFILE.value,
            instrument_id=PERP_INSTRUMENT,
            disposition=CoverageDisposition.REAL_OFFICIAL_BAR.value,
            material=material,
            source_sha256s=source_sha256s,
        )
        row = list(
            execution_csv_row(
                canonical_identity=identity,
                instrument_id=PERP_INSTRUMENT,
                disposition=CoverageDisposition.REAL_OFFICIAL_BAR.value,
                material=material,
                source_sha256s=source_sha256s,
            ),
        )
        row.pop(5)
        execution_writer.writerow(row)
        execution_bars.append(
            normalized_bar(
                source_role=SourceRole.USDM_PERPETUAL_EXECUTION_1M,
                instrument_id=PERP_INSTRUMENT,
                material=material,
                canonical_identity=identity,
            ),
        )
        reconciliation = canonical_sha256(
            {
                "role": "PERPETUAL_EXECUTION_1M",
                "minute_start_ms": minute,
                "canonical_bar_identity": identity,
                "official_sources": sorted(source_sha256s),
                "semantic_sha256": selected.semantic_sha256,
            },
        )
        execution_state[minute] = (identity, reconciliation)
    del exec_daily, exec_monthly, exec_rest
    gc.collect()

    mark_daily_pairs = analyzer.source_kind_pairs(
        old,
        new,
        "usdm_mark",
        "USDM_DAILY_MARK_ARCHIVE",
        "USDM_DAILY_MARK_KLINES",
    )
    mark_monthly_pairs = analyzer.source_kind_pairs(
        old,
        new,
        "usdm_mark",
        "USDM_MONTHLY_MARK_ARCHIVE",
        "USDM_MONTHLY_MARK_KLINES",
    )
    mark_rest_items = analyzer.old_fapi_rest(old, "usdm_mark") + analyzer.new_fapi_rest(
        new,
        "USDM_REST_MARK_KLINES_JULY",
    )
    mark_daily, mark_daily_summary = analyzer.build_archive_map(
        mark_daily_pairs,
        source_role="USDM_DAILY_MARK_KLINES",
        kind="mark",
        start_ms=START_MS,
        end_ms=END_MS,
    )
    mark_monthly, mark_monthly_summary = analyzer.build_archive_map(
        mark_monthly_pairs,
        source_role="USDM_MONTHLY_MARK_KLINES",
        kind="mark",
        start_ms=START_MS,
        end_ms=END_MS,
    )
    mark_rest, mark_rest_summary = analyzer.build_rest_map(
        mark_rest_items,
        source_role="USDM_REST_MARK_KLINES",
        kind="mark",
        start_ms=START_MS,
        end_ms=END_MS,
    )
    write_kline_observations(
        source_writer,
        registry,
        mark_rest,
        source_role="USDM_REST_MARK_KLINES",
        superseded=empty,
    )
    write_kline_observations(
        source_writer,
        registry,
        mark_daily,
        source_role="USDM_DAILY_MARK_KLINES",
        superseded=empty,
    )
    write_kline_observations(
        source_writer,
        registry,
        mark_monthly,
        source_role="USDM_MONTHLY_MARK_KLINES",
        superseded=empty,
    )
    mark_bars: list[NormalizedBar] = []
    dispositions: list[MinuteDisposition] = []
    reconciliation_ids: list[str] = []
    redundant_missing = Counter()
    for minute in range(START_MS, END_MS, ONE_MINUTE_MS):
        rows = {
            "REST": mark_rest.get(minute),
            "DAILY": mark_daily.get(minute),
            "MONTHLY": mark_monthly.get(minute),
        }
        selected, roles = canonical_consensus(rows, minimum=2)
        present_valid = {
            role: item for role, item in rows.items()
            if item is not None and not item.invalid_reasons
        }
        if any(item.semantic_sha256 != selected.semantic_sha256 for item in present_valid.values()):
            raise RuntimeError(f"conflicting official Mark observation at {minute}")
        for role, item in rows.items():
            if item is None:
                redundant_missing[role] += 1
        sources = [rows[role].source_sha256 for role in roles]
        material = selected.material
        identity = material_identity(
            market_profile=PERP_PROFILE.value,
            instrument_id=PERP_INSTRUMENT,
            disposition="ORIGINAL_OFFICIAL_MARK_BAR",
            material=material,
            source_sha256s=sources,
        )
        mark_writer.writerow(
            (
                identity,
                PERP_INSTRUMENT,
                minute * 1_000_000,
                (minute + ONE_MINUTE_MS) * 1_000_000,
                (minute + ONE_MINUTE_MS) * 1_000_000,
                str(material[1]), str(material[1]),
                str(material[2]), str(material[2]),
                str(material[3]), str(material[3]),
                str(material[4]), str(material[4]),
                sorted(sources)[0],
                canonical_text(sorted(sources)),
            ),
        )
        mark_bars.append(
            NormalizedBar(
                source_role=SourceRole.USDM_PERPETUAL_MARK_1M,
                instrument_id=PERP_INSTRUMENT,
                interval_start_ns=minute * 1_000_000,
                interval_end_exclusive_ns=(minute + ONE_MINUTE_MS) * 1_000_000,
                available_at_ns=(minute + ONE_MINUTE_MS) * 1_000_000,
                open=Decimal(str(material[1])),
                high=Decimal(str(material[2])),
                low=Decimal(str(material[3])),
                close=Decimal(str(material[4])),
                volume=Decimal("0"),
                source_row_number=0,
                source_row_sha256=identity,
            ),
        )
        execution_identity, execution_reconciliation = execution_state[minute]
        reconciliation = canonical_sha256(
            {
                "profile": PERP_PROFILE.value,
                "minute_start_ms": minute,
                "execution_reconciliation_identity": execution_reconciliation,
                "mark_canonical_identity": identity,
                "mark_official_sources": sorted(sources),
                "mark_semantic_sha256": selected.semantic_sha256,
                "missing_redundant_roles": sorted(role for role, value in rows.items() if value is None),
            },
        )
        reason = (
            "EXECUTION_THREE_ROLE_EXACT;MARK_AT_LEAST_TWO_OFFICIAL_ROLES_EXACT"
            if len(roles) == 2
            else "EXECUTION_AND_MARK_THREE_ROLE_EXACT_AGREEMENT"
        )
        minute_writer.writerow(
            (
                PERP_PROFILE.value,
                PERP_INSTRUMENT,
                minute * 1_000_000,
                CoverageDisposition.REAL_OFFICIAL_BAR.value,
                execution_identity,
                null(None),
                reconciliation,
                reason,
                False,
            ),
        )
        dispositions.append(
            MinuteDisposition(
                open_time_ns=minute * 1_000_000,
                disposition=CoverageDisposition.REAL_OFFICIAL_BAR,
                canonical_bar_identity=execution_identity,
                proof_identity="NOT_APPLICABLE",
                source_reconciliation_identity=reconciliation,
            ),
        )
        reconciliation_ids.append(reconciliation)
    del mark_daily, mark_monthly, mark_rest
    gc.collect()
    if len(execution_bars) != EXPECTED_MINUTES or len(mark_bars) != EXPECTED_MINUTES:
        raise RuntimeError("Perpetual execution/Mark grid is incomplete")
    expected_redundant = analysis["perpetual"]["mark"]["redundant_delivery_unavailable_by_role"]
    if dict(sorted(redundant_missing.items())) != expected_redundant:
        raise RuntimeError("Perpetual redundant-role disposition diverged from Phase A")
    return {
        "execution_bars": execution_bars,
        "mark_bars": mark_bars,
        "dispositions": dispositions,
        "source_reconciliation_identity": canonical_sha256(
            {
                "profile": PERP_PROFILE.value,
                "phase_a_analysis_identity": analysis["analysis_identity"],
                "minute_reconciliation_identities": reconciliation_ids,
            },
        ),
        "execution_source_summaries": {
            "daily": exec_daily_summary,
            "monthly": exec_monthly_summary,
            "rest": exec_rest_summary,
        },
        "mark_source_summaries": {
            "daily": mark_daily_summary,
            "monthly": mark_monthly_summary,
            "rest": mark_rest_summary,
        },
        "redundant_missing_by_role": dict(sorted(redundant_missing.items())),
        "execution_binding_pairs": exec_monthly_pairs,
        "mark_binding_pairs": mark_monthly_pairs,
        "used_raw_sha256s": sorted(
            summary_raw_hashes(
                exec_daily_summary,
                exec_monthly_summary,
                exec_rest_summary,
                mark_daily_summary,
                mark_monthly_summary,
                mark_rest_summary,
            )
            | pair_raw_hashes(exec_daily_pairs)
            | pair_raw_hashes(exec_monthly_pairs)
            | pair_raw_hashes(mark_daily_pairs)
            | pair_raw_hashes(mark_monthly_pairs)
        ),
    }


def process_funding(
    *,
    analyzer: Any,
    old: dict[str, Any],
    new: dict[str, Any],
    registry: SourceRegistry,
    source_writer: csv.writer,
    funding_writer: csv.writer,
) -> dict[str, Any]:
    pairs = analyzer.old_archive_pairs(old, "usdm_funding", "monthly", START_MS, JULY_START_MS)
    pairs += analyzer.new_archive_pairs(new, "USDM_MONTHLY_FUNDING_RATE")
    archive_rows: dict[int, tuple[int, str, str, int]] = {}
    ordered_source_hashes: list[str] = []
    for pair in sorted(pairs, key=lambda item: analyzer.task_value(item, "range_start_ms", "start_ms")):
        source_sha = pair["archive"]["raw_object_sha256"]
        ordered_source_hashes.append(source_sha)
        analyzer.verify_pair(pair)
        with zipfile.ZipFile(io.BytesIO(registry.bytes(source_sha))) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].is_dir():
                raise RuntimeError("funding archive member mismatch")
            with archive.open(members[0]) as binary:
                rows = csv.reader(io.TextIOWrapper(binary, encoding="utf-8", newline=""), strict=True)
                if next(rows, None) != [
                    "calc_time",
                    "funding_interval_hours",
                    "last_funding_rate",
                ]:
                    raise RuntimeError("funding archive header mismatch")
                raw_rows = tuple(rows)
        prior_timestamp: int | None = None
        for row_number, row in enumerate(raw_rows, start=2):
            if len(row) != 3:
                raise RuntimeError("funding archive row malformed")
            timestamp = int(row[0])
            interval_hours = int(row[1])
            rate = row[2]
            try:
                parsed_rate = Decimal(rate)
            except Exception as exc:
                raise RuntimeError("funding archive raw rate lexeme is invalid") from exc
            if not parsed_rate.is_finite() or not rate or rate.strip() != rate:
                raise RuntimeError("funding archive raw rate lexeme is invalid")
            if prior_timestamp is not None and timestamp <= prior_timestamp:
                raise RuntimeError("funding archive timestamps are non-monotonic")
            prior_timestamp = timestamp
            if timestamp < START_MS or timestamp >= END_MS:
                continue
            if timestamp in archive_rows:
                raise RuntimeError(f"duplicate archive funding timestamp {timestamp}")
            archive_rows[timestamp] = (interval_hours, rate, source_sha, row_number)
            semantic = canonical_sha256(
                {"funding_time_ms": timestamp, "interval_hours": interval_hours, "rate": rate},
            )
            source_writer.writerow(
                source_observation_row(
                    registry,
                    source_sha256=source_sha,
                    source_role="USDM_MONTHLY_FUNDING_RATE",
                    event_time_ms=timestamp,
                    semantic_sha256=semantic,
                    original_fields=(str(timestamp), str(interval_hours), rate),
                ),
            )

    rest = new["funding_rest"]
    rest_payload = registry.bytes(rest["raw_object_sha256"])
    rest_value = json.loads(rest_payload)
    if not isinstance(rest_value, list) or len(rest_value) >= 1000:
        raise RuntimeError("funding REST response is malformed or pagination-incomplete")
    rest_rows: dict[int, tuple[str, int]] = {}
    for row_number, item in enumerate(rest_value, start=1):
        if item.get("symbol") != "BTCUSDT":
            raise RuntimeError("funding REST symbol mismatch")
        timestamp = int(item["fundingTime"])
        rate = str(item["fundingRate"])
        try:
            parsed_rate = Decimal(rate)
        except Exception as exc:
            raise RuntimeError("funding REST raw rate lexeme is invalid") from exc
        if not parsed_rate.is_finite() or not rate or rate.strip() != rate:
            raise RuntimeError("funding REST raw rate lexeme is invalid")
        if timestamp in rest_rows:
            raise RuntimeError("duplicate funding REST timestamp")
        rest_rows[timestamp] = (rate, row_number)
        semantic = canonical_sha256({"funding_time_ms": timestamp, "rate": rate})
        source_writer.writerow(
            source_observation_row(
                registry,
                source_sha256=rest["raw_object_sha256"],
                source_role="USDM_REST_FUNDING_RATE",
                event_time_ms=timestamp,
                semantic_sha256=semantic,
                original_fields=(str(item["symbol"]), str(timestamp), rate, str(item.get("markPrice", ""))),
            ),
        )

    events: list[FundingEvent] = []
    source_by_event: dict[str, str] = {}
    for timestamp in sorted(archive_rows):
        interval_hours, rate, source_sha, row_number = archive_rows[timestamp]
        rest_row = rest_rows.get(timestamp)
        if rest_row is None or Decimal(rest_row[0]) != Decimal(rate):
            raise RuntimeError(f"funding archive/REST conflict at {timestamp}")
        event_key = canonical_sha256(
            {
                "instrument_id": PERP_INSTRUMENT,
                "calc_time_ns": timestamp * 1_000_000,
                "funding_interval_hours": interval_hours,
                "funding_rate": Decimal(rate),
                "funding_rate_raw_lexeme": rate,
            },
        )
        source_row_hash = canonical_sha256(
            {"source_sha256": source_sha, "row_number": row_number, "event_key": event_key},
        )
        event = FundingEvent(
            instrument_id=PERP_INSTRUMENT,
            calc_time_ns=timestamp * 1_000_000,
            funding_interval_hours=interval_hours,
            funding_rate=Decimal(rate),
            source_row_number=row_number,
            source_row_sha256=source_row_hash,
            event_key=event_key,
            raw_rate_text=rate,
        )
        events.append(event)
        source_by_event[event_key] = source_sha
        sources = sorted({source_sha, rest["raw_object_sha256"]})
        funding_writer.writerow(
            (
                event_key,
                PERP_INSTRUMENT,
                timestamp * 1_000_000,
                interval_hours,
                rate,
                rate,
                source_sha,
                canonical_text(sources),
            ),
        )
    if set(rest_rows) != set(archive_rows):
        raise RuntimeError("funding REST/archive event set mismatch")
    time_range = TimeRange(
        start_inclusive=datetime(2021, 1, 1, tzinfo=UTC),
        end_exclusive=datetime(2021, 8, 1, tzinfo=UTC),
    )
    schedule = prove_funding_schedule_from_official_objects(
        tuple(events),
        source_object_sha256s=tuple(ordered_source_hashes),
        time_range=time_range,
    )
    return {
        "events": events,
        "schedule": schedule,
        "binding_pairs": pairs,
        "archive_source_sha256s": ordered_source_hashes,
        "rest_source_sha256": rest["raw_object_sha256"],
        "event_source_sha256_by_identity": source_by_event,
        "used_raw_sha256s": sorted(
            pair_raw_hashes(pairs)
            | {str(rest["raw_object_sha256"])}
        ),
    }


def load_metadata(
    registry: SourceRegistry,
    new: dict[str, Any],
) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    post = json.loads(POST_ADOPTION_PATH.read_text(encoding="utf-8"))
    matches = [
        item for item in post["observations"]
        if item["source_role"] == "SPOT_CURRENT_INSTRUMENT_METADATA"
        and item["exact_url"] == SPOT_METADATA_URL
    ]
    if len(matches) != 1:
        raise RuntimeError("exact post-adoption Spot metadata observation is missing")
    spot_observation = matches[0]
    spot_metadata = parse_spot_instrument_metadata(
        registry.bytes(spot_observation["raw_object_sha256"]),
        raw_symbol="BTCUSDT",
        instrument_id=SPOT_INSTRUMENT,
        source_object_sha256=spot_observation["raw_object_sha256"],
        maker_fee_rate=FEE_RATE,
        taker_fee_rate=FEE_RATE,
        fee_rate_basis=FEE_BASIS,
        official_source=SPOT_METADATA_URL,
    )
    perp_items = [
        item for item in new["public_references"]
        if item["source_role"] == "USDM_CURRENT_INSTRUMENT_METADATA"
        and item["exact_url"] == "https://fapi.binance.com/fapi/v1/exchangeInfo"
    ]
    if len(perp_items) != 1:
        raise RuntimeError("exact USD-M metadata observation is missing")
    perp_observation = perp_items[0]
    perp_metadata = parse_usdm_instrument_metadata(
        registry.bytes(perp_observation["raw_object_sha256"]),
        raw_symbol="BTCUSDT",
        instrument_id=PERP_INSTRUMENT,
        source_object_sha256=perp_observation["raw_object_sha256"],
        maker_fee_rate=FEE_RATE,
        taker_fee_rate=FEE_RATE,
        fee_rate_basis=FEE_BASIS,
    )
    return spot_metadata, perp_metadata, spot_observation, perp_observation


def required_decimal_precision(value: Decimal) -> int:
    """Return the minimum finite decimal places needed for exact equality."""

    if not value.is_finite():
        raise RuntimeError("non-finite source Decimal")
    fixed = format(value, "f")
    if "." not in fixed:
        return 0
    return len(fixed.rstrip("0").split(".", 1)[1])


def source_decimal_precision(value: Decimal) -> int:
    if not value.is_finite():
        raise RuntimeError("non-finite source Decimal")
    return max(0, -value.as_tuple().exponent)


def numeric_field_audit(
    rows: Iterable[Any],
    *,
    field: str,
    timestamp_field: str,
    previous_representation_precision: int,
) -> dict[str, Any]:
    required_distribution: Counter[int] = Counter()
    source_distribution: Counter[int] = Counter()
    affected: list[int] = []
    trailing_zero_only = 0
    count = 0
    for row in rows:
        count += 1
        value = getattr(row, field)
        if not isinstance(value, Decimal):
            value = Decimal(str(value))
        required = required_decimal_precision(value)
        source = source_decimal_precision(value)
        required_distribution[required] += 1
        source_distribution[source] += 1
        if required > previous_representation_precision:
            affected.append(int(getattr(row, timestamp_field)))
        elif source > previous_representation_precision:
            trailing_zero_only += 1
    maximum = max(required_distribution, default=0)
    return {
        "field": field,
        "row_count": count,
        "previous_representation_precision": previous_representation_precision,
        "maximum_exact_required_decimal_precision": maximum,
        "required_precision_distribution": {
            str(key): required_distribution[key] for key in sorted(required_distribution)
        },
        "source_spelling_precision_distribution": {
            str(key): source_distribution[key] for key in sorted(source_distribution)
        },
        "affected_row_count": len(affected),
        "first_affected_timestamp_ns": None if not affected else min(affected),
        "last_affected_timestamp_ns": None if not affected else max(affected),
        "values_requiring_non_zero_digits_above_previous_precision": len(affected),
        "source_spellings_with_trailing_zeros_only_above_previous_precision": trailing_zero_only,
        "lossless_zero_padding_possible": True,
        "rounding_required": False,
    }


def build_source_precision_audit(
    *,
    spot_bars: tuple[NormalizedBar, ...] | list[NormalizedBar],
    perpetual_execution_bars: tuple[NormalizedBar, ...] | list[NormalizedBar],
    perpetual_mark_bars: tuple[NormalizedBar, ...] | list[NormalizedBar],
    funding_events: tuple[FundingEvent, ...] | list[FundingEvent],
    spot_metadata: Any,
    perp_metadata: Any,
) -> dict[str, Any]:
    spot_fields = {
        field: numeric_field_audit(
            spot_bars,
            field=field,
            timestamp_field="interval_start_ns",
            previous_representation_precision=(
                spot_metadata.size_precision if field == "volume" else spot_metadata.price_precision
            ),
        )
        for field in ("open", "high", "low", "close", "volume")
    }
    perp_execution_fields = {
        field: numeric_field_audit(
            perpetual_execution_bars,
            field=field,
            timestamp_field="interval_start_ns",
            previous_representation_precision=(
                perp_metadata.size_precision if field == "volume" else perp_metadata.price_precision
            ),
        )
        for field in ("open", "high", "low", "close", "volume")
    }
    perp_mark_fields = {
        field: numeric_field_audit(
            perpetual_mark_bars,
            field=field,
            timestamp_field="interval_start_ns",
            previous_representation_precision=perp_metadata.price_precision,
        )
        for field in ("open", "high", "low", "close")
    }
    funding_fields = {
        "funding_rate": numeric_field_audit(
            funding_events,
            field="funding_rate",
            timestamp_field="calc_time_ns",
            previous_representation_precision=0,
        ),
    }
    spot_price_precision = max(
        item["maximum_exact_required_decimal_precision"]
        for field, item in spot_fields.items()
        if field != "volume"
    )
    spot_size_precision = spot_fields["volume"]["maximum_exact_required_decimal_precision"]
    perp_execution_price_precision = max(
        item["maximum_exact_required_decimal_precision"]
        for field, item in perp_execution_fields.items()
        if field != "volume"
    )
    perp_mark_precision = max(
        item["maximum_exact_required_decimal_precision"] for item in perp_mark_fields.values()
    )
    perp_size_precision = perp_execution_fields["volume"]["maximum_exact_required_decimal_precision"]
    material = {
        "schema": "source-precision-audit-v1",
        "spot": {
            "fields": spot_fields,
            "execution_price_representation_precision": spot_price_precision,
            "bar_volume_representation_precision": spot_size_precision,
            "instrument_size_representation_precision": spot_size_precision,
            "economic_order_quantity_contract": {
                "min_quantity": str(spot_metadata.min_quantity),
                "max_quantity": str(spot_metadata.max_quantity),
                "size_increment": str(spot_metadata.size_increment),
                "representation_precision": spot_size_precision,
            },
        },
        "perpetual": {
            "execution_fields": perp_execution_fields,
            "mark_fields": perp_mark_fields,
            "funding_fields": funding_fields,
            "execution_price_maximum_exact_precision": perp_execution_price_precision,
            "mark_price_maximum_exact_precision": perp_mark_precision,
            "instrument_price_representation_precision": max(
                perp_execution_price_precision,
                perp_mark_precision,
            ),
            "bar_volume_and_order_quantity_representation_precision": perp_size_precision,
            "economic_order_quantity_contract": {
                "min_quantity": str(perp_metadata.min_quantity),
                "max_quantity": str(perp_metadata.max_quantity),
                "size_increment": str(perp_metadata.size_increment),
                "representation_precision": perp_size_precision,
            },
        },
        "raw_canonical_decimal_text_changed": False,
        "numeric_market_value_changed": False,
        "rounding_or_truncation_used": False,
    }
    return {"source_precision_audit_identity": canonical_sha256(material), **material}


def pair_range(analyzer: Any, pair: dict[str, Any]) -> TimeRange:
    start_ms = int(analyzer.task_value(pair, "range_start_ms", "start_ms"))
    end_ms = int(analyzer.task_value(pair, "range_end_ms", "end_ms"))
    return TimeRange(
        start_inclusive=datetime_from_ms(start_ms),
        end_exclusive=datetime_from_ms(end_ms),
    )


def pair_raw_hashes(pairs: Iterable[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for pair in pairs:
        for key in ("archive", "checksum"):
            observation = pair.get(key)
            if isinstance(observation, dict) and observation.get("raw_object_sha256"):
                result.add(str(observation["raw_object_sha256"]))
    return result


def summary_raw_hashes(*summaries: dict[str, Any]) -> set[str]:
    return {
        str(digest)
        for summary in summaries
        for digest in summary.get("source_object_sha256s", ())
    }


def pair_binding(
    analyzer: Any,
    registry: SourceRegistry,
    pair: dict[str, Any],
    *,
    source_role: SourceRole,
    market_profile: MarketProfile,
    interval: str,
) -> SourceObjectBinding:
    if not pair.get("archive_available", True):
        raise RuntimeError("unavailable archive cannot bind a DatasetRelease")
    archive = pair["archive"]
    digest = archive["raw_object_sha256"]
    task = pair["task"]
    filename = task.get("exact_filename") or task.get("filename")
    locator = task["url"]
    return SourceObjectBinding(
        source_role=source_role,
        source_locator=locator,
        exact_filename=filename,
        byte_size=registry.raw[digest].byte_size,
        sha256=digest,
        publisher_checksum=pair["publisher_checksum"],
        instrument="BTCUSDT",
        market_profile=market_profile.value,
        requested_interval=interval,
        requested_time_range=pair_range(analyzer, pair),
        conflicts_with_sha256=(),
    )


def metadata_binding(
    registry: SourceRegistry,
    observation: dict[str, Any],
    *,
    source_role: SourceRole,
    market_profile: MarketProfile,
    exact_filename: str,
) -> SourceObjectBinding:
    digest = observation["raw_object_sha256"]
    locator = observation.get("exact_url") or observation.get("exact_locator")
    return SourceObjectBinding(
        source_role=source_role,
        source_locator=locator,
        exact_filename=exact_filename,
        byte_size=registry.raw[digest].byte_size,
        sha256=digest,
        publisher_checksum="NOT_AVAILABLE",
        instrument="BTCUSDT",
        market_profile=market_profile.value,
        requested_interval="NOT_APPLICABLE",
        requested_time_range="NOT_APPLICABLE",
        conflicts_with_sha256=(),
    )


def source_bindings(
    *,
    analyzer: Any,
    registry: SourceRegistry,
    spot_pairs: list[dict[str, Any]],
    perp_execution_pairs: list[dict[str, Any]],
    perp_mark_pairs: list[dict[str, Any]],
    funding_pairs: list[dict[str, Any]],
    spot_metadata_observation: dict[str, Any],
    spot_historical_grid_observation: dict[str, Any],
    perp_metadata_observation: dict[str, Any],
    historical_grid_observation: dict[str, Any],
) -> tuple[tuple[SourceObjectBinding, ...], tuple[SourceObjectBinding, ...]]:
    spot = [
        pair_binding(
            analyzer,
            registry,
            pair,
            source_role=SourceRole.SPOT_EXECUTION_1M,
            market_profile=SPOT_PROFILE,
            interval="1m",
        )
        for pair in spot_pairs
    ]
    spot.append(
        metadata_binding(
            registry,
            spot_metadata_observation,
            source_role=SourceRole.SPOT_INSTRUMENT_METADATA,
            market_profile=SPOT_PROFILE,
            exact_filename="spot-exchangeInfo-BTCUSDT.json",
        ),
    )
    spot.append(
        metadata_binding(
            registry,
            spot_historical_grid_observation,
            source_role=SourceRole.SPOT_HISTORICAL_ORDER_GRID,
            market_profile=SPOT_PROFILE,
            exact_filename=SPOT_HISTORICAL_GRID_FILENAME,
        ),
    )
    perp: list[SourceObjectBinding] = []
    for pairs, role, interval in (
        (perp_execution_pairs, SourceRole.USDM_PERPETUAL_EXECUTION_1M, "1m"),
        (perp_mark_pairs, SourceRole.USDM_PERPETUAL_MARK_1M, "1m"),
        (funding_pairs, SourceRole.USDM_PERPETUAL_FUNDING, "EVENT"),
    ):
        perp.extend(
            pair_binding(
                analyzer,
                registry,
                pair,
                source_role=role,
                market_profile=PERP_PROFILE,
                interval=interval,
            )
            for pair in pairs
        )
    perp.append(
        metadata_binding(
            registry,
            perp_metadata_observation,
            source_role=SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA,
            market_profile=PERP_PROFILE,
            exact_filename="usdm-exchangeInfo.json",
        ),
    )
    perp.append(
        metadata_binding(
            registry,
            historical_grid_observation,
            source_role=SourceRole.USDM_PERPETUAL_HISTORICAL_ORDER_GRID,
            market_profile=PERP_PROFILE,
            exact_filename=HISTORICAL_GRID_FILENAME,
        ),
    )
    return (
        tuple(sorted(spot, key=lambda item: (item.source_role.value, item.source_locator, item.sha256))),
        tuple(sorted(perp, key=lambda item: (item.source_role.value, item.source_locator, item.sha256))),
    )


def window_contracts(analysis: dict[str, Any]) -> dict[str, Any]:
    geometry = {
        **analysis["partition_geometry"],
        "dataset_start_inclusive": "2021-01-01T00:00:00Z",
        "scoring_start_inclusive": "2021-02-01T00:00:00Z",
        "scoring_end_exclusive": "2021-08-01T00:00:00Z",
        "dataset_end_exclusive": "2021-08-01T00:00:00Z",
    }
    partition_identity = canonical_sha256(geometry)
    rows: list[dict[str, Any]] = []
    for item in analysis["window_scan"]:
        material = {
            **item,
            "partition_geometry_identity": partition_identity,
            "phase_a_analysis_identity": analysis["analysis_identity"],
        }
        rows.append({"data_window_identity": canonical_sha256(material), **material})
    selected = next(item for item in rows if item["shift_months"] == 1 and item["status"] == "PASS")
    return {
        "partition_geometry": geometry,
        "partition_geometry_identity": partition_identity,
        "windows": rows,
        "selected": selected,
        "data_quality_exposure_identity": canonical_sha256(rows),
    }


def insert_raw_inventory(connection: duckdb.DuckDBPyConnection, registry: SourceRegistry) -> None:
    connection.executemany(
        "INSERT INTO raw_objects VALUES (?, ?, ?, ?, ?)",
        [
            (
                item.sha256,
                item.byte_size,
                str(item.local_path.relative_to(ROOT)),
                "IMMUTABLE_BINANCE_OFFICIAL_RAW_BYTES",
                True,
            )
            for item in sorted(registry.raw.values(), key=lambda value: value.sha256)
        ],
    )
    connection.executemany(
        "INSERT INTO source_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [raw_source_observation_row(item) for item in registry.observations],
    )


def iter_archive_pairs(old: dict[str, Any], new: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield from old["archive_pairs"]
    yield from new["archive_pairs"]


def publisher_checksum_rows(
    old: dict[str, Any],
    new: dict[str, Any],
    registry: SourceRegistry,
) -> tuple[tuple[Any, ...], ...]:
    rows: dict[tuple[str, str], tuple[Any, ...]] = {}
    for pair in iter_archive_pairs(old, new):
        if not pair.get("archive_available", True):
            continue
        archive = pair["archive"]
        checksum = pair.get("checksum")
        if checksum is None or pair.get("publisher_checksum_match") is not True:
            raise RuntimeError("available official archive lacks a matching publisher checksum")
        archive_sha = archive["raw_object_sha256"]
        checksum_sha = checksum["raw_object_sha256"]
        selected = (archive_sha in registry.raw, checksum_sha in registry.raw)
        if selected == (False, False):
            continue
        if selected != (True, True):
            raise RuntimeError("authorized archive/checksum inventory binding is incomplete")
        task = pair["task"]
        filename = task.get("exact_filename") or task.get("filename")
        identity = canonical_sha256(
            {
                "archive_raw_object_sha256": archive_sha,
                "checksum_raw_object_sha256": checksum_sha,
                "filename": filename,
                "publisher_sha256": pair["publisher_checksum"],
            },
        )
        rows[(archive_sha, checksum_sha)] = (
            identity,
            archive_sha,
            checksum_sha,
            filename,
            pair["publisher_checksum"],
            True,
        )
    return tuple(rows[key] for key in sorted(rows))


def insert_publisher_checksums(
    connection: duckdb.DuckDBPyConnection,
    rows: tuple[tuple[Any, ...], ...],
) -> None:
    connection.executemany(
        "INSERT INTO publisher_checksums VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def close_publisher_checksum_inventory(
    raw_hashes: set[str],
    checksum_rows: tuple[tuple[Any, ...], ...],
) -> set[str]:
    """Add checksum response bytes for every participating official archive."""

    result = set(raw_hashes)
    for _, archive_sha256, checksum_sha256, _, _, _ in checksum_rows:
        if archive_sha256 in result:
            result.add(checksum_sha256)
    return result


def build_full_raw_inventory(
    *,
    registry: SourceRegistry,
    raw_hashes: set[str],
    market_profile: MarketProfile,
    instrument_id: str,
    data_window_identity: str,
    source_reconciliation_identity: str,
    checksum_rows: tuple[tuple[Any, ...], ...],
) -> DatasetRawInventory:
    """Build the canonical typed inventory from acquisition-level authority."""

    checksum_by_archive: dict[str, list[PublisherChecksumBinding]] = {}
    for _, archive_sha256, checksum_sha256, filename, publisher_sha256, local_match in checksum_rows:
        if archive_sha256 not in raw_hashes:
            continue
        if local_match is not True:
            raise RuntimeError("publisher checksum entered inventory without a local match")
        checksum_by_archive.setdefault(archive_sha256, []).append(
            PublisherChecksumBinding(
                checksum_raw_object_sha256=checksum_sha256,
                exact_filename=filename,
                publisher_sha256=publisher_sha256,
            ),
        )
    objects: list[RawInventoryObject] = []
    for digest in sorted(raw_hashes):
        binding = registry.raw.get(digest)
        origins = registry.by_sha.get(digest)
        if binding is None or not origins:
            raise RuntimeError(f"participating Raw object lacks acquisition authority: {digest}")
        typed_origins = tuple(
            sorted(
                (
                    RawInventoryOrigin(
                        observation_id=str(item["observation_id"]),
                        source_role=str(item["source_role"]),
                        exact_locator=str(item["exact_locator"]),
                        exact_query_json=str(item["exact_query_json"]),
                        http_status=int(item["http_status"]),
                        validation_status=(
                            "UNAVAILABLE" if int(item["http_status"]) == 404 else "RAW_PRESERVED"
                        ),
                        delivery_classification=(
                            "REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE"
                            if int(item["http_status"]) == 404 and "MARK" in str(item["source_role"])
                            else "NOT_APPLICABLE"
                        ),
                    )
                    for item in origins
                ),
                key=lambda item: (
                    item.source_role,
                    item.exact_locator,
                    item.exact_query_json,
                    item.observation_id,
                ),
            ),
        )
        typed_checksums = tuple(
            sorted(
                checksum_by_archive.get(digest, ()),
                key=lambda item: (
                    item.exact_filename,
                    item.publisher_sha256,
                    item.checksum_raw_object_sha256,
                ),
            ),
        )
        objects.append(
            RawInventoryObject(
                raw_object_sha256=digest,
                byte_size=binding.byte_size,
                instrument="BTCUSDT",
                market_profile=market_profile,
                origins=typed_origins,
                publisher_checksum_bindings=typed_checksums,
            ),
        )
    return DatasetRawInventory.create(
        market_profile=market_profile,
        instrument_id=instrument_id,
        data_window_identity=data_window_identity,
        source_reconciliation_identity=source_reconciliation_identity,
        raw_object_count=len(objects),
        raw_objects=tuple(objects),
    )


def _add_source_json_hashes(
    connection: duckdb.DuckDBPyConnection,
    result: set[str],
    table: str,
) -> None:
    for (payload,) in connection.execute(
        f"SELECT DISTINCT source_sha256s_json FROM {table}",
    ).fetchall():
        values = json.loads(payload)
        if not isinstance(values, list) or any(
            not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
            for item in values
        ):
            raise RuntimeError(f"DATA_HASH_MISMATCH: malformed {table} source projection")
        result.update(values)


def derive_participating_raw_hashes(
    connection: duckdb.DuckDBPyConnection,
    market_profile: MarketProfile,
) -> set[str]:
    """Project participating Raw objects independently from relational semantics."""

    result: set[str] = set()
    if market_profile is SPOT_PROFILE:
        _add_source_json_hashes(connection, result, "spot_execution_bars_1m")
        result.update(
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT source_raw_object_sha256 FROM spot_agg_trades",
            ).fetchall()
        )
        for raw_trade, aggtrade in connection.execute(
            """
            SELECT DISTINCT raw_trade_source_sha256, aggtrade_source_sha256
            FROM verified_no_trade_intervals
            """,
        ).fetchall():
            result.update((raw_trade, aggtrade))
    else:
        _add_source_json_hashes(connection, result, "perpetual_execution_bars_1m")
        _add_source_json_hashes(connection, result, "perpetual_mark_bars_1m")
        _add_source_json_hashes(connection, result, "perpetual_funding_events")
        result.update(
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT raw_object_sha256
                FROM source_observations
                WHERE parsed_event_time_ms IS NULL
                  AND semantic_row_sha256 IS NULL
                  AND validation_status = 'UNAVAILABLE'
                  AND delivery_classification = 'REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE'
                  AND source_role LIKE '%MARK%'
                  AND instrument = 'BTCUSDT'
                """,
            ).fetchall()
        )
    conflict_observations: set[str] = set()
    for (payload,) in connection.execute(
        "SELECT source_observation_ids_json FROM source_conflicts WHERE market_profile = ?",
        [market_profile.value],
    ).fetchall():
        values = json.loads(payload)
        if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
            raise RuntimeError("DATA_HASH_MISMATCH: malformed conflict observation projection")
        conflict_observations.update(values)
    if conflict_observations:
        observation_to_raw = dict(
            connection.execute(
                "SELECT observation_id, raw_object_sha256 FROM source_observations",
            ).fetchall(),
        )
        missing = sorted(conflict_observations - set(observation_to_raw))
        if missing:
            raise RuntimeError(
                f"DATA_HASH_MISMATCH: conflict observations do not resolve: {missing[:3]}",
            )
        result.update(observation_to_raw[item] for item in conflict_observations)
    result.update(
        row[0]
        for row in connection.execute(
            """
            SELECT DISTINCT binding.source_raw_object_sha256
            FROM instrument_metadata_source_bindings AS binding
            JOIN instrument_metadata AS metadata
              ON metadata.instrument_metadata_identity = binding.instrument_metadata_identity
            WHERE metadata.market_profile = ?
            """,
            [market_profile.value],
        ).fetchall()
    )
    for archive_sha256, checksum_sha256 in connection.execute(
        "SELECT archive_raw_object_sha256, checksum_raw_object_sha256 FROM publisher_checksums",
    ).fetchall():
        if archive_sha256 in result:
            result.add(checksum_sha256)
    return result


def verify_full_raw_inventory_gate(
    connection: duckdb.DuckDBPyConnection,
    release: Any,
    registry: SourceRegistry,
) -> dict[str, Any]:
    """Require four-way equality and independently re-hash every participating blob."""

    inventory = release.raw_inventory
    if not isinstance(inventory, DatasetRawInventory):
        raise RuntimeError("DATASET_RAW_INVENTORY_MISMATCH: typed full Raw inventory is absent")
    declared = {item.raw_object_sha256 for item in inventory.raw_objects}
    members = {
        row[0]
        for row in connection.execute(
            """
            SELECT member_identity FROM release_members
            WHERE dataset_release_id = ? AND member_type = 'RAW_OBJECT'
            """,
            [release.dataset_release_id],
        ).fetchall()
    }
    participating = derive_participating_raw_hashes(connection, release.market_profile)
    verified: set[str] = set()
    database_raw = {
        row[0]: (int(row[1]), bool(row[2]))
        for row in connection.execute(
            "SELECT raw_object_sha256, byte_size, content_verified FROM raw_objects",
        ).fetchall()
    }
    for digest in sorted(participating):
        binding = registry.raw.get(digest)
        if binding is None:
            continue
        database_binding = database_raw.get(digest)
        if (
            database_binding == (binding.byte_size, True)
            and binding.local_path.is_file()
            and not binding.local_path.is_symlink()
            and binding.local_path.stat().st_size == binding.byte_size
            and hash_file(binding.local_path) == digest
        ):
            verified.add(digest)
    if not declared == members == participating == verified:
        raise RuntimeError(
            "DATASET_RAW_INVENTORY_MISMATCH: full Raw inventory equality failed "
            f"declared={len(declared)} members={len(members)} "
            f"participating={len(participating)} verified={len(verified)}",
        )
    inventory_by_hash = {
        item.raw_object_sha256: item for item in inventory.raw_objects
    }
    for digest in declared:
        if inventory_by_hash[digest].byte_size != database_raw[digest][0]:
            raise RuntimeError("DATA_HASH_MISMATCH: Raw inventory byte-size projection differs")
    acquisition_rows: dict[str, list[tuple[Any, ...]]] = {}
    for row in connection.execute(
        """
        SELECT raw_object_sha256, observation_id, source_role, exact_locator,
               exact_query_json, http_status, validation_status,
               coalesce(delivery_classification, 'NOT_APPLICABLE')
        FROM source_observations
        WHERE parsed_event_time_ms IS NULL AND semantic_row_sha256 IS NULL
        ORDER BY raw_object_sha256, source_role, exact_locator, exact_query_json, observation_id
        """,
    ).fetchall():
        acquisition_rows.setdefault(row[0], []).append(tuple(row[1:]))
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
            raise RuntimeError("DATA_SOURCE_INVALID: Raw acquisition-origin projection differs")
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
        tuple(row)
        for row in connection.execute(
            """
            SELECT archive_raw_object_sha256, checksum_raw_object_sha256,
                   exact_filename, publisher_sha256
            FROM publisher_checksums
            """,
        ).fetchall()
        if row[0] in declared
    }
    if declared_checksums != database_checksums:
        raise RuntimeError("DATA_SOURCE_INVALID: publisher-checksum projection differs")
    return {
        "market_profile": release.market_profile.value,
        "dataset_release_id": release.dataset_release_id,
        "raw_inventory_identity": inventory.raw_inventory_identity,
        "raw_object_count": len(declared),
        "release_equals_members": True,
        "release_equals_independent_participation_projection": True,
        "release_equals_verified_blobs": True,
        "acquisition_origins_equal": True,
        "publisher_checksum_bindings_equal": True,
    }


def table_columns(connection: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info('{table}')").fetchall()]


def hash_query(connection: duckdb.DuckDBPyConnection, query: str) -> str:
    with tempfile.TemporaryDirectory(prefix="binance-semantic-export-") as directory:
        export = Path(directory) / "ordered.csv"
        escaped = str(export).replace("'", "''")
        connection.execute(
            f"COPY ({query}) TO '{escaped}' "
            f"(FORMAT CSV, HEADER TRUE, NULLSTR '{CSV_NULL}', DELIMITER ',', QUOTE '\"', ESCAPE '\"')",
        )
        return hash_file(export)


TABLE_ORDER = {
    "schema_metadata": "schema_identity",
    "raw_objects": "raw_object_sha256",
    "source_observations": "observation_id",
    "publisher_checksums": "checksum_identity",
    "source_conflicts": "conflict_identity",
    "spot_agg_trades": "source_raw_object_sha256, aggregate_trade_id",
    "spot_execution_bars_1m": "instrument_id, open_time_ns",
    "perpetual_execution_bars_1m": "instrument_id, open_time_ns",
    "perpetual_mark_bars_1m": "instrument_id, open_time_ns",
    "perpetual_funding_events": "instrument_id, funding_time_ns",
    "verified_no_trade_intervals": "instrument_id, start_ns",
    "minute_dispositions": "market_profile, instrument_id, open_time_ns",
    "instrument_metadata": "instrument_metadata_identity",
    "instrument_metadata_source_bindings": (
        "instrument_metadata_identity, source_raw_object_sha256, binding_purpose"
    ),
    "nautilus_market_state_acceptance": "validation_identity",
    "data_windows": "shift_months, data_window_identity",
    "dataset_releases": "market_profile, instrument_id",
    "dataset_release_supersessions": "superseded_dataset_release_id",
    "release_members": "dataset_release_id, member_type, member_identity",
    "validation_results": "validation_name, validation_identity",
}


def semantic_table_hashes(connection: duckdb.DuckDBPyConnection) -> dict[str, str]:
    result = {
        table: hash_query(connection, f"SELECT * FROM {table} ORDER BY {ordering}")
        for table, ordering in TABLE_ORDER.items()
    }
    build_rows = connection.execute(
        """
        SELECT schema_identity, source_inventory_identity, semantic_database_identity,
               semantic_export_contract, table_hashes_json, row_counts_json, dataset_release_ids_json,
               catalog_identities_json, completed_at_utc
        FROM build_manifests ORDER BY schema_identity
        """,
    ).fetchall()
    result["build_manifests"] = sha256_bytes(
        b"".join(canonical_json_bytes(list(row)) + b"\n" for row in build_rows),
    )
    return result


def row_counts(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name",
        ).fetchall()
    ]
    return {table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]) for table in tables}


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json_bytes(value) + b"\n"
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"immutable JSON artifact collision: {path}")
        return
    descriptor, temporary = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def validation_row(name: str, material: dict[str, Any], checked_at: str) -> tuple[Any, ...]:
    identity = canonical_sha256({"validation_name": name, "status": "PASS", "material": material})
    return identity, name, "PASS", canonical_text(material), checked_at


def build(
    *,
    database_path: Path,
    catalog_root: Path,
    staging: Path,
    result_path: Path,
    role: str,
) -> dict[str, Any]:
    if database_path.exists():
        raise RuntimeError(f"fresh versioned DuckDB path required: {database_path}")
    if catalog_root.exists() and any(catalog_root.iterdir()):
        raise RuntimeError(f"fresh versioned catalog root required: {catalog_root}")
    if duckdb.__version__ != EXPECTED_DUCKDB_VERSION:
        raise RuntimeError(f"DuckDB identity mismatch: {duckdb.__version__}")
    duckdb_module = Path(duckdb.__file__).resolve()
    expected_site = (ROOT / ".data-venv/lib/python3.12/site-packages").resolve()
    if expected_site not in duckdb_module.parents:
        raise RuntimeError(f"DuckDB was not imported from the independent Data Tool environment: {duckdb_module}")
    if hash_file(ROOT / "SSOT.md") != EXPECTED_SSOT_IDENTITY:
        raise RuntimeError("adopted SSOT identity changed")

    old = json.loads(OLD_INDEX_PATH.read_text(encoding="utf-8"))
    new = json.loads(NEW_INDEX_PATH.read_text(encoding="utf-8"))
    analysis = json.loads(ANALYSIS_PATH.read_text(encoding="utf-8"))
    if new.get("acquisition_identity") != EXPECTED_ACQUISITION_IDENTITY:
        raise RuntimeError("Phase-A acquisition identity changed")
    if analysis.get("analysis_identity") != EXPECTED_ANALYSIS_IDENTITY or analysis.get("status") != "PASS":
        raise RuntimeError("adopted Phase-A analysis identity/status changed")
    if analysis["source_policy"] != {
        "binary_float_material_calculation_used": False,
        "credentials_used": False,
        "free_official_binance_only": True,
        "mark_reconstructed": False,
        "paid_provider_used": False,
        "synthetic_price_created": False,
        "third_party_data_used": False,
    }:
        raise RuntimeError("Phase-A source policy changed")

    analyzer = load_analyzer()
    registry = SourceRegistry()
    registry.load()
    spot_historical_grid_observation = register_spot_historical_order_grid_authority(registry)
    historical_grid_observation = register_historical_order_grid_authority(registry)
    (
        base_spot_metadata,
        base_perp_metadata,
        spot_metadata_observation,
        perp_metadata_observation,
    ) = load_metadata(
        registry,
        new,
    )
    windows = window_contracts(analysis)
    selected = windows["selected"]
    time_range = TimeRange(
        start_inclusive=datetime(2021, 1, 1, tzinfo=UTC),
        end_exclusive=datetime(2021, 8, 1, tzinfo=UTC),
    )
    checked_at = deterministic_timestamp()
    schema_bytes = SCHEMA_PATH.read_bytes()
    schema_identity = sha256_bytes(schema_bytes)
    data_tool_lock_identity = hash_file(DATA_TOOL_LOCK_PATH)

    database_path.parent.mkdir(parents=True, exist_ok=True)
    catalog_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir(parents=True, exist_ok=True)
    csv_paths = {
        "source_observations": staging / "source-observations.csv",
        "spot_execution_bars_1m": staging / "spot-execution-bars.csv",
        "perpetual_execution_bars_1m": staging / "perpetual-execution-bars.csv",
        "perpetual_mark_bars_1m": staging / "perpetual-mark-bars.csv",
        "minute_dispositions": staging / "minute-dispositions.csv",
        "source_conflicts": staging / "source-conflicts.csv",
        "verified_no_trade_intervals": staging / "verified-no-trade.csv",
        "spot_agg_trades": staging / "spot-agg-trades.csv",
        "perpetual_funding_events": staging / "perpetual-funding-events.csv",
    }
    streams: dict[str, Any] = {}
    writers: dict[str, csv.writer] = {}
    headers = {
        "source_observations": SOURCE_OBSERVATION_HEADER,
        "spot_execution_bars_1m": EXECUTION_HEADER,
        "perpetual_execution_bars_1m": PERP_EXECUTION_HEADER,
        "perpetual_mark_bars_1m": MARK_HEADER,
        "minute_dispositions": MINUTE_HEADER,
        "source_conflicts": CONFLICT_HEADER,
        "verified_no_trade_intervals": NO_TRADE_HEADER,
        "spot_agg_trades": AGGTRADE_HEADER,
        "perpetual_funding_events": FUNDING_HEADER,
    }
    for name, path in csv_paths.items():
        streams[name], writers[name] = csv_writer(path, headers[name])

    connection = configure_database(database_path)
    result: dict[str, Any] = {}
    try:
        connection.execute("BEGIN TRANSACTION")
        connection.execute(schema_bytes.decode("utf-8"))
        connection.execute(
            "INSERT INTO schema_metadata VALUES (?, ?, ?, ?, ?, ?)",
            [
                schema_identity,
                "free-official-binance-duckdb-v3-full-raw-inventory",
                EXPECTED_DUCKDB_VERSION,
                "UTC_INTEGER_NANOSECONDS_CANONICAL;SOURCE_UNITS_EXPLICIT",
                False,
                False,
            ],
        )
        insert_raw_inventory(connection, registry)
        checksum_rows = publisher_checksum_rows(old, new, registry)
        insert_publisher_checksums(connection, checksum_rows)
        checksum_count = len(checksum_rows)

        spot = process_spot(
            analyzer=analyzer,
            old=old,
            new=new,
            analysis=analysis,
            registry=registry,
            source_writer=writers["source_observations"],
            execution_writer=writers["spot_execution_bars_1m"],
            minute_writer=writers["minute_dispositions"],
            conflict_writer=writers["source_conflicts"],
            no_trade_writer=writers["verified_no_trade_intervals"],
            aggtrade_writer=writers["spot_agg_trades"],
        )
        spot_coverage_identity = minute_coverage_identity(spot["dispositions"])

        perpetual = process_perpetual_market_data(
            analyzer=analyzer,
            old=old,
            new=new,
            analysis=analysis,
            registry=registry,
            source_writer=writers["source_observations"],
            execution_writer=writers["perpetual_execution_bars_1m"],
            mark_writer=writers["perpetual_mark_bars_1m"],
            minute_writer=writers["minute_dispositions"],
        )
        funding = process_funding(
            analyzer=analyzer,
            old=old,
            new=new,
            registry=registry,
            source_writer=writers["source_observations"],
            funding_writer=writers["perpetual_funding_events"],
        )
        perp_coverage_identity = minute_coverage_identity(perpetual["dispositions"])

        source_precision_audit = build_source_precision_audit(
            spot_bars=spot["bars"],
            perpetual_execution_bars=perpetual["execution_bars"],
            perpetual_mark_bars=perpetual["mark_bars"],
            funding_events=funding["events"],
            spot_metadata=base_spot_metadata,
            perp_metadata=base_perp_metadata,
        )
        spot_metadata = bind_lossless_instrument_representation(
            base_spot_metadata,
            price_precision=int(
                source_precision_audit["spot"]["execution_price_representation_precision"],
            ),
            size_precision=int(
                source_precision_audit["spot"]["instrument_size_representation_precision"],
            ),
            price_increment=Decimal("0.01"),
            size_increment=Decimal("0.000001"),
            representation_evidence={
                "source_precision_audit_identity": source_precision_audit[
                    "source_precision_audit_identity"
                ],
                "normalization": "LOSSLESS_ZERO_PADDING_ONLY",
            },
            economic_order_grid_evidence={
                "historical_numeric_increments_proven": True,
                "historical_exact_for_window": False,
                "price_increment": {
                    "value": "0.01",
                    "authority": "CURRENT_OFFICIAL_FILTER_PLUS_NO_RELEVANT_BINANCE_CHANGE_IN_OFFICIAL_ANNOUNCEMENT_AUDIT",
                    "source_raw_object_sha256s": [
                        base_spot_metadata.source_object_sha256,
                        SPOT_HISTORICAL_GRID_SHA256,
                    ],
                },
                "size_increment": {
                    "value": "0.000001",
                    "authority": "OFFICIAL_BINANCE_BEFORE_AFTER_STEP_SIZE_TABLE_EFFECTIVE_2021_08_26",
                    "source_raw_object_sha256": SPOT_HISTORICAL_GRID_SHA256,
                },
                "limits": "CURRENT_OFFICIAL_FILTERS_FAIL_CLOSED_FOR_QUALIFICATION",
            },
        )
        perp_metadata = bind_lossless_instrument_representation(
            base_perp_metadata,
            price_precision=int(
                source_precision_audit["perpetual"]["instrument_price_representation_precision"],
            ),
            size_precision=int(
                source_precision_audit["perpetual"][
                    "bar_volume_and_order_quantity_representation_precision"
                ],
            ),
            price_increment=Decimal("0.01"),
            size_increment=Decimal("0.001"),
            representation_evidence={
                "source_precision_audit_identity": source_precision_audit[
                    "source_precision_audit_identity"
                ],
                "normalization": "LOSSLESS_ZERO_PADDING_ONLY",
            },
            economic_order_grid_evidence={
                "historical_numeric_increments_proven": True,
                "historical_exact_for_window": False,
                "price_increment": {
                    "value": "0.01",
                    "authority": "OFFICIAL_BINANCE_BEFORE_AFTER_TICK_SIZE_TABLE_EFFECTIVE_2022_02_15",
                    "source_raw_object_sha256": HISTORICAL_GRID_SHA256,
                },
                "size_increment": {
                    "value": "0.001",
                    "authority": "OFFICIAL_EXCHANGE_INFO_AND_LOCKED_QUANTITY_REPRESENTATION",
                    "source_raw_object_sha256": base_perp_metadata.source_object_sha256,
                },
                "limits": "CURRENT_OFFICIAL_FILTERS_FAIL_CLOSED_FOR_QUALIFICATION",
            },
        )

        spot_catalog_binding = {
            "data_window_identity": selected["data_window_identity"],
            "partition_geometry_identity": windows["partition_geometry_identity"],
            "minute_coverage_identity": spot_coverage_identity,
            "normalized_time_range": time_range.to_builtins(),
        }
        spot_catalog = build_nautilus_catalog(
            catalog_root / "spot",
            metadata=spot_metadata,
            execution_bars=spot["bars"],
            semantic_binding=spot_catalog_binding,
        )
        perp_catalog_binding = {
            "data_window_identity": selected["data_window_identity"],
            "partition_geometry_identity": windows["partition_geometry_identity"],
            "minute_coverage_identity": perp_coverage_identity,
            "normalized_time_range": time_range.to_builtins(),
        }
        perp_catalog = build_nautilus_catalog(
            catalog_root / "perpetual",
            metadata=perp_metadata,
            execution_bars=perpetual["execution_bars"],
            mark_bars=perpetual["mark_bars"],
            funding_events=funding["events"],
            funding_native_binding=FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR,
            semantic_binding=perp_catalog_binding,
        )

        spot_market_acceptance = qualify_executable_market_state(
            config_path=SPOT_ACCEPTANCE_CONFIG,
            catalog_identity=spot_catalog.catalog_identity,
            metadata=spot_metadata,
            instrument=spot_catalog.instrument,
            execution_bars=spot_catalog.execution_bars,
            diagnostic_root=staging / "nautilus-diagnostics/spot",
        )
        spot_validation_identity = market_state_acceptance_identity(spot_market_acceptance)
        perp_market_acceptance = qualify_executable_market_state(
            config_path=PERP_ACCEPTANCE_CONFIG,
            catalog_identity=perp_catalog.catalog_identity,
            metadata=perp_metadata,
            instrument=perp_catalog.instrument,
            execution_bars=perp_catalog.execution_bars,
            mark_updates=perp_catalog.mark_updates,
            funding_updates=perp_catalog.funding_updates,
            diagnostic_root=staging / "nautilus-diagnostics/perpetual",
        )
        perp_validation_identity = market_state_acceptance_identity(perp_market_acceptance)

        spot_bindings, perp_bindings = source_bindings(
            analyzer=analyzer,
            registry=registry,
            spot_pairs=spot["binding_pairs"],
            perp_execution_pairs=perpetual["execution_binding_pairs"],
            perp_mark_pairs=perpetual["mark_binding_pairs"],
            funding_pairs=funding["binding_pairs"],
            spot_metadata_observation=spot_metadata_observation,
            spot_historical_grid_observation=spot_historical_grid_observation,
            perp_metadata_observation=perp_metadata_observation,
            historical_grid_observation=historical_grid_observation,
        )
        spot_raw_hashes = close_publisher_checksum_inventory(
            set(spot["used_raw_sha256s"])
            | {spot_metadata.source_object_sha256, SPOT_HISTORICAL_GRID_SHA256},
            checksum_rows,
        )
        perp_raw_hashes = close_publisher_checksum_inventory(
            set(perpetual["used_raw_sha256s"])
            | set(funding["used_raw_sha256s"])
            | {perp_metadata.source_object_sha256, HISTORICAL_GRID_SHA256},
            checksum_rows,
        )
        spot_raw_inventory = build_full_raw_inventory(
            registry=registry,
            raw_hashes=spot_raw_hashes,
            market_profile=SPOT_PROFILE,
            instrument_id=SPOT_INSTRUMENT,
            data_window_identity=selected["data_window_identity"],
            source_reconciliation_identity=spot["source_reconciliation_identity"],
            checksum_rows=checksum_rows,
        )
        perp_raw_inventory = build_full_raw_inventory(
            registry=registry,
            raw_hashes=perp_raw_hashes,
            market_profile=PERP_PROFILE,
            instrument_id=PERP_INSTRUMENT,
            data_window_identity=selected["data_window_identity"],
            source_reconciliation_identity=perpetual["source_reconciliation_identity"],
            checksum_rows=checksum_rows,
        )
        spot_release = build_dataset_release(
            market_profile=SPOT_PROFILE,
            instrument_id=SPOT_INSTRUMENT,
            source_objects=spot_bindings,
            normalized_time_range=time_range,
            instrument_metadata=spot_metadata,
            execution_bars=spot["bars"],
            catalog_identity=spot_catalog.catalog_identity,
            created_at_utc=datetime.fromisoformat(checked_at.replace("Z", "+00:00")),
            minute_dispositions=spot["dispositions"],
            data_window_identity=selected["data_window_identity"],
            partition_geometry_identity=windows["partition_geometry_identity"],
            minute_coverage_identity_value=spot_coverage_identity,
            source_reconciliation_identity=spot["source_reconciliation_identity"],
            derived_validation_identity=spot_validation_identity,
            data_tool_lock_identity=data_tool_lock_identity,
            data_quality_exposure_identity=windows["data_quality_exposure_identity"],
            normalizer_version=FULL_RAW_INVENTORY_NORMALIZER_VERSION,
            raw_inventory=spot_raw_inventory,
        )
        perp_release = build_dataset_release(
            market_profile=PERP_PROFILE,
            instrument_id=PERP_INSTRUMENT,
            source_objects=perp_bindings,
            normalized_time_range=time_range,
            instrument_metadata=perp_metadata,
            execution_bars=perpetual["execution_bars"],
            mark_bars=perpetual["mark_bars"],
            funding_events=funding["events"],
            funding_schedule=funding["schedule"],
            funding_native_binding=FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR,
            catalog_identity=perp_catalog.catalog_identity,
            created_at_utc=datetime.fromisoformat(checked_at.replace("Z", "+00:00")),
            minute_dispositions=perpetual["dispositions"],
            data_window_identity=selected["data_window_identity"],
            partition_geometry_identity=windows["partition_geometry_identity"],
            minute_coverage_identity_value=perp_coverage_identity,
            source_reconciliation_identity=perpetual["source_reconciliation_identity"],
            derived_validation_identity=perp_validation_identity,
            data_tool_lock_identity=data_tool_lock_identity,
            data_quality_exposure_identity=windows["data_quality_exposure_identity"],
            normalizer_version=FULL_RAW_INVENTORY_NORMALIZER_VERSION,
            raw_inventory=perp_raw_inventory,
        )

        for stream in streams.values():
            close_csv(stream)
        streams.clear()
        for table, path in csv_paths.items():
            copy_csv(connection, table, path)

        connection.executemany(
            "INSERT INTO instrument_metadata VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    spot_metadata.instrument_metadata_identity,
                    SPOT_PROFILE.value,
                    SPOT_INSTRUMENT,
                    spot_metadata.source_object_sha256,
                    spot_metadata.observed_at_utc.isoformat().replace("+00:00", "Z"),
                    False,
                    spot_metadata.to_json_bytes().decode("utf-8"),
                ),
                (
                    perp_metadata.instrument_metadata_identity,
                    PERP_PROFILE.value,
                    PERP_INSTRUMENT,
                    perp_metadata.source_object_sha256,
                    perp_metadata.observed_at_utc.isoformat().replace("+00:00", "Z"),
                    False,
                    perp_metadata.to_json_bytes().decode("utf-8"),
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO instrument_metadata_source_bindings VALUES (?, ?, ?, ?)",
            [
                (
                    spot_metadata.instrument_metadata_identity,
                    base_spot_metadata.source_object_sha256,
                    SourceRole.SPOT_INSTRUMENT_METADATA.value,
                    "CURRENT_OFFICIAL_FILTERS",
                ),
                (
                    spot_metadata.instrument_metadata_identity,
                    base_spot_metadata.source_object_sha256,
                    SourceRole.SPOT_INSTRUMENT_METADATA.value,
                    "LOSSLESS_RUNTIME_REPRESENTATION",
                ),
                (
                    spot_metadata.instrument_metadata_identity,
                    SPOT_HISTORICAL_GRID_SHA256,
                    SourceRole.SPOT_HISTORICAL_ORDER_GRID.value,
                    "HISTORICAL_ORDER_GRID",
                ),
                (
                    perp_metadata.instrument_metadata_identity,
                    base_perp_metadata.source_object_sha256,
                    SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA.value,
                    "CURRENT_OFFICIAL_FILTERS",
                ),
                (
                    perp_metadata.instrument_metadata_identity,
                    base_perp_metadata.source_object_sha256,
                    SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA.value,
                    "LOSSLESS_RUNTIME_REPRESENTATION",
                ),
                (
                    perp_metadata.instrument_metadata_identity,
                    HISTORICAL_GRID_SHA256,
                    SourceRole.USDM_PERPETUAL_HISTORICAL_ORDER_GRID.value,
                    "HISTORICAL_PRICE_GRID",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO nautilus_market_state_acceptance VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    spot_validation_identity,
                    SPOT_PROFILE.value,
                    SPOT_INSTRUMENT,
                    spot_metadata.instrument_metadata_identity,
                    spot_market_acceptance["expected_executable_bars"],
                    spot_market_acceptance["accepted_executable_bars"],
                    spot_market_acceptance["expected_mark_updates"],
                    spot_market_acceptance["accepted_mark_updates"],
                    spot_market_acceptance["precision_skipped_bars"],
                    spot_market_acceptance["rejected_precision_events"],
                    spot_market_acceptance["missing_market_state"],
                    canonical_text(spot_market_acceptance),
                ),
                (
                    perp_validation_identity,
                    PERP_PROFILE.value,
                    PERP_INSTRUMENT,
                    perp_metadata.instrument_metadata_identity,
                    perp_market_acceptance["expected_executable_bars"],
                    perp_market_acceptance["accepted_executable_bars"],
                    perp_market_acceptance["expected_mark_updates"],
                    perp_market_acceptance["accepted_mark_updates"],
                    perp_market_acceptance["precision_skipped_bars"],
                    perp_market_acceptance["rejected_precision_events"],
                    perp_market_acceptance["missing_market_state"],
                    canonical_text(perp_market_acceptance),
                ),
            ],
        )
        for item in windows["windows"]:
            connection.execute(
                "INSERT INTO data_windows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    item["data_window_identity"],
                    item["classification"],
                    item["shift_months"],
                    iso8601_ns(item["dataset_start_inclusive"]),
                    iso8601_ns(item["scoring_start_inclusive"]),
                    iso8601_ns(item["scoring_end_exclusive"]),
                    iso8601_ns(item["dataset_end_exclusive"]),
                    windows["partition_geometry_identity"],
                    item["status"],
                    item["reason"],
                    False,
                ],
            )
        releases = (spot_release, perp_release)
        connection.executemany(
            """
            INSERT INTO dataset_releases (
                dataset_release_id, market_profile, instrument_id,
                data_window_identity, partition_geometry_identity,
                minute_coverage_identity, source_reconciliation_identity,
                raw_inventory_identity, raw_inventory_object_count,
                catalog_identity, semantic_release_json, status, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    release.dataset_release_id,
                    release.market_profile.value,
                    release.instrument_id,
                    release.data_window_identity,
                    release.partition_geometry_identity,
                    release.minute_coverage_identity,
                    release.source_reconciliation_identity,
                    release.raw_inventory.raw_inventory_identity,
                    release.raw_inventory.raw_object_count,
                    release.catalog_identity,
                    release.to_json_bytes().decode("utf-8"),
                    "PASS",
                    checked_at,
                )
                for release in releases
            ],
        )
        # The INSERT statement above names all physical columns explicitly to prevent
        # accidental schema drift from being hidden by positional insertion.
        # DuckDB reports thirteen columns for this table; fail closed otherwise.
        if len(table_columns(connection, "dataset_releases")) != 13:
            raise RuntimeError("dataset_releases schema drift")
        connection.executemany(
            "INSERT INTO dataset_release_supersessions VALUES (?, ?, ?, ?)",
            [
                (
                    SUPERSEDED_SPOT_RELEASE,
                    spot_release.dataset_release_id,
                    "SUPERSEDED_INSTRUMENT_REPRESENTATION_INCOMPATIBLE_WITH_PINNED_NAUTILUS",
                    "INSTRUMENT_REPRESENTATION_PREVENTED_EXECUTABLE_MARKET_STATE",
                ),
                (
                    SUPERSEDED_PERP_RELEASE,
                    perp_release.dataset_release_id,
                    "SUPERSEDED_INSTRUMENT_REPRESENTATION_INCOMPATIBLE_WITH_PINNED_NAUTILUS",
                    "INSTRUMENT_REPRESENTATION_PREVENTED_EXECUTABLE_MARKET_STATE",
                ),
            ],
        )

        for release in releases:
            source_objects = derive_participating_raw_hashes(
                connection,
                release.market_profile,
            )
            missing_raw_bindings = sorted(source_objects - set(registry.raw))
            if missing_raw_bindings:
                raise RuntimeError(f"release raw bindings are not preserved: {missing_raw_bindings}")
            connection.executemany(
                "INSERT INTO release_members VALUES (?, 'RAW_OBJECT', ?, ?)",
                [(release.dataset_release_id, digest, digest) for digest in sorted(source_objects)],
            )
            connection.execute(
                "INSERT INTO release_members VALUES (?, 'INSTRUMENT_METADATA', ?, ?)",
                [
                    release.dataset_release_id,
                    release.instrument_metadata_identity,
                    spot_metadata.source_object_sha256
                    if release.market_profile is SPOT_PROFILE
                    else perp_metadata.source_object_sha256,
                ],
            )
        connection.execute(
            """
            INSERT INTO release_members
            SELECT ?, 'EXECUTION_BAR', canonical_bar_identity, primary_source_sha256
            FROM spot_execution_bars_1m
            """,
            [spot_release.dataset_release_id],
        )
        connection.execute(
            """
            INSERT INTO release_members
            SELECT ?, 'MINUTE_DISPOSITION', source_reconciliation_identity, NULL
            FROM minute_dispositions WHERE market_profile = ?
            """,
            [spot_release.dataset_release_id, SPOT_PROFILE.value],
        )
        connection.execute(
            """
            INSERT INTO release_members
            SELECT ?, 'EXECUTION_BAR', canonical_bar_identity, primary_source_sha256
            FROM perpetual_execution_bars_1m
            """,
            [perp_release.dataset_release_id],
        )
        connection.execute(
            """
            INSERT INTO release_members
            SELECT ?, 'MARK_BAR', canonical_bar_identity, primary_source_sha256
            FROM perpetual_mark_bars_1m
            """,
            [perp_release.dataset_release_id],
        )
        connection.execute(
            """
            INSERT INTO release_members
            SELECT ?, 'FUNDING_EVENT', event_identity, primary_source_sha256
            FROM perpetual_funding_events
            """,
            [perp_release.dataset_release_id],
        )
        connection.execute(
            """
            INSERT INTO release_members
            SELECT ?, 'MINUTE_DISPOSITION', source_reconciliation_identity, NULL
            FROM minute_dispositions WHERE market_profile = ?
            """,
            [perp_release.dataset_release_id, PERP_PROFILE.value],
        )

        inventory_gate_results = tuple(
            verify_full_raw_inventory_gate(connection, release, registry)
            for release in releases
        )

        validations = [
            validation_row(
                "FREE_OFFICIAL_SOURCE_POLICY",
                {
                    "free_official_binance_only": True,
                    "credentials_used": False,
                    "paid_provider_used": False,
                    "third_party_data_used": False,
                },
                checked_at,
            ),
            validation_row(
                "SPOT_MINUTE_DISPOSITION_GATE",
                {
                    "expected_minutes": EXPECTED_MINUTES,
                    "actual_minutes": len(spot["dispositions"]),
                    "canonical_bars": len(spot["bars"]),
                    "verified_no_trade_minutes": spot["no_trade_count"],
                    "unresolved": 0,
                    "synthetic_bars": 0,
                },
                checked_at,
            ),
            validation_row(
                "PERPETUAL_EXECUTION_MARK_FUNDING_GATE",
                {
                    "execution_minutes": len(perpetual["execution_bars"]),
                    "mark_minutes": len(perpetual["mark_bars"]),
                    "funding_events": len(funding["events"]),
                    "missing_mark_minutes": 0,
                    "mark_substitutions": 0,
                    "funding_conflicts": 0,
                },
                checked_at,
            ),
            validation_row(
                "SOURCE_PRECISION_AND_VALUE_CONTINUITY",
                source_precision_audit,
                checked_at,
            ),
            validation_row(
                "NAUTILUS_EXECUTABLE_MARKET_STATE_ACCEPTANCE_SPOT",
                {
                    "validation_identity": spot_validation_identity,
                    **spot_market_acceptance,
                },
                checked_at,
            ),
            validation_row(
                "NAUTILUS_EXECUTABLE_MARKET_STATE_ACCEPTANCE_PERPETUAL",
                {
                    "validation_identity": perp_validation_identity,
                    **perp_market_acceptance,
                },
                checked_at,
            ),
            validation_row(
                "DUCKDB_OFFLINE_NO_EXTENSIONS",
                {
                    "duckdb_version": duckdb.__version__,
                    "duckdb_module": str(duckdb_module),
                    "extensions_install_allowed": False,
                    "extensions_load_allowed": False,
                    "network_allowed": False,
                },
                checked_at,
            ),
            validation_row(
                "DATASET_RELEASE_GATE",
                {
                    "dataset_release_ids": sorted(release.dataset_release_id for release in releases),
                    "status": "PASS",
                    "binary_float_material_calculation_used": False,
                    "synthetic_ohlc_count": 0,
                },
                checked_at,
            ),
            validation_row(
                "FULL_RAW_INVENTORY_BIDIRECTIONAL_GATE",
                {
                    "profiles": list(inventory_gate_results),
                    "status": "PASS",
                },
                checked_at,
            ),
        ]
        connection.executemany("INSERT INTO validation_results VALUES (?, ?, ?, ?, ?)", validations)

        pre_manifest_hashes = {
            table: hash_query(connection, f"SELECT * FROM {table} ORDER BY {ordering}")
            for table, ordering in TABLE_ORDER.items()
        }
        semantic_database_identity = canonical_sha256(pre_manifest_hashes)
        source_inventory_identity = hash_query(
            connection,
            "SELECT raw_object_sha256, byte_size FROM raw_objects ORDER BY raw_object_sha256",
        )
        counts = row_counts(connection)
        counts["build_manifests"] = 1
        release_ids = sorted(release.dataset_release_id for release in releases)
        catalog_identities = sorted(release.catalog_identity for release in releases)
        build_material = {
            "schema_identity": schema_identity,
            "source_inventory_identity": source_inventory_identity,
            "semantic_database_identity": semantic_database_identity,
            "table_hashes": pre_manifest_hashes,
            "row_counts": counts,
            "dataset_release_ids": release_ids,
            "catalog_identities": catalog_identities,
            "completed_at_utc": checked_at,
            "semantic_export_contract": SEMANTIC_EXPORT_CONTRACT,
        }
        build_identity = canonical_sha256({"build_role": role, **build_material})
        connection.execute(
            "INSERT INTO build_manifests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                build_identity,
                role,
                schema_identity,
                source_inventory_identity,
                semantic_database_identity,
                SEMANTIC_EXPORT_CONTRACT,
                canonical_text(pre_manifest_hashes),
                canonical_text(counts),
                canonical_text(release_ids),
                canonical_text(catalog_identities),
                checked_at,
            ],
        )
        connection.execute("COMMIT")
        connection.execute("CHECKPOINT")

        artifact_root = result_path.parent / "release-artifacts"
        write_json(artifact_root / f"{spot_release.dataset_release_id}.json", spot_release.to_builtins())
        write_json(artifact_root / f"{perp_release.dataset_release_id}.json", perp_release.to_builtins())
        write_json(
            artifact_root / f"{spot_metadata.instrument_metadata_identity}.metadata.json",
            spot_metadata.to_builtins(),
        )
        write_json(
            artifact_root / f"{perp_metadata.instrument_metadata_identity}.metadata.json",
            perp_metadata.to_builtins(),
        )
        funding_material = {
            "schedule_identity": funding["schedule"].schedule_identity,
            "events": [item.semantic_payload() for item in funding["events"]],
            "native_binding": FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR,
        }
        if canonical_sha256(funding_material) != perp_release.funding_data_identity:
            raise RuntimeError("funding artifact identity mismatch")
        write_json(
            artifact_root / f"{perp_release.funding_data_identity}.funding.json",
            {"funding_data_identity": perp_release.funding_data_identity, **funding_material},
        )
        write_json(
            artifact_root / f"{spot_validation_identity}.market-state.json",
            {"validation_identity": spot_validation_identity, **spot_market_acceptance},
        )
        write_json(
            artifact_root / f"{perp_validation_identity}.market-state.json",
            {"validation_identity": perp_validation_identity, **perp_market_acceptance},
        )
        result = {
            "schema": "free-official-binance-duckdb-build-result-v3-full-raw-inventory",
            "status": "PASS",
            "build_role": role,
            "build_identity": build_identity,
            "schema_identity": schema_identity,
            "source_inventory_identity": source_inventory_identity,
            "semantic_database_identity": semantic_database_identity,
            "semantic_export_contract": SEMANTIC_EXPORT_CONTRACT,
            "pre_manifest_table_hashes": pre_manifest_hashes,
            "row_counts": counts,
            "dataset_release_ids": release_ids,
            "full_raw_inventory_gate": list(inventory_gate_results),
            "superseded_dataset_releases": {
                SUPERSEDED_SPOT_RELEASE: spot_release.dataset_release_id,
                SUPERSEDED_PERP_RELEASE: perp_release.dataset_release_id,
            },
            "instrument_metadata_identities": {
                SPOT_PROFILE.value: spot_metadata.instrument_metadata_identity,
                PERP_PROFILE.value: perp_metadata.instrument_metadata_identity,
            },
            "source_precision_audit": source_precision_audit,
            "market_state_acceptance": {
                SPOT_PROFILE.value: {
                    "validation_identity": spot_validation_identity,
                    **spot_market_acceptance,
                },
                PERP_PROFILE.value: {
                    "validation_identity": perp_validation_identity,
                    **perp_market_acceptance,
                },
            },
            "releases": {
                SPOT_PROFILE.value: spot_release.to_builtins(),
                PERP_PROFILE.value: perp_release.to_builtins(),
            },
            "catalogs": {
                SPOT_PROFILE.value: {
                    "catalog_identity": spot_catalog.catalog_identity,
                    "execution_bar_count": len(spot_catalog.execution_bars),
                    "mark_update_count": len(spot_catalog.mark_updates),
                    "funding_update_count": len(spot_catalog.funding_updates),
                },
                PERP_PROFILE.value: {
                    "catalog_identity": perp_catalog.catalog_identity,
                    "execution_bar_count": len(perp_catalog.execution_bars),
                    "mark_update_count": len(perp_catalog.mark_updates),
                    "funding_update_count": len(perp_catalog.funding_updates),
                    "funding_source_event_count": len(funding["events"]),
                },
            },
            "spot": {key: value for key, value in spot.items() if key not in {"bars", "dispositions", "binding_pairs"}},
            "perpetual": {
                key: value
                for key, value in perpetual.items()
                if key not in {
                    "execution_bars", "mark_bars", "dispositions", "execution_binding_pairs", "mark_binding_pairs"
                }
            },
            "funding": {
                "event_count": len(funding["events"]),
                "schedule_identity": funding["schedule"].schedule_identity,
                "archive_source_sha256s": funding["archive_source_sha256s"],
                "rest_source_sha256": funding["rest_source_sha256"],
            },
            "window_contracts": windows,
            "data_tool_lock_identity": data_tool_lock_identity,
            "duckdb_module": str(duckdb_module),
            "network_used_during_build": False,
            "strategy_run": False,
            "official_trial": False,
        }
    except BaseException:
        try:
            connection.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        for stream in streams.values():
            if not stream.closed:
                close_csv(stream)
        connection.close()

    readonly = configure_database(database_path, read_only=True)
    try:
        readonly_hashes = semantic_table_hashes(readonly)
        readonly_counts = row_counts(readonly)
        blockers = int(
            readonly.execute("SELECT count(*) FROM minute_dispositions WHERE blocking").fetchone()[0],
        )
        failed_validations = int(
            readonly.execute("SELECT count(*) FROM validation_results WHERE status <> 'PASS'").fetchone()[0],
        )
        release_statuses = readonly.execute(
            "SELECT market_profile, status FROM dataset_releases ORDER BY market_profile",
        ).fetchall()
    finally:
        readonly.close()
    if blockers or failed_validations or release_statuses != [
        (SPOT_PROFILE.value, "PASS"),
        (PERP_PROFILE.value, "PASS"),
    ]:
        raise RuntimeError("read-only DatasetRelease validation failed")
    expected_readonly_hashes = dict(result["pre_manifest_table_hashes"])
    expected_readonly_hashes["build_manifests"] = readonly_hashes["build_manifests"]
    if readonly_hashes != expected_readonly_hashes or readonly_counts != result["row_counts"]:
        raise RuntimeError("read-only semantic revalidation mismatch")
    database_hash = hash_file(database_path)
    database_size = database_path.stat().st_size
    database_path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    result.update(
        {
            "database_path": str(database_path.relative_to(ROOT)),
            "database_file_sha256": database_hash,
            "database_size_bytes": database_size,
            "readonly_reopen": "PASS",
            "readonly_table_hashes": readonly_hashes,
            "readonly_row_counts": readonly_counts,
            "database_mode": oct(database_path.stat().st_mode & 0o777),
        },
    )
    write_json(result_path, result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "build_role": role,
                "database_file_sha256": database_hash,
                "semantic_database_identity": result["semantic_database_identity"],
                "dataset_release_ids": result["dataset_release_ids"],
                "catalog_identities": sorted(item["catalog_identity"] for item in result["catalogs"].values()),
            },
            sort_keys=True,
        ),
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--catalog-root", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--role", choices=("PRIMARY", "INDEPENDENT_REBUILD"), required=True)
    return parser.parse_args()


def rooted(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    arguments = parse_args()
    build(
        database_path=rooted(arguments.database),
        catalog_root=rooted(arguments.catalog_root),
        staging=rooted(arguments.staging),
        result_path=rooted(arguments.result),
        role=arguments.role,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
