from __future__ import annotations

import unittest
from pathlib import Path

from crypto_lab.data import assert_official_active_raw_inventory
from crypto_lab.status import FailureCode


ROOT = Path(__file__).resolve().parents[2]


class OfficialActiveRawEqualityTests(unittest.TestCase):
    def test_exact_union_passes_and_mutations_fail_closed(self) -> None:
        declared = {f"{index:064x}" for index in range(5)}
        assert_official_active_raw_inventory(set(declared), set(declared))
        extra = set(declared) | {"f" * 64}
        with self.assertRaisesRegex(RuntimeError, FailureCode.DATASET_RAW_INVENTORY_MISMATCH.value):
            assert_official_active_raw_inventory(extra, declared)
        missing = set(declared)
        missing.pop()
        with self.assertRaisesRegex(RuntimeError, FailureCode.DATASET_RAW_INVENTORY_MISMATCH.value):
            assert_official_active_raw_inventory(missing, declared)
        with self.assertRaisesRegex(RuntimeError, FailureCode.DATASET_RAW_INVENTORY_MISMATCH.value):
            assert_official_active_raw_inventory(
                set(declared),
                set(declared),
                extra_checksums=[("a" * 64, "b" * 64)],
            )

    def test_inactive_inventory_documents_the_twelve_retry_009_leftovers(self) -> None:
        path = (
            ROOT
            / "evidence/audit/adversarial-remediation-002/inactive-raw-objects-retry-009.json"
        )
        self.assertTrue(path.is_file())
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "inactive-raw-objects-v1")
        self.assertEqual(payload["official_active_raw_object_count"], 2231)
        self.assertEqual(payload["inactive_object_count"], 12)
        self.assertEqual(len(payload["objects"]), 12)
        self.assertTrue(all(item["official_active"] is False for item in payload["objects"]))
        self.assertTrue(all(item["raw_object_sha256"] for item in payload["objects"]))
        self.assertTrue(all(item["roles"] for item in payload["objects"]))
        self.assertTrue(all(item["locators"] for item in payload["objects"]))

    def test_wrong_hash_locator_role_profile_and_instrument_fail_closed(self) -> None:
        declared = {"a" * 64, "b" * 64}
        with self.assertRaisesRegex(RuntimeError, FailureCode.DATASET_RAW_INVENTORY_MISMATCH.value):
            assert_official_active_raw_inventory({"c" * 64}, declared)
        with self.assertRaisesRegex(RuntimeError, FailureCode.DATASET_RAW_INVENTORY_MISMATCH.value):
            assert_official_active_raw_inventory(
                declared,
                declared,
                extra_checksums=[("a" * 64, "d" * 64)],
            )
        from dataclasses import replace
        from crypto_lab.config import MarketProfile
        from crypto_lab.data import DatasetRawInventory
        from tests.adversarial.test_r2_full_raw_inventory import make_release

        release, _blobs = make_release()
        inventory = release.raw_inventory
        assert isinstance(inventory, DatasetRawInventory)
        first = inventory.raw_objects[0]
        with self.assertRaises(Exception):
            DatasetRawInventory.create(
                market_profile=inventory.market_profile,
                instrument_id=inventory.instrument_id,
                data_window_identity=inventory.data_window_identity,
                source_reconciliation_identity=inventory.source_reconciliation_identity,
                raw_object_count=inventory.raw_object_count,
                raw_objects=(
                    replace(first, raw_object_sha256="e" * 64),
                    *inventory.raw_objects[1:],
                ),
            )
        with self.assertRaises(Exception):
            DatasetRawInventory.create(
                market_profile=inventory.market_profile,
                instrument_id="ETHUSDT.BINANCE",
                data_window_identity=inventory.data_window_identity,
                source_reconciliation_identity=inventory.source_reconciliation_identity,
                raw_object_count=inventory.raw_object_count,
                raw_objects=inventory.raw_objects,
            )
        with self.assertRaises(Exception):
            replace(
                first,
                origins=(
                    replace(
                        first.origins[0],
                        exact_locator="https://attacker.invalid/object",
                    ),
                ),
            )
        with self.assertRaises(Exception):
            DatasetRawInventory.create(
                market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
                instrument_id=inventory.instrument_id,
                data_window_identity=inventory.data_window_identity,
                source_reconciliation_identity=inventory.source_reconciliation_identity,
                raw_object_count=inventory.raw_object_count,
                raw_objects=inventory.raw_objects,
            )


if __name__ == "__main__":
    unittest.main()
