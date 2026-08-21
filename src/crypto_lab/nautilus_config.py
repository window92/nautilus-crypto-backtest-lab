"""Mechanical bindings from frozen M0 models to NautilusTrader v1.231.0 configs."""

from __future__ import annotations

from nautilus_trader.backtest.config import BacktestDataConfig
from nautilus_trader.backtest.config import BacktestEngineConfig
from nautilus_trader.backtest.config import BacktestVenueConfig
from nautilus_trader.backtest.config import FillModelConfig as NativeFillModelConfig
from nautilus_trader.backtest.config import ImportableFillModelConfig
from nautilus_trader.backtest.config import ImportableLatencyModelConfig
from nautilus_trader.backtest.config import LatencyModelConfig as NativeLatencyModelConfig
from nautilus_trader.backtest.config import MarginModelConfig as NativeMarginModelConfig
from nautilus_trader.cache.config import CacheConfig as NativeCacheConfig
from nautilus_trader.common import Environment
from nautilus_trader.common.config import ImportableActorConfig
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.common.config import MessageBusConfig as NativeMessageBusConfig
from nautilus_trader.data.config import DataEngineConfig as NativeDataEngineConfig
from nautilus_trader.execution.config import ExecEngineConfig as NativeExecEngineConfig
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.portfolio.config import PortfolioConfig as NativePortfolioConfig
from nautilus_trader.risk.config import RiskEngineConfig as NativeRiskEngineConfig

from crypto_lab.config import DISABLED
from crypto_lab.config import NONE_MULTI_CURRENCY
from crypto_lab.config import NOT_APPLICABLE
from crypto_lab.config import NautilusDataConfig
from crypto_lab.config import NautilusEngineConfig
from crypto_lab.config import NautilusVenueConfig
from crypto_lab.hashing import to_canonical_builtins


def _disabled_to_none(value: str):
    if value != DISABLED:  # Models reject this earlier; keep the binding fail-closed.
        raise ValueError(f"expected explicit DISABLED sentinel, received {value!r}")
    return None


def to_nautilus_venue_config(config: NautilusVenueConfig) -> BacktestVenueConfig:
    modules = [
        ImportableActorConfig(
            actor_path=item.actor_path,
            config_path=item.config_path,
            config=dict(item.config),
        )
        for item in config.modules
    ]
    fill_model = ImportableFillModelConfig(
        fill_model_path=config.fill_model.fill_model_path,
        config_path=config.fill_model.config_path,
        config={
            "prob_fill_on_limit": config.fill_model.prob_fill_on_limit,
            "prob_slippage": config.fill_model.prob_slippage,
            "random_seed": config.fill_model.random_seed,
        },
    )
    latency_model = ImportableLatencyModelConfig(
        latency_model_path=config.latency_model.latency_model_path,
        config_path=config.latency_model.config_path,
        config={
            "base_latency_nanos": config.latency_model.base_latency_nanos,
            "insert_latency_nanos": config.latency_model.insert_latency_nanos,
            "update_latency_nanos": config.latency_model.update_latency_nanos,
            "cancel_latency_nanos": config.latency_model.cancel_latency_nanos,
        },
    )
    # Constructing these native configs exercises their strict v1.231.0 schemas now,
    # rather than allowing M1 to discover a renamed field after M0 is frozen.
    NativeFillModelConfig(**fill_model.config)
    NativeLatencyModelConfig(**latency_model.config)

    return BacktestVenueConfig(
        name=config.name,
        oms_type=config.oms_type,
        account_type=config.account_type,
        starting_balances=[f"{item.amount} {item.currency}" for item in config.starting_balances],
        base_currency=None if config.base_currency == NONE_MULTI_CURRENCY else config.base_currency,
        default_leverage=float(config.default_leverage),
        leverages={item.instrument_id: float(item.leverage) for item in config.instrument_leverages},
        margin_model=NativeMarginModelConfig(
            model_type=config.margin_model.model_type,
            config=dict(config.margin_model.config),
        ),
        modules=modules,
        fill_model=fill_model,
        latency_model=latency_model,
        fee_model=None,
        book_type=config.book_type,
        routing=config.routing,
        reject_stop_orders=config.reject_stop_orders,
        support_gtd_orders=config.support_gtd_orders,
        support_contingent_orders=config.support_contingent_orders,
        oto_trigger_mode=config.oto_trigger_mode,
        use_position_ids=config.use_position_ids,
        use_random_ids=config.use_random_ids,
        use_reduce_only=config.use_reduce_only,
        use_market_order_acks=config.use_market_order_acks,
        bar_execution=config.bar_execution,
        bar_adaptive_high_low_ordering=config.bar_adaptive_high_low_ordering,
        trade_execution=config.trade_execution,
        liquidity_consumption=config.liquidity_consumption,
        queue_position=config.queue_position,
        allow_cash_borrowing=config.allow_cash_borrowing,
        frozen_account=config.frozen_account,
        price_protection_points=config.price_protection_points,
        settlement_prices={
            InstrumentId.from_str(item.instrument_id): float(item.price)
            for item in config.settlement_prices
        },
        liquidation_enabled=config.liquidation_enabled,
        liquidation_trigger_ratio=config.liquidation_trigger_ratio,
        liquidation_cancel_open_orders=config.liquidation_cancel_open_orders,
    )


def to_nautilus_engine_config(config: NautilusEngineConfig) -> BacktestEngineConfig:
    cache = config.cache
    message_bus = config.message_bus
    data_engine = config.data_engine
    risk_engine = config.risk_engine
    exec_engine = config.exec_engine
    portfolio = config.portfolio

    return BacktestEngineConfig(
        environment=Environment.BACKTEST,
        trader_id=config.trader_id,
        instance_id=None,
        cache=NativeCacheConfig(
            database=_disabled_to_none(cache.database),
            encoding=cache.encoding,
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
        ),
        message_bus=NativeMessageBusConfig(
            database=_disabled_to_none(message_bus.database),
            encoding=message_bus.encoding,
            timestamps_as_iso8601=message_bus.timestamps_as_iso8601,
            buffer_interval_ms=_disabled_to_none(message_bus.buffer_interval_ms),
            autotrim_mins=_disabled_to_none(message_bus.autotrim_mins),
            use_trader_prefix=message_bus.use_trader_prefix,
            use_trader_id=message_bus.use_trader_id,
            use_instance_id=message_bus.use_instance_id,
            streams_prefix=message_bus.streams_prefix,
            stream_per_topic=message_bus.stream_per_topic,
            external_streams=list(message_bus.external_streams),
            types_filter=_disabled_to_none(message_bus.types_filter),
            heartbeat_interval_secs=_disabled_to_none(message_bus.heartbeat_interval_secs),
        ),
        data_engine=NativeDataEngineConfig(
            time_bars_interval_type=data_engine.time_bars_interval_type,
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
            min_account_state_logging_interval_ms=_disabled_to_none(
                portfolio.min_account_state_logging_interval_ms,
            ),
            snapshot_interval_ms=_disabled_to_none(portfolio.snapshot_interval_ms),
            debug=portfolio.debug,
        ),
        emulator=None,
        streaming=None,
        catalogs=list(config.catalogs),
        actors=list(config.actors),
        strategies=list(config.strategies),
        exec_algorithms=list(config.exec_algorithms),
        controller=None,
        load_state=config.load_state,
        save_state=config.save_state,
        loop_debug=config.loop_debug,
        logging=LoggingConfig(bypass_logging=config.logging_bypass),
        timeout_connection=config.timeout_connection,
        timeout_reconciliation=config.timeout_reconciliation,
        timeout_portfolio=config.timeout_portfolio,
        timeout_disconnection=config.timeout_disconnection,
        timeout_post_stop=config.timeout_post_stop,
        timeout_shutdown=config.timeout_shutdown,
        run_analysis=config.run_analysis,
    )


def to_nautilus_data_configs(
    configs: tuple[NautilusDataConfig, ...],
) -> list[BacktestDataConfig]:
    native: list[BacktestDataConfig] = []
    for config in configs:
        start_time = to_canonical_builtins(config.start_time)
        end_time = to_canonical_builtins(config.end_time)
        native.append(
            BacktestDataConfig(
                catalog_path=config.catalog_path,
                data_cls=config.data_cls,
                catalog_fs_protocol=config.catalog_fs_protocol,
                catalog_fs_storage_options=dict(config.catalog_fs_storage_options),
                catalog_fs_rust_storage_options=dict(config.catalog_fs_rust_storage_options),
                instrument_id=config.instrument_id,
                start_time=start_time,
                end_time=end_time,
                filter_expr=None if config.filter_expr == NOT_APPLICABLE else config.filter_expr,
                client_id=None if config.client_id == NOT_APPLICABLE else config.client_id,
                metadata=dict(config.metadata),
                bar_spec=config.bar_spec,
                instrument_ids=list(config.instrument_ids),
                bar_types=list(config.bar_types),
                optimize_file_loading=config.optimize_file_loading,
            ),
        )
    return native
