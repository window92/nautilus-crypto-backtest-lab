from __future__ import annotations

import unittest

from scripts.validate_audit_research_runs import DEFAULT_FREEZE
from scripts.validate_audit_research_runs import validate


class AuditResearchValidatorTests(unittest.TestCase):
    def test_final_six_development_runs_revalidate_from_committed_evidence(self) -> None:
        result = validate(DEFAULT_FREEZE, require_remote_tip=False)
        self.assertEqual(result["status"], "PASS", result["failures"])
        self.assertEqual(result["validated_run_count"], 6)
        self.assertEqual(result["validated_evidence_directory_count"], 12)
        self.assertEqual(result["holdout_entry_count"], 0)
        self.assertFalse(result["final_holdout_used"])
        self.assertFalse(result["profitability_claim_authorized"])
        self.assertEqual(
            {item["market_profile"] for item in result["runs"]},
            {
                "BINANCE_SPOT_CASH_LONG_ONLY",
                "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING",
            },
        )


if __name__ == "__main__":
    unittest.main()
