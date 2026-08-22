from __future__ import annotations

import unittest

from crypto_lab.m1_qualification import qualify_native_mark_fallback
from crypto_lab.m1_qualification import qualify_native_spot_cash_behavior


class M1NativeProfileQualificationTests(unittest.TestCase):
    def test_g07_native_cash_limitation_requires_project_pre_submit_guard(self) -> None:
        result = qualify_native_spot_cash_behavior()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(all(result["conditions"].values()))
        self.assertFalse(result["synthetic_bid_ask_or_quote_data_used"])
        self.assertEqual(
            result["accepted_project_behavior"],
            "BLOCK_BEFORE_SUBMISSION_WITH_SPOT_SHORT_OR_BORROW_DETECTED",
        )

    def test_g11_native_mark_path_and_fallback_negative_control(self) -> None:
        result = qualify_native_mark_fallback()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(all(result["conditions"].values()))
        self.assertEqual(
            result["accepted_project_behavior"],
            "BLOCKED_MARK_ROLE_INVALID_BEFORE_ENGINE",
        )


if __name__ == "__main__":
    unittest.main()
