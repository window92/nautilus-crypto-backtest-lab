#!/usr/bin/env python3
"""Freeze OWNER_STRATEGY_RESEARCH_001 protocols and all six intended workflows."""

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
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
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
from crypto_lab.research import benchmark_trial_candidate_id
from crypto_lab.strategies import BUY_AND_HOLD_REGISTRATION_ID
from crypto_lab.strategies import TSMOM_FULL_REGISTRATION_ID
from crypto_lab.strategies import TSMOM_VOL20_REGISTRATION_ID
from crypto_lab.strategies import locked_buy_and_hold_strategy_spec
from crypto_lab.strategies import locked_weekly_tsmom_strategy_spec


ROOT = Path(__file__).resolve().parents[1]
EPOCH = "owner-strategy-research-001"
RESEARCH_FAMILY = "BTCUSDT_WEEKLY_TSMOM28_V1"
SPOT_RELEASE = "fd8542c109cfbf7d6b19d5b7bbb7705c6a161efc807695f3671978c381e34eca"
PERPETUAL_RELEASE = "b6c8f5d659f3441c924b613d770342796c90b90a970f42a3dc8227c856198917"
SPOT_CATALOG = "db0971d28caba547378e3acba5ad8df1cbd0d6d5be963d153248928a729e374f"
PERPETUAL_CATALOG = "7c96897a8e1ea3c02198238a277fb8c3d995f54dd90dc381e534a5f21b017ae0"
DUCKDB_SEMANTIC_IDENTITY = "11329c1497ff6bf3a68c5d3ba994f5ac2bbd0ece51cf489f9fa3f681a01ecbff"
BASELINE_COMMIT = "621caa3d71106f85f10015c54d0e31e75e0d42cd"
SSOT_SHA256 = "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99"
RUNTIME_LOCK_SHA256 = "4032df9f355348c2a0cfa9f79f331f97c9a8d24ecc8490a573d2c7f788bafddd"
DEPENDENCY_LOCK_SHA256 = "b2765c9e33b10566fc327b48920fd1d3a73618c19622baaceab1fe9dca61df47"
WARMUP_START = datetime(2021, 1, 1, tzinfo=UTC)
DEVELOPMENT = UtcInterval(
    start_inclusive=datetime(2021, 2, 1, tzinfo=UTC),
    end_exclusive=datetime(2021, 8, 1, tzinfo=UTC),
)
FEE = FeeAssumption(
    maker_fee=Decimal("0.001"),
    taker_fee=Decimal("0.001"),
    explicit_zero_fee=False,
    reason="SSOT Appendix A qualification-only observable estimated fee",
    claim_class="ESTIMATED_FEE",
)


def _future(day: int) -> UtcInterval:
    return UtcInterval(
        start_inclusive=datetime(2099, 1, day, tzinfo=UTC),
        end_exclusive=datetime(2099, 1, day + 1, tzinfo=UTC),
    )


def _profile_record_id(profile: MarketProfile) -> str:
    registry = QualifiedProfileRegistry.from_json_bytes(
        (ROOT / "evidence/m3/m3-acceptance-001/qualified-profile-registry.json").read_bytes(),
    )
    record = next(item for item in registry.records if item.profile_id is profile)
    if record.checker_result != "CHECK_PASS" or record.replay_result != "PASS":
        raise RuntimeError(f"unqualified Market Profile: {profile.value}")
    return record.qualified_profile_record_id


def _release(profile: MarketProfile) -> DatasetRelease:
    identity = SPOT_RELEASE if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else PERPETUAL_RELEASE
    release = DatasetRelease.from_json_bytes((ROOT / "data/releases" / f"{identity}.json").read_bytes())
    expected_catalog = SPOT_CATALOG if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else PERPETUAL_CATALOG
    if (
        release.market_profile is not profile
        or release.catalog_identity != expected_catalog
        or release.normalized_time_range.start_inclusive != WARMUP_START
        or release.normalized_time_range.end_exclusive != DEVELOPMENT.end_exclusive
    ):
        raise RuntimeError("locked DatasetRelease/catalog/window mismatch")
    return release


def build_protocol(
    profile: MarketProfile,
    *,
    frozen_at_utc: datetime,
) -> tuple[ResearchProtocol, tuple[OwnerWorkflowInput, ...]]:
    release = _release(profile)
    suffix = "spot" if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else "perpetual"
    benchmark_id = f"BUY_AND_HOLD_1X_V1_{suffix.upper()}"
    specs = (
        locked_weekly_tsmom_strategy_spec(TSMOM_FULL_REGISTRATION_ID, profile),
        locked_weekly_tsmom_strategy_spec(TSMOM_VOL20_REGISTRATION_ID, profile),
    )
    candidates = (
        CandidateSpec.create(
            candidate_label="TSMOM28_FULL_NOTIONAL",
            strategy_spec_id=specs[0].strategy_spec_id,
            parameter_values=dict(specs[0].parameters),
        ),
        CandidateSpec.create(
            candidate_label="TSMOM28_VOLATILITY_TARGET_20",
            strategy_spec_id=specs[1].strategy_spec_id,
            parameter_values=dict(specs[1].parameters),
        ),
    )
    benchmark_spec = locked_buy_and_hold_strategy_spec(profile, benchmark_id)
    parameter_names = sorted(set(specs[0].parameters) | set(specs[1].parameters))
    protocol = ResearchProtocol.create(
        frozen_at_utc=frozen_at_utc,
        research_family_id=RESEARCH_FAMILY,
        hypothesis_id=(
            "published-short-horizon-tsmom-with-mixed-bitcoin-oos-and-crash-risk-"
            f"fixed-full-versus-vol20-{suffix}"
        ),
        research_intent=ResearchIntent.EXPLORATORY,
        market_profile=profile,
        instrument_scope=InstrumentScope.SINGLE_INSTRUMENT,
        instrument_ids=(release.instrument_id,),
        instrument_selection_basis=(
            "Owner-locked BTCUSDT on qualified official Binance repaired DatasetRelease; "
            "EXPOSED_DEVELOPMENT_DATA; DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT"
        ),
        universe_selection_rule=NOT_APPLICABLE,
        universe_as_of_rule=NOT_APPLICABLE,
        universe_membership_sha256=NOT_APPLICABLE,
        dataset_release_ids=(release.dataset_release_id,),
        strategy_family=RESEARCH_FAMILY,
        ordered_candidates=candidates,
        parameter_domain={
            name: tuple(
                dict.fromkeys(
                    spec.parameters[name]
                    for spec in specs
                    if name in spec.parameters
                ),
            )
            for name in parameter_names
        },
        search_budget=2,
        candidate_ordering="AS_LISTED",
        deterministic_generator="EXACT_TWO_OWNER_LOCKED_CANDIDATES_NO_POST_RESULT_ADDITION",
        random_seeds=(0,),
        primary_metric="NATIVE_NET_PNL_EXPLORATORY_DIAGNOSTIC_ONLY",
        required_benchmark=BenchmarkSpec(
            benchmark_id=benchmark_id,
            definition=(
                f"registration_id={BUY_AND_HOLD_REGISTRATION_ID};"
                f"strategy_spec_id={benchmark_spec.strategy_spec_id};"
                "enter LONG 1x at first eligible scoring market state after 60s latency;"
                "hold through scoring_end_exclusive without synthetic close"
            ),
            scored_interval=DEVELOPMENT,
            cost_basis="SAME_INITIAL_EQUITY_DATASET_PROFILE_0.001_FEE_AND_TERMINAL_POLICY",
            frozen_before_result_exposure=True,
        ),
        selection_rule="NO_PUBLISHABLE_WINNER_SELECTION_EXPLORATORY_RESULTS_ONLY",
        tie_break_rule="NOT_APPLICABLE_NO_WINNER_SELECTION",
        development_interval=DEVELOPMENT,
        validation_interval=_future(1),
        oos_interval=_future(2),
        final_holdout_interval=_future(3),
        purge_embargo_rule=PurgeEmbargoRule(
            mode=NOT_APPLICABLE,
            reason="Causal completed-price signal has no forward label or training target",
            purge_seconds=0,
            embargo_seconds=0,
            max_forward_dependency_seconds=0,
        ),
        time_series_split="CHRONOLOGICAL",
        multiple_testing_treatment="HOLM_BONFERRONI",
        sample_adequacy_rule=SampleAdequacyRule(
            counted_observation=NOT_APPLICABLE,
            minimum_completed_trades=NOT_APPLICABLE,
            rationale=(
                "Exploratory exposed-development study; native completed units are disclosed "
                "but no confirmatory threshold may be retrofitted"
            ),
        ),
        monte_carlo_spec=MonteCarloSpec(
            resampling_method=ResamplingMethod.NOT_APPLICABLE,
            simulation_count=0,
            random_seed=0,
            block_length=NOT_APPLICABLE,
            quantile_method="R7_LINEAR_INTERPOLATION",
            decimal_places=8,
            not_applicable_reason=(
                "Exploratory exposed-development study makes no eligible confirmatory claim"
            ),
        ),
        intended_claim_scope=ClaimScope.INSTRUMENT_ONLY,
        claim_basis=(
            "EXPLORATORY_OPERATIONAL_VALIDATION; EXPLORATORY; "
            "EXPOSED_DEVELOPMENT_DATA; DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT; "
            "FINAL_HOLDOUT_USED_FALSE; REAL_PROFITABILITY_CLAIM_FALSE; "
            "OPTIMIZATION_PERFORMED_FALSE"
        ),
        kill_criteria=(
            "DATASET_OR_CATALOG_IDENTITY_MISMATCH",
            "MECHANICAL_INTEGRITY_NOT_PASS",
            "CHECKER_NOT_PASS",
            "DETERMINISTIC_REPLAY_NOT_PASS",
            "OFFLINE_BOUNDARY_NOT_PASS",
            "NO_MARKET_OR_PRECISION_REJECTION",
        ),
        terminal_policy="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
    )
    common = dict(
        schema_version=1,
        protocol=protocol,
        dataset_release_id=release.dataset_release_id,
        qualified_profile_record_id=_profile_record_id(profile),
        partition_role=PartitionRole.DEVELOPMENT,
        warmup_start=WARMUP_START,
        scoring_start=DEVELOPMENT.start_inclusive,
        scoring_end_exclusive=DEVELOPMENT.end_exclusive,
        initial_capital=MoneyAmount(amount=Decimal("10000"), currency="USDT"),
        fee_assumption=FEE,
        seed=0,
    )
    benchmark_workflow = OwnerWorkflowInput(
        **common,
        workflow_purpose=OwnerWorkflowPurpose.BENCHMARK_STUDY,
        trial_id=f"{EPOCH}-{suffix}-benchmark-buy-and-hold-1x-development",
        candidate_id=benchmark_trial_candidate_id(
            protocol.required_benchmark,
            strategy_spec_id=benchmark_spec.strategy_spec_id,
        ),
        run_id=f"{EPOCH}-{suffix}-benchmark-run",
        registered_strategy_id=BUY_AND_HOLD_REGISTRATION_ID,
        strategy_spec=benchmark_spec,
    )
    candidate_workflows = tuple(
        OwnerWorkflowInput(
            **common,
            workflow_purpose=OwnerWorkflowPurpose.OWNER_STUDY,
            trial_id=f"{EPOCH}-{suffix}-candidate-{label}-development",
            candidate_id=candidate.candidate_id,
            run_id=f"{EPOCH}-{suffix}-candidate-{label}-run",
            registered_strategy_id=registration,
            strategy_spec=spec,
        )
        for label, registration, spec, candidate in (
            ("a", TSMOM_FULL_REGISTRATION_ID, specs[0], candidates[0]),
            ("b", TSMOM_VOL20_REGISTRATION_ID, specs[1], candidates[1]),
        )
    )
    return protocol, (benchmark_workflow, *candidate_workflows)


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
    protocols_dir = output / "frozen-protocols"
    workflows_dir = output / "frozen-workflows"
    specs_dir = output / "strategy-specs"
    for directory in (protocols_dir, workflows_dir, specs_dir):
        directory.mkdir()

    locked_files = {
        "SSOT.md": SSOT_SHA256,
        "runtime.lock.json": RUNTIME_LOCK_SHA256,
        "requirements.lock.txt": DEPENDENCY_LOCK_SHA256,
    }
    for relative, expected in locked_files.items():
        if sha256_file(ROOT / relative) != expected:
            raise RuntimeError(f"locked identity mismatch while freezing research: {relative}")

    baseline = {
        "schema": "owner-strategy-research-001-baseline-attestation-v1",
        "verified_before_first_write": True,
        "verified_user": "builder",
        "repository_path": str(ROOT),
        "branch": "main",
        "head": BASELINE_COMMIT,
        "origin_main": BASELINE_COMMIT,
        "git_status_at_cold_start": "CLEAN",
        "ssot_sha256": SSOT_SHA256,
        "runtime_lock_sha256": RUNTIME_LOCK_SHA256,
        "dependency_lock_sha256": DEPENDENCY_LOCK_SHA256,
        "required_ancestor_commits": [
            "39507102ebf42264928492b8db0b8d1ecaf3e931",
            BASELINE_COMMIT,
        ],
        "result_bearing_trial_accessed": False,
    }
    (output / "baseline-attestation.json").write_bytes(canonical_json_bytes(baseline) + b"\n")

    authorization = {
        "schema": "owner-strategy-research-001-authorization-v1",
        "authorization_id": "OWNER_STRATEGY_RESEARCH_001",
        "research_purpose": "EXPLORATORY_OPERATIONAL_VALIDATION",
        "research_intent": "EXPLORATORY",
        "final_holdout_used": False,
        "real_profitability_claim": False,
        "optimization_performed": False,
        "candidate_budget": 2,
        "benchmark_count": 2,
        "network_permitted": False,
        "data_reacquisition_permitted": False,
        "window_search_permitted": False,
        "ssot_runtime_dependency_changes_permitted": False,
        "authoritative_workflow_required": True,
    }
    (output / "owner-authorization.json").write_bytes(
        canonical_json_bytes(authorization) + b"\n",
    )

    inventory: list[dict[str, object]] = []
    protocols: list[ResearchProtocol] = []
    workflows: list[OwnerWorkflowInput] = []
    for profile in (
        MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
    ):
        protocol, profile_workflows = build_protocol(profile, frozen_at_utc=frozen)
        protocols.append(protocol)
        workflows.extend(profile_workflows)
        suffix = "spot" if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else "perpetual"
        protocol_path = protocols_dir / f"{suffix}.json"
        protocol_path.write_bytes(protocol.to_json_bytes() + b"\n")
        for workflow in profile_workflows:
            workflow_path = workflows_dir / f"{workflow.trial_id}.json"
            workflow_path.write_bytes(workflow.to_json_bytes() + b"\n")
            spec_path = specs_dir / f"{workflow.trial_id}.json"
            spec_path.write_bytes(workflow.strategy_spec.to_json_bytes() + b"\n")

    basis = {
        "schema": "owner-strategy-research-001-basis-v1",
        "research_family_id": RESEARCH_FAMILY,
        "research_purpose": "EXPLORATORY_OPERATIONAL_VALIDATION",
        "research_intent": "EXPLORATORY",
        "final_holdout_used": False,
        "real_profitability_claim": False,
        "optimization_performed": False,
        "candidate_budget": 2,
        "candidate_registration_ids": [TSMOM_FULL_REGISTRATION_ID, TSMOM_VOL20_REGISTRATION_ID],
        "benchmark_registration_id": BUY_AND_HOLD_REGISTRATION_ID,
        "multiple_testing_policy": "HOLM_BONFERRONI",
        "source_urls": [
            "https://www.nber.org/papers/w24877",
            "https://onlinelibrary.wiley.com/doi/10.1111/fima.12310",
            "https://doi.org/10.1007/s10479-019-03357-1",
            "https://link.springer.com/article/10.1007/s11408-025-00474-9",
        ],
        "balanced_conclusion": [
            "Published evidence supports testing short-horizon Time-Series Momentum.",
            "Published evidence for Bitcoin technical strategies is mixed out of sample.",
            "Cryptocurrency Momentum can experience severe crashes.",
            "Volatility management is a research candidate, not an assumed improvement.",
            "No cited paper establishes profitability for either frozen candidate in this laboratory.",
        ],
        "network_used_for_sources": False,
        "spot_dataset_release_id": SPOT_RELEASE,
        "perpetual_dataset_release_id": PERPETUAL_RELEASE,
        "spot_catalog_identity": SPOT_CATALOG,
        "perpetual_catalog_identity": PERPETUAL_CATALOG,
        "duckdb_semantic_identity": DUCKDB_SEMANTIC_IDENTITY,
        "warmup_start": "2021-01-01T00:00:00Z",
        "scoring_interval": DEVELOPMENT.to_builtins(),
        "data_classification": [
            "EXPOSED_DEVELOPMENT_DATA",
            "DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT",
        ],
        "protocol_ids": [protocol.protocol_id for protocol in protocols],
        "workflow_trial_ids": [workflow.trial_id for workflow in workflows],
    }
    (output / "research-basis.json").write_bytes(canonical_json_bytes(basis) + b"\n")

    data_bindings = {
        "schema": "owner-strategy-research-001-data-bindings-v1",
        "spot": {
            "market_profile": MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY.value,
            "dataset_release_id": SPOT_RELEASE,
            "catalog_identity": SPOT_CATALOG,
        },
        "perpetual": {
            "market_profile": (
                MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value
            ),
            "dataset_release_id": PERPETUAL_RELEASE,
            "catalog_identity": PERPETUAL_CATALOG,
        },
        "duckdb_semantic_identity": DUCKDB_SEMANTIC_IDENTITY,
        "dataset_start_inclusive": "2021-01-01T00:00:00Z",
        "scoring_start_inclusive": "2021-02-01T00:00:00Z",
        "dataset_and_scoring_end_exclusive": "2021-08-01T00:00:00Z",
        "classification": [
            "EXPOSED_DEVELOPMENT_DATA",
            "DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT",
        ],
        "canonical_data_or_catalog_modified": False,
    }
    (output / "data-and-profile-identities.json").write_bytes(
        canonical_json_bytes(data_bindings) + b"\n",
    )

    failed_attempts = (
        {
            "sequence": 1,
            "phase": "TARGETED_TEST_INVOCATION",
            "disposition": "NON_PRODUCT_COMMAND_INVOCATION_CORRECTED",
            "failure": "initial unittest invocation omitted PYTHONPATH=src",
            "preserved_effect": "no result-bearing trial executed and no product bytes changed",
        },
        {
            "sequence": 2,
            "phase": "PINNED_RUNTIME_API_QUALIFICATION",
            "disposition": "PRODUCT_DEFECT_FIXED_BEFORE_PROTOCOL_FREEZE",
            "failure": (
                "pinned Nautilus Portfolio.equity does not accept the newer target_currency "
                "argument assumed by the first local qualification"
            ),
            "preserved_effect": "native public API retained; no project equity or PnL engine added",
        },
        {
            "sequence": 3,
            "phase": "REGRESSION_DISCOVERY",
            "disposition": "PRODUCT_AND_TEST_CONTRACTS_FIXED_BEFORE_PROTOCOL_FREEZE",
            "failure": (
                "first full discovery exposed stale registered-strategy expectations, a benchmark "
                "interval assertion inconsistent with the scored-partition contract, and an "
                "over-broad benchmark-order rule affecting qualification fixtures"
            ),
            "preserved_effect": "all failures retained in this additive log; no candidate result accessed",
        },
        {
            "sequence": 4,
            "phase": "RUNTIME_PREFLIGHT",
            "disposition": "OUTPUT_PATH_COLLISION_NO_PREFLIGHT_BYPASS",
            "failure": (
                "default preflight output targeted historical OWNER_SMOKE_001 evidence and "
                "correctly refused overwrite"
            ),
            "preserved_effect": (
                "historical bytes remained unchanged; epoch-specific preflight deferred until "
                "the implementation checkpoint is clean"
            ),
        },
        {
            "sequence": 5,
            "phase": "TARGETED_OWNER_WORKFLOW_REGRESSION",
            "disposition": "NON_PRODUCT_COMMAND_INVOCATION_CORRECTED",
            "failure": (
                "the first targeted command used the nonexistent module name "
                "tests.adversarial.test_aud009_public_owner_workflow"
            ),
            "preserved_effect": (
                "the actual tests.adversarial.test_aud009_owner_workflow module was then "
                "executed successfully; no result-bearing trial was run"
            ),
        },
        {
            "sequence": 6,
            "phase": "FULL_REGRESSION_DISCOVERY",
            "disposition": "LOCKED_RUNTIME_ENVIRONMENT_INVOCATION_CORRECTED",
            "failure": (
                "the first full-discovery process omitted locked TZ=UTC and LC_ALL=C.UTF-8; "
                "the runtime preflight therefore fail-closed with RUNTIME_LOCK_MISMATCH"
            ),
            "preserved_effect": (
                "a single golden test reproduced the cause and passed under the locked environment; "
                "the complete suite is rerun under the exact runtime environment"
            ),
        },
    )
    (output / "failed-attempts.jsonl").write_bytes(
        b"".join(canonical_json_bytes(item) + b"\n" for item in failed_attempts),
    )
    for path in sorted(output.rglob("*")):
        if path.is_file():
            inventory.append(
                {
                    "path": str(path.relative_to(output)),
                    "sha256": sha256_file(path),
                    "byte_size": path.stat().st_size,
                },
            )
    manifest = {
        "schema": "owner-strategy-research-001-frozen-input-manifest-v1",
        "frozen_at_utc": frozen.isoformat().replace("+00:00", "Z"),
        "research_family_id": RESEARCH_FAMILY,
        "protocol_count": len(protocols),
        "candidate_count": 2,
        "candidate_profile_trial_count": 4,
        "benchmark_trial_count": 2,
        "result_accessed": False,
        "inventory": inventory,
    }
    manifest["manifest_identity"] = canonical_sha256(manifest)
    (output / "frozen-input-manifest.json").write_bytes(canonical_json_bytes(manifest) + b"\n")
    print(canonical_json_bytes({"protocol_ids": basis["protocol_ids"], "trials": basis["workflow_trial_ids"]}).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
