from __future__ import annotations

import unittest

from nautilus_trader.backtest import BacktestDataConfig
from nautilus_trader.backtest import BacktestEngineConfig
from nautilus_trader.backtest import BacktestVenueConfig
from nautilus_trader.execution import MakerTakerFeeModel
from nautilus_trader.execution import StaticLatencyModel

from crypto_lab.config import LabRunConfig
from crypto_lab.config import SourceRevision
from crypto_lab.nautilus_config import to_nautilus_data_configs
from crypto_lab.nautilus_config import to_nautilus_engine_config
from crypto_lab.nautilus_config import to_nautilus_venue_config
from tests.helpers import encode_config
from tests.helpers import load_spot_config_dict
from tests.helpers import load_source_revision_dict


class M0DownstreamContractTests(unittest.TestCase):
    def test_m1_can_parse_and_bind_m0_config_without_defaults(self) -> None:
        config = LabRunConfig.from_json_bytes(encode_config(load_spot_config_dict()))
        venue = to_nautilus_venue_config(config.nautilus_venue_config)
        engine = to_nautilus_engine_config(config.nautilus_engine_config)
        data = to_nautilus_data_configs(config.nautilus_data_config)

        self.assertIsInstance(venue, BacktestVenueConfig)
        self.assertIsInstance(engine, BacktestEngineConfig)
        self.assertTrue(data)
        self.assertTrue(all(isinstance(item, BacktestDataConfig) for item in data))
        self.assertIsNone(engine.instance_id)
        self.assertEqual(venue.price_protection_points, 0)
        self.assertIsInstance(venue.fee_model, MakerTakerFeeModel)
        self.assertFalse(engine.portfolio.use_mark_prices)
        self.assertIsNone(engine.msgbus.types_filter)
        self.assertIsNone(engine.portfolio.snapshot_interval_ms)
        self.assertFalse(config.nautilus_engine_config.shutdown_on_error)
        self.assertEqual(config.nautilus_engine_config.delay_post_stop, 10)
        self.assertEqual(engine.timeout_connection, 60)
        self.assertEqual(engine.timeout_shutdown, 5)
        self.assertFalse(engine.logging.bypass_logging)
        self.assertTrue(engine.data_engine.time_bars_timestamp_on_close)
        self.assertTrue(engine.data_engine.time_bars_skip_first_non_full_bar)
        self.assertFalse(engine.data_engine.time_bars_build_with_no_updates)
        self.assertIsInstance(venue.latency_model, StaticLatencyModel)
        self.assertEqual(
            config.nautilus_venue_config.latency_model.latency_model_path,
            "nautilus_trader.execution:StaticLatencyModel",
        )

    def test_m1_can_parse_separate_source_revision_without_defaults(self) -> None:
        revision = SourceRevision.from_json_bytes(encode_config(load_source_revision_dict()))

        self.assertEqual(
            revision.repository,
            "https://github.com/window92/nautilus-crypto-backtest-lab.git",
        )
        self.assertEqual(revision.branch_ref, "refs/heads/main")
        self.assertEqual(revision.git_commit, "55338e31e99cfa30683858747faf16a4f5f46287")
        self.assertEqual(revision.git_tree, "0e1316c7c04235431f6001c66fa63bf59f3992dc")
        self.assertTrue(revision.clean_worktree)


if __name__ == "__main__":
    unittest.main()
