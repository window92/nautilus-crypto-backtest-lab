from __future__ import annotations

import unittest

from crypto_lab.data import DataContractError
from crypto_lab.data_provenance import ProvenanceError
from crypto_lab.research import ResearchError
from crypto_lab.runtime import RuntimeLockMismatch
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

    def test_material_error_types_reject_uncontrolled_failure_codes(self) -> None:
        constructors = (
            lambda: DataContractError("TYPO_CODE", "detail"),
            lambda: ResearchError("TYPO_CODE", "detail"),
            lambda: RuntimeLockMismatch("TYPO_CODE", ["detail"]),
            lambda: ProvenanceError("TYPO_CODE", "detail"),
        )
        for constructor in constructors:
            with self.subTest(constructor=constructor):
                with self.assertRaisesRegex(ValueError, "unknown"):
                    constructor()

    def test_material_error_types_store_canonical_failure_code_values(self) -> None:
        self.assertEqual(
            DataContractError(FailureCode.DATA_GAP, "detail").code,
            FailureCode.DATA_GAP.value,
        )
        self.assertEqual(
            ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "detail").code,
            FailureCode.EVIDENCE_INCOMPLETE.value,
        )
        self.assertEqual(
            RuntimeLockMismatch(FailureCode.RUNTIME_LOCK_MISMATCH, ["detail"]).code,
            FailureCode.RUNTIME_LOCK_MISMATCH.value,
        )


if __name__ == "__main__":
    unittest.main()
