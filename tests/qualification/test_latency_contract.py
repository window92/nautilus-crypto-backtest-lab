from __future__ import annotations

import unittest

from crypto_lab.config import LabRunConfig
from crypto_lab.qualification import qualify_latency_contract
from tests.helpers import encode_config
from tests.helpers import load_spot_config_dict


class LatencyContractQualificationTests(unittest.TestCase):
    def test_pinned_backtest_venue_accepts_exact_constant_latency_contract(self) -> None:
        config = LabRunConfig.from_json_bytes(encode_config(load_spot_config_dict()))
        evidence = qualify_latency_contract(config)

        self.assertEqual(evidence["status"], "VERIFIED")
        self.assertTrue(evidence["venue_accepted"])
        self.assertEqual(
            evidence["actual_class_path"],
            "nautilus_trader.backtest.models.latency:LatencyModel",
        )
        self.assertEqual(evidence["base_latency_nanos"], 60_000_000_000)
        self.assertEqual(evidence["configured_insert_latency_nanos"], 0)
        self.assertEqual(evidence["configured_update_latency_nanos"], 0)
        self.assertEqual(evidence["configured_cancel_latency_nanos"], 0)
        self.assertEqual(evidence["effective_insert_latency_nanos"], 60_000_000_000)
        self.assertEqual(evidence["effective_update_latency_nanos"], 60_000_000_000)
        self.assertEqual(evidence["effective_cancel_latency_nanos"], 60_000_000_000)


if __name__ == "__main__":
    unittest.main()

