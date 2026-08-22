"""Strict M2 Binance data boundary and immutable Dataset Release contract.

Raw Binance bytes remain the provenance authority.  This module validates and
normalizes those bytes, then uses supported NautilusTrader public data and
catalog APIs for the derived execution inventory.  It does not implement any
trading, matching, funding-settlement, accounting, or valuation engine.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import urllib.request
import zipfile
from dataclasses import dataclass
from dataclasses import replace
from datetime import UTC
from datetime import date
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from decimal import InvalidOperation
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlparse

from nautilus_trader.model import AggregationSource
from nautilus_trader.model import Bar
from nautilus_trader.model import BarAggregation
from nautilus_trader.model import BarSpecification
from nautilus_trader.model import BarType
from nautilus_trader.model import CryptoPerpetual
from nautilus_trader.model import Currency
from nautilus_trader.model import CurrencyPair
from nautilus_trader.model import FundingRateUpdate
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import MarkPriceUpdate
from nautilus_trader.model import Money
from nautilus_trader.model import Price
from nautilus_trader.model import PriceType
from nautilus_trader.model import Quantity
from nautilus_trader.model import Symbol
from nautilus_trader.persistence import ParquetDataCatalog

from crypto_lab.config import ConfigError
from crypto_lab.config import MarketProfile
from crypto_lab.config import StrictModel
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.status import FailureCode


ONE_MINUTE_NS = 60_000_000_000
SPOT_MICROSECOND_TRANSITION = date(2025, 1, 1)
NOT_APPLICABLE = "NOT_APPLICABLE"
NOT_AVAILABLE = "NOT_AVAILABLE"
NORMALIZER_VERSION = "binance-public-data-v1-m2.1"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INTEGER = re.compile(r"0|[1-9][0-9]*\Z")
_SPOT_ARCHIVE = re.compile(
    r"/data/spot/(daily|monthly)/klines/[A-Z0-9]+/1m/[A-Za-z0-9._-]+\.zip\Z",
)
_USDM_EXECUTION_ARCHIVE = re.compile(
    r"/data/futures/um/(daily|monthly)/klines/[A-Z0-9]+/1m/[A-Za-z0-9._-]+\.zip\Z",
)
_USDM_MARK_ARCHIVE = re.compile(
    r"/data/futures/um/(daily|monthly)/markPriceKlines/[A-Z0-9]+/1m/[A-Za-z0-9._-]+\.zip\Z",
)
_USDM_FUNDING_ARCHIVE = re.compile(
    r"/data/futures/um/monthly/fundingRate/[A-Z0-9]+/[A-Za-z0-9._-]+\.zip\Z",
)


class DataContractError(ValueError):
    """A fail-closed SSOT data-boundary violation."""

    def __init__(self, code: FailureCode | str, message: str) -> None:
        self.code = code.value if isinstance(code, FailureCode) else code
        super().__init__(f"{self.code}: {message}")


class SourceRole(StrEnum):
    SPOT_EXECUTION_1M = "SPOT_EXECUTION_1M"
    USDM_PERPETUAL_EXECUTION_1M = "USDM_PERPETUAL_EXECUTION_1M"
    USDM_PERPETUAL_MARK_1M = "USDM_PERPETUAL_MARK_1M"
    USDM_PERPETUAL_FUNDING = "USDM_PERPETUAL_FUNDING"
    SPOT_INSTRUMENT_METADATA = "SPOT_INSTRUMENT_METADATA"
    USDM_PERPETUAL_INSTRUMENT_METADATA = "USDM_PERPETUAL_INSTRUMENT_METADATA"
    USDM_PERPETUAL_FUNDING_METADATA = "USDM_PERPETUAL_FUNDING_METADATA"
    USDM_EXECUTION_TIMESTAMP_PROBE = "USDM_EXECUTION_TIMESTAMP_PROBE"
    USDM_MARK_TIMESTAMP_PROBE = "USDM_MARK_TIMESTAMP_PROBE"
    USDM_FUNDING_TIMESTAMP_PROBE = "USDM_FUNDING_TIMESTAMP_PROBE"
    PUBLISHER_CHECKSUM = "PUBLISHER_CHECKSUM"
    BINANCE_PUBLIC_DATA_CONTRACT = "BINANCE_PUBLIC_DATA_CONTRACT"
    BINANCE_OFFICIAL_API_CONTRACT = "BINANCE_OFFICIAL_API_CONTRACT"


class TimestampUnit(StrEnum):
    MILLISECONDS = "MILLISECONDS"
    MICROSECONDS = "MICROSECONDS"


class TimeRange(StrictModel):
    start_inclusive: datetime
    end_exclusive: datetime

    def __post_init__(self) -> None:
        for name in ("start_inclusive", "end_exclusive"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ConfigError(f"time_range.{name}: must use timezone-aware UTC")
        if self.start_inclusive >= self.end_exclusive:
            raise ConfigError("time_range: start must precede end")

    @property
    def start_ns(self) -> int:
        return _datetime_to_ns(self.start_inclusive)

    @property
    def end_ns(self) -> int:
        return _datetime_to_ns(self.end_exclusive)


class AcquisitionRequest(StrictModel):
    source_role: SourceRole
    source_locator: str
    exact_filename: str
    instrument: str
    market_profile: str
    requested_interval: str
    requested_time_range: TimeRange | str

    def __post_init__(self) -> None:
        _validate_source_locator(self.source_role, self.source_locator)
        if not self.exact_filename or Path(self.exact_filename).name != self.exact_filename:
            raise ConfigError("acquisition.exact_filename: must be one stable basename")
        if not self.instrument:
            raise ConfigError("acquisition.instrument: must not be empty")
        valid_profiles = {item.value for item in MarketProfile} | {NOT_APPLICABLE}
        if self.market_profile not in valid_profiles:
            raise ConfigError("acquisition.market_profile: unsupported value")


class RawObjectRecord(StrictModel):
    schema_version: int
    source_role: SourceRole
    source_locator: str
    acquired_at_utc: datetime
    exact_filename: str
    byte_size: int
    sha256: str
    publisher_checksum: str
    instrument: str
    market_profile: str
    requested_interval: str
    requested_time_range: TimeRange | str
    conflicts_with_sha256: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ConfigError("raw_object.schema_version: only version 1 is supported")
        _validate_source_locator(self.source_role, self.source_locator)
        if self.acquired_at_utc.tzinfo is None or self.acquired_at_utc.utcoffset() != timedelta(0):
            raise ConfigError("raw_object.acquired_at_utc: must use UTC")
        if self.byte_size < 0:
            raise ConfigError("raw_object.byte_size: must be non-negative")
        _require_sha256(self.sha256, "raw_object.sha256")
        if self.publisher_checksum != NOT_AVAILABLE:
            _require_sha256(self.publisher_checksum, "raw_object.publisher_checksum")
        for digest in self.conflicts_with_sha256:
            _require_sha256(digest, "raw_object.conflicts_with_sha256")
        if tuple(sorted(set(self.conflicts_with_sha256))) != self.conflicts_with_sha256:
            raise ConfigError("raw_object.conflicts_with_sha256: must be unique and sorted")


class SourceObjectBinding(StrictModel):
    source_role: SourceRole
    source_locator: str
    exact_filename: str
    byte_size: int
    sha256: str
    publisher_checksum: str
    instrument: str
    market_profile: str
    requested_interval: str
    requested_time_range: TimeRange | str

    def __post_init__(self) -> None:
        _validate_source_locator(self.source_role, self.source_locator)
        if self.byte_size < 0:
            raise ConfigError("source_object.byte_size: must be non-negative")
        _require_sha256(self.sha256, "source_object.sha256")
        if self.publisher_checksum != NOT_AVAILABLE:
            _require_sha256(self.publisher_checksum, "source_object.publisher_checksum")

    @classmethod
    def from_raw(cls, record: RawObjectRecord) -> SourceObjectBinding:
        return cls(
            source_role=record.source_role,
            source_locator=record.source_locator,
            exact_filename=record.exact_filename,
            byte_size=record.byte_size,
            sha256=record.sha256,
            publisher_checksum=record.publisher_checksum,
            instrument=record.instrument,
            market_profile=record.market_profile,
            requested_interval=record.requested_interval,
            requested_time_range=record.requested_time_range,
        )


class RoleCompleteness(StrictModel):
    source_role: SourceRole
    expected_count: int
    actual_count: int
    start_inclusive_ns: int
    end_exclusive_ns: int
    status: str

    def __post_init__(self) -> None:
        if self.status != "PASS":
            raise ConfigError("role_completeness.status: accepted releases require PASS")
        if self.expected_count < 0 or self.actual_count != self.expected_count:
            raise ConfigError("role_completeness: count mismatch")
        if self.start_inclusive_ns >= self.end_exclusive_ns:
            raise ConfigError("role_completeness: invalid half-open range")


class CompletenessResult(StrictModel):
    status: str
    no_repairs: bool
    role_results: tuple[RoleCompleteness, ...]

    def __post_init__(self) -> None:
        if self.status != "PASS" or self.no_repairs is not True:
            raise ConfigError("completeness_result: release must pass without repair")
        if not self.role_results:
            raise ConfigError("completeness_result.role_results: must not be empty")


class InstrumentMetadata(StrictModel):
    schema_version: int
    instrument_metadata_identity: str
    market_profile: MarketProfile
    instrument_id: str
    raw_symbol: str
    official_source: str
    source_object_sha256: str
    observed_at_utc: datetime
    historical_exact: bool
    limitations: tuple[str, ...]
    status: str
    base_currency: str
    quote_currency: str
    settlement_currency: str
    contract_type: str
    is_inverse: bool
    price_precision: int
    size_precision: int
    price_increment: Decimal
    size_increment: Decimal
    min_price: Decimal
    max_price: Decimal
    min_quantity: Decimal
    max_quantity: Decimal
    min_notional: Decimal
    max_notional: Decimal | str
    multiplier: Decimal
    margin_init: Decimal | str
    margin_maint: Decimal | str
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal
    fee_rate_basis: str
    official_definition: dict[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ConfigError("instrument_metadata.schema_version: only version 1 is supported")
        _require_sha256(
            self.instrument_metadata_identity,
            "instrument_metadata.instrument_metadata_identity",
        )
        _require_sha256(self.source_object_sha256, "instrument_metadata.source_object_sha256")
        if self.observed_at_utc.tzinfo is None or self.observed_at_utc.utcoffset() != timedelta(0):
            raise ConfigError("instrument_metadata.observed_at_utc: must use UTC")
        if self.historical_exact:
            raise ConfigError(
                "instrument_metadata.historical_exact: current public metadata cannot be labeled historical",
            )
        if not self.limitations:
            raise ConfigError("instrument_metadata.limitations: disclosure is required")
        if self.price_precision < 0 or self.size_precision < 0:
            raise ConfigError("instrument_metadata: precision must be non-negative")
        positive = (
            self.price_increment,
            self.size_increment,
            self.max_price,
            self.max_quantity,
            self.multiplier,
        )
        if any(not item.is_finite() or item <= 0 for item in positive):
            raise ConfigError("instrument_metadata: positive finite limits are required")
        nonnegative = (
            self.min_price,
            self.min_quantity,
            self.min_notional,
            self.maker_fee_rate,
            self.taker_fee_rate,
        )
        if any(not item.is_finite() or item < 0 for item in nonnegative):
            raise ConfigError("instrument_metadata: non-negative finite values are required")
        if not self.fee_rate_basis:
            raise ConfigError("instrument_metadata.fee_rate_basis: must be explicit")
        object.__setattr__(self, "official_definition", _deep_freeze(self.official_definition))
        if canonical_sha256(self._material_payload()) != self.instrument_metadata_identity:
            raise ConfigError("instrument_metadata_identity does not match material payload")

    def _material_payload(self) -> dict[str, Any]:
        payload = self.to_builtins()
        payload.pop("instrument_metadata_identity", None)
        payload.pop("observed_at_utc", None)
        return payload

    @classmethod
    def create(cls, **values: Any) -> InstrumentMetadata:
        material = dict(values)
        material.pop("observed_at_utc", None)
        material["schema_version"] = 1
        identity = canonical_sha256(material)
        return cls(schema_version=1, instrument_metadata_identity=identity, **values)


class FundingSlot(StrictModel):
    event_key: str
    calc_time_ns: int
    funding_interval_hours: int

    def __post_init__(self) -> None:
        _require_sha256(self.event_key, "funding_slot.event_key")
        if self.calc_time_ns < 0 or self.funding_interval_hours <= 0:
            raise ConfigError("funding_slot: invalid time or interval")


class FundingScheduleEvidence(StrictModel):
    schema_version: int
    schedule_identity: str
    instrument_id: str
    source_object_sha256: str
    normalized_time_range: TimeRange
    timestamp_unit: TimestampUnit
    proof_basis: str
    proven: bool
    expected_events: tuple[FundingSlot, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ConfigError("funding_schedule.schema_version: only version 1 is supported")
        _require_sha256(self.schedule_identity, "funding_schedule.schedule_identity")
        _require_sha256(self.source_object_sha256, "funding_schedule.source_object_sha256")
        if not self.proven or not self.expected_events:
            raise ConfigError("funding_schedule: official proof and expected events are required")
        if self.timestamp_unit is not TimestampUnit.MILLISECONDS:
            raise ConfigError("funding_schedule: USD-M funding uses its own millisecond contract")
        keys = tuple(item.event_key for item in self.expected_events)
        if len(keys) != len(set(keys)):
            raise ConfigError("funding_schedule: duplicate expected event keys")
        times = tuple(item.calc_time_ns for item in self.expected_events)
        if times != tuple(sorted(times)):
            raise ConfigError("funding_schedule: events must be ordered")
        if canonical_sha256(self._material_payload()) != self.schedule_identity:
            raise ConfigError("funding_schedule.schedule_identity mismatch")

    def _material_payload(self) -> dict[str, Any]:
        payload = self.to_builtins()
        payload.pop("schedule_identity", None)
        return payload


class DatasetRelease(StrictModel):
    """The only stable M2 object exported to downstream phases."""

    schema_version: int
    dataset_release_id: str
    market_profile: MarketProfile
    instrument_id: str
    source_objects: tuple[SourceObjectBinding, ...]
    normalized_time_range: TimeRange
    execution_bar_interval: str
    available_signal_bar_intervals: tuple[TimeRange, ...]
    instrument_metadata_identity: str
    funding_data_identity: str
    mark_data_identity: str
    normalizer_version: str
    timestamp_rules_identity: str
    catalog_identity: str
    completeness_result: CompletenessResult
    created_at_utc: datetime

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ConfigError("dataset_release.schema_version: only version 1 is supported")
        for name in (
            "dataset_release_id",
            "instrument_metadata_identity",
            "timestamp_rules_identity",
            "catalog_identity",
        ):
            _require_sha256(getattr(self, name), f"dataset_release.{name}")
        if self.created_at_utc.tzinfo is None or self.created_at_utc.utcoffset() != timedelta(0):
            raise ConfigError("dataset_release.created_at_utc: must use UTC")
        if self.execution_bar_interval != "1m":
            raise ConfigError("dataset_release.execution_bar_interval: V1 requires 1m")
        if self.normalizer_version != NORMALIZER_VERSION:
            raise ConfigError("dataset_release.normalizer_version: unsupported version")
        if not self.source_objects or not self.available_signal_bar_intervals:
            raise ConfigError("dataset_release: source objects and signal intervals are required")
        ordering = tuple(
            sorted(
                self.source_objects,
                key=lambda item: (item.source_role.value, item.source_locator, item.sha256),
            ),
        )
        if ordering != self.source_objects:
            raise ConfigError("dataset_release.source_objects: canonical ordering required")
        roles = {item.source_role for item in self.source_objects}
        if self.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
            required = {SourceRole.SPOT_EXECUTION_1M, SourceRole.SPOT_INSTRUMENT_METADATA}
            forbidden = {
                SourceRole.USDM_PERPETUAL_EXECUTION_1M,
                SourceRole.USDM_PERPETUAL_MARK_1M,
                SourceRole.USDM_PERPETUAL_FUNDING,
                SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA,
            }
            if not required.issubset(roles) or roles & forbidden:
                raise ConfigError("dataset_release: Spot source roles are invalid")
            if self.funding_data_identity != NOT_APPLICABLE or self.mark_data_identity != NOT_APPLICABLE:
                raise ConfigError("dataset_release: Spot forbids funding and mark identities")
        else:
            required = {
                SourceRole.USDM_PERPETUAL_EXECUTION_1M,
                SourceRole.USDM_PERPETUAL_MARK_1M,
                SourceRole.USDM_PERPETUAL_FUNDING,
                SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA,
            }
            forbidden = {SourceRole.SPOT_EXECUTION_1M, SourceRole.SPOT_INSTRUMENT_METADATA}
            if not required.issubset(roles) or roles & forbidden:
                raise ConfigError("dataset_release: Perpetual source roles are invalid")
            _require_sha256(self.funding_data_identity, "dataset_release.funding_data_identity")
            _require_sha256(self.mark_data_identity, "dataset_release.mark_data_identity")
        if canonical_sha256(self.material_payload()) != self.dataset_release_id:
            raise ConfigError("dataset_release_id does not match canonical material payload")

    def material_payload(self) -> dict[str, Any]:
        payload = self.to_builtins()
        payload.pop("dataset_release_id", None)
        payload.pop("created_at_utc", None)
        return payload

    def with_created_at(self, value: datetime) -> DatasetRelease:
        return replace(self, created_at_utc=value)


@dataclass(frozen=True)
class NormalizedBar:
    source_role: SourceRole
    instrument_id: str
    interval_start_ns: int
    interval_end_exclusive_ns: int
    available_at_ns: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source_row_number: int
    source_row_sha256: str

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "source_role": self.source_role.value,
            "instrument_id": self.instrument_id,
            "interval_start_ns": self.interval_start_ns,
            "interval_end_exclusive_ns": self.interval_end_exclusive_ns,
            "available_at_ns": self.available_at_ns,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True)
class FundingEvent:
    instrument_id: str
    calc_time_ns: int
    funding_interval_hours: int
    funding_rate: Decimal
    source_row_number: int
    source_row_sha256: str
    event_key: str

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "event_key": self.event_key,
            "instrument_id": self.instrument_id,
            "calc_time_ns": self.calc_time_ns,
            "funding_interval_hours": self.funding_interval_hours,
            "funding_rate": self.funding_rate,
        }


@dataclass(frozen=True)
class CatalogBuildResult:
    catalog_identity: str
    semantic_inventory: dict[str, Any]
    instrument: CurrencyPair | CryptoPerpetual
    execution_bars: tuple[Bar, ...]
    mark_updates: tuple[MarkPriceUpdate, ...]
    funding_updates: tuple[FundingRateUpdate, ...]


def _require_sha256(value: str, path: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ConfigError(f"{path}: must be a lowercase SHA-256")


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _datetime_to_ns(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise DataContractError(FailureCode.DATA_TIMESTAMP_INVALID, "timestamp must use UTC")
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return (
        delta.days * 86_400_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def _ns_to_datetime(value: int) -> datetime:
    if value % 1_000:
        raise DataContractError(
            FailureCode.DATA_TIMESTAMP_INVALID,
            "timestamp cannot be represented by the UTC evidence schema",
        )
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value // 1_000)


def _validate_source_locator(role: SourceRole, locator: str) -> None:
    parsed = urlparse(locator)
    if parsed.scheme != "https" or parsed.params or parsed.query and role not in {
        SourceRole.SPOT_INSTRUMENT_METADATA,
        SourceRole.BINANCE_OFFICIAL_API_CONTRACT,
        SourceRole.USDM_EXECUTION_TIMESTAMP_PROBE,
        SourceRole.USDM_MARK_TIMESTAMP_PROBE,
        SourceRole.USDM_FUNDING_TIMESTAMP_PROBE,
    }:
        raise ConfigError("source_locator: HTTPS official source required")
    path = parsed.path
    valid = False
    if role is SourceRole.SPOT_EXECUTION_1M:
        valid = parsed.netloc == "data.binance.vision" and bool(_SPOT_ARCHIVE.fullmatch(path))
    elif role is SourceRole.USDM_PERPETUAL_EXECUTION_1M:
        valid = parsed.netloc == "data.binance.vision" and bool(
            _USDM_EXECUTION_ARCHIVE.fullmatch(path),
        )
    elif role is SourceRole.USDM_PERPETUAL_MARK_1M:
        valid = parsed.netloc == "data.binance.vision" and bool(_USDM_MARK_ARCHIVE.fullmatch(path))
    elif role is SourceRole.USDM_PERPETUAL_FUNDING:
        valid = parsed.netloc == "data.binance.vision" and bool(
            _USDM_FUNDING_ARCHIVE.fullmatch(path),
        )
    elif role is SourceRole.PUBLISHER_CHECKSUM:
        valid = parsed.netloc == "data.binance.vision" and path.endswith(".zip.CHECKSUM")
    elif role is SourceRole.SPOT_INSTRUMENT_METADATA:
        valid = (
            parsed.netloc == "api.binance.com"
            and path == "/api/v3/exchangeInfo"
            and parsed.query == "symbol=BTCUSDT"
        )
    elif role is SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA:
        valid = parsed.netloc == "fapi.binance.com" and path == "/fapi/v1/exchangeInfo"
    elif role is SourceRole.USDM_PERPETUAL_FUNDING_METADATA:
        valid = parsed.netloc == "fapi.binance.com" and path == "/fapi/v1/fundingInfo"
    elif role is SourceRole.USDM_EXECUTION_TIMESTAMP_PROBE:
        valid = (
            parsed.netloc == "fapi.binance.com"
            and path == "/fapi/v1/klines"
            and parsed.query
            == "symbol=BTCUSDT&interval=1m&startTime=1735689600000&endTime=1735689659999&limit=1"
        )
    elif role is SourceRole.USDM_MARK_TIMESTAMP_PROBE:
        valid = (
            parsed.netloc == "fapi.binance.com"
            and path == "/fapi/v1/markPriceKlines"
            and parsed.query
            == "symbol=BTCUSDT&interval=1m&startTime=1735689600000&endTime=1735689659999&limit=1"
        )
    elif role is SourceRole.USDM_FUNDING_TIMESTAMP_PROBE:
        valid = (
            parsed.netloc == "fapi.binance.com"
            and path == "/fapi/v1/fundingRate"
            and parsed.query
            == "symbol=BTCUSDT&startTime=1735689600000&endTime=1735689601000&limit=10"
        )
    elif role is SourceRole.BINANCE_PUBLIC_DATA_CONTRACT:
        valid = (
            parsed.netloc == "raw.githubusercontent.com"
            and path == "/binance/binance-public-data/master/README.md"
        )
    elif role is SourceRole.BINANCE_OFFICIAL_API_CONTRACT:
        valid = (
            parsed.netloc == "developers.binance.com" and path.startswith("/docs/")
        ) or (
            parsed.netloc == "raw.githubusercontent.com" and path.startswith("/binance/")
        ) or (
            parsed.netloc == "api.github.com" and path.startswith("/repos/binance/")
        )
    if not valid:
        raise ConfigError(f"source_locator: locator is invalid for role {role.value}")


def timestamp_unit_for(source_role: SourceRole, *, source_date: date) -> TimestampUnit:
    """Resolve the unit from the official role/date contract, never magnitude."""

    if source_role is SourceRole.SPOT_EXECUTION_1M:
        return (
            TimestampUnit.MILLISECONDS
            if source_date < SPOT_MICROSECOND_TRANSITION
            else TimestampUnit.MICROSECONDS
        )
    if source_role in {
        SourceRole.USDM_PERPETUAL_EXECUTION_1M,
        SourceRole.USDM_PERPETUAL_MARK_1M,
        SourceRole.USDM_PERPETUAL_FUNDING,
    }:
        return TimestampUnit.MILLISECONDS
    raise DataContractError(
        FailureCode.DATA_TIMESTAMP_INVALID,
        f"role {source_role.value} has no market timestamp contract",
    )


def _timestamp_to_ns(raw: str, unit: TimestampUnit) -> int:
    if _INTEGER.fullmatch(raw) is None:
        raise DataContractError(FailureCode.DATA_TIMESTAMP_INVALID, f"invalid timestamp {raw!r}")
    value = int(raw)
    multiplier = 1_000_000 if unit is TimestampUnit.MILLISECONDS else 1_000
    return value * multiplier


def _decimal(raw: str, field: str) -> Decimal:
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise DataContractError(FailureCode.DATA_SOURCE_INVALID, f"malformed {field}") from exc
    if not value.is_finite():
        raise DataContractError(FailureCode.DATA_SOURCE_INVALID, f"non-finite {field}")
    return value


def _csv_rows(payload: bytes) -> list[list[str]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DataContractError(FailureCode.DATA_SOURCE_INVALID, "CSV is not UTF-8") from exc
    try:
        return list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as exc:
        raise DataContractError(FailureCode.DATA_SOURCE_INVALID, "malformed CSV") from exc


def extract_single_csv_archive(payload: bytes, *, expected_filename: str) -> bytes:
    """Extract one exact CSV member after the archive bytes are frozen."""

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) != 1:
                raise DataContractError(
                    FailureCode.DATA_SOURCE_INVALID,
                    "official archive must contain exactly one member",
                )
            member = members[0]
            if (
                member.is_dir()
                or member.filename != expected_filename
                or Path(member.filename).name != member.filename
            ):
                raise DataContractError(
                    FailureCode.DATA_SOURCE_INVALID,
                    "archive member identity mismatch",
                )
            return archive.read(member)
    except DataContractError:
        raise
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise DataContractError(FailureCode.DATA_SOURCE_INVALID, "invalid ZIP archive") from exc


def parse_kline_csv(
    payload: bytes,
    *,
    source_role: SourceRole,
    instrument_id: str,
    market_profile: MarketProfile,
    source_date: date,
) -> tuple[NormalizedBar, ...]:
    if source_role not in {
        SourceRole.SPOT_EXECUTION_1M,
        SourceRole.USDM_PERPETUAL_EXECUTION_1M,
        SourceRole.USDM_PERPETUAL_MARK_1M,
    }:
        raise DataContractError(FailureCode.DATA_ROLE_MISMATCH, "role is not a kline role")
    if source_role is SourceRole.SPOT_EXECUTION_1M:
        if market_profile is not MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
            raise DataContractError(FailureCode.DATA_ROLE_MISMATCH, "Spot row in non-Spot profile")
    elif market_profile is not MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
        raise DataContractError(FailureCode.DATA_ROLE_MISMATCH, "USD-M row in non-Perpetual profile")

    unit = timestamp_unit_for(source_role, source_date=source_date)
    unit_ns = 1_000_000 if unit is TimestampUnit.MILLISECONDS else 1_000
    rows = _csv_rows(payload)
    expected_header = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "count",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "ignore",
    ]
    start_row_number = 1
    if source_role is not SourceRole.SPOT_EXECUTION_1M:
        if not rows or rows[0] != expected_header:
            raise DataContractError(FailureCode.DATA_SOURCE_INVALID, "USD-M kline header mismatch")
        rows = rows[1:]
        start_row_number = 2
    elif rows and rows[0] == expected_header:
        raise DataContractError(FailureCode.DATA_SOURCE_INVALID, "Spot archive unexpectedly has a header")
    if not rows:
        raise DataContractError(FailureCode.DATA_GAP, "kline object has no rows")

    result: list[NormalizedBar] = []
    seen: dict[int, str] = {}
    prior: int | None = None
    for offset, row in enumerate(rows):
        row_number = start_row_number + offset
        if len(row) != 12:
            raise DataContractError(
                FailureCode.DATA_SOURCE_INVALID,
                f"row {row_number}: expected 12 fields",
            )
        start_ns = _timestamp_to_ns(row[0], unit)
        close_ns = _timestamp_to_ns(row[6], unit)
        end_ns = start_ns + ONE_MINUTE_NS
        if close_ns != end_ns - unit_ns:
            raise DataContractError(
                FailureCode.DATA_TIMESTAMP_INVALID,
                f"row {row_number}: close time is not the one-minute inclusive endpoint",
            )
        values = [_decimal(row[index], name) for index, name in zip(
            (1, 2, 3, 4, 5),
            ("open", "high", "low", "close", "volume"),
            strict=True,
        )]
        open_, high, low, close, volume = values
        if high < max(open_, close, low) or low > min(open_, close, high):
            raise DataContractError(
                FailureCode.DATA_SOURCE_INVALID,
                f"row {row_number}: invalid OHLC relationship",
            )
        if volume < 0:
            raise DataContractError(
                FailureCode.DATA_SOURCE_INVALID,
                f"row {row_number}: negative volume",
            )
        for index, name in ((7, "quote_volume"), (9, "taker_buy_volume"), (10, "taker_buy_quote")):
            if _decimal(row[index], name) < 0:
                raise DataContractError(
                    FailureCode.DATA_SOURCE_INVALID,
                    f"row {row_number}: negative {name}",
                )
        if _INTEGER.fullmatch(row[8]) is None:
            raise DataContractError(FailureCode.DATA_SOURCE_INVALID, f"row {row_number}: invalid count")
        row_bytes = (",".join(row) + "\n").encode("utf-8")
        row_hash = hashlib.sha256(row_bytes).hexdigest()
        if start_ns in seen:
            raise DataContractError(
                FailureCode.DATA_DUPLICATE_CONFLICT,
                f"row {row_number}: duplicate minute conflicts with {seen[start_ns]}",
            )
        if prior is not None and start_ns <= prior:
            raise DataContractError(
                FailureCode.DATA_TIMESTAMP_INVALID,
                f"row {row_number}: non-monotonic timestamp",
            )
        seen[start_ns] = row_hash
        prior = start_ns
        result.append(
            NormalizedBar(
                source_role=source_role,
                instrument_id=instrument_id,
                interval_start_ns=start_ns,
                interval_end_exclusive_ns=end_ns,
                available_at_ns=end_ns,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                source_row_number=row_number,
                source_row_sha256=row_hash,
            ),
        )
    return tuple(result)


def validate_one_minute_grid(
    bars: tuple[NormalizedBar, ...] | list[NormalizedBar],
    *,
    source_role: SourceRole,
    time_range: TimeRange,
) -> RoleCompleteness:
    if time_range.start_ns % ONE_MINUTE_NS or time_range.end_ns % ONE_MINUTE_NS:
        raise DataContractError(FailureCode.DATA_TIMESTAMP_INVALID, "grid endpoints are not minute aligned")
    selected = [
        item
        for item in bars
        if time_range.start_ns <= item.interval_start_ns < time_range.end_ns
    ]
    if any(item.source_role is not source_role for item in selected):
        raise DataContractError(FailureCode.DATA_ROLE_MISMATCH, "grid contains a different source role")
    by_time: dict[int, NormalizedBar] = {}
    for item in selected:
        if item.interval_start_ns in by_time:
            raise DataContractError(
                FailureCode.DATA_DUPLICATE_CONFLICT,
                f"duplicate minute {item.interval_start_ns}",
            )
        by_time[item.interval_start_ns] = item
    expected = tuple(range(time_range.start_ns, time_range.end_ns, ONE_MINUTE_NS))
    missing = [value for value in expected if value not in by_time]
    extras = sorted(set(by_time) - set(expected))
    if missing or extras:
        raise DataContractError(
            FailureCode.DATA_GAP,
            f"one-minute grid mismatch missing={missing} extras={extras}",
        )
    ordered = [by_time[value] for value in expected]
    if any(
        item.available_at_ns != item.interval_end_exclusive_ns
        or item.interval_end_exclusive_ns != item.interval_start_ns + ONE_MINUTE_NS
        for item in ordered
    ):
        raise DataContractError(
            FailureCode.DATA_TIMESTAMP_INVALID,
            "available_at is not the completion boundary",
        )
    return RoleCompleteness(
        source_role=source_role,
        expected_count=len(expected),
        actual_count=len(ordered),
        start_inclusive_ns=time_range.start_ns,
        end_exclusive_ns=time_range.end_ns,
        status="PASS",
    )


def parse_funding_csv(payload: bytes, *, instrument_id: str) -> tuple[FundingEvent, ...]:
    rows = _csv_rows(payload)
    expected_header = ["calc_time", "funding_interval_hours", "last_funding_rate"]
    if not rows or rows[0] != expected_header:
        raise DataContractError(FailureCode.DATA_SOURCE_INVALID, "funding header mismatch")
    result: list[FundingEvent] = []
    seen_times: dict[int, str] = {}
    prior: int | None = None
    for row_number, row in enumerate(rows[1:], start=2):
        if len(row) != 3:
            raise DataContractError(
                FailureCode.DATA_SOURCE_INVALID,
                f"funding row {row_number}: expected 3 fields",
            )
        calc_time_ns = _timestamp_to_ns(row[0], TimestampUnit.MILLISECONDS)
        if _INTEGER.fullmatch(row[1]) is None or int(row[1]) <= 0:
            raise DataContractError(
                FailureCode.FUNDING_AMBIGUOUS,
                f"funding row {row_number}: invalid explicit interval",
            )
        interval = int(row[1])
        rate = _decimal(row[2], "funding_rate")
        row_hash = hashlib.sha256((",".join(row) + "\n").encode("utf-8")).hexdigest()
        event_key = canonical_sha256(
            {
                "instrument_id": instrument_id,
                "calc_time_ns": calc_time_ns,
                "funding_interval_hours": interval,
                "funding_rate": rate,
            },
        )
        if calc_time_ns in seen_times:
            raise DataContractError(
                FailureCode.FUNDING_AMBIGUOUS,
                f"funding row {row_number}: duplicate timestamp conflicts with {seen_times[calc_time_ns]}",
            )
        if prior is not None and calc_time_ns <= prior:
            raise DataContractError(
                FailureCode.DATA_TIMESTAMP_INVALID,
                f"funding row {row_number}: non-monotonic timestamp",
            )
        seen_times[calc_time_ns] = event_key
        prior = calc_time_ns
        result.append(
            FundingEvent(
                instrument_id=instrument_id,
                calc_time_ns=calc_time_ns,
                funding_interval_hours=interval,
                funding_rate=rate,
                source_row_number=row_number,
                source_row_sha256=row_hash,
                event_key=event_key,
            ),
        )
    if not result:
        raise DataContractError(FailureCode.FUNDING_AMBIGUOUS, "funding object has no events")
    return tuple(result)


def prove_funding_schedule(
    events: tuple[FundingEvent, ...],
    *,
    source_object_sha256: str,
    time_range: TimeRange,
) -> FundingScheduleEvidence:
    _require_sha256(source_object_sha256, "funding_schedule.source_object_sha256")
    expected = tuple(
        FundingSlot(
            event_key=item.event_key,
            calc_time_ns=item.calc_time_ns,
            funding_interval_hours=item.funding_interval_hours,
        )
        for item in events
        if time_range.start_ns <= item.calc_time_ns < time_range.end_ns
    )
    if not expected:
        raise DataContractError(
            FailureCode.FUNDING_AMBIGUOUS,
            "official funding evidence proves no event in the requested interval",
        )
    material = {
        "schema_version": 1,
        "instrument_id": events[0].instrument_id,
        "source_object_sha256": source_object_sha256,
        "normalized_time_range": time_range,
        "timestamp_unit": TimestampUnit.MILLISECONDS,
        "proof_basis": "OFFICIAL_BINANCE_FUNDING_ARCHIVE_EXPLICIT_INTERVAL_ROWS",
        "proven": True,
        "expected_events": expected,
    }
    return FundingScheduleEvidence(schedule_identity=canonical_sha256(material), **material)


def validate_funding_schedule(
    events: tuple[FundingEvent, ...] | list[FundingEvent],
    schedule: FundingScheduleEvidence | None,
) -> str:
    if schedule is None or not schedule.proven:
        raise DataContractError(FailureCode.FUNDING_AMBIGUOUS, "funding schedule is unproven")
    selected = [
        item
        for item in events
        if schedule.normalized_time_range.start_ns
        <= item.calc_time_ns
        < schedule.normalized_time_range.end_ns
    ]
    by_key: dict[str, list[FundingEvent]] = {}
    by_time: dict[int, list[FundingEvent]] = {}
    for item in selected:
        by_key.setdefault(item.event_key, []).append(item)
        by_time.setdefault(item.calc_time_ns, []).append(item)
    if any(len(items) != 1 for items in by_time.values()):
        raise DataContractError(FailureCode.FUNDING_AMBIGUOUS, "conflicting funding duplicate")
    expected_keys = {item.event_key for item in schedule.expected_events}
    missing = [item.event_key for item in schedule.expected_events if len(by_key.get(item.event_key, [])) != 1]
    if missing:
        raise DataContractError(FailureCode.FUNDING_MISSING, f"required funding events missing: {missing}")
    unexpected = [item.event_key for item in selected if item.event_key not in expected_keys]
    if unexpected:
        raise DataContractError(
            FailureCode.FUNDING_AMBIGUOUS,
            f"unexpected funding events compete with proven schedule: {unexpected}",
        )
    return canonical_sha256(
        {
            "schedule_identity": schedule.schedule_identity,
            "events": [item.semantic_payload() for item in selected],
        },
    )


def parse_publisher_checksum(checksum_bytes: bytes, *, exact_filename: str) -> str:
    try:
        value = checksum_bytes.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise DataContractError(FailureCode.DATA_HASH_MISMATCH, "checksum is not ASCII") from exc
    match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", value)
    if match is None or match.group(2) != exact_filename:
        raise DataContractError(
            FailureCode.DATA_HASH_MISMATCH,
            "publisher checksum filename or format mismatch",
        )
    return match.group(1)


def verify_publisher_checksum(
    payload: bytes,
    checksum_bytes: bytes,
    *,
    exact_filename: str,
) -> str:
    expected = parse_publisher_checksum(checksum_bytes, exact_filename=exact_filename)
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise DataContractError(
            FailureCode.DATA_HASH_MISMATCH,
            f"publisher={expected} local={actual}",
        )
    return actual


class RawObjectStore:
    """Content-addressed immutable raw bytes plus additive locator observations."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.blobs = self.root / "sha256"
        self.observations = self.root / "observations"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.observations.mkdir(parents=True, exist_ok=True)

    def blob_path(self, digest: str) -> Path:
        _require_sha256(digest, "raw_store.digest")
        return self.blobs / digest[:2] / f"{digest}.blob"

    @staticmethod
    def _exclusive_write(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise DataContractError(
                    FailureCode.DATA_HASH_MISMATCH,
                    f"immutable path collision at {path}",
                )
            return
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            raise

    def store_bytes(
        self,
        payload: bytes,
        *,
        request: AcquisitionRequest,
        acquired_at_utc: datetime,
        publisher_checksum: str = NOT_AVAILABLE,
    ) -> RawObjectRecord:
        digest = hashlib.sha256(payload).hexdigest()
        if publisher_checksum != NOT_AVAILABLE:
            _require_sha256(publisher_checksum, "raw_store.publisher_checksum")
        blob = self.blob_path(digest)
        self._exclusive_write(blob, payload)
        locator_key = hashlib.sha256(request.source_locator.encode("utf-8")).hexdigest()
        locator_dir = self.observations / locator_key
        existing: list[str] = []
        if locator_dir.is_dir():
            for path in sorted(locator_dir.glob("*.json")):
                try:
                    prior = RawObjectRecord.from_json_bytes(path.read_bytes())
                except Exception as exc:
                    raise DataContractError(
                        FailureCode.DATA_SOURCE_INVALID,
                        f"invalid prior raw observation {path}",
                    ) from exc
                if prior.sha256 != digest:
                    existing.append(prior.sha256)
        record = RawObjectRecord(
            schema_version=1,
            source_role=request.source_role,
            source_locator=request.source_locator,
            acquired_at_utc=acquired_at_utc,
            exact_filename=request.exact_filename,
            byte_size=len(payload),
            sha256=digest,
            publisher_checksum=publisher_checksum,
            instrument=request.instrument,
            market_profile=request.market_profile,
            requested_interval=request.requested_interval,
            requested_time_range=request.requested_time_range,
            conflicts_with_sha256=tuple(sorted(set(existing))),
        )
        observation_payload = record.to_json_bytes()
        observation_id = hashlib.sha256(observation_payload).hexdigest()
        self._exclusive_write(locator_dir / f"{observation_id}.json", observation_payload)
        return record

    def read_bytes(self, digest: str) -> bytes:
        payload = self.blob_path(digest).read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise DataContractError(FailureCode.DATA_HASH_MISMATCH, "raw blob hash mismatch")
        return payload


class OfficialBinanceAcquirer:
    """Explicit setup-time network acquisition; never called by an Official Run."""

    def __init__(self, store: RawObjectStore, fetch_bytes: Any = None) -> None:
        self.store = store
        self.fetch_bytes = fetch_bytes or self._fetch

    @staticmethod
    def _fetch(locator: str) -> bytes:
        request = urllib.request.Request(locator, headers={"User-Agent": "nautilus-crypto-lab-m2/1"})
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - allowlisted first
            return response.read()

    def acquire(
        self,
        request: AcquisitionRequest,
        *,
        acquired_at_utc: datetime,
        checksum_request: AcquisitionRequest | None = None,
    ) -> tuple[RawObjectRecord, RawObjectRecord | None]:
        _validate_source_locator(request.source_role, request.source_locator)
        payload = self.fetch_bytes(request.source_locator)
        checksum_record: RawObjectRecord | None = None
        publisher_checksum = NOT_AVAILABLE
        checksum_bytes: bytes | None = None
        if checksum_request is not None:
            if checksum_request.source_role is not SourceRole.PUBLISHER_CHECKSUM:
                raise DataContractError(
                    FailureCode.DATA_ROLE_MISMATCH,
                    "checksum request has the wrong role",
                )
            checksum_bytes = self.fetch_bytes(checksum_request.source_locator)
            publisher_checksum = parse_publisher_checksum(
                checksum_bytes,
                exact_filename=request.exact_filename,
            )
            checksum_record = self.store.store_bytes(
                checksum_bytes,
                request=checksum_request,
                acquired_at_utc=acquired_at_utc,
            )
        raw_record = self.store.store_bytes(
            payload,
            request=request,
            acquired_at_utc=acquired_at_utc,
            publisher_checksum=publisher_checksum,
        )
        if checksum_bytes is not None:
            verify_publisher_checksum(payload, checksum_bytes, exact_filename=request.exact_filename)
        return raw_record, checksum_record


def _strict_json(payload: bytes) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise DataContractError(
                    FailureCode.DATA_SOURCE_INVALID,
                    f"duplicate JSON field {key!r}",
                )
            result[key] = value
        return result

    try:
        value = json.loads(payload, object_pairs_hook=pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except DataContractError:
        raise
    except Exception as exc:
        raise DataContractError(FailureCode.DATA_SOURCE_INVALID, "invalid metadata JSON") from exc
    if not isinstance(value, dict):
        raise DataContractError(FailureCode.DATA_SOURCE_INVALID, "metadata root must be an object")
    return value


def _filter(definition: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [item for item in definition.get("filters", []) if item.get("filterType") == name]
    if len(matches) != 1:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            f"expected exactly one {name} filter",
        )
    return matches[0]


def _precision(increment: Decimal) -> int:
    if not increment.is_finite() or increment <= 0:
        raise DataContractError(FailureCode.INSTRUMENT_METADATA_INVALID, "invalid increment")
    normalized = increment.normalize()
    return max(0, -normalized.as_tuple().exponent)


def _official_server_time(value: Any) -> datetime:
    if type(value) is not int or value < 0:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "official exchangeInfo serverTime is invalid",
        )
    return _ns_to_datetime(value * 1_000_000)


def parse_spot_instrument_metadata(
    payload: bytes,
    *,
    source_object_sha256: str,
    maker_fee_rate: Decimal,
    taker_fee_rate: Decimal,
    fee_rate_basis: str,
) -> InstrumentMetadata:
    data = _strict_json(payload)
    definitions = data.get("symbols")
    if not isinstance(definitions, list):
        raise DataContractError(FailureCode.INSTRUMENT_METADATA_INVALID, "symbols missing")
    matches = [item for item in definitions if item.get("symbol") == "BTCUSDT"]
    if len(matches) != 1:
        raise DataContractError(FailureCode.INSTRUMENT_METADATA_INVALID, "BTCUSDT definition ambiguous")
    definition = matches[0]
    price = _filter(definition, "PRICE_FILTER")
    lot = _filter(definition, "LOT_SIZE")
    notional = _filter(definition, "NOTIONAL")
    increment = _decimal(price["tickSize"], "tickSize")
    size_increment = _decimal(lot["stepSize"], "stepSize")
    values = {
        "market_profile": MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        "instrument_id": "BTCUSDT.BINANCE",
        "raw_symbol": "BTCUSDT",
        "official_source": "https://api.binance.com/api/v3/exchangeInfo?symbol=BTCUSDT",
        "source_object_sha256": source_object_sha256,
        "observed_at_utc": _official_server_time(data.get("serverTime")),
        "historical_exact": False,
        "limitations": (
            "CURRENT_METADATA_OBSERVED_AFTER_QUALIFICATION_INTERVAL",
            "EXACT_HISTORICAL_VENUE_RULES_UNAVAILABLE",
            "ACCOUNT_SPECIFIC_HISTORICAL_FEE_TIER_UNAVAILABLE",
        ),
        "status": str(definition.get("status")),
        "base_currency": str(definition.get("baseAsset")),
        "quote_currency": str(definition.get("quoteAsset")),
        "settlement_currency": str(definition.get("quoteAsset")),
        "contract_type": "SPOT",
        "is_inverse": False,
        "price_precision": _precision(increment),
        "size_precision": _precision(size_increment),
        "price_increment": increment,
        "size_increment": size_increment,
        "min_price": _decimal(price["minPrice"], "minPrice"),
        "max_price": _decimal(price["maxPrice"], "maxPrice"),
        "min_quantity": _decimal(lot["minQty"], "minQty"),
        "max_quantity": _decimal(lot["maxQty"], "maxQty"),
        "min_notional": _decimal(notional["minNotional"], "minNotional"),
        "max_notional": _decimal(notional["maxNotional"], "maxNotional"),
        "multiplier": Decimal("1"),
        "margin_init": NOT_APPLICABLE,
        "margin_maint": NOT_APPLICABLE,
        "maker_fee_rate": maker_fee_rate,
        "taker_fee_rate": taker_fee_rate,
        "fee_rate_basis": fee_rate_basis,
        "official_definition": definition,
    }
    return InstrumentMetadata.create(**values)


def parse_usdm_instrument_metadata(
    payload: bytes,
    *,
    source_object_sha256: str,
    maker_fee_rate: Decimal,
    taker_fee_rate: Decimal,
    fee_rate_basis: str,
) -> InstrumentMetadata:
    data = _strict_json(payload)
    definitions = data.get("symbols")
    if not isinstance(definitions, list):
        raise DataContractError(FailureCode.INSTRUMENT_METADATA_INVALID, "symbols missing")
    matches = [item for item in definitions if item.get("symbol") == "BTCUSDT"]
    if len(matches) != 1:
        raise DataContractError(FailureCode.INSTRUMENT_METADATA_INVALID, "BTCUSDT definition ambiguous")
    definition = matches[0]
    if definition.get("contractType") != "PERPETUAL" or definition.get("marginAsset") != "USDT":
        raise DataContractError(FailureCode.INSTRUMENT_METADATA_INVALID, "not linear USDT perpetual")
    price = _filter(definition, "PRICE_FILTER")
    lot = _filter(definition, "LOT_SIZE")
    notional = _filter(definition, "MIN_NOTIONAL")
    increment = _decimal(price["tickSize"], "tickSize")
    size_increment = _decimal(lot["stepSize"], "stepSize")
    values = {
        "market_profile": MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        "instrument_id": "BTCUSDT-PERP.BINANCE",
        "raw_symbol": "BTCUSDT",
        "official_source": "https://fapi.binance.com/fapi/v1/exchangeInfo",
        "source_object_sha256": source_object_sha256,
        "observed_at_utc": _official_server_time(data.get("serverTime")),
        "historical_exact": False,
        "limitations": (
            "CURRENT_METADATA_OBSERVED_AFTER_QUALIFICATION_INTERVAL",
            "EXACT_HISTORICAL_VENUE_RULES_UNAVAILABLE",
            "ACCOUNT_SPECIFIC_HISTORICAL_FEE_TIER_UNAVAILABLE",
        ),
        "status": str(definition.get("status")),
        "base_currency": str(definition.get("baseAsset")),
        "quote_currency": str(definition.get("quoteAsset")),
        "settlement_currency": str(definition.get("marginAsset")),
        "contract_type": str(definition.get("contractType")),
        "is_inverse": False,
        "price_precision": _precision(increment),
        "size_precision": _precision(size_increment),
        "price_increment": increment,
        "size_increment": size_increment,
        "min_price": _decimal(price["minPrice"], "minPrice"),
        "max_price": _decimal(price["maxPrice"], "maxPrice"),
        "min_quantity": _decimal(lot["minQty"], "minQty"),
        "max_quantity": _decimal(lot["maxQty"], "maxQty"),
        "min_notional": _decimal(notional["notional"], "minNotional"),
        "max_notional": NOT_APPLICABLE,
        "multiplier": Decimal("1"),
        "margin_init": _decimal(str(definition["requiredMarginPercent"]), "margin_init") / 100,
        "margin_maint": _decimal(str(definition["maintMarginPercent"]), "margin_maint") / 100,
        "maker_fee_rate": maker_fee_rate,
        "taker_fee_rate": taker_fee_rate,
        "fee_rate_basis": fee_rate_basis,
        "official_definition": definition,
    }
    return InstrumentMetadata.create(**values)


def _decimal_string(value: Decimal, precision: int, *, field: str) -> str:
    quantum = Decimal(1).scaleb(-precision)
    quantized = value.quantize(quantum)
    if quantized != value:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            f"{field}={value} is incompatible with precision {precision}",
        )
    return f"{quantized:.{precision}f}"


def to_nautilus_instrument(metadata: InstrumentMetadata) -> CurrencyPair | CryptoPerpetual:
    instrument_id = InstrumentId.from_str(metadata.instrument_id)
    raw_symbol = Symbol(metadata.raw_symbol)
    base = Currency.from_str(metadata.base_currency)
    quote = Currency.from_str(metadata.quote_currency)
    observed_ns = _datetime_to_ns(metadata.observed_at_utc)
    common = {
        "instrument_id": instrument_id,
        "raw_symbol": raw_symbol,
        "base_currency": base,
        "quote_currency": quote,
        "price_precision": metadata.price_precision,
        "size_precision": metadata.size_precision,
        "price_increment": Price.from_str(
            _decimal_string(metadata.price_increment, metadata.price_precision, field="price_increment"),
        ),
        "size_increment": Quantity.from_str(
            _decimal_string(metadata.size_increment, metadata.size_precision, field="size_increment"),
        ),
        "ts_event": observed_ns,
        "ts_init": observed_ns,
        "multiplier": Quantity.from_str("1"),
        "max_quantity": Quantity.from_str(
            _decimal_string(metadata.max_quantity, metadata.size_precision, field="max_quantity"),
        ),
        "min_quantity": Quantity.from_str(
            _decimal_string(metadata.min_quantity, metadata.size_precision, field="min_quantity"),
        ),
        "min_notional": Money.from_str(f"{metadata.min_notional} {metadata.quote_currency}"),
        "max_price": Price.from_str(
            _decimal_string(metadata.max_price, metadata.price_precision, field="max_price"),
        ),
        "min_price": Price.from_str(
            _decimal_string(metadata.min_price, metadata.price_precision, field="min_price"),
        ),
        "maker_fee": metadata.maker_fee_rate,
        "taker_fee": metadata.taker_fee_rate,
        "info": {
            "instrument_metadata_identity": metadata.instrument_metadata_identity,
            "historical_exact": metadata.historical_exact,
            "fee_rate_basis": metadata.fee_rate_basis,
        },
    }
    if isinstance(metadata.max_notional, Decimal):
        common["max_notional"] = Money.from_str(
            f"{metadata.max_notional} {metadata.quote_currency}",
        )
    if metadata.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        return CurrencyPair(
            **common,
            margin_init=Decimal("0"),
            margin_maint=Decimal("0"),
        )
    if not isinstance(metadata.margin_init, Decimal) or not isinstance(metadata.margin_maint, Decimal):
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "Perpetual margin rates are required",
        )
    return CryptoPerpetual(
        **common,
        settlement_currency=Currency.from_str(metadata.settlement_currency),
        is_inverse=False,
        margin_init=metadata.margin_init,
        margin_maint=metadata.margin_maint,
    )


def _bar_type(instrument_id: str) -> BarType:
    return BarType(
        InstrumentId.from_str(instrument_id),
        BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
        AggregationSource.EXTERNAL,
    )


def to_nautilus_execution_bars(
    bars: tuple[NormalizedBar, ...] | list[NormalizedBar],
    metadata: InstrumentMetadata,
) -> tuple[Bar, ...]:
    bar_type = _bar_type(metadata.instrument_id)
    result: list[Bar] = []
    for item in bars:
        if item.instrument_id != metadata.instrument_id or item.source_role not in {
            SourceRole.SPOT_EXECUTION_1M,
            SourceRole.USDM_PERPETUAL_EXECUTION_1M,
        }:
            raise DataContractError(FailureCode.DATA_ROLE_MISMATCH, "execution Bar role mismatch")
        result.append(
            Bar(
                bar_type,
                Price.from_str(_decimal_string(item.open, metadata.price_precision, field="open")),
                Price.from_str(_decimal_string(item.high, metadata.price_precision, field="high")),
                Price.from_str(_decimal_string(item.low, metadata.price_precision, field="low")),
                Price.from_str(_decimal_string(item.close, metadata.price_precision, field="close")),
                Quantity.from_str(
                    _decimal_string(item.volume, metadata.size_precision, field="volume"),
                ),
                item.available_at_ns,
                item.available_at_ns,
            ),
        )
    return tuple(result)


def _source_precision(value: Decimal) -> int:
    return max(0, -value.as_tuple().exponent)


def to_nautilus_mark_updates(
    bars: tuple[NormalizedBar, ...] | list[NormalizedBar],
    metadata: InstrumentMetadata,
) -> tuple[MarkPriceUpdate, ...]:
    if metadata.market_profile is not MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
        raise DataContractError(FailureCode.MARK_ROLE_INVALID, "Spot cannot have mark updates")
    instrument_id = InstrumentId.from_str(metadata.instrument_id)
    # The public Rust catalog requires one Arrow identity (including precision)
    # per write batch. Binance preserves numerically exact marks with variable
    # textual scale, so normalize only the representation to the maximum scale
    # present in this frozen batch. Decimal equality below proves no value is
    # rounded or repaired.
    batch_precision = max((_source_precision(item.close) for item in bars), default=0)
    result: list[MarkPriceUpdate] = []
    for item in bars:
        if item.source_role is not SourceRole.USDM_PERPETUAL_MARK_1M:
            raise DataContractError(FailureCode.MARK_ROLE_INVALID, "prohibited mark source role")
        result.append(
            MarkPriceUpdate(
                instrument_id,
                Price.from_str(
                    _decimal_string(item.close, batch_precision, field="mark_close"),
                ),
                item.available_at_ns,
                item.available_at_ns,
            ),
        )
    return tuple(result)


def to_nautilus_funding_updates(
    events: tuple[FundingEvent, ...] | list[FundingEvent],
    metadata: InstrumentMetadata,
) -> tuple[FundingRateUpdate, ...]:
    if metadata.market_profile is not MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
        raise DataContractError(FailureCode.DATA_ROLE_MISMATCH, "Spot cannot have funding updates")
    instrument_id = InstrumentId.from_str(metadata.instrument_id)
    return tuple(
        FundingRateUpdate(
            instrument_id,
            item.funding_rate,
            item.calc_time_ns,
            item.calc_time_ns,
            interval=item.funding_interval_hours * 60,
            next_funding_ns=None,
        )
        for item in events
    )


def _bar_projection(item: Bar) -> dict[str, Any]:
    return {
        "bar_type": str(item.bar_type),
        "open": str(item.open),
        "high": str(item.high),
        "low": str(item.low),
        "close": str(item.close),
        "volume": str(item.volume),
        "ts_event": int(item.ts_event),
        "ts_init": int(item.ts_init),
    }


def _mark_projection(item: MarkPriceUpdate) -> dict[str, Any]:
    return {
        "instrument_id": str(item.instrument_id),
        "value": str(item.value),
        "ts_event": int(item.ts_event),
        "ts_init": int(item.ts_init),
    }


def _funding_projection(item: FundingRateUpdate) -> dict[str, Any]:
    return {
        "instrument_id": str(item.instrument_id),
        "rate": str(item.rate),
        "interval": item.interval,
        "next_funding_ns": item.next_funding_ns,
        "ts_event": int(item.ts_event),
        "ts_init": int(item.ts_init),
    }


def catalog_semantic_inventory(
    catalog: ParquetDataCatalog,
    *,
    instrument_id: str,
    bar_type: str,
    funding_updates: tuple[FundingRateUpdate, ...] = (),
) -> dict[str, Any]:
    instruments = catalog.instruments([instrument_id])
    bars = catalog.query_bars([bar_type])
    marks = catalog.query_mark_price_updates([instrument_id])
    return {
        "schema": "nautilus-semantic-inventory-v1",
        "instruments": [item.to_dict() for item in instruments],
        "execution_bars": [_bar_projection(item) for item in bars],
        "mark_price_updates": [_mark_projection(item) for item in marks],
        "funding_rate_updates": [_funding_projection(item) for item in funding_updates],
    }


def build_nautilus_catalog(
    catalog_root: Path,
    *,
    metadata: InstrumentMetadata,
    execution_bars: tuple[NormalizedBar, ...] | list[NormalizedBar],
    mark_bars: tuple[NormalizedBar, ...] | list[NormalizedBar] = (),
    funding_events: tuple[FundingEvent, ...] | list[FundingEvent] = (),
) -> CatalogBuildResult:
    root = Path(catalog_root)
    if root.exists() and any(root.iterdir()):
        raise DataContractError(
            FailureCode.DATASET_RELEASE_STALE,
            "catalog target must be a fresh derived-data directory",
        )
    root.mkdir(parents=True, exist_ok=True)
    instrument = to_nautilus_instrument(metadata)
    native_bars = to_nautilus_execution_bars(execution_bars, metadata)
    native_marks = to_nautilus_mark_updates(mark_bars, metadata) if mark_bars else ()
    native_funding = to_nautilus_funding_updates(funding_events, metadata) if funding_events else ()
    catalog = ParquetDataCatalog(str(root))
    catalog.write_instruments([instrument])
    if native_bars:
        catalog.write_bars(native_bars)
    if native_marks:
        catalog.write_mark_price_updates(native_marks)
    inventory = catalog_semantic_inventory(
        catalog,
        instrument_id=metadata.instrument_id,
        bar_type=str(_bar_type(metadata.instrument_id)),
        funding_updates=native_funding,
    )
    return CatalogBuildResult(
        catalog_identity=canonical_sha256(inventory),
        semantic_inventory=inventory,
        instrument=instrument,
        execution_bars=native_bars,
        mark_updates=native_marks,
        funding_updates=native_funding,
    )


def verify_catalog_identity(release: DatasetRelease, semantic_inventory: dict[str, Any]) -> None:
    actual = canonical_sha256(semantic_inventory)
    if actual != release.catalog_identity:
        raise DataContractError(
            FailureCode.DATASET_RELEASE_STALE,
            f"catalog identity expected={release.catalog_identity} actual={actual}",
        )


def timestamp_rules_identity() -> str:
    return canonical_sha256(
        {
            "spot_before_2025_01_01": TimestampUnit.MILLISECONDS.value,
            "spot_from_2025_01_01": TimestampUnit.MICROSECONDS.value,
            "usdm_execution": TimestampUnit.MILLISECONDS.value,
            "usdm_mark": TimestampUnit.MILLISECONDS.value,
            "usdm_funding": TimestampUnit.MILLISECONDS.value,
            "one_minute_available_at": "interval_start_plus_60_seconds",
        },
    )


def _normalized_identity(bars: tuple[NormalizedBar, ...] | list[NormalizedBar]) -> str:
    return canonical_sha256([item.semantic_payload() for item in bars])


def build_dataset_release(
    *,
    market_profile: MarketProfile,
    instrument_id: str,
    source_objects: tuple[SourceObjectBinding, ...] | list[SourceObjectBinding],
    normalized_time_range: TimeRange,
    instrument_metadata: InstrumentMetadata,
    execution_bars: tuple[NormalizedBar, ...] | list[NormalizedBar],
    catalog_identity: str,
    created_at_utc: datetime,
    mark_bars: tuple[NormalizedBar, ...] | list[NormalizedBar] = (),
    funding_events: tuple[FundingEvent, ...] | list[FundingEvent] = (),
    funding_schedule: FundingScheduleEvidence | None = None,
) -> DatasetRelease:
    if instrument_metadata.market_profile is not market_profile or instrument_metadata.instrument_id != instrument_id:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "metadata does not match release profile/instrument",
        )
    execution_role = (
        SourceRole.SPOT_EXECUTION_1M
        if market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else SourceRole.USDM_PERPETUAL_EXECUTION_1M
    )
    role_results = [
        validate_one_minute_grid(
            execution_bars,
            source_role=execution_role,
            time_range=normalized_time_range,
        ),
    ]
    if market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        if mark_bars or funding_events or funding_schedule is not None:
            raise DataContractError(
                FailureCode.DATA_ROLE_MISMATCH,
                "Spot release forbids mark and funding roles",
            )
        mark_identity = NOT_APPLICABLE
        funding_identity = NOT_APPLICABLE
    else:
        if not mark_bars:
            raise DataContractError(FailureCode.MARK_ROLE_INVALID, "Perpetual mark role missing")
        role_results.append(
            validate_one_minute_grid(
                mark_bars,
                source_role=SourceRole.USDM_PERPETUAL_MARK_1M,
                time_range=normalized_time_range,
            ),
        )
        mark_identity = _normalized_identity(mark_bars)
        funding_identity = validate_funding_schedule(funding_events, funding_schedule)
    completeness = CompletenessResult(
        status="PASS",
        no_repairs=True,
        role_results=tuple(role_results),
    )
    bindings = tuple(
        sorted(
            source_objects,
            key=lambda item: (item.source_role.value, item.source_locator, item.sha256),
        ),
    )
    values = {
        "schema_version": 1,
        "market_profile": market_profile,
        "instrument_id": instrument_id,
        "source_objects": bindings,
        "normalized_time_range": normalized_time_range,
        "execution_bar_interval": "1m",
        "available_signal_bar_intervals": (normalized_time_range,),
        "instrument_metadata_identity": instrument_metadata.instrument_metadata_identity,
        "funding_data_identity": funding_identity,
        "mark_data_identity": mark_identity,
        "normalizer_version": NORMALIZER_VERSION,
        "timestamp_rules_identity": timestamp_rules_identity(),
        "catalog_identity": catalog_identity,
        "completeness_result": completeness,
    }
    identity = canonical_sha256(values)
    return DatasetRelease(dataset_release_id=identity, created_at_utc=created_at_utc, **values)


__all__ = ["DatasetRelease"]
