from __future__ import annotations

import copy
import unittest

from nautilus_trader.model import MarkPriceUpdate
from nautilus_trader.model import Price

from crypto_lab.checker import _validate_material_valuation_grid
from tests.m1_helpers import PERP_ID
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import make_bars


HOUR_NS = 3_600_000_000_000
DAY_NS = 24 * HOUR_NS


class MaterialValuationGridAdversarialTests(unittest.TestCase):
    def _perpetual_fixture(self):
        bar = make_bars(
            PERP_ID,
            ((DAY_NS, "100.00", "101.00", "99.00", "100.50"),),
        )[0]
        marks = tuple(
            MarkPriceUpdate(
                PERP_ID,
                Price.from_str(value),
                timestamp,
                timestamp,
            )
            for timestamp, value in (
                (0, "99.00"),
                (8 * HOUR_NS, "100.00"),
                (16 * HOUR_NS, "101.00"),
                (DAY_NS, "102.00"),
            )
        )
        observations = {
            "valuation_bars": [
                {
                    "bar_type": str(bar.bar_type),
                    "ts_event": int(bar.ts_event),
                    "ts_init": int(bar.ts_init),
                    "callback_clock_ns": int(bar.ts_init),
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": str(bar.volume),
                },
            ],
            "mark_price_updates": [
                {
                    "instrument_id": str(item.instrument_id),
                    "value": str(item.value),
                    "ts_event": int(item.ts_event),
                    "ts_init": int(item.ts_init),
                }
                for item in marks
            ],
        }
        return (bar, *marks), observations

    def _validate(self, resolved_data, observations, *, perpetual=True):
        instrument_id = str(PERP_ID if perpetual else SPOT_ID)
        return _validate_material_valuation_grid(
            resolved_data=tuple(resolved_data),
            observations=observations,
            instrument_id=instrument_id,
            execution_bar_type=f"{instrument_id}-1-MINUTE-LAST-EXTERNAL",
            scoring_start_ns=0,
            scoring_end_exclusive_ns=DAY_NS,
            perpetual=perpetual,
        )

    def test_exact_dataset_projection_passes(self) -> None:
        resolved, observations = self._perpetual_fixture()
        bar_ok, mark_ok, detail = self._validate(resolved, observations)
        self.assertTrue(bar_ok)
        self.assertTrue(mark_ok)
        self.assertEqual(detail["expected_daily_bar_count"], 1)
        self.assertEqual(detail["expected_eight_hour_mark_count"], 4)

    def test_jointly_rehashed_mark_and_snapshot_cannot_replace_dataset_mark(self) -> None:
        resolved, observations = self._perpetual_fixture()
        tampered = copy.deepcopy(observations)
        tampered["mark_price_updates"][2]["value"] = "999.00"
        _bar_ok, mark_ok, _detail = self._validate(resolved, tampered)
        self.assertFalse(mark_ok)

    def test_missing_duplicate_wrong_instrument_and_wrong_bar_each_fail_exact_role(self) -> None:
        resolved, observations = self._perpetual_fixture()
        cases = []
        missing = copy.deepcopy(observations)
        missing["mark_price_updates"].pop()
        cases.append((missing, True, False))
        duplicate = copy.deepcopy(observations)
        duplicate["mark_price_updates"].append(
            copy.deepcopy(duplicate["mark_price_updates"][-1]),
        )
        cases.append((duplicate, True, False))
        wrong_instrument = copy.deepcopy(observations)
        wrong_instrument["mark_price_updates"][0]["instrument_id"] = str(SPOT_ID)
        cases.append((wrong_instrument, True, False))
        wrong_bar = copy.deepcopy(observations)
        wrong_bar["valuation_bars"][0]["close"] = "999.00"
        cases.append((wrong_bar, False, True))
        for tampered, expected_bar, expected_mark in cases:
            with self.subTest(tampered=tampered):
                bar_ok, mark_ok, _detail = self._validate(resolved, tampered)
                self.assertEqual(bar_ok, expected_bar)
                self.assertEqual(mark_ok, expected_mark)

    def test_spot_forbids_material_mark_rows(self) -> None:
        bar = make_bars(
            SPOT_ID,
            ((DAY_NS, "100.00", "101.00", "99.00", "100.50"),),
        )[0]
        observations = {
            "valuation_bars": [
                {
                    "bar_type": str(bar.bar_type),
                    "ts_event": DAY_NS,
                    "ts_init": DAY_NS,
                    "callback_clock_ns": DAY_NS,
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": str(bar.volume),
                },
            ],
            "mark_price_updates": [],
        }
        bar_ok, mark_ok, _detail = self._validate((bar,), observations, perpetual=False)
        self.assertTrue(bar_ok)
        self.assertTrue(mark_ok)
        observations["mark_price_updates"] = [
            {
                "instrument_id": str(SPOT_ID),
                "value": "100.50",
                "ts_event": DAY_NS,
                "ts_init": DAY_NS,
            },
        ]
        _bar_ok, mark_ok, _detail = self._validate((bar,), observations, perpetual=False)
        self.assertFalse(mark_ok)


if __name__ == "__main__":
    unittest.main()
