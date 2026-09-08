from __future__ import annotations

import unittest

from scripts.validate_audit_research_runs import DEFAULT_FREEZE
from scripts.validate_audit_research_runs import _require_exact_checker_match
from scripts.validate_audit_research_runs import validate


class AuditResearchValidatorTests(unittest.TestCase):
    def test_missing_raw_source_diagnostic_remains_fail_closed(self) -> None:
        missing_object = "f" * 64
        regenerated = {
            "outcome": "CHECK_FAIL",
            "failure_codes": ["FUNDING_AMBIGUOUS"],
            "checks": [
                {
                    "name": "official_funding_exact_binding",
                    "pass": False,
                    "detail": (
                        "DATA_HASH_MISMATCH: source object "
                        f"data/raw/objects/sha256/{missing_object[:2]}/{missing_object}.bin "
                        "does not resolve"
                    ),
                },
            ],
        }
        with self.assertRaises(ValueError) as raised:
            _require_exact_checker_match(
                role="PRIMARY",
                persisted={"outcome": "CHECK_PASS", "failure_codes": []},
                regenerated=regenerated,
            )
        message = str(raised.exception)
        self.assertIn("persisted checker differs", message)
        self.assertIn("DATA_HASH_MISMATCH", message)
        self.assertIn(missing_object, message)
        self.assertIn("does not resolve", message)

    def test_legacy_six_development_runs_cannot_revalidate_as_current_results(self) -> None:
        result = validate(DEFAULT_FREEZE, require_remote_tip=False)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["validated_run_count"], 0)
        self.assertEqual(len(result["failures"]), 6)
        self.assertEqual(result["holdout_entry_count"], 0)
        self.assertFalse(result["final_holdout_used"])
        self.assertFalse(result["profitability_claim_authorized"])
        self.assertTrue(
            all(
                "result is not ACTIVE" in failure
                or "required result-status registry is missing" in failure
                or "final primary Run is revoked" in failure
                for failure in result["failures"]
            ),
        )


if __name__ == "__main__":
    unittest.main()
