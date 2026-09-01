from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from crypto_lab.config import MarketProfile
from crypto_lab.data import FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY
from crypto_lab.data import DatasetRawInventory
from crypto_lab.data import M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION
from crypto_lab.m3 import PERPETUAL_QUALIFICATION_RELEASE_ID
from crypto_lab.m3 import SPOT_QUALIFICATION_RELEASE_ID
from crypto_lab.m3 import qualification_dataset_release


ROOT = Path(__file__).resolve().parents[2]


class M3ProfileGoldenTests(unittest.TestCase):
    def test_spot_repaired_release_and_native_market_limits_are_exact(self) -> None:
        release = qualification_dataset_release(
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            repository_root=ROOT,
        )
        resolved = release.resolve_runtime_data(__import__("pathlib").Path("data"))
        self.assertEqual(release.dataset_release_id, SPOT_QUALIFICATION_RELEASE_ID)
        self.assertEqual(release.schema_version, 2)
        self.assertEqual(
            release.normalizer_version,
            M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION,
        )
        self.assertIsInstance(release.raw_inventory, DatasetRawInventory)
        self.assertEqual(release.raw_inventory.raw_object_count, 5)
        self.assertEqual(resolved.instrument.min_quantity.as_decimal(), Decimal("0.00001"))
        self.assertEqual(resolved.instrument.max_quantity.as_decimal(), Decimal("107.65653"))
        self.assertEqual(resolved.instrument.size_increment.as_decimal(), Decimal("0.00001"))
        self.assertEqual(resolved.instrument.size_precision, 8)
        self.assertEqual(Decimal(str(resolved.instrument.taker_fee)), Decimal("0.001"))
        self.assertEqual(release.mark_data_identity, "NOT_APPLICABLE")
        self.assertEqual(release.funding_data_identity, "NOT_APPLICABLE")

    def test_perpetual_repaired_release_funding_and_native_limits_are_exact(self) -> None:
        release = qualification_dataset_release(
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            repository_root=ROOT,
        )
        resolved = release.resolve_runtime_data(__import__("pathlib").Path("data"))
        self.assertEqual(release.dataset_release_id, PERPETUAL_QUALIFICATION_RELEASE_ID)
        self.assertEqual(release.schema_version, 2)
        self.assertEqual(
            release.normalizer_version,
            M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION,
        )
        self.assertIsInstance(release.raw_inventory, DatasetRawInventory)
        self.assertEqual(release.raw_inventory.raw_object_count, 7)
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
