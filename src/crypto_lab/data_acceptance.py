"""Pinned-Nautilus executable market-state acceptance for Dataset Releases.

This is a data qualification boundary, not a Strategy Run.  It feeds every
execution Bar/Mark/Funding update to the locked engine and uses small declared
sentinel MARKET orders only to prove that accepted Bars create executable
market state.  No result is used as research performance.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.backtest import BacktestEngineConfig
from nautilus_trader.common import FileWriterConfig
from nautilus_trader.common import LoggerConfig
from nautilus_trader.common import LogLevel
from nautilus_trader.execution import StaticLatencyModel
from nautilus_trader.model import Bar
from nautilus_trader.model import FundingRateUpdate
from nautilus_trader.model import MarkPriceUpdate

from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.data import DataContractError
from crypto_lab.data import InstrumentMetadata
from crypto_lab.hashing import canonical_sha256
from crypto_lab.nautilus_config import add_venue_from_config
from crypto_lab.nautilus_config import to_nautilus_engine_config
from crypto_lab.status import FailureCode
from crypto_lab.strategies import GuardedCausalStrategy
from crypto_lab.strategies import OrderIntent
from crypto_lab.strategies import StrategyPlan


ONE_MINUTE_NS = 60_000_000_000
LOCKED_LATENCY_NS = 60_000_000_000
ERROR_PATTERNS = (
    "Bar price precision",
    "Bar volume precision",
    "MarkPriceUpdate price precision",
    "No market for ",
    "panic",
    "fatal",
)


def _engine_config_with_error_log(config: LabRunConfig, directory: Path, name: str) -> Any:
    native = to_nautilus_engine_config(config.nautilus_engine_config)
    return BacktestEngineConfig(
        trader_id=native.trader_id,
        load_state=native.load_state,
        save_state=native.save_state,
        shutdown_on_error=native.shutdown_on_error,
        bypass_logging=native.bypass_logging,
        run_analysis=native.run_analysis,
        timeout_connection=int(native.timeout_connection),
        timeout_reconciliation=int(native.timeout_reconciliation),
        timeout_portfolio=int(native.timeout_portfolio),
        timeout_disconnection=int(native.timeout_disconnection),
        delay_post_stop=int(native.delay_post_stop),
        timeout_shutdown=int(native.timeout_shutdown),
        cache=native.cache,
        msgbus=native.msgbus,
        data_engine=native.data_engine,
        risk_engine=native.risk_engine,
        exec_engine=native.exec_engine,
        portfolio=native.portfolio,
        controller=native.controller,
        logging=LoggerConfig(
            stdout_level=LogLevel.ERROR,
            fileout_level=LogLevel.ERROR,
            is_colored=False,
            print_config=False,
            bypass_logging=False,
            file_config=FileWriterConfig(
                directory=str(directory),
                file_name=name,
            ),
            clear_log_file=True,
            fileout_sync_on_flush=True,
        ),
    )


def _diagnostic_material(directory: Path, name: str) -> dict[str, Any]:
    candidates = sorted(directory.glob(f"{name}*"))
    if not candidates:
        payload = b""
        path = None
    elif len(candidates) == 1:
        path = candidates[0]
        payload = path.read_bytes()
    else:
        raise DataContractError(
            FailureCode.UNSUPPORTED_RUNTIME,
            "ambiguous pinned-runtime diagnostic log output",
        )
    text = payload.decode("utf-8", errors="replace")
    matches = [pattern for pattern in ERROR_PATTERNS if pattern.lower() in text.lower()]
    return {
        "log_created": path is not None,
        "log_byte_size": len(payload),
        "log_sha256": hashlib.sha256(payload).hexdigest(),
        "error_patterns": matches,
        "fatal_runtime_diagnostics": len(matches),
    }


def _static_precision_failures(
    *,
    instrument: Any,
    execution_bars: tuple[Bar, ...],
    mark_updates: tuple[MarkPriceUpdate, ...],
) -> dict[str, int]:
    price_precision = instrument.price_precision
    size_precision = instrument.size_precision
    tick = instrument.price_increment.as_decimal()
    price_mismatch = 0
    volume_mismatch = 0
    execution_tick_mismatch = 0
    mark_mismatch = 0
    for bar in execution_bars:
        prices = (bar.open, bar.high, bar.low, bar.close)
        price_mismatch += any(item.precision != price_precision for item in prices)
        volume_mismatch += bar.volume.precision != size_precision
        execution_tick_mismatch += any(item.as_decimal() % tick != 0 for item in prices)
    for mark in mark_updates:
        mark_mismatch += mark.value.precision != price_precision
    return {
        "execution_price_precision_mismatches": price_mismatch,
        "execution_volume_precision_mismatches": volume_mismatch,
        "execution_tick_grid_mismatches": execution_tick_mismatch,
        "mark_precision_mismatches": mark_mismatch,
    }


def _sentinel_indices(
    execution_bars: tuple[Bar, ...],
    *,
    profile: MarketProfile,
    funding_updates: tuple[FundingRateUpdate, ...],
) -> tuple[tuple[str, int], ...]:
    size = len(execution_bars)
    candidates: list[tuple[str, int]] = [
        ("WINDOW_START", 1),
        ("WINDOW_MIDPOINT", size // 2),
        ("WINDOW_END", size - 4),
    ]
    if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        gap_index = next(
            (
                index
                for index in range(1, size)
                if int(execution_bars[index].ts_init)
                - int(execution_bars[index - 1].ts_init)
                > ONE_MINUTE_NS
            ),
            None,
        )
        if gap_index is not None:
            candidates.append(("AFTER_VERIFIED_NO_TRADE", gap_index))
    elif funding_updates:
        boundary = int(funding_updates[len(funding_updates) // 2].ts_init)
        index = min(
            range(size),
            key=lambda item: abs(int(execution_bars[item].ts_init) - boundary),
        )
        candidates.append(("AROUND_FUNDING_BOUNDARY", min(max(index, 1), size - 4)))
    result: list[tuple[str, int]] = []
    seen: set[int] = set()
    for role, index in candidates:
        index = min(max(index, 1), size - 4)
        if index not in seen:
            result.append((role, index))
            seen.add(index)
    return tuple(result)


def _run_sentinel(
    *,
    config: LabRunConfig,
    instrument: Any,
    all_data: tuple[Any, ...],
    execution_bars: tuple[Bar, ...],
    signal_index: int,
    role: str,
    quantity: str,
    diagnostic_root: Path,
    zero_latency: bool = False,
) -> dict[str, Any]:
    signal_bar = execution_bars[signal_index]
    start_ns = int(execution_bars[signal_index - 1].ts_init)
    end_ns = int(execution_bars[signal_index + 3].ts_init)
    subset = tuple(
        item
        for item in all_data
        if start_ns <= int(item.ts_init) <= end_ns
    )
    plan = StrategyPlan(
        intents_by_bar_ns={
            int(signal_bar.ts_init): (
                OrderIntent(
                    side="BUY",
                    quantity=quantity,
                    order_type="MARKET",
                    reason=f"DATA_ACCEPTANCE_SENTINEL_{role}",
                ),
            ),
        },
        conflict_rule="FIRST_ELIGIBLE_INTENT",
        qualification_attempt_all_intents=False,
    )
    log_name = f"sentinel-{role.lower()}-{'zero' if zero_latency else 'locked'}"
    engine = BacktestEngine(_engine_config_with_error_log(config, diagnostic_root, log_name))
    strategy = GuardedCausalStrategy()
    try:
        add_venue_from_config(
            engine,
            config.nautilus_venue_config,
            latency_model_override=(StaticLatencyModel(0, 0, 0, 0) if zero_latency else None),
        )
        engine.add_instrument(instrument)
        strategy.configure(
            instrument_id=instrument.id,
            bar_type=signal_bar.bar_type,
            execution_bar_type=signal_bar.bar_type,
            profile=config.market_profile,
            plan=plan,
            scoring_start_ns=start_ns - ONE_MINUTE_NS,
            scoring_end_exclusive_ns=end_ns + ONE_MINUTE_NS,
            effective_insert_latency_ns=0 if zero_latency else LOCKED_LATENCY_NS,
            size_precision=instrument.size_precision,
            min_quantity=(
                None if instrument.min_quantity is None else instrument.min_quantity.as_decimal()
            ),
            max_quantity=(
                None if instrument.max_quantity is None else instrument.max_quantity.as_decimal()
            ),
            size_increment=instrument.size_increment.as_decimal(),
            initial_capital_amount=config.initial_capital.amount,
            initial_capital_currency=config.initial_capital.currency,
        )
        engine.add_strategy(strategy)
        engine.add_data(list(subset))
        engine.run()
        orders = engine.cache.orders(instrument_id=instrument.id)
        order_events = [event.to_dict() for order in orders for event in order.events()]
        fills = [item for item in order_events if item["type"] == "OrderFilled"]
        rejected = [item for item in order_events if item["type"] == "OrderRejected"]
        submitted = strategy.observations["submitted_intents"]
        valid = bool(
            len(orders) == 1
            and len(fills) == 1
            and not rejected
            and len(submitted) == 1
            and not strategy.observations["guard_failures"]
            and int(fills[0]["ts_event"]) > int(signal_bar.ts_init)
            and (
                zero_latency
                or int(fills[0]["ts_event"]) >= int(signal_bar.ts_init) + LOCKED_LATENCY_NS
            )
        )
        result = {
            "role": role,
            "signal_bar_ts_init": int(signal_bar.ts_init),
            "effective_insert_latency_ns": 0 if zero_latency else LOCKED_LATENCY_NS,
            "order_count": len(orders),
            "fill_count": len(fills),
            "rejected_order_count": len(rejected),
            "fill_ts_event": None if not fills else int(fills[0]["ts_event"]),
            "fill_price": None if not fills else str(fills[0]["last_px"]),
            "fill_quantity": None if not fills else str(fills[0]["last_qty"]),
            "same_bar_fill": bool(fills and int(fills[0]["ts_event"]) <= int(signal_bar.ts_init)),
            "guard_failure_count": len(strategy.observations["guard_failures"]),
            "status": "PASS" if valid else "FAIL",
            "performance_research": False,
        }
    finally:
        engine.dispose()
    result["diagnostics"] = _diagnostic_material(diagnostic_root, log_name)
    if result["diagnostics"]["fatal_runtime_diagnostics"]:
        result["status"] = "FAIL"
    return result


def qualify_executable_market_state(
    *,
    config_path: Path,
    catalog_identity: str,
    metadata: InstrumentMetadata,
    instrument: Any,
    execution_bars: tuple[Bar, ...],
    mark_updates: tuple[MarkPriceUpdate, ...] = (),
    funding_updates: tuple[FundingRateUpdate, ...] = (),
    diagnostic_root: Path,
) -> dict[str, Any]:
    """Run the all-data ingestion and distributed sentinel gates."""

    config = LabRunConfig.from_json_bytes(Path(config_path).read_bytes())
    if len(catalog_identity) != 64 or any(
        character not in "0123456789abcdef" for character in catalog_identity
    ):
        raise DataContractError(
            FailureCode.DATA_HASH_MISMATCH,
            "market-state acceptance requires the exact catalog identity",
        )
    if config.market_profile is not metadata.market_profile or str(instrument.id) != metadata.instrument_id:
        raise DataContractError(
            FailureCode.DATA_ROLE_MISMATCH,
            "acceptance config, metadata and native Instrument differ",
        )
    diagnostic_root.mkdir(parents=True, exist_ok=True)
    failures = _static_precision_failures(
        instrument=instrument,
        execution_bars=execution_bars,
        mark_updates=mark_updates,
    )
    priorities = {MarkPriceUpdate: 0, FundingRateUpdate: 1, Bar: 2}
    all_data = tuple(
        sorted(
            (*mark_updates, *funding_updates, *execution_bars),
            key=lambda item: (int(item.ts_init), priorities[type(item)]),
        ),
    )
    log_name = "all-data-ingestion"
    engine = BacktestEngine(_engine_config_with_error_log(config, diagnostic_root, log_name))
    engine_error: str | None = None
    iterations = -1
    mark_count = -1
    funding_count = -1
    try:
        add_venue_from_config(engine, config.nautilus_venue_config)
        engine.add_instrument(instrument)
        engine.add_data(list(all_data))
        engine.run()
        native_result = engine.get_result()
        iterations = int(native_result.iterations)
        mark_count = int(engine.cache.mark_price_count(instrument.id))
        funding_count = int(engine.cache.funding_rate_count(instrument.id))
    except Exception as exc:
        engine_error = f"{type(exc).__name__}: {exc}"
    finally:
        engine.dispose()
    diagnostics = _diagnostic_material(diagnostic_root, log_name)

    quantity = "0.10000" if metadata.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else "0.100"
    sentinels = [
        _run_sentinel(
            config=config,
            instrument=instrument,
            all_data=all_data,
            execution_bars=execution_bars,
            signal_index=index,
            role=role,
            quantity=quantity,
            diagnostic_root=diagnostic_root,
        )
        for role, index in _sentinel_indices(
            execution_bars,
            profile=metadata.market_profile,
            funding_updates=funding_updates,
        )
    ]
    zero_control = _run_sentinel(
        config=config,
        instrument=instrument,
        all_data=all_data,
        execution_bars=execution_bars,
        signal_index=1,
        role="ZERO_LATENCY_NEGATIVE_CONTROL",
        quantity=quantity,
        diagnostic_root=diagnostic_root,
        zero_latency=True,
    )
    zero_control["accepted_for_release"] = False
    zero_control["reason"] = "LOCKED_LATENCY_IS_60_SECONDS"

    precision_rejections = sum(failures.values())
    sentinel_pass = all(item["status"] == "PASS" for item in sentinels)
    full_pass = bool(
        engine_error is None
        and iterations == len(all_data)
        and precision_rejections == 0
        and diagnostics["fatal_runtime_diagnostics"] == 0
        and sentinel_pass
    )
    material = {
        "schema": "nautilus-executable-market-state-acceptance-v1",
        "gate": "NAUTILUS_EXECUTABLE_MARKET_STATE_ACCEPTANCE",
        "status": "PASS" if full_pass else "FAIL",
        "dataset_profile": metadata.market_profile.value,
        "instrument_id": metadata.instrument_id,
        "instrument_metadata_identity": metadata.instrument_metadata_identity,
        "catalog_identity": catalog_identity,
        "pinned_nautilus_version": "2.0.0rc2",
        "lab_run_config_sha256": config.config_sha256,
        "expected_executable_bars": len(execution_bars),
        "accepted_executable_bars": len(execution_bars) if full_pass else 0,
        "precision_skipped_bars": failures["execution_price_precision_mismatches"]
        + failures["execution_volume_precision_mismatches"],
        "expected_mark_updates": len(mark_updates),
        "accepted_mark_updates": len(mark_updates) if full_pass else 0,
        "rejected_precision_events": precision_rejections,
        "no_market_data_precision_warnings": diagnostics["fatal_runtime_diagnostics"],
        "fatal_runtime_diagnostics": diagnostics["fatal_runtime_diagnostics"],
        "missing_market_state": sum(item["status"] != "PASS" for item in sentinels),
        "all_data_event_count": len(all_data),
        "engine_iterations": iterations,
        "engine_error": engine_error,
        "native_mark_cache_count": mark_count,
        "native_funding_cache_count": funding_count,
        "static_precision_and_grid": failures,
        "runtime_diagnostics": diagnostics,
        "sentinel_fills": sentinels,
        "zero_latency_negative_control": zero_control,
        "strategy_research_run": False,
        "financial_performance_evaluated": False,
        "raw_numeric_values_mutated": False,
        "zero_padding_only": True,
    }
    if not full_pass:
        raise DataContractError(
            FailureCode.INSTRUMENT_METADATA_INVALID,
            f"executable market-state acceptance failed: {material}",
        )
    return material


def market_state_acceptance_identity(material: dict[str, Any]) -> str:
    return canonical_sha256(material)


__all__ = ["market_state_acceptance_identity", "qualify_executable_market_state"]
