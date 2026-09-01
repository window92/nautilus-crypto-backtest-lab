from __future__ import annotations

import copy
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from crypto_lab.m3 import MechanicalIntegrity
from crypto_lab.reporting import ReportInput
from crypto_lab.reporting import build_report
from crypto_lab.reporting import write_report
from crypto_lab.research import ClaimEvaluationInput
from crypto_lab.research import ClaimScope
from crypto_lab.research import MonteCarloStatus
from crypto_lab.research import PartitionRole
from crypto_lab.research import ResearchEligibility
from crypto_lab.research import ResearchIntent
from crypto_lab.research import SampleAdequacy
from crypto_lab.research import TrialDefinition
from crypto_lab.research import TrialJournal
from crypto_lab.research import TrialState
from crypto_lab.research import evaluate_claim
from tests.m4_helpers import candidate
from tests.m4_helpers import instant
from tests.m4_helpers import valid_protocol


def claim_input(**changes: object) -> ClaimEvaluationInput:
    protocol = valid_protocol()
    base = ClaimEvaluationInput(
        protocol=protocol,
        mechanical_integrity=MechanicalIntegrity.PASS,
        checker_result="COMPONENT_CHECK_PASS",
        official_seal_result="OFFICIAL_SEAL_PASS",
        underlying_official_runs_valid=True,
        qualification_only=False,
        protocol_frozen_before_results=True,
        supporting_trial_protocol_ids=(protocol.protocol_id,),
        complete_trial_history=True,
        partitions_valid=True,
        selected_partition_role=PartitionRole.FINAL_HOLDOUT,
        holdout_valid=True,
        benchmark_valid=True,
        multiple_testing_valid=True,
        sample_adequacy_by_instrument={"BTCUSDT.BINANCE": SampleAdequacy.ADEQUATE.value},
        monte_carlo_by_instrument={"BTCUSDT.BINANCE": MonteCarloStatus.COMPLETED.value},
        diagnostics_complete_by_instrument={"BTCUSDT.BINANCE": True},
        claim_scope_supported=True,
        universe_evidence_valid=True,
        unresolved_material_ambiguities=(),
        synthetic_contract_fixture=True,
    )
    return replace(base, **changes)


class ClaimGateTests(unittest.TestCase):
    def test_positive_synthetic_contract_is_eligible_but_not_a_real_claim(self) -> None:
        result = evaluate_claim(claim_input())
        self.assertEqual(result.research_eligibility, ResearchEligibility.ELIGIBLE)
        self.assertTrue(result.eligible_confirmatory_profitability_claim)
        self.assertIn("SYNTHETIC_CONTRACT_FIXTURE_NOT_REAL_CLAIM", result.limitations)

    def test_development_partition_is_ineligible_without_claiming_holdout_consumption(self) -> None:
        result = evaluate_claim(
            claim_input(
                selected_partition_role=PartitionRole.DEVELOPMENT,
                holdout_valid=False,
            ),
        )
        self.assertEqual(result.research_eligibility, ResearchEligibility.INELIGIBLE)
        self.assertFalse(result.eligible_confirmatory_profitability_claim)
        self.assertIn("FINAL_HOLDOUT_NOT_USED", result.reasons)
        self.assertIn("CLAIM_INELIGIBLE", result.failure_codes)
        self.assertNotIn("HOLDOUT_INVALID_OR_CONSUMED", result.reasons)
        self.assertNotIn("HOLDOUT_ALREADY_CONSUMED", result.failure_codes)

    def test_selected_invalid_final_holdout_retains_consumed_failure(self) -> None:
        result = evaluate_claim(claim_input(holdout_valid=False))
        self.assertEqual(result.research_eligibility, ResearchEligibility.INELIGIBLE)
        self.assertIn("HOLDOUT_INVALID_OR_CONSUMED", result.reasons)
        self.assertIn("HOLDOUT_ALREADY_CONSUMED", result.failure_codes)

    def test_profitability_never_overrides_failed_mechanical_integrity(self) -> None:
        result = evaluate_claim(
            claim_input(mechanical_integrity=MechanicalIntegrity.FAIL),
        )
        self.assertEqual(result.research_eligibility, ResearchEligibility.INELIGIBLE)
        self.assertFalse(result.eligible_confirmatory_profitability_claim)
        self.assertIn("MECHANICAL_INTEGRITY_NOT_PASS", result.reasons)

    def test_missing_multiplicity_and_low_sample_block_confirmatory_eligibility(self) -> None:
        multiplicity = evaluate_claim(
            claim_input(multiple_testing_valid=False),
        )
        self.assertEqual(multiplicity.research_eligibility, ResearchEligibility.EXPLORATORY_ONLY)
        self.assertIn("MULTIPLE_TESTING_UNDECLARED", multiplicity.failure_codes)
        low_sample = evaluate_claim(
            claim_input(
                sample_adequacy_by_instrument={
                    "BTCUSDT.BINANCE": SampleAdequacy.LOW_CONFIDENCE.value,
                },
            ),
        )
        self.assertEqual(low_sample.research_eligibility, ResearchEligibility.EXPLORATORY_ONLY)
        self.assertIn("SAMPLE_ADEQUACY_NOT_ADEQUATE", low_sample.reasons)

    def test_adequate_sample_requires_completed_monte_carlo(self) -> None:
        result = evaluate_claim(
            claim_input(
                monte_carlo_by_instrument={
                    "BTCUSDT.BINANCE": MonteCarloStatus.MC_LOW_CONFIDENCE.value,
                },
            ),
        )
        self.assertEqual(result.research_eligibility, ResearchEligibility.INELIGIBLE)
        self.assertIn("MONTE_CARLO_NOT_COMPLETED", result.reasons)

    def test_later_protocol_reanalysis_is_exploratory_only(self) -> None:
        current = valid_protocol()
        prior = valid_protocol(frozen_at=instant("2019-11-01T00:00:00Z"))
        changed = type(current).create_from(
            current,
            primary_metric="CHANGED_AFTER_RESULTS",
            frozen_at_utc=instant("2020-06-01T00:00:00Z"),
        )
        result = evaluate_claim(
            claim_input(
                protocol=changed,
                supporting_trial_protocol_ids=(prior.protocol_id,),
                protocol_frozen_before_results=False,
            ),
        )
        self.assertEqual(result.research_eligibility, ResearchEligibility.EXPLORATORY_ONLY)
        self.assertIn("LATER_PROTOCOL_REANALYSIS", result.reasons)

    def test_single_instrument_cannot_support_market_wide_scope(self) -> None:
        protocol = type(valid_protocol()).create_from(
            valid_protocol(),
            intended_claim_scope=ClaimScope.POINT_IN_TIME_UNIVERSE,
        )
        result = evaluate_claim(
            claim_input(protocol=protocol, claim_scope_supported=False),
        )
        self.assertEqual(result.research_eligibility, ResearchEligibility.INELIGIBLE)
        self.assertIn("CLAIM_SCOPE_UNSUPPORTED", result.reasons)

    def test_qualification_evidence_is_ineligible_for_real_profitability_claim(self) -> None:
        result = evaluate_claim(
            claim_input(qualification_only=True, synthetic_contract_fixture=False),
        )
        self.assertEqual(result.research_eligibility, ResearchEligibility.INELIGIBLE)
        self.assertIn("QUALIFICATION_EVIDENCE_NOT_PROFITABILITY_PROOF", result.reasons)

    def test_missing_benchmark_diagnostics_or_instrument_results_are_ineligible(self) -> None:
        for changes, expected in (
            ({"benchmark_valid": False}, "BENCHMARK_MISSING_OR_INVALID"),
            ({"diagnostics_complete_by_instrument": {}}, "PERFORMANCE_DIAGNOSTICS_INCOMPLETE"),
            ({"sample_adequacy_by_instrument": {}}, "SAMPLE_ADEQUACY_NOT_ADEQUATE"),
            ({"monte_carlo_by_instrument": {}}, "MONTE_CARLO_NOT_COMPLETED"),
        ):
            with self.subTest(changes=changes):
                result = evaluate_claim(claim_input(**changes))
                self.assertFalse(result.eligible_confirmatory_profitability_claim)
                self.assertIn(expected, result.reasons)

    def test_unresolved_material_ambiguity_blocks_instead_of_guessing(self) -> None:
        result = evaluate_claim(
            claim_input(unresolved_material_ambiguities=("AMBIGUOUS_NATIVE_TRADE_SEQUENCE",)),
        )
        self.assertEqual(result.research_eligibility, ResearchEligibility.BLOCKED)
        self.assertIn("AMBIGUOUS_NATIVE_TRADE_SEQUENCE", result.reasons)


class ReportTests(unittest.TestCase):
    def test_g17_winner_only_report_is_ineligible_and_all_trials_are_disclosed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = TrialJournal(Path(temporary) / "trials.jsonl")
            protocol = valid_protocol(candidate_count=2)
            trials = []
            for index, terminal in enumerate((TrialState.FAILED, TrialState.COMPLETED)):
                item = TrialDefinition.synthetic(
                    trial_id=f"trial-{index}",
                    protocol=protocol,
                    candidate=candidate(index),
                    run_id=f"run-{index}",
                )
                trials.append(item)
                journal.start(item, at_utc=instant("2020-05-01T00:00:00Z"))
                journal.finish(
                    item.trial_id,
                    state=terminal,
                    at_utc=instant("2020-05-01T00:01:00Z"),
                    result_ref=f"runs/run-{index}/result.json",
                    reason="fixture",
                    result_exposed=True,
                )
            report = build_report(
                ReportInput.synthetic(
                    protocol=protocol,
                    claim_evaluation=evaluate_claim(
                        claim_input(
                            protocol=protocol,
                            supporting_trial_protocol_ids=(protocol.protocol_id,),
                        ),
                    ),
                    trial_records=journal.read_records(),
                    included_trial_ids=(trials[1].trial_id,),
                    selected_trial_id=trials[1].trial_id,
                ),
            )
            self.assertEqual(report.claim_evaluation.research_eligibility, ResearchEligibility.INELIGIBLE)
            self.assertIn("TRIAL_HISTORY_INCOMPLETE", report.claim_evaluation.failure_codes)
            self.assertIn("trial-0", report.markdown)
            self.assertIn("FAILED", report.markdown)

    def test_reporting_does_not_mutate_run_evidence_or_claim_input(self) -> None:
        report_input = ReportInput.synthetic(
            protocol=valid_protocol(),
            claim_evaluation=evaluate_claim(claim_input()),
            trial_records=(),
            included_trial_ids=(),
            selected_trial_id="NOT_APPLICABLE",
        )
        before = report_input.to_json_bytes()
        report = build_report(report_input)
        self.assertEqual(before, report_input.to_json_bytes())
        self.assertIn("Estimated bar execution", report.markdown)
        self.assertIn("unsupported", report.markdown.lower())
        self.assertEqual(report.json_payload["profitability_claim_is_real"], False)

    def test_json_and_markdown_reports_roundtrip_with_required_disclosures(self) -> None:
        report_input = ReportInput.synthetic(
            protocol=valid_protocol(),
            claim_evaluation=evaluate_claim(claim_input()),
            trial_records=(),
            included_trial_ids=(),
            selected_trial_id="NOT_APPLICABLE",
        )
        report = build_report(report_input)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            json_path = root / "report.json"
            markdown_path = root / "report.md"
            write_report(report, json_path=json_path, markdown_path=markdown_path)
            reparsed = type(report).from_json_bytes(json_path.read_bytes())
            self.assertEqual(reparsed, report)
            markdown = markdown_path.read_text(encoding="utf-8")
            for disclosure in (
                "MechanicalIntegrity",
                "ResearchEligibility",
                "Multiple-testing treatment",
                "Benchmark",
                "Sample adequacy",
                "Monte Carlo",
                "Estimated bar execution",
                "unsupported/UNKNOWN",
            ):
                self.assertIn(disclosure, markdown)


if __name__ == "__main__":
    unittest.main()
