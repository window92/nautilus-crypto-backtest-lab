"""Strict M0 configuration contracts with no material implicit defaults."""

from __future__ import annotations

import json
import re
import types
from dataclasses import MISSING
from dataclasses import dataclass
from dataclasses import fields
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Self, get_args, get_origin, get_type_hints

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import to_canonical_builtins


NOT_APPLICABLE = "NOT_APPLICABLE"
DISABLED = "DISABLED"
NONE_MULTI_CURRENCY = "NONE_MULTI_CURRENCY"

NAUTILUS_VERSION = "2.0.0rc2"
NAUTILUS_SOURCE_REPOSITORY = "nautechsystems/nautilus_trader"
NAUTILUS_SOURCE_COMMIT = "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"
NAUTILUS_WHEEL_FILENAME = (
    "nautilus_trader-2.0.0rc2-cp312-cp312-manylinux_2_34_x86_64.whl"
)
NAUTILUS_WHEEL_SHA256 = "716169aca15bfb615a27610a9230e670dec5be3d4606fea591fe64eca145a5ac"

LATENCY_MODEL_PATH = "nautilus_trader.execution:StaticLatencyModel"
LATENCY_CONFIG_PATH = NOT_APPLICABLE
FILL_MODEL_PATH = "nautilus_trader.execution:DefaultFillModel"
FILL_CONFIG_PATH = NOT_APPLICABLE
MAKER_TAKER_FEE_MODEL_PATH = "nautilus_trader.execution:MakerTakerFeeModel"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")


class ConfigError(ValueError):
    code = "CONFIG_INVALID"


class MarketProfile(StrEnum):
    BINANCE_SPOT_CASH_LONG_ONLY = "BINANCE_SPOT_CASH_LONG_ONLY"
    BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING = (
        "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING"
    )


class RunPurpose(StrEnum):
    QUALIFICATION = "QUALIFICATION"
    RESEARCH = "RESEARCH"
    OFFICIAL = "OFFICIAL"


def _fail(path: str, message: str) -> None:
    raise ValueError(f"{path}: {message}")


def _require_equal(value: Any, expected: Any, path: str) -> None:
    if value != expected:
        _fail(path, f"must equal {expected!r}, received {value!r}")


def _require_sha256(value: str, path: str) -> None:
    if _SHA256.fullmatch(value) is None:
        _fail(path, "must be a lowercase 64-character SHA-256")


def _require_git_sha(value: str, path: str) -> None:
    if _GIT_SHA.fullmatch(value) is None:
        _fail(path, "must be a lowercase 40-character Git object ID")


def _require_nonempty(value: str, path: str) -> None:
    if not value or value.strip() != value:
        _fail(path, "must be a non-empty, whitespace-stable string")


def _require_utc(value: datetime, path: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail(path, "must be timezone-aware UTC")
    if value.utcoffset().total_seconds() != 0:
        _fail(path, "must use UTC")


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _freeze_field(instance: object, name: str) -> None:
    object.__setattr__(instance, name, _deep_freeze(getattr(instance, name)))


def _decode_json(payload: bytes) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ConfigError(f"duplicate JSON field {key!r}")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ConfigError(f"invalid JSON numeric constant {value!r}")

    try:
        return json.loads(
            payload,
            object_pairs_hook=object_pairs,
            parse_constant=invalid_constant,
        )
    except ConfigError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(str(exc)) from exc


def _decode_typed(value: Any, expected: Any, path: str) -> Any:
    if expected is Any:
        return value
    if expected is type(None):
        if value is not None:
            raise ConfigError(f"{path}: expected null")
        return None
    if expected is Decimal:
        if not isinstance(value, str):
            raise ConfigError(f"{path}: Decimal values must be JSON strings")
        try:
            return Decimal(value)
        except Exception as exc:
            raise ConfigError(f"{path}: invalid Decimal string") from exc
    if expected is datetime:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ConfigError(f"{path}: timestamp must be an explicit UTC string")
        try:
            return datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise ConfigError(f"{path}: invalid UTC timestamp") from exc
    if isinstance(expected, type) and issubclass(expected, StrEnum):
        if not isinstance(value, str):
            raise ConfigError(f"{path}: enum value must be a string")
        try:
            return expected(value)
        except ValueError as exc:
            raise ConfigError(f"{path}: unknown {expected.__name__} value {value!r}") from exc
    if isinstance(expected, type) and issubclass(expected, StrictModel):
        if not isinstance(value, dict):
            raise ConfigError(f"{path}: expected object")
        type_hints = get_type_hints(expected)
        model_fields = {field.name: field for field in fields(expected)}
        unknown = sorted(set(value) - set(model_fields))
        missing = sorted(
            name
            for name, field in model_fields.items()
            if name not in value and field.default is MISSING and field.default_factory is MISSING
        )
        if unknown:
            raise ConfigError(f"{path}: unknown fields {unknown}")
        if missing:
            raise ConfigError(f"{path}: missing fields {missing}")
        decoded = {
            name: _decode_typed(value[name], type_hints[name], f"{path}.{name}")
            for name in value
        }
        try:
            return expected(**decoded)
        except ConfigError:
            raise
        except (TypeError, ValueError) as exc:
            raise ConfigError(str(exc)) from exc

    origin = get_origin(expected)
    args = get_args(expected)
    if origin is tuple:
        if not isinstance(value, list):
            raise ConfigError(f"{path}: expected array")
        item_type = args[0]
        return tuple(
            _decode_typed(item, item_type, f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    if origin is dict:
        if not isinstance(value, dict):
            raise ConfigError(f"{path}: expected object")
        key_type, item_type = args
        if key_type is not str or any(not isinstance(key, str) for key in value):
            raise ConfigError(f"{path}: object keys must be strings")
        return {
            key: _decode_typed(item, item_type, f"{path}.{key}")
            for key, item in value.items()
        }
    if origin in (types.UnionType,):
        errors: list[str] = []
        for option in args:
            try:
                return _decode_typed(value, option, path)
            except ConfigError as exc:
                errors.append(str(exc))
        raise ConfigError(f"{path}: does not match allowed union: {errors}")
    if expected is bool:
        if type(value) is not bool:
            raise ConfigError(f"{path}: expected boolean")
        return value
    if expected is int:
        if type(value) is not int:
            raise ConfigError(f"{path}: expected integer")
        return value
    if expected is float:
        if type(value) is not float:
            raise ConfigError(f"{path}: expected float")
        return value
    if expected is str:
        if not isinstance(value, str):
            raise ConfigError(f"{path}: expected string")
        return value
    raise ConfigError(f"{path}: unsupported schema type {expected!r}")


class StrictModel:
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        dataclass(cls, frozen=True, kw_only=True)

    @classmethod
    def from_json_bytes(cls, payload: bytes) -> Self:
        value = _decode_json(payload)
        return _decode_typed(value, cls, "$")

    def to_json_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    def to_builtins(self) -> dict[str, Any]:
        value = model_to_builtins(self)
        if not isinstance(value, dict):  # pragma: no cover - all models are objects
            raise TypeError("model did not encode to a JSON object")
        return value


def model_to_builtins(value: Any) -> Any:
    return to_canonical_builtins(value)


class RuntimeLock(StrictModel):
    schema_version: int
    nautilus_version: str
    nautilus_source_repository: str
    nautilus_source_commit: str
    nautilus_wheel_filename: str
    nautilus_wheel_sha256: str
    nautilus_wheel_size_bytes: int
    nautilus_provenance_status: str
    runtime_implementation: str
    python_implementation: str
    python_version: str
    python_abi: str
    platform: str
    machine_architecture: str
    glibc_version: str
    dependency_lock_filename: str
    dependency_lock_sha256: str
    dependencies: dict[str, str]
    pip_version: str
    timezone: str
    locale: str

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            _fail("runtime_lock.schema_version", "only schema version 2 is supported")
        for name in ("nautilus_wheel_sha256", "dependency_lock_sha256"):
            _require_sha256(getattr(self, name), f"runtime_lock.{name}")
        _require_git_sha(self.nautilus_source_commit, "runtime_lock.nautilus_source_commit")
        if self.nautilus_wheel_size_bytes <= 0:
            _fail("runtime_lock.nautilus_wheel_size_bytes", "must be positive")
        if not self.dependencies:
            _fail("runtime_lock.dependencies", "complete resolved dependency set is required")
        for name, version in self.dependencies.items():
            _require_nonempty(name, "runtime_lock.dependencies.name")
            _require_nonempty(version, f"runtime_lock.dependencies.{name}")
        for name in (
            "nautilus_version",
            "nautilus_source_repository",
            "nautilus_wheel_filename",
            "nautilus_provenance_status",
            "runtime_implementation",
            "python_implementation",
            "python_version",
            "python_abi",
            "platform",
            "machine_architecture",
            "glibc_version",
            "dependency_lock_filename",
            "pip_version",
            "timezone",
            "locale",
        ):
            _require_nonempty(getattr(self, name), f"runtime_lock.{name}")
        _freeze_field(self, "dependencies")


class SourceRevision(StrictModel):
    """Immutable Git identity evidence, separate from execution Runtime Lock identity."""

    repository: str
    branch_ref: str
    git_commit: str
    git_tree: str
    clean_worktree: bool
    captured_at_utc: datetime

    def __post_init__(self) -> None:
        _require_nonempty(self.repository, "source_revision.repository")
        _require_nonempty(self.branch_ref, "source_revision.branch_ref")
        _require_git_sha(self.git_commit, "source_revision.git_commit")
        _require_git_sha(self.git_tree, "source_revision.git_tree")
        _require_utc(self.captured_at_utc, "source_revision.captured_at_utc")


class MoneyAmount(StrictModel):
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not self.amount.is_finite():
            _fail("money.amount", "must be finite")
        _require_nonempty(self.currency, "money.currency")


class InstrumentLeverage(StrictModel):
    instrument_id: str
    leverage: Decimal

    def __post_init__(self) -> None:
        _require_nonempty(self.instrument_id, "instrument_leverage.instrument_id")
        if not self.leverage.is_finite() or self.leverage <= 0:
            _fail("instrument_leverage.leverage", "must be finite and positive")


class SettlementPrice(StrictModel):
    instrument_id: str
    price: Decimal

    def __post_init__(self) -> None:
        _require_nonempty(self.instrument_id, "settlement_price.instrument_id")
        if not self.price.is_finite() or self.price <= 0:
            _fail("settlement_price.price", "must be finite and positive")


class ImportableActorBinding(StrictModel):
    actor_path: str
    config_path: str
    config: dict[str, Any]

    def __post_init__(self) -> None:
        _require_nonempty(self.actor_path, "module.actor_path")
        _require_nonempty(self.config_path, "module.config_path")
        _freeze_field(self, "config")


class MarginModelConfig(StrictModel):
    model_type: str
    config: dict[str, Any]

    def __post_init__(self) -> None:
        _require_equal(self.model_type, "leveraged", "margin_model.model_type")
        _require_equal(self.config, {}, "margin_model.config")
        _freeze_field(self, "config")


class FillModelConfig(StrictModel):
    fill_model_path: str
    config_path: str
    prob_fill_on_limit: float
    prob_slippage: float
    random_seed: int

    def __post_init__(self) -> None:
        _require_equal(self.fill_model_path, FILL_MODEL_PATH, "fill_model.fill_model_path")
        _require_equal(self.config_path, FILL_CONFIG_PATH, "fill_model.config_path")
        _require_equal(self.prob_fill_on_limit, 1.0, "fill_model.prob_fill_on_limit")
        _require_equal(self.prob_slippage, 1.0, "fill_model.prob_slippage")
        _require_equal(self.random_seed, 0, "fill_model.random_seed")


class LatencyModelConfig(StrictModel):
    latency_model_path: str
    config_path: str
    base_latency_nanos: int
    insert_latency_nanos: int
    update_latency_nanos: int
    cancel_latency_nanos: int
    effective_insert_latency_nanos: int
    effective_update_latency_nanos: int
    effective_cancel_latency_nanos: int

    def __post_init__(self) -> None:
        expected = {
            "latency_model_path": LATENCY_MODEL_PATH,
            "config_path": LATENCY_CONFIG_PATH,
            "base_latency_nanos": 60_000_000_000,
            "insert_latency_nanos": 0,
            "update_latency_nanos": 0,
            "cancel_latency_nanos": 0,
            "effective_insert_latency_nanos": 60_000_000_000,
            "effective_update_latency_nanos": 60_000_000_000,
            "effective_cancel_latency_nanos": 60_000_000_000,
        }
        for name, value in expected.items():
            _require_equal(getattr(self, name), value, f"latency_model.{name}")


class NautilusVenueConfig(StrictModel):
    name: str
    oms_type: str
    account_type: str
    starting_balances: tuple[MoneyAmount, ...]
    base_currency: str
    default_leverage: Decimal
    instrument_leverages: tuple[InstrumentLeverage, ...]
    margin_model: MarginModelConfig
    modules: tuple[ImportableActorBinding, ...]
    fill_model: FillModelConfig
    latency_model: LatencyModelConfig
    fee_model: str
    effective_fee_model_path: str
    book_type: str
    routing: bool
    reject_stop_orders: bool
    support_gtd_orders: bool
    support_contingent_orders: bool
    oto_trigger_mode: str
    use_position_ids: bool
    use_random_ids: bool
    use_reduce_only: bool
    use_message_queue: bool
    use_market_order_acks: bool
    bar_execution: bool
    bar_adaptive_high_low_ordering: bool
    trade_execution: bool
    liquidity_consumption: bool
    queue_position: bool
    allow_cash_borrowing: bool
    frozen_account: bool
    price_protection_points: int
    settlement_prices: tuple[SettlementPrice, ...]
    liquidation_enabled: bool
    liquidation_trigger_ratio: float
    liquidation_cancel_open_orders: bool

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "nautilus_venue_config.name")
        expected = {
            "name": "BINANCE",
            "oms_type": "NETTING",
            "base_currency": NONE_MULTI_CURRENCY,
            "default_leverage": Decimal("1"),
            "effective_fee_model_path": MAKER_TAKER_FEE_MODEL_PATH,
            "fee_model": MAKER_TAKER_FEE_MODEL_PATH,
            "book_type": "L1_MBP",
            "routing": False,
            "reject_stop_orders": True,
            "support_gtd_orders": False,
            "support_contingent_orders": False,
            "oto_trigger_mode": "PARTIAL",
            "use_position_ids": True,
            "use_random_ids": False,
            "use_reduce_only": True,
            "use_message_queue": True,
            "use_market_order_acks": False,
            "bar_execution": True,
            "bar_adaptive_high_low_ordering": False,
            "trade_execution": False,
            "liquidity_consumption": True,
            "queue_position": False,
            "allow_cash_borrowing": False,
            "frozen_account": False,
            "price_protection_points": 0,
            "liquidation_enabled": False,
            "liquidation_trigger_ratio": 1.0,
            "liquidation_cancel_open_orders": True,
        }
        for name, value in expected.items():
            _require_equal(getattr(self, name), value, f"nautilus_venue_config.{name}")
        if not self.starting_balances:
            _fail("nautilus_venue_config.starting_balances", "must not be empty")
        if any(item.amount <= 0 for item in self.starting_balances):
            _fail("nautilus_venue_config.starting_balances", "amounts must be positive")
        if any(item.leverage != Decimal("1") for item in self.instrument_leverages):
            _fail("nautilus_venue_config.instrument_leverages", "V1 leverage must be 1")
        if self.settlement_prices:
            _fail("nautilus_venue_config.settlement_prices", "V1 has no expiry settlement")


class CacheConfig(StrictModel):
    encoding: str
    timestamps_as_iso8601: bool
    persist_account_events: bool
    buffer_interval_ms: str
    bulk_read_batch_size: str
    use_trader_prefix: bool
    use_instance_id: bool
    flush_on_start: bool
    drop_instruments_on_reset: bool
    tick_capacity: int
    bar_capacity: int
    save_market_data: bool

    def __post_init__(self) -> None:
        expected = {
            "encoding": "json",
            "timestamps_as_iso8601": False,
            "persist_account_events": True,
            "buffer_interval_ms": DISABLED,
            "bulk_read_batch_size": DISABLED,
            "use_trader_prefix": True,
            "use_instance_id": False,
            "flush_on_start": False,
            "drop_instruments_on_reset": True,
            "tick_capacity": 10_000,
            "bar_capacity": 10_000,
            "save_market_data": False,
        }
        for name, value in expected.items():
            _require_equal(getattr(self, name), value, f"cache.{name}")


class MessageBusConfig(StrictModel):
    encoding: str
    encoding_market_data: str
    encoding_builtin: str
    timestamps_as_iso8601: bool
    buffer_interval_ms: str
    autotrim_mins: str
    autotrim_maxlen: str
    use_trader_prefix: bool
    use_trader_id: bool
    use_instance_id: bool
    streams_prefix: str
    stream_per_topic: bool
    external_streams: tuple[str, ...]
    types_filter: str
    heartbeat_interval_secs: str

    def __post_init__(self) -> None:
        expected = {
            "encoding": "json",
            "encoding_market_data": DISABLED,
            "encoding_builtin": DISABLED,
            "timestamps_as_iso8601": False,
            "buffer_interval_ms": DISABLED,
            "autotrim_mins": DISABLED,
            "autotrim_maxlen": DISABLED,
            "use_trader_prefix": True,
            "use_trader_id": True,
            "use_instance_id": False,
            "streams_prefix": "stream",
            "stream_per_topic": True,
            "external_streams": (),
            "types_filter": DISABLED,
            "heartbeat_interval_secs": DISABLED,
        }
        for name, value in expected.items():
            _require_equal(getattr(self, name), value, f"msgbus.{name}")


class DataEngineConfig(StrictModel):
    time_bars_interval_type: str
    time_bars_timestamp_on_close: bool
    time_bars_skip_first_non_full_bar: bool
    time_bars_build_with_no_updates: bool
    time_bars_origin_offset: dict[str, str]
    time_bars_build_delay: int
    validate_data_sequence: bool
    buffer_deltas: bool
    emit_quotes_from_book: bool
    emit_quotes_from_book_depths: bool
    external_clients: tuple[str, ...]
    debug: bool
    disable_historical_cache: bool

    def __post_init__(self) -> None:
        expected = {
            "time_bars_interval_type": "left-open",
            "time_bars_timestamp_on_close": True,
            "time_bars_skip_first_non_full_bar": True,
            "time_bars_build_with_no_updates": False,
            "time_bars_origin_offset": {},
            "time_bars_build_delay": 0,
            "validate_data_sequence": False,
            "buffer_deltas": False,
            "emit_quotes_from_book": False,
            "emit_quotes_from_book_depths": False,
            "external_clients": (),
            "debug": False,
            "disable_historical_cache": False,
        }
        for name, value in expected.items():
            _require_equal(getattr(self, name), value, f"data_engine.{name}")
        _freeze_field(self, "time_bars_origin_offset")


class RiskEngineConfig(StrictModel):
    bypass: bool
    max_order_submit_rate: str
    max_order_modify_rate: str
    max_notional_per_order: dict[str, str]
    debug: bool

    def __post_init__(self) -> None:
        expected = {
            "bypass": False,
            "max_order_submit_rate": "100/00:00:01",
            "max_order_modify_rate": "100/00:00:01",
            "max_notional_per_order": {},
            "debug": False,
        }
        for name, value in expected.items():
            _require_equal(getattr(self, name), value, f"risk_engine.{name}")
        _freeze_field(self, "max_notional_per_order")


class ExecEngineConfig(StrictModel):
    load_cache: bool
    manage_own_order_books: bool
    snapshot_orders: bool
    snapshot_positions: bool
    snapshot_positions_interval_secs: str
    external_clients: tuple[str, ...]
    allow_overfills: bool
    carry_replay_events_on_reopen: bool
    purge_closed_orders_interval_mins: str
    purge_closed_orders_buffer_mins: str
    purge_closed_positions_interval_mins: str
    purge_closed_positions_buffer_mins: str
    purge_account_events_interval_mins: str
    purge_account_events_lookback_mins: str
    purge_from_database: bool
    debug: bool

    def __post_init__(self) -> None:
        expected = {
            "load_cache": True,
            "manage_own_order_books": False,
            "snapshot_orders": False,
            "snapshot_positions": False,
            "snapshot_positions_interval_secs": DISABLED,
            "external_clients": (),
            "allow_overfills": False,
            "carry_replay_events_on_reopen": False,
            "purge_closed_orders_interval_mins": DISABLED,
            "purge_closed_orders_buffer_mins": DISABLED,
            "purge_closed_positions_interval_mins": DISABLED,
            "purge_closed_positions_buffer_mins": DISABLED,
            "purge_account_events_interval_mins": DISABLED,
            "purge_account_events_lookback_mins": DISABLED,
            "purge_from_database": False,
            "debug": False,
        }
        for name, value in expected.items():
            _require_equal(getattr(self, name), value, f"exec_engine.{name}")


class PortfolioConfig(StrictModel):
    use_mark_prices: bool
    use_mark_xrates: bool
    bar_updates: bool
    convert_to_account_base_currency: bool
    equity_curve: bool
    min_account_state_logging_interval_ms: str
    snapshot_interval_ms: str
    debug: bool

    def __post_init__(self) -> None:
        expected = {
            "use_mark_xrates": False,
            "bar_updates": True,
            "convert_to_account_base_currency": True,
            "equity_curve": True,
            "min_account_state_logging_interval_ms": DISABLED,
            "snapshot_interval_ms": DISABLED,
            "debug": False,
        }
        for name, value in expected.items():
            _require_equal(getattr(self, name), value, f"portfolio.{name}")


class NautilusEngineConfig(StrictModel):
    environment: str
    trader_id: str
    instance_id_policy: str
    cache: CacheConfig
    msgbus: MessageBusConfig
    data_engine: DataEngineConfig
    risk_engine: RiskEngineConfig
    exec_engine: ExecEngineConfig
    portfolio: PortfolioConfig
    controller: str
    load_state: bool
    save_state: bool
    shutdown_on_error: bool
    bypass_logging: bool
    run_analysis: bool
    timeout_connection: int
    timeout_reconciliation: int
    timeout_portfolio: int
    timeout_disconnection: int
    delay_post_stop: int
    timeout_shutdown: int

    def __post_init__(self) -> None:
        _require_nonempty(self.trader_id, "nautilus_engine_config.trader_id")
        expected = {
            "environment": "BACKTEST",
            "instance_id_policy": "RUNTIME_GENERATED_NON_SEMANTIC",
            "controller": DISABLED,
            "load_state": False,
            "save_state": False,
            "shutdown_on_error": False,
            "bypass_logging": False,
            "run_analysis": True,
            "timeout_connection": 60,
            "timeout_reconciliation": 30,
            "timeout_portfolio": 10,
            "timeout_disconnection": 10,
            "delay_post_stop": 10,
            "timeout_shutdown": 5,
        }
        for name, value in expected.items():
            _require_equal(getattr(self, name), value, f"nautilus_engine_config.{name}")


class NautilusDataConfig(StrictModel):
    catalog_path: str
    catalog_fs_protocol: str
    catalog_fs_storage_options: dict[str, str]
    catalog_fs_rust_storage_options: dict[str, str]
    data_type: str
    instrument_id: str
    start_time: datetime
    end_time: datetime
    filter_expr: str
    client_id: str
    metadata: dict[str, str]
    bar_spec: str
    instrument_ids: tuple[str, ...]
    bar_types: tuple[str, ...]
    optimize_file_loading: bool

    def __post_init__(self) -> None:
        for name in ("catalog_path", "data_type", "instrument_id", "bar_spec"):
            _require_nonempty(getattr(self, name), f"nautilus_data_config.{name}")
        _require_equal(self.data_type, "Bar", "nautilus_data_config.data_type")
        _require_equal(self.catalog_fs_protocol, "file", "nautilus_data_config.catalog_fs_protocol")
        _require_equal(self.filter_expr, NOT_APPLICABLE, "nautilus_data_config.filter_expr")
        _require_equal(self.client_id, NOT_APPLICABLE, "nautilus_data_config.client_id")
        _require_equal(self.optimize_file_loading, False, "nautilus_data_config.optimize_file_loading")
        _require_utc(self.start_time, "nautilus_data_config.start_time")
        _require_utc(self.end_time, "nautilus_data_config.end_time")
        if self.start_time >= self.end_time:
            _fail("nautilus_data_config", "start_time must be before end_time")
        for name in (
            "catalog_fs_storage_options",
            "catalog_fs_rust_storage_options",
            "metadata",
        ):
            _freeze_field(self, name)


class FeeAssumption(StrictModel):
    maker_fee: Decimal
    taker_fee: Decimal
    explicit_zero_fee: bool
    reason: str
    claim_class: str

    def __post_init__(self) -> None:
        if not self.maker_fee.is_finite() or not self.taker_fee.is_finite():
            _fail("fee_assumption", "fee rates must be finite")
        _require_nonempty(self.reason, "fee_assumption.reason")
        _require_equal(self.claim_class, "ESTIMATED_FEE", "fee_assumption.claim_class")
        both_zero = self.maker_fee == 0 and self.taker_fee == 0
        if both_zero != self.explicit_zero_fee:
            _fail(
                "fee_assumption.explicit_zero_fee",
                "must be true exactly when both fee rates are explicitly zero",
            )


class NamedSeed(StrictModel):
    name: str
    value: int

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "random_seed.name")


class LabRunConfig(StrictModel):
    run_id: str
    run_purpose: RunPurpose
    runtime_lock_sha256: str
    market_profile: MarketProfile
    instrument_id: str
    dataset_release_id: str
    strategy_spec_id: str
    initial_capital: MoneyAmount
    warmup_start: datetime
    scoring_start: datetime
    scoring_end_exclusive: datetime
    execution_bar_type: str
    signal_bar_types: tuple[str, ...]
    nautilus_engine_config: NautilusEngineConfig
    nautilus_venue_config: NautilusVenueConfig
    nautilus_data_config: tuple[NautilusDataConfig, ...]
    fee_assumption: FeeAssumption
    funding_binding: str
    mark_binding: str
    random_seeds: tuple[NamedSeed, ...]
    research_protocol_id: str
    terminal_policy: str
    network_policy: str
    execution_model: str
    allowed_order_types: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("run_id", "instrument_id", "execution_bar_type"):
            _require_nonempty(getattr(self, name), f"lab_run_config.{name}")
        for name in ("runtime_lock_sha256", "dataset_release_id", "strategy_spec_id"):
            _require_sha256(getattr(self, name), f"lab_run_config.{name}")
        if self.research_protocol_id != NOT_APPLICABLE:
            _require_sha256(self.research_protocol_id, "lab_run_config.research_protocol_id")
        for name in ("warmup_start", "scoring_start", "scoring_end_exclusive"):
            _require_utc(getattr(self, name), f"lab_run_config.{name}")
        if not self.warmup_start <= self.scoring_start < self.scoring_end_exclusive:
            _fail(
                "lab_run_config.study_window",
                "must satisfy warmup_start <= scoring_start < scoring_end_exclusive",
            )
        if not self.initial_capital.amount.is_finite() or self.initial_capital.amount <= 0:
            _fail("lab_run_config.initial_capital", "must be finite and positive")
        if len(self.nautilus_venue_config.starting_balances) != 1:
            _fail("nautilus_venue_config.starting_balances", "V1 requires one allocation")
        starting = self.nautilus_venue_config.starting_balances[0]
        if starting != self.initial_capital:
            _fail(
                "nautilus_venue_config.starting_balances",
                "must equal frozen initial_capital",
            )
        if not self.signal_bar_types:
            _fail("lab_run_config.signal_bar_types", "must not be empty")
        if len(set(self.signal_bar_types)) != len(self.signal_bar_types):
            _fail("lab_run_config.signal_bar_types", "must not contain duplicates")
        if not self.nautilus_data_config:
            _fail("lab_run_config.nautilus_data_config", "must not be empty")
        if not any(
            item.instrument_id == self.instrument_id
            and item.data_type == "Bar"
            and item.bar_spec == "1-MINUTE-LAST"
            and item.start_time <= self.warmup_start
            and item.end_time >= self.scoring_end_exclusive
            for item in self.nautilus_data_config
        ):
            _fail(
                "lab_run_config.nautilus_data_config",
                "must cover the full run interval for the configured instrument",
            )
        if any(item.instrument_id != self.instrument_id for item in self.nautilus_data_config):
            _fail(
                "lab_run_config.nautilus_data_config",
                "Official V1 config may bind only the Run Instrument",
            )
        expected_execution_bar_type = f"{self.instrument_id}-1-MINUTE-LAST-EXTERNAL"
        _require_equal(
            self.execution_bar_type,
            expected_execution_bar_type,
            "lab_run_config.execution_bar_type",
        )
        seed_names = [seed.name for seed in self.random_seeds]
        if len(set(seed_names)) != len(seed_names):
            _fail("lab_run_config.random_seeds", "seed names must be unique")
        if {seed.name: seed.value for seed in self.random_seeds}.get("fill_model") != 0:
            _fail("lab_run_config.random_seeds", "fill_model seed 0 must be explicit")

        expected = {
            "terminal_policy": "MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
            "network_policy": "OFFLINE",
            "execution_model": "BAR_BASED_ESTIMATED_EXECUTION",
            "allowed_order_types": ("MARKET",),
        }
        for name, value in expected.items():
            _require_equal(getattr(self, name), value, f"lab_run_config.{name}")

        venue = self.nautilus_venue_config
        portfolio = self.nautilus_engine_config.portfolio
        if self.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
            _require_equal(venue.account_type, "CASH", "nautilus_venue_config.account_type")
            _require_equal(portfolio.use_mark_prices, False, "portfolio.use_mark_prices")
            _require_equal(self.funding_binding, NOT_APPLICABLE, "funding_binding")
            _require_equal(self.mark_binding, NOT_APPLICABLE, "mark_binding")
        else:
            _require_equal(venue.account_type, "MARGIN", "nautilus_venue_config.account_type")
            _require_equal(portfolio.use_mark_prices, True, "portfolio.use_mark_prices")
            _require_sha256(self.funding_binding, "funding_binding")
            _require_sha256(self.mark_binding, "mark_binding")

    @property
    def material_payload(self) -> dict[str, Any]:
        payload = self.to_builtins()
        payload.pop("run_id")
        for item in payload["nautilus_data_config"]:
            item.pop("catalog_path")
        return payload

    @property
    def config_sha256(self) -> str:
        return canonical_sha256(self.material_payload)
