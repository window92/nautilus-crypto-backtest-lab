from __future__ import annotations

import unittest
from decimal import Decimal

from crypto_lab.config import MarketProfile
from crypto_lab.data import FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY
from crypto_lab.m3 import PERPETUAL_QUALIFICATION_RELEASE_ID
from crypto_lab.m3 import SPOT_QUALIFICATION_RELEASE_ID
from crypto_lab.m3 import qualification_dataset_release


class M3ProfileGoldenTests(unittest.TestCase):
    def test_spot_repaired_release_and_native_market_limits_are_exact(self) -> None:
        release = qualification_dataset_release(MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY)
        resolved = release.resolve_runtime_data(__import__("pathlib").Path("data"))
        self.assertEqual(release.dataset_release_id, SPOT_QUALIFICATION_RELEASE_ID)
        self.assertEqual(str(resolved.instrument.min_quantity), "0.00001")
        self.assertEqual(str(resolved.instrument.max_quantity), "107.65653")
        self.assertEqual(str(resolved.instrument.size_increment), "0.00001")
        self.assertEqual(Decimal(str(resolved.instrument.taker_fee)), Decimal("0.001"))
        self.assertEqual(release.mark_data_identity, "NOT_APPLICABLE")
        self.assertEqual(release.funding_data_identity, "NOT_APPLICABLE")

    def test_perpetual_repaired_release_funding_and_native_limits_are_exact(self) -> None:
        release = qualification_dataset_release(
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        )
        resolved = release.resolve_runtime_data(__import__("pathlib").Path("data"))
        self.assertEqual(release.dataset_release_id, PERPETUAL_QUALIFICATION_RELEASE_ID)
        self.assertEqual(str(resolved.instrument.min_quantity), "0.001")
        self.assertEqual(str(resolved.instrument.max_quantity), "120.000")
        self.assertEqual(str(resolved.instrument.size_increment), "0.001")
        self.assertEqual(resolved.funding_native_binding, FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY)
        self.assertEqual(resolved.funding_source_event_count, 1)
        self.assertEqual(resolved.funding_runtime_update_count, 2)
        funding = [item for item in resolved.data if type(item).__name__ == "FundingRateUpdate"]
        self.assertEqual(len(funding), 2)
        self.assertEqual({int(item.ts_event) for item in funding}, {1_735_718_400_000_000_000})
        self.assertEqual({str(item.rate) for item in funding}, {"0.00010000"})
