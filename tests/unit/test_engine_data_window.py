from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from nautilus_trader.model import FundingRateUpdate
from nautilus_trader.model import MarkPriceUpdate
from nautilus_trader.model import Price

from crypto_lab.config import MarketProfile
from crypto_lab.runner import run_lab
from crypto_lab.runner import select_engine_data_window
from tests.m1_helpers import PERP_ID
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import make_bars
from tests.m1_helpers import make_request
from tests.m1_helpers import plan


MINUTE = 60_000_000_000


class EngineDataWindowTests(unittest.TestCase):
    @staticmethod
    def _bars(instrument_id):
        return make_bars(
            instrument_id,
            tuple(
                (timestamp, "100.00", "101.00", "99.00", "100.00")
                for timestamp in (MINUTE, 2 * MINUTE, 3 * MINUTE, 4 * MINUTE)
            ),
        )

    def test_spot_release_larger_than_run_is_filtered_before_engine_boundary(self) -> None:
        selected, evidence = select_engine_data_window(
            self._bars(SPOT_ID),
            warmup_start_ns=MINUTE,
            scoring_end_exclusive_ns=3 * MINUTE,
        )
        self.assertEqual([int(item.ts_init) for item in selected], [2 * MINUTE, 3 * MINUTE])
        self.assertEqual(evidence["source_object_count"], 4)
        self.assertEqual(evidence["engine_object_count"], 2)
        self.assertEqual(evidence["dropped_before_warmup_count"], 1)
        self.assertEqual(evidence["dropped_after_scoring_count"], 1)
        self.assertFalse(evidence["engine_received_post_boundary_data"])
        self.assertEqual(evidence["latest_qualified_valuation_observation_ns"], 3 * MINUTE)

    def test_perpetual_boundary_keeps_final_mark_but_excludes_funding_at_end(self) -> None:
        bars = self._bars(PERP_ID)
        marks = tuple(
            MarkPriceUpdate(PERP_ID, Price.from_str("100.00"), timestamp, timestamp)
            for timestamp in (MINUTE, 2 * MINUTE, 3 * MINUTE, 4 * MINUTE)
        )
        funding = tuple(
            FundingRateUpdate(
                PERP_ID,
                Decimal("0.0001"),
                timestamp,
                timestamp,
                interval=480,
                next_funding_ns=timestamp,
            )
            for timestamp in (MINUTE, 2 * MINUTE, 3 * MINUTE, 4 * MINUTE)
        )
        selected, evidence = select_engine_data_window(
            tuple((*bars, *marks, *funding)),
            warmup_start_ns=MINUTE,
            scoring_end_exclusive_ns=3 * MINUTE,
        )
        self.assertEqual(
            [int(item.ts_init) for item in selected if isinstance(item, MarkPriceUpdate)],
            [2 * MINUTE, 3 * MINUTE],
        )
        self.assertEqual(
            [int(item.ts_init) for item in selected if isinstance(item, FundingRateUpdate)],
            [MINUTE, 2 * MINUTE],
        )
        self.assertEqual(evidence["selected_counts"]["Bar"], 2)
        self.assertEqual(evidence["selected_counts"]["MarkPriceUpdate"], 2)
        self.assertEqual(evidence["selected_counts"]["FundingRateUpdate"], 2)
        self.assertFalse(evidence["point_events_at_scoring_end_included"])
        self.assertTrue(evidence["completed_interval_observations_at_scoring_end_included"])

    def test_spot_engine_callbacks_never_receive_post_boundary_release_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_lab(
                make_request(
                    Path(temporary),
                    run_id="spot-engine-window-callbacks",
                    profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                    data=self._bars(SPOT_ID),
                    plan=plan({}),
                    scoring_start_ns=0,
                    scoring_end_ns=3 * MINUTE,
                ),
            )
        self.assertEqual(
            [row["ts_init"] for row in result.strategy_observations["bars"]],
            [MINUTE, 2 * MINUTE, 3 * MINUTE],
        )
        self.assertTrue(
            all(
                row["ts_init"] <= 3 * MINUTE
                for row in result.strategy_observations["bars"]
            ),
        )

    def test_perpetual_callbacks_exclude_boundary_funding_and_all_future_data(self) -> None:
        bars = self._bars(PERP_ID)
        data = []
        for bar in bars:
            timestamp = int(bar.ts_init)
            data.extend(
                (
                    MarkPriceUpdate(
                        PERP_ID,
                        Price.from_str("100.00"),
                        timestamp,
                        timestamp,
                    ),
                    FundingRateUpdate(
                        PERP_ID,
                        Decimal("0.0001"),
                        timestamp,
                        timestamp,
                        interval=480,
                        next_funding_ns=timestamp,
                    ),
                    bar,
                ),
            )
        with tempfile.TemporaryDirectory() as temporary:
            result = run_lab(
                make_request(
                    Path(temporary),
                    run_id="perpetual-engine-window-callbacks",
                    profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
                    data=tuple(data),
                    plan=plan({}),
                    scoring_start_ns=0,
                    scoring_end_ns=3 * MINUTE,
                ),
            )
        observations = result.strategy_observations
        self.assertEqual(
            [row["ts_init"] for row in observations["mark_price_updates"]],
            [MINUTE, 2 * MINUTE, 3 * MINUTE],
        )
        self.assertEqual(
            [row["ts_init"] for row in observations["funding_rate_updates"]],
            [MINUTE, 2 * MINUTE],
        )
        self.assertLessEqual(
            max(row["ts_init"] for row in observations["bars"]),
            3 * MINUTE,
        )


if __name__ == "__main__":
    unittest.main()
