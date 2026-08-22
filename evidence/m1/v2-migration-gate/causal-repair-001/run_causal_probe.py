#!/usr/bin/env python3
"""Reproduce CAUSAL_FILL_REPAIR_001 with NautilusTrader public v2 APIs only."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.backtest import BacktestEngineConfig
from nautilus_trader.common import LoggerConfig
from nautilus_trader.common import LogLevel
from nautilus_trader.execution import DefaultFillModel
from nautilus_trader.execution import MakerTakerFeeModel
from nautilus_trader.execution import StaticLatencyModel
from nautilus_trader.model import AccountType
from nautilus_trader.model import AggregationSource
from nautilus_trader.model import Bar
from nautilus_trader.model import BarAggregation
from nautilus_trader.model import BarSpecification
from nautilus_trader.model import BarType
from nautilus_trader.model import BookType
from nautilus_trader.model import CryptoPerpetual
from nautilus_trader.model import Currency
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import Money
from nautilus_trader.model import OmsType
from nautilus_trader.model import OrderSide
from nautilus_trader.model import Price
from nautilus_trader.model import PriceType
from nautilus_trader.model import Quantity
from nautilus_trader.model import Symbol
from nautilus_trader.model import TimeInForce
from nautilus_trader.model import Venue
from nautilus_trader.portfolio import PortfolioConfig
from nautilus_trader.trading import Strategy


EXPECTED_RUNTIME = "2.0.0rc2"
SOURCE_COMMIT = "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"
INSTRUMENT_ID = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
VENUE = Venue("BINANCE")
BTC = Currency.from_str("BTC")
USDT = Currency.from_str("USDT")
BAR_TYPE = BarType(
    INSTRUMENT_ID,
    BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
    AggregationSource.EXTERNAL,
)
ORDER_QUANTITY = Quantity.from_str("1")
BAR_VOLUME = Quantity.from_str("1000")
SIGNAL_BAR_AVAILABLE_AT = 60_000_000_000
SIGNAL_LOW = Decimal("99.00")
SIGNAL_HIGH = Decimal("101.00")

BAR_ROWS = (
    (60_000_000_000, "100.00", "101.00", "99.00", "100.00"),
    (120_000_000_000, "200.00", "201.00", "199.00", "200.00"),
    (180_000_000_000, "300.00", "301.00", "299.00", "300.00"),
)


def optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def make_instrument() -> CryptoPerpetual:
    return CryptoPerpetual(
        INSTRUMENT_ID,
        Symbol("BTCUSDT"),
        BTC,
        USDT,
        USDT,
        False,
        2,
        0,
        Price.from_str("0.01"),
        Quantity.from_str("1"),
        0,
        0,
        multiplier=Quantity.from_str("1"),
        margin_init=Decimal("1"),
        margin_maint=Decimal("1"),
        maker_fee=Decimal("0"),
        taker_fee=Decimal("0"),
    )


def make_bars() -> list[Bar]:
    return [
        Bar(
            BAR_TYPE,
            Price.from_str(open_),
            Price.from_str(high),
            Price.from_str(low),
            Price.from_str(close),
            BAR_VOLUME,
            ts_event,
            ts_event,
        )
        for ts_event, open_, high, low, close in BAR_ROWS
    ]


def public_cache_market_snapshot(strategy: Strategy) -> dict[str, Any]:
    prices: dict[str, str | None] = {}
    for price_type in (PriceType.BID, PriceType.ASK, PriceType.LAST):
        prices[price_type.name] = optional_text(
            strategy.cache.price(INSTRUMENT_ID, price_type)
        )

    book = strategy.cache.order_book(INSTRUMENT_ID)
    return {
        "scope": "public shared cache; not the private SimulatedExchange matching book",
        "best_bid": prices["BID"],
        "best_ask": prices["ASK"],
        "last_price": prices["LAST"],
        "order_book_present": book is not None,
    }


class SignalFromFirstCompletedBar(Strategy):
    """Submit exactly one native MARKET BUY from the first completed bar callback."""

    def __init__(self) -> None:
        super().__init__()
        self.sent = False
        self.bar_callbacks: list[dict[str, Any]] = []
        self.signal: dict[str, Any] | None = None

    def on_start(self) -> None:
        self.subscribe_bars(BAR_TYPE)

    def on_bar(self, bar: Bar) -> None:
        callback = {
            "bar_type": str(bar.bar_type),
            "bar_instrument_id": str(bar.bar_type.instrument_id),
            "ts_event": int(bar.ts_event),
            "ts_init": int(bar.ts_init),
            "strategy_clock_ns": int(self.clock.timestamp_ns()),
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
            "price_precision": bar.open.precision,
            "volume": str(bar.volume),
            "volume_precision": bar.volume.precision,
            "market_before_submission": public_cache_market_snapshot(self),
        }
        self.bar_callbacks.append(callback)

        if self.sent:
            return

        self.sent = True
        self.signal = {
            "instrument_id": str(INSTRUMENT_ID),
            "bar_type": str(bar.bar_type),
            "signal_timestamp_ns": int(self.clock.timestamp_ns()),
            "signal_bar_available_at_ns": int(bar.ts_init),
            "callback_index": 1,
        }
        order = self.order_factory.market(
            INSTRUMENT_ID,
            OrderSide.BUY,
            ORDER_QUANTITY,
            time_in_force=TimeInForce.GTC,
        )
        self.submit_order(order)


def engine_configuration(
    latency: StaticLatencyModel,
    fill_model: DefaultFillModel,
) -> dict[str, Any]:
    return {
        "instrument_id": str(INSTRUMENT_ID),
        "instrument_price_precision": 2,
        "instrument_price_increment": "0.01",
        "instrument_size_precision": 0,
        "instrument_size_increment": "1",
        "bar_type": str(BAR_TYPE),
        "book_type": BookType.L1_MBP.name,
        "oms_type": OmsType.NETTING.name,
        "account_type": AccountType.MARGIN.name,
        "default_leverage": "1",
        "bar_execution": True,
        "bar_adaptive_high_low_ordering": False,
        "trade_execution": False,
        "liquidity_consumption": True,
        "queue_position": False,
        "routing": False,
        "reject_stop_orders": True,
        "support_gtd_orders": False,
        "support_contingent_orders": False,
        "use_position_ids": False,
        "use_random_ids": False,
        "use_reduce_only": True,
        "use_message_queue": True,
        "use_market_order_acks": False,
        "allow_cash_borrowing": False,
        "frozen_account": False,
        "price_protection_points": 0,
        "liquidation_enabled": False,
        "portfolio_use_mark_prices": True,
        "market_order_time_in_force": TimeInForce.GTC.name,
        "order_quantity": str(ORDER_QUANTITY),
        "order_quantity_precision": ORDER_QUANTITY.precision,
        "bar_volume": str(BAR_VOLUME),
        "bar_volume_precision": BAR_VOLUME.precision,
        "source_derived_synthetic_ohlc_leg_quantity": "250",
        "minimum_leg_quantity_exceeds_order_quantity": True,
        "fill_model_module": DefaultFillModel.__module__,
        "fill_model_class": DefaultFillModel.__name__,
        "fill_model_repr": repr(fill_model),
        "custom_fill_model": False,
        "latency_model_module": StaticLatencyModel.__module__,
        "latency_model_class": StaticLatencyModel.__name__,
        "latency_model_repr": repr(latency),
    }


def make_engine(
    latency: StaticLatencyModel,
    fill_model: DefaultFillModel,
) -> BacktestEngine:
    engine = BacktestEngine(
        BacktestEngineConfig(
            logging=LoggerConfig(
                stdout_level=LogLevel.ERROR,
                print_config=False,
                is_colored=False,
            ),
            run_analysis=False,
            portfolio=PortfolioConfig(use_mark_prices=True),
        )
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money.from_str("1000 USDT")],
        base_currency=USDT,
        default_leverage=Decimal("1"),
        margin_model=None,
        fill_model=fill_model,
        fee_model=MakerTakerFeeModel(),
        latency_model=latency,
        modules=None,
        book_type=BookType.L1_MBP,
        routing=False,
        reject_stop_orders=True,
        support_gtd_orders=False,
        support_contingent_orders=False,
        use_position_ids=False,
        use_random_ids=False,
        use_reduce_only=True,
        use_message_queue=True,
        use_market_order_acks=False,
        bar_execution=True,
        bar_adaptive_high_low_ordering=False,
        trade_execution=False,
        liquidity_consumption=True,
        queue_position=False,
        allow_cash_borrowing=False,
        frozen_account=False,
        price_protection_points=0,
        liquidation_enabled=False,
    )
    engine.add_instrument(make_instrument())
    return engine


def event_timestamp(events: list[dict[str, Any]], event_type: str) -> int | None:
    event = next((item for item in events if item["type"] == event_type), None)
    return None if event is None else int(event["ts_event"])


def run_case(
    *,
    name: str,
    base_latency_nanos: int,
    prob_slippage: float,
    acceptance_role: str,
) -> dict[str, Any]:
    latency = StaticLatencyModel(base_latency_nanos, 0, 0, 0)
    fill_model = DefaultFillModel(1.0, prob_slippage, 0)
    engine = make_engine(latency, fill_model)
    strategy = SignalFromFirstCompletedBar()
    engine.add_strategy(strategy)
    engine.add_data(make_bars())
    engine.run()

    orders = engine.cache.orders(instrument_id=INSTRUMENT_ID)
    order_events = [] if not orders else [event.to_dict() for event in orders[0].events()]
    fills = [event for event in order_events if event["type"] == "OrderFilled"]
    rejections = [event for event in order_events if event["type"] == "OrderRejected"]
    positions = [
        {
            "instrument_id": str(position.instrument_id),
            "signed_qty": position.signed_qty,
            "quantity": str(position.quantity),
            "average_open_price": str(position.avg_px_open),
        }
        for position in engine.cache.positions(instrument_id=INSTRUMENT_ID)
    ]

    initialized_ts = event_timestamp(order_events, "OrderInitialized")
    submitted_ts = event_timestamp(order_events, "OrderSubmitted")
    accepted_ts = event_timestamp(order_events, "OrderAccepted")
    filled_ts = event_timestamp(order_events, "OrderFilled")
    rejected_ts = event_timestamp(order_events, "OrderRejected")
    command_arrival_ts = next(
        (
            int(item["ts_event"])
            for item in order_events
            if item["type"]
            in {"OrderAccepted", "OrderFilled", "OrderRejected", "OrderDenied"}
        ),
        None,
    )
    fill_price = None if not fills else Decimal(fills[0]["last_px"])
    fill_time = None if not fills else int(fills[0]["ts_event"])
    same_signal_bar_price = (
        False if fill_price is None else SIGNAL_LOW <= fill_price <= SIGNAL_HIGH
    )
    positive_invariant_pass = bool(
        fills
        and fill_time is not None
        and fill_time > SIGNAL_BAR_AVAILABLE_AT
        and not same_signal_bar_price
    )
    negative_invariant_violation = bool(
        fills
        and fill_time == SIGNAL_BAR_AVAILABLE_AT
        and same_signal_bar_price
    )

    if acceptance_role == "G02_POSITIVE":
        checker_outcome = "CHECK_PASS" if positive_invariant_pass else "CHECK_BLOCKED"
        qualification_status = "PASS" if positive_invariant_pass else "BLOCKED"
    elif acceptance_role == "G03_NEGATIVE_CONTROL":
        checker_outcome = "CHECK_FAIL" if negative_invariant_violation else "CHECK_BLOCKED"
        qualification_status = "PASS" if negative_invariant_violation else "BLOCKED"
    else:
        checker_outcome = "NOT_ACCEPTANCE"
        qualification_status = "DIAGNOSTIC_FILL" if fills else "DIAGNOSTIC_NO_FILL"

    result = {
        "name": name,
        "acceptance_role": acceptance_role,
        "qualification_status": qualification_status,
        "checker_outcome": checker_outcome,
        "configuration": engine_configuration(latency, fill_model),
        "effective_insert_latency_nanos": base_latency_nanos,
        "bars": strategy.bar_callbacks,
        "signal": strategy.signal,
        "event_sequence": [item["type"] for item in order_events],
        "order_events": order_events,
        "timestamps": {
            "signal_bar_available_at_ns": SIGNAL_BAR_AVAILABLE_AT,
            "signal_timestamp_ns": None
            if strategy.signal is None
            else strategy.signal["signal_timestamp_ns"],
            "order_initialized_ns": initialized_ts,
            "order_submitted_ns": submitted_ts,
            "order_accepted_ns": accepted_ts,
            "command_arrival_ns": command_arrival_ts,
            "order_filled_ns": filled_ts,
            "order_rejected_ns": rejected_ts,
        },
        "fill_count": len(fills),
        "fill_price": None if not fills else fills[0]["last_px"],
        "fill_quantity": None if not fills else fills[0]["last_qty"],
        "fill_time_gt_signal_available_at": bool(
            fill_time is not None and fill_time > SIGNAL_BAR_AVAILABLE_AT
        ),
        "fill_price_in_signal_bar_range": same_signal_bar_price,
        "positive_invariant_pass": positive_invariant_pass,
        "negative_invariant_violation": negative_invariant_violation,
        "positions": positions,
        "rejection_reasons": [item["reason"] for item in rejections],
        "simulated_liquidity": {
            "bar_volume": "1000",
            "size_increment": "1",
            "source_derived_open_leg_quantity": "250",
            "source_derived_high_leg_quantity": "250",
            "source_derived_low_leg_quantity": "250",
            "source_derived_close_leg_quantity": "250",
            "order_quantity": "1",
            "each_leg_exceeds_order": True,
            "matching_book_quantity_exposed_by_public_python_api": False,
        },
    }
    engine.dispose()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    runtime = importlib.metadata.version("nautilus-trader")
    if runtime != EXPECTED_RUNTIME:
        raise RuntimeError(f"Expected {EXPECTED_RUNTIME}, observed {runtime}")

    positive = run_case(
        name="causal-positive",
        base_latency_nanos=60_000_000_000,
        prob_slippage=1.0,
        acceptance_role="G02_POSITIVE",
    )
    negative = run_case(
        name="zero-latency-negative-control",
        base_latency_nanos=0,
        prob_slippage=1.0,
        acceptance_role="G03_NEGATIVE_CONTROL",
    )

    locked_contract_pass = (
        positive["qualification_status"] == "PASS"
        and negative["qualification_status"] == "PASS"
    )
    diagnostic: dict[str, Any]
    if locked_contract_pass:
        diagnostic = {
            "executed": False,
            "reason": "The locked Standard FillModel 1.0/1.0/0 contract passed; the conditional prob_slippage=0.0 diagnostic was not needed.",
        }
    else:
        diagnostic = {
            "executed": True,
            "result": run_case(
                name="diagnostic-prob-slippage-zero",
                base_latency_nanos=60_000_000_000,
                prob_slippage=0.0,
                acceptance_role="DIAGNOSTIC_ONLY",
            ),
        }

    output = {
        "schema": "causal-fill-repair-001-probe-v1",
        "runtime": f"nautilus_trader=={runtime}",
        "source_commit": SOURCE_COMMIT,
        "public_api_only": True,
        "private_pyo3_api_used": False,
        "custom_fill_model_used": False,
        "synthetic_bid_ask_or_quote_data_used": False,
        "positive": positive,
        "negative_control": negative,
        "diagnostic": diagnostic,
        "tests": {
            "acceptance_cases_executed": 2,
            "acceptance_cases_passed": 2 if locked_contract_pass else 0,
            "diagnostic_cases_executed": 0 if locked_contract_pass else 1,
        },
        "verdict": (
            "V2_MIGRATION_GATE_PASS_AFTER_CAUSAL_REPAIR"
            if locked_contract_pass
            else "V2_MIGRATION_GATE_BLOCKED_CONFIRMED"
        ),
    }
    serialized = json.dumps(output, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if locked_contract_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
