from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crypto_lab.checker import CheckerOutcome
from crypto_lab.checker import CheckerReport
from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import MarketProfile
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.m3 import MechanicalIntegrity
from crypto_lab.m3 import MechanicalIntegrityResult
from crypto_lab.research import ClaimEvaluation
from crypto_lab.research import ResearchEligibility
from crypto_lab.research import ResearchIntent
from crypto_lab.result_status import load_historical_result_registry
from crypto_lab.runner import RunResult
from crypto_lab.runner import run_lab
from crypto_lab.status import FailureCode
from crypto_lab.status import RunState
from crypto_lab.status import canonicalize_evidence_failure_codes
from crypto_lab.status import validated_failure_codes
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import a4_bars
from tests.m1_helpers import make_request
from tests.m1_helpers import plan

ROOT = Path(__file__).resolve().parents[2]


class R2FailureCodeVocabularyTests(unittest.TestCase):
    def test_product_output_rejects_unknown_codes_but_evidence_maps_them_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown failure code"):
            validated_failure_codes(("ATTACKER_DEFINED_PASS",))
        self.assertEqual(
            canonicalize_evidence_failure_codes(
                [
                    FailureCode.DATA_GAP.value,
                    "ATTACKER_DEFINED_PASS",
                    FailureCode.DATA_GAP.value,
                ],
            ),
            (
                FailureCode.DATA_GAP.value,
                FailureCode.EVIDENCE_INCOMPLETE.value,
            ),
        )
        self.assertEqual(
            canonicalize_evidence_failure_codes("NOT_A_JSON_ARRAY"),
            (FailureCode.EVIDENCE_INCOMPLETE.value,),
        )

    def test_persisted_report_and_m3_schema_reject_arbitrary_codes(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown failure code"):
            CheckerReport(
                CheckerOutcome.COMPONENT_CHECK_FAIL,
                ("ATTACKER_DEFINED_PASS",),
                (),
            )
        with self.assertRaisesRegex(ValueError, "unknown failure code"):
            MechanicalIntegrityResult(
                state=MechanicalIntegrity.FAIL,
                checker_result="COMPONENT_CHECK_FAIL",
                replay_result="FAIL",
                run_ids=("primary", "replay"),
                failure_codes=("ATTACKER_DEFINED_PASS",),
            )
        with self.assertRaisesRegex(ValueError, "unknown failure code"):
            RunResult(
                run_id="unknown-code",
                state=RunState.FAILED,
                failure_codes=("ATTACKER_DEFINED_PASS",),
                checker_outcome=CheckerOutcome.COMPONENT_CHECK_FAIL,
                official_seal_outcome=None,
                config_sha256="0" * 64,
                semantic_digest="1" * 64,
                evidence_dir=Path("/tmp/not-used"),
                evidence_inventory=(),
                orders=(),
                fills=(),
                positions=(),
                account_events=(),
                funding_events=(),
                strategy_observations={},
            )
        with self.assertRaisesRegex(ValueError, "unknown failure code"):
            ClaimEvaluation.create(
                protocol_id="NOT_APPLICABLE",
                mechanical_integrity=MechanicalIntegrity.FAIL,
                research_intent=ResearchIntent.EXPLORATORY,
                research_eligibility=ResearchEligibility.INELIGIBLE,
                eligible_confirmatory_profitability_claim=False,
                failure_codes=("ATTACKER_DEFINED_PASS",),
                reasons=("fixture",),
                limitations=("fixture",),
            )
        accepted = ClaimEvaluation.create(
            protocol_id="NOT_APPLICABLE",
            mechanical_integrity=MechanicalIntegrity.FAIL,
            research_intent=ResearchIntent.EXPLORATORY,
            research_eligibility=ResearchEligibility.INELIGIBLE,
            eligible_confirmatory_profitability_claim=False,
            failure_codes=(FailureCode.CLAIM_INELIGIBLE.value,),
            reasons=("fixture",),
            limitations=("fixture",),
        )
        with self.assertRaisesRegex(ValueError, "unknown failure code"):
            accepted.force_ineligible("ATTACKER_DEFINED_PASS", "tampered")

    def test_checker_never_propagates_unknown_preflight_or_guard_lexemes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = run_lab(
                make_request(
                    Path(temporary),
                    run_id="r2-failure-code-vocabulary",
                    profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                    data=a4_bars(SPOT_ID),
                    plan=plan({}),
                    scoring_start_ns=0,
                    scoring_end_ns=180_000_000_000,
                ),
            )
            result_path = result.evidence_dir / "nautilus_result.json"
            baseline = json.loads(result_path.read_text(encoding="utf-8"))
            for field, mutation in (
                ("preflight_failure_codes", ["ATTACKER_DEFINED_PASS"]),
                ("guard_failures", [{"failure_code": "ATTACKER_DEFINED_PASS"}]),
            ):
                with self.subTest(field=field):
                    tampered = json.loads(json.dumps(baseline))
                    if field == "guard_failures":
                        tampered["strategy_observations"][field] = mutation
                    else:
                        tampered[field] = mutation
                    result_path.write_bytes(canonical_json_bytes(tampered) + b"\n")
                    report = check_evidence_directory(
                        result.evidence_dir,
                        repository_root=ROOT,
                    )
                    self.assertEqual(
                        report.outcome,
                        CheckerOutcome.COMPONENT_CHECK_BLOCKED,
                    )
                    self.assertIn(
                        FailureCode.EVIDENCE_INCOMPLETE.value,
                        report.failure_codes,
                    )
                    self.assertNotIn("ATTACKER_DEFINED_PASS", report.failure_codes)
                    self.assertTrue(
                        all(FailureCode(code).value == code for code in report.failure_codes),
                    )
            result_path.write_bytes(canonical_json_bytes(baseline) + b"\n")

    def test_legacy_registry_unknown_code_becomes_canonical_incomplete(self) -> None:
        records = [
            {
                "path": "runs/historical-run",
                "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
                "historical_run_status": "REVOKED",
                "financial_result_status": "INVALIDATED",
                "finding_ids": ["F-001"],
                "current_checker_outcome": "CHECK_FAIL",
                "current_failure_codes": ["ATTACKER_DEFINED_PASS"],
                "historical_bytes_preserved": True,
                "evidence_hashes": {
                    "checker.json": "1" * 64,
                    "status.json": "2" * 64,
                    "evidence_manifest.json": "3" * 64,
                },
            },
        ]
        manifest = {
            "schema": "audit-historical-result-status-v1",
            "audit_id": "COMPREHENSIVE_AUDIT_REMEDIATION_001",
            "audited_baseline_commit": "890b9d41cc05ff091f41c82409d196c91b86d452",
            "source_commit": "8" * 40,
            "recorded_at_utc": "2026-08-30T00:00:00Z",
            "historical_policy": "Historical bytes are immutable.",
            "final_holdout_authorized": False,
            "profitability_claim_authorized": False,
            "record_count": 1,
            "records": records,
            "records_identity": canonical_sha256(records),
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            registry = load_historical_result_registry(path)
        self.assertEqual(
            registry.records[0].current_failure_codes,
            (FailureCode.EVIDENCE_INCOMPLETE.value,),
        )


if __name__ == "__main__":
    unittest.main()
