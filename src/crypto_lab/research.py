"""M4 research-governance contracts over immutable completed evidence.

This module never imports the data builder or Nautilus runner.  It records
research intent, history, exposure, and diagnostics inputs; Nautilus remains
the only owner of trading and financial truth.
"""

from __future__ import annotations

import json
import os
import random
import tempfile
from dataclasses import fields
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from decimal import ROUND_HALF_EVEN
from enum import StrEnum
from pathlib import Path
from typing import Any

from crypto_lab.config import MarketProfile
from crypto_lab.config import NOT_APPLICABLE
from crypto_lab.config import StrictModel
from crypto_lab.config import _freeze_field
from crypto_lab.config import _require_nonempty
from crypto_lab.config import _require_sha256
from crypto_lab.config import _require_utc
from crypto_lab.hashing import canonical_sha256
from crypto_lab.m3 import MechanicalIntegrity
from crypto_lab.m3 import QualificationDownstreamBundle
from crypto_lab.m3 import QualifiedProfileRecord
from crypto_lab.m3 import QualifiedProfileRegistry


class ResearchError(ValueError):
    """Fail-closed M4 contract error with an SSOT failure code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class ResearchIntent(StrEnum):
    EXPLORATORY = "EXPLORATORY"
    CONFIRMATORY = "CONFIRMATORY"


class ResearchEligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    EXPLORATORY_ONLY = "EXPLORATORY_ONLY"
    INELIGIBLE = "INELIGIBLE"
    BLOCKED = "BLOCKED"


class InstrumentScope(StrEnum):
    SINGLE_INSTRUMENT = "SINGLE_INSTRUMENT"
    FROZEN_INSTRUMENT_SET = "FROZEN_INSTRUMENT_SET"
    POINT_IN_TIME_UNIVERSE = "POINT_IN_TIME_UNIVERSE"


class ClaimScope(StrEnum):
    INSTRUMENT_ONLY = "INSTRUMENT_ONLY"
    FROZEN_SET_ONLY = "FROZEN_SET_ONLY"
    POINT_IN_TIME_UNIVERSE = "POINT_IN_TIME_UNIVERSE"


class PartitionRole(StrEnum):
    DEVELOPMENT = "DEVELOPMENT"
    VALIDATION = "VALIDATION"
    OOS = "OOS"
    FINAL_HOLDOUT = "FINAL_HOLDOUT"


class TrialState(StrEnum):
    PLANNED = "PLANNED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    ABORTED = "ABORTED"


TERMINAL_TRIAL_STATES = (
    TrialState.COMPLETED,
    TrialState.FAILED,
    TrialState.BLOCKED,
    TrialState.ABORTED,
)


class SampleAdequacy(StrEnum):
    ADEQUATE = "ADEQUATE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class MonteCarloStatus(StrEnum):
    COMPLETED = "COMPLETED"
    MC_LOW_CONFIDENCE = "MC_LOW_CONFIDENCE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ResamplingMethod(StrEnum):
    IID_BOOTSTRAP = "IID_BOOTSTRAP"
    MOVING_BLOCK_BOOTSTRAP = "MOVING_BLOCK_BOOTSTRAP"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class UtcInterval(StrictModel):
    start_inclusive: datetime
    end_exclusive: datetime

    def __post_init__(self) -> None:
        _require_utc(self.start_inclusive, "interval.start_inclusive")
        _require_utc(self.end_exclusive, "interval.end_exclusive")
        if self.start_inclusive >= self.end_exclusive:
            raise ResearchError("PARTITION_LEAKAGE", "interval must be non-empty and half-open")

    def overlaps(self, other: UtcInterval) -> bool:
        return self.start_inclusive < other.end_exclusive and other.start_inclusive < self.end_exclusive


class CandidateSpec(StrictModel):
    candidate_id: str
    candidate_label: str
    strategy_spec_id: str
    parameter_values: dict[str, str]

    def __post_init__(self) -> None:
        _require_sha256(self.candidate_id, "candidate.candidate_id")
        _require_sha256(self.strategy_spec_id, "candidate.strategy_spec_id")
        _require_nonempty(self.candidate_label, "candidate.candidate_label")
        if not self.parameter_values:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "candidate parameters must not be empty")
        for key, value in self.parameter_values.items():
            _require_nonempty(key, "candidate.parameter_name")
            _require_nonempty(value, f"candidate.parameter_values.{key}")
        if canonical_sha256(self.material_payload()) != self.candidate_id:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "candidate content identity mismatch")
        _freeze_field(self, "parameter_values")

    def material_payload(self) -> dict[str, Any]:
        return {
            "parameter_values": dict(self.parameter_values),
            "strategy_spec_id": self.strategy_spec_id,
        }

    @classmethod
    def create(
        cls,
        *,
        candidate_label: str,
        strategy_spec_id: str,
        parameter_values: dict[str, str],
    ) -> CandidateSpec:
        material = {
            "parameter_values": dict(parameter_values),
            "strategy_spec_id": strategy_spec_id,
        }
        return cls(
            candidate_id=canonical_sha256(material),
            candidate_label=candidate_label,
            strategy_spec_id=strategy_spec_id,
            parameter_values=parameter_values,
        )


class BenchmarkSpec(StrictModel):
    benchmark_id: str
    definition: str
    scored_interval: UtcInterval
    cost_basis: str
    frozen_before_result_exposure: bool

    def __post_init__(self) -> None:
        for name in ("benchmark_id", "definition", "cost_basis"):
            _require_nonempty(getattr(self, name), f"benchmark.{name}")
        if not self.frozen_before_result_exposure:
            raise ResearchError(
                "RESEARCH_PROTOCOL_INVALID",
                "benchmark must be bound before result exposure",
            )
        if self.cost_basis in {"UNKNOWN", NOT_APPLICABLE}:
            raise ResearchError(
                "RESEARCH_PROTOCOL_INVALID",
                "benchmark cost basis must be explicit and compatible",
            )


def benchmark_trial_candidate_id(
    benchmark: BenchmarkSpec,
    *,
    strategy_spec_id: str,
) -> str:
    """Return the journal identity for a benchmark without consuming candidate budget."""

    _require_sha256(strategy_spec_id, "benchmark.strategy_spec_id")
    return canonical_sha256(
        {
            "benchmark": benchmark.to_builtins(),
            "strategy_spec_id": strategy_spec_id,
            "trial_role": "REGISTERED_BENCHMARK_NOT_RESEARCH_CANDIDATE",
        },
    )


class UniverseMembershipDecision(StrictModel):
    instrument_id: str
    selected: bool
    selection_timestamp_utc: datetime
    source_observed_at_utc: datetime
    official_source_reference: str
    source_content_sha256: str
    available_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_nonempty(self.instrument_id, "universe_membership.instrument_id")
        _require_utc(self.selection_timestamp_utc, "universe_membership.selection_timestamp")
        _require_utc(self.source_observed_at_utc, "universe_membership.source_observed_at")
        _require_nonempty(
            self.official_source_reference,
            "universe_membership.official_source_reference",
        )
        _require_sha256(self.source_content_sha256, "universe_membership.source_content_sha256")
        if not self.available_fields:
            raise ResearchError(
                "RESEARCH_PROTOCOL_INVALID",
                "point-in-time membership requires source fields",
            )
        if self.source_observed_at_utc > self.selection_timestamp_utc:
            raise ResearchError(
                "RESEARCH_PROTOCOL_INVALID",
                "membership used information unavailable at selection time",
            )


class UniverseMembershipEvidence(StrictModel):
    schema_version: int
    decisions: tuple[UniverseMembershipDecision, ...]
    membership_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != 1 or not self.decisions:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "invalid universe membership evidence")
        _require_sha256(self.membership_sha256, "universe_membership.membership_sha256")
        keys = [
            (item.instrument_id, item.selection_timestamp_utc)
            for item in self.decisions
        ]
        if len(keys) != len(set(keys)):
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "duplicate universe decision")
        if canonical_sha256(self.material_payload()) != self.membership_sha256:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "universe membership identity mismatch")

    def material_payload(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "decisions": self.decisions}

    @classmethod
    def create(
        cls,
        decisions: tuple[UniverseMembershipDecision, ...],
    ) -> UniverseMembershipEvidence:
        material = {"schema_version": 1, "decisions": decisions}
        return cls(
            schema_version=1,
            decisions=decisions,
            membership_sha256=canonical_sha256(material),
        )


class PurgeEmbargoRule(StrictModel):
    mode: str
    reason: str
    purge_seconds: int
    embargo_seconds: int
    max_forward_dependency_seconds: int

    def __post_init__(self) -> None:
        if self.mode not in {"APPLICABLE", NOT_APPLICABLE}:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "unknown purge/embargo mode")
        _require_nonempty(self.reason, "purge_embargo.reason")
        if min(self.purge_seconds, self.embargo_seconds, self.max_forward_dependency_seconds) < 0:
            raise ResearchError("PARTITION_LEAKAGE", "purge and embargo durations cannot be negative")
        if self.mode == NOT_APPLICABLE:
            if self.reason == NOT_APPLICABLE or self.max_forward_dependency_seconds != 0:
                raise ResearchError(
                    "PARTITION_LEAKAGE",
                    "NOT_APPLICABLE requires a reason and zero forward dependency",
                )
        elif self.purge_seconds + self.embargo_seconds < self.max_forward_dependency_seconds:
            raise ResearchError("PARTITION_LEAKAGE", "purge and embargo are insufficient")


class SampleAdequacyRule(StrictModel):
    counted_observation: str
    minimum_completed_trades: int | str
    rationale: str

    def __post_init__(self) -> None:
        _require_nonempty(self.counted_observation, "sample_adequacy.counted_observation")
        _require_nonempty(self.rationale, "sample_adequacy.rationale")
        if isinstance(self.minimum_completed_trades, int):
            if self.minimum_completed_trades <= 0:
                raise ResearchError(
                    "RESEARCH_PROTOCOL_INVALID",
                    "minimum_completed_trades must be a positive integer",
                )
            if self.counted_observation != "NAUTILUS_NATIVE_COMPLETED_TRADE":
                raise ResearchError(
                    "RESEARCH_PROTOCOL_INVALID",
                    "sample unit must be the native Nautilus completed trade",
                )
        elif self.minimum_completed_trades != NOT_APPLICABLE:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "invalid sample adequacy rule")


class MonteCarloSpec(StrictModel):
    resampling_method: ResamplingMethod
    simulation_count: int
    random_seed: int
    block_length: int | str
    quantile_method: str
    decimal_places: int
    not_applicable_reason: str

    def __post_init__(self) -> None:
        if self.quantile_method != "R7_LINEAR_INTERPOLATION":
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "unsupported quantile method")
        if not 0 <= self.decimal_places <= 18:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "invalid Monte Carlo precision")
        if self.resampling_method is ResamplingMethod.NOT_APPLICABLE:
            if self.simulation_count != 0 or self.block_length != NOT_APPLICABLE:
                raise ResearchError("RESEARCH_PROTOCOL_INVALID", "NOT_APPLICABLE Monte Carlo must not run")
            if self.not_applicable_reason == NOT_APPLICABLE:
                raise ResearchError("RESEARCH_PROTOCOL_INVALID", "Monte Carlo N/A requires a reason")
            return
        if self.simulation_count <= 0:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "simulation_count must be positive")
        if self.resampling_method is ResamplingMethod.IID_BOOTSTRAP:
            if self.block_length != NOT_APPLICABLE:
                raise ResearchError("RESEARCH_PROTOCOL_INVALID", "IID block length must be N/A")
        elif not isinstance(self.block_length, int) or self.block_length <= 0:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "moving-block length must be positive")
        if self.not_applicable_reason != NOT_APPLICABLE:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "applicable Monte Carlo reason must be N/A")


def validate_partition_boundaries(
    development: UtcInterval,
    validation: UtcInterval,
    oos: UtcInterval,
    final_holdout: UtcInterval,
    purge_embargo: PurgeEmbargoRule,
) -> None:
    ordered = (development, validation, oos, final_holdout)
    if any(left.end_exclusive > right.start_inclusive for left, right in zip(ordered, ordered[1:])):
        raise ResearchError("PARTITION_LEAKAGE", "chronological partitions overlap")
    PurgeEmbargoRule.from_json_bytes(purge_embargo.to_json_bytes())


class PartitionStartEvidence(StrictModel):
    """Read-only boundary evidence; it never carries financial state forward."""

    partition_role: PartitionRole
    configured_initial_capital: Decimal
    observed_starting_cash: Decimal
    observed_starting_position_quantity: Decimal
    observed_starting_realized_pnl: Decimal
    pending_strategy_orders: int
    warmup_scored_order_count: int
    scoring_start_utc: datetime
    warmup_context_end_exclusive: datetime
    source: str

    def __post_init__(self) -> None:
        for name in (
            "configured_initial_capital",
            "observed_starting_cash",
            "observed_starting_position_quantity",
            "observed_starting_realized_pnl",
        ):
            if not getattr(self, name).is_finite():
                raise ResearchError("PARTITION_LEAKAGE", f"{name} must be finite")
        if self.configured_initial_capital <= 0:
            raise ResearchError("PARTITION_LEAKAGE", "Initial Capital must be positive")
        if self.observed_starting_cash != self.configured_initial_capital:
            raise ResearchError("PARTITION_LEAKAGE", "scored partition did not reset Initial Capital")
        if self.observed_starting_position_quantity != 0:
            raise ResearchError("PARTITION_LEAKAGE", "position carried into scored partition")
        if self.observed_starting_realized_pnl != 0:
            raise ResearchError("PARTITION_LEAKAGE", "PnL carried into scored partition")
        if self.pending_strategy_orders != 0:
            raise ResearchError("PARTITION_LEAKAGE", "pending order carried into scored partition")
        if self.warmup_scored_order_count != 0:
            raise ResearchError("PARTITION_LEAKAGE", "warmup submitted a scored order")
        _require_utc(self.scoring_start_utc, "partition_start.scoring_start")
        _require_utc(
            self.warmup_context_end_exclusive,
            "partition_start.warmup_context_end_exclusive",
        )
        if self.warmup_context_end_exclusive > self.scoring_start_utc:
            raise ResearchError("PARTITION_LEAKAGE", "warmup used later-partition context")
        if self.source != "NAUTILUS_PERSISTED_RUN_EVIDENCE":
            raise ResearchError("PARTITION_LEAKAGE", "partition state must come from Run evidence")


class ResearchProtocol(StrictModel):
    schema_version: int
    protocol_id: str
    frozen_at_utc: datetime
    research_family_id: str
    hypothesis_id: str
    research_intent: ResearchIntent
    market_profile: MarketProfile
    instrument_scope: InstrumentScope
    instrument_ids: tuple[str, ...]
    instrument_selection_basis: str
    universe_selection_rule: str
    universe_as_of_rule: str
    universe_membership_sha256: str
    dataset_release_ids: tuple[str, ...]
    strategy_family: str
    ordered_candidates: tuple[CandidateSpec, ...]
    parameter_domain: dict[str, tuple[str, ...]]
    search_budget: int
    candidate_ordering: str
    deterministic_generator: str
    random_seeds: tuple[int, ...]
    primary_metric: str
    required_benchmark: BenchmarkSpec
    selection_rule: str
    tie_break_rule: str
    development_interval: UtcInterval
    validation_interval: UtcInterval
    oos_interval: UtcInterval
    final_holdout_interval: UtcInterval
    purge_embargo_rule: PurgeEmbargoRule
    time_series_split: str
    multiple_testing_treatment: str
    sample_adequacy_rule: SampleAdequacyRule
    monte_carlo_spec: MonteCarloSpec
    intended_claim_scope: ClaimScope
    claim_basis: str
    kill_criteria: tuple[str, ...]
    terminal_policy: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "only ResearchProtocol v1 is supported")
        _require_sha256(self.protocol_id, "research_protocol.protocol_id")
        _require_utc(self.frozen_at_utc, "research_protocol.frozen_at_utc")
        for name in (
            "research_family_id",
            "hypothesis_id",
            "instrument_selection_basis",
            "strategy_family",
            "candidate_ordering",
            "deterministic_generator",
            "primary_metric",
            "selection_rule",
            "tie_break_rule",
            "multiple_testing_treatment",
            "claim_basis",
            "terminal_policy",
        ):
            _require_nonempty(getattr(self, name), f"research_protocol.{name}")
        if not self.instrument_ids or len(set(self.instrument_ids)) != len(self.instrument_ids):
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "Instrument scope must be non-empty and unique")
        if self.instrument_scope is InstrumentScope.SINGLE_INSTRUMENT and len(self.instrument_ids) != 1:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "SINGLE_INSTRUMENT requires exactly one Instrument")
        if self.instrument_scope is InstrumentScope.POINT_IN_TIME_UNIVERSE:
            if (
                self.universe_selection_rule == NOT_APPLICABLE
                or self.universe_as_of_rule == NOT_APPLICABLE
                or self.universe_membership_sha256 == NOT_APPLICABLE
            ):
                raise ResearchError(
                    "RESEARCH_PROTOCOL_INVALID",
                    "point-in-time universe requires frozen rule, as-of rule, and membership identity",
                )
            _require_sha256(
                self.universe_membership_sha256,
                "research_protocol.universe_membership_sha256",
            )
        elif any(
            item != NOT_APPLICABLE
            for item in (
                self.universe_selection_rule,
                self.universe_as_of_rule,
                self.universe_membership_sha256,
            )
        ):
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "non-universe scope must mark universe fields N/A")
        if not self.dataset_release_ids:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "Dataset Release identity is required")
        for identity in self.dataset_release_ids:
            _require_sha256(identity, "research_protocol.dataset_release_ids")
        if not self.ordered_candidates or len({item.candidate_id for item in self.ordered_candidates}) != len(
            self.ordered_candidates
        ):
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "candidate domain must be non-empty and unique")
        if self.search_budget <= 0 or self.search_budget > len(self.ordered_candidates):
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "search budget exceeds frozen candidates")
        if self.candidate_ordering != "AS_LISTED":
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "V1 ordered set must use AS_LISTED")
        if not self.random_seeds:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "random seeds must be frozen")
        for candidate in self.ordered_candidates:
            for name, domain in self.parameter_domain.items():
                if name in candidate.parameter_values and candidate.parameter_values[name] not in domain:
                    raise ResearchError("RESEARCH_PROTOCOL_INVALID", "candidate lies outside frozen domain")
        validate_partition_boundaries(
            self.development_interval,
            self.validation_interval,
            self.oos_interval,
            self.final_holdout_interval,
            self.purge_embargo_rule,
        )
        if self.required_benchmark.scored_interval not in (
            self.development_interval,
            self.validation_interval,
            self.oos_interval,
            self.final_holdout_interval,
        ):
            raise ResearchError(
                "RESEARCH_PROTOCOL_INVALID",
                "benchmark interval must match one declared scored partition",
            )
        if self.time_series_split != "CHRONOLOGICAL":
            raise ResearchError("PARTITION_LEAKAGE", "random time-series shuffling is forbidden")
        if self.research_intent is ResearchIntent.CONFIRMATORY and not isinstance(
            self.sample_adequacy_rule.minimum_completed_trades,
            int,
        ):
            raise ResearchError(
                "RESEARCH_PROTOCOL_INVALID",
                "confirmatory trade claim requires a positive frozen sample threshold",
            )
        if (
            self.research_intent is ResearchIntent.CONFIRMATORY
            and self.claim_basis == "TRADE_BASED_PROFITABILITY"
            and self.monte_carlo_spec.resampling_method is ResamplingMethod.NOT_APPLICABLE
        ):
            raise ResearchError(
                "RESEARCH_PROTOCOL_INVALID",
                "confirmatory trade claim must freeze an applicable Monte Carlo specification",
            )
        if len(self.ordered_candidates) > 1 and self.multiple_testing_treatment == NOT_APPLICABLE:
            raise ResearchError(
                "MULTIPLE_TESTING_UNDECLARED",
                "NOT_APPLICABLE is valid only for one predeclared candidate",
            )
        if self.terminal_policy != "MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE":
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "terminal policy differs from V1")
        if canonical_sha256(self.material_payload()) != self.protocol_id:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "protocol content identity mismatch")
        _freeze_field(self, "instrument_ids")
        _freeze_field(self, "dataset_release_ids")
        _freeze_field(self, "ordered_candidates")
        _freeze_field(self, "parameter_domain")
        _freeze_field(self, "random_seeds")
        _freeze_field(self, "kill_criteria")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name not in {"protocol_id", "frozen_at_utc"}
        }

    @classmethod
    def create(cls, *, frozen_at_utc: datetime, **values: Any) -> ResearchProtocol:
        material = {"schema_version": 1, **values}
        return cls(
            schema_version=1,
            protocol_id=canonical_sha256(material),
            frozen_at_utc=frozen_at_utc,
            **values,
        )

    @classmethod
    def create_from(
        cls,
        protocol: ResearchProtocol,
        *,
        frozen_at_utc: datetime | None = None,
        **changes: Any,
    ) -> ResearchProtocol:
        values = {
            field.name: getattr(protocol, field.name)
            for field in fields(protocol)
            if field.name not in {"schema_version", "protocol_id", "frozen_at_utc"}
        }
        values.update(changes)
        return cls.create(
            frozen_at_utc=frozen_at_utc or protocol.frozen_at_utc,
            **values,
        )


class TrialDefinition(StrictModel):
    trial_id: str
    research_family_id: str
    hypothesis_id: str
    protocol_id: str
    candidate_id: str
    candidate_parameters_sha256: str
    run_id: str
    config_sha256: str
    strategy_spec_id: str
    dataset_release_id: str
    partition_role: PartitionRole
    seed: int
    market_profile: MarketProfile
    instrument_id: str
    scored_interval: UtcInterval

    def __post_init__(self) -> None:
        for name in ("trial_id", "research_family_id", "hypothesis_id", "run_id", "instrument_id"):
            _require_nonempty(getattr(self, name), f"trial_definition.{name}")
        for name in (
            "protocol_id",
            "candidate_id",
            "candidate_parameters_sha256",
            "config_sha256",
            "strategy_spec_id",
            "dataset_release_id",
        ):
            _require_sha256(getattr(self, name), f"trial_definition.{name}")

    @classmethod
    def synthetic(
        cls,
        *,
        trial_id: str,
        protocol: ResearchProtocol,
        candidate: CandidateSpec,
        run_id: str,
    ) -> TrialDefinition:
        return cls(
            trial_id=trial_id,
            research_family_id=protocol.research_family_id,
            hypothesis_id=protocol.hypothesis_id,
            protocol_id=protocol.protocol_id,
            candidate_id=candidate.candidate_id,
            candidate_parameters_sha256=canonical_sha256(dict(candidate.parameter_values)),
            run_id=run_id,
            config_sha256=canonical_sha256(
                {
                    "candidate_id": candidate.candidate_id,
                    "dataset_release_id": protocol.dataset_release_ids[0],
                    "protocol_id": protocol.protocol_id,
                },
            ),
            strategy_spec_id=candidate.strategy_spec_id,
            dataset_release_id=protocol.dataset_release_ids[0],
            partition_role=PartitionRole.FINAL_HOLDOUT,
            seed=protocol.random_seeds[0],
            market_profile=protocol.market_profile,
            instrument_id=protocol.instrument_ids[0],
            scored_interval=protocol.final_holdout_interval,
        )


class TrialRecord(StrictModel):
    schema_version: int
    journal_sequence: int
    previous_entry_sha256: str
    journal_entry_sha256: str
    trial_id: str
    research_family_id: str
    hypothesis_id: str
    protocol_id: str
    candidate_id: str
    candidate_parameters_sha256: str
    run_id: str
    config_sha256: str
    strategy_spec_id: str
    dataset_release_id: str
    partition_role: PartitionRole
    seed: int
    market_profile: MarketProfile
    instrument_id: str
    scored_interval: UtcInterval
    state: TrialState
    started_at_utc: datetime
    recorded_at_utc: datetime
    finished_at_utc: datetime | str
    result_ref: str
    failure_or_block_reason: str
    result_exposed: bool

    def __post_init__(self) -> None:
        if self.schema_version != 1 or self.journal_sequence <= 0:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "invalid trial journal schema or sequence")
        if self.previous_entry_sha256 != "GENESIS":
            _require_sha256(self.previous_entry_sha256, "trial.previous_entry_sha256")
        _require_sha256(self.journal_entry_sha256, "trial.journal_entry_sha256")
        _require_utc(self.started_at_utc, "trial.started_at_utc")
        _require_utc(self.recorded_at_utc, "trial.recorded_at_utc")
        if isinstance(self.finished_at_utc, datetime):
            _require_utc(self.finished_at_utc, "trial.finished_at_utc")
        elif self.finished_at_utc != NOT_APPLICABLE:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "invalid terminal timestamp marker")
        if self.state in TERMINAL_TRIAL_STATES and not isinstance(self.finished_at_utc, datetime):
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "terminal trial needs finish time")
        if self.state not in TERMINAL_TRIAL_STATES and self.finished_at_utc != NOT_APPLICABLE:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "non-terminal trial cannot have finish time")
        if self.result_exposed and self.result_ref == NOT_APPLICABLE:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "exposed result needs an evidence reference")
        if self.recorded_at_utc < self.started_at_utc:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "trial record predates its start")
        if isinstance(self.finished_at_utc, datetime):
            if self.finished_at_utc != self.recorded_at_utc:
                raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "terminal timestamps diverge")
        if canonical_sha256(self.material_payload()) != self.journal_entry_sha256:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "trial journal entry hash mismatch")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "journal_entry_sha256"
        }

    @classmethod
    def create(
        cls,
        *,
        definition: TrialDefinition,
        journal_sequence: int,
        previous_entry_sha256: str,
        state: TrialState,
        started_at_utc: datetime,
        recorded_at_utc: datetime,
        finished_at_utc: datetime | str,
        result_ref: str,
        failure_or_block_reason: str,
        result_exposed: bool,
    ) -> TrialRecord:
        payload = {
            "schema_version": 1,
            "journal_sequence": journal_sequence,
            "previous_entry_sha256": previous_entry_sha256,
            **definition.to_builtins(),
            "state": state,
            "started_at_utc": started_at_utc,
            "recorded_at_utc": recorded_at_utc,
            "finished_at_utc": finished_at_utc,
            "result_ref": result_ref,
            "failure_or_block_reason": failure_or_block_reason,
            "result_exposed": result_exposed,
        }
        return cls(journal_entry_sha256=canonical_sha256(payload), **payload)


class TrialJournal:
    """Single-node, fsync-safe, append-only JSONL trial history."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read_records(self) -> tuple[TrialRecord, ...]:
        if not self.path.exists():
            return ()
        payload = self.path.read_bytes()
        if payload and not payload.endswith(b"\n"):
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "truncated trial journal line")
        records: list[TrialRecord] = []
        for line_number, line in enumerate(payload.splitlines(), start=1):
            if not line:
                raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "blank trial journal line")
            try:
                record = TrialRecord.from_json_bytes(line)
            except Exception as exc:
                raise ResearchError(
                    "TRIAL_HISTORY_INCOMPLETE",
                    f"malformed trial journal line {line_number}: {exc}",
                ) from exc
            expected_previous = "GENESIS" if not records else records[-1].journal_entry_sha256
            if record.journal_sequence != line_number or record.previous_entry_sha256 != expected_previous:
                raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "trial journal hash chain is broken")
            records.append(record)
        self._validate_transitions(records)
        return tuple(records)

    @staticmethod
    def _validate_transitions(records: list[TrialRecord]) -> None:
        by_trial: dict[str, list[TrialRecord]] = {}
        for record in records:
            by_trial.setdefault(record.trial_id, []).append(record)
        definition_fields = tuple(field.name for field in fields(TrialDefinition))
        for trial_id, trial_records in by_trial.items():
            states = [item.state for item in trial_records]
            if not states or states[0] is not TrialState.PLANNED:
                raise ResearchError(
                    "TRIAL_HISTORY_INCOMPLETE",
                    f"trial {trial_id} was not recorded before execution",
                )
            if len(states) >= 2 and states[1] is not TrialState.STARTED:
                raise ResearchError("TRIAL_HISTORY_INCOMPLETE", f"invalid transition for {trial_id}")
            if len(states) > 3 or (len(states) == 3 and states[2] not in TERMINAL_TRIAL_STATES):
                raise ResearchError("TRIAL_HISTORY_INCOMPLETE", f"invalid transition for {trial_id}")
            frozen_definition = tuple(
                getattr(trial_records[0], name) for name in definition_fields
            )
            if any(
                tuple(getattr(item, name) for name in definition_fields) != frozen_definition
                for item in trial_records[1:]
            ):
                raise ResearchError(
                    "TRIAL_HISTORY_INCOMPLETE",
                    f"trial {trial_id} changed its frozen definition",
                )

    def _definition_for(self, record: TrialRecord) -> TrialDefinition:
        return TrialDefinition(
            **{
                field.name: getattr(record, field.name)
                for field in fields(TrialDefinition)
            },
        )

    def _append(
        self,
        definition: TrialDefinition,
        *,
        state: TrialState,
        started_at_utc: datetime,
        recorded_at_utc: datetime,
        finished_at_utc: datetime | str,
        result_ref: str,
        reason: str,
        result_exposed: bool,
    ) -> TrialRecord:
        records = list(self.read_records())
        existing = [item for item in records if item.trial_id == definition.trial_id]
        if not existing:
            if state is not TrialState.PLANNED:
                raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "new trial must begin PLANNED")
        elif existing[-1].state is TrialState.PLANNED:
            if state is not TrialState.STARTED:
                raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "PLANNED must transition to STARTED")
        elif existing[-1].state is TrialState.STARTED:
            if state not in TERMINAL_TRIAL_STATES:
                raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "STARTED must become terminal")
        else:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "terminal trial cannot transition")
        record = TrialRecord.create(
            definition=definition,
            journal_sequence=len(records) + 1,
            previous_entry_sha256="GENESIS" if not records else records[-1].journal_entry_sha256,
            state=state,
            started_at_utc=started_at_utc,
            recorded_at_utc=recorded_at_utc,
            finished_at_utc=finished_at_utc,
            result_ref=result_ref,
            failure_or_block_reason=reason,
            result_exposed=result_exposed,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            data = record.to_json_bytes() + b"\n"
            offset = 0
            while offset < len(data):
                offset += os.write(fd, data[offset:])
            os.fsync(fd)
        finally:
            os.close(fd)
        return record

    def start(self, definition: TrialDefinition, *, at_utc: datetime) -> tuple[TrialRecord, TrialRecord]:
        _require_utc(at_utc, "trial.start")
        if any(item.trial_id == definition.trial_id for item in self.read_records()):
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "retry requires a new trial_id")
        planned = self._append(
            definition,
            state=TrialState.PLANNED,
            started_at_utc=at_utc,
            recorded_at_utc=at_utc,
            finished_at_utc=NOT_APPLICABLE,
            result_ref=NOT_APPLICABLE,
            reason=NOT_APPLICABLE,
            result_exposed=False,
        )
        started = self._append(
            definition,
            state=TrialState.STARTED,
            started_at_utc=at_utc,
            recorded_at_utc=at_utc,
            finished_at_utc=NOT_APPLICABLE,
            result_ref=NOT_APPLICABLE,
            reason=NOT_APPLICABLE,
            result_exposed=False,
        )
        return planned, started

    def finish(
        self,
        trial_id: str,
        *,
        state: TrialState,
        at_utc: datetime,
        result_ref: str,
        reason: str,
        result_exposed: bool,
    ) -> TrialRecord:
        if state not in TERMINAL_TRIAL_STATES:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "finish requires a terminal state")
        records = [item for item in self.read_records() if item.trial_id == trial_id]
        if not records:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "unknown trial")
        return self._append(
            self._definition_for(records[-1]),
            state=state,
            started_at_utc=records[0].started_at_utc,
            recorded_at_utc=at_utc,
            finished_at_utc=at_utc,
            result_ref=result_ref,
            reason=reason,
            result_exposed=result_exposed,
        )

    def transition(self, trial_id: str, *, state: TrialState, at_utc: datetime) -> TrialRecord:
        records = [item for item in self.read_records() if item.trial_id == trial_id]
        if not records:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "unknown trial")
        return self._append(
            self._definition_for(records[-1]),
            state=state,
            started_at_utc=records[0].started_at_utc,
            recorded_at_utc=at_utc,
            finished_at_utc=NOT_APPLICABLE,
            result_ref=NOT_APPLICABLE,
            reason=NOT_APPLICABLE,
            result_exposed=False,
        )

    def started_trial_ids(self) -> tuple[str, ...]:
        result: list[str] = []
        for record in self.read_records():
            if record.state is TrialState.STARTED and record.trial_id not in result:
                result.append(record.trial_id)
        return tuple(result)


class ResearchScheduler:
    def __init__(self, protocol: ResearchProtocol) -> None:
        self.protocol = protocol

    def next_candidate(self, started: tuple[TrialDefinition, ...]) -> CandidateSpec:
        for definition in started:
            self.validate_trial(definition)
        identities = [item.candidate_id for item in started]
        if len(set(identities)) != len(identities):
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "candidate relabel cannot hide a repeat")
        expected_prefix = [item.candidate_id for item in self.protocol.ordered_candidates[: len(started)]]
        if identities != expected_prefix:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "candidate order diverged from protocol")
        if len(started) >= self.protocol.search_budget:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "frozen search budget exhausted")
        return self.protocol.ordered_candidates[len(started)]

    def validate_trial(self, definition: TrialDefinition) -> None:
        candidates = {item.candidate_id: item for item in self.protocol.ordered_candidates}
        candidate = candidates.get(definition.candidate_id)
        if candidate is None:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "trial candidate is outside frozen domain")
        expected = {
            "research_family_id": self.protocol.research_family_id,
            "hypothesis_id": self.protocol.hypothesis_id,
            "protocol_id": self.protocol.protocol_id,
            "candidate_parameters_sha256": canonical_sha256(dict(candidate.parameter_values)),
            "config_sha256": canonical_sha256(
                {
                    "candidate_id": candidate.candidate_id,
                    "dataset_release_id": self.protocol.dataset_release_ids[0],
                    "protocol_id": self.protocol.protocol_id,
                },
            ),
            "strategy_spec_id": candidate.strategy_spec_id,
            "dataset_release_id": self.protocol.dataset_release_ids[0],
            "partition_role": PartitionRole.FINAL_HOLDOUT,
            "seed": self.protocol.random_seeds[0],
            "market_profile": self.protocol.market_profile,
            "instrument_id": self.protocol.instrument_ids[0],
            "scored_interval": self.protocol.final_holdout_interval,
        }
        mismatches = [
            name for name, value in expected.items() if getattr(definition, name) != value
        ]
        if mismatches:
            raise ResearchError(
                "RESEARCH_PROTOCOL_INVALID",
                f"trial changed frozen protocol inputs: {','.join(mismatches)}",
            )


class ResultExposure(StrictModel):
    trial_id: str
    market_profile: MarketProfile
    instrument_id: str
    scored_interval: UtcInterval
    research_family_id: str
    hypothesis_lineage: tuple[str, ...]
    strategy_lineage: tuple[str, ...]
    dataset_release_id: str
    first_exposure_at_utc: datetime
    exposure_type: str
    evidence_reference: str
    source_branch: str
    source_commit: str
    seed: int
    result_bearing: bool

    def __post_init__(self) -> None:
        for name in (
            "trial_id",
            "instrument_id",
            "research_family_id",
            "exposure_type",
            "evidence_reference",
            "source_branch",
        ):
            _require_nonempty(getattr(self, name), f"exposure.{name}")
        _require_sha256(self.dataset_release_id, "exposure.dataset_release_id")
        if len(self.source_commit) != 40 or any(char not in "0123456789abcdef" for char in self.source_commit):
            raise ResearchError("HOLDOUT_HISTORY_VIOLATION", "invalid source commit identity")
        _require_utc(self.first_exposure_at_utc, "exposure.first_exposure_at_utc")
        if not self.hypothesis_lineage or not self.strategy_lineage:
            raise ResearchError("HOLDOUT_HISTORY_VIOLATION", "exposure lineage is required")


class HoldoutEntry(StrictModel):
    entry_id: str
    previous_entry_sha256: str
    exposure: ResultExposure

    def __post_init__(self) -> None:
        _require_sha256(self.entry_id, "holdout.entry_id")
        if self.previous_entry_sha256 != "GENESIS":
            _require_sha256(self.previous_entry_sha256, "holdout.previous_entry_sha256")
        if canonical_sha256(self.exposure) != self.entry_id:
            raise ResearchError("HOLDOUT_HISTORY_VIOLATION", "holdout entry identity mismatch")

    @classmethod
    def create(cls, exposure: ResultExposure, previous: str) -> HoldoutEntry:
        return cls(
            entry_id=canonical_sha256(exposure),
            previous_entry_sha256=previous,
            exposure=exposure,
        )


class HoldoutLockSnapshot(StrictModel):
    schema_version: int
    entries: tuple[HoldoutEntry, ...]
    history_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ResearchError("HOLDOUT_HISTORY_VIOLATION", "invalid holdout schema")
        _require_sha256(self.history_sha256, "holdout.history_sha256")
        if canonical_sha256(self.entries) != self.history_sha256:
            raise ResearchError("HOLDOUT_HISTORY_VIOLATION", "holdout history hash mismatch")
        previous = "GENESIS"
        for entry in self.entries:
            if entry.previous_entry_sha256 != previous:
                raise ResearchError("HOLDOUT_HISTORY_VIOLATION", "holdout chain was shortened")
            previous = entry.entry_id

    @classmethod
    def create(cls, entries: tuple[HoldoutEntry, ...]) -> HoldoutLockSnapshot:
        return cls(schema_version=1, entries=entries, history_sha256=canonical_sha256(entries))


def _same_market_overlap(left: ResultExposure, right: ResultExposure) -> bool:
    return (
        left.market_profile is right.market_profile
        and left.instrument_id == right.instrument_id
        and left.scored_interval.overlaps(right.scored_interval)
    )


class HoldoutLockStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def read(self) -> HoldoutLockSnapshot:
        if not self.path.exists() or self.path.read_bytes().strip() in {b"", b"{}"}:
            return HoldoutLockSnapshot.create(())
        try:
            return HoldoutLockSnapshot.from_json_bytes(self.path.read_bytes())
        except Exception as exc:
            raise ResearchError("HOLDOUT_HISTORY_VIOLATION", str(exc)) from exc

    def _write(self, snapshot: HoldoutLockSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{self.path.name}.",
            dir=self.path.parent,
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            handle.write(snapshot.to_json_bytes() + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if not handle.closed:
                handle.close()
            temporary.unlink(missing_ok=True)

    def consume(
        self,
        exposure: ResultExposure,
        *,
        journal: TrialJournal,
        exposure_resolver: dict[str, ResultExposure],
    ) -> HoldoutEntry:
        if not exposure.result_bearing:
            raise ResearchError("HOLDOUT_HISTORY_VIOLATION", "non-result cannot consume Holdout")
        self.require_fresh(
            exposure,
            journal=journal,
            exposure_resolver=exposure_resolver,
            consuming_trial_id=exposure.trial_id,
        )
        snapshot = self.read()
        identity = canonical_sha256(exposure)
        for entry in snapshot.entries:
            if entry.entry_id == identity:
                return entry
            if _same_market_overlap(entry.exposure, exposure):
                raise ResearchError("HOLDOUT_ALREADY_CONSUMED", "overlapping Holdout is consumed")
        previous = "GENESIS" if not snapshot.entries else snapshot.entries[-1].entry_id
        entry = HoldoutEntry.create(exposure, previous)
        self._write(HoldoutLockSnapshot.create((*snapshot.entries, entry)))
        return entry

    def require_fresh(
        self,
        candidate: ResultExposure,
        *,
        journal: TrialJournal,
        exposure_resolver: dict[str, ResultExposure],
        consuming_trial_id: str | None = None,
    ) -> None:
        records = journal.read_records()
        exposed_ids = {
            record.trial_id
            for record in records
            if record.state in TERMINAL_TRIAL_STATES and record.result_exposed
        }
        for trial_id in sorted(exposed_ids):
            if trial_id not in exposure_resolver:
                raise ResearchError(
                    "HOLDOUT_HISTORY_VIOLATION",
                    f"result-bearing trial {trial_id} has unresolved exposure",
                )
            prior = exposure_resolver[trial_id]
            if trial_id == consuming_trial_id:
                if prior != candidate:
                    raise ResearchError(
                        "HOLDOUT_HISTORY_VIOLATION",
                        "current trial exposure does not match the frozen candidate exposure",
                    )
                continue
            if _same_market_overlap(prior, candidate):
                raise ResearchError("HOLDOUT_ALREADY_CONSUMED", "prior result exposed the interval")
        for entry in self.read().entries:
            if _same_market_overlap(entry.exposure, candidate):
                raise ResearchError("HOLDOUT_ALREADY_CONSUMED", "Holdout lock already covers interval")


class CompletedTradeSeries(StrictModel):
    source: str
    evidence_sha256: str
    settlement_currency: str
    stable_native_sequence: bool
    native_completed_unit_count: int | str
    realized_pnl_outcomes: tuple[Decimal, ...]
    realized_returns: tuple[Decimal, ...]
    unambiguous_net_after_cost: bool
    net_outcomes: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if self.source != "NAUTILUS_NATIVE_COMPLETED_TRADES":
            raise ResearchError("CLAIM_INELIGIBLE", "project trade pairing is forbidden")
        _require_sha256(self.evidence_sha256, "completed_trades.evidence_sha256")
        _require_nonempty(self.settlement_currency, "completed_trades.settlement_currency")
        values = (*self.realized_pnl_outcomes, *self.realized_returns, *self.net_outcomes)
        if any(not outcome.is_finite() for outcome in values):
            raise ResearchError("CLAIM_INELIGIBLE", "trade outcome must be finite")
        if not self.stable_native_sequence:
            if self.native_completed_unit_count != "UNDEFINED":
                raise ResearchError(
                    "CLAIM_INELIGIBLE",
                    "unstable native sequence cannot publish a completed-unit count",
                )
            if self.realized_pnl_outcomes or self.realized_returns or self.net_outcomes:
                raise ResearchError(
                    "CLAIM_INELIGIBLE",
                    "unstable native sequence cannot publish unit outcomes",
                )
            if self.unambiguous_net_after_cost:
                raise ResearchError(
                    "CLAIM_INELIGIBLE",
                    "unstable native sequence cannot claim net-after-cost outcomes",
                )
            return
        if type(self.native_completed_unit_count) is not int:
            raise ResearchError("CLAIM_INELIGIBLE", "native completed-unit count must be integer")
        count = self.native_completed_unit_count
        if count < 0:
            raise ResearchError("CLAIM_INELIGIBLE", "native completed-unit count is negative")
        if len(self.realized_pnl_outcomes) != count or len(self.realized_returns) != count:
            raise ResearchError(
                "CLAIM_INELIGIBLE",
                "native completed-unit values are incomplete",
            )
        if self.unambiguous_net_after_cost:
            if len(self.net_outcomes) != count:
                raise ResearchError("CLAIM_INELIGIBLE", "native net outcome sequence is incomplete")
            if self.net_outcomes != self.realized_pnl_outcomes:
                raise ResearchError(
                    "CLAIM_INELIGIBLE",
                    "native net outcomes must be the persisted Position.realized_pnl sequence",
                )
        elif self.net_outcomes:
            raise ResearchError("CLAIM_INELIGIBLE", "ambiguous costs forbid net outcomes")


def evaluate_sample_adequacy(
    rule: SampleAdequacyRule,
    completed_trades: CompletedTradeSeries,
) -> SampleAdequacy:
    threshold = rule.minimum_completed_trades
    if threshold == NOT_APPLICABLE:
        return SampleAdequacy.NOT_APPLICABLE
    if not completed_trades.stable_native_sequence:
        return SampleAdequacy.LOW_CONFIDENCE
    assert isinstance(threshold, int)
    assert isinstance(completed_trades.native_completed_unit_count, int)
    return (
        SampleAdequacy.ADEQUATE
        if completed_trades.native_completed_unit_count >= threshold
        else SampleAdequacy.LOW_CONFIDENCE
    )


class PercentileValues(StrictModel):
    p05: Decimal | str
    p50: Decimal | str
    p95: Decimal | str


class MonteCarloResult(StrictModel):
    schema_version: int
    diagnostic_id: str
    status: MonteCarloStatus
    status_reason: str
    input_trade_evidence_sha256: str
    resampling_method: ResamplingMethod
    simulations_requested: int
    simulations_completed: int
    random_seed: int
    block_length: int | str
    quantile_method: str
    decimal_places: int
    final_equity_distribution: PercentileValues
    max_drawdown_distribution: PercentileValues
    positive_simulation_rate: Decimal | str
    worst_simulated_drawdown: Decimal | str
    consecutive_loss_streak_distribution: PercentileValues
    original_result_location_in_distribution: Decimal | str
    top_winner_dependency: str
    outlier_dependency: dict[str, str]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ResearchError("CLAIM_INELIGIBLE", "unknown Monte Carlo result schema")
        _require_sha256(self.diagnostic_id, "monte_carlo.diagnostic_id")
        _require_sha256(self.input_trade_evidence_sha256, "monte_carlo.input_trade_evidence_sha256")
        if canonical_sha256(self.material_payload()) != self.diagnostic_id:
            raise ResearchError("CLAIM_INELIGIBLE", "Monte Carlo result identity mismatch")
        _freeze_field(self, "outlier_dependency")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "diagnostic_id"
        }

    @classmethod
    def create(cls, **values: Any) -> MonteCarloResult:
        material = {"schema_version": 1, **values}
        return cls(diagnostic_id=canonical_sha256(material), **material)


def _quantum(decimal_places: int) -> Decimal:
    return Decimal(1).scaleb(-decimal_places)


def _rounded(value: Decimal, decimal_places: int) -> Decimal:
    return value.quantize(_quantum(decimal_places), rounding=ROUND_HALF_EVEN)


def _percentile(values: list[Decimal], probability: Decimal, places: int) -> Decimal:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires observations")
    position = Decimal(len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - Decimal(lower)
    return _rounded(ordered[lower] + (ordered[upper] - ordered[lower]) * weight, places)


def _percentiles(values: list[Decimal], places: int) -> PercentileValues:
    return PercentileValues(
        p05=_percentile(values, Decimal("0.05"), places),
        p50=_percentile(values, Decimal("0.50"), places),
        p95=_percentile(values, Decimal("0.95"), places),
    )


def _unavailable_percentiles() -> PercentileValues:
    return PercentileValues(p05=NOT_APPLICABLE, p50=NOT_APPLICABLE, p95=NOT_APPLICABLE)


def run_monte_carlo(
    spec: MonteCarloSpec,
    completed_trades: CompletedTradeSeries,
    *,
    initial_capital: Decimal,
    sample_adequacy: SampleAdequacy,
) -> MonteCarloResult:
    if not initial_capital.is_finite() or initial_capital <= 0:
        raise ResearchError("CLAIM_INELIGIBLE", "Monte Carlo initial capital must be positive")
    unavailable_reason = ""
    status = MonteCarloStatus.COMPLETED
    if spec.resampling_method is ResamplingMethod.NOT_APPLICABLE:
        status = MonteCarloStatus.NOT_APPLICABLE
        unavailable_reason = spec.not_applicable_reason
    elif sample_adequacy is not SampleAdequacy.ADEQUATE:
        status = MonteCarloStatus.MC_LOW_CONFIDENCE
        unavailable_reason = "sample adequacy is not ADEQUATE"
    elif not completed_trades.unambiguous_net_after_cost or not completed_trades.net_outcomes:
        status = MonteCarloStatus.MC_LOW_CONFIDENCE
        unavailable_reason = "native net completed-trade evidence is ambiguous or empty"
    if status is not MonteCarloStatus.COMPLETED:
        return MonteCarloResult.create(
            status=status,
            status_reason=unavailable_reason,
            input_trade_evidence_sha256=completed_trades.evidence_sha256,
            resampling_method=spec.resampling_method,
            simulations_requested=spec.simulation_count,
            simulations_completed=0,
            random_seed=spec.random_seed,
            block_length=spec.block_length,
            quantile_method=spec.quantile_method,
            decimal_places=spec.decimal_places,
            final_equity_distribution=_unavailable_percentiles(),
            max_drawdown_distribution=_unavailable_percentiles(),
            positive_simulation_rate=NOT_APPLICABLE,
            worst_simulated_drawdown=NOT_APPLICABLE,
            consecutive_loss_streak_distribution=_unavailable_percentiles(),
            original_result_location_in_distribution=NOT_APPLICABLE,
            top_winner_dependency=NOT_APPLICABLE,
            outlier_dependency={
                "original_net_pnl": NOT_APPLICABLE,
                "without_top_winner": NOT_APPLICABLE,
            },
        )

    outcomes = tuple(completed_trades.net_outcomes)
    if spec.resampling_method is ResamplingMethod.MOVING_BLOCK_BOOTSTRAP:
        assert isinstance(spec.block_length, int)
        if spec.block_length > len(outcomes):
            raise ResearchError("CLAIM_INELIGIBLE", "block length exceeds native trade sample")
    generator = random.Random(spec.random_seed)
    final_equities: list[Decimal] = []
    max_drawdowns: list[Decimal] = []
    loss_streaks: list[Decimal] = []
    for _ in range(spec.simulation_count):
        if spec.resampling_method is ResamplingMethod.IID_BOOTSTRAP:
            sampled = [outcomes[generator.randrange(len(outcomes))] for _ in outcomes]
        else:
            assert isinstance(spec.block_length, int)
            sampled = []
            while len(sampled) < len(outcomes):
                start = generator.randrange(len(outcomes) - spec.block_length + 1)
                sampled.extend(outcomes[start : start + spec.block_length])
            sampled = sampled[: len(outcomes)]
        equity = initial_capital
        peak = initial_capital
        maximum_drawdown = Decimal(0)
        current_streak = 0
        maximum_streak = 0
        for outcome in sampled:
            equity += outcome
            if equity > peak:
                peak = equity
            if peak > 0:
                maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak)
            if outcome < 0:
                current_streak += 1
                maximum_streak = max(maximum_streak, current_streak)
            else:
                current_streak = 0
        final_equities.append(equity)
        max_drawdowns.append(maximum_drawdown)
        loss_streaks.append(Decimal(maximum_streak))

    original_final = initial_capital + sum(outcomes, Decimal(0))
    original_location = Decimal(sum(item <= original_final for item in final_equities)) / Decimal(
        len(final_equities),
    )
    positive = [outcome for outcome in outcomes if outcome > 0]
    top_dependency = (
        NOT_APPLICABLE
        if not positive
        else str(_rounded(max(positive) / sum(positive, Decimal(0)), spec.decimal_places))
    )
    top_winner = max(positive) if positive else Decimal(0)
    return MonteCarloResult.create(
        status=MonteCarloStatus.COMPLETED,
        status_reason="COMPLETED_FROM_FROZEN_NATIVE_NET_TRADE_OUTCOMES",
        input_trade_evidence_sha256=completed_trades.evidence_sha256,
        resampling_method=spec.resampling_method,
        simulations_requested=spec.simulation_count,
        simulations_completed=spec.simulation_count,
        random_seed=spec.random_seed,
        block_length=spec.block_length,
        quantile_method=spec.quantile_method,
        decimal_places=spec.decimal_places,
        final_equity_distribution=_percentiles(final_equities, spec.decimal_places),
        max_drawdown_distribution=_percentiles(max_drawdowns, spec.decimal_places),
        positive_simulation_rate=_rounded(
            Decimal(sum(item > initial_capital for item in final_equities)) / Decimal(len(final_equities)),
            spec.decimal_places,
        ),
        worst_simulated_drawdown=_rounded(max(max_drawdowns), spec.decimal_places),
        consecutive_loss_streak_distribution=_percentiles(loss_streaks, spec.decimal_places),
        original_result_location_in_distribution=_rounded(original_location, spec.decimal_places),
        top_winner_dependency=top_dependency,
        outlier_dependency={
            "original_net_pnl": str(_rounded(sum(outcomes, Decimal(0)), spec.decimal_places)),
            "without_top_winner": (
                NOT_APPLICABLE
                if not positive
                else str(_rounded(sum(outcomes, Decimal(0)) - top_winner, spec.decimal_places))
            ),
        },
    )


class ClaimEvaluationInput(StrictModel):
    protocol: ResearchProtocol
    mechanical_integrity: MechanicalIntegrity
    checker_result: str
    underlying_official_runs_valid: bool
    qualification_only: bool
    protocol_frozen_before_results: bool
    supporting_trial_protocol_ids: tuple[str, ...]
    complete_trial_history: bool
    partitions_valid: bool
    holdout_valid: bool
    benchmark_valid: bool
    multiple_testing_valid: bool
    sample_adequacy_by_instrument: dict[str, str]
    monte_carlo_by_instrument: dict[str, str]
    diagnostics_complete_by_instrument: dict[str, bool]
    claim_scope_supported: bool
    universe_evidence_valid: bool
    unresolved_material_ambiguities: tuple[str, ...]
    synthetic_contract_fixture: bool

    def __post_init__(self) -> None:
        for identity in self.supporting_trial_protocol_ids:
            _require_sha256(identity, "claim.supporting_trial_protocol_ids")
        _freeze_field(self, "sample_adequacy_by_instrument")
        _freeze_field(self, "monte_carlo_by_instrument")
        _freeze_field(self, "diagnostics_complete_by_instrument")


class ClaimEvaluation(StrictModel):
    schema_version: int
    claim_evaluation_id: str
    protocol_id: str
    mechanical_integrity: MechanicalIntegrity
    research_intent: ResearchIntent
    research_eligibility: ResearchEligibility
    eligible_confirmatory_profitability_claim: bool
    failure_codes: tuple[str, ...]
    reasons: tuple[str, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ResearchError("CLAIM_INELIGIBLE", "unknown claim result schema")
        _require_sha256(self.claim_evaluation_id, "claim.claim_evaluation_id")
        if self.protocol_id != NOT_APPLICABLE:
            _require_sha256(self.protocol_id, "claim.protocol_id")
        if self.eligible_confirmatory_profitability_claim and self.research_eligibility is not ResearchEligibility.ELIGIBLE:
            raise ResearchError("CLAIM_INELIGIBLE", "claim cannot override eligibility")
        if canonical_sha256(self.material_payload()) != self.claim_evaluation_id:
            raise ResearchError("CLAIM_INELIGIBLE", "claim result identity mismatch")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "claim_evaluation_id"
        }

    @classmethod
    def create(cls, **values: Any) -> ClaimEvaluation:
        material = {"schema_version": 1, **values}
        return cls(claim_evaluation_id=canonical_sha256(material), **material)

    def force_ineligible(self, code: str, reason: str) -> ClaimEvaluation:
        return ClaimEvaluation.create(
            protocol_id=self.protocol_id,
            mechanical_integrity=self.mechanical_integrity,
            research_intent=self.research_intent,
            research_eligibility=ResearchEligibility.INELIGIBLE,
            eligible_confirmatory_profitability_claim=False,
            failure_codes=tuple(dict.fromkeys((*self.failure_codes, code))),
            reasons=tuple(dict.fromkeys((*self.reasons, reason))),
            limitations=self.limitations,
        )


def _evaluate_claim_from_resolved_evidence(value: ClaimEvaluationInput) -> ClaimEvaluation:
    hard_reasons: list[str] = []
    exploratory_reasons: list[str] = []
    blocked_reasons: list[str] = []
    codes: list[str] = []
    if value.mechanical_integrity is MechanicalIntegrity.BLOCKED:
        blocked_reasons.append("MECHANICAL_INTEGRITY_BLOCKED")
        codes.append("CHECKER_BLOCKED")
    elif value.mechanical_integrity is not MechanicalIntegrity.PASS:
        hard_reasons.append("MECHANICAL_INTEGRITY_NOT_PASS")
        codes.append("CLAIM_INELIGIBLE")
    if value.checker_result != "CHECK_PASS":
        hard_reasons.append("CHECKER_NOT_PASS")
        codes.append("CHECKER_FAILURE")
    if not value.underlying_official_runs_valid:
        hard_reasons.append("UNDERLYING_OFFICIAL_RUN_INVALID")
        codes.append("CLAIM_INELIGIBLE")
    if value.qualification_only:
        hard_reasons.append("QUALIFICATION_EVIDENCE_NOT_PROFITABILITY_PROOF")
        codes.append("CLAIM_INELIGIBLE")
    if value.protocol.research_intent is ResearchIntent.EXPLORATORY:
        exploratory_reasons.append("EXPLORATORY_PROTOCOL")
    if (
        not value.protocol_frozen_before_results
        or not value.supporting_trial_protocol_ids
        or any(identity != value.protocol.protocol_id for identity in value.supporting_trial_protocol_ids)
    ):
        exploratory_reasons.append("LATER_PROTOCOL_REANALYSIS")
    if not value.complete_trial_history:
        hard_reasons.append("TRIAL_HISTORY_INCOMPLETE")
        codes.append("TRIAL_HISTORY_INCOMPLETE")
    if not value.partitions_valid:
        hard_reasons.append("PARTITION_LEAKAGE")
        codes.append("PARTITION_LEAKAGE")
    if not value.holdout_valid:
        hard_reasons.append("HOLDOUT_INVALID_OR_CONSUMED")
        codes.append("HOLDOUT_ALREADY_CONSUMED")
    if not value.benchmark_valid:
        hard_reasons.append("BENCHMARK_MISSING_OR_INVALID")
        codes.append("CLAIM_INELIGIBLE")
    if not value.multiple_testing_valid or value.protocol.multiple_testing_treatment == "UNDECLARED":
        exploratory_reasons.append("MULTIPLE_TESTING_UNDECLARED")
        codes.append("MULTIPLE_TESTING_UNDECLARED")
    required_instruments = set(value.protocol.instrument_ids)
    if set(value.sample_adequacy_by_instrument) != required_instruments or any(
        status != SampleAdequacy.ADEQUATE.value
        for status in value.sample_adequacy_by_instrument.values()
    ):
        exploratory_reasons.append("SAMPLE_ADEQUACY_NOT_ADEQUATE")
        codes.append("CLAIM_INELIGIBLE")
    if set(value.monte_carlo_by_instrument) != required_instruments or any(
        status != MonteCarloStatus.COMPLETED.value
        for status in value.monte_carlo_by_instrument.values()
    ):
        hard_reasons.append("MONTE_CARLO_NOT_COMPLETED")
        codes.append("CLAIM_INELIGIBLE")
    if not value.claim_scope_supported:
        hard_reasons.append("CLAIM_SCOPE_UNSUPPORTED")
        codes.append("CLAIM_INELIGIBLE")
    if not value.universe_evidence_valid:
        hard_reasons.append("POINT_IN_TIME_UNIVERSE_EVIDENCE_INVALID")
        codes.append("CLAIM_INELIGIBLE")
    if set(value.diagnostics_complete_by_instrument) != required_instruments or not all(
        value.diagnostics_complete_by_instrument.values()
    ):
        hard_reasons.append("PERFORMANCE_DIAGNOSTICS_INCOMPLETE")
        codes.append("EVIDENCE_INCOMPLETE")
    if value.unresolved_material_ambiguities:
        blocked_reasons.extend(value.unresolved_material_ambiguities)
        codes.append("CLAIM_INELIGIBLE")
    if blocked_reasons:
        eligibility = ResearchEligibility.BLOCKED
    elif hard_reasons:
        eligibility = ResearchEligibility.INELIGIBLE
    elif exploratory_reasons:
        eligibility = ResearchEligibility.EXPLORATORY_ONLY
    else:
        eligibility = ResearchEligibility.ELIGIBLE
    limitations = [
        "BAR_BASED_ESTIMATED_EXECUTION",
        "ESTIMATED_FEE_UNLESS_EXACT_TIER_PROVEN",
        "QUEUE_IMPACT_SPREAD_LIQUIDATION_UNSUPPORTED",
    ]
    if value.synthetic_contract_fixture:
        limitations.append("SYNTHETIC_CONTRACT_FIXTURE_NOT_REAL_CLAIM")
    reasons = tuple(dict.fromkeys((*blocked_reasons, *hard_reasons, *exploratory_reasons)))
    return ClaimEvaluation.create(
        protocol_id=value.protocol.protocol_id,
        mechanical_integrity=value.mechanical_integrity,
        research_intent=value.protocol.research_intent,
        research_eligibility=eligibility,
        eligible_confirmatory_profitability_claim=(eligibility is ResearchEligibility.ELIGIBLE),
        failure_codes=tuple(dict.fromkeys(codes)),
        reasons=reasons,
        limitations=tuple(limitations),
    )


def evaluate_claim(value: ClaimEvaluationInput) -> ClaimEvaluation:
    """Evaluate only an internal/synthetic contract fixture or an ineligible view.

    Official callers cannot turn asserted booleans into an eligible claim.  The
    production boundary is ``OfficialEvidenceResolver``, which invokes the
    private evaluator only after resolving every fact from repository evidence.
    """

    result = _evaluate_claim_from_resolved_evidence(value)
    if (
        result.research_eligibility is ResearchEligibility.ELIGIBLE
        and not value.synthetic_contract_fixture
    ):
        raise ResearchError(
            "EVIDENCE_INCOMPLETE",
            "eligible Official claims require OfficialEvidenceResolver identities",
        )
    return result


class M3QualificationResearchView(StrictModel):
    profile_record: QualifiedProfileRecord
    qualification_bundle: QualificationDownstreamBundle
    claim_evaluation: ClaimEvaluation

    def __post_init__(self) -> None:
        if self.qualification_bundle.profile_record != self.profile_record:
            raise ResearchError("DOWNSTREAM_CONTRACT_FAILURE", "M3 view record mismatch")
        if self.claim_evaluation.research_eligibility is not ResearchEligibility.INELIGIBLE:
            raise ResearchError(
                "CLAIM_INELIGIBLE",
                "qualification evidence must remain ineligible for profitability claims",
            )


class M3ResearchBoundary:
    """Public M3-to-M4 reader; it never imports runner/data internals."""

    def __init__(self, registry: QualifiedProfileRegistry, bundles: tuple[M3QualificationResearchView, ...]) -> None:
        self.registry = registry
        self.bundles = bundles

    @classmethod
    def load(
        cls,
        *,
        registry_path: Path,
        downstream_directory: Path,
        expected_registry_identity: str,
    ) -> M3ResearchBoundary:
        registry = QualifiedProfileRegistry.from_json_bytes(Path(registry_path).read_bytes())
        if registry.registry_content_sha256 != expected_registry_identity:
            raise ResearchError("DOWNSTREAM_CONTRACT_FAILURE", "M3 registry identity mismatch")
        views: list[M3QualificationResearchView] = []
        for record in registry.records:
            bundle = QualificationDownstreamBundle.from_json_bytes(
                (Path(downstream_directory) / f"{record.profile_id.value}.json").read_bytes(),
            )
            if bundle.profile_record != record:
                raise ResearchError("DOWNSTREAM_CONTRACT_FAILURE", "M3 bundle diverges from registry")
            claim = ClaimEvaluation.create(
                protocol_id=NOT_APPLICABLE,
                mechanical_integrity=MechanicalIntegrity.PASS,
                research_intent=ResearchIntent.EXPLORATORY,
                research_eligibility=ResearchEligibility.INELIGIBLE,
                eligible_confirmatory_profitability_claim=False,
                failure_codes=("CLAIM_INELIGIBLE",),
                reasons=("QUALIFICATION_EVIDENCE_NOT_PROFITABILITY_PROOF",),
                limitations=tuple(bundle.qualification_limitations),
            )
            views.append(
                M3QualificationResearchView(
                    profile_record=record,
                    qualification_bundle=bundle,
                    claim_evaluation=claim,
                ),
            )
        return cls(registry, tuple(views))


__all__ = [
    "BenchmarkSpec",
    "CandidateSpec",
    "ClaimEvaluation",
    "ClaimEvaluationInput",
    "ClaimScope",
    "CompletedTradeSeries",
    "HoldoutEntry",
    "HoldoutLockSnapshot",
    "HoldoutLockStore",
    "InstrumentScope",
    "M3ResearchBoundary",
    "M3QualificationResearchView",
    "MonteCarloResult",
    "MonteCarloSpec",
    "MonteCarloStatus",
    "PartitionRole",
    "PartitionStartEvidence",
    "PercentileValues",
    "PurgeEmbargoRule",
    "ResearchEligibility",
    "ResearchError",
    "ResearchIntent",
    "ResearchProtocol",
    "ResearchScheduler",
    "ResamplingMethod",
    "ResultExposure",
    "SampleAdequacy",
    "SampleAdequacyRule",
    "TrialDefinition",
    "TrialJournal",
    "TrialRecord",
    "TrialState",
    "UniverseMembershipDecision",
    "UniverseMembershipEvidence",
    "UtcInterval",
    "benchmark_trial_candidate_id",
    "evaluate_claim",
    "evaluate_sample_adequacy",
    "run_monte_carlo",
    "validate_partition_boundaries",
]
