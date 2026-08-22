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
from decimal import ROUND_CEILING
from decimal import ROUND_FLOOR
from enum import StrEnum
from math import lcm
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qs
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
from crypto_lab.config import _decode_json
from crypto_lab.config import _decode_typed
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.status import FailureCode


ONE_MINUTE_NS = 60_000_000_000
SPOT_MICROSECOND_TRANSITION = date(2025, 1, 1)
NOT_APPLICABLE = "NOT_APPLICABLE"
NOT_AVAILABLE = "NOT_AVAILABLE"
NORMALIZER_VERSION = "binance-public-data-v1-m2.2"
FUNDING_NATIVE_BINDING_SINGLE = "SINGLE_SOURCE_EVENT"
FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY = (
    "NAUTILUS_2_0_0RC2_INTERVAL_BOUNDARY_REPEAT_ONCE"
)
HISTORICAL_NORMALIZER_VERSIONS = frozenset({"binance-public-data-v1-m2.1"})

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

    def __init__(
        self,
        code: FailureCode | str,
        message: str,
        *,
        evidence: dict[str, str] | None = None,
    ) -> None:
        self.code = code.value if isinstance(code, FailureCode) else code
        self.evidence = MappingProxyType(dict(evidence or {}))
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
        _validate_acquisition_request(self)


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
    conflicts_with_sha256: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_source_locator(self.source_role, self.source_locator)
        if self.byte_size < 0:
            raise ConfigError("source_object.byte_size: must be non-negative")
        _require_sha256(self.sha256, "source_object.sha256")
        if self.publisher_checksum != NOT_AVAILABLE:
            _require_sha256(self.publisher_checksum, "source_object.publisher_checksum")
        for digest in self.conflicts_with_sha256:
            _require_sha256(digest, "source_object.conflicts_with_sha256")
        if tuple(sorted(set(self.conflicts_with_sha256))) != self.conflicts_with_sha256:
            raise ConfigError("source_object.conflicts_with_sha256: must be unique and sorted")

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
            conflicts_with_sha256=record.conflicts_with_sha256,
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


class SyntheticDataDescriptor(StrictModel):
    """Typed, qualification-only description of one M1 native input object."""

    type: str
    instrument_id: str
    ts_event: int
    ts_init: int
    value: str

    def __post_init__(self) -> None:
        if self.type not in {"Bar", "MarkPriceUpdate", "FundingRateUpdate"}:
            raise ConfigError("synthetic_data.type: unsupported native object")
        if not self.instrument_id or self.ts_event < 0 or self.ts_init < 0:
            raise ConfigError("synthetic_data: invalid identity or timestamp")


class SyntheticFundingExpectation(StrictModel):
    boundary_ns: int
    pnl_change: str

    def __post_init__(self) -> None:
        if self.boundary_ns < 0 or not self.pnl_change:
            raise ConfigError("synthetic_funding_expectation: invalid expected settlement")


class SyntheticQualificationDatasetRelease(StrictModel):
    """M1-only strict fixture; prohibited from RESEARCH and OFFICIAL paths."""

    dataset_release_id: str
    qualification_scope: str
    market_profile: MarketProfile
    instrument_id: str
    data: tuple[SyntheticDataDescriptor, ...]
    mark_role: str
    mark_complete: bool | str
    funding_role: str
    funding_complete: bool | str
    expected_funding_settlements: tuple[SyntheticFundingExpectation, ...]

    def __post_init__(self) -> None:
        _require_sha256(self.dataset_release_id, "synthetic_release.dataset_release_id")
        if self.qualification_scope != "M1_SYNTHETIC_QUALIFICATION_ONLY":
            raise ConfigError("synthetic_release.qualification_scope: qualification-only value required")
        if not self.instrument_id or not self.data:
            raise ConfigError("synthetic_release: instrument and native data descriptors are required")
        if any(item.instrument_id != self.instrument_id for item in self.data):
            raise ConfigError("synthetic_release: data Instrument mismatch")
        if self.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
            if (
                self.mark_role != NOT_APPLICABLE
                or self.mark_complete != NOT_APPLICABLE
                or self.funding_role != NOT_APPLICABLE
                or self.funding_complete != NOT_APPLICABLE
                or self.expected_funding_settlements
            ):
                raise ConfigError("synthetic_release: Spot forbids derivative roles")
        else:
            if self.mark_role != "markPriceKlines" or type(self.mark_complete) is not bool:
                raise ConfigError("synthetic_release: Perpetual mark fixture state must be explicit")
            if self.funding_role != "fundingRate" or type(self.funding_complete) is not bool:
                raise ConfigError("synthetic_release: Perpetual funding fixture state must be explicit")
        if canonical_sha256(self.material_payload()) != self.dataset_release_id:
            raise ConfigError("synthetic_release.dataset_release_id mismatch")

    def material_payload(self) -> dict[str, Any]:
        payload = self.to_builtins()
        payload.pop("dataset_release_id", None)
        return payload

    @classmethod
    def create(cls, **values: Any) -> SyntheticQualificationDatasetRelease:
        return cls(dataset_release_id=canonical_sha256(values), **values)


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
    lot_size_min_quantity: Decimal
    lot_size_max_quantity: Decimal
    lot_size_step_size: Decimal
    market_lot_size_min_quantity: Decimal
    market_lot_size_max_quantity: Decimal
    market_lot_size_step_size: Decimal
    effective_market_derivation: tuple[str, ...]
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
        if self.schema_version != 2:
            raise ConfigError("instrument_metadata.schema_version: only version 2 is supported")
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
            self.lot_size_min_quantity,
            self.lot_size_max_quantity,
            self.lot_size_step_size,
            self.market_lot_size_min_quantity,
            self.market_lot_size_max_quantity,
            self.market_lot_size_step_size,
        )
        if any(not item.is_finite() or item < 0 for item in nonnegative):
            raise ConfigError("instrument_metadata: non-negative finite values are required")
        if not self.fee_rate_basis:
            raise ConfigError("instrument_metadata.fee_rate_basis: must be explicit")
        if not self.effective_market_derivation:
            raise ConfigError("instrument_metadata.effective_market_derivation: required")
        if self.size_increment != _market_quantity_intersection(
            lot_min=self.lot_size_min_quantity,
            lot_max=self.lot_size_max_quantity,
            lot_step=self.lot_size_step_size,
            market_min=self.market_lot_size_min_quantity,
            market_max=self.market_lot_size_max_quantity,
            market_step=self.market_lot_size_step_size,
        )[2]:
            raise ConfigError("instrument_metadata.size_increment: effective MARKET grid mismatch")
        effective = _market_quantity_intersection(
            lot_min=self.lot_size_min_quantity,
            lot_max=self.lot_size_max_quantity,
            lot_step=self.lot_size_step_size,
            market_min=self.market_lot_size_min_quantity,
            market_max=self.market_lot_size_max_quantity,
            market_step=self.market_lot_size_step_size,
        )
        if (self.min_quantity, self.max_quantity, self.size_increment) != effective[:3]:
            raise ConfigError("instrument_metadata: effective MARKET limits mismatch")
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
        material["schema_version"] = 2
        identity = canonical_sha256(material)
        return cls(schema_version=2, instrument_metadata_identity=identity, **values)


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
        if self.normalizer_version not in {NORMALIZER_VERSION, *HISTORICAL_NORMALIZER_VERSIONS}:
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
        if self.normalizer_version == NORMALIZER_VERSION:
            _validate_source_bindings(
                market_profile=self.market_profile,
                instrument_id=self.instrument_id,
                source_objects=self.source_objects,
                normalized_time_range=self.normalized_time_range,
            )
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

    @classmethod
    def from_json_bytes(cls, payload: bytes) -> DatasetRelease:
        """Parse current releases strictly and old M2.1 manifests as historical only."""

        value = _decode_json(payload)
        if not isinstance(value, dict):
            raise ConfigError("$: expected object")
        normalizer = value.get("normalizer_version")
        if normalizer in HISTORICAL_NORMALIZER_VERSIONS:
            sources = value.get("source_objects")
            if isinstance(sources, list):
                for source in sources:
                    if isinstance(source, dict):
                        source.setdefault("conflicts_with_sha256", [])
        elif normalizer == NORMALIZER_VERSION:
            sources = value.get("source_objects")
            if isinstance(sources, list) and any(
                not isinstance(source, dict) or "conflicts_with_sha256" not in source
                for source in sources
            ):
                raise ConfigError("$.source_objects: missing conflicts_with_sha256")
        return _decode_typed(value, cls, "$")

    def material_payload(self) -> dict[str, Any]:
        payload = self.to_builtins()
        payload.pop("dataset_release_id", None)
        payload.pop("created_at_utc", None)
        if self.normalizer_version in HISTORICAL_NORMALIZER_VERSIONS:
            for source in payload["source_objects"]:
                source.pop("conflicts_with_sha256", None)
        return payload

    def with_created_at(self, value: datetime) -> DatasetRelease:
        return replace(self, created_at_utc=value)

    @property
    def is_current_contract(self) -> bool:
        return self.normalizer_version == NORMALIZER_VERSION

    def resolve_runtime_data(self, data_root: Path) -> ResolvedDatasetRelease:
        """Resolve physical derived data without putting its path in content identity."""

        return _resolve_dataset_release_runtime(self, Path(data_root))


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


@dataclass(frozen=True)
class ResolvedDatasetRelease:
    instrument: CurrencyPair | CryptoPerpetual
    data: tuple[Bar | MarkPriceUpdate | FundingRateUpdate, ...]
    catalog_path: Path
    semantic_inventory: dict[str, Any]
    funding_native_binding: str = FUNDING_NATIVE_BINDING_SINGLE
    funding_source_event_count: int = 0
    funding_runtime_update_count: int = 0


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
        query = parse_qs(parsed.query, keep_blank_values=True)
        valid = (
            parsed.netloc == "api.binance.com"
            and path == "/api/v3/exchangeInfo"
            and set(query) == {"symbol"}
            and len(query["symbol"]) == 1
            and re.fullmatch(r"[A-Z0-9]+", query["symbol"][0]) is not None
        )
    elif role is SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA:
        valid = parsed.netloc == "fapi.binance.com" and path == "/fapi/v1/exchangeInfo"
    elif role is SourceRole.USDM_PERPETUAL_FUNDING_METADATA:
        valid = parsed.netloc == "fapi.binance.com" and path == "/fapi/v1/fundingInfo"
    elif role is SourceRole.USDM_EXECUTION_TIMESTAMP_PROBE:
        query = parse_qs(parsed.query, keep_blank_values=True)
        valid = (
            parsed.netloc == "fapi.binance.com"
            and path == "/fapi/v1/klines"
            and set(query) == {"symbol", "interval", "startTime", "endTime", "limit"}
            and query.get("interval") == ["1m"]
            and query.get("limit") == ["1"]
            and all(_INTEGER.fullmatch(query[name][0]) for name in ("startTime", "endTime"))
            and re.fullmatch(r"[A-Z0-9]+", query.get("symbol", [""])[0]) is not None
        )
    elif role is SourceRole.USDM_MARK_TIMESTAMP_PROBE:
        query = parse_qs(parsed.query, keep_blank_values=True)
        valid = (
            parsed.netloc == "fapi.binance.com"
            and path == "/fapi/v1/markPriceKlines"
            and set(query) == {"symbol", "interval", "startTime", "endTime", "limit"}
            and query.get("interval") == ["1m"]
            and query.get("limit") == ["1"]
            and all(_INTEGER.fullmatch(query[name][0]) for name in ("startTime", "endTime"))
            and re.fullmatch(r"[A-Z0-9]+", query.get("symbol", [""])[0]) is not None
        )
    elif role is SourceRole.USDM_FUNDING_TIMESTAMP_PROBE:
        query = parse_qs(parsed.query, keep_blank_values=True)
        valid = (
            parsed.netloc == "fapi.binance.com"
            and path == "/fapi/v1/fundingRate"
            and set(query) == {"symbol", "startTime", "endTime", "limit"}
            and all(_INTEGER.fullmatch(query[name][0]) for name in ("startTime", "endTime", "limit"))
            and re.fullmatch(r"[A-Z0-9]+", query.get("symbol", [""])[0]) is not None
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


def _archive_locator_fields(locator: str) -> tuple[str, str, str, str] | None:
    """Return cadence, symbol, interval, and filename for an allowed archive path."""

    parts = urlparse(locator).path.strip("/").split("/")
    if len(parts) < 7 or parts[0] != "data":
        return None
    filename = parts[-1]
    if parts[1:4] == ["spot", "daily", "klines"] or parts[1:4] == ["spot", "monthly", "klines"]:
        return parts[2], parts[4], parts[5], filename
    if parts[1:5] in (
        ["futures", "um", "daily", "klines"],
        ["futures", "um", "monthly", "klines"],
        ["futures", "um", "daily", "markPriceKlines"],
        ["futures", "um", "monthly", "markPriceKlines"],
    ):
        return parts[3], parts[5], parts[6], filename
    if parts[1:5] == ["futures", "um", "monthly", "fundingRate"]:
        return parts[3], parts[5], "EVENT", filename
    return None


def _validate_acquisition_request(request: AcquisitionRequest) -> None:
    role = request.source_role
    profile = request.market_profile
    archive_fields = _archive_locator_fields(request.source_locator.removesuffix(".CHECKSUM"))
    market_roles = {
        SourceRole.SPOT_EXECUTION_1M,
        SourceRole.USDM_PERPETUAL_EXECUTION_1M,
        SourceRole.USDM_PERPETUAL_MARK_1M,
        SourceRole.USDM_PERPETUAL_FUNDING,
        SourceRole.PUBLISHER_CHECKSUM,
    }
    if role in market_roles:
        if archive_fields is None:
            raise ConfigError("acquisition.source_locator: archive identity is not resolvable")
        _cadence, symbol, interval, filename = archive_fields
        expected_filename = filename + (".CHECKSUM" if role is SourceRole.PUBLISHER_CHECKSUM else "")
        if request.instrument != symbol or request.exact_filename != expected_filename:
            raise ConfigError("acquisition: symbol or filename does not match locator")
        if request.requested_interval != interval:
            raise ConfigError("acquisition.requested_interval: does not match locator")
        if not isinstance(request.requested_time_range, TimeRange):
            raise ConfigError("acquisition.requested_time_range: market object requires a range")
    elif role is SourceRole.SPOT_INSTRUMENT_METADATA:
        query = parse_qs(urlparse(request.source_locator).query)
        if query.get("symbol") != [request.instrument]:
            raise ConfigError("acquisition.instrument: Spot metadata symbol mismatch")
        if request.exact_filename != f"spot-exchangeInfo-{request.instrument}.json":
            raise ConfigError("acquisition.exact_filename: Spot metadata filename mismatch")
    if role is SourceRole.SPOT_EXECUTION_1M and profile != MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY.value:
        raise ConfigError("acquisition.market_profile: Spot execution profile mismatch")
    if role in {
        SourceRole.USDM_PERPETUAL_EXECUTION_1M,
        SourceRole.USDM_PERPETUAL_MARK_1M,
        SourceRole.USDM_PERPETUAL_FUNDING,
    } and profile != MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value:
        raise ConfigError("acquisition.market_profile: USD-M profile mismatch")


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


def prove_funding_schedule_from_official_objects(
    events: tuple[FundingEvent, ...],
    *,
    source_object_sha256s: tuple[str, ...],
    time_range: TimeRange,
) -> FundingScheduleEvidence:
    """Prove one interval from an ordered immutable set of official monthly objects.

    ``FundingScheduleEvidence`` v1 has one SHA field.  For a multi-month
    interval that field is the canonical collection identity below, while the
    acquisition manifest preserves every constituent source SHA and publisher
    checksum.  It is never presented as the hash of a downloaded byte object.
    """

    if len(source_object_sha256s) < 2 or len(set(source_object_sha256s)) != len(
        source_object_sha256s,
    ):
        raise DataContractError(
            FailureCode.FUNDING_AMBIGUOUS,
            "multi-object funding proof needs at least two unique official source objects",
        )
    for digest in source_object_sha256s:
        _require_sha256(digest, "funding_schedule.source_object_sha256s")
    collection_identity = canonical_sha256(
        {"ordered_official_funding_source_object_sha256s": source_object_sha256s},
    )
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
        "source_object_sha256": collection_identity,
        "normalized_time_range": time_range,
        "timestamp_unit": TimestampUnit.MILLISECONDS,
        "proof_basis": "OFFICIAL_BINANCE_FUNDING_ARCHIVE_SET_EXPLICIT_INTERVAL_ROWS",
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
        # Preserve the exact archive response before any parsing or semantic work.
        preliminary_archive = self.store.store_bytes(
            payload,
            request=request,
            acquired_at_utc=acquired_at_utc,
        )
        checksum_record: RawObjectRecord | None = None
        checksum_bytes: bytes | None = None
        if checksum_request is not None:
            if checksum_request.source_role is not SourceRole.PUBLISHER_CHECKSUM:
                raise DataContractError(
                    FailureCode.DATA_ROLE_MISMATCH,
                    "checksum request has the wrong role",
                )
            checksum_bytes = self.fetch_bytes(checksum_request.source_locator)
            # Preserve the exact checksum response before attempting to decode it.
            checksum_record = self.store.store_bytes(
                checksum_bytes,
                request=checksum_request,
                acquired_at_utc=acquired_at_utc,
            )
            evidence = {
                "archive_sha256": preliminary_archive.sha256,
                "checksum_sha256": checksum_record.sha256,
            }
            try:
                publisher_checksum = parse_publisher_checksum(
                    checksum_bytes,
                    exact_filename=request.exact_filename,
                )
                verify_publisher_checksum(
                    payload,
                    checksum_bytes,
                    exact_filename=request.exact_filename,
                )
            except DataContractError as exc:
                raise DataContractError(
                    exc.code,
                    f"{exc}; preserved archive/checksum response hashes",
                    evidence=evidence,
                ) from exc
            verified_archive = self.store.store_bytes(
                payload,
                request=request,
                acquired_at_utc=acquired_at_utc,
                publisher_checksum=publisher_checksum,
            )
            return verified_archive, checksum_record
        return preliminary_archive, None


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


def _market_quantity_intersection(
    *,
    lot_min: Decimal,
    lot_max: Decimal,
    lot_step: Decimal,
    market_min: Decimal,
    market_max: Decimal,
    market_step: Decimal,
) -> tuple[Decimal, Decimal, Decimal, tuple[str, ...]]:
    """Resolve the exact intersection of enabled LOT and MARKET_LOT rules."""

    values = (lot_min, lot_max, lot_step, market_min, market_max, market_step)
    if any(not value.is_finite() or value < 0 for value in values):
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "quantity filter values must be finite and non-negative",
        )
    steps = tuple(value for value in (lot_step, market_step) if value > 0)
    lower_bounds = tuple(value for value in (lot_min, market_min) if value > 0)
    upper_bounds = tuple(value for value in (lot_max, market_max) if value > 0)
    if not steps or not lower_bounds or not upper_bounds:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "effective MARKET quantity grid and bounds are not representable",
        )
    scale = max(max(0, -value.as_tuple().exponent) for value in steps)
    step_units: list[int] = []
    for value in steps:
        scaled = value.scaleb(scale)
        if scaled != scaled.to_integral_value():
            raise DataContractError(
                FailureCode.INSTRUMENT_METADATA_INVALID,
                "quantity step cannot be represented exactly",
            )
        step_units.append(int(scaled))
    intersection_step = Decimal(lcm(*step_units)).scaleb(-scale)
    raw_lower = max(lower_bounds)
    raw_upper = min(upper_bounds)
    lower_grid_index = (raw_lower / intersection_step).to_integral_value(rounding=ROUND_CEILING)
    upper_grid_index = (raw_upper / intersection_step).to_integral_value(rounding=ROUND_FLOOR)
    effective_min = lower_grid_index * intersection_step
    effective_max = upper_grid_index * intersection_step
    if effective_min <= 0 or effective_min > effective_max:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "LOT_SIZE and MARKET_LOT_SIZE have no valid MARKET quantity intersection",
        )
    derivation = (
        "ZERO_FILTER_COMPONENTS_DISABLED_BY_OFFICIAL_BINANCE_FILTER_CONTRACT",
        f"ENABLED_MIN_INTERSECTION=max({lot_min},{market_min})={raw_lower}",
        f"ENABLED_MAX_INTERSECTION=min({lot_max},{market_max})={raw_upper}",
        f"ENABLED_STEP_INTERSECTION=lcm_decimal({lot_step},{market_step})={intersection_step}",
        f"EFFECTIVE_MIN=ceil({raw_lower}/{intersection_step})*{intersection_step}={effective_min}",
        f"EFFECTIVE_MAX=floor({raw_upper}/{intersection_step})*{intersection_step}={effective_max}",
    )
    return effective_min, effective_max, intersection_step, derivation


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
    raw_symbol: str,
    instrument_id: str,
    source_object_sha256: str,
    maker_fee_rate: Decimal,
    taker_fee_rate: Decimal,
    fee_rate_basis: str,
) -> InstrumentMetadata:
    data = _strict_json(payload)
    definitions = data.get("symbols")
    if not isinstance(definitions, list):
        raise DataContractError(FailureCode.INSTRUMENT_METADATA_INVALID, "symbols missing")
    if instrument_id != f"{raw_symbol}.BINANCE":
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "Spot Instrument ID does not match the explicit Binance symbol",
        )
    matches = [item for item in definitions if item.get("symbol") == raw_symbol]
    if len(matches) != 1:
        raise DataContractError(FailureCode.INSTRUMENT_METADATA_INVALID, "Spot symbol definition ambiguous")
    definition = matches[0]
    price = _filter(definition, "PRICE_FILTER")
    lot = _filter(definition, "LOT_SIZE")
    market_lot = _filter(definition, "MARKET_LOT_SIZE")
    notional = _filter(definition, "NOTIONAL")
    increment = _decimal(price["tickSize"], "tickSize")
    lot_min = _decimal(lot["minQty"], "LOT_SIZE.minQty")
    lot_max = _decimal(lot["maxQty"], "LOT_SIZE.maxQty")
    lot_step = _decimal(lot["stepSize"], "LOT_SIZE.stepSize")
    market_min = _decimal(market_lot["minQty"], "MARKET_LOT_SIZE.minQty")
    market_max = _decimal(market_lot["maxQty"], "MARKET_LOT_SIZE.maxQty")
    market_step = _decimal(market_lot["stepSize"], "MARKET_LOT_SIZE.stepSize")
    effective_min, effective_max, size_increment, derivation = _market_quantity_intersection(
        lot_min=lot_min,
        lot_max=lot_max,
        lot_step=lot_step,
        market_min=market_min,
        market_max=market_max,
        market_step=market_step,
    )
    values = {
        "market_profile": MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        "instrument_id": instrument_id,
        "raw_symbol": raw_symbol,
        "official_source": f"https://api.binance.com/api/v3/exchangeInfo?symbol={raw_symbol}",
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
        "min_quantity": effective_min,
        "max_quantity": effective_max,
        "lot_size_min_quantity": lot_min,
        "lot_size_max_quantity": lot_max,
        "lot_size_step_size": lot_step,
        "market_lot_size_min_quantity": market_min,
        "market_lot_size_max_quantity": market_max,
        "market_lot_size_step_size": market_step,
        "effective_market_derivation": derivation,
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
    raw_symbol: str,
    instrument_id: str,
    source_object_sha256: str,
    maker_fee_rate: Decimal,
    taker_fee_rate: Decimal,
    fee_rate_basis: str,
) -> InstrumentMetadata:
    data = _strict_json(payload)
    definitions = data.get("symbols")
    if not isinstance(definitions, list):
        raise DataContractError(FailureCode.INSTRUMENT_METADATA_INVALID, "symbols missing")
    if instrument_id != f"{raw_symbol}-PERP.BINANCE":
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "Perpetual Instrument ID does not match the explicit Binance symbol",
        )
    matches = [item for item in definitions if item.get("symbol") == raw_symbol]
    if len(matches) != 1:
        raise DataContractError(FailureCode.INSTRUMENT_METADATA_INVALID, "USD-M symbol definition ambiguous")
    definition = matches[0]
    if definition.get("contractType") != "PERPETUAL" or definition.get("marginAsset") != "USDT":
        raise DataContractError(FailureCode.INSTRUMENT_METADATA_INVALID, "not linear USDT perpetual")
    price = _filter(definition, "PRICE_FILTER")
    lot = _filter(definition, "LOT_SIZE")
    market_lot = _filter(definition, "MARKET_LOT_SIZE")
    notional = _filter(definition, "MIN_NOTIONAL")
    increment = _decimal(price["tickSize"], "tickSize")
    lot_min = _decimal(lot["minQty"], "LOT_SIZE.minQty")
    lot_max = _decimal(lot["maxQty"], "LOT_SIZE.maxQty")
    lot_step = _decimal(lot["stepSize"], "LOT_SIZE.stepSize")
    market_min = _decimal(market_lot["minQty"], "MARKET_LOT_SIZE.minQty")
    market_max = _decimal(market_lot["maxQty"], "MARKET_LOT_SIZE.maxQty")
    market_step = _decimal(market_lot["stepSize"], "MARKET_LOT_SIZE.stepSize")
    effective_min, effective_max, size_increment, derivation = _market_quantity_intersection(
        lot_min=lot_min,
        lot_max=lot_max,
        lot_step=lot_step,
        market_min=market_min,
        market_max=market_max,
        market_step=market_step,
    )
    values = {
        "market_profile": MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        "instrument_id": instrument_id,
        "raw_symbol": raw_symbol,
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
        "min_quantity": effective_min,
        "max_quantity": effective_max,
        "lot_size_min_quantity": lot_min,
        "lot_size_max_quantity": lot_max,
        "lot_size_step_size": lot_step,
        "market_lot_size_min_quantity": market_min,
        "market_lot_size_max_quantity": market_max,
        "market_lot_size_step_size": market_step,
        "effective_market_derivation": derivation,
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
            "official_source": metadata.official_source,
            "source_object_sha256": metadata.source_object_sha256,
            "official_definition": metadata.to_builtins()["official_definition"],
            "binance_quantity_filters": {
                "LOT_SIZE": {
                    "minQty": str(metadata.lot_size_min_quantity),
                    "maxQty": str(metadata.lot_size_max_quantity),
                    "stepSize": str(metadata.lot_size_step_size),
                },
                "MARKET_LOT_SIZE": {
                    "minQty": str(metadata.market_lot_size_min_quantity),
                    "maxQty": str(metadata.market_lot_size_max_quantity),
                    "stepSize": str(metadata.market_lot_size_step_size),
                },
                "effective_MARKET": {
                    "minQty": str(metadata.min_quantity),
                    "maxQty": str(metadata.max_quantity),
                    "stepSize": str(metadata.size_increment),
                    "derivation": list(metadata.effective_market_derivation),
                },
            },
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


def validate_market_order_quantity(
    instrument: CurrencyPair | CryptoPerpetual,
    quantity: Quantity,
) -> None:
    """Validate V1 MARKET eligibility from the native Instrument constraints."""

    if quantity.precision != instrument.size_precision:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "MARKET quantity precision differs from the native Instrument",
        )
    value = quantity.as_decimal()
    minimum = None if instrument.min_quantity is None else instrument.min_quantity.as_decimal()
    maximum = None if instrument.max_quantity is None else instrument.max_quantity.as_decimal()
    increment = instrument.size_increment.as_decimal()
    if minimum is not None and value < minimum:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            f"MARKET quantity {value} is below native minimum {minimum}",
        )
    if maximum is not None and value > maximum:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            f"MARKET quantity {value} is above native maximum {maximum}",
        )
    if increment <= 0 or value % increment != 0:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            f"MARKET quantity {value} is not on native grid {increment}",
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
    *,
    native_binding: str = FUNDING_NATIVE_BINDING_SINGLE,
) -> tuple[FundingRateUpdate, ...]:
    if metadata.market_profile is not MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
        raise DataContractError(FailureCode.DATA_ROLE_MISMATCH, "Spot cannot have funding updates")
    instrument_id = InstrumentId.from_str(metadata.instrument_id)
    if native_binding not in {
        FUNDING_NATIVE_BINDING_SINGLE,
        FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
    }:
        raise DataContractError(
            FailureCode.FUNDING_AMBIGUOUS,
            f"unsupported native funding binding {native_binding!r}",
        )
    repetitions = (
        2
        if native_binding == FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY
        else 1
    )
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
        for _ in range(repetitions)
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
    funding_native_binding: str = FUNDING_NATIVE_BINDING_SINGLE,
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
    native_funding = (
        to_nautilus_funding_updates(
            funding_events,
            metadata,
            native_binding=funding_native_binding,
        )
        if funding_events
        else ()
    )
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


def _resolve_dataset_release_runtime(
    release: DatasetRelease,
    data_root: Path,
) -> ResolvedDatasetRelease:
    """Resolve and verify one current release through the public DatasetRelease boundary."""

    if not release.is_current_contract:
        raise DataContractError(
            FailureCode.DATASET_RELEASE_STALE,
            "historical Dataset Releases cannot enter the active run path",
        )
    if canonical_sha256(release.material_payload()) != release.dataset_release_id:
        raise DataContractError(FailureCode.DATA_HASH_MISMATCH, "Dataset Release identity mismatch")
    raw_root = data_root / "raw" / "sha256"
    for source in release.source_objects:
        path = raw_root / source.sha256[:2] / f"{source.sha256}.blob"
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source.sha256:
            raise DataContractError(
                FailureCode.DATA_HASH_MISMATCH,
                f"source object {source.sha256} does not resolve",
            )

    release_root = data_root / "releases"
    metadata_path = release_root / f"{release.instrument_metadata_identity}.metadata.json"
    try:
        metadata = InstrumentMetadata.from_json_bytes(metadata_path.read_bytes())
    except Exception as exc:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "instrument metadata does not resolve",
        ) from exc
    if (
        metadata.instrument_metadata_identity != release.instrument_metadata_identity
        or metadata.instrument_id != release.instrument_id
        or metadata.market_profile is not release.market_profile
    ):
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "instrument metadata binding mismatch",
        )

    catalog_path = data_root / "catalog" / release.catalog_identity
    if not catalog_path.is_dir():
        raise DataContractError(FailureCode.DATASET_RELEASE_STALE, "catalog identity does not resolve")
    catalog = ParquetDataCatalog(str(catalog_path))
    instruments = tuple(catalog.instruments([release.instrument_id]))
    bars = tuple(catalog.query_bars([str(_bar_type(release.instrument_id))]))
    marks = tuple(catalog.query_mark_price_updates([release.instrument_id]))
    if len(instruments) != 1 or not bars:
        raise DataContractError(FailureCode.DATASET_RELEASE_STALE, "catalog inventory is incomplete")
    instrument = instruments[0]
    if (
        str(instrument.id) != release.instrument_id
        or instrument.info.get("instrument_metadata_identity") != release.instrument_metadata_identity
    ):
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "native Instrument is not bound to release metadata",
        )

    funding_updates: tuple[FundingRateUpdate, ...] = ()
    funding_native_binding = FUNDING_NATIVE_BINDING_SINGLE
    funding_source_event_count = 0
    if release.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        if marks or release.mark_data_identity != NOT_APPLICABLE or release.funding_data_identity != NOT_APPLICABLE:
            raise DataContractError(FailureCode.DATA_ROLE_MISMATCH, "Spot catalog has derivative roles")
    else:
        if not marks:
            raise DataContractError(FailureCode.MARK_ROLE_INVALID, "Perpetual mark catalog is empty")
        funding_path = release_root / f"{release.funding_data_identity}.funding.json"
        try:
            funding_payload = _strict_json(funding_path.read_bytes())
        except Exception as exc:
            raise DataContractError(FailureCode.FUNDING_MISSING, "funding evidence does not resolve") from exc
        declared = funding_payload.pop("funding_data_identity", None)
        if declared != release.funding_data_identity or canonical_sha256(funding_payload) != declared:
            raise DataContractError(FailureCode.DATA_HASH_MISMATCH, "funding data identity mismatch")
        events = funding_payload.get("events")
        if not isinstance(events, list) or not events:
            raise DataContractError(FailureCode.FUNDING_MISSING, "funding evidence has no events")
        funding_native_binding = str(
            funding_payload.get("native_binding", FUNDING_NATIVE_BINDING_SINGLE),
        )
        if funding_native_binding not in {
            FUNDING_NATIVE_BINDING_SINGLE,
            FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
        }:
            raise DataContractError(
                FailureCode.FUNDING_AMBIGUOUS,
                "funding evidence declares an unsupported native binding",
            )
        funding_source_event_count = len(events)
        instrument_id = InstrumentId.from_str(release.instrument_id)
        try:
            funding_updates = to_nautilus_funding_updates(
                tuple(
                    FundingEvent(
                        instrument_id=release.instrument_id,
                        calc_time_ns=int(item["calc_time_ns"]),
                        funding_interval_hours=int(item["funding_interval_hours"]),
                        funding_rate=Decimal(str(item["funding_rate"])),
                        source_row_number=0,
                        source_row_sha256="0" * 64,
                        event_key=str(item["event_key"]),
                    )
                    for item in events
                ),
                metadata,
                native_binding=funding_native_binding,
            )
        except Exception as exc:
            raise DataContractError(FailureCode.FUNDING_AMBIGUOUS, "funding event is malformed") from exc

    inventory = catalog_semantic_inventory(
        catalog,
        instrument_id=release.instrument_id,
        bar_type=str(_bar_type(release.instrument_id)),
        funding_updates=funding_updates,
    )
    verify_catalog_identity(release, inventory)
    priorities = {MarkPriceUpdate: 0, FundingRateUpdate: 1, Bar: 2}
    data = tuple(
        sorted(
            (*marks, *funding_updates, *bars),
            key=lambda item: (int(item.ts_init), priorities[type(item)]),
        ),
    )
    return ResolvedDatasetRelease(
        instrument=instrument,
        data=data,
        catalog_path=catalog_path,
        semantic_inventory=inventory,
        funding_native_binding=funding_native_binding,
        funding_source_event_count=funding_source_event_count,
        funding_runtime_update_count=len(funding_updates),
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


def _raw_symbol_for_release(profile: MarketProfile, instrument_id: str) -> str:
    suffix = (
        ".BINANCE"
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else "-PERP.BINANCE"
    )
    if not instrument_id.endswith(suffix):
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "release Instrument ID is incompatible with its Market Profile",
        )
    symbol = instrument_id.removesuffix(suffix)
    if re.fullmatch(r"[A-Z0-9]+", symbol) is None:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "release Binance symbol is invalid",
        )
    return symbol


def _source_date_from_filename(role: SourceRole, filename: str) -> date:
    patterns = (
        rf"[A-Z0-9]+-1m-(\d{{4}}-\d{{2}}-\d{{2}})\.zip",
        rf"[A-Z0-9]+-1m-(\d{{4}}-\d{{2}})\.zip",
        rf"[A-Z0-9]+-fundingRate-(\d{{4}}-\d{{2}})\.zip",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, filename)
        if match is None:
            continue
        raw = match.group(1)
        try:
            return date.fromisoformat(raw + ("-01" if len(raw) == 7 else ""))
        except ValueError as exc:
            raise DataContractError(
                FailureCode.DATA_TIMESTAMP_INVALID,
                "source filename date is invalid",
            ) from exc
    raise DataContractError(
        FailureCode.DATA_SOURCE_INVALID,
        f"source filename {filename!r} has no supported date identity",
    )


def _ranges_cover(bindings: tuple[SourceObjectBinding, ...], target: TimeRange) -> bool:
    ranges = sorted(
        (
            item.requested_time_range.start_inclusive,
            item.requested_time_range.end_exclusive,
        )
        for item in bindings
        if isinstance(item.requested_time_range, TimeRange)
    )
    cursor = target.start_inclusive
    for start, end in ranges:
        if end <= cursor:
            continue
        if start > cursor:
            return False
        cursor = max(cursor, end)
        if cursor >= target.end_exclusive:
            return True
    return cursor >= target.end_exclusive


def _validate_source_bindings(
    *,
    market_profile: MarketProfile,
    instrument_id: str,
    source_objects: tuple[SourceObjectBinding, ...],
    normalized_time_range: TimeRange,
) -> None:
    symbol = _raw_symbol_for_release(market_profile, instrument_id)
    if market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        allowed = {
            SourceRole.SPOT_EXECUTION_1M,
            SourceRole.SPOT_INSTRUMENT_METADATA,
        }
        range_roles = {SourceRole.SPOT_EXECUTION_1M: "1m"}
    else:
        allowed = {
            SourceRole.USDM_PERPETUAL_EXECUTION_1M,
            SourceRole.USDM_PERPETUAL_MARK_1M,
            SourceRole.USDM_PERPETUAL_FUNDING,
            SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA,
        }
        range_roles = {
            SourceRole.USDM_PERPETUAL_EXECUTION_1M: "1m",
            SourceRole.USDM_PERPETUAL_MARK_1M: "1m",
            SourceRole.USDM_PERPETUAL_FUNDING: "EVENT",
        }
    for source in source_objects:
        if source.conflicts_with_sha256:
            raise DataContractError(
                FailureCode.DATA_DUPLICATE_CONFLICT,
                f"unresolved source conflict for {source.source_locator}",
            )
        if source.source_role not in allowed:
            raise DataContractError(
                FailureCode.DATA_ROLE_MISMATCH,
                "source role is not allowed by the target release",
            )
        if source.instrument != symbol or source.market_profile != market_profile.value:
            raise DataContractError(
                FailureCode.DATA_ROLE_MISMATCH,
                "source Instrument or Market Profile does not match target release",
            )
        expected_interval = range_roles.get(source.source_role, NOT_APPLICABLE)
        if source.requested_interval != expected_interval:
            raise DataContractError(
                FailureCode.DATA_ROLE_MISMATCH,
                "source interval does not match target role",
            )
        if source.source_role in range_roles:
            fields = _archive_locator_fields(source.source_locator)
            if fields is None:
                raise DataContractError(FailureCode.DATA_SOURCE_INVALID, "archive locator is unresolved")
            _cadence, locator_symbol, locator_interval, locator_filename = fields
            if (
                locator_symbol != symbol
                or locator_interval != expected_interval
                or locator_filename != source.exact_filename
            ):
                raise DataContractError(
                    FailureCode.DATA_SOURCE_INVALID,
                    "archive locator, filename, symbol, or interval is inconsistent",
                )
            if not isinstance(source.requested_time_range, TimeRange):
                raise DataContractError(
                    FailureCode.DATA_SOURCE_INVALID,
                    "market source has no requested time range",
                )
            source_date = _source_date_from_filename(source.source_role, source.exact_filename)
            unit = timestamp_unit_for(source.source_role, source_date=source_date)
            expected_unit = (
                TimestampUnit.MICROSECONDS
                if source.source_role is SourceRole.SPOT_EXECUTION_1M
                and source_date >= SPOT_MICROSECOND_TRANSITION
                else TimestampUnit.MILLISECONDS
            )
            if unit is not expected_unit:
                raise DataContractError(
                    FailureCode.DATA_TIMESTAMP_INVALID,
                    "source timestamp unit contract is unresolved",
                )
        elif source.source_role is SourceRole.SPOT_INSTRUMENT_METADATA:
            query = parse_qs(urlparse(source.source_locator).query)
            if (
                query.get("symbol") != [symbol]
                or source.exact_filename != f"spot-exchangeInfo-{symbol}.json"
                or source.requested_time_range != NOT_APPLICABLE
            ):
                raise DataContractError(
                    FailureCode.DATA_SOURCE_INVALID,
                    "Spot metadata binding is inconsistent",
                )
        elif source.source_role is SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA:
            if (
                urlparse(source.source_locator).path != "/fapi/v1/exchangeInfo"
                or source.exact_filename != "usdm-exchangeInfo.json"
                or source.requested_time_range != NOT_APPLICABLE
            ):
                raise DataContractError(
                    FailureCode.DATA_SOURCE_INVALID,
                    "USD-M metadata binding is inconsistent",
                )
    for role in range_roles:
        role_bindings = tuple(item for item in source_objects if item.source_role is role)
        if not role_bindings or not _ranges_cover(role_bindings, normalized_time_range):
            raise DataContractError(
                FailureCode.DATA_SOURCE_INVALID,
                f"source range for {role.value} does not cover the release",
            )


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
    funding_native_binding: str = FUNDING_NATIVE_BINDING_SINGLE,
) -> DatasetRelease:
    if instrument_metadata.market_profile is not market_profile or instrument_metadata.instrument_id != instrument_id:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "metadata does not match release profile/instrument",
        )
    bindings = tuple(
        sorted(
            source_objects,
            key=lambda item: (item.source_role.value, item.source_locator, item.sha256),
        ),
    )
    _validate_source_bindings(
        market_profile=market_profile,
        instrument_id=instrument_id,
        source_objects=bindings,
        normalized_time_range=normalized_time_range,
    )
    if any(item.instrument_id != instrument_id for item in execution_bars):
        raise DataContractError(FailureCode.DATA_ROLE_MISMATCH, "execution Bar Instrument mismatch")
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
        if any(item.instrument_id != instrument_id for item in mark_bars):
            raise DataContractError(FailureCode.MARK_ROLE_INVALID, "mark Bar Instrument mismatch")
        if any(item.instrument_id != instrument_id for item in funding_events):
            raise DataContractError(FailureCode.DATA_ROLE_MISMATCH, "funding Instrument mismatch")
        role_results.append(
            validate_one_minute_grid(
                mark_bars,
                source_role=SourceRole.USDM_PERPETUAL_MARK_1M,
                time_range=normalized_time_range,
            ),
        )
        mark_identity = _normalized_identity(mark_bars)
        funding_identity = validate_funding_schedule(funding_events, funding_schedule)
        if funding_native_binding != FUNDING_NATIVE_BINDING_SINGLE:
            if funding_native_binding != FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY:
                raise DataContractError(
                    FailureCode.FUNDING_AMBIGUOUS,
                    "unsupported native funding binding",
                )
            funding_identity = canonical_sha256(
                {
                    "schedule_identity": funding_schedule.schedule_identity,
                    "events": [item.semantic_payload() for item in funding_events],
                    "native_binding": funding_native_binding,
                },
            )
    completeness = CompletenessResult(
        status="PASS",
        no_repairs=True,
        role_results=tuple(role_results),
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
