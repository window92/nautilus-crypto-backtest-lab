from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from nautilus_trader.model import CurrencyPair
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import Symbol

from crypto_lab.config import LabRunConfig
from crypto_lab.data_acceptance import _run_sentinel
from crypto_lab.strategies import spot_base_buy_maximum_cost
from crypto_lab.strategies import spot_quote_buy_capacity
from tests.m1_helpers import BTC
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import USDT
from tests.m1_helpers import make_bars


ROOT = Path(__file__).resolve().parents[2]
MINUTE_NS = 60_000_000_000


class SpotAffordabilityMutationControls(unittest.TestCase):
    """Independent known-number controls for the fee-reservation sign."""

    def test_base_quantity_cost_adds_not_subtracts_commission(self) -> None:
        # 2 BTC * 100 USDT plus a 10bp taker commission = 200.2 USDT.
        self.assertEqual(
            spot_base_buy_maximum_cost(
                base_quantity=Decimal("2"),
                maximum_fill_price=Decimal("100"),
                taker_fee_rate=Decimal("0.001"),
            ),
            Decimal("200.200"),
        )

    def test_quote_capacity_reserves_fee_and_both_rounding_budgets(self) -> None:
        capacity = spot_quote_buy_capacity(
            available_quote=Decimal("1000"),
            taker_fee_rate=Decimal("0.001"),
            commission_rounding_reserve=Decimal("0.00000001"),
            base_rounding_reserve=Decimal("0.01"),
        )
        self.assertEqual(
            capacity,
            (Decimal("1000") - Decimal("0.00000001"))
            / Decimal("1.001")
            - Decimal("0.01"),
        )
        self.assertLess(capacity, Decimal("999"))

    def test_invalid_affordability_domain_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "affordability"):
            spot_base_buy_maximum_cost(
                base_quantity=Decimal("1"),
                maximum_fill_price=Decimal("100"),
                taker_fee_rate=Decimal("-0.001"),
            )

    def test_dataset_acceptance_sentinel_uses_reserved_quote_sizing(self) -> None:
        """The real 1,000,000 USDT max-price bound must not block the sentinel.

        A base-denominated 0.1 BTC order would require 100,100 USDT at that
        bound and is correctly rejected against the locked 10,000 USDT CASH
        account.  Dataset acceptance instead submits the signal-close notional
        through the same fee/rounding-reserved quote path as production Spot
        strategies; the native Fill must still occur after the locked latency.
        """

        base_quantity_bound = spot_base_buy_maximum_cost(
            base_quantity=Decimal("0.1"),
            maximum_fill_price=Decimal("1000000.01"),
            taker_fee_rate=Decimal("0.001"),
        )
        self.assertEqual(base_quantity_bound, Decimal("100100.001001"))
        self.assertGreater(base_quantity_bound, Decimal("10000"))

        instrument = CurrencyPair(
            SPOT_ID,
            Symbol("BTCUSDT"),
            BTC,
            USDT,
            price_precision=2,
            size_precision=5,
            price_increment=Price.from_str("0.01"),
            size_increment=Quantity.from_str("0.00001"),
            min_price=Price.from_str("0.01"),
            max_price=Price.from_str("1000000.00"),
            ts_event=0,
            ts_init=0,
            multiplier=Quantity.from_str("1"),
            margin_init=Decimal("1"),
            margin_maint=Decimal("1"),
            maker_fee=Decimal("0.001"),
            taker_fee=Decimal("0.001"),
        )
        bars = make_bars(
            SPOT_ID,
            tuple(
                (minute * MINUTE_NS, "100.00", "101.00", "99.00", "100.00")
                for minute in range(1, 7)
            ),
            volume="1000.00000",
        )
        config_path = (
            ROOT
            / "runs/owner-smoke-002-spot-run-retry-001-8a09aee98d9f"
            / "lab_run_config.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = _run_sentinel(
                config=LabRunConfig.from_json_bytes(config_path.read_bytes()),
                instrument=instrument,
                all_data=bars,
                execution_bars=bars,
                signal_index=1,
                role="R2_SPOT_RESERVED_QUOTE_SIZING",
                quantity="0.10000",
                diagnostic_root=Path(temporary),
            )
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["order_count"], 1)
        self.assertEqual(result["fill_count"], 1)
        self.assertEqual(result["guard_failure_count"], 0)
        self.assertEqual(result["guard_failures"], [])
        self.assertGreaterEqual(
            int(result["fill_ts_event"]),
            int(result["signal_bar_ts_init"]) + MINUTE_NS,
        )


if __name__ == "__main__":
    unittest.main()
