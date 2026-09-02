from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType

from crypto_lab.config import MarketProfile
from crypto_lab.hashing import canonical_sha256
from crypto_lab.reporting import OFFICIAL_ANNUALIZATION_DAYS
from crypto_lab.reporting import OFFICIAL_EQUITY_OBSERVATION_BASIS
from crypto_lab.reporting import OFFICIAL_RISK_MINIMUM_SAMPLE_COUNT
from crypto_lab.reporting import REQUIRED_SCIENTIFIC_LIMITATIONS
from crypto_lab.reporting import ReportOutput
from crypto_lab.status import FailureCode
from scripts.run_adversarial_remediation_002_acceptance import _plan_epoch
from scripts.validate_adversarial_remediation_002_runs import COMPONENT_PASS
from scripts.validate_adversarial_remediation_002_runs import EXPECTED_BRANCH
from scripts.validate_adversarial_remediation_002_runs import EXPECTED_EPOCH
from scripts.validate_adversarial_remediation_002_runs import EXPECTED_PLAN_SCHEMA
from scripts.validate_adversarial_remediation_002_runs import EXPECTED_RESEARCH_FAMILY
from scripts.validate_adversarial_remediation_002_runs import R2ValidationFailure
from scripts.validate_adversarial_remediation_002_runs import SEAL_PASS
from scripts.validate_adversarial_remediation_002_runs import validate
from scripts.validate_adversarial_remediation_002_runs import validate_claim_payload
from scripts.validate_adversarial_remediation_002_runs import validate_component_payload
from scripts.validate_adversarial_remediation_002_runs import validate_performance_payload
from scripts.validate_adversarial_remediation_002_runs import validate_plan_payload
from scripts.validate_adversarial_remediation_002_runs import validate_rebuild_payload
from scripts.validate_adversarial_remediation_002_runs import validate_report_claim_projection
from scripts.validate_adversarial_remediation_002_runs import validate_replay_payload


VALIDATOR_ROOT = Path(__file__).resolve().parents[2]


def plan_payload() -> dict[str, object]:
    profiles = (
        MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY.value,
        MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value,
    )
    execution = []
    for sequence in range(1, 7):
        profile = profiles[0 if sequence <= 3 else 1]
        purpose = "BENCHMARK_STUDY" if sequence in {1, 4} else "OWNER_STUDY"
        trial_id = f"{EXPECTED_EPOCH}-trial-{sequence}"
        execution.append(
            {
                "sequence": sequence,
                "profile": profile,
                "purpose": purpose,
                "partition_role": "DEVELOPMENT",
                "trial_id": trial_id,
                "run_id": f"{EXPECTED_EPOCH}-run-{sequence}",
                "workflow_input": f"/tmp/plan/workflow-inputs/{trial_id}.json",
                "workflow_input_sha256": format(sequence, "064x"),
                "result_summary": f"/tmp/plan/results/{trial_id}.json",
                "command_argv": [
                    "/usr/bin/env",
                    "-i",
                    "PATH=/usr/bin:/bin",
                    "LANG=C.UTF-8",
                    "LC_ALL=C.UTF-8",
                    "TZ=UTC",
                    str(VALIDATOR_ROOT / ".venv/bin/python"),
                    "-I",
                    "-P",
                    "-S",
                    "-B",
                    "-X",
                    "pycache_prefix=/dev/null",
                    str(VALIDATOR_ROOT / "scripts/isolated_runtime_bootstrap.py"),
                    "--authority",
                    str(VALIDATOR_ROOT / "runtime-bootstrap-authority.json"),
                    "--repository",
                    str(VALIDATOR_ROOT),
                    "--entrypoint",
                    "crypto_lab.owner:main",
                    "--",
                    "--input",
                    f"/tmp/plan/workflow-inputs/{trial_id}.json",
                    "--repository",
                    str(VALIDATOR_ROOT),
                    "--output",
                    f"/tmp/plan/results/{trial_id}.json",
                ],
                "owner_fresh_child_count": 2,
                "expected_copies": ["PRIMARY", "REPLAY"],
                "component_validation_required": COMPONENT_PASS,
                "official_seal_required": SEAL_PASS,
                "deterministic_replay_required": "PASS",
                "final_holdout_used": False,
                "profitability_claim_authorized": False,
            },
        )
    value: dict[str, object] = {
        "schema": EXPECTED_PLAN_SCHEMA,
        "epoch": EXPECTED_EPOCH,
        "frozen_at_utc": "2026-08-31T00:00:00Z",
        "source": {
            "branch": EXPECTED_BRANCH,
            "head": "1" * 40,
            "source_tree": "2" * 40,
            "remote_ref": f"origin/{EXPECTED_BRANCH}",
            "remote_tip": "1" * 40,
        },
        "runtime_lock_sha256": "3" * 64,
        "runtime_bootstrap_authority_sha256": "6" * 64,
        "qualification_registry": {},
        "data_rebuild_validation": {},
        "dataset_releases": {},
        "protocol_ids": ["4" * 64, "5" * 64],
        "research_family_id": EXPECTED_RESEARCH_FAMILY,
        "execution": execution,
        "workflow_count": 6,
        "primary_run_count": 6,
        "replay_run_count": 6,
        "fresh_process_run_count": 12,
        "execution_order": (
            "SPOT_BENCHMARK_CANDIDATE_A_CANDIDATE_B_THEN_PERPETUAL_SAME_ORDER"
        ),
        "owner_checkpoint_contract": {
            "normal_push_required_after_each": True,
            "squash_rebase_force_push_forbidden": True,
        },
        "preparation_only": True,
        "owner_executed": False,
        "final_holdout_used": False,
        "live_trading_used": False,
        "profitability_claim_authorized": False,
    }
    value["plan_identity"] = canonical_sha256(value)
    return value


def replay_payload() -> dict[str, object]:
    value: dict[str, object] = {
        "schema": "owner-deterministic-replay-v2",
        "trial_id": "trial",
        "primary_run_ref": "runs/primary",
        "replay_run_ref": "runs/replays/trial/replay",
        "primary_config_sha256": "1" * 64,
        "replay_config_sha256": "1" * 64,
        "primary_semantic_digest": "2" * 64,
        "replay_semantic_digest": "2" * 64,
        "primary_state": "COMPLETED",
        "replay_state": "COMPLETED",
        "primary_component_validation": COMPONENT_PASS,
        "replay_component_validation": COMPONENT_PASS,
        "primary_official_seal": SEAL_PASS,
        "replay_official_seal": SEAL_PASS,
        "fresh_processes": True,
        "read_only_checker_revalidated": True,
        "result": "PASS",
        "primary_child_returncode": 0,
        "replay_child_returncode": 0,
        "primary_child_diagnostic": "NOT_APPLICABLE",
        "replay_child_diagnostic": "NOT_APPLICABLE",
    }
    value["replay_identity"] = canonical_sha256(value)
    return value


class R2AcceptanceValidatorTests(unittest.TestCase):
    def test_master_acceptance_binds_the_exact_plan_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "execution-plan.json"
            path.write_text(
                '{"epoch":"adversarial-remediation-002-retry-009"}\n',
                encoding="utf-8",
            )
            self.assertEqual(
                _plan_epoch(path),
                "adversarial-remediation-002-retry-009",
            )
            for invalid in (
                {},
                {"epoch": None},
                {"epoch": "retry-009"},
                {"epoch": "adversarial-remediation-002/escape"},
            ):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(
                    ValueError,
                    "in-scope explicit epoch",
                ):
                    path.write_text(json.dumps(invalid), encoding="utf-8")
                    _plan_epoch(path)

    def assert_code(self, expected: FailureCode, operation) -> None:
        with self.assertRaises(R2ValidationFailure) as raised:
            operation()
        self.assertEqual(raised.exception.code, expected)
        self.assertTrue(raised.exception.stage)
        self.assertTrue(raised.exception.detail)

    def test_plan_is_exactly_r2_development_only(self) -> None:
        valid = plan_payload()
        validate_plan_payload(valid, repository_root=VALIDATOR_ROOT)

        holdout = plan_payload()
        holdout["final_holdout_used"] = True
        material = dict(holdout)
        material.pop("plan_identity")
        holdout["plan_identity"] = canonical_sha256(material)
        self.assert_code(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            lambda: validate_plan_payload(
                holdout,
                repository_root=VALIDATOR_ROOT,
            ),
        )

        stale_epoch = plan_payload()
        stale_epoch["epoch"] = "different-epoch"
        material = dict(stale_epoch)
        material.pop("plan_identity")
        stale_epoch["plan_identity"] = canonical_sha256(material)
        self.assert_code(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            lambda: validate_plan_payload(
                stale_epoch,
                repository_root=VALIDATOR_ROOT,
            ),
        )

        direct_import = plan_payload()
        command = direct_import["execution"][0]["command_argv"]
        command[6:6] = [f"PYTHONPATH={VALIDATOR_ROOT / 'src'}"]
        material = dict(direct_import)
        material.pop("plan_identity")
        direct_import["plan_identity"] = canonical_sha256(material)
        self.assert_code(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            lambda: validate_plan_payload(
                direct_import,
                repository_root=VALIDATOR_ROOT,
            ),
        )

    def test_component_cannot_promote_a_legacy_or_partial_outcome(self) -> None:
        valid = {
            "outcome": COMPONENT_PASS,
            "failure_codes": [],
            "mutated_run_evidence": False,
            "checks": [{"name": "financial_reconciliation", "pass": True}],
        }
        validate_component_payload(valid, stage="fixture")
        stale = dict(valid)
        stale["outcome"] = "CHECK_PASS"
        self.assert_code(
            FailureCode.CHECKER_FAILURE,
            lambda: validate_component_payload(stale, stage="fixture"),
        )
        failed_check = dict(valid)
        failed_check["checks"] = [{"name": "financial_reconciliation", "pass": False}]
        self.assert_code(
            FailureCode.CHECKER_FAILURE,
            lambda: validate_component_payload(failed_check, stage="fixture"),
        )

    def test_replay_requires_both_seals_and_exact_semantic_digest(self) -> None:
        valid = replay_payload()
        validate_replay_payload(valid, trial_id="trial", primary_ref="runs/primary")
        altered = replay_payload()
        altered["replay_semantic_digest"] = "9" * 64
        material = dict(altered)
        material.pop("replay_identity")
        altered["replay_identity"] = canonical_sha256(material)
        self.assert_code(
            FailureCode.DETERMINISM_FAILURE,
            lambda: validate_replay_payload(
                altered,
                trial_id="trial",
                primary_ref="runs/primary",
            ),
        )
        missing_seal = replay_payload()
        missing_seal["replay_official_seal"] = "FAIL"
        material = dict(missing_seal)
        material.pop("replay_identity")
        missing_seal["replay_identity"] = canonical_sha256(material)
        self.assert_code(
            FailureCode.DETERMINISM_FAILURE,
            lambda: validate_replay_payload(
                missing_seal,
                trial_id="trial",
                primary_ref="runs/primary",
            ),
        )
        extra_authority = replay_payload()
        extra_authority["final_holdout_used"] = True
        material = dict(extra_authority)
        material.pop("replay_identity")
        extra_authority["replay_identity"] = canonical_sha256(material)
        self.assert_code(
            FailureCode.DETERMINISM_FAILURE,
            lambda: validate_replay_payload(
                extra_authority,
                trial_id="trial",
                primary_ref="runs/primary",
            ),
        )

    def test_metrics_reject_non_daily_or_warmup_basis(self) -> None:
        valid = {
            "schema_version": 2,
            "run_id": "run",
            "equity_observation_basis": OFFICIAL_EQUITY_OBSERVATION_BASIS,
            "valuation_frequency": "DAILY_MARKED_PORTFOLIO_EQUITY_UTC",
            "annualization_days": str(OFFICIAL_ANNUALIZATION_DAYS),
            "minimum_risk_sample_count": OFFICIAL_RISK_MINIMUM_SAMPLE_COUNT,
            "intraday_drawdown_captured": False,
            "scored_start": "2021-02-01T00:00:00Z",
            "scoring_end_exclusive": "2021-08-01T00:00:00Z",
            "scientific_limitations": list(REQUIRED_SCIENTIFIC_LIMITATIONS),
            "daily_return_sample_count": 1,
            "daily_returns": [{}],
        }
        validate_performance_payload(
            valid,
            run_id="run",
            scoring_start="2021-02-01T00:00:00Z",
            scoring_end_exclusive="2021-08-01T00:00:00Z",
        )
        invalid = dict(valid)
        invalid["equity_observation_basis"] = "CASH_ONLY_WITH_WARMUP"
        self.assert_code(
            FailureCode.PERFORMANCE_METRICS_INVALID,
            lambda: validate_performance_payload(
                invalid,
                run_id="run",
                scoring_start="2021-02-01T00:00:00Z",
                scoring_end_exclusive="2021-08-01T00:00:00Z",
            ),
        )
        malformed = dict(valid)
        malformed["daily_returns"] = None
        self.assert_code(
            FailureCode.PERFORMANCE_METRICS_INVALID,
            lambda: validate_performance_payload(
                malformed,
                run_id="run",
                scoring_start="2021-02-01T00:00:00Z",
                scoring_end_exclusive="2021-08-01T00:00:00Z",
            ),
        )

    def test_report_cannot_authorize_profitability_live_or_holdout(self) -> None:
        valid = {
            "selected_trial_id": f"{EXPECTED_EPOCH}-trial",
            "report_purpose": "OFFICIAL_RESEARCH_REPORT",
            "research_intent": "EXPLORATORY",
            "mechanical_integrity": "PASS",
            "research_eligibility": "INELIGIBLE",
            "development_only": True,
            "trial_count": 1,
            "trial_history": [{"trial_id": f"{EXPECTED_EPOCH}-trial"}],
            "claim_scope": "INSTRUMENT_ONLY",
            "drawdown_frequency": "DAILY_NOT_INTRADAY",
            "estimated_bar_execution": True,
            "estimated_fee_limitation": True,
            "historical_exchange_filter_claim": "NOT_FULLY_PROVEN",
            "historical_fee_tier_claim": "NOT_PROVEN",
            "historical_spread_claim": "NOT_MODELED",
            "liquidation_claim": "NOT_MODELED",
            "market_impact_claim": "NOT_MODELED",
            "perpetual_leverage": "FIXED_AT_ONE_IN_V1",
            "queue_position_claim": "NOT_MODELED",
            "terminal_position_disposition": "CAUSALLY_MARKED_NOT_ACTUALLY_CLOSED",
            "profitability_claim_is_real": False,
            "live_trading_authorized": False,
            "final_holdout_used": False,
            "qualification_limitations": list(REQUIRED_SCIENTIFIC_LIMITATIONS),
        }
        validate_claim_payload(valid, trial_id=f"{EXPECTED_EPOCH}-trial")
        frozen = MappingProxyType(
            {
                **valid,
                "trial_history": tuple(
                    MappingProxyType(dict(item)) for item in valid["trial_history"]
                ),
                "qualification_limitations": tuple(valid["qualification_limitations"]),
            },
        )
        validate_claim_payload(frozen, trial_id=f"{EXPECTED_EPOCH}-trial")
        frozen_tamper = MappingProxyType({**frozen, "development_only": False})
        self.assert_code(
            FailureCode.CLAIM_INELIGIBLE,
            lambda: validate_claim_payload(
                frozen_tamper,
                trial_id=f"{EXPECTED_EPOCH}-trial",
            ),
        )
        for field, value in (
            ("profitability_claim_is_real", True),
            ("live_trading_authorized", True),
            ("final_holdout_used", True),
            ("research_eligibility", "ELIGIBLE"),
            ("research_eligibility", "ARBITRARY_AUTHORITY"),
            ("development_only", False),
        ):
            altered = dict(valid)
            altered[field] = value
            with self.subTest(field=field):
                self.assert_code(
                    FailureCode.CLAIM_INELIGIBLE,
                    lambda altered=altered: validate_claim_payload(
                        altered,
                        trial_id=f"{EXPECTED_EPOCH}-trial",
                    ),
                )

    def test_persisted_retry_009_reports_survive_strict_immutable_projection(self) -> None:
        reports = sorted(
            (VALIDATOR_ROOT / "research/reports").glob(
                "adversarial-remediation-002-retry-009-*.json",
            ),
        )
        self.assertEqual(len(reports), 6)
        for path in reports:
            with self.subTest(path=path.name):
                report = ReportOutput.from_json_bytes(path.read_bytes())
                payload = validate_report_claim_projection(
                    report,
                    trial_id=path.stem,
                    require_current_development_holdout_semantics=False,
                )
                self.assertEqual(payload["claim_result"], report.claim_evaluation.to_builtins())

    def test_partial_retry_010_reports_fail_current_holdout_semantics(self) -> None:
        reports = sorted(
            (VALIDATOR_ROOT / "research/reports").glob(
                "adversarial-remediation-002-retry-010-spot-*.json",
            ),
        )
        self.assertEqual(len(reports), 3)
        for path in reports:
            with self.subTest(path=path.name):
                report = ReportOutput.from_json_bytes(path.read_bytes())
                self.assert_code(
                    FailureCode.CLAIM_INELIGIBLE,
                    lambda report=report, path=path: validate_report_claim_projection(
                        report,
                        trial_id=path.stem,
                    ),
                )

    def test_dataset_rebuild_missing_four_way_proof_is_structured(self) -> None:
        invalid = {
            "schema": (
                "free-official-binance-deterministic-rebuild-validation-v2-"
                "full-raw-inventory"
            ),
            "status": "PASS",
            "strategy_run": False,
            "official_trial": False,
            "network_used": False,
            "primary_readonly_gate": {},
            "independent_readonly_gate": {},
            "comparison": {"dataset_release_ids": []},
            "materialized_release_artifacts": {},
            "nautilus_catalog_validation": {},
        }
        self.assert_code(
            FailureCode.DATASET_RAW_INVENTORY_MISMATCH,
            lambda: validate_rebuild_payload(invalid, releases={}),
        )

    def test_missing_plan_returns_closed_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = validate(
                plan_path=None,
                plan_root=Path(temporary),
                repository_root=Path(__file__).resolve().parents[2],
                require_remote_tip=False,
            )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["failure_codes"], [FailureCode.RESEARCH_PROTOCOL_INVALID.value])
        self.assertEqual(result["validated_primary_run_count"], 0)
        self.assertEqual(result["validated_evidence_directory_count"], 0)
        self.assertFalse(result["final_holdout_used"])
        self.assertFalse(result["live_trading_used"])
        self.assertFalse(result["profitability_claim_authorized"])

    def test_symlinked_plan_is_rejected_before_any_run_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target.json"
            target.write_text("{}\n", encoding="utf-8")
            link = root / "execution-plan.json"
            link.symlink_to(target)
            result = validate(
                plan_path=link,
                repository_root=Path(__file__).resolve().parents[2],
                require_remote_tip=False,
            )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["failure_codes"], [FailureCode.EVIDENCE_INCOMPLETE.value])


if __name__ == "__main__":
    unittest.main()
