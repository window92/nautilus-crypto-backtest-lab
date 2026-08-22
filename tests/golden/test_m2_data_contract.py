from __future__ import annotations

import unittest
from datetime import UTC
from datetime import datetime
from pathlib import Path

from crypto_lab.config import MarketProfile
from crypto_lab.data import SourceRole
from crypto_lab.data import parse_kline_csv


FIXTURES = Path(__file__).parent / "fixtures/m2"


class M2GoldenDataContractTests(unittest.TestCase):
    def test_spot_timestamp_transition_is_explicit_and_bar_is_available_on_completion(self) -> None:
        pre = parse_kline_csv(
            FIXTURES.joinpath("spot-pre-transition.csv").read_bytes(),
            source_role=SourceRole.SPOT_EXECUTION_1M,
            instrument_id="BTCUSDT.BINANCE",
            market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            source_date=datetime(2024, 12, 31, tzinfo=UTC).date(),
        )
        post = parse_kline_csv(
            FIXTURES.joinpath("spot-post-transition.csv").read_bytes(),
            source_role=SourceRole.SPOT_EXECUTION_1M,
            instrument_id="BTCUSDT.BINANCE",
            market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            source_date=datetime(2025, 1, 1, tzinfo=UTC).date(),
        )
        bars = (*pre, *post)
        self.assertEqual(
            [bar.interval_start_ns for bar in bars],
            [
                1_735_689_480_000_000_000,
                1_735_689_540_000_000_000,
                1_735_689_600_000_000_000,
                1_735_689_660_000_000_000,
            ],
        )
        self.assertTrue(
            all(
                bar.available_at_ns == bar.interval_start_ns + 60_000_000_000
                and bar.interval_end_exclusive_ns == bar.available_at_ns
                for bar in bars
            ),
        )


if __name__ == "__main__":
    unittest.main()
