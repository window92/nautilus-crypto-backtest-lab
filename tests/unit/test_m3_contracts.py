from __future__ import annotations

import unittest
from datetime import UTC
from datetime import datetime

from crypto_lab.config import MarketProfile
from crypto_lab.config import SourceRevision
from crypto_lab.m3 import MechanicalIntegrity
from crypto_lab.m3 import MechanicalIntegrityResult
from crypto_lab.m3 import M3NegativeControl
from crypto_lab.m3 import ProfileQualificationState
from crypto_lab.m3 import QualifiedProfileRecord
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.m3 import qualification_strategy_inputs
from crypto_lab.m3 import negative_qualification_inputs
from crypto_lab.offline import NetworkAttemptBlocked
from crypto_lab.offline import offline_network_guard
import socket


RUNTIME_LOCK_SHA256 = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
COMMIT = "0" * 40
TREE = "1" * 40


def source_revision() -> SourceRevision:
    return SourceRevision(
        repository="https://github.com/window92/nautilus-crypto-backtest-lab.git",
        branch_ref="main",
        git_commit=COMMIT,
        git_tree=TREE,
        clean_worktree=True,
        captured_at_utc=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
    )


def qualified_record(profile: MarketProfile, marker: str) -> QualifiedProfileRecord:
    return QualifiedProfileRecord.create(
        profile_id=profile,
        qualification_state=ProfileQualificationState.QUALIFIED,
        runtime_lock_sha256=RUNTIME_LOCK_SHA256,
        source_revision=source_revision(),
        base_dataset_release_id=marker * 64,
        dataset_release_id=("a" if marker == "2" else "b") * 64,
        strategy_spec_id=("c" if marker == "2" else "d") * 64,
        accepted_run_ids=(f"m3-{profile.value.lower()}-primary", f"m3-{profile.value.lower()}-replay"),
        checker_result="CHECK_PASS",
        replay_result="PASS",
        evidence_references=(f"runs/{profile.value.lower()}/",),
        qualification_limitations=(
            "QUALIFICATION_INTERVAL_EXPOSED_NOT_FRESH_HOLDOUT",
            "BAR_BASED_ESTIMATED_EXECUTION",
            "ESTIMATED_FEE",
        ),
    )


class M3ContractTests(unittest.TestCase):
    def test_qualification_strategy_schedule_is_frozen_before_execution(self) -> None:
        spot = qualification_strategy_inputs(MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY)
        perpetual = qualification_strategy_inputs(
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        )

        self.assertEqual(
            spot.strategy_plan.material_payload(),
            {
                "conflict_rule": "FIRST_ELIGIBLE_INTENT",
                "intents_by_bar_ns": {
                    "1735689600000000000": [
                        {
                            "order_type": "MARKET",
                            "quantity": "0.00100",
                            "reason": "M3_SPOT_COMPLETED_BAR_LONG_ENTRY",
                            "side": "BUY",
                        },
                    ],
                },
                "qualification_attempt_all_intents": False,
            },
        )
        self.assertEqual(
            perpetual.strategy_plan.material_payload()["intents_by_bar_ns"],
            {
                "1735718220000000000": [
                    {
                        "order_type": "MARKET",
                        "quantity": "0.004",
                        "reason": "M3_PERP_OPEN_LONG_BEFORE_FUNDING",
                        "side": "BUY",
                    },
                ],
                "1735718280000000000": [
                    {
                        "order_type": "MARKET",
                        "quantity": "0.001",
                        "reason": "M3_PERP_REDUCE_LONG_BEFORE_FUNDING",
                        "side": "SELL",
                    },
                ],
                "1735718460000000000": [
                    {
                        "order_type": "MARKET",
                        "quantity": "0.003",
                        "reason": "M3_PERP_CLOSE_EXACTLY_FLAT_AFTER_FUNDING",
                        "side": "SELL",
                    },
                ],
                "1735718520000000000": [
                    {
                        "order_type": "MARKET",
                        "quantity": "0.001",
                        "reason": "M3_PERP_REOPEN_SHORT_FROM_FLAT",
                        "side": "SELL",
                    },
                ],
            },
        )
        for inputs in (spot, perpetual):
            self.assertEqual(
                inputs.strategy_spec.parameters["strategy_plan_sha256"],
                inputs.strategy_plan.strategy_plan_sha256,
            )
            self.assertEqual(inputs.strategy_spec.parameters["run_purpose"], "QUALIFICATION")
            self.assertEqual(inputs.strategy_spec.parameters["result_dependent_branching"], "false")

    def test_registry_is_strict_content_addressed_and_has_exactly_two_profiles(self) -> None:
        spot = qualified_record(MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY, "2")
        perpetual = qualified_record(
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            "7",
        )
        registry = QualifiedProfileRegistry.create(records=(spot, perpetual))
        reparsed = QualifiedProfileRegistry.from_json_bytes(registry.to_json_bytes())
        self.assertEqual(reparsed, registry)
        self.assertEqual(reparsed.registry_content_sha256, registry.registry_content_sha256)
        self.assertEqual(
            tuple(item.qualification_state for item in reparsed.records),
            (ProfileQualificationState.QUALIFIED, ProfileQualificationState.QUALIFIED),
        )
        with self.assertRaises(ValueError):
            QualifiedProfileRegistry.create(records=(spot,))

    def test_mechanical_integrity_shape_cannot_report_pass_without_checker_and_replay(self) -> None:
        accepted = MechanicalIntegrityResult(
            state=MechanicalIntegrity.PASS,
            checker_result="CHECK_PASS",
            replay_result="PASS",
            run_ids=("primary", "replay"),
            failure_codes=(),
        )
        self.assertEqual(
            MechanicalIntegrityResult.from_json_bytes(accepted.to_json_bytes()),
            accepted,
        )
        with self.assertRaises(ValueError):
            MechanicalIntegrityResult(
                state=MechanicalIntegrity.PASS,
                checker_result="CHECK_FAIL",
                replay_result="PASS",
                run_ids=("primary", "replay"),
                failure_codes=(),
            )

    def test_negative_control_plans_are_frozen_pre_submit_contracts(self) -> None:
        spot = negative_qualification_inputs(M3NegativeControl.SPOT_SHORT)
        cross = negative_qualification_inputs(M3NegativeControl.PERP_DIRECT_CROSS_ZERO)
        concurrent = negative_qualification_inputs(M3NegativeControl.PERP_CONCURRENT_ORDER)
        above = negative_qualification_inputs(M3NegativeControl.PERP_ABOVE_MARKET_MAX)
        post = negative_qualification_inputs(M3NegativeControl.PERP_POST_BOUNDARY_OPEN)
        self.assertEqual(
            spot.strategy_plan.intents_by_bar_ns[1_735_689_600_000_000_000][0].side,
            "SELL",
        )
        self.assertEqual(
            cross.strategy_plan.intents_by_bar_ns[1_735_718_280_000_000_000][0].quantity,
            "0.005",
        )
        self.assertTrue(concurrent.strategy_plan.qualification_attempt_all_intents)
        self.assertEqual(
            above.strategy_plan.intents_by_bar_ns[1_735_718_220_000_000_000][0].quantity,
            "120.001",
        )
        self.assertEqual(tuple(post.strategy_plan.intents_by_bar_ns), (1_735_718_400_000_000_000,))

    def test_offline_guard_blocks_and_records_connection_attempt(self) -> None:
        with offline_network_guard() as evidence:
            with self.assertRaises(NetworkAttemptBlocked):
                socket.create_connection(("example.invalid", 443))
        self.assertEqual(len(evidence.attempts), 1)
        self.assertEqual(evidence.attempts[0]["api"], "socket.create_connection")


if __name__ == "__main__":
    unittest.main()
