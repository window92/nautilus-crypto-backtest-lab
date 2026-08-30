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
from crypto_lab.nautilus_config import add_venue_from_config
from crypto_lab.nautilus_config import to_nautilus_engine_config
from crypto_lab.strategies import BUY_AND_HOLD_REGISTRATION_ID
from crypto_lab.strategies import TSMOM_FULL_REGISTRATION_ID
from crypto_lab.strategies import TSMOM_VOL20_REGISTRATION_ID
from crypto_lab.strategies import create_registered_strategy
from crypto_lab.strategies import locked_buy_and_hold_strategy_spec
from crypto_lab.strategies import locked_weekly_tsmom_strategy_spec
from crypto_lab.strategies import resolve_registered_strategy_identity
from tests.m1_helpers import PERP_ID
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import complete_perpetual_roles
from tests.m1_helpers import make_request
from tests.m1_helpers import plan


MINUTE_NS = 60_000_000_000
DAY_NS = 86_400_000_000_000


def _source() -> SourceRevision:
    return SourceRevision(
        repository="https://github.com/window92/nautilus-crypto-backtest-lab.git",
        branch_ref="main",
        git_commit="1" * 40,
        git_tree="2" * 40,
        clean_worktree=True,
        captured_at_utc=datetime(2026, 8, 24, tzinfo=UTC),
    )


def _instrument(profile: MarketProfile):
    common = dict(
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        min_price=Price.from_str("0.01"),
        max_price=Price.from_str("1000000.00"),
        ts_event=0,
        ts_init=0,
        multiplier=Quantity.from_str("1"),
        margin_init=Decimal("1"),
        margin_maint=Decimal("1"),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
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


def _bars(instrument_id: InstrumentId, *, days: int = 42) -> tuple[Bar, ...]:
    bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
    volume = "100.00000" if instrument_id == SPOT_ID else "100.000"
    result: list[Bar] = []
    for minute in range(1, days * 1_440 + 2):
        day = (minute - 1) // 1_440
        close = Decimal(100 + day) if day < 32 else Decimal(50)
        price = f"{close:.2f}"
        result.append(
            Bar(
                bar_type,
                Price.from_str(price),
                Price.from_str(f"{close + Decimal('0.10'):.2f}"),
                Price.from_str(f"{close - Decimal('0.10'):.2f}"),
                Price.from_str(price),
                Quantity.from_str(volume),
                minute * MINUTE_NS,
                minute * MINUTE_NS,
            ),
        )
    return tuple(result)


class WeeklyTsmomStrategyIntegrationTests(unittest.TestCase):
    def _run(self, profile: MarketProfile, registration_id: str):
        instrument_id = SPOT_ID if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else PERP_ID
        bars = _bars(instrument_id)
        seed = bars[:3] if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else complete_perpetual_roles(bars[:3])
        with tempfile.TemporaryDirectory() as temporary:
            request = make_request(
                Path(temporary),
                run_id="weekly-tsmom-integration",
                profile=profile,
                data=seed,
                plan=plan({}),
                scoring_start_ns=0,
                scoring_end_ns=3 * MINUTE_NS,
            )
            instrument = _instrument(profile)
            engine = BacktestEngine(to_nautilus_engine_config(request.lab_run_config.nautilus_engine_config))
            add_venue_from_config(engine, request.lab_run_config.nautilus_venue_config)
            engine.add_instrument(instrument)
            if registration_id == BUY_AND_HOLD_REGISTRATION_ID:
                spec = locked_buy_and_hold_strategy_spec(profile, "BUY_AND_HOLD_1X_V1_TEST")
                scoring_start = 32 * DAY_NS
                scoring_end = 34 * DAY_NS
            else:
                spec = locked_weekly_tsmom_strategy_spec(registration_id, profile)
                scoring_start = 32 * DAY_NS
                scoring_end = 41 * DAY_NS
            identity = resolve_registered_strategy_identity(
                registration_id,
                strategy_spec=spec,
                source_revision=_source(),
            )
            strategy = create_registered_strategy(
                identity,
                strategy_spec=spec,
                source_revision=_source(),
                configuration={
                    "instrument_id": instrument_id,
                    "bar_type": BarType.from_str(spec.signal_bar_types[0]),
                    "execution_bar_type": bars[0].bar_type,
                    "profile": profile,
                    "scoring_start_ns": scoring_start,
                    "scoring_end_exclusive_ns": scoring_end,
                    "effective_insert_latency_ns": MINUTE_NS,
                    "size_precision": instrument.size_precision,
                    "min_quantity": None,
                    "max_quantity": None,
                    "size_increment": instrument.size_increment.as_decimal(),
                    "initial_capital_amount": Decimal("1000"),
                    "initial_capital_currency": "USDT",
                },
            )
            engine.add_strategy(strategy)
            data = (
                list(bars)
                if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
                else [
                    item
                    for bar in bars
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
            )
            engine.add_data(data)
            engine.run(start=0)
            orders = tuple(engine.cache.orders(instrument_id=instrument_id))
            fills = tuple(
                event
                for order in orders
                for event in order.events()
                if type(event).__name__ == "OrderFilled"
            )
            signed = sum(
                (Decimal(str(item.signed_qty)) for item in engine.cache.positions_open(instrument_id=instrument_id)),
                Decimal(0),
            )
            observations = strategy.observations
            engine.dispose()
        return observations, orders, fills, signed

    def test_weekly_full_candidate_is_causal_long_flat_or_separate_reverse(self) -> None:
        for profile in (
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        ):
            observations, orders, fills, signed = self._run(profile, TSMOM_FULL_REGISTRATION_ID)
            self.assertEqual(len(observations["signals"]), 2)
            self.assertEqual([item["target"] for item in observations["signals"]], ["LONG", "FLAT" if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else "SHORT"])
            self.assertEqual(len(orders), 2 if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else 3)
            self.assertEqual(len(fills), len(orders))
            self.assertEqual(observations["guard_failures"], [])
            self.assertEqual(observations["daily_signal_bars"][0]["completed_close_count"], 1)
            self.assertGreaterEqual(signed, 0) if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else self.assertLess(signed, 0)
            for fill in fills:
                intent = next(
                    item
                    for item in observations["submitted_intents"]
                    if item["client_order_id"] == str(fill.client_order_id)
                )
                self.assertGreaterEqual(int(fill.ts_event), int(intent["effective_insert_at_ns"]))
            if profile is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
                self.assertEqual(
                    [item["event"] for item in observations["reversal_sequence"]],
                    ["CLOSE_TO_FLAT_SUBMITTED", "NATIVE_FLAT_CONFIRMED", "SEPARATE_REOPEN_SUBMITTED"],
                )

    def test_registered_benchmark_enters_once_and_holds(self) -> None:
        observations, orders, fills, signed = self._run(
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            BUY_AND_HOLD_REGISTRATION_ID,
        )
        self.assertEqual(len(observations["benchmark_entries"]), 1)
        self.assertEqual(len(orders), 1)
        self.assertEqual(len(fills), 1)
        self.assertGreater(signed, 0)
        self.assertEqual(observations["submitted_intents"][0]["reason"], "BUY_AND_HOLD_1X_INITIAL_ENTRY")

    def test_weekly_vol20_candidate_uses_locked_fraction_without_leverage(self) -> None:
        observations, orders, fills, _ = self._run(
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            TSMOM_VOL20_REGISTRATION_ID,
        )
        fractions = tuple(
            Decimal(item["target_fraction"])
            for item in observations["signals"]
            if item["target"] != "FLAT"
        )
        self.assertTrue(fractions)
        self.assertTrue(all(Decimal(0) < value <= Decimal(1) for value in fractions))
        self.assertTrue(any(value < Decimal(1) for value in fractions))
        self.assertEqual(len(fills), len(orders))


if __name__ == "__main__":
    unittest.main()
