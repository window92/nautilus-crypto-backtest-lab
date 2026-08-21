from __future__ import annotations

import math
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from crypto_lab.hashing import CanonicalJSONError
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256


class CanonicalJSONTests(unittest.TestCase):
    def test_decimal_timestamp_and_key_order_match_independent_golden(self) -> None:
        value = {
            "z": Decimal("1.2300"),
            "nested": {"b": 2, "a": True},
            "a": datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        }
        expected_path = Path("tests/golden/fixtures/canonical-json.expected.json")
        expected = expected_path.read_bytes().rstrip(b"\n")

        self.assertEqual(canonical_json_bytes(value), expected)
        self.assertEqual(
            canonical_sha256(value),
            "8fd8d00a202e3577c0e3915954867f67730241b881eaad280e6694fdd8be4f15",
        )

    def test_non_utc_or_naive_datetime_is_rejected(self) -> None:
        with self.assertRaises(CanonicalJSONError):
            canonical_json_bytes({"at": datetime(2024, 1, 1)})

    def test_nan_and_infinity_are_rejected(self) -> None:
        for invalid in (math.nan, math.inf, -math.inf):
            with self.subTest(invalid=invalid), self.assertRaises(CanonicalJSONError):
                canonical_json_bytes({"value": invalid})


if __name__ == "__main__":
    unittest.main()

