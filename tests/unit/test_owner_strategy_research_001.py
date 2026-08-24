from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from decimal import Decimal

from crypto_lab.config import MarketProfile
from crypto_lab.config import NOT_APPLICABLE
from crypto_lab.config import SourceRevision
from crypto_lab.research import BenchmarkSpec
from crypto_lab.research import CandidateSpec
from crypto_lab.research import ClaimScope
from crypto_lab.research import InstrumentScope
from crypto_lab.research import MonteCarloSpec
from crypto_lab.research import PurgeEmbargoRule
from crypto_lab.research import ResearchError
from crypto_lab.research import ResearchIntent
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import ResamplingMethod
from crypto_lab.research import SampleAdequacyRule
from crypto_lab.research import UtcInterval
from crypto_lab.strategies import TSMOM_FULL_REGISTRATION_ID
from crypto_lab.strategies import TSMOM_VOL20_REGISTRATION_ID
from crypto_lab.strategies import annualized_realized_volatility_28d
from crypto_lab.strategies import floor_to_increment
from crypto_lab.strategies import is_monday_utc_boundary
from crypto_lab.strategies import locked_weekly_tsmom_strategy_spec
from crypto_lab.strategies import momentum_28d
from crypto_lab.strategies import resolve_registered_strategy_identity
from crypto_lab.strategies import volatility_target_fraction


DAY_NS = 86_400_000_000_000


def _interval(day: int) -> UtcInterval:
    return UtcInterval(
        start_inclusive=datetime(2098, 1, day, tzinfo=UTC),
        end_exclusive=datetime(2098, 1, day + 1, tzinfo=UTC),
    )


class OwnerStrategyResearch001Tests(unittest.TestCase):
    def test_exact_momentum_golden_and_29_close_requirement(self) -> None:
        closes = tuple(Decimal(100 + index) for index in range(29))
        self.assertEqual(momentum_28d(closes), Decimal("0.28"))
        with self.assertRaisesRegex(ValueError, "exactly 29"):
            momentum_28d(closes[:-1])
        with self.assertRaisesRegex(ValueError, "positive"):
            momentum_28d((Decimal(0), *closes[1:]))

    def test_volatility_formula_matches_independent_locked_golden(self) -> None:
        # 14 +ln(2) and 14 -ln(2) returns have population mean zero and
        # population standard deviation ln(2). The literal below was frozen
        # independently at 50-decimal working precision.
        closes = tuple(Decimal(1 if index % 2 == 0 else 2) for index in range(29))
        volatility = annualized_realized_volatility_28d(closes)
        self.assertEqual(
            volatility,
            Decimal("13.242558290607729849121286614314753108367502024384"),
        )
        self.assertEqual(
            volatility_target_fraction(volatility),
            Decimal("0.015102821948070999527100677423216836630158174748958"),
        )
        self.assertEqual(
            annualized_realized_volatility_28d((Decimal(1),) * 29),
            Decimal(0),
        )
        self.assertEqual(volatility_target_fraction(Decimal(0)), Decimal(0))
        for invalid in (Decimal("NaN"), Decimal("Infinity"), Decimal("-0.1")):
            with self.assertRaises(ValueError):
                volatility_target_fraction(invalid)
        with self.assertRaisesRegex(ValueError, "finite and positive"):
            annualized_realized_volatility_28d((Decimal(1),) * 28 + (Decimal("NaN"),))

    def test_monday_schedule_and_increment_floor_are_exact(self) -> None:
        monday = int(datetime(2021, 2, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
        self.assertTrue(is_monday_utc_boundary(monday))
        self.assertFalse(is_monday_utc_boundary(monday + DAY_NS))
        self.assertFalse(is_monday_utc_boundary(monday + 1))
        self.assertEqual(
            floor_to_increment(Decimal("1.234567"), Decimal("0.001")),
            Decimal("1.234"),
        )
        self.assertLessEqual(volatility_target_fraction(Decimal("0.01")), Decimal(1))

    def test_both_candidates_have_static_distinct_registered_identities(self) -> None:
        source = SourceRevision(
            repository="https://github.com/window92/nautilus-crypto-backtest-lab.git",
            branch_ref="main",
            git_commit="1" * 40,
            git_tree="2" * 40,
            clean_worktree=True,
            captured_at_utc=datetime(2026, 8, 24, tzinfo=UTC),
        )
        identities = []
        for registration in (TSMOM_FULL_REGISTRATION_ID, TSMOM_VOL20_REGISTRATION_ID):
            spec = locked_weekly_tsmom_strategy_spec(
                registration,
                MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            )
            identities.append(
                resolve_registered_strategy_identity(
                    registration,
                    strategy_spec=spec,
                    source_revision=source,
                ),
            )
            mutated = replace(
                spec,
                parameters={**spec.parameters, "momentum_formula": "C[-1]/C[-28]-1"},
            )
            with self.assertRaisesRegex(ValueError, "CONFIG_HASH_MISMATCH|contract mismatch"):
                from crypto_lab.strategies import create_registered_strategy

                create_registered_strategy(
                    identities[-1],
                    strategy_spec=mutated,
                    source_revision=source,
                    configuration={},
                )
        self.assertNotEqual(identities[0].strategy_identity_sha256, identities[1].strategy_identity_sha256)

    def test_protocol_binds_two_candidates_holm_and_development_benchmark(self) -> None:
        profile = MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        specs = tuple(
            locked_weekly_tsmom_strategy_spec(registration, profile)
            for registration in (TSMOM_FULL_REGISTRATION_ID, TSMOM_VOL20_REGISTRATION_ID)
        )
        candidates = tuple(
            CandidateSpec.create(
                candidate_label=f"CANDIDATE_{index}",
                strategy_spec_id=spec.strategy_spec_id,
                parameter_values=dict(spec.parameters),
            )
            for index, spec in enumerate(specs)
        )
        development, validation, oos, holdout = (_interval(index) for index in range(1, 5))
        protocol = ResearchProtocol.create(
            frozen_at_utc=datetime(2026, 8, 24, tzinfo=UTC),
            research_family_id="BTCUSDT_WEEKLY_TSMOM28_V1",
            hypothesis_id="fixed-two-candidate-test",
            research_intent=ResearchIntent.EXPLORATORY,
            market_profile=profile,
            instrument_scope=InstrumentScope.SINGLE_INSTRUMENT,
            instrument_ids=(specs[0].instrument_id,),
            instrument_selection_basis="Owner-locked BTCUSDT",
            universe_selection_rule=NOT_APPLICABLE,
            universe_as_of_rule=NOT_APPLICABLE,
            universe_membership_sha256=NOT_APPLICABLE,
            dataset_release_ids=("1" * 64,),
            strategy_family="BTCUSDT_WEEKLY_TSMOM28_V1",
            ordered_candidates=candidates,
            parameter_domain={"candidate_mode": tuple(item.parameters["candidate_mode"] for item in specs)},
            search_budget=2,
            candidate_ordering="AS_LISTED",
            deterministic_generator="EXACT_TWO_OWNER_LOCKED_CANDIDATES",
            random_seeds=(0,),
            primary_metric="NET_PNL_EXPLORATORY",
            required_benchmark=BenchmarkSpec(
                benchmark_id="BUY_AND_HOLD_1X_V1_SPOT",
                definition="Registered 1x benchmark",
                scored_interval=development,
                cost_basis="SAME_FEES_INITIAL_EQUITY_AND_DATASET",
                frozen_before_result_exposure=True,
            ),
            selection_rule="NO_WINNER_SELECTION_EXPLORATORY",
            tie_break_rule="NOT_APPLICABLE_NO_SELECTION",
            development_interval=development,
            validation_interval=validation,
            oos_interval=oos,
            final_holdout_interval=holdout,
            purge_embargo_rule=PurgeEmbargoRule(
                mode=NOT_APPLICABLE,
                reason="No forward label",
                purge_seconds=0,
                embargo_seconds=0,
                max_forward_dependency_seconds=0,
            ),
            time_series_split="CHRONOLOGICAL",
            multiple_testing_treatment="HOLM_BONFERRONI",
            sample_adequacy_rule=SampleAdequacyRule(
                counted_observation=NOT_APPLICABLE,
                minimum_completed_trades=NOT_APPLICABLE,
                rationale="Exploratory",
            ),
            monte_carlo_spec=MonteCarloSpec(
                resampling_method=ResamplingMethod.NOT_APPLICABLE,
                simulation_count=0,
                random_seed=0,
                block_length=NOT_APPLICABLE,
                quantile_method="R7_LINEAR_INTERPOLATION",
                decimal_places=8,
                not_applicable_reason="Exploratory",
            ),
            intended_claim_scope=ClaimScope.INSTRUMENT_ONLY,
            claim_basis="EXPOSED_DEVELOPMENT_NO_PROFITABILITY_CLAIM",
            kill_criteria=("CHECKER_NOT_PASS",),
            terminal_policy="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
        )
        self.assertEqual(protocol.search_budget, 2)
        self.assertEqual(protocol.multiple_testing_treatment, "HOLM_BONFERRONI")
        self.assertEqual(protocol.required_benchmark.scored_interval, development)
        with self.assertRaisesRegex(ResearchError, "one declared scored partition"):
            ResearchProtocol.create_from(
                protocol,
                required_benchmark=replace(
                    protocol.required_benchmark,
                    scored_interval=UtcInterval(
                        start_inclusive=datetime(2099, 1, 1, tzinfo=UTC),
                        end_exclusive=datetime(2099, 1, 2, tzinfo=UTC),
                    ),
                ),
            )


if __name__ == "__main__":
    unittest.main()
