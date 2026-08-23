from __future__ import annotations

import unittest

from crypto_lab.m1_qualification import qualify_sparse_real_bar_behavior


class SparseOfficialMarketQualificationTests(unittest.TestCase):
    def test_sparse_market_never_creates_a_price_or_fill_inside_no_trade_interval(self) -> None:
        result = qualify_sparse_real_bar_behavior()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(all(result["conditions"].values()))
        self.assertEqual(
            result["input_bar_timestamps_ns"],
            [60_000_000_000, 180_000_000_000, 240_000_000_000],
        )
        self.assertEqual(result["fill"]["ts_event"], 180_000_000_000)
        self.assertEqual(result["fill"]["last_px"], "300.01")
        daily = result["daily_resampling"]
        self.assertEqual(daily["status"], "PASS")
        self.assertNotIn(daily["missing_source_minute_ns"], daily["input_real_bar_timestamps_ns"])
        self.assertEqual(
            daily["observed_completed_daily_bars"],
            [daily["expected_completed_daily_bar"]],
        )
        self.assertTrue(daily["nautilus_internal_aggregation_used"])
        self.assertFalse(daily["synthetic_source_bar_added"])
        self.assertTrue(result["nautilus_is_only_execution_engine"])
        self.assertFalse(result["project_matching_or_fill_engine"])


if __name__ == "__main__":
    unittest.main()
