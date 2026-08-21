from __future__ import annotations

import unittest

from crypto_lab.status import FailureCode
from crypto_lab.status import RunState


class StatusContractTests(unittest.TestCase):
    def test_run_terminal_states_match_ssot(self) -> None:
        self.assertEqual(
            {state.value for state in RunState},
            {"COMPLETED", "FAILED", "BLOCKED", "ABORTED"},
        )

    def test_m0_failure_codes_are_stable(self) -> None:
        self.assertEqual(FailureCode.RUNTIME_LOCK_MISMATCH.value, "RUNTIME_LOCK_MISMATCH")
        self.assertEqual(
            FailureCode.RUNTIME_WHEEL_HASH_MISMATCH.value,
            "RUNTIME_WHEEL_HASH_MISMATCH",
        )
        self.assertEqual(FailureCode.UNSUPPORTED_RUNTIME.value, "UNSUPPORTED_RUNTIME")
        self.assertEqual(FailureCode.CONFIG_INVALID.value, "CONFIG_INVALID")


if __name__ == "__main__":
    unittest.main()

