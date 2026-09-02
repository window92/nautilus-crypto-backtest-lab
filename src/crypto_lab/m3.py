"""M3 real-data profile qualification contracts.

This module freezes qualification-only signal schedules and publishes the two
content-addressed profile records consumed by M4.  It does not implement
research governance, reporting, or any financial engine behavior.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.config import SourceRevision
from crypto_lab.config import StrictModel
from crypto_lab.config import _freeze_field
from crypto_lab.config import _require_equal
from crypto_lab.config import _require_sha256
from crypto_lab.hashing import canonical_sha256
from crypto_lab.git_identity import require_repository_root
from crypto_lab.data import DatasetRelease
from crypto_lab.data import M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION
from crypto_lab.runner import LabRunRequest
from crypto_lab.runner import QualificationControl
from crypto_lab.runner import RunResult
from crypto_lab.status import validated_failure_codes
from crypto_lab.strategies import OrderIntent
from crypto_lab.strategies import StrategyPlan
from crypto_lab.strategies import StrategySpec


SPOT_BASE_RELEASE_ID = "2e0bdefe2b664821c559e95d35a3462c8354606076e1ec81d0ce6272f89b9a44"
PERPETUAL_BASE_RELEASE_ID = "749e654402021fafafe4a3269005c5ef1253c3743f04c35622726bca957a356b"
SPOT_QUALIFICATION_RELEASE_ID = "702ff072654e9fb8d25b54e372a76e6545f2ddccddde8ef44795ec7e8cef97d7"
PERPETUAL_QUALIFICATION_RELEASE_ID = "e8ab1bc815aa22a179ecdd1daa48d2a966a45aa213d4c9031e8246b054e5b6db"
EXPOSED_QUALIFICATION_LIMITATION = "QUALIFICATION_INTERVAL_EXPOSED_NOT_FRESH_HOLDOUT"
M3_FEE_RATE = Decimal("0.001")
COMPONENT_CHECK_PASS = "COMPONENT_CHECK_PASS"
LEGACY_CHECK_PASS = "CHECK_PASS"


class ProfileQualificationState(StrEnum):
    QUALIFIED = "QUALIFIED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class MechanicalIntegrity(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


class M3NegativeControl(StrEnum):
    SPOT_SHORT = "SPOT_SHORT"
    PERP_DIRECT_CROSS_ZERO = "PERP_DIRECT_CROSS_ZERO"
    PERP_CONCURRENT_ORDER = "PERP_CONCURRENT_ORDER"
    PERP_ABOVE_MARKET_MAX = "PERP_ABOVE_MARKET_MAX"
    PERP_POST_BOUNDARY_OPEN = "PERP_POST_BOUNDARY_OPEN"


class MechanicalIntegrityResult(StrictModel):
    state: MechanicalIntegrity
    checker_result: str
    replay_result: str
    run_ids: tuple[str, ...]
    failure_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        codes = validated_failure_codes(
            self.failure_codes,
            field="mechanical_integrity.failure_codes",
        )
        object.__setattr__(self, "failure_codes", codes)
        if not self.run_ids:
            raise ValueError("mechanical_integrity.run_ids: must not be empty")
        if self.state is MechanicalIntegrity.PASS:
            _require_equal(
                self.checker_result,
                COMPONENT_CHECK_PASS,
                "mechanical_integrity.component_validation",
            )
            _require_equal(self.replay_result, "PASS", "mechanical_integrity.replay")
            if codes:
                raise ValueError("mechanical_integrity.failure_codes: PASS cannot have failures")
        elif not codes:
            raise ValueError(
                "mechanical_integrity.failure_codes: non-PASS requires a canonical code",
            )


class QualifiedProfileRecord(StrictModel):
    schema_version: int
    qualified_profile_record_id: str
    profile_id: MarketProfile
    qualification_state: ProfileQualificationState
    runtime_lock_sha256: str
    source_revision: SourceRevision
    base_dataset_release_id: str
    dataset_release_id: str
    strategy_spec_id: str
    accepted_run_ids: tuple[str, ...]
    checker_result: str
    replay_result: str
    evidence_references: tuple[str, ...]
    qualification_limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version not in {1, 2}:
            raise ValueError("qualified_profile.schema_version: only versions 1 and 2 are supported")
        for field in (
            "qualified_profile_record_id",
            "runtime_lock_sha256",
            "base_dataset_release_id",
            "dataset_release_id",
            "strategy_spec_id",
        ):
            _require_sha256(getattr(self, field), f"qualified_profile.{field}")
        if len(set(self.accepted_run_ids)) != len(self.accepted_run_ids):
            raise ValueError("qualified_profile.accepted_run_ids: duplicate Run ID")
        if not self.evidence_references or not self.qualification_limitations:
            raise ValueError("qualified_profile: evidence and limitations are required")
        if EXPOSED_QUALIFICATION_LIMITATION not in self.qualification_limitations:
            raise ValueError("qualified_profile: exposed qualification interval disclosure required")
        if self.qualification_state is ProfileQualificationState.QUALIFIED:
            if len(self.accepted_run_ids) != 2:
                raise ValueError("qualified_profile: primary and fresh-process replay are required")
            expected_component = (
                LEGACY_CHECK_PASS if self.schema_version == 1 else COMPONENT_CHECK_PASS
            )
            _require_equal(
                self.checker_result,
                expected_component,
                "qualified_profile.component_validation_result",
            )
            _require_equal(self.replay_result, "PASS", "qualified_profile.replay_result")
        if canonical_sha256(self.material_payload()) != self.qualified_profile_record_id:
            raise ValueError("qualified_profile_record_id does not match material payload")

    def material_payload(self) -> dict[str, Any]:
        payload = self.to_builtins()
        payload.pop("qualified_profile_record_id", None)
        payload["source_revision"].pop("captured_at_utc", None)
        return payload

    @classmethod
    def create(cls, **values: Any) -> QualifiedProfileRecord:
        material = {"schema_version": 2, **values}
        identity_material = dict(material)
        source = values["source_revision"].to_builtins()
        source.pop("captured_at_utc", None)
        identity_material["source_revision"] = source
        return cls(
            schema_version=2,
            qualified_profile_record_id=canonical_sha256(identity_material),
            **values,
        )


class QualifiedProfileRegistry(StrictModel):
    schema_version: int
    records: tuple[QualifiedProfileRecord, ...]
    registry_content_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version not in {1, 2}:
            raise ValueError(
                "qualified_profile_registry.schema_version: only versions 1 and 2 are supported",
            )
        _require_sha256(
            self.registry_content_sha256,
            "qualified_profile_registry.registry_content_sha256",
        )
        expected_profiles = (
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        )
        if tuple(record.profile_id for record in self.records) != expected_profiles:
            raise ValueError("qualified_profile_registry: exactly one ordered record per V1 profile")
        if any(
            record.qualification_state is not ProfileQualificationState.QUALIFIED
            for record in self.records
        ):
            raise ValueError("qualified_profile_registry: every published record must be QUALIFIED")
        if any(record.schema_version != self.schema_version for record in self.records):
            raise ValueError("qualified_profile_registry: record schema must match registry schema")
        if canonical_sha256(self.material_payload()) != self.registry_content_sha256:
            raise ValueError("qualified_profile_registry content identity mismatch")

    def material_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "records": [record.to_builtins() for record in self.records],
        }

    @classmethod
    def create(
        cls,
        *,
        records: tuple[QualifiedProfileRecord, ...],
    ) -> QualifiedProfileRegistry:
        material = {
            "schema_version": 2,
            "records": [record.to_builtins() for record in records],
        }
        return cls(
            schema_version=2,
            records=records,
            registry_content_sha256=canonical_sha256(material),
        )


class QualificationDownstreamBundle(StrictModel):
    """M3's strict shape-only handoff to a future M4 consumer."""

    schema_version: int
    profile_record: QualifiedProfileRecord
    run_result: dict[str, Any]
    evidence_manifest: dict[str, Any]
    mechanical_integrity: MechanicalIntegrityResult
    qualification_limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version not in {1, 2}:
            raise ValueError("qualification_bundle.schema_version: only versions 1 and 2 are supported")
        if self.profile_record.qualification_state is not ProfileQualificationState.QUALIFIED:
            raise ValueError("qualification_bundle: profile must be QUALIFIED")
        if self.mechanical_integrity.state is not MechanicalIntegrity.PASS:
            raise ValueError("qualification_bundle: MechanicalIntegrity must PASS")
        if self.run_result.get("state") != "COMPLETED":
            raise ValueError("qualification_bundle: RunResult must be COMPLETED")
        expected_component = (
            LEGACY_CHECK_PASS if self.schema_version == 1 else COMPONENT_CHECK_PASS
        )
        actual_component = self.run_result.get(
            "checker_outcome" if self.schema_version == 1 else "component_validation_outcome",
        )
        if actual_component != expected_component:
            raise ValueError("qualification_bundle: component validation must pass")
        if self.run_result.get("run_id") not in self.profile_record.accepted_run_ids:
            raise ValueError("qualification_bundle: RunResult is not an accepted Run")
        if tuple(self.qualification_limitations) != tuple(
            self.profile_record.qualification_limitations,
        ):
            raise ValueError("qualification_bundle: limitations diverge from profile record")
        if self.evidence_manifest.get("schema") != "run-evidence-manifest-v1":
            raise ValueError("qualification_bundle: complete evidence manifest is required")
        _freeze_field(self, "run_result")
        _freeze_field(self, "evidence_manifest")


def compare_deterministic_replay(primary: RunResult, replay: RunResult) -> dict[str, Any]:
    """Compare all semantic event groups through their canonical M1 digest."""

    components = (
        "order_intents",
        "orders",
        "fills",
        "positions",
        "account_events",
        "fee_events",
        "funding_settlements",
        "terminal_portfolio",
    )
    accepted = (
        primary.state.value == "COMPLETED"
        and replay.state.value == "COMPLETED"
        and primary.checker_outcome.value == COMPONENT_CHECK_PASS
        and replay.checker_outcome.value == COMPONENT_CHECK_PASS
        and not primary.failure_codes
        and not replay.failure_codes
        and primary.semantic_digest == replay.semantic_digest
    )
    return {
        "schema": "m3-deterministic-replay-v1",
        "primary_run_id": primary.run_id,
        "replay_run_id": replay.run_id,
        "fresh_processes": True,
        "semantic_components": list(components),
        "primary_semantic_digest": primary.semantic_digest,
        "replay_semantic_digest": replay.semantic_digest,
        "ignored_fields": [
            "run_id",
            "event_id",
            "client_order_id",
            "venue_order_id",
            "trade_id",
            "position_id",
            "strategy_id",
            "trader_id",
            "instance_id",
            "captured_at_utc",
        ],
        "result": "PASS" if accepted else "FAIL",
    }


@dataclass(frozen=True)
class QualificationStrategyInputs:
    strategy_spec: StrategySpec
    strategy_plan: StrategyPlan


def _intent(side: str, quantity: str, reason: str) -> OrderIntent:
    return OrderIntent(side=side, quantity=quantity, order_type="MARKET", reason=reason)


def qualification_strategy_inputs(profile: MarketProfile) -> QualificationStrategyInputs:
    """Return the result-independent M3 schedule frozen before any Run output."""

    if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        instrument_id = "BTCUSDT.BINANCE"
        plan = StrategyPlan(
            intents_by_bar_ns={
                1_735_689_600_000_000_000: (
                    _intent("BUY", "0.00100", "M3_SPOT_COMPLETED_BAR_LONG_ENTRY"),
                ),
            },
            conflict_rule="FIRST_ELIGIBLE_INTENT",
            qualification_attempt_all_intents=False,
        )
        base_release = SPOT_BASE_RELEASE_ID
        sizing = (
            "QUOTE_NOTIONAL_EQUAL_TO_0.00100_BTC_TIMES_COMPLETED_SIGNAL_CLOSE_"
            "CAPPED_BY_NATIVE_FREE_QUOTE_AND_FEE_ROUNDING_RESERVES"
        )
        spot_buy_sizing_mode = "QUOTE_NOTIONAL_FROM_COMPLETED_SIGNAL_CLOSE"
        entry = "BUY_ON_FIXED_COMPLETED_BAR_ENDING_2025_01_01T00_00_00Z"
        exit_rule = "NO_EXIT_TERMINAL_OPEN_POSITION_DISCLOSED"
        warmup = "ONE_COMPLETE_MINUTE_BEFORE_SCORING"
    elif profile is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
        instrument_id = "BTCUSDT-PERP.BINANCE"
        plan = StrategyPlan(
            intents_by_bar_ns={
                1_735_718_220_000_000_000: (
                    _intent("BUY", "0.004", "M3_PERP_OPEN_LONG_BEFORE_FUNDING"),
                ),
                1_735_718_280_000_000_000: (
                    _intent("SELL", "0.001", "M3_PERP_REDUCE_LONG_BEFORE_FUNDING"),
                ),
                1_735_718_460_000_000_000: (
                    _intent("SELL", "0.003", "M3_PERP_CLOSE_EXACTLY_FLAT_AFTER_FUNDING"),
                ),
                1_735_718_520_000_000_000: (
                    _intent("SELL", "0.001", "M3_PERP_REOPEN_SHORT_FROM_FLAT"),
                ),
            },
            conflict_rule="FIRST_ELIGIBLE_INTENT",
            qualification_attempt_all_intents=False,
        )
        base_release = PERPETUAL_BASE_RELEASE_ID
        sizing = "FIXED_0.004_OPEN_0.001_REDUCE_0.003_CLOSE_0.001_REOPEN"
        spot_buy_sizing_mode = "NOT_APPLICABLE"
        entry = "FROZEN_COMPLETED_BAR_SEQUENCE"
        exit_rule = "REDUCE_THEN_EXACT_FLAT_THEN_SEPARATE_SHORT_FROM_FLAT"
        warmup = "ZERO_DURATION_EXPLICIT_QUALIFICATION_WARMUP"
    else:
        raise ValueError(f"unsupported M3 profile {profile!r}")

    parameters = {
        "base_dataset_release_id": base_release,
        "fee_rate": "0.001",
        "network_access": "FORBIDDEN",
        "m3_profile_qualification": "true",
        "qualification_interval_exposed": "true",
        "result_dependent_branching": "false",
        "run_purpose": "QUALIFICATION",
        "spot_buy_sizing_mode": spot_buy_sizing_mode,
        "strategy_plan_sha256": plan.strategy_plan_sha256,
    }
    strategy_spec = StrategySpec(
        strategy_id=f"m3-{profile.value.lower()}-mechanical-qualification",
        strategy_version="1",
        market_profile=profile,
        instrument_id=instrument_id,
        signal_bar_types=(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL",),
        parameters=parameters,
        indicator_definitions=(),
        warmup_requirement=warmup,
        sizing_rule=sizing,
        entry_rule=entry,
        exit_rule=exit_rule,
        conflict_rule="FIRST_ELIGIBLE_INTENT",
        terminal_behavior="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
        market_order_time_in_force="GTC",
    )
    return QualificationStrategyInputs(strategy_spec=strategy_spec, strategy_plan=plan)


def bind_qualification_plan(
    profile: MarketProfile,
    *,
    plan: StrategyPlan,
    qualification_label: str,
) -> QualificationStrategyInputs:
    """Bind a negative-control schedule without weakening the positive strategy contract."""

    if not qualification_label:
        raise ValueError("qualification_label must not be empty")
    base = qualification_strategy_inputs(profile)
    parameters = dict(base.strategy_spec.parameters)
    parameters["strategy_plan_sha256"] = plan.strategy_plan_sha256
    parameters["qualification_control"] = qualification_label
    spec = replace(
        base.strategy_spec,
        strategy_id=f"{base.strategy_spec.strategy_id}-{qualification_label.lower()}",
        parameters=parameters,
    )
    return QualificationStrategyInputs(strategy_spec=spec, strategy_plan=plan)


def negative_qualification_inputs(control: M3NegativeControl) -> QualificationStrategyInputs:
    """Return one frozen pre-submit negative-control schedule."""

    if control is M3NegativeControl.SPOT_SHORT:
        profile = MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        plan = StrategyPlan(
            {1_735_689_600_000_000_000: (_intent("SELL", "0.00100", control.value),)},
            "FIRST_ELIGIBLE_INTENT",
            False,
        )
    elif control is M3NegativeControl.PERP_DIRECT_CROSS_ZERO:
        profile = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
        plan = StrategyPlan(
            {
                1_735_718_220_000_000_000: (_intent("BUY", "0.004", "CONTROL_OPEN"),),
                1_735_718_280_000_000_000: (_intent("SELL", "0.005", control.value),),
            },
            "FIRST_ELIGIBLE_INTENT",
            False,
        )
    elif control is M3NegativeControl.PERP_CONCURRENT_ORDER:
        profile = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
        plan = StrategyPlan(
            {
                1_735_718_220_000_000_000: (
                    _intent("BUY", "0.004", "CONTROL_FIRST_ORDER"),
                    _intent("BUY", "0.001", control.value),
                ),
            },
            "FIRST_ELIGIBLE_INTENT",
            True,
        )
    elif control is M3NegativeControl.PERP_ABOVE_MARKET_MAX:
        profile = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
        plan = StrategyPlan(
            {1_735_718_220_000_000_000: (_intent("BUY", "120.001", control.value),)},
            "FIRST_ELIGIBLE_INTENT",
            False,
        )
    elif control is M3NegativeControl.PERP_POST_BOUNDARY_OPEN:
        profile = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
        plan = StrategyPlan(
            {1_735_718_400_000_000_000: (_intent("BUY", "0.001", control.value),)},
            "FIRST_ELIGIBLE_INTENT",
            False,
        )
    else:  # pragma: no cover - closed enum protects this branch
        raise ValueError(f"unsupported M3 negative control {control!r}")
    return bind_qualification_plan(
        profile,
        plan=plan,
        qualification_label=control.value,
    )


def _iso(value: Any) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _repository_root(value: Path) -> Path:
    return require_repository_root(value)


def _base_release(profile: MarketProfile, *, repository_root: Path) -> DatasetRelease:
    identity = (
        SPOT_BASE_RELEASE_ID
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else PERPETUAL_BASE_RELEASE_ID
    )
    return DatasetRelease.from_json_bytes(
        (repository_root / "data/releases" / f"{identity}.json").read_bytes(),
    )


def qualification_dataset_release(
    profile: MarketProfile,
    *,
    repository_root: Path,
) -> DatasetRelease:
    """Load the immutable additive qualification release through the M2 contract."""

    repository = _repository_root(repository_root)
    identity = (
        SPOT_QUALIFICATION_RELEASE_ID
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else PERPETUAL_QUALIFICATION_RELEASE_ID
    )
    release = DatasetRelease.from_json_bytes(
        (repository / "data/releases" / f"{identity}.json").read_bytes(),
    )
    validate_m3_dataset_release(release, repository_root=repository)
    return release


def validate_m3_dataset_release(
    release: DatasetRelease,
    *,
    repository_root: Path,
) -> None:
    """Require the repaired M2 provenance while permitting an additive fee binding."""

    repository = _repository_root(repository_root)
    if (
        not isinstance(release, DatasetRelease)
        or release.schema_version != 2
        or release.normalizer_version
        != M3_QUALIFICATION_FULL_RAW_INVENTORY_NORMALIZER_VERSION
        or not release.has_full_raw_inventory
    ):
        raise ValueError(
            "M3 requires a schema-v2 qualification DatasetRelease with full Raw inventory",
        )
    base = _base_release(release.market_profile, repository_root=repository)
    immutable_equal = (
        release.market_profile is base.market_profile
        and release.instrument_id == base.instrument_id
        and release.source_objects == base.source_objects
        and release.normalized_time_range == base.normalized_time_range
        and release.execution_bar_interval == base.execution_bar_interval
        and release.available_signal_bar_intervals == base.available_signal_bar_intervals
        and release.mark_data_identity == base.mark_data_identity
        and release.timestamp_rules_identity == base.timestamp_rules_identity
        and release.completeness_result == base.completeness_result
    )
    if not immutable_equal:
        raise ValueError("M3 qualification release diverges from repaired frozen market data")
    resolved = release.resolve_runtime_data(repository / "data")
    if (
        Decimal(str(resolved.instrument.maker_fee)) != M3_FEE_RATE
        or Decimal(str(resolved.instrument.taker_fee)) != M3_FEE_RATE
    ):
        raise ValueError("M3 requires the explicit observable Appendix A fee assumption")


def build_m3_request(
    release: DatasetRelease,
    *,
    source_revision: SourceRevision,
    evidence_root: Path,
    repository_root: Path,
    run_id: str,
    strategy_inputs: QualificationStrategyInputs | None = None,
    qualification_control: QualificationControl = QualificationControl.STANDARD,
) -> LabRunRequest:
    """Bind a strict DatasetRelease directly to the public M1 ``run_lab`` call."""

    repository = _repository_root(repository_root)
    validate_m3_dataset_release(release, repository_root=repository)
    if not source_revision.clean_worktree:
        raise ValueError("accepted M3 qualification requires a clean SourceRevision")
    inputs = strategy_inputs or qualification_strategy_inputs(release.market_profile)
    if inputs.strategy_spec.market_profile is not release.market_profile:
        raise ValueError("M3 StrategySpec profile does not match DatasetRelease")
    if inputs.strategy_spec.instrument_id != release.instrument_id:
        raise ValueError("M3 StrategySpec Instrument does not match DatasetRelease")
    if (
        inputs.strategy_spec.parameters.get("strategy_plan_sha256")
        != inputs.strategy_plan.strategy_plan_sha256
    ):
        raise ValueError("M3 StrategySpec does not bind the exact StrategyPlan")

    template = repository / "configs/m3/qualification-run-template.json"
    raw = copy.deepcopy(json.loads(template.read_text(encoding="utf-8")))
    scoring_start = (
        release.normalized_time_range.start_inclusive + timedelta(minutes=1)
        if release.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else release.normalized_time_range.start_inclusive
    )
    raw.update(
        {
            "run_id": run_id,
            "run_purpose": "QUALIFICATION",
            "market_profile": release.market_profile.value,
            "instrument_id": release.instrument_id,
            "dataset_release_id": release.dataset_release_id,
            "strategy_spec_id": inputs.strategy_spec.strategy_spec_id,
            "initial_capital": {"amount": "1000.00", "currency": "USDT"},
            "warmup_start": _iso(release.normalized_time_range.start_inclusive),
            "scoring_start": _iso(scoring_start),
            "scoring_end_exclusive": _iso(release.normalized_time_range.end_exclusive),
            "execution_bar_type": f"{release.instrument_id}-1-MINUTE-LAST-EXTERNAL",
            "signal_bar_types": [f"{release.instrument_id}-1-MINUTE-LAST-EXTERNAL"],
            "funding_binding": release.funding_data_identity,
            "mark_binding": release.mark_data_identity,
            "fee_assumption": {
                "maker_fee": "0.001",
                "taker_fee": "0.001",
                "explicit_zero_fee": False,
                "reason": "SSOT Appendix A qualification-only observable estimated fee",
                "claim_class": "ESTIMATED_FEE",
            },
        },
    )
    venue = raw["nautilus_venue_config"]
    venue["starting_balances"] = [raw["initial_capital"]]
    venue["account_type"] = (
        "CASH"
        if release.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else "MARGIN"
    )
    venue["instrument_leverages"] = (
        []
        if release.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else [{"instrument_id": release.instrument_id, "leverage": "1"}]
    )
    raw["nautilus_engine_config"]["portfolio"]["use_mark_prices"] = (
        release.market_profile
        is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
    )
    raw["nautilus_data_config"] = [
        {
            "catalog_path": str(repository / "data/catalog" / release.catalog_identity),
            "catalog_fs_protocol": "file",
            "catalog_fs_storage_options": {},
            "catalog_fs_rust_storage_options": {},
            "data_type": "Bar",
            "instrument_id": release.instrument_id,
            "start_time": _iso(release.normalized_time_range.start_inclusive),
            "end_time": _iso(release.normalized_time_range.end_exclusive),
            "filter_expr": "NOT_APPLICABLE",
            "client_id": "NOT_APPLICABLE",
            "metadata": {},
            "bar_spec": "1-MINUTE-LAST",
            "instrument_ids": [],
            "bar_types": [],
            "optimize_file_loading": False,
        },
    ]
    config = LabRunConfig.from_json_bytes(
        json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    return LabRunRequest(
        lab_run_config=config,
        source_revision=source_revision,
        strategy_spec=inputs.strategy_spec,
        dataset_release=release,
        instrument=None,
        data=(),
        strategy_plan=inputs.strategy_plan,
        evidence_root=Path(evidence_root),
        repository_root=repository,
        qualification_control=qualification_control,
    )


__all__ = [
    "EXPOSED_QUALIFICATION_LIMITATION",
    "MechanicalIntegrity",
    "MechanicalIntegrityResult",
    "M3NegativeControl",
    "M3_FEE_RATE",
    "PERPETUAL_BASE_RELEASE_ID",
    "PERPETUAL_QUALIFICATION_RELEASE_ID",
    "ProfileQualificationState",
    "QualificationDownstreamBundle",
    "QualificationStrategyInputs",
    "QualifiedProfileRecord",
    "QualifiedProfileRegistry",
    "SPOT_BASE_RELEASE_ID",
    "SPOT_QUALIFICATION_RELEASE_ID",
    "bind_qualification_plan",
    "build_m3_request",
    "qualification_strategy_inputs",
    "qualification_dataset_release",
    "negative_qualification_inputs",
    "compare_deterministic_replay",
    "validate_m3_dataset_release",
]
