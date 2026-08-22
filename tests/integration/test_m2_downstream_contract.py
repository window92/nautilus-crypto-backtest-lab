from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import crypto_lab
from crypto_lab import DatasetRelease
from crypto_lab.data import build_nautilus_catalog
from tests.m2_helpers import perp_execution_bars
from tests.m2_helpers import perp_mark_bars
from tests.m2_helpers import perp_metadata
from tests.unit.test_m2_release_contract import build_perp


class M2DownstreamContractTests(unittest.TestCase):
    def test_m3_can_parse_dataset_release_without_defaults_or_internal_parser_exports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            release = build_perp(Path(temporary) / "catalog")
            parsed = DatasetRelease.from_json_bytes(release.to_json_bytes())
            self.assertEqual(parsed.dataset_release_id, release.dataset_release_id)
            self.assertEqual(parsed.execution_bar_interval, "1m")
            self.assertEqual(parsed.normalized_time_range, release.normalized_time_range)
            self.assertEqual(parsed.available_signal_bar_intervals, (release.normalized_time_range,))
            self.assertEqual(parsed.completeness_result.status, "PASS")
            self.assertFalse(hasattr(crypto_lab, "RawObjectStore"))
            self.assertFalse(hasattr(crypto_lab, "parse_kline_csv"))

    def test_release_rows_are_already_native_catalog_compatible_for_m3(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = build_nautilus_catalog(
                Path(temporary) / "catalog",
                metadata=perp_metadata(),
                execution_bars=perp_execution_bars(),
                mark_bars=perp_mark_bars(),
            )
            self.assertEqual(len(result.execution_bars), 4)
            self.assertEqual(len(result.mark_updates), 4)
            self.assertTrue(
                all(item.ts_init == item.ts_event for item in result.execution_bars),
            )
            self.assertEqual(
                [int(item.ts_init) for item in result.execution_bars],
                [
                    1_735_689_660_000_000_000,
                    1_735_689_720_000_000_000,
                    1_735_689_780_000_000_000,
                    1_735_689_840_000_000_000,
                ],
            )


if __name__ == "__main__":
    unittest.main()
