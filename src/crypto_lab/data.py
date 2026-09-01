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
from dataclasses import fields
from dataclasses import replace
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
from crypto_lab.timestamps import unix_ns_to_utc_datetime
from crypto_lab.timestamps import utc_datetime_to_ns


ONE_MINUTE_NS = 60_000_000_000
SPOT_MICROSECOND_TRANSITION = date(2025, 1, 1)
NOT_APPLICABLE = "NOT_APPLICABLE"
NOT_AVAILABLE = "NOT_AVAILABLE"
NORMALIZER_VERSION = "binance-public-data-v1-m2.3"
INSTRUMENT_REPAIR_NORMALIZER_VERSION = "binance-public-data-v1-m2.4"
FULL_RAW_INVENTORY_NORMALIZER_VERSION = "binance-public-data-v1-m2.5"
M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION = (
    "binance-public-data-v1-m2.5-qualification"
)
FULL_RAW_INVENTORY_NORMALIZER_VERSIONS = frozenset(
    {
        FULL_RAW_INVENTORY_NORMALIZER_VERSION,
        M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION,
    },
)
FUNDING_NATIVE_BINDING_SINGLE = "SINGLE_SOURCE_EVENT"
FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY = (
    "NAUTILUS_2_0_0RC2_INTERVAL_BOUNDARY_REPEAT_ONCE"
)
FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR = (
    "NAUTILUS_2_0_0RC2_EXPLICIT_SOURCE_BOUNDARY_PAIR"
)
HISTORICAL_NORMALIZER_VERSIONS = frozenset({"binance-public-data-v1-m2.1"})
ACTIVE_NORMALIZER_VERSIONS = frozenset(
    {
        "binance-public-data-v1-m2.2",
        NORMALIZER_VERSION,
        INSTRUMENT_REPAIR_NORMALIZER_VERSION,
        *FULL_RAW_INVENTORY_NORMALIZER_VERSIONS,
    },
)
LEGACY_RELEASE_SCHEMA_VERSIONS = frozenset(
    {"binance-public-data-v1-m2.1", "binance-public-data-v1-m2.2"},
)

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INTEGER = re.compile(r"0|[1-9][0-9]*\Z")
_PROVENANCE_ROLE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_FULL_RAW_INVENTORY_HOSTS = {
    MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY: frozenset(
        {
            "api.binance.com",
            "data.binance.vision",
            "data-api.binance.vision",
            "www.binance.com",
        },
    ),
    MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING: frozenset(
        {"data.binance.vision", "fapi.binance.com", "www.binance.com"},
    ),
}
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
        try:
            self.code = FailureCode(code).value
        except ValueError as exc:
            raise ValueError(f"unknown SSOT failure code: {code!r}") from exc
        self.evidence = MappingProxyType(dict(evidence or {}))
        super().__init__(f"{self.code}: {message}")


class SourceRole(StrEnum):
    SPOT_EXECUTION_1M = "SPOT_EXECUTION_1M"
    USDM_PERPETUAL_EXECUTION_1M = "USDM_PERPETUAL_EXECUTION_1M"
    USDM_PERPETUAL_MARK_1M = "USDM_PERPETUAL_MARK_1M"
    USDM_PERPETUAL_FUNDING = "USDM_PERPETUAL_FUNDING"
    SPOT_INSTRUMENT_METADATA = "SPOT_INSTRUMENT_METADATA"
    SPOT_HISTORICAL_ORDER_GRID = "SPOT_HISTORICAL_ORDER_GRID"
    USDM_PERPETUAL_INSTRUMENT_METADATA = "USDM_PERPETUAL_INSTRUMENT_METADATA"
    USDM_PERPETUAL_FUNDING_METADATA = "USDM_PERPETUAL_FUNDING_METADATA"
    USDM_PERPETUAL_HISTORICAL_ORDER_GRID = "USDM_PERPETUAL_HISTORICAL_ORDER_GRID"
    USDM_EXECUTION_TIMESTAMP_PROBE = "USDM_EXECUTION_TIMESTAMP_PROBE"
    USDM_MARK_TIMESTAMP_PROBE = "USDM_MARK_TIMESTAMP_PROBE"
    USDM_FUNDING_TIMESTAMP_PROBE = "USDM_FUNDING_TIMESTAMP_PROBE"
    PUBLISHER_CHECKSUM = "PUBLISHER_CHECKSUM"
    BINANCE_PUBLIC_DATA_CONTRACT = "BINANCE_PUBLIC_DATA_CONTRACT"
    BINANCE_OFFICIAL_API_CONTRACT = "BINANCE_OFFICIAL_API_CONTRACT"


class TimestampUnit(StrEnum):
    MILLISECONDS = "MILLISECONDS"
    MICROSECONDS = "MICROSECONDS"


class CoverageDisposition(StrEnum):
    REAL_OFFICIAL_BAR = "REAL_OFFICIAL_BAR"
    DERIVED_FROM_OFFICIAL_TRADES = "DERIVED_FROM_OFFICIAL_TRADES"
    VERIFIED_NO_TRADE_INTERVAL = "VERIFIED_NO_TRADE_INTERVAL"
    SOURCE_CONFLICT = "SOURCE_CONFLICT"
    SOURCE_INCOMPLETE = "SOURCE_INCOMPLETE"
    UNRESOLVED_GAP = "UNRESOLVED_GAP"


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


class RawInventoryOrigin(StrictModel):
    """One exact acquisition-level origin for a release Raw object."""

    observation_id: str
    source_role: str
    exact_locator: str
    exact_query_json: str
    http_status: int
    validation_status: str
    delivery_classification: str

    def __post_init__(self) -> None:
        _require_sha256(self.observation_id, "raw_inventory.origin.observation_id")
        if _PROVENANCE_ROLE.fullmatch(self.source_role) is None:
            raise ConfigError("raw_inventory.origin.source_role: invalid provenance role")
        parsed = urlparse(self.exact_locator)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ConfigError("raw_inventory.origin.exact_locator: exact HTTPS locator required")
        try:
            query = _decode_json(self.exact_query_json.encode("utf-8"))
        except ConfigError as exc:
            raise ConfigError("raw_inventory.origin.exact_query_json: invalid canonical JSON") from exc
        if canonical_json_bytes(query).decode("utf-8") != self.exact_query_json:
            raise ConfigError("raw_inventory.origin.exact_query_json: canonical JSON required")
        if not 100 <= self.http_status <= 599:
            raise ConfigError("raw_inventory.origin.http_status: invalid HTTP status")
        if self.validation_status == "RAW_PRESERVED":
            if not 200 <= self.http_status <= 299 or self.delivery_classification != NOT_APPLICABLE:
                raise ConfigError("raw_inventory.origin: preserved response status is inconsistent")
        elif self.validation_status == "UNAVAILABLE":
            if (
                self.http_status != 404
                or self.delivery_classification
                != "REDUNDANT_OFFICIAL_DELIVERY_ROLE_UNAVAILABLE"
                or "MARK" not in self.source_role
            ):
                raise ConfigError("raw_inventory.origin: unavailable proof is not the locked Mark 404")
        else:
            raise ConfigError("raw_inventory.origin.validation_status: unsupported value")


class PublisherChecksumBinding(StrictModel):
    """Publisher checksum bytes binding one official archive filename and hash."""

    checksum_raw_object_sha256: str
    exact_filename: str
    publisher_sha256: str

    def __post_init__(self) -> None:
        _require_sha256(
            self.checksum_raw_object_sha256,
            "raw_inventory.publisher_checksum.checksum_raw_object_sha256",
        )
        _require_sha256(
            self.publisher_sha256,
            "raw_inventory.publisher_checksum.publisher_sha256",
        )
        if not self.exact_filename or Path(self.exact_filename).name != self.exact_filename:
            raise ConfigError("raw_inventory.publisher_checksum.exact_filename: basename required")


class RawInventoryObject(StrictModel):
    """One unique content-addressed Raw object in a complete release inventory."""

    raw_object_sha256: str
    byte_size: int
    instrument: str
    market_profile: MarketProfile
    origins: tuple[RawInventoryOrigin, ...]
    publisher_checksum_bindings: tuple[PublisherChecksumBinding, ...]

    def __post_init__(self) -> None:
        _require_sha256(self.raw_object_sha256, "raw_inventory.object.raw_object_sha256")
        if self.byte_size <= 0:
            raise ConfigError("raw_inventory.object.byte_size: accepted Raw object must be non-empty")
        if self.instrument != "BTCUSDT":
            raise ConfigError("raw_inventory.object.instrument: V1 requires BTCUSDT")
        if not self.origins:
            raise ConfigError("raw_inventory.object.origins: acquisition origin required")
        origin_order = tuple(
            sorted(
                self.origins,
                key=lambda item: (
                    item.source_role,
                    item.exact_locator,
                    item.exact_query_json,
                    item.observation_id,
                ),
            ),
        )
        if origin_order != self.origins or len(set(self.origins)) != len(self.origins):
            raise ConfigError("raw_inventory.object.origins: canonical unique ordering required")
        allowed_hosts = _FULL_RAW_INVENTORY_HOSTS[self.market_profile]
        if any(urlparse(item.exact_locator).hostname not in allowed_hosts for item in self.origins):
            raise ConfigError("raw_inventory.object.origins: locator host is not allowed for profile")
        checksum_order = tuple(
            sorted(
                self.publisher_checksum_bindings,
                key=lambda item: (
                    item.exact_filename,
                    item.publisher_sha256,
                    item.checksum_raw_object_sha256,
                ),
            ),
        )
        if (
            checksum_order != self.publisher_checksum_bindings
            or len(set(self.publisher_checksum_bindings)) != len(self.publisher_checksum_bindings)
        ):
            raise ConfigError(
                "raw_inventory.object.publisher_checksum_bindings: canonical unique ordering required",
            )
        if any(
            item.publisher_sha256 != self.raw_object_sha256
            for item in self.publisher_checksum_bindings
        ):
            raise ConfigError("raw_inventory.object: publisher checksum does not bind archive hash")


class DatasetRawInventory(StrictModel):
    """Complete direct and indirect Raw provenance for one DatasetRelease."""

    schema_version: int
    raw_inventory_identity: str
    market_profile: MarketProfile
    instrument_id: str
    data_window_identity: str
    source_reconciliation_identity: str
    raw_object_count: int
    raw_objects: tuple[RawInventoryObject, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ConfigError("raw_inventory.schema_version: only version 1 is supported")
        for name in (
            "raw_inventory_identity",
            "data_window_identity",
            "source_reconciliation_identity",
        ):
            _require_sha256(getattr(self, name), f"raw_inventory.{name}")
        if self.instrument_id not in {"BTCUSDT.BINANCE", "BTCUSDT-PERP.BINANCE"}:
            raise ConfigError("raw_inventory.instrument_id: unsupported V1 Instrument")
        if self.raw_object_count <= 0 or self.raw_object_count != len(self.raw_objects):
            raise ConfigError("raw_inventory.raw_object_count: exact positive count required")
        object_order = tuple(sorted(self.raw_objects, key=lambda item: item.raw_object_sha256))
        hashes = tuple(item.raw_object_sha256 for item in self.raw_objects)
        if object_order != self.raw_objects or len(set(hashes)) != len(hashes):
            raise ConfigError("raw_inventory.raw_objects: canonical unique ordering required")
        if any(item.market_profile is not self.market_profile for item in self.raw_objects):
            raise ConfigError("raw_inventory.raw_objects: Market Profile mismatch")
        object_hashes = set(hashes)
        if any(
            binding.checksum_raw_object_sha256 not in object_hashes
            for item in self.raw_objects
            for binding in item.publisher_checksum_bindings
        ):
            raise ConfigError("raw_inventory: publisher checksum Raw object is absent")
        if canonical_sha256(self.material_payload()) != self.raw_inventory_identity:
            raise ConfigError("raw_inventory_identity does not match canonical material payload")

    def material_payload(self) -> dict[str, Any]:
        payload = self.to_builtins()
        payload.pop("raw_inventory_identity", None)
        return payload

    @classmethod
    def create(cls, **values: Any) -> DatasetRawInventory:
        material = {"schema_version": 1, **values}
        material.pop("raw_inventory_identity", None)
        return cls(
            raw_inventory_identity=canonical_sha256(material),
            **material,
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


class MinuteDisposition(StrictModel):
    """One exact minute of execution coverage; verified no-trade is not a Bar."""

    open_time_ns: int
    disposition: CoverageDisposition
    canonical_bar_identity: str
    proof_identity: str
    source_reconciliation_identity: str

    def __post_init__(self) -> None:
        if self.open_time_ns < 0 or self.open_time_ns % ONE_MINUTE_NS:
            raise ConfigError("minute_disposition.open_time_ns: must be an aligned UTC minute")
        if self.disposition in {
            CoverageDisposition.REAL_OFFICIAL_BAR,
            CoverageDisposition.DERIVED_FROM_OFFICIAL_TRADES,
        }:
            _require_sha256(
                self.canonical_bar_identity,
                "minute_disposition.canonical_bar_identity",
            )
            if self.proof_identity != NOT_APPLICABLE:
                raise ConfigError("minute_disposition: accepted Bar cannot carry no-trade proof")
        elif self.disposition is CoverageDisposition.VERIFIED_NO_TRADE_INTERVAL:
            if self.canonical_bar_identity != NOT_APPLICABLE:
                raise ConfigError("minute_disposition: verified no-trade cannot carry a Bar identity")
            _require_sha256(self.proof_identity, "minute_disposition.proof_identity")
        else:
            if self.canonical_bar_identity != NOT_APPLICABLE:
                raise ConfigError("minute_disposition: blocking minute cannot carry a Bar identity")
        _require_sha256(
            self.source_reconciliation_identity,
            "minute_disposition.source_reconciliation_identity",
        )


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
    data_window_identity: str = NOT_APPLICABLE
    partition_geometry_identity: str = NOT_APPLICABLE
    minute_coverage_identity: str = NOT_APPLICABLE
    source_reconciliation_identity: str = NOT_APPLICABLE
    derived_validation_identity: str = NOT_APPLICABLE
    data_tool_lock_identity: str = NOT_APPLICABLE
    data_quality_exposure_identity: str = NOT_APPLICABLE
    raw_inventory: DatasetRawInventory | str = NOT_APPLICABLE

    def __post_init__(self) -> None:
        if self.schema_version not in {1, 2}:
            raise ConfigError("dataset_release.schema_version: only versions 1 and 2 are supported")
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
        if self.normalizer_version not in {
            *ACTIVE_NORMALIZER_VERSIONS,
            *HISTORICAL_NORMALIZER_VERSIONS,
        }:
            raise ConfigError("dataset_release.normalizer_version: unsupported version")
        if self.normalizer_version in {
            INSTRUMENT_REPAIR_NORMALIZER_VERSION,
            *FULL_RAW_INVENTORY_NORMALIZER_VERSIONS,
        }:
            for name in (
                "data_window_identity",
                "partition_geometry_identity",
                "minute_coverage_identity",
                "source_reconciliation_identity",
                "data_quality_exposure_identity",
            ):
                _require_sha256(getattr(self, name), f"dataset_release.{name}")
        if self.normalizer_version in {
            INSTRUMENT_REPAIR_NORMALIZER_VERSION,
            FULL_RAW_INVENTORY_NORMALIZER_VERSION,
        }:
            for name in ("derived_validation_identity", "data_tool_lock_identity"):
                _require_sha256(getattr(self, name), f"dataset_release.{name}")
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
        if self.normalizer_version in ACTIVE_NORMALIZER_VERSIONS:
            _validate_source_bindings(
                market_profile=self.market_profile,
                instrument_id=self.instrument_id,
                source_objects=self.source_objects,
                normalized_time_range=self.normalized_time_range,
            )
        roles = {item.source_role for item in self.source_objects}
        if self.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
            required = {SourceRole.SPOT_EXECUTION_1M, SourceRole.SPOT_INSTRUMENT_METADATA}
            if self.normalizer_version in {
                INSTRUMENT_REPAIR_NORMALIZER_VERSION,
                FULL_RAW_INVENTORY_NORMALIZER_VERSION,
            }:
                required.add(SourceRole.SPOT_HISTORICAL_ORDER_GRID)
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
            if self.normalizer_version in {
                INSTRUMENT_REPAIR_NORMALIZER_VERSION,
                FULL_RAW_INVENTORY_NORMALIZER_VERSION,
            }:
                required.add(SourceRole.USDM_PERPETUAL_HISTORICAL_ORDER_GRID)
            forbidden = {SourceRole.SPOT_EXECUTION_1M, SourceRole.SPOT_INSTRUMENT_METADATA}
            if not required.issubset(roles) or roles & forbidden:
                raise ConfigError("dataset_release: Perpetual source roles are invalid")
            _require_sha256(self.funding_data_identity, "dataset_release.funding_data_identity")
            _require_sha256(self.mark_data_identity, "dataset_release.mark_data_identity")
        if self.normalizer_version in FULL_RAW_INVENTORY_NORMALIZER_VERSIONS:
            if self.schema_version != 2 or not isinstance(self.raw_inventory, DatasetRawInventory):
                raise ConfigError(
                    "dataset_release.raw_inventory: schema v2 typed full inventory required",
                )
            inventory = self.raw_inventory
            if (
                inventory.market_profile is not self.market_profile
                or inventory.instrument_id != self.instrument_id
                or inventory.data_window_identity != self.data_window_identity
                or inventory.source_reconciliation_identity
                != self.source_reconciliation_identity
            ):
                raise ConfigError("dataset_release.raw_inventory: release binding mismatch")
            inventory_by_hash = {
                item.raw_object_sha256: item for item in inventory.raw_objects
            }
            for source in self.source_objects:
                item = inventory_by_hash.get(source.sha256)
                if (
                    item is None
                    or item.byte_size != source.byte_size
                    or item.instrument != source.instrument
                    or item.market_profile.value != source.market_profile
                ):
                    raise ConfigError(
                        "dataset_release.raw_inventory: direct source object is absent or mismatched",
                    )
                if source.publisher_checksum != NOT_AVAILABLE and not any(
                    checksum.exact_filename == source.exact_filename
                    and checksum.publisher_sha256 == source.publisher_checksum
                    for checksum in item.publisher_checksum_bindings
                ):
                    raise ConfigError(
                        "dataset_release.raw_inventory: direct publisher checksum is unbound",
                    )
        elif self.schema_version != 1 or self.raw_inventory != NOT_APPLICABLE:
            raise ConfigError(
                "dataset_release.raw_inventory: only the v2 full-inventory contract may declare it",
            )
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
        elif normalizer in ACTIVE_NORMALIZER_VERSIONS:
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
        if self.schema_version == 1:
            payload.pop("raw_inventory", None)
        if self.normalizer_version in LEGACY_RELEASE_SCHEMA_VERSIONS:
            for name in (
                "data_window_identity",
                "partition_geometry_identity",
                "minute_coverage_identity",
                "source_reconciliation_identity",
                "derived_validation_identity",
                "data_tool_lock_identity",
                "data_quality_exposure_identity",
            ):
                payload.pop(name, None)
        if self.normalizer_version in HISTORICAL_NORMALIZER_VERSIONS:
            for source in payload["source_objects"]:
                source.pop("conflicts_with_sha256", None)
        return payload

    def to_json_bytes(self) -> bytes:
        payload = self.to_builtins()
        if self.schema_version == 1:
            payload.pop("raw_inventory", None)
        return canonical_json_bytes(payload)

    def with_created_at(self, value: datetime) -> DatasetRelease:
        return replace(self, created_at_utc=value)

    @property
    def is_current_contract(self) -> bool:
        return self.normalizer_version in ACTIVE_NORMALIZER_VERSIONS

    @property
    def has_full_raw_inventory(self) -> bool:
        return (
            self.normalizer_version in FULL_RAW_INVENTORY_NORMALIZER_VERSIONS
            and isinstance(self.raw_inventory, DatasetRawInventory)
        )

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
    raw_rate_text: str = NOT_AVAILABLE

    def __post_init__(self) -> None:
        if self.raw_rate_text == NOT_AVAILABLE:
            return
        if not self.raw_rate_text or self.raw_rate_text.strip() != self.raw_rate_text:
            raise DataContractError(
                FailureCode.DATA_SOURCE_INVALID,
                "funding raw rate lexeme is empty or whitespace-normalized",
            )
        try:
            raw_decimal = Decimal(self.raw_rate_text)
        except InvalidOperation as exc:
            raise DataContractError(
                FailureCode.DATA_SOURCE_INVALID,
                "funding raw rate lexeme is not Decimal text",
            ) from exc
        if not raw_decimal.is_finite() or raw_decimal != self.funding_rate:
            raise DataContractError(
                FailureCode.DATA_HASH_MISMATCH,
                "funding raw rate lexeme does not equal the normalized Decimal",
            )
        expected_event_key = canonical_sha256(
            {
                "instrument_id": self.instrument_id,
                "calc_time_ns": self.calc_time_ns,
                "funding_interval_hours": self.funding_interval_hours,
                "funding_rate": self.funding_rate,
                "funding_rate_raw_lexeme": self.raw_rate_text,
            },
        )
        if self.event_key != expected_event_key:
            raise DataContractError(
                FailureCode.DATA_HASH_MISMATCH,
                "funding event key does not bind the original rate lexeme",
            )

    def semantic_payload(self) -> dict[str, Any]:
        payload = {
            "event_key": self.event_key,
            "instrument_id": self.instrument_id,
            "calc_time_ns": self.calc_time_ns,
            "funding_interval_hours": self.funding_interval_hours,
            "funding_rate": self.funding_rate,
        }
        if self.raw_rate_text != NOT_AVAILABLE:
            payload["funding_rate_raw_lexeme"] = self.raw_rate_text
        return payload


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
    funding_source_events: tuple[dict[str, Any], ...] = ()
    market_state_acceptance: dict[str, Any] | None = None


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
    try:
        return utc_datetime_to_ns(value)
    except (TypeError, ValueError) as exc:
        raise DataContractError(FailureCode.DATA_TIMESTAMP_INVALID, "timestamp must use UTC") from exc


def _ns_to_datetime(value: int) -> datetime:
    try:
        return unix_ns_to_utc_datetime(value)
    except (TypeError, ValueError) as exc:
        raise DataContractError(
            FailureCode.DATA_TIMESTAMP_INVALID,
            "timestamp cannot be represented by the UTC evidence schema",
        ) from exc


def _validate_source_locator(role: SourceRole, locator: str) -> None:
    parsed = urlparse(locator)
    if parsed.scheme != "https" or parsed.params or parsed.query and role not in {
        SourceRole.SPOT_INSTRUMENT_METADATA,
        SourceRole.SPOT_HISTORICAL_ORDER_GRID,
        SourceRole.BINANCE_OFFICIAL_API_CONTRACT,
        SourceRole.USDM_EXECUTION_TIMESTAMP_PROBE,
        SourceRole.USDM_MARK_TIMESTAMP_PROBE,
        SourceRole.USDM_FUNDING_TIMESTAMP_PROBE,
        SourceRole.USDM_PERPETUAL_HISTORICAL_ORDER_GRID,
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
            parsed.netloc in {"api.binance.com", "data-api.binance.vision"}
            and path == "/api/v3/exchangeInfo"
            and set(query) == {"symbol"}
            and len(query["symbol"]) == 1
            and re.fullmatch(r"[A-Z0-9]+", query["symbol"][0]) is not None
        )
    elif role is SourceRole.SPOT_HISTORICAL_ORDER_GRID:
        query = parse_qs(parsed.query, keep_blank_values=True)
        valid = (
            parsed.netloc == "www.binance.com"
            and path == "/bapi/composite/v1/public/cms/article/detail/query"
            and query == {"articleCode": ["6925d618ab6b47e2936cc4614eaad64b"]}
        )
    elif role is SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA:
        valid = parsed.netloc == "fapi.binance.com" and path == "/fapi/v1/exchangeInfo"
    elif role is SourceRole.USDM_PERPETUAL_FUNDING_METADATA:
        valid = parsed.netloc == "fapi.binance.com" and path == "/fapi/v1/fundingInfo"
    elif role is SourceRole.USDM_PERPETUAL_HISTORICAL_ORDER_GRID:
        query = parse_qs(parsed.query, keep_blank_values=True)
        valid = (
            parsed.netloc == "www.binance.com"
            and path == "/bapi/composite/v1/public/cms/article/detail/query"
            and query == {"articleCode": ["81e6795b0bae49828cbd52479094a987"]}
        )
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


def minute_coverage_identity(
    dispositions: tuple[MinuteDisposition, ...] | list[MinuteDisposition],
) -> str:
    ordered = sorted(dispositions, key=lambda item: item.open_time_ns)
    return canonical_sha256([item.to_builtins() for item in ordered])


def validate_sparse_one_minute_grid(
    bars: tuple[NormalizedBar, ...] | list[NormalizedBar],
    *,
    source_role: SourceRole,
    time_range: TimeRange,
    dispositions: tuple[MinuteDisposition, ...] | list[MinuteDisposition],
) -> RoleCompleteness:
    """Validate a complete minute-disposition grid over sparse real Spot Bars."""

    if time_range.start_ns % ONE_MINUTE_NS or time_range.end_ns % ONE_MINUTE_NS:
        raise DataContractError(FailureCode.DATA_TIMESTAMP_INVALID, "grid endpoints are not minute aligned")
    expected = tuple(range(time_range.start_ns, time_range.end_ns, ONE_MINUTE_NS))
    by_minute: dict[int, MinuteDisposition] = {}
    for item in dispositions:
        if item.open_time_ns in by_minute:
            raise DataContractError(
                FailureCode.DATA_DUPLICATE_CONFLICT,
                f"duplicate minute disposition {item.open_time_ns}",
            )
        by_minute[item.open_time_ns] = item
    missing = [minute for minute in expected if minute not in by_minute]
    extras = sorted(set(by_minute) - set(expected))
    if missing or extras:
        raise DataContractError(
            FailureCode.DATA_GAP,
            f"minute-disposition grid mismatch missing={missing} extras={extras}",
        )
    blocking = [
        item.open_time_ns
        for item in by_minute.values()
        if item.disposition in {
            CoverageDisposition.SOURCE_CONFLICT,
            CoverageDisposition.SOURCE_INCOMPLETE,
            CoverageDisposition.UNRESOLVED_GAP,
        }
    ]
    if blocking:
        raise DataContractError(FailureCode.DATA_GAP, f"blocking minute dispositions: {blocking}")

    by_bar_time: dict[int, NormalizedBar] = {}
    for item in bars:
        if item.source_role is not source_role:
            raise DataContractError(FailureCode.DATA_ROLE_MISMATCH, "grid contains a different source role")
        if item.interval_start_ns in by_bar_time:
            raise DataContractError(
                FailureCode.DATA_DUPLICATE_CONFLICT,
                f"duplicate execution Bar {item.interval_start_ns}",
            )
        if (
            item.available_at_ns != item.interval_end_exclusive_ns
            or item.interval_end_exclusive_ns != item.interval_start_ns + ONE_MINUTE_NS
        ):
            raise DataContractError(
                FailureCode.DATA_TIMESTAMP_INVALID,
                "available_at is not the completion boundary",
            )
        by_bar_time[item.interval_start_ns] = item
    expected_bar_minutes = {
        minute
        for minute, item in by_minute.items()
        if item.disposition in {
            CoverageDisposition.REAL_OFFICIAL_BAR,
            CoverageDisposition.DERIVED_FROM_OFFICIAL_TRADES,
        }
    }
    if set(by_bar_time) != expected_bar_minutes:
        raise DataContractError(
            FailureCode.DATA_GAP,
            "canonical Bar inventory does not exactly match accepted minute dispositions",
        )
    for minute, bar in by_bar_time.items():
        if bar.source_row_sha256 != by_minute[minute].canonical_bar_identity:
            raise DataContractError(
                FailureCode.DATA_HASH_MISMATCH,
                f"canonical Bar identity mismatch at {minute}",
            )
    return RoleCompleteness(
        source_role=source_role,
        expected_count=len(expected),
        actual_count=len(by_minute),
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
                "funding_rate_raw_lexeme": row[2],
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
                raw_rate_text=row[2],
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
    official_source: str | None = None,
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
        "official_source": official_source
        or f"https://api.binance.com/api/v3/exchangeInfo?symbol={raw_symbol}",
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


def bind_lossless_instrument_representation(
    metadata: InstrumentMetadata,
    *,
    price_precision: int,
    size_precision: int,
    price_increment: Decimal | None = None,
    size_increment: Decimal | None = None,
    representation_evidence: dict[str, Any],
    economic_order_grid_evidence: dict[str, Any],
) -> InstrumentMetadata:
    """Create additive Instrument metadata without changing any economic value.

    ``price_precision`` and ``size_precision`` describe the common Nautilus
    runtime representation needed by official market data.  The increment and
    limit fields remain the independent Binance economic order grid.  A caller
    may supply a historically proven price increment, but every other numeric
    value is copied exactly from the source metadata.
    """

    if price_precision < 0 or size_precision < 0:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "runtime representation precision must be non-negative",
        )
    if not representation_evidence or not economic_order_grid_evidence:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "representation and economic-grid evidence are both required",
        )
    effective_price_increment = (
        metadata.price_increment if price_increment is None else price_increment
    )
    effective_size_increment = (
        metadata.size_increment if size_increment is None else size_increment
    )
    if not effective_price_increment.is_finite() or effective_price_increment <= 0:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "historical price increment must be positive and finite",
        )
    if not effective_size_increment.is_finite() or effective_size_increment <= 0:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "historical size increment must be positive and finite",
        )
    # These conversions are validation-only: quantize equality in
    # ``_decimal_string`` rejects rounding or truncation.
    for field, value, precision in (
        ("price_increment", effective_price_increment, price_precision),
        ("min_price", metadata.min_price, price_precision),
        ("max_price", metadata.max_price, price_precision),
        ("size_increment", effective_size_increment, size_precision),
        ("min_quantity", metadata.min_quantity, size_precision),
        ("max_quantity", metadata.max_quantity, size_precision),
    ):
        _decimal_string(value, precision, field=field)

    definition = metadata.to_builtins()["official_definition"]
    definition["nautilus_runtime_representation"] = {
        "price_precision": price_precision,
        "size_precision": size_precision,
        "normalization": "LOSSLESS_ZERO_PADDING_ONLY",
        "evidence": representation_evidence,
    }
    definition["binance_economic_order_grid"] = {
        "price_increment": effective_price_increment,
        "size_increment": effective_size_increment,
        "validation": "NUMERIC_INCREMENT_AND_LIMITS_BEFORE_NAUTILUS_SUBMISSION",
        "evidence": economic_order_grid_evidence,
    }
    values = {field.name: getattr(metadata, field.name) for field in fields(metadata)}
    values.pop("schema_version")
    values.pop("instrument_metadata_identity")
    values.update(
        {
            "price_precision": price_precision,
            "size_precision": size_precision,
            "price_increment": effective_price_increment,
            "size_increment": effective_size_increment,
            "official_definition": definition,
            "limitations": tuple(
                item
                for item in metadata.limitations
                if not (
                    item == "EXACT_HISTORICAL_VENUE_RULES_UNAVAILABLE"
                    and economic_order_grid_evidence.get("historical_exact_for_window") is True
                )
            )
            + (
                *(
                    ("HISTORICAL_ORDER_GRID_BOUND_TO_OFFICIAL_EVIDENCE",)
                    if economic_order_grid_evidence.get("historical_exact_for_window") is True
                    else ()
                ),
                "REPRESENTATION_PRECISION_IS_NOT_AN_ORDER_GRID",
            ),
        },
    )
    if effective_size_increment != metadata.size_increment:
        if metadata.market_lot_size_step_size != 0:
            raise DataContractError(
                FailureCode.INSTRUMENT_METADATA_INVALID,
                "historical size increment override is ambiguous with non-zero MARKET_LOT_SIZE step",
            )
        values["lot_size_step_size"] = effective_size_increment
        effective_min, effective_max, derived_step, derivation = _market_quantity_intersection(
            lot_min=metadata.lot_size_min_quantity,
            lot_max=metadata.lot_size_max_quantity,
            lot_step=effective_size_increment,
            market_min=metadata.market_lot_size_min_quantity,
            market_max=metadata.market_lot_size_max_quantity,
            market_step=metadata.market_lot_size_step_size,
        )
        if derived_step != effective_size_increment:
            raise DataContractError(
                FailureCode.INSTRUMENT_METADATA_INVALID,
                "historical size increment does not resolve to the effective MARKET grid",
            )
        values["min_quantity"] = effective_min
        values["max_quantity"] = effective_max
        values["effective_market_derivation"] = (
            *derivation,
            "HISTORICAL_LOT_SIZE_STEP_FROM_OFFICIAL_BINANCE_ANNOUNCEMENT",
        )
    return InstrumentMetadata.create(**values)


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
    filters = instrument.info.get("binance_quantity_filters", {})
    if not filters:
        # Qualification fixtures predating the Binance source-binding contract
        # remain governed by the native Instrument min/max/increment checks
        # above. Active m2.4 Binance releases always carry the audited filters.
        return
    if not isinstance(filters, dict):
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "native Instrument has no auditable Binance quantity filters",
        )
    for filter_name in ("LOT_SIZE", "MARKET_LOT_SIZE"):
        rule = filters.get(filter_name)
        if not isinstance(rule, dict):
            raise DataContractError(
                FailureCode.INSTRUMENT_METADATA_INVALID,
                f"native Instrument is missing {filter_name}",
            )
        try:
            rule_minimum = Decimal(str(rule["minQty"]))
            rule_maximum = Decimal(str(rule["maxQty"]))
            rule_step = Decimal(str(rule["stepSize"]))
        except Exception as exc:
            raise DataContractError(
                FailureCode.INSTRUMENT_METADATA_INVALID,
                f"native Instrument has malformed {filter_name}",
            ) from exc
        if rule_minimum > 0 and value < rule_minimum:
            raise DataContractError(
                FailureCode.INSTRUMENT_METADATA_INVALID,
                f"MARKET quantity {value} is below {filter_name} minimum {rule_minimum}",
            )
        if rule_maximum > 0 and value > rule_maximum:
            raise DataContractError(
                FailureCode.INSTRUMENT_METADATA_INVALID,
                f"MARKET quantity {value} is above {filter_name} maximum {rule_maximum}",
            )
        if rule_step > 0 and value % rule_step != 0:
            raise DataContractError(
                FailureCode.INSTRUMENT_METADATA_INVALID,
                f"MARKET quantity {value} is outside {filter_name} step {rule_step}",
            )


def validate_limit_order_price(
    instrument: CurrencyPair | CryptoPerpetual,
    price: Price,
) -> None:
    """Reject a precision-compatible LIMIT price outside the Binance tick grid."""

    if price.precision != instrument.price_precision:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "LIMIT price precision differs from the native Instrument",
        )
    value = price.as_decimal()
    minimum = None if instrument.min_price is None else instrument.min_price.as_decimal()
    maximum = None if instrument.max_price is None else instrument.max_price.as_decimal()
    increment = instrument.price_increment.as_decimal()
    if minimum is not None and value < minimum:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            f"LIMIT price {value} is below native minimum {minimum}",
        )
    if maximum is not None and value > maximum:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            f"LIMIT price {value} is above native maximum {maximum}",
        )
    if increment <= 0 or value % increment != 0:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            f"LIMIT price {value} is not on native grid {increment}",
        )


def lossless_runtime_quantity_text(value: str, precision: int) -> str:
    """Return the exact Decimal value zero-padded to the native precision."""

    try:
        quantity = Decimal(value)
    except Exception as exc:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "order quantity is not an exact Decimal string",
        ) from exc
    if _source_precision(quantity) > precision:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            "order quantity representation may only be zero-padded, never reduced",
        )
    return _decimal_string(quantity, precision, field="order_quantity")


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
    material = tuple(bars)
    price_precision = metadata.price_precision
    volume_precision = metadata.size_precision
    result: list[Bar] = []
    for item in material:
        if item.instrument_id != metadata.instrument_id or item.source_role not in {
            SourceRole.SPOT_EXECUTION_1M,
            SourceRole.USDM_PERPETUAL_EXECUTION_1M,
        }:
            raise DataContractError(FailureCode.DATA_ROLE_MISMATCH, "execution Bar role mismatch")
        result.append(
            Bar(
                bar_type,
                Price.from_str(_decimal_string(item.open, price_precision, field="open")),
                Price.from_str(_decimal_string(item.high, price_precision, field="high")),
                Price.from_str(_decimal_string(item.low, price_precision, field="low")),
                Price.from_str(_decimal_string(item.close, price_precision, field="close")),
                Quantity.from_str(
                    _decimal_string(item.volume, volume_precision, field="volume"),
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
    # One Arrow/Nautilus identity is required for the whole instrument.  The
    # representation is therefore bound to the Instrument rather than inferred
    # from a batch. ``_decimal_string`` proves zero-padding is lossless.
    batch_precision = metadata.price_precision
    result: list[MarkPriceUpdate] = []
    for item in bars:
        if (
            item.source_role is not SourceRole.USDM_PERPETUAL_MARK_1M
            or item.instrument_id != metadata.instrument_id
        ):
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
        FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR,
    }:
        raise DataContractError(
            FailureCode.FUNDING_AMBIGUOUS,
            f"unsupported native funding binding {native_binding!r}",
        )
    repetitions = (
        2
        if native_binding in {
            FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
            FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR,
        }
        else 1
    )
    if any(item.instrument_id != metadata.instrument_id for item in events):
        raise DataContractError(
            FailureCode.DATA_ROLE_MISMATCH,
            "funding event Instrument does not match metadata",
        )
    return tuple(
        FundingRateUpdate(
            instrument_id,
            item.funding_rate,
            item.calc_time_ns,
            item.calc_time_ns,
            interval=item.funding_interval_hours * 60,
            next_funding_ns=(
                item.calc_time_ns
                if native_binding == FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR
                else None
            ),
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
    semantic_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    instruments = catalog.instruments([instrument_id])
    bars = catalog.query_bars([bar_type])
    marks = catalog.query_mark_price_updates([instrument_id])
    result = {
        "schema": "nautilus-semantic-inventory-v1",
        "instruments": [item.to_dict() for item in instruments],
        "execution_bars": [_bar_projection(item) for item in bars],
        "mark_price_updates": [_mark_projection(item) for item in marks],
        "funding_rate_updates": [_funding_projection(item) for item in funding_updates],
    }
    if semantic_binding is not None:
        result["release_binding"] = semantic_binding
    return result


def build_nautilus_catalog(
    catalog_root: Path,
    *,
    metadata: InstrumentMetadata,
    execution_bars: tuple[NormalizedBar, ...] | list[NormalizedBar],
    mark_bars: tuple[NormalizedBar, ...] | list[NormalizedBar] = (),
    funding_events: tuple[FundingEvent, ...] | list[FundingEvent] = (),
    funding_native_binding: str = FUNDING_NATIVE_BINDING_SINGLE,
    semantic_binding: dict[str, Any] | None = None,
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
        semantic_binding=semantic_binding,
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
    verify_dataset_raw_inventory(release, data_root)

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
    if any(str(mark.instrument_id) != metadata.instrument_id for mark in marks):
        raise DataContractError(
            FailureCode.MARK_ROLE_INVALID,
            "catalog mark Instrument does not match release metadata",
        )

    funding_updates: tuple[FundingRateUpdate, ...] = ()
    funding_native_binding = FUNDING_NATIVE_BINDING_SINGLE
    funding_source_event_count = 0
    funding_source_events: tuple[dict[str, Any], ...] = ()
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
        if any(
            not isinstance(item, dict)
            or item.get("instrument_id") != metadata.instrument_id
            for item in events
        ):
            raise DataContractError(
                FailureCode.DATA_ROLE_MISMATCH,
                "catalog funding Instrument does not match release metadata",
            )
        if release.normalizer_version in FULL_RAW_INVENTORY_NORMALIZER_VERSIONS and any(
            "funding_rate_raw_lexeme" not in item for item in events
        ):
            raise DataContractError(
                FailureCode.DATA_SOURCE_INVALID,
                "full-inventory funding evidence omits the original rate lexeme",
            )
        funding_native_binding = str(
            funding_payload.get("native_binding", FUNDING_NATIVE_BINDING_SINGLE),
        )
        if funding_native_binding not in {
            FUNDING_NATIVE_BINDING_SINGLE,
            FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
            FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR,
        }:
            raise DataContractError(
                FailureCode.FUNDING_AMBIGUOUS,
                "funding evidence declares an unsupported native binding",
            )
        funding_source_event_count = len(events)
        funding_source_events = tuple(dict(item) for item in events)
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
                        raw_rate_text=str(item.get("funding_rate_raw_lexeme", NOT_AVAILABLE)),
                    )
                    for item in events
                ),
                metadata,
                native_binding=funding_native_binding,
            )
        except Exception as exc:
            raise DataContractError(FailureCode.FUNDING_AMBIGUOUS, "funding event is malformed") from exc

    market_state_acceptance: dict[str, Any] | None = None
    if release.normalizer_version in {
        INSTRUMENT_REPAIR_NORMALIZER_VERSION,
        FULL_RAW_INVENTORY_NORMALIZER_VERSION,
    }:
        acceptance_path = release_root / f"{release.derived_validation_identity}.market-state.json"
        try:
            acceptance_payload = _strict_json(acceptance_path.read_bytes())
        except Exception as exc:
            raise DataContractError(
                FailureCode.DATASET_RELEASE_STALE,
                "executable market-state acceptance does not resolve",
            ) from exc
        declared_acceptance = acceptance_payload.pop("validation_identity", None)
        if (
            declared_acceptance != release.derived_validation_identity
            or canonical_sha256(acceptance_payload) != declared_acceptance
            or acceptance_payload.get("status") != "PASS"
            or acceptance_payload.get("catalog_identity") != release.catalog_identity
            or acceptance_payload.get("instrument_metadata_identity")
            != release.instrument_metadata_identity
        ):
            raise DataContractError(
                FailureCode.DATASET_RELEASE_STALE,
                "executable market-state acceptance identity mismatch",
            )
        market_state_acceptance = acceptance_payload

    inventory = catalog_semantic_inventory(
        catalog,
        instrument_id=release.instrument_id,
        bar_type=str(_bar_type(release.instrument_id)),
        funding_updates=funding_updates,
        semantic_binding=(
            {
                "data_window_identity": release.data_window_identity,
                "partition_geometry_identity": release.partition_geometry_identity,
                "minute_coverage_identity": release.minute_coverage_identity,
                "normalized_time_range": release.normalized_time_range.to_builtins(),
            }
            if release.normalizer_version in {
                NORMALIZER_VERSION,
                INSTRUMENT_REPAIR_NORMALIZER_VERSION,
                FULL_RAW_INVENTORY_NORMALIZER_VERSION,
            }
            and release.data_tool_lock_identity != NOT_APPLICABLE
            else None
        ),
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
        funding_source_events=funding_source_events,
        market_state_acceptance=market_state_acceptance,
    )


def _stream_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_dataset_raw_inventory(release: DatasetRelease, data_root: Path) -> None:
    """Verify every direct/indirect Raw byte claimed by a DatasetRelease.

    The v2 contract verifies the complete content-addressed inventory, including
    publisher checksum response bytes and unavailable-response proofs. Historical
    v1 releases retain their direct-source-only behavior so their identities and
    read-only replay meaning are not rewritten.
    """

    raw_root = Path(data_root) / "raw" / "sha256"
    if release.has_full_raw_inventory:
        assert isinstance(release.raw_inventory, DatasetRawInventory)
        raw_objects = tuple(
            (item.raw_object_sha256, item.byte_size)
            for item in release.raw_inventory.raw_objects
        )
    else:
        raw_objects = tuple((item.sha256, item.byte_size) for item in release.source_objects)
    verified: dict[str, Path] = {}
    for raw_sha256, expected_size in raw_objects:
        path = raw_root / raw_sha256[:2] / f"{raw_sha256}.blob"
        try:
            stat_result = path.lstat()
        except FileNotFoundError as exc:
            raise DataContractError(
                FailureCode.DATA_HASH_MISMATCH,
                f"Raw object {raw_sha256} is missing",
            ) from exc
        if path.is_symlink() or not path.is_file() or stat_result.st_size != expected_size:
            raise DataContractError(
                FailureCode.DATA_HASH_MISMATCH,
                f"Raw object {raw_sha256} is not an exact regular-file binding",
            )
        if _stream_sha256(path) != raw_sha256:
            raise DataContractError(
                FailureCode.DATA_HASH_MISMATCH,
                f"Raw object {raw_sha256} content hash mismatch",
            )
        verified[raw_sha256] = path
    if not release.has_full_raw_inventory:
        return
    assert isinstance(release.raw_inventory, DatasetRawInventory)
    for raw_object in release.raw_inventory.raw_objects:
        for binding in raw_object.publisher_checksum_bindings:
            checksum_path = verified[binding.checksum_raw_object_sha256]
            try:
                lines = tuple(
                    line.strip()
                    for line in checksum_path.read_text(encoding="ascii").splitlines()
                    if line.strip()
                )
            except (OSError, UnicodeError) as exc:
                raise DataContractError(
                    FailureCode.DATA_SOURCE_INVALID,
                    "publisher checksum Raw object is not canonical ASCII",
                ) from exc
            expected = (binding.publisher_sha256, binding.exact_filename)
            match = (
                re.fullmatch(r"([0-9a-f]{64})[ \t]+\*?([^\s]+)", lines[0])
                if len(lines) == 1
                else None
            )
            actual = (match.group(1), match.group(2)) if match is not None else ()
            if actual != expected:
                raise DataContractError(
                    FailureCode.DATA_SOURCE_INVALID,
                    "publisher checksum bytes do not bind the declared archive",
                )


def assert_official_active_raw_inventory(
    active: set[str],
    declared_union: set[str],
    extra_checksums: list[tuple[str, str]] | tuple[tuple[str, str], ...] = (),
) -> None:
    """Fail closed when Official DuckDB active objects are not the release union."""

    extra_active = sorted(active - declared_union)
    missing_active = sorted(declared_union - active)
    if extra_active or missing_active or len(active) != len(declared_union):
        raise RuntimeError(
            "DATASET_RAW_INVENTORY_MISMATCH: DuckDB active raw_objects differ from "
            "DatasetRelease inventories extra="
            f"{len(extra_active)} missing={len(missing_active)} "
            f"active={len(active)} declared_union={len(declared_union)}",
        )
    if extra_checksums:
        raise RuntimeError(
            "DATASET_RAW_INVENTORY_MISMATCH: publisher_checksums contain "
            f"inactive archives count={len(extra_checksums)}",
        )


RESEARCH_REBUILD_VALIDATION_SCHEMA = (
    "free-official-binance-deterministic-rebuild-validation-v2-full-raw-inventory"
)
RESEARCH_REBUILD_VALIDATION_REF = (
    "evidence/audit/adversarial-remediation-002/data-rebuild-validation.json"
)


def validate_research_dataset_rebuild_proof(
    release: DatasetRelease,
    value: dict[str, Any],
) -> None:
    """Bind a research DatasetRelease to the independent four-way DB proof.

    This validates a persisted, Git-bound proof.  It deliberately does not
    replace the independent DuckDB validator which creates that proof.
    """

    expected_root_fields = {
        "schema",
        "status",
        "duckdb_version",
        "comparison",
        "primary_readonly_gate",
        "independent_readonly_gate",
        "catalog_physical_comparison",
        "materialized_release_artifacts",
        "nautilus_catalog_validation",
        "strategy_run",
        "official_trial",
        "network_used",
    }
    if (
        release.normalizer_version != FULL_RAW_INVENTORY_NORMALIZER_VERSION
        or not release.has_full_raw_inventory
        or set(value) != expected_root_fields
        or value.get("schema") != RESEARCH_REBUILD_VALIDATION_SCHEMA
        or value.get("status") != "PASS"
        or value.get("duckdb_version") != "1.4.5"
        or value.get("strategy_run") is not False
        or value.get("official_trial") is not False
        or value.get("network_used") is not False
        or value.get("primary_readonly_gate") != value.get("independent_readonly_gate")
    ):
        raise DataContractError(
            FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
            "research DatasetRelease rebuild root proof differs",
        )
    comparison = value.get("comparison")
    materialized = value.get("materialized_release_artifacts")
    catalogs = value.get("nautilus_catalog_validation")
    gate = value.get("primary_readonly_gate")
    if not all(isinstance(item, dict) for item in (comparison, materialized, catalogs, gate)):
        raise DataContractError(
            FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
            "research DatasetRelease rebuild proof is incomplete",
        )
    expected_profiles = {
        MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY.value,
        MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value,
    }
    inventory_results = gate.get("full_raw_inventory_results")
    if (
        set(materialized) != expected_profiles
        or set(catalogs) != expected_profiles
        or not isinstance(inventory_results, list)
        or len(inventory_results) != 2
    ):
        raise DataContractError(
            FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
            "research DatasetRelease rebuild profile set differs",
        )
    by_profile: dict[str, dict[str, Any]] = {}
    for item in inventory_results:
        if (
            not isinstance(item, dict)
            or set(item)
            != {
                "market_profile",
                "dataset_release_id",
                "raw_inventory_identity",
                "raw_object_count",
                "four_way_equality",
            }
            or not isinstance(item.get("market_profile"), str)
        ):
            raise DataContractError(
                FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
                "research DatasetRelease four-way proof shape differs",
            )
        by_profile[str(item["market_profile"])] = item
    if set(by_profile) != expected_profiles:
        raise DataContractError(
            FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
            "research DatasetRelease four-way profile set differs",
        )
    release_ids = sorted(
        str(item.get("dataset_release_id"))
        for item in materialized.values()
        if isinstance(item, dict)
    )
    catalog_ids = sorted(
        str(item.get("catalog_identity"))
        for item in materialized.values()
        if isinstance(item, dict)
    )
    if (
        len(release_ids) != 2
        or sorted(comparison.get("dataset_release_ids", [])) != release_ids
        or sorted(comparison.get("catalog_identities", [])) != catalog_ids
        or comparison.get("status") != "PASS"
    ):
        raise DataContractError(
            FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
            "research DatasetRelease comparison identities differ",
        )
    inventory = release.raw_inventory
    assert isinstance(inventory, DatasetRawInventory)
    profile = release.market_profile.value
    selected_materialized = materialized.get(profile)
    selected_catalog = catalogs.get(profile)
    selected_gate = by_profile.get(profile)
    if (
        not isinstance(selected_materialized, dict)
        or not isinstance(selected_catalog, dict)
        or not isinstance(selected_gate, dict)
        or selected_materialized.get("dataset_release_id") != release.dataset_release_id
        or selected_materialized.get("catalog_identity") != release.catalog_identity
        or selected_materialized.get("raw_inventory_identity")
        != inventory.raw_inventory_identity
        or selected_materialized.get("raw_inventory_object_count")
        != inventory.raw_object_count
        or selected_catalog.get("status") != "PASS"
        or selected_catalog.get("catalog_identity") != release.catalog_identity
        or selected_gate.get("dataset_release_id") != release.dataset_release_id
        or selected_gate.get("raw_inventory_identity") != inventory.raw_inventory_identity
        or selected_gate.get("raw_object_count") != inventory.raw_object_count
        or selected_gate.get("four_way_equality") is not True
    ):
        raise DataContractError(
            FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
            "research DatasetRelease does not match its four-way rebuild proof",
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
            SourceRole.SPOT_HISTORICAL_ORDER_GRID,
        }
        range_roles = {SourceRole.SPOT_EXECUTION_1M: "1m"}
    else:
        allowed = {
            SourceRole.USDM_PERPETUAL_EXECUTION_1M,
            SourceRole.USDM_PERPETUAL_MARK_1M,
            SourceRole.USDM_PERPETUAL_FUNDING,
            SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA,
            SourceRole.USDM_PERPETUAL_HISTORICAL_ORDER_GRID,
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
        elif source.source_role is SourceRole.SPOT_HISTORICAL_ORDER_GRID:
            if (
                source.exact_filename
                != "binance-spot-btcusdt-step-size-2021-08-26.json"
                or source.requested_time_range != NOT_APPLICABLE
            ):
                raise DataContractError(
                    FailureCode.DATA_SOURCE_INVALID,
                    "Spot historical order-grid binding is inconsistent",
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
        elif source.source_role is SourceRole.USDM_PERPETUAL_HISTORICAL_ORDER_GRID:
            if (
                source.exact_filename
                != "binance-usdm-btcusdt-tick-size-2022-02-15.json"
                or source.requested_time_range != NOT_APPLICABLE
            ):
                raise DataContractError(
                    FailureCode.DATA_SOURCE_INVALID,
                    "USD-M historical order-grid binding is inconsistent",
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
    minute_dispositions: tuple[MinuteDisposition, ...] | list[MinuteDisposition] = (),
    data_window_identity: str | None = None,
    partition_geometry_identity: str | None = None,
    minute_coverage_identity_value: str | None = None,
    source_reconciliation_identity: str | None = None,
    derived_validation_identity: str = NOT_APPLICABLE,
    data_tool_lock_identity: str = NOT_APPLICABLE,
    data_quality_exposure_identity: str | None = None,
    normalizer_version: str = NORMALIZER_VERSION,
    raw_inventory: DatasetRawInventory | None = None,
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
    if minute_dispositions:
        calculated_coverage_identity = minute_coverage_identity(minute_dispositions)
        if (
            minute_coverage_identity_value is not None
            and minute_coverage_identity_value != calculated_coverage_identity
        ):
            raise DataContractError(
                FailureCode.DATA_HASH_MISMATCH,
                "declared minute-coverage identity does not match dispositions",
            )
        role_results = [
            validate_sparse_one_minute_grid(
                execution_bars,
                source_role=execution_role,
                time_range=normalized_time_range,
                dispositions=minute_dispositions,
            ),
        ]
    else:
        calculated_coverage_identity = canonical_sha256(
            [item.semantic_payload() for item in execution_bars],
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
            if funding_native_binding not in {
                FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
                FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR,
            }:
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
    if normalizer_version in FULL_RAW_INVENTORY_NORMALIZER_VERSIONS:
        if raw_inventory is None:
            raise DataContractError(
                FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
                "full-inventory Dataset Release requires typed Raw inventory",
            )
        schema_version = 2
        raw_inventory_value: DatasetRawInventory | str = raw_inventory
    else:
        if raw_inventory is not None:
            raise DataContractError(
                FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
                "typed Raw inventory is restricted to the full-inventory contract",
            )
        schema_version = 1
        raw_inventory_value = NOT_APPLICABLE
    values = {
        "schema_version": schema_version,
        "market_profile": market_profile,
        "instrument_id": instrument_id,
        "source_objects": bindings,
        "normalized_time_range": normalized_time_range,
        "execution_bar_interval": "1m",
        "available_signal_bar_intervals": (normalized_time_range,),
        "instrument_metadata_identity": instrument_metadata.instrument_metadata_identity,
        "funding_data_identity": funding_identity,
        "mark_data_identity": mark_identity,
        "normalizer_version": normalizer_version,
        "timestamp_rules_identity": timestamp_rules_identity(),
        "catalog_identity": catalog_identity,
        "completeness_result": completeness,
        "data_window_identity": data_window_identity or canonical_sha256(
            {"normalized_time_range": normalized_time_range},
        ),
        "partition_geometry_identity": partition_geometry_identity or canonical_sha256(
            {"normalized_time_range": normalized_time_range, "partition_geometry": "LEGACY_SINGLE_RANGE"},
        ),
        "minute_coverage_identity": minute_coverage_identity_value or calculated_coverage_identity,
        "source_reconciliation_identity": source_reconciliation_identity or canonical_sha256(
            [item.to_builtins() for item in bindings],
        ),
        "derived_validation_identity": derived_validation_identity,
        "data_tool_lock_identity": data_tool_lock_identity,
        "data_quality_exposure_identity": data_quality_exposure_identity or canonical_sha256(
            {"classification": "NO_ADDITIONAL_DATA_QUALITY_EXPOSURE"},
        ),
        "raw_inventory": raw_inventory_value,
    }
    identity_values = dict(values)
    if schema_version == 1:
        identity_values.pop("raw_inventory", None)
    identity = canonical_sha256(identity_values)
    return DatasetRelease(dataset_release_id=identity, created_at_utc=created_at_utc, **values)


__all__ = [
    "DatasetRawInventory",
    "DatasetRelease",
    "FULL_RAW_INVENTORY_NORMALIZER_VERSION",
    "FULL_RAW_INVENTORY_NORMALIZER_VERSIONS",
    "M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION",
    "PublisherChecksumBinding",
    "RawInventoryObject",
    "RawInventoryOrigin",
    "RESEARCH_REBUILD_VALIDATION_SCHEMA",
    "RESEARCH_REBUILD_VALIDATION_REF",
    "assert_official_active_raw_inventory",
    "validate_research_dataset_rebuild_proof",
    "verify_dataset_raw_inventory",
]
