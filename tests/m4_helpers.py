from __future__ import annotations

from datetime import UTC
from datetime import datetime
from decimal import Decimal

from crypto_lab.config import MarketProfile
from crypto_lab.research import BenchmarkSpec
from crypto_lab.research import CandidateSpec
from crypto_lab.research import ClaimScope
from crypto_lab.research import InstrumentScope
from crypto_lab.research import MonteCarloSpec
from crypto_lab.research import PurgeEmbargoRule
from crypto_lab.research import ResearchIntent
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import ResamplingMethod
from crypto_lab.research import SampleAdequacyRule
from crypto_lab.research import UtcInterval


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def interval(start: str, end: str) -> UtcInterval:
    return UtcInterval(start_inclusive=instant(start), end_exclusive=instant(end))


DEVELOPMENT = interval("2020-01-01T00:00:00Z", "2020-02-01T00:00:00Z")
VALIDATION = interval("2020-02-01T00:00:00Z", "2020-03-01T00:00:00Z")
OOS = interval("2020-03-01T00:00:00Z", "2020-04-01T00:00:00Z")
HOLDOUT = interval("2020-04-01T00:00:00Z", "2020-05-01T00:00:00Z")


def candidate(index: int) -> CandidateSpec:
    return CandidateSpec.create(
        candidate_label=f"candidate-{index}",
        strategy_spec_id=f"{index + 1:x}" * 64,
        parameter_values={"lookback": str(index + 2), "threshold": f"0.{index + 1}"},
    )


def valid_protocol(
    *,
    candidate_count: int = 1,
    research_intent: ResearchIntent = ResearchIntent.CONFIRMATORY,
    multiple_testing_treatment: str | None = None,
    instrument_scope: InstrumentScope = InstrumentScope.SINGLE_INSTRUMENT,
    intended_claim_scope: ClaimScope = ClaimScope.INSTRUMENT_ONLY,
    sample_minimum: int | str = 2,
    frozen_at: datetime | None = None,
) -> ResearchProtocol:
    candidates = tuple(candidate(index) for index in range(candidate_count))
    treatment = multiple_testing_treatment
    if treatment is None:
        treatment = "NOT_APPLICABLE" if candidate_count == 1 else "HOLM_BONFERRONI"
    sample_rule = SampleAdequacyRule(
        counted_observation="NAUTILUS_NATIVE_COMPLETED_TRADE",
        minimum_completed_trades=sample_minimum,
        rationale=(
            "Synthetic contract fixture requires two independent native completed trades"
            if isinstance(sample_minimum, int)
            else "Exploratory fixture does not seek a trade-based confirmatory claim"
        ),
    )
    mc_spec = MonteCarloSpec(
        resampling_method=(
            ResamplingMethod.IID_BOOTSTRAP
            if isinstance(sample_minimum, int)
            else ResamplingMethod.NOT_APPLICABLE
        ),
        simulation_count=8 if isinstance(sample_minimum, int) else 0,
        random_seed=7,
        block_length="NOT_APPLICABLE",
        quantile_method="R7_LINEAR_INTERPOLATION",
        decimal_places=8,
        not_applicable_reason=(
            "NOT_APPLICABLE"
            if isinstance(sample_minimum, int)
            else "Exploratory fixture"
        ),
    )
    universe_rule = (
        "NOT_APPLICABLE"
        if instrument_scope is not InstrumentScope.POINT_IN_TIME_UNIVERSE
        else "TOP_VOLUME_USDT_AT_EACH_SELECTION_TIME"
    )
    universe_as_of = (
        "NOT_APPLICABLE"
        if instrument_scope is not InstrumentScope.POINT_IN_TIME_UNIVERSE
        else "MEMBERSHIP_FIELDS_AVAILABLE_AT_SELECTION_TIMESTAMP"
    )
    membership = (
        "NOT_APPLICABLE"
        if instrument_scope is not InstrumentScope.POINT_IN_TIME_UNIVERSE
        else "e" * 64
    )
    instruments = (
        ("BTCUSDT.BINANCE",)
        if instrument_scope is InstrumentScope.SINGLE_INSTRUMENT
        else ("BTCUSDT.BINANCE", "ETHUSDT.BINANCE")
    )
    return ResearchProtocol.create(
        frozen_at_utc=frozen_at or instant("2019-12-01T00:00:00Z"),
        research_family_id="synthetic-m4-family",
        hypothesis_id="synthetic-m4-hypothesis",
        research_intent=research_intent,
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        instrument_scope=instrument_scope,
        instrument_ids=instruments,
        instrument_selection_basis="Predeclared synthetic contract fixture",
        universe_selection_rule=universe_rule,
        universe_as_of_rule=universe_as_of,
        universe_membership_sha256=membership,
        dataset_release_ids=("d" * 64,),
        strategy_family="M4_SYNTHETIC_CONTRACT_ONLY",
        ordered_candidates=candidates,
        parameter_domain={"lookback": tuple(str(i + 2) for i in range(candidate_count))},
        search_budget=candidate_count,
        candidate_ordering="AS_LISTED",
        deterministic_generator="NOT_APPLICABLE",
        random_seeds=(7,),
        primary_metric="NAUTILUS_NATIVE_TOTAL_RETURN",
        required_benchmark=BenchmarkSpec(
            benchmark_id="BUY_AND_HOLD_SAME_INSTRUMENT",
            definition="One frozen same-interval benchmark",
            scored_interval=HOLDOUT,
            cost_basis="SAME_ESTIMATED_FEE_AND_EXECUTION_BASIS",
            frozen_before_result_exposure=True,
        ),
        selection_rule="MAX_PRIMARY_METRIC_SUBJECT_TO_KILL_CRITERIA",
        tie_break_rule="LOWEST_CANDIDATE_ID_LEXICOGRAPHICALLY",
        development_interval=DEVELOPMENT,
        validation_interval=VALIDATION,
        oos_interval=OOS,
        final_holdout_interval=HOLDOUT,
        purge_embargo_rule=PurgeEmbargoRule(
            mode="NOT_APPLICABLE",
            reason="No forward-dependent label, feature, or training sample",
            purge_seconds=0,
            embargo_seconds=0,
            max_forward_dependency_seconds=0,
        ),
        time_series_split="CHRONOLOGICAL",
        multiple_testing_treatment=treatment,
        sample_adequacy_rule=sample_rule,
        monte_carlo_spec=mc_spec,
        intended_claim_scope=intended_claim_scope,
        claim_basis="TRADE_BASED_PROFITABILITY",
        kill_criteria=("MECHANICAL_INTEGRITY_NOT_PASS", "CHECKER_NOT_PASS"),
        terminal_policy="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
    )


NATIVE_TRADES = (Decimal("10"), Decimal("-5"))
