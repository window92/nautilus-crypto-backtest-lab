from __future__ import annotations

import unittest
from decimal import Decimal

from crypto_lab.m1_qualification import qualify_native_perpetual_funding


class M1NativeFundingQualificationTests(unittest.TestCase):
    def test_g09_positive_funding_debits_long_exactly_once(self) -> None:
        result = qualify_native_perpetual_funding()

        self.assertEqual(result["runtime_version"], "2.0.0rc2")
        self.assertEqual(result["status"], "PASS")
        cases = {case["name"]: case for case in result["cases"]}
        self.assertEqual(
            Decimal(cases["long_next_funding_ns"]["actual_cash_effect_usdt"]),
            Decimal("-2"),
        )
        self.assertEqual(
            Decimal(cases["short_next_funding_ns"]["actual_cash_effect_usdt"]),
            Decimal("2"),
        )
        self.assertEqual(
            Decimal(cases["long_interval"]["actual_cash_effect_usdt"]),
            Decimal("-2"),
        )
        self.assertEqual(
            Decimal(cases["short_interval"]["actual_cash_effect_usdt"]),
            Decimal("2"),
        )
        self.assertEqual(
            Decimal(cases["post_boundary_not_charged"]["actual_cash_effect_usdt"]),
            Decimal("0"),
        )
        self.assertTrue(
            all(
                all(case["conditions"].values())
                for case in result["cases"]
            ),
        )
        self.assertFalse(result["project_cash_posting"])


if __name__ == "__main__":
    unittest.main()
