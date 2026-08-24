"""Synthetic causal qualifications for the registered OWNER_SMOKE SMA20 path."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import CryptoPerpetual
from nautilus_trader.model import Currency
from nautilus_trader.model import CurrencyPair
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import MarkPriceUpdate
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import Symbol

from crypto_lab.config import MarketProfile
from crypto_lab.config import SourceRevision
from crypto_lab.native_positions import capture_native_completed_position_sequence
from crypto_lab.nautilus_config import add_venue_from_config
from crypto_lab.nautilus_config import to_nautilus_engine_config
from crypto_lab.strategies import BtcusdtDailyPriceVsSma20Trend
from crypto_lab.strategies import create_registered_strategy
from crypto_lab.strategies import locked_sma20_strategy_spec
from crypto_lab.strategies import resolve_registered_strategy_identity
from crypto_lab.strategies.daily_sma_trend import DAY_NS
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import PERP_ID
from tests.m1_helpers import a4_bars
from tests.m1_helpers import complete_perpetual_roles
from tests.m1_helpers import make_request
from tests.m1_helpers import plan


MINUTE_NS = 60_000_000_000


def _source() -> SourceRevision:
    return SourceRevision(
        repository="https://github.com/window92/nautilus-crypto-backtest-lab.git",
        branch_ref="main",
        git_commit="1" * 40,
        git_tree="2" * 40,
        clean_worktree=True,
        captured_at_utc=datetime(2026, 8, 22, tzinfo=UTC),
    )


def _instrument(profile: MarketProfile):
    common = dict(
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        ts_event=0,
        ts_init=0,
        multiplier=Quantity.from_str("1"),
        margin_init=Decimal("1"),
        margin_maint=Decimal("1"),
        maker_fee=Decimal("0"),
        taker_fee=Decimal("0"),
    )
    btc = Currency.from_str("BTC")
    usdt = Currency.from_str("USDT")
    if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        return CurrencyPair(
            SPOT_ID,
            Symbol("BTCUSDT"),
            btc,
            usdt,
            size_precision=5,
            size_increment=Quantity.from_str("0.00001"),
            **common,
        )
    return CryptoPerpetual(
        PERP_ID,
        Symbol("BTCUSDT"),
        btc,
        usdt,
        usdt,
        False,
        size_precision=3,
        size_increment=Quantity.from_str("0.001"),
        **common,
    )


def _execution_bars(instrument_id: InstrumentId) -> tuple[Bar, ...]:
    bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
    result = []
    final_minute = 22 * 1_440 + 3
    for minute in range(1, final_minute + 1):
        day = (minute - 1) // 1_440
        close = "100.00" if day < 20 else ("110.00" if day == 20 else "90.00")
        value = Decimal(close)
        result.append(
            Bar(
                bar_type,
                Price.from_str(close),
                Price.from_str(f"{value + Decimal('0.10'):.2f}"),
                Price.from_str(f"{value - Decimal('0.10'):.2f}"),
                Price.from_str(close),
                Quantity.from_str("1.00000" if instrument_id == SPOT_ID else "1.000"),
                minute * MINUTE_NS,
                minute * MINUTE_NS,
            ),
        )
    return tuple(result)


class OwnerSmokeDailyStrategyIntegrationTests(unittest.TestCase):
    def _run(self, profile: MarketProfile):
        instrument_id = SPOT_ID if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else PERP_ID
        execution_bars = _execution_bars(instrument_id)
        seed_data = (
            execution_bars[:3]
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else complete_perpetual_roles(execution_bars[:3])
        )
        with tempfile.TemporaryDirectory() as temporary:
            request = make_request(
                Path(temporary),
                run_id="owner-smoke-synthetic-config",
                profile=profile,
                data=seed_data,
                plan=plan({}),
                scoring_start_ns=0,
                scoring_end_ns=3 * MINUTE_NS,
            )
            native_instrument = _instrument(profile)
            engine = BacktestEngine(to_nautilus_engine_config(request.lab_run_config.nautilus_engine_config))
            add_venue_from_config(engine, request.lab_run_config.nautilus_venue_config)
            engine.add_instrument(native_instrument)
            spec = locked_sma20_strategy_spec(profile)
            identity = resolve_registered_strategy_identity(
                BtcusdtDailyPriceVsSma20Trend.REGISTRATION_ID,
                strategy_spec=spec,
                source_revision=_source(),
            )
            signal_bar_type = BarType.from_str(spec.signal_bar_types[0])
            strategy = create_registered_strategy(
                identity,
                strategy_spec=spec,
                source_revision=_source(),
                configuration={
                    "instrument_id": instrument_id,
                    "bar_type": signal_bar_type,
                    "execution_bar_type": execution_bars[0].bar_type,
                    "profile": profile,
                    "scoring_start_ns": 20 * DAY_NS,
                    "scoring_end_exclusive_ns": 23 * DAY_NS,
                    "effective_insert_latency_ns": MINUTE_NS,
                    "size_precision": native_instrument.size_precision,
                    "min_quantity": None,
                    "max_quantity": None,
                    "size_increment": native_instrument.size_increment.as_decimal(),
                    "initial_capital_amount": Decimal("1000.00"),
                    "initial_capital_currency": "USDT",
                },
            )
            engine.add_strategy(strategy)
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
                data = list(execution_bars)
            else:
                data = [
                    item
                    for bar in execution_bars
                    for item in (
                        MarkPriceUpdate(
                            instrument_id,
                            Price.from_str(str(bar.close)),
                            int(bar.ts_init),
                            int(bar.ts_init),
                        ),
                        bar,
                    )
                ]
            engine.add_data(data)
            # The economic window begins on the exact UTC boundary, one minute
            # before the first completed external 1m Bar becomes available.
            engine.run(start=0)
            orders = tuple(engine.cache.orders(instrument_id=instrument_id))
            fills = tuple(
                event
                for order in orders
                for event in order.events()
                if type(event).__name__ == "OrderFilled"
            )
            signed_position = sum(
                (Decimal(str(item.signed_qty)) for item in engine.cache.positions_open(instrument_id=instrument_id)),
                Decimal(0),
            )
            observations = strategy.observations
            native_completed = capture_native_completed_position_sequence(
                engine.cache,
                instrument_id=instrument_id,
                source_run_id="owner-smoke-native-position-qualification",
                expected_settlement_currency="USDT",
                expected_closed_cycle_count=sum(
                    item["event_type"] == "PositionClosed"
                    for item in observations["position_sequence"]
                ),
            )
            engine.dispose()
        return observations, orders, fills, signed_position, native_completed

    def test_spot_daily_resampling_is_utc_complete_and_causal(self) -> None:
        observations, orders, fills, signed, native_completed = self._run(
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        )
        daily = observations["daily_signal_bars"]
        self.assertEqual(len(daily), 22)
        self.assertTrue(all(item["interval_end_exclusive_ns"] % DAY_NS == 0 for item in daily))
        self.assertEqual(daily[-1]["interval_end_exclusive_ns"], 22 * DAY_NS)
        self.assertEqual(len(observations["signals"]), 2)
        self.assertEqual([item["target"] for item in observations["signals"]], ["LONG", "FLAT"])
        self.assertEqual(len(orders), 2)
        self.assertEqual(len(fills), 2)
        self.assertEqual(signed, Decimal(0))
        self.assertEqual(native_completed.completed_trade_count, 1)
        self.assertEqual(native_completed.terminal_open_position_count, 0)
        self.assertEqual(native_completed.units[0].source_kind, "CACHE_CLOSED_POSITION")
        for fill in fills:
            submitted = next(
                item
                for item in observations["submitted_intents"]
                if item["client_order_id"] == str(fill.client_order_id)
            )
            self.assertGreater(int(fill.ts_event), submitted["signal_bar_available_at_ns"])

    def test_perpetual_reversal_closes_flat_then_reopens_separately(self) -> None:
        observations, orders, fills, signed, native_completed = self._run(
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        )
        self.assertEqual([item["target"] for item in observations["signals"]], ["LONG", "SHORT"])
        self.assertEqual(len(orders), 3)
        self.assertEqual(len(fills), 3)
        self.assertEqual(signed, Decimal("-0.100"))
        self.assertEqual(native_completed.completed_trade_count, 1)
        self.assertEqual(native_completed.terminal_open_position_count, 1)
        self.assertEqual(native_completed.units[0].source_kind, "CACHE_POSITION_SNAPSHOT")
        sequence = observations["reversal_sequence"]
        self.assertEqual(
            [item["event"] for item in sequence],
            ["CLOSE_TO_FLAT_SUBMITTED", "NATIVE_FLAT_CONFIRMED", "SEPARATE_REOPEN_SUBMITTED"],
        )
        self.assertNotEqual(sequence[0]["client_order_id"], sequence[2]["client_order_id"])
        self.assertLess(sequence[0]["observed_at_ns"], sequence[2]["observed_at_ns"])
        submitted = observations["submitted_intents"]
        self.assertEqual(Decimal(submitted[1]["quantity"]), Decimal("0.100"))
        self.assertEqual(Decimal(submitted[1]["position_before"]), Decimal("0.100"))
        self.assertEqual(Decimal(submitted[2]["position_before"]), Decimal(0))

    def test_deterministic_replay_matches_for_both_profiles(self) -> None:
        for profile in (
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        ):
            first = self._run(profile)
            second = self._run(profile)

            def semantic(value):
                observations, orders, fills, signed, native_completed = value
                return {
                    "signals": observations["signals"],
                    "submitted": [
                        {
                            key: item[key]
                            for key in (
                                "side",
                                "quantity",
                                "reason",
                                "signal_bar_interval_start_ns",
                                "signal_bar_interval_end_exclusive_ns",
                                "effective_insert_at_ns",
                                "position_before",
                            )
                        }
                        for item in observations["submitted_intents"]
                    ],
                    "orders": [
                        (
                            "BUY" if order.is_buy else "SELL",
                            str(order.quantity),
                            str(order.status),
                            int(order.ts_init),
                        )
                        for order in orders
                    ],
                    "fills": [
                        (
                            str(fill.order_side),
                            str(fill.last_qty),
                            str(fill.last_px),
                            str(fill.commission),
                            int(fill.ts_event),
                        )
                        for fill in fills
                    ],
                    "terminal_signed_position": str(signed),
                    "native_completed_positions": native_completed.semantic_sequence_sha256,
                }

            self.assertEqual(semantic(first), semantic(second))


if __name__ == "__main__":
    unittest.main()
