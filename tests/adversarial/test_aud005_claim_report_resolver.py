from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import crypto_lab
from crypto_lab.diagnostics import DiagnosticResolution
from crypto_lab.diagnostics import reconcile_diagnostic_resolution
from crypto_lab.m3 import MechanicalIntegrity
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.official import OfficialEvidenceLocator
from crypto_lab.official import OfficialEvidenceResolver
from crypto_lab.official import _candidate_schedule_complete
from crypto_lab.official import _historical_failed_checker_is_retained
from crypto_lab.reporting import ReportInput
from crypto_lab.reporting import build_report
from crypto_lab.research import ClaimEvaluationInput
from crypto_lab.research import MonteCarloStatus
from crypto_lab.research import ResearchError
from crypto_lab.research import SampleAdequacy
from crypto_lab.research import TrialDefinition
from crypto_lab.research import TrialJournal
from crypto_lab.research import TrialState
from crypto_lab.research import evaluate_claim
from tests.adversarial.test_aud003_004_authoritative_history import HistoryAttackFixture
from tests.m4_helpers import valid_protocol
from tests.m4_helpers import candidate
from tests.m4_helpers import instant


def _asserted_claim(*, synthetic: bool) -> ClaimEvaluationInput:
    protocol = valid_protocol()
    return ClaimEvaluationInput(
        protocol=protocol,
        mechanical_integrity=MechanicalIntegrity.PASS,
        checker_result="CHECK_PASS",
        underlying_official_runs_valid=True,
        qualification_only=False,
        protocol_frozen_before_results=True,
        supporting_trial_protocol_ids=(protocol.protocol_id,),
        complete_trial_history=True,
        partitions_valid=True,
        holdout_valid=True,
        benchmark_valid=True,
        multiple_testing_valid=True,
        sample_adequacy_by_instrument={"BTCUSDT.BINANCE": SampleAdequacy.ADEQUATE.value},
        monte_carlo_by_instrument={"BTCUSDT.BINANCE": MonteCarloStatus.COMPLETED.value},
        diagnostics_complete_by_instrument={"BTCUSDT.BINANCE": True},
        claim_scope_supported=True,
        universe_evidence_valid=True,
        unresolved_material_ambiguities=(),
        synthetic_contract_fixture=synthetic,
    )


def _diagnostic(*, run_hash: str) -> DiagnosticResolution:
    return DiagnosticResolution.create(
        run_id="run-one",
        protocol_id="a" * 64,
        run_evidence_hashes={"native_completed_trades.json": run_hash},
        native_completed_trades_status="UNAVAILABLE",
        native_completed_trade_count="UNDEFINED",
        performance_diagnostics_status="INCOMPLETE",
        sample_adequacy=SampleAdequacy.NOT_APPLICABLE,
        monte_carlo_status=MonteCarloStatus.NOT_APPLICABLE,
        benchmark_status="MISSING",
        benchmark_id="fixture-benchmark",
        claim_scope="INSTRUMENT_ONLY",
        complete_for_confirmatory_profitability_claim=False,
        limitations=("NATIVE_COMPLETED_TRADE_SEQUENCE_UNAVAILABLE",),
    )


class Aud005ClaimReportResolverTests(unittest.TestCase):
    def test_historical_failed_checker_is_preserved_but_cannot_pose_as_pass(self) -> None:
        status = {
            "state": "FAILED",
            "checker_outcome": "CHECK_FAIL",
            "failure_codes": ["LOOKAHEAD_DETECTED"],
        }
        checker = {
            "outcome": "CHECK_FAIL",
            "failure_codes": ["LOOKAHEAD_DETECTED"],
            "mutated_run_evidence": False,
        }
        self.assertTrue(
            _historical_failed_checker_is_retained(TrialState.FAILED, status, checker),
        )
        self.assertFalse(
            _historical_failed_checker_is_retained(
                TrialState.FAILED,
                {**status, "checker_outcome": "CHECK_PASS", "failure_codes": []},
                {**checker, "outcome": "CHECK_PASS", "failure_codes": []},
            ),
        )
        self.assertFalse(
            _historical_failed_checker_is_retained(
                TrialState.COMPLETED,
                {**status, "state": "COMPLETED"},
                checker,
            ),
        )

        blocked_status = {
            "state": "BLOCKED",
            "checker_outcome": "CHECK_BLOCKED",
            "failure_codes": ["INSTRUMENT_METADATA_INVALID"],
        }
        blocked_checker = {
            "outcome": "CHECK_BLOCKED",
            "failure_codes": ["INSTRUMENT_METADATA_INVALID"],
            "mutated_run_evidence": False,
        }
        self.assertFalse(
            _historical_failed_checker_is_retained(
                TrialState.FAILED,
                blocked_status,
                blocked_checker,
            ),
        )
        self.assertTrue(
            _historical_failed_checker_is_retained(
                TrialState.FAILED,
                blocked_status,
                blocked_checker,
                failure_or_block_reason="DETERMINISTIC_REPLAY_MISMATCH_OR_FAILURE",
            ),
        )
        self.assertTrue(
            _historical_failed_checker_is_retained(
                TrialState.FAILED,
                {
                    **blocked_status,
                    "failure_codes": [
                        "RUNTIME_LOCK_MISMATCH",
                        "INSTRUMENT_METADATA_INVALID",
                    ],
                },
                {
                    **blocked_checker,
                    "failure_codes": [
                        "INSTRUMENT_METADATA_INVALID",
                        "RUNTIME_LOCK_MISMATCH",
                    ],
                },
                failure_or_block_reason="DETERMINISTIC_REPLAY_MISMATCH_OR_FAILURE",
            ),
        )

    def test_official_locator_rejects_assertions_subsets_metrics_and_trades(self) -> None:
        base = {
            "schema_version": 1,
            "protocol_id": "a" * 64,
            "selected_trial_id": "trial-one",
            "expected_history_anchor_sha256": "b" * 64,
            "report_purpose": "OFFICIAL_RESEARCH_REPORT",
        }
        forged = (
            {"mechanical_integrity": "PASS"},
            {"eligible": True},
            {"included_trial_ids": ["winner-only"]},
            {"metrics": {"total_return": "999"}},
            {"native_completed_trades": ["999"]},
            {"diagnostics_complete": True},
        )
        for assertion in forged:
            with self.subTest(assertion=assertion):
                payload = {**base, **assertion}
                with self.assertRaises(ValueError):
                    OfficialEvidenceLocator.from_json_bytes(
                        __import__("json").dumps(payload).encode("utf-8"),
                    )

    def test_forged_boolean_claim_cannot_become_an_official_eligible_claim(self) -> None:
        with self.assertRaisesRegex(ResearchError, "OfficialEvidenceResolver"):
            evaluate_claim(_asserted_claim(synthetic=False))
        self.assertFalse(hasattr(crypto_lab, "evaluate_claim"))
        self.assertFalse(hasattr(crypto_lab, "build_report"))
        self.assertFalse(hasattr(crypto_lab, "ClaimEvaluationInput"))

    def test_winner_only_official_report_is_rejected_at_public_low_level_boundary(self) -> None:
        synthetic_claim = evaluate_claim(_asserted_claim(synthetic=True))
        value = ReportInput.synthetic(
            protocol=valid_protocol(),
            claim_evaluation=synthetic_claim,
            trial_records=(),
            included_trial_ids=(),
            selected_trial_id="NOT_APPLICABLE",
        )
        forged = replace(value, report_purpose="OFFICIAL_RESEARCH_REPORT")
        with self.assertRaisesRegex(ResearchError, "OfficialEvidenceLocator"):
            build_report(forged)

    def test_forged_metrics_or_native_trades_do_not_match_derived_resolution(self) -> None:
        expected = _diagnostic(run_hash="1" * 64)
        for forged in (
            _diagnostic(run_hash="2" * 64),
            replace(
                expected,
                diagnostic_resolution_id=DiagnosticResolution.create(
                    **{
                        **{
                            key: value
                            for key, value in expected.to_builtins().items()
                            if key not in {"schema_version", "diagnostic_resolution_id"}
                        },
                        "run_evidence_hashes": {"native_completed_trades.json": "3" * 64},
                    },
                ).diagnostic_resolution_id,
                run_evidence_hashes={"native_completed_trades.json": "3" * 64},
            ),
        ):
            with self.subTest(forged=forged.run_evidence_hashes):
                with tempfile.TemporaryDirectory() as temporary:
                    path = Path(temporary) / "diagnostic.json"
                    path.write_bytes(forged.to_json_bytes() + b"\n")
                    with patch(
                        "crypto_lab.diagnostics.derive_diagnostic_resolution",
                        return_value=expected,
                    ):
                        with self.assertRaisesRegex(ResearchError, "stale or forged"):
                            reconcile_diagnostic_resolution(
                                path=path,
                                run_dir=Path(temporary),
                                protocol=valid_protocol(),
                                benchmark_directory=Path(temporary),
                            )

    def test_stale_authoritative_history_head_blocks_before_protocol_or_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HistoryAttackFixture(Path(temporary))
            resolver = OfficialEvidenceResolver(repository_root=Path(temporary))
            locator = OfficialEvidenceLocator(
                schema_version=1,
                protocol_id="a" * 64,
                selected_trial_id="trial-one",
                expected_history_anchor_sha256="f" * 64,
                report_purpose="OFFICIAL_RESEARCH_REPORT",
            )
            with self.assertRaisesRegex(ResearchError, "stale authoritative history head"):
                resolver.resolve(locator)

    def test_missing_frozen_candidate_schedule_is_not_complete_evidence(self) -> None:
        protocol = valid_protocol(candidate_count=2)
        with tempfile.TemporaryDirectory() as temporary:
            journal = TrialJournal(Path(temporary) / "trials.jsonl")
            first = TrialDefinition.synthetic(
                trial_id="candidate-zero",
                protocol=protocol,
                candidate=candidate(0),
                run_id="candidate-zero-run",
            )
            _planned, started = journal.start(
                first,
                at_utc=instant("2020-05-01T00:00:00Z"),
            )
            self.assertFalse(_candidate_schedule_complete(protocol, (started,)))
            second = TrialDefinition.synthetic(
                trial_id="candidate-one",
                protocol=protocol,
                candidate=candidate(1),
                run_id="candidate-one-run",
            )
            _planned_two, started_two = journal.start(
                second,
                at_utc=instant("2020-05-01T00:01:00Z"),
            )
            self.assertTrue(_candidate_schedule_complete(protocol, (started, started_two)))

    def test_run_manifest_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            run_dir.mkdir()
            outside = root / "outside.json"
            outside.write_bytes(b"{}\n")
            (run_dir / "escaped.json").symlink_to(outside)
            entries = [
                {
                    "path": "escaped.json",
                    "sha256": sha256_file(outside),
                    "byte_size": outside.stat().st_size,
                },
            ]
            (run_dir / "evidence_manifest.json").write_bytes(
                canonical_json_bytes(
                    {
                        "schema": "run-evidence-manifest-v1",
                        "run_id": "symlink-run",
                        "entries": entries,
                        "inventory_content_sha256": canonical_sha256(entries),
                        "manifest_self_excluded": True,
                    },
                )
                + b"\n",
            )
            resolver = object.__new__(OfficialEvidenceResolver)
            with self.assertRaisesRegex(ResearchError, "manifest mismatch"):
                resolver._verify_manifest(run_dir, "symlink-run")


if __name__ == "__main__":
    unittest.main()
