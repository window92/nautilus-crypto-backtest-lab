from __future__ import annotations

import unittest
from decimal import Decimal

from crypto_lab.strategies import spot_base_buy_maximum_cost
from crypto_lab.strategies import spot_quote_buy_capacity


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


if __name__ == "__main__":
    unittest.main()
