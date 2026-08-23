"""Regression coverage for current sparse Spot DatasetRelease preflight."""

from __future__ import annotations

import unittest
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from crypto_lab.config import FeeAssumption
from crypto_lab.config import MarketProfile
from crypto_lab.data import CompletenessResult
from crypto_lab.data import RoleCompleteness
from crypto_lab.data import SourceRole
from crypto_lab.data import TimeRange
from crypto_lab.hashing import canonical_sha256
from crypto_lab.runner import _preflight_data
from tests.m1_helpers import PERP_ID
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import make_bars
from tests.m1_helpers import make_instrument


MINUTE_NS = 60_000_000_000


class OwnerSmoke002SparsePreflightTests(unittest.TestCase):
    def _preflight(
        self,
        profile: MarketProfile,
        *,
        include_middle: bool = False,
        volume: str = "1000.000000",
    ) -> list[str]:
        instrument_id = SPOT_ID if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else PERP_ID
        rows = [
            (MINUTE_NS, "100.00", "101.00", "99.00", "100.00"),
            (3 * MINUTE_NS, "102.00", "103.00", "101.00", "102.00"),
        ]
        if include_middle:
            rows.insert(1, (2 * MINUTE_NS, "100.00", "100.00", "100.00", "100.00"))
        bars = make_bars(
            instrument_id,
            tuple(rows),
            # Historical source precision is preserved independently from the
            # current Instrument order-size precision.
            volume=volume,
        )
        start = datetime(1970, 1, 1, tzinfo=UTC)
        end = datetime.fromtimestamp(3 * 60, tz=UTC)
        role = (
            SourceRole.SPOT_EXECUTION_1M
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else SourceRole.USDM_PERPETUAL_EXECUTION_1M
        )
        completeness = CompletenessResult(
            status="PASS",
            no_repairs=True,
            role_results=(
                RoleCompleteness(
                    source_role=role,
                    expected_count=3,
                    actual_count=3,
                    start_inclusive_ns=0,
                    end_exclusive_ns=3 * MINUTE_NS,
                    status="PASS",
                ),
            ),
        )
        release_binding = {
            "data_window_identity": "a" * 64,
            "partition_geometry_identity": "b" * 64,
            "minute_coverage_identity": "c" * 64,
            "normalized_time_range": {
                "start_inclusive": "1970-01-01T00:00:00Z",
                "end_exclusive": "1970-01-01T00:03:00Z",
            },
        }
        inventory = {
            "schema": "nautilus-semantic-inventory-v1",
            "instruments": [],
            "execution_bars": [
                {
                    "ts_event": int(bar.ts_event),
                    "ts_init": int(bar.ts_init),
                }
                for bar in bars
            ],
            "mark_price_updates": [],
            "funding_rate_updates": [],
            "release_binding": release_binding,
        }
        release = SimpleNamespace(
            market_profile=profile,
            normalized_time_range=TimeRange(start_inclusive=start, end_exclusive=end),
            completeness_result=completeness,
            data_window_identity="a" * 64,
            partition_geometry_identity="b" * 64,
            minute_coverage_identity="c" * 64,
            catalog_identity=canonical_sha256(inventory),
            mark_data_identity="NOT_APPLICABLE",
            funding_data_identity="NOT_APPLICABLE",
        )
        run = SimpleNamespace(
            market_profile=profile,
            instrument_id=str(instrument_id),
            execution_bar_type=f"{instrument_id}-1-MINUTE-LAST-EXTERNAL",
            fee_assumption=FeeAssumption(
                maker_fee=Decimal("0.001"),
                taker_fee=Decimal("0.001"),
                explicit_zero_fee=False,
                reason="Regression fixture estimated fee",
                claim_class="ESTIMATED_FEE",
            ),
            scoring_end_exclusive=end,
        )
        request = SimpleNamespace(lab_run_config=run, dataset_release=release)
        resolved = SimpleNamespace(semantic_inventory=inventory)
        return _preflight_data(
            request,
            instrument=make_instrument(
                profile,
                maker_fee=Decimal("0.001"),
                taker_fee=Decimal("0.001"),
            ),
            data=bars,
            resolved=resolved,
        )

    def test_current_spot_release_accepts_sparse_bars_bound_to_complete_minute_dispositions(self) -> None:
        self.assertEqual(self._preflight(MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY), [])

    def test_perpetual_execution_grid_still_rejects_a_missing_minute(self) -> None:
        self.assertIn(
            "DATA_GAP",
            self._preflight(MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING),
        )

    def test_official_zero_volume_perpetual_bar_is_not_treated_as_synthetic(self) -> None:
        failures = self._preflight(
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            include_middle=True,
            volume="0.000",
        )
        self.assertNotIn("INSTRUMENT_METADATA_INVALID", failures)
        self.assertNotIn("DATA_GAP", failures)


if __name__ == "__main__":
    unittest.main()
