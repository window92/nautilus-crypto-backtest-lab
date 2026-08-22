"""Mechanical bindings from frozen project models to public NautilusTrader v2 APIs."""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.backtest import BacktestDataConfig
from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.backtest import BacktestEngineConfig
from nautilus_trader.backtest import BacktestVenueConfig
from nautilus_trader.common import CacheConfig as NativeCacheConfig
from nautilus_trader.common import ImportableActorConfig
from nautilus_trader.common import LoggerConfig
from nautilus_trader.common import LogLevel
from nautilus_trader.common import MessageBusConfig as NativeMessageBusConfig
from nautilus_trader.common import SerializationEncoding
from nautilus_trader.core import dt_to_unix_nanos
from nautilus_trader.data import DataEngineConfig as NativeDataEngineConfig
from nautilus_trader.execution import DefaultFillModel
from nautilus_trader.execution import ExecutionEngineConfig as NativeExecEngineConfig
from nautilus_trader.execution import MakerTakerFeeModel
from nautilus_trader.execution import StaticLatencyModel
from nautilus_trader.model import AccountType
from nautilus_trader.model import BarIntervalType
from nautilus_trader.model import BarSpecification
from nautilus_trader.model import BarType
from nautilus_trader.model import ClientId
from nautilus_trader.model import BookType
from nautilus_trader.model import Currency
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import LeveragedMarginModel
from nautilus_trader.model import Money
from nautilus_trader.model import OmsType
from nautilus_trader.model import OtoTriggerMode
from nautilus_trader.model import Price
from nautilus_trader.model import TraderId
from nautilus_trader.model import Venue
from nautilus_trader.portfolio import PortfolioConfig as NativePortfolioConfig
from nautilus_trader.risk import RiskEngineConfig as NativeRiskEngineConfig

from crypto_lab.config import DISABLED
from crypto_lab.config import NONE_MULTI_CURRENCY
from crypto_lab.config import NOT_APPLICABLE
from crypto_lab.config import NautilusDataConfig
from crypto_lab.config import NautilusEngineConfig
from crypto_lab.config import NautilusVenueConfig
from crypto_lab.hashing import to_canonical_builtins


def _disabled_to_none(value: str):
    if value != DISABLED:
        raise ValueError(f"expected explicit DISABLED sentinel, received {value!r}")
    return None


def _serialization_encoding(value: str) -> SerializationEncoding:
    mapping = {
        "json": SerializationEncoding.JSON,
        "msgpack": SerializationEncoding.MSG_PACK,
    }
    try:
        return mapping[value]
    except KeyError as exc:
        raise ValueError(f"unsupported serialization encoding {value!r}") from exc


def make_fill_model(config: NautilusVenueConfig) -> DefaultFillModel:
    binding = config.fill_model
    return DefaultFillModel(
        prob_fill_on_limit=binding.prob_fill_on_limit,
        prob_slippage=binding.prob_slippage,
        random_seed=binding.random_seed,
    )


def make_latency_model(config: NautilusVenueConfig) -> StaticLatencyModel:
    binding = config.latency_model
    return StaticLatencyModel(
        base_latency_nanos=binding.base_latency_nanos,
        insert_latency_nanos=binding.insert_latency_nanos,
        update_latency_nanos=binding.update_latency_nanos,
        cancel_latency_nanos=binding.cancel_latency_nanos,
    )


def make_fee_model(config: NautilusVenueConfig) -> MakerTakerFeeModel:
    # The strict model verifies both fee identity fields before this point.
    if config.fee_model != config.effective_fee_model_path:
        raise ValueError("configured and effective fee model identities differ")
    return MakerTakerFeeModel()


def to_nautilus_venue_config(config: NautilusVenueConfig) -> BacktestVenueConfig:
    modules = [
        ImportableActorConfig(
            actor_path=item.actor_path,
            config_path=item.config_path,
            config=dict(item.config),
        )
        for item in config.modules
    ]
    return BacktestVenueConfig(
        name=config.name,
        oms_type=getattr(OmsType, config.oms_type),
        account_type=getattr(AccountType, config.account_type),
        book_type=getattr(BookType, config.book_type),
        starting_balances=[f"{item.amount} {item.currency}" for item in config.starting_balances],
        routing=config.routing,
        frozen_account=config.frozen_account,
        reject_stop_orders=config.reject_stop_orders,
        support_gtd_orders=config.support_gtd_orders,
        support_contingent_orders=config.support_contingent_orders,
        use_position_ids=config.use_position_ids,
        use_random_ids=config.use_random_ids,
        use_reduce_only=config.use_reduce_only,
        bar_execution=config.bar_execution,
        bar_adaptive_high_low_ordering=config.bar_adaptive_high_low_ordering,
        trade_execution=config.trade_execution,
        use_market_order_acks=config.use_market_order_acks,
        liquidity_consumption=config.liquidity_consumption,
        allow_cash_borrowing=config.allow_cash_borrowing,
        queue_position=config.queue_position,
        oto_trigger_mode=getattr(OtoTriggerMode, config.oto_trigger_mode),
        base_currency=(
            None
            if config.base_currency == NONE_MULTI_CURRENCY
            else Currency.from_str(config.base_currency)
        ),
        default_leverage=config.default_leverage,
        leverages={
            InstrumentId.from_str(item.instrument_id): item.leverage
            for item in config.instrument_leverages
        },
        margin_model=LeveragedMarginModel(),
        modules=modules,
        fill_model=make_fill_model(config),
        latency_model=make_latency_model(config),
        fee_model=make_fee_model(config),
        price_protection_points=config.price_protection_points,
        settlement_prices={
            InstrumentId.from_str(item.instrument_id): Price.from_str(str(item.price))
            for item in config.settlement_prices
        },
        liquidation_enabled=config.liquidation_enabled,
        liquidation_trigger_ratio=config.liquidation_trigger_ratio,
        liquidation_cancel_open_orders=config.liquidation_cancel_open_orders,
    )


def add_venue_from_config(
    engine: BacktestEngine,
    config: NautilusVenueConfig,
    *,
    latency_model_override: StaticLatencyModel | None = None,
) -> None:
    engine.add_venue(
        venue=Venue(config.name),
        oms_type=getattr(OmsType, config.oms_type),
        account_type=getattr(AccountType, config.account_type),
        starting_balances=[
            Money.from_str(f"{item.amount} {item.currency}")
            for item in config.starting_balances
        ],
        base_currency=(
            None
            if config.base_currency == NONE_MULTI_CURRENCY
            else Currency.from_str(config.base_currency)
        ),
        default_leverage=config.default_leverage,
        leverages={
            InstrumentId.from_str(item.instrument_id): item.leverage
            for item in config.instrument_leverages
        },
        margin_model=LeveragedMarginModel(),
        fill_model=make_fill_model(config),
        fee_model=make_fee_model(config),
        latency_model=(
            make_latency_model(config)
            if latency_model_override is None
            else latency_model_override
        ),
        modules=[],
        book_type=getattr(BookType, config.book_type),
        routing=config.routing,
        reject_stop_orders=config.reject_stop_orders,
        support_gtd_orders=config.support_gtd_orders,
        support_contingent_orders=config.support_contingent_orders,
        use_position_ids=config.use_position_ids,
        use_random_ids=config.use_random_ids,
        use_reduce_only=config.use_reduce_only,
        use_message_queue=config.use_message_queue,
        use_market_order_acks=config.use_market_order_acks,
        bar_execution=config.bar_execution,
        bar_adaptive_high_low_ordering=config.bar_adaptive_high_low_ordering,
        trade_execution=config.trade_execution,
        liquidity_consumption=config.liquidity_consumption,
        queue_position=config.queue_position,
        allow_cash_borrowing=config.allow_cash_borrowing,
        frozen_account=config.frozen_account,
        oto_trigger_mode=getattr(OtoTriggerMode, config.oto_trigger_mode),
        price_protection_points=config.price_protection_points,
        settlement_prices={
            InstrumentId.from_str(item.instrument_id): Price.from_str(str(item.price))
            for item in config.settlement_prices
        },
        liquidation_enabled=config.liquidation_enabled,
        liquidation_trigger_ratio=config.liquidation_trigger_ratio,
        liquidation_cancel_open_orders=config.liquidation_cancel_open_orders,
    )


def to_nautilus_engine_config(config: NautilusEngineConfig) -> BacktestEngineConfig:
    cache = config.cache
    msgbus = config.msgbus
    data_engine = config.data_engine
    risk_engine = config.risk_engine
    exec_engine = config.exec_engine
    portfolio = config.portfolio
    return BacktestEngineConfig(
        trader_id=TraderId.from_str(config.trader_id),
        instance_id=None,
        cache=NativeCacheConfig(
            encoding=_serialization_encoding(cache.encoding),
            timestamps_as_iso8601=cache.timestamps_as_iso8601,
            persist_account_events=cache.persist_account_events,
            buffer_interval_ms=_disabled_to_none(cache.buffer_interval_ms),
            bulk_read_batch_size=_disabled_to_none(cache.bulk_read_batch_size),
            use_trader_prefix=cache.use_trader_prefix,
            use_instance_id=cache.use_instance_id,
            flush_on_start=cache.flush_on_start,
            drop_instruments_on_reset=cache.drop_instruments_on_reset,
            tick_capacity=cache.tick_capacity,
            bar_capacity=cache.bar_capacity,
            save_market_data=cache.save_market_data,
        ),
        msgbus=NativeMessageBusConfig(
            encoding=_serialization_encoding(msgbus.encoding),
            encoding_market_data=_disabled_to_none(msgbus.encoding_market_data),
            encoding_builtin=_disabled_to_none(msgbus.encoding_builtin),
            timestamps_as_iso8601=msgbus.timestamps_as_iso8601,
            buffer_interval_ms=_disabled_to_none(msgbus.buffer_interval_ms),
            autotrim_mins=_disabled_to_none(msgbus.autotrim_mins),
            autotrim_maxlen=_disabled_to_none(msgbus.autotrim_maxlen),
            use_trader_prefix=msgbus.use_trader_prefix,
            use_trader_id=msgbus.use_trader_id,
            use_instance_id=msgbus.use_instance_id,
            streams_prefix=msgbus.streams_prefix,
            stream_per_topic=msgbus.stream_per_topic,
            external_streams=list(msgbus.external_streams),
            types_filter=_disabled_to_none(msgbus.types_filter),
            heartbeat_interval_secs=_disabled_to_none(msgbus.heartbeat_interval_secs),
        ),
        data_engine=NativeDataEngineConfig(
            time_bars_interval_type=(
                BarIntervalType.LEFT_OPEN
                if data_engine.time_bars_interval_type == "left-open"
                else BarIntervalType.RIGHT_OPEN
            ),
            time_bars_timestamp_on_close=data_engine.time_bars_timestamp_on_close,
            time_bars_skip_first_non_full_bar=data_engine.time_bars_skip_first_non_full_bar,
            time_bars_build_with_no_updates=data_engine.time_bars_build_with_no_updates,
            time_bars_origin_offset=dict(data_engine.time_bars_origin_offset),
            time_bars_build_delay=data_engine.time_bars_build_delay,
            validate_data_sequence=data_engine.validate_data_sequence,
            buffer_deltas=data_engine.buffer_deltas,
            emit_quotes_from_book=data_engine.emit_quotes_from_book,
            emit_quotes_from_book_depths=data_engine.emit_quotes_from_book_depths,
            external_clients=list(data_engine.external_clients),
            debug=data_engine.debug,
            disable_historical_cache=data_engine.disable_historical_cache,
        ),
        risk_engine=NativeRiskEngineConfig(
            bypass=risk_engine.bypass,
            max_order_submit_rate=risk_engine.max_order_submit_rate,
            max_order_modify_rate=risk_engine.max_order_modify_rate,
            max_notional_per_order=dict(risk_engine.max_notional_per_order),
            debug=risk_engine.debug,
        ),
        exec_engine=NativeExecEngineConfig(
            load_cache=exec_engine.load_cache,
            manage_own_order_books=exec_engine.manage_own_order_books,
            snapshot_orders=exec_engine.snapshot_orders,
            snapshot_positions=exec_engine.snapshot_positions,
            snapshot_positions_interval_secs=_disabled_to_none(
                exec_engine.snapshot_positions_interval_secs,
            ),
            external_clients=list(exec_engine.external_clients),
            allow_overfills=exec_engine.allow_overfills,
            carry_replay_events_on_reopen=exec_engine.carry_replay_events_on_reopen,
            purge_closed_orders_interval_mins=_disabled_to_none(
                exec_engine.purge_closed_orders_interval_mins,
            ),
            purge_closed_orders_buffer_mins=_disabled_to_none(
                exec_engine.purge_closed_orders_buffer_mins,
            ),
            purge_closed_positions_interval_mins=_disabled_to_none(
                exec_engine.purge_closed_positions_interval_mins,
            ),
            purge_closed_positions_buffer_mins=_disabled_to_none(
                exec_engine.purge_closed_positions_buffer_mins,
            ),
            purge_account_events_interval_mins=_disabled_to_none(
                exec_engine.purge_account_events_interval_mins,
            ),
            purge_account_events_lookback_mins=_disabled_to_none(
                exec_engine.purge_account_events_lookback_mins,
            ),
            purge_from_database=exec_engine.purge_from_database,
            debug=exec_engine.debug,
        ),
        portfolio=NativePortfolioConfig(
            use_mark_prices=portfolio.use_mark_prices,
            use_mark_xrates=portfolio.use_mark_xrates,
            bar_updates=portfolio.bar_updates,
            convert_to_account_base_currency=portfolio.convert_to_account_base_currency,
            equity_curve=portfolio.equity_curve,
            min_account_state_logging_interval_ms=_disabled_to_none(
                portfolio.min_account_state_logging_interval_ms,
            ),
            snapshot_interval_ms=_disabled_to_none(portfolio.snapshot_interval_ms),
            debug=portfolio.debug,
        ),
        controller=None,
        load_state=config.load_state,
        save_state=config.save_state,
        shutdown_on_error=config.shutdown_on_error,
        bypass_logging=config.bypass_logging,
        logging=LoggerConfig(
            stdout_level=LogLevel.ERROR,
            bypass_logging=config.bypass_logging,
            print_config=False,
            is_colored=False,
        ),
        run_analysis=config.run_analysis,
        timeout_connection=config.timeout_connection,
        timeout_reconciliation=config.timeout_reconciliation,
        timeout_portfolio=config.timeout_portfolio,
        timeout_disconnection=config.timeout_disconnection,
        delay_post_stop=config.delay_post_stop,
        timeout_shutdown=config.timeout_shutdown,
    )


def to_nautilus_data_configs(
    configs: tuple[NautilusDataConfig, ...],
) -> list[BacktestDataConfig]:
    native: list[BacktestDataConfig] = []
    for config in configs:
        native.append(
            BacktestDataConfig(
                data_type=config.data_type,
                catalog_path=config.catalog_path,
                catalog_fs_protocol=config.catalog_fs_protocol,
                catalog_fs_storage_options=dict(config.catalog_fs_storage_options),
                catalog_fs_rust_storage_options=dict(config.catalog_fs_rust_storage_options),
                instrument_id=InstrumentId.from_str(config.instrument_id),
                start_time=dt_to_unix_nanos(config.start_time),
                end_time=dt_to_unix_nanos(config.end_time),
                filter_expr=(
                    None if config.filter_expr == NOT_APPLICABLE else config.filter_expr
                ),
                client_id=(
                    None
                    if config.client_id == NOT_APPLICABLE
                    else ClientId.from_str(config.client_id)
                ),
                metadata=dict(config.metadata),
                bar_spec=BarSpecification.from_str(config.bar_spec),
                instrument_ids=[
                    InstrumentId.from_str(value) for value in config.instrument_ids
                ],
                bar_types=[BarType.from_str(value) for value in config.bar_types],
                optimize_file_loading=config.optimize_file_loading,
            ),
        )
    return native
