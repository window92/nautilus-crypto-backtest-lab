"""Native v2 runtime qualifications retained separately from the M1 runner."""

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
from nautilus_trader.model import BookType
from nautilus_trader.model import CryptoPerpetual
from nautilus_trader.model import Currency
from nautilus_trader.model import FundingRateUpdate
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import MarkPriceUpdate
from nautilus_trader.model import Money
from nautilus_trader.model import OmsType
from nautilus_trader.model import OrderSide
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import QuoteTick
from nautilus_trader.model import Symbol
from nautilus_trader.model import TimeInForce
from nautilus_trader.model import Venue
from nautilus_trader.portfolio import PortfolioConfig
from nautilus_trader.trading import Strategy


PERP_ID = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
VENUE = Venue("BINANCE")
BTC = Currency.from_str("BTC")
USDT = Currency.from_str("USDT")


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


def _quote(ts: int) -> QuoteTick:
    return QuoteTick(
        PERP_ID,
        Price.from_str("99.99"),
        Price.from_str("100.00"),
        Quantity.from_str("100"),
        Quantity.from_str("100"),
        ts,
        ts,
    )


class _FundingStrategy(Strategy):
    def __init__(self) -> None:
        super().__init__()
        self.side = OrderSide.BUY
        self.open_at_ns = 0
        self.submitted = False
        self.observed: list[dict[str, Any]] = []

    def on_start(self) -> None:
        self.subscribe_quotes(PERP_ID)
        self.subscribe_mark_prices(PERP_ID)
        self.subscribe_funding_rates(PERP_ID)

    def on_quote(self, event: QuoteTick) -> None:
        if not self.submitted and event.ts_event >= self.open_at_ns:
            order = self.order_factory.market(
                PERP_ID,
                self.side,
                Quantity.from_str("2"),
                time_in_force=TimeInForce.IOC,
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
        base_currency=USDT,
        default_leverage=Decimal("1"),
        fill_model=DefaultFillModel(1.0, 1.0, 0),
        fee_model=MakerTakerFeeModel(),
        latency_model=StaticLatencyModel(),
        book_type=BookType.L1_MBP,
        routing=False,
        reject_stop_orders=True,
        support_gtd_orders=False,
        support_contingent_orders=False,
        use_position_ids=False,
        use_random_ids=False,
        use_reduce_only=True,
        use_market_order_acks=False,
        bar_execution=True,
        bar_adaptive_high_low_ordering=False,
        trade_execution=False,
        liquidity_consumption=True,
        queue_position=False,
        allow_cash_borrowing=False,
        frozen_account=False,
        price_protection_points=0,
    )
    engine.add_instrument(_perpetual())
    return engine


def _run_funding_case(
    *,
    name: str,
    side: OrderSide,
    boundary_mode: str,
    open_after_boundary: bool = False,
) -> dict[str, Any]:
    engine = _engine()
    boundary = 4_000_000_000 if boundary_mode == "next_funding_ns" else 60_000_000_000
    strategy = _FundingStrategy()
    strategy.side = side
    strategy.open_at_ns = 5_000_000_000 if open_after_boundary else 1_000_000_000
    engine.add_strategy(strategy)
    if boundary_mode == "next_funding_ns":
        funding = [
            FundingRateUpdate(
                PERP_ID,
                Decimal("0.01"),
                ts,
                ts,
                interval=480,
                next_funding_ns=boundary,
            )
            for ts in (3_000_000_000, 3_100_000_000)
        ]
        data = [
            _quote(1_000_000_000),
            _quote(2_000_000_000),
            MarkPriceUpdate(
                PERP_ID,
                Price.from_str("100.00"),
                2_500_000_000,
                2_500_000_000,
            ),
            *funding,
            _quote(5_000_000_000),
            _quote(6_000_000_000),
        ]
    elif boundary_mode == "interval":
        funding = [
            FundingRateUpdate(
                PERP_ID,
                Decimal("0.01"),
                boundary,
                boundary,
                interval=1,
                next_funding_ns=None,
            )
            for _ in range(2)
        ]
        data = [
            _quote(1_000_000_000),
            _quote(2_000_000_000),
            MarkPriceUpdate(
                PERP_ID,
                Price.from_str("100.00"),
                50_000_000_000,
                50_000_000_000,
            ),
            *funding,
            _quote(61_000_000_000),
        ]
    else:
        raise ValueError(f"unsupported boundary mode {boundary_mode!r}")

    try:
        engine.add_data(data)
        engine.run()
        account = engine.cache.account_for_venue(VENUE)
        position = engine.cache.positions_open(instrument_id=PERP_ID)[0]
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
        boundary_accounts = [
            item for item in account_events if item["ts_event"] == boundary
        ]
        fills = [item for item in order_events if item["type"] == "OrderFilled"]
        balance = Decimal(str(account.balance(USDT).total.as_decimal()))
        conditions = {
            "funding_rate_updates_ingested": (
                engine.cache.funding_rate_count(PERP_ID) == 2
                and len(strategy.observed) == 2
            ),
            "mark_bound": str(engine.cache.mark_price(PERP_ID).value) == "100.00",
            "native_value_and_direction": actual == expected,
            "settled_exactly_once": len(funding_adjustments) == (0 if open_after_boundary else 1),
            "boundary_timestamp": all(
                item["ts_event"] == boundary for item in funding_adjustments
            ),
            "native_funding_reason": all(
                (item["reason"] or "").startswith("funding_settlement:")
                for item in funding_adjustments
            ),
            "account_cash_effect": balance == Decimal("1000") + expected,
            "account_state_boundary_evidence": (
                not boundary_accounts
                if open_after_boundary
                else any(
                    Decimal(item["balances"][0]["total"])
                    == Decimal("1000") + expected
                    for item in boundary_accounts
                )
            ),
            "post_boundary_position_not_charged": (
                len(funding_adjustments) == 0 and fills[0]["ts_event"] > boundary
                if open_after_boundary
                else True
            ),
        }
        return {
            "name": name,
            "status": "PASS" if all(conditions.values()) else "FAIL",
            "boundary_mode": boundary_mode,
            "boundary_ns": boundary,
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
    """Prove long/short sign, both timing modes, once-only, and post-boundary exclusion."""

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
        "cases": cases,
        "project_cash_posting": False,
    }
