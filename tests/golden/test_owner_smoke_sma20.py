"""Independent golden expectations for OWNER_OPERATIONAL_SMOKE_001."""

from __future__ import annotations

import unittest
from decimal import Decimal

from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity

from crypto_lab.config import MarketProfile
from crypto_lab.strategies.daily_sma_trend import BtcusdtDailyPriceVsSma20Trend
from crypto_lab.strategies.daily_sma_trend import TargetState
from crypto_lab.strategies.daily_sma_trend import classify_target
from crypto_lab.strategies.daily_sma_trend import DAY_NS
from crypto_lab.strategies.daily_sma_trend import validate_completed_utc_daily_bar


class OwnerSmokeSma20GoldenTests(unittest.TestCase):
    def test_native_sma20_matches_independent_arithmetic_mean(self) -> None:
        closes = tuple(Decimal(value) for value in range(1, 21))
        independently_calculated = sum(closes, Decimal(0)) / Decimal(20)
        self.assertEqual(independently_calculated, Decimal("10.5"))

        indicator = SimpleMovingAverage(20)
        for index, close in enumerate(closes, start=1):
            indicator.update_raw(float(close))
            self.assertEqual(indicator.count, index)
            self.assertEqual(indicator.initialized, index == 20)

        self.assertEqual(Decimal(str(indicator.value)), independently_calculated)

        indicator.update_raw(21.0)
        self.assertEqual(indicator.count, 20)
        self.assertTrue(indicator.initialized)

    def test_first_signal_requires_exactly_twenty_completed_daily_closes(self) -> None:
        indicator = SimpleMovingAverage(20)
        for close in range(1, 20):
            indicator.update_raw(float(close))
            self.assertFalse(indicator.initialized)
        indicator.update_raw(20.0)
        self.assertTrue(indicator.initialized)
        self.assertEqual(indicator.count, 20)

    def test_spot_equality_is_flat_and_never_short(self) -> None:
        profile = MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        self.assertIs(classify_target(Decimal("100"), Decimal("100"), profile), TargetState.FLAT)
        self.assertIs(classify_target(Decimal("101"), Decimal("100"), profile), TargetState.LONG)
        self.assertIs(classify_target(Decimal("99"), Decimal("100"), profile), TargetState.FLAT)

    def test_perpetual_equality_is_flat(self) -> None:
        profile = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
        self.assertIs(classify_target(Decimal("100"), Decimal("100"), profile), TargetState.FLAT)
        self.assertIs(classify_target(Decimal("101"), Decimal("100"), profile), TargetState.LONG)
        self.assertIs(classify_target(Decimal("99"), Decimal("100"), profile), TargetState.SHORT)

    def test_spot_short_target_is_rejected_before_any_submission(self) -> None:
        class FixedClock:
            @staticmethod
            def timestamp_ns() -> int:
                return DAY_NS

        class FlatSpotStrategy(BtcusdtDailyPriceVsSma20Trend):
            def _signed_position(self) -> Decimal:
                return Decimal(0)

            @property
            def clock(self) -> FixedClock:
                return FixedClock()

        strategy = FlatSpotStrategy()
        strategy._profile = MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        strategy._quantity = "0.10000"
        signal_bar = Bar(
            BarType.from_str("BTCUSDT.BINANCE-1-DAY-LAST-INTERNAL"),
            Price.from_str("100.00"),
            Price.from_str("101.00"),
            Price.from_str("99.00"),
            Price.from_str("100.00"),
            Quantity.from_str("1.00000"),
            DAY_NS,
            DAY_NS,
        )
        strategy._act_on_target(TargetState.SHORT, signal_bar)
        self.assertEqual(strategy.observations["submitted_intents"], [])
        self.assertEqual(
            strategy.observations["guard_failures"][0]["failure_code"],
            "SPOT_SHORT_OR_BORROW_DETECTED",
        )

    def test_incomplete_or_non_utc_daily_bar_is_rejected(self) -> None:
        bar_type = BarType.from_str("BTCUSDT.BINANCE-1-DAY-LAST-INTERNAL")
        incomplete = Bar(
            bar_type,
            Price.from_str("100.00"),
            Price.from_str("101.00"),
            Price.from_str("99.00"),
            Price.from_str("100.00"),
            Quantity.from_str("1.00000"),
            DAY_NS - 1,
            DAY_NS - 1,
        )
        with self.assertRaisesRegex(ValueError, "TIMEFRAME_AGGREGATION_UNRESOLVED"):
            validate_completed_utc_daily_bar(incomplete)


if __name__ == "__main__":
    unittest.main()
