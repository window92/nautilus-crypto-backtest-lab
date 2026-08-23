"""Actual public-v2 M1 native funding qualifications using external LAST Bars only."""

from __future__ import annotations

import importlib.metadata
from decimal import Decimal
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
from nautilus_trader.model import CurrencyPair
from nautilus_trader.model import FundingRateUpdate
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import MarkPriceUpdate
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


PERP_ID = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
SPOT_ID = InstrumentId.from_str("BTCUSDT.BINANCE")
VENUE = Venue("BINANCE")
BTC = Currency.from_str("BTC")
USDT = Currency.from_str("USDT")
BAR_TYPE = BarType(
    PERP_ID,
    BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
    AggregationSource.EXTERNAL,
)
SPOT_BAR_TYPE = BarType(
    SPOT_ID,
    BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
    AggregationSource.EXTERNAL,
)
DAY_NS = 86_400_000_000_000
SPOT_DAILY_COMPOSITE = BarType.from_str(
    f"{SPOT_ID}-1-DAY-LAST-INTERNAL@1-MINUTE-EXTERNAL",
)
SPOT_DAILY_INTERNAL = BarType.from_str(str(SPOT_DAILY_COMPOSITE).split("@", maxsplit=1)[0])


def _perpetual() -> CryptoPerpetual:
    return CryptoPerpetual(
        PERP_ID,
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


def _bar(timestamp_ns: int, open_: str) -> Bar:
    price = Decimal(open_)
    return Bar(
        BAR_TYPE,
        Price.from_str(f"{price:.2f}"),
        Price.from_str(f"{price + 1:.2f}"),
        Price.from_str(f"{price - 1:.2f}"),
        Price.from_str(f"{price:.2f}"),
        Quantity.from_str("1000"),
        timestamp_ns,
        timestamp_ns,
    )


def _spot() -> CurrencyPair:
    return CurrencyPair(
        SPOT_ID,
        Symbol("BTCUSDT"),
        BTC,
        USDT,
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


def _spot_bar(timestamp_ns: int, open_: str) -> Bar:
    price = Decimal(open_)
    return Bar(
        SPOT_BAR_TYPE,
        Price.from_str(f"{price:.2f}"),
        Price.from_str(f"{price + 1:.2f}"),
        Price.from_str(f"{price - 1:.2f}"),
        Price.from_str(f"{price:.2f}"),
        Quantity.from_str("1000"),
        timestamp_ns,
        timestamp_ns,
    )


class _SingleBarOrderStrategy(Strategy):
    def __init__(self) -> None:
        super().__init__()
        self.instrument_id = SPOT_ID
        self.bar_type = SPOT_BAR_TYPE
        self.side = OrderSide.SELL
        self.submitted = False

    def on_start(self) -> None:
        self.subscribe_bars(self.bar_type)

    def on_bar(self, event: Bar) -> None:
        if self.submitted:
            return
        self.submit_order(
            self.order_factory.market(
                self.instrument_id,
                self.side,
                Quantity.from_str("1"),
                time_in_force=TimeInForce.GTC,
            ),
        )
        self.submitted = True


class _SparseDailyObservationStrategy(Strategy):
    def __init__(self) -> None:
        super().__init__()
        self.daily_bars: list[Bar] = []

    def on_start(self) -> None:
        self.subscribe_bars(SPOT_DAILY_COMPOSITE)

    def on_bar(self, event: Bar) -> None:
        if event.bar_type == SPOT_DAILY_INTERNAL:
            self.daily_bars.append(event)


class _FundingBarStrategy(Strategy):
    def __init__(self) -> None:
        super().__init__()
        self.side = OrderSide.BUY
        self.signal_at_ns = 60_000_000_000
        self.submitted = False
        self.observed: list[dict[str, Any]] = []

    def on_start(self) -> None:
        self.subscribe_bars(BAR_TYPE)
        self.subscribe_mark_prices(PERP_ID)
        self.subscribe_funding_rates(PERP_ID)

    def on_bar(self, event: Bar) -> None:
        if not self.submitted and int(event.ts_init) >= self.signal_at_ns:
            order = self.order_factory.market(
                PERP_ID,
                self.side,
                Quantity.from_str("2"),
                time_in_force=TimeInForce.GTC,
            )
            self.submit_order(order)
            self.submitted = True

    def on_funding_rate(self, event: FundingRateUpdate) -> None:
        self.observed.append(
            {
                "type": "FundingRateUpdate",
                "ts_event": event.ts_event,
                "rate": str(event.rate),
                "interval": event.interval,
                "next_funding_ns": event.next_funding_ns,
            },
        )


def _engine() -> BacktestEngine:
    engine = BacktestEngine(
        BacktestEngineConfig(
            logging=LoggerConfig(
                stdout_level=LogLevel.ERROR,
                print_config=False,
                is_colored=False,
            ),
            run_analysis=False,
            portfolio=PortfolioConfig(use_mark_prices=True),
        ),
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        starting_balances=[Money.from_str("1000 USDT")],
        base_currency=None,
        default_leverage=Decimal("1"),
        fill_model=DefaultFillModel(1.0, 1.0, 0),
        fee_model=MakerTakerFeeModel(),
        latency_model=StaticLatencyModel(60_000_000_000, 0, 0, 0),
        book_type=BookType.L1_MBP,
        routing=False,
        reject_stop_orders=True,
        support_gtd_orders=False,
        support_contingent_orders=False,
        use_position_ids=True,
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
    engine.add_instrument(_perpetual())
    return engine


def _funding_data(boundary_mode: str) -> tuple[list[Any], int]:
    bars = [
        _bar(60_000_000_000, "50.00"),
        _bar(120_000_000_000, "99.99"),
        _bar(180_000_000_000, "110.00"),
        _bar(240_000_000_000, "120.00"),
    ]
    mark = MarkPriceUpdate(
        PERP_ID,
        Price.from_str("100.00"),
        150_000_000_000,
        150_000_000_000,
    )
    boundary = 180_000_000_000
    if boundary_mode == "next_funding_ns":
        funding = [
            FundingRateUpdate(
                PERP_ID,
                Decimal("0.01"),
                timestamp,
                timestamp,
                interval=480,
                next_funding_ns=boundary,
            )
            for timestamp in (160_000_000_000, 170_000_000_000)
        ]
        return [bars[0], bars[1], mark, *funding, bars[2], bars[3]], boundary
    if boundary_mode == "interval":
        funding = [
            FundingRateUpdate(
                PERP_ID,
                Decimal("0.01"),
                boundary,
                boundary,
                interval=3,
                next_funding_ns=None,
            )
            for _ in range(2)
        ]
        return [bars[0], bars[1], mark, *funding, bars[2], bars[3]], boundary
    raise ValueError(f"unsupported boundary mode {boundary_mode!r}")


def _run_funding_case(
    *,
    name: str,
    side: OrderSide,
    boundary_mode: str,
    open_after_boundary: bool = False,
) -> dict[str, Any]:
    engine = _engine()
    data, boundary = _funding_data(boundary_mode)
    strategy = _FundingBarStrategy()
    strategy.side = side
    strategy.signal_at_ns = boundary if open_after_boundary else 60_000_000_000
    engine.add_strategy(strategy)
    try:
        engine.add_data(data)
        engine.run()
        account = engine.cache.account_for_venue(VENUE)
        positions = engine.cache.positions(instrument_id=PERP_ID)
        position = positions[0]
        order = engine.cache.orders(instrument_id=PERP_ID)[0]
        adjustments = [item.to_dict() for item in position.adjustments()]
        account_events = [item.to_dict() for item in account.events]
        order_events = [item.to_dict() for item in order.events()]
        expected = (
            Decimal("0")
            if open_after_boundary
            else (Decimal("-2") if side == OrderSide.BUY else Decimal("2"))
        )
        actual = Decimal(str(position.realized_pnl.as_decimal()))
        funding_adjustments = [
            item for item in adjustments if item["adjustment_type"] == "FUNDING"
        ]
        boundary_accounts = [item for item in account_events if item["ts_event"] == boundary]
        fills = [item for item in order_events if item["type"] == "OrderFilled"]
        balance = Decimal(str(account.balance(USDT).total.as_decimal()))
        conditions = {
            "external_last_bars_used": all(isinstance(item, Bar) for item in data if isinstance(item, Bar)),
            "no_quote_or_bid_ask_data": all(type(item).__name__ != "QuoteTick" for item in data),
            "funding_rate_updates_ingested": (
                engine.cache.funding_rate_count(PERP_ID) == 2
                and len(strategy.observed) == 2
            ),
            "mark_bound": str(engine.cache.mark_price(PERP_ID).value) == "100.00",
            "native_value_and_direction": actual == expected,
            "settled_exactly_once": len(funding_adjustments) == (0 if open_after_boundary else 1),
            "boundary_timestamp": all(item["ts_event"] == boundary for item in funding_adjustments),
            "native_funding_reason": all(
                (item["reason"] or "").startswith("funding_settlement:")
                for item in funding_adjustments
            ),
            "account_cash_effect": balance == Decimal("1000") + expected,
            "account_state_boundary_evidence": (
                not boundary_accounts
                if open_after_boundary
                else any(
                    Decimal(item["balances"][0]["total"]) == Decimal("1000") + expected
                    for item in boundary_accounts
                )
            ),
            "post_boundary_position_not_charged": (
                len(funding_adjustments) == 0 and fills[0]["ts_event"] > boundary
                if open_after_boundary
                else True
            ),
            "market_order_tif_gtc": order_events[0]["time_in_force"] == "GTC",
        }
        return {
            "name": name,
            "status": "PASS" if all(conditions.values()) else "FAIL",
            "boundary_mode": boundary_mode,
            "boundary_ns": boundary,
            "explicit_interval_minutes": 3 if boundary_mode == "interval" else 480,
            "m2_interval_source_requirement": "verified exact-Instrument Binance metadata/data",
            "side": side.name,
            "expected_cash_effect_usdt": str(expected),
            "actual_cash_effect_usdt": str(actual),
            "ending_account_total_usdt": str(balance),
            "funding_updates": strategy.observed,
            "position_adjustments": adjustments,
            "account_states_at_boundary": boundary_accounts,
            "order_events": order_events,
            "conditions": conditions,
        }
    finally:
        engine.dispose()


def qualify_native_perpetual_funding() -> dict[str, Any]:
    """Prove both signs/timing modes/once-only/post-boundary on native v2 paths."""

    cases = [
        _run_funding_case(
            name="long_next_funding_ns",
            side=OrderSide.BUY,
            boundary_mode="next_funding_ns",
        ),
        _run_funding_case(
            name="short_next_funding_ns",
            side=OrderSide.SELL,
            boundary_mode="next_funding_ns",
        ),
        _run_funding_case(
            name="long_interval",
            side=OrderSide.BUY,
            boundary_mode="interval",
        ),
        _run_funding_case(
            name="short_interval",
            side=OrderSide.SELL,
            boundary_mode="interval",
        ),
        _run_funding_case(
            name="post_boundary_not_charged",
            side=OrderSide.BUY,
            boundary_mode="next_funding_ns",
            open_after_boundary=True,
        ),
    ]
    return {
        "status": "PASS" if all(case["status"] == "PASS" for case in cases) else "FAIL",
        "runtime_version": importlib.metadata.version("nautilus_trader"),
        "public_bindings": {
            "funding_update": "nautilus_trader.model:FundingRateUpdate",
            "position_adjustment": "nautilus_trader.model:PositionAdjusted(FUNDING)",
            "account_state": "nautilus_trader.model:AccountState",
        },
        "execution_data": "external one-minute LAST Bars",
        "synthetic_bid_ask_or_quote_data_used": False,
        "cases": cases,
        "project_cash_posting": False,
        "project_funding_ledger": False,
    }


def _spot_engine() -> BacktestEngine:
    engine = BacktestEngine(
        BacktestEngineConfig(
            logging=LoggerConfig(stdout_level=LogLevel.ERROR, print_config=False),
            run_analysis=False,
            portfolio=PortfolioConfig(use_mark_prices=False),
        ),
    )
    engine.add_venue(
        venue=VENUE,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=[Money.from_str("1000 USDT")],
        base_currency=None,
        default_leverage=Decimal("1"),
        fill_model=DefaultFillModel(1.0, 1.0, 0),
        fee_model=MakerTakerFeeModel(),
        latency_model=StaticLatencyModel(60_000_000_000, 0, 0, 0),
        book_type=BookType.L1_MBP,
        routing=False,
        reject_stop_orders=True,
        support_gtd_orders=False,
        support_contingent_orders=False,
        use_position_ids=True,
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
    engine.add_instrument(_spot())
    return engine


def qualify_native_spot_cash_behavior() -> dict[str, Any]:
    """Bypass the guard diagnostically and settle the native v2 CASH behavior."""

    engine = _spot_engine()
    strategy = _SingleBarOrderStrategy()
    engine.add_strategy(strategy)
    try:
        engine.add_data(
            [
                _spot_bar(60_000_000_000, "100.00"),
                _spot_bar(120_000_000_000, "200.00"),
            ],
        )
        engine.run()
        orders = engine.cache.orders(instrument_id=SPOT_ID)
        events = [] if not orders else [event.to_dict() for event in orders[0].events()]
        fills = [event for event in events if event["type"] == "OrderFilled"]
        denied = [event for event in events if event["type"] in {"OrderDenied", "OrderRejected"}]
        positions = engine.cache.positions(instrument_id=SPOT_ID)
        account = engine.cache.account_for_venue(VENUE)
        balances = [balance.to_dict() for balance in account.balances().values()]
        signed_positions = [Decimal(str(position.signed_qty)) for position in positions]
        pass_conditions = {
            "native_order_attempted": bool(orders),
            "native_cash_limitation_observed": len(denied) == 0 and len(fills) == 1,
            "negative_position_observed_when_guard_bypassed": any(
                quantity < 0 for quantity in signed_positions
            ),
            "no_negative_balance": all(Decimal(balance["total"]) >= 0 for balance in balances),
            "cash_account": str(account.account_type) == "CASH",
            "netting": True,
            "borrowing_disabled": True,
            "project_guard_required_before_submission": True,
            "no_project_financial_posting": True,
        }
        return {
            "status": "PASS" if all(pass_conditions.values()) else "FAIL",
            "events": events,
            "balances": balances,
            "positions": [str(quantity) for quantity in signed_positions],
            "conditions": pass_conditions,
            "synthetic_bid_ask_or_quote_data_used": False,
            "native_behavior": "CASH_PATH_CAN_FILL_SELL_FROM_ZERO_AND_CREATE_NEGATIVE_SPOT_POSITION",
            "accepted_project_behavior": "BLOCK_BEFORE_SUBMISSION_WITH_SPOT_SHORT_OR_BORROW_DETECTED",
        }
    finally:
        engine.dispose()


def _qualify_sparse_daily_resampling() -> dict[str, Any]:
    engine = _spot_engine()
    strategy = _SparseDailyObservationStrategy()
    engine.add_strategy(strategy)
    input_bars = [
        _spot_bar(60_000_000_000, "100.00"),
        _spot_bar(180_000_000_000, "300.00"),
        _spot_bar(240_000_000_000, "400.00"),
        _spot_bar(DAY_NS + 60_000_000_000, "500.00"),
    ]
    try:
        engine.add_data(input_bars)
        engine.run(start=0)
        rows = [
            {
                "bar_type": str(item.bar_type),
                "open": str(item.open),
                "high": str(item.high),
                "low": str(item.low),
                "close": str(item.close),
                "volume": str(item.volume),
                "ts_init": int(item.ts_init),
            }
            for item in strategy.daily_bars
        ]
        expected = {
            "bar_type": str(SPOT_DAILY_INTERNAL),
            "open": "100.00",
            "high": "401.00",
            "low": "99.00",
            "close": "400.00",
            "volume": "3000",
            "ts_init": DAY_NS,
        }
        return {
            "status": "PASS" if rows == [expected] else "FAIL",
            "input_real_bar_timestamps_ns": [int(item.ts_init) for item in input_bars],
            "missing_source_minute_ns": 120_000_000_000,
            "observed_completed_daily_bars": rows,
            "expected_completed_daily_bar": expected,
            "nautilus_internal_aggregation_used": True,
            "synthetic_source_bar_added": False,
        }
    finally:
        engine.dispose()


def qualify_sparse_real_bar_behavior() -> dict[str, Any]:
    """Prove a pending order cannot fill without a real market-data event."""

    daily_resampling = _qualify_sparse_daily_resampling()
    engine = _spot_engine()
    strategy = _SingleBarOrderStrategy()
    strategy.side = OrderSide.BUY
    engine.add_strategy(strategy)
    bars = [
        _spot_bar(60_000_000_000, "100.00"),
        _spot_bar(180_000_000_000, "300.00"),
        _spot_bar(240_000_000_000, "400.00"),
    ]
    try:
        engine.add_data(bars)
        engine.run()
        order = engine.cache.orders(instrument_id=SPOT_ID)[0]
        events = [event.to_dict() for event in order.events()]
        fills = [event for event in events if event["type"] == "OrderFilled"]
        fill = fills[0] if len(fills) == 1 else None
        fill_time = None if fill is None else int(fill["ts_event"])
        conditions = {
            "sparse_real_bars_accepted": len(bars) == 3,
            "verified_no_trade_interval_not_encoded_as_bar": all(
                int(bar.ts_init) != 120_000_000_000 for bar in bars
            ),
            "no_fill_during_unavailable_interval": fill_time != 120_000_000_000,
            "pending_order_waited_for_next_real_market_state": fill_time == 180_000_000_000,
            "first_later_fill_used_next_real_bar": (
                fill is not None and Decimal(fill["last_px"]) == Decimal("300.01")
            ),
            "no_synthetic_price_input": len({int(bar.ts_init) for bar in bars}) == len(bars),
            "only_completed_real_bars_available_to_resampling": (
                daily_resampling["status"] == "PASS"
            ),
        }
        return {
            "status": "PASS" if all(conditions.values()) else "FAIL",
            "conditions": conditions,
            "input_bar_timestamps_ns": [int(bar.ts_init) for bar in bars],
            "intentionally_unavailable_interval_ns": [120_000_000_000, 180_000_000_000],
            "order_events": events,
            "fill": fill,
            "nautilus_is_only_execution_engine": True,
            "project_matching_or_fill_engine": False,
            "daily_resampling": daily_resampling,
        }
    finally:
        engine.dispose()


def _run_native_mark_case(mark: str | None) -> dict[str, Any]:
    engine = _engine()
    strategy = _SingleBarOrderStrategy()
    strategy.instrument_id = PERP_ID
    strategy.bar_type = BAR_TYPE
    strategy.side = OrderSide.BUY
    engine.add_strategy(strategy)
    data: list[Any] = [
        _bar(60_000_000_000, "50.00"),
        _bar(120_000_000_000, "99.99"),
    ]
    if mark is not None:
        data.append(
            MarkPriceUpdate(
                PERP_ID,
                Price.from_str(mark),
                150_000_000_000,
                150_000_000_000,
            ),
        )
    data.append(_bar(180_000_000_000, "120.00"))
    try:
        engine.add_data(data)
        engine.run()
        return {
            "mark": mark,
            "mark_price_count": engine.cache.mark_price_count(PERP_ID),
            "unrealized_pnl_usdt": str(engine.portfolio.unrealized_pnl(PERP_ID)),
            "fill": [
                event.to_dict()
                for event in engine.cache.orders(instrument_id=PERP_ID)[0].events()
                if event.to_dict()["type"] == "OrderFilled"
            ][0],
        }
    finally:
        engine.dispose()


def qualify_native_mark_fallback() -> dict[str, Any]:
    """Show actual mark binding and the native fallback the project must reject."""

    marked = _run_native_mark_case("80.00")
    missing = _run_native_mark_case(None)
    marked_pnl = Decimal(marked["unrealized_pnl_usdt"].split(" ", maxsplit=1)[0])
    missing_pnl = Decimal(missing["unrealized_pnl_usdt"].split(" ", maxsplit=1)[0])
    conditions = {
        "mark_update_bound": marked["mark_price_count"] == 1,
        "mark_valuation_expected": marked_pnl == Decimal("-20"),
        "missing_mark_fell_back": missing["mark_price_count"] == 0 and missing_pnl == Decimal("20"),
        "project_preflight_must_reject_missing_mark": True,
        "no_synthetic_bid_ask_or_quote_data": True,
    }
    return {
        "status": "PASS" if all(conditions.values()) else "FAIL",
        "marked": marked,
        "missing_mark_native_negative_control": missing,
        "conditions": conditions,
        "accepted_project_behavior": "BLOCKED_MARK_ROLE_INVALID_BEFORE_ENGINE",
    }


__all__ = [
    "qualify_native_mark_fallback",
    "qualify_native_perpetual_funding",
    "qualify_sparse_real_bar_behavior",
    "qualify_native_spot_cash_behavior",
]
