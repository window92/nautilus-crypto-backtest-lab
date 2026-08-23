#!/usr/bin/env python3
"""Generate the two strict OWNER_SMOKE_002 public Owner Workflow inputs."""

from __future__ import annotations

import argparse
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from crypto_lab.config import FeeAssumption
from crypto_lab.config import MarketProfile
from crypto_lab.config import MoneyAmount
from crypto_lab.config import NOT_APPLICABLE
from crypto_lab.data import DatasetRelease
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.owner import OwnerWorkflowInput
from crypto_lab.owner import OwnerWorkflowPurpose
from crypto_lab.research import BenchmarkSpec
from crypto_lab.research import CandidateSpec
from crypto_lab.research import ClaimScope
from crypto_lab.research import InstrumentScope
from crypto_lab.research import MonteCarloSpec
from crypto_lab.research import PartitionRole
from crypto_lab.research import PurgeEmbargoRule
from crypto_lab.research import ResearchIntent
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import ResamplingMethod
from crypto_lab.research import SampleAdequacyRule
from crypto_lab.research import UtcInterval
from crypto_lab.strategies import locked_sma20_strategy_spec


ROOT = Path(__file__).resolve().parents[1]
REGISTRATION_ID = "btcusdt_daily_price_vs_sma20_trend_v1"
SPOT_RELEASE_ID = "95e04adb076be05eba0a970aa0978f1a4d1f41ad3caf04e9cd5859dd408ac099"
PERPETUAL_RELEASE_ID = "9c8a5f679f38852119d1d2054b0711965f0a6d89d5dd0e0ebedaa8d8df66b503"
WARMUP_START = datetime(2021, 1, 1, tzinfo=UTC)
SCORING = UtcInterval(
    start_inclusive=datetime(2021, 2, 1, tzinfo=UTC),
    end_exclusive=datetime(2021, 8, 1, tzinfo=UTC),
)
FEE_RATE = Decimal("0.001")
FEE_REASON = "SSOT Appendix A qualification-only observable estimated fee"


def _future_interval(day: int) -> UtcInterval:
    return UtcInterval(
        start_inclusive=datetime(2099, 1, day, tzinfo=UTC),
        end_exclusive=datetime(2099, 1, day + 1, tzinfo=UTC),
    )


def _profile_record_id(profile: MarketProfile) -> str:
    registry = QualifiedProfileRegistry.from_json_bytes(
        (
            ROOT
            / "evidence/m3/m3-acceptance-001/qualified-profile-registry.json"
        ).read_bytes(),
    )
    record = next(item for item in registry.records if item.profile_id is profile)
    if record.checker_result != "CHECK_PASS" or record.replay_result != "PASS":
        raise RuntimeError(f"qualified profile is unavailable: {profile.value}")
    return record.qualified_profile_record_id


def _workflow(
    *,
    frozen_at_utc: datetime,
    profile: MarketProfile,
    release_id: str,
) -> OwnerWorkflowInput:
    release = DatasetRelease.from_json_bytes(
        (ROOT / "data/releases" / f"{release_id}.json").read_bytes(),
    )
    if release.market_profile is not profile:
        raise RuntimeError("DatasetRelease profile mismatch")
    expected_range = {
        "start_inclusive": "2021-01-01T00:00:00Z",
        "end_exclusive": "2021-08-01T00:00:00Z",
    }
    if release.normalized_time_range.to_builtins() != expected_range:
        raise RuntimeError("DatasetRelease window mismatch")

    suffix = "spot" if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else "perpetual"
    spec = locked_sma20_strategy_spec(profile)
    candidate = CandidateSpec.create(
        candidate_label="SMA20_ONLY_PRE_REGISTERED_CANDIDATE",
        strategy_spec_id=spec.strategy_spec_id,
        parameter_values=dict(spec.parameters),
    )
    protocol = ResearchProtocol.create(
        frozen_at_utc=frozen_at_utc,
        research_family_id=f"owner-smoke-002-{suffix}-daily-price-vs-sma20",
        hypothesis_id=(
            "verified-data-causal-reproducible-owner-workflow-price-vs-sma20-"
            f"{suffix}-not-profitability"
        ),
        research_intent=ResearchIntent.EXPLORATORY,
        market_profile=profile,
        instrument_scope=InstrumentScope.SINGLE_INSTRUMENT,
        instrument_ids=(release.instrument_id,),
        instrument_selection_basis=(
            "OWNER_SMOKE_002_VERIFIED_DATA_EXECUTION locked BTCUSDT profile"
        ),
        universe_selection_rule=NOT_APPLICABLE,
        universe_as_of_rule=NOT_APPLICABLE,
        universe_membership_sha256=NOT_APPLICABLE,
        dataset_release_ids=(release.dataset_release_id,),
        strategy_family="BTCUSDT_DAILY_PRICE_VS_SMA20_TREND",
        ordered_candidates=(candidate,),
        parameter_domain={name: (value,) for name, value in spec.parameters.items()},
        search_budget=1,
        candidate_ordering="AS_LISTED",
        deterministic_generator="NOT_APPLICABLE_ONE_EXPLICIT_CANDIDATE_NO_STRATEGY_RANDOMNESS",
        random_seeds=(0,),
        primary_metric="EXPLORATORY_OPERATIONAL_VALIDATION",
        required_benchmark=BenchmarkSpec(
            benchmark_id=f"OWNER_SMOKE_002_{suffix.upper()}_NO_PROFITABILITY_BENCHMARK",
            definition=(
                "Structural protocol field only; no benchmark-dependent or profitability "
                "claim is authorized"
            ),
            # ResearchProtocol binds its required benchmark to the declared
            # Final Holdout even when this exploratory Trial executes only the
            # DEVELOPMENT interval and does not consume that Holdout.
            scored_interval=_future_interval(3),
            cost_basis="SAME_EXPLICIT_ESTIMATED_FEE_BASIS",
            frozen_before_result_exposure=True,
        ),
        selection_rule="ONLY_PREDECLARED_SMA20_CANDIDATE_NO_RANKING",
        tie_break_rule="NOT_APPLICABLE_SINGLE_CANDIDATE",
        development_interval=SCORING,
        validation_interval=_future_interval(1),
        oos_interval=_future_interval(2),
        final_holdout_interval=_future_interval(3),
        purge_embargo_rule=PurgeEmbargoRule(
            mode=NOT_APPLICABLE,
            reason="No trained model, label, or forward target in this operational validation",
            purge_seconds=0,
            embargo_seconds=0,
            max_forward_dependency_seconds=0,
        ),
        time_series_split="CHRONOLOGICAL",
        multiple_testing_treatment=NOT_APPLICABLE,
        sample_adequacy_rule=SampleAdequacyRule(
            counted_observation=NOT_APPLICABLE,
            minimum_completed_trades=NOT_APPLICABLE,
            rationale="Exploratory operational validation makes no profitability claim",
        ),
        monte_carlo_spec=MonteCarloSpec(
            resampling_method=ResamplingMethod.NOT_APPLICABLE,
            simulation_count=0,
            random_seed=0,
            block_length=NOT_APPLICABLE,
            quantile_method="R7_LINEAR_INTERPOLATION",
            decimal_places=8,
            not_applicable_reason=(
                "Exploratory operational validation makes no profitability claim"
            ),
        ),
        intended_claim_scope=ClaimScope.INSTRUMENT_ONLY,
        claim_basis=(
            "EXPLORATORY_OPERATIONAL_VALIDATION_ONLY; "
            "DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT; NO_FINAL_HOLDOUT; "
            "NO_REAL_PROFITABILITY_CLAIM"
        ),
        kill_criteria=(
            "MECHANICAL_INTEGRITY_NOT_PASS",
            "CHECKER_NOT_PASS",
            "DETERMINISTIC_REPLAY_NOT_PASS",
            "OFFLINE_BOUNDARY_NOT_PASS",
        ),
        terminal_policy="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
    )
    workflow = OwnerWorkflowInput(
        schema_version=1,
        workflow_purpose=OwnerWorkflowPurpose.OWNER_STUDY,
        protocol=protocol,
        trial_id=f"owner-smoke-002-{suffix}-sma20-development",
        candidate_id=candidate.candidate_id,
        run_id=f"owner-smoke-002-{suffix}-run",
        registered_strategy_id=REGISTRATION_ID,
        strategy_spec=spec,
        dataset_release_id=release.dataset_release_id,
        qualified_profile_record_id=_profile_record_id(profile),
        partition_role=PartitionRole.DEVELOPMENT,
        warmup_start=WARMUP_START,
        scoring_start=SCORING.start_inclusive,
        scoring_end_exclusive=SCORING.end_exclusive,
        initial_capital=MoneyAmount(amount=Decimal("10000"), currency="USDT"),
        fee_assumption=FeeAssumption(
            maker_fee=FEE_RATE,
            taker_fee=FEE_RATE,
            explicit_zero_fee=False,
            reason=FEE_REASON,
            claim_class="ESTIMATED_FEE",
        ),
        seed=0,
    )
    if (
        workflow.partition_role is not PartitionRole.DEVELOPMENT
        or workflow.scoring_start != SCORING.start_inclusive
        or workflow.scoring_end_exclusive != SCORING.end_exclusive
        or workflow.protocol.final_holdout_interval.start_inclusive
        < release.normalized_time_range.end_exclusive
    ):
        raise RuntimeError(
            "OWNER_SMOKE_002 must execute DEVELOPMENT only; its required structural "
            "Final Holdout declaration must remain outside the DatasetRelease and unused",
        )
    return workflow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-at-utc", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    frozen = datetime.fromisoformat(args.frozen_at_utc.replace("Z", "+00:00"))
    if frozen.tzinfo is None or frozen.utcoffset() != UTC.utcoffset(frozen):
        raise ValueError("frozen-at-utc must be explicit UTC")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    values = (
        _workflow(
            frozen_at_utc=frozen,
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            release_id=SPOT_RELEASE_ID,
        ),
        _workflow(
            frozen_at_utc=frozen,
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            release_id=PERPETUAL_RELEASE_ID,
        ),
    )
    for value in values:
        path = output / f"{value.trial_id}.json"
        path.write_bytes(value.to_json_bytes() + b"\n")
        # Parse from exact bytes to prove the strict public boundary round-trip.
        if OwnerWorkflowInput.from_json_bytes(path.read_bytes()) != value:
            raise RuntimeError(f"Owner Workflow input round-trip failed: {path}")
        print(
            f"{value.trial_id} protocol={value.protocol.protocol_id} "
            f"strategy_spec={value.strategy_spec.strategy_spec_id} release={value.dataset_release_id}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
