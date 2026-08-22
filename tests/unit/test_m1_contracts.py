from __future__ import annotations

import json
import unittest

from crypto_lab.config import ConfigError
from crypto_lab.config import MarketProfile
from crypto_lab.strategies import StrategySpec
from tests.m1_helpers import make_strategy_spec


class M1StrictContractTests(unittest.TestCase):
    def test_strategy_spec_rejects_unknown_and_missing_material_fields(self) -> None:
        spec = make_strategy_spec(
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            "BTCUSDT.BINANCE",
        )
        raw = json.loads(spec.to_json_bytes())
        raw["unknown_material_default"] = True
        with self.assertRaises(ConfigError):
            StrategySpec.from_json_bytes(json.dumps(raw).encode("utf-8"))

        raw.pop("unknown_material_default")
        raw.pop("sizing_rule")
        with self.assertRaises(ConfigError):
            StrategySpec.from_json_bytes(json.dumps(raw).encode("utf-8"))

    def test_strategy_spec_locks_market_order_tif_and_terminal_policy(self) -> None:
        spec = make_strategy_spec(
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            "BTCUSDT.BINANCE",
        )
        raw = json.loads(spec.to_json_bytes())
        raw["market_order_time_in_force"] = "IOC"
        with self.assertRaises(ConfigError):
            StrategySpec.from_json_bytes(json.dumps(raw).encode("utf-8"))

        raw["market_order_time_in_force"] = "GTC"
        raw["terminal_behavior"] = "SYNTHETIC_CLOSE"
        with self.assertRaises(ConfigError):
            StrategySpec.from_json_bytes(json.dumps(raw).encode("utf-8"))

    def test_strategy_spec_identity_ignores_only_human_strategy_id(self) -> None:
        first = make_strategy_spec(
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            "BTCUSDT.BINANCE",
        )
        raw = json.loads(first.to_json_bytes())
        raw["strategy_id"] = "another-human-label"
        relabeled = StrategySpec.from_json_bytes(json.dumps(raw).encode("utf-8"))
        self.assertEqual(first.strategy_spec_id, relabeled.strategy_spec_id)

        raw["parameters"]["fixture"] = "MATERIAL_CHANGE"
        changed = StrategySpec.from_json_bytes(json.dumps(raw).encode("utf-8"))
        self.assertNotEqual(first.strategy_spec_id, changed.strategy_spec_id)


if __name__ == "__main__":
    unittest.main()
