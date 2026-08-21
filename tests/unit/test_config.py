from __future__ import annotations

import copy
import unittest
from datetime import timezone
from decimal import Decimal

from crypto_lab.config import ConfigError
from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.config import model_to_builtins
from tests.helpers import encode_config
from tests.helpers import load_spot_config_dict


class LabRunConfigTests(unittest.TestCase):
    def test_round_trip_preserves_decimal_strings_and_utc_timestamps(self) -> None:
        config = LabRunConfig.from_json_bytes(encode_config(load_spot_config_dict()))
        round_tripped = LabRunConfig.from_json_bytes(config.to_json_bytes())
        builtins = model_to_builtins(round_tripped)

        self.assertEqual(round_tripped, config)
        self.assertIsInstance(round_tripped.initial_capital.amount, Decimal)
        self.assertEqual(builtins["initial_capital"]["amount"], "1000.00")
        self.assertEqual(builtins["warmup_start"], "2024-01-01T00:00:00Z")
        self.assertIs(round_tripped.warmup_start.tzinfo, timezone.utc)

    def test_unknown_top_level_field_fails(self) -> None:
        data = load_spot_config_dict()
        data["silent_default"] = True
        with self.assertRaises(ConfigError):
            LabRunConfig.from_json_bytes(encode_config(data))

    def test_duplicate_field_fails(self) -> None:
        payload = encode_config(load_spot_config_dict())
        duplicate = payload.replace(
            b'{"run_id":"m0-downstream-contract-001",',
            b'{"run_id":"m0-downstream-contract-001","run_id":"duplicate",',
            1,
        )
        with self.assertRaises(ConfigError):
            LabRunConfig.from_json_bytes(duplicate)

    def test_missing_material_field_fails(self) -> None:
        data = load_spot_config_dict()
        del data["fee_assumption"]
        with self.assertRaises(ConfigError):
            LabRunConfig.from_json_bytes(encode_config(data))

    def test_unknown_or_missing_nested_material_field_fails(self) -> None:
        unknown = load_spot_config_dict()
        unknown["nautilus_venue_config"]["implicit_matching_switch"] = False
        with self.assertRaises(ConfigError):
            LabRunConfig.from_json_bytes(encode_config(unknown))

        missing = load_spot_config_dict()
        del missing["nautilus_venue_config"]["trade_execution"]
        with self.assertRaises(ConfigError):
            LabRunConfig.from_json_bytes(encode_config(missing))

    def test_required_material_runtime_settings_are_explicit(self) -> None:
        config = LabRunConfig.from_json_bytes(encode_config(load_spot_config_dict()))
        venue = config.nautilus_venue_config

        self.assertEqual(venue.price_protection_points, 0)
        self.assertIsNone(venue.fee_model)
        self.assertEqual(
            venue.effective_fee_model_path,
            "nautilus_trader.backtest.models.fee:MakerTakerFeeModel",
        )
        self.assertFalse(config.nautilus_engine_config.portfolio.use_mark_prices)
        self.assertEqual(venue.fill_model.prob_fill_on_limit, 1.0)
        self.assertEqual(venue.fill_model.prob_slippage, 1.0)
        self.assertEqual(venue.fill_model.random_seed, 0)
        self.assertTrue(venue.bar_execution)
        self.assertFalse(venue.trade_execution)
        self.assertTrue(venue.liquidity_consumption)
        self.assertFalse(venue.queue_position)
        self.assertTrue(venue.use_message_queue)
        engine = config.nautilus_engine_config
        self.assertEqual(engine.message_bus.types_filter, "DISABLED")
        self.assertEqual(engine.portfolio.snapshot_interval_ms, "DISABLED")
        self.assertEqual(engine.catalogs, ())
        self.assertEqual(engine.strategies, ())
        self.assertEqual(engine.timeout_connection, 60.0)
        self.assertEqual(engine.timeout_shutdown, 5.0)
        self.assertFalse(engine.logging_bypass)

    def test_frozen_config_rejects_nested_material_mutation(self) -> None:
        config = LabRunConfig.from_json_bytes(encode_config(load_spot_config_dict()))
        with self.assertRaises(TypeError):
            config.nautilus_engine_config.risk_engine.max_notional_per_order[
                "BTCUSDT.BINANCE"
            ] = "1"

    def test_perpetual_profile_requires_mark_prices_funding_and_mark_bindings(self) -> None:
        data = copy.deepcopy(load_spot_config_dict())
        data["market_profile"] = "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING"
        data["nautilus_venue_config"]["account_type"] = "MARGIN"
        data["nautilus_engine_config"]["portfolio"]["use_mark_prices"] = True
        data["funding_binding"] = "e" * 64
        data["mark_binding"] = "f" * 64

        config = LabRunConfig.from_json_bytes(encode_config(data))
        self.assertEqual(
            config.market_profile,
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        )
        self.assertTrue(config.nautilus_engine_config.portfolio.use_mark_prices)

        data["nautilus_engine_config"]["portfolio"]["use_mark_prices"] = False
        with self.assertRaises(ConfigError):
            LabRunConfig.from_json_bytes(encode_config(data))


if __name__ == "__main__":
    unittest.main()
