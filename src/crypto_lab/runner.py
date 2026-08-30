"""Minimal M1 causal Nautilus runner and stable persisted evidence interface."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.execution import StaticLatencyModel
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import FundingRateUpdate
from nautilus_trader.model import MarkPriceUpdate

from crypto_lab.checker import CheckerOutcome
from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.config import RunPurpose
from crypto_lab.config import RuntimeLock
from crypto_lab.config import SourceRevision
from crypto_lab.data import DataContractError
from crypto_lab.data import DatasetRelease
from crypto_lab.data import ResolvedDatasetRelease
from crypto_lab.data import SourceRole
from crypto_lab.data import SyntheticQualificationDatasetRelease
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.git_identity import GitIdentityError
from crypto_lab.git_identity import capture_actual_source_revision
from crypto_lab.git_identity import verify_source_revision
from crypto_lab.nautilus_config import add_venue_from_config
from crypto_lab.nautilus_config import to_nautilus_engine_config
from crypto_lab.native_positions import capture_native_completed_position_sequence
from crypto_lab.offline import OfflineBoundaryUnavailable
from crypto_lab.offline import NetworkAttemptBlocked
from crypto_lab.offline import activate_process_network_isolation
from crypto_lab.offline import offline_network_guard
from crypto_lab.paths import atomic_create_run_directory
from crypto_lab.paths import validate_safe_component
from crypto_lab.runtime import RuntimeLockMismatch
from crypto_lab.runtime import verify_runtime_lock
from crypto_lab.status import FailureCode
from crypto_lab.status import RunState
from crypto_lab.timestamps import utc_datetime_to_ns
from crypto_lab.strategies import GuardedCausalStrategy
from crypto_lab.strategies import RegisteredStrategyIdentity
from crypto_lab.strategies import StrategyPlan
from crypto_lab.strategies import StrategySpec
from crypto_lab.strategies import create_registered_strategy
from crypto_lab.strategies import resolve_registered_strategy_identity


ROOT = Path(__file__).resolve().parents[2]
ONE_MINUTE_NS = 60_000_000_000


class QualificationControl(StrEnum):
    STANDARD = "STANDARD"
    ZERO_LATENCY_NEGATIVE_CONTROL = "ZERO_LATENCY_NEGATIVE_CONTROL"
    NETWORK_ATTEMPT_NEGATIVE_CONTROL = "NETWORK_ATTEMPT_NEGATIVE_CONTROL"


@dataclass(frozen=True)
class LabRunRequest:
    """Qualification-only schedule boundary; never an Official Research request."""

    lab_run_config: LabRunConfig
    source_revision: SourceRevision
    strategy_spec: StrategySpec
    dataset_release: DatasetRelease | SyntheticQualificationDatasetRelease
    instrument: Any | None
    data: tuple[Any, ...]
    strategy_plan: StrategyPlan
    evidence_root: Path
    qualification_control: QualificationControl

    def __post_init__(self) -> None:
        if not isinstance(self.lab_run_config, LabRunConfig):
            raise TypeError("lab_run_config must be LabRunConfig")
        if not isinstance(self.source_revision, SourceRevision):
            raise TypeError("source_revision must be SourceRevision")
        if not isinstance(self.strategy_spec, StrategySpec):
            raise TypeError("strategy_spec must be StrategySpec")
        if not isinstance(self.strategy_plan, StrategyPlan):
            raise TypeError("strategy_plan must be StrategyPlan")
        if not isinstance(self.evidence_root, Path):
            raise TypeError("evidence_root must be pathlib.Path")
        if not isinstance(self.qualification_control, QualificationControl):
            raise TypeError("qualification_control must be QualificationControl")
        if not isinstance(
            self.dataset_release,
            DatasetRelease | SyntheticQualificationDatasetRelease,
        ):
            raise TypeError("dataset_release must be a strict DatasetRelease contract")
        if self.lab_run_config.run_purpose is not RunPurpose.QUALIFICATION:
            raise ValueError(
                "CONFIG_INVALID: StrategyPlan and QualificationControl are forbidden outside QUALIFICATION",
            )
        object.__setattr__(self, "data", tuple(self.data))


@dataclass(frozen=True)
class OfficialLabRunRequest:
    """Strict Official/Research request resolved through the closed Strategy registry."""

    lab_run_config: LabRunConfig
    source_revision: SourceRevision
    strategy_spec: StrategySpec
    dataset_release: DatasetRelease
    registered_strategy_id: str
    evidence_root: Path
    repository_root: Path

    def __post_init__(self) -> None:
        if not isinstance(self.lab_run_config, LabRunConfig):
            raise TypeError("lab_run_config must be LabRunConfig")
        if self.lab_run_config.run_purpose not in {RunPurpose.RESEARCH, RunPurpose.OFFICIAL}:
            raise ValueError("CONFIG_INVALID: Official boundary requires RESEARCH or OFFICIAL purpose")
        if not isinstance(self.source_revision, SourceRevision):
            raise TypeError("source_revision must be SourceRevision")
        if not isinstance(self.strategy_spec, StrategySpec):
            raise TypeError("strategy_spec must be StrategySpec")
        if not isinstance(self.dataset_release, DatasetRelease):
            raise TypeError("Official boundary requires a strict non-synthetic DatasetRelease")
        if not isinstance(self.registered_strategy_id, str) or not self.registered_strategy_id:
            raise TypeError("registered_strategy_id must be a non-empty registry identifier")
        if not isinstance(self.evidence_root, Path) or not isinstance(self.repository_root, Path):
            raise TypeError("evidence_root and repository_root must be pathlib.Path")
        # Reject an unregistered or incomplete material identity before an
        # Official request can reach preflight or evidence creation.
        self.strategy_identity

    @property
    def strategy_identity(self) -> RegisteredStrategyIdentity:
        return resolve_registered_strategy_identity(
            self.registered_strategy_id,
            strategy_spec=self.strategy_spec,
            source_revision=self.source_revision,
        )


@dataclass(frozen=True)
class RunResult:
    run_id: str
    state: RunState
    failure_codes: tuple[str, ...]
    checker_outcome: CheckerOutcome
    config_sha256: str
    semantic_digest: str
    evidence_dir: Path
    evidence_inventory: tuple[tuple[str, str], ...]
    orders: tuple[dict[str, Any], ...]
    fills: tuple[dict[str, Any], ...]
    positions: tuple[dict[str, Any], ...]
    account_events: tuple[dict[str, Any], ...]
    funding_events: tuple[dict[str, Any], ...]
    strategy_observations: dict[str, Any]

    def to_builtins(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "state": self.state.value,
            "failure_codes": list(self.failure_codes),
            "checker_outcome": self.checker_outcome.value,
            "config_sha256": self.config_sha256,
            "semantic_digest": self.semantic_digest,
            "evidence_dir": str(self.evidence_dir),
            "evidence_inventory": [
                {"path": path, "sha256": digest}
                for path, digest in self.evidence_inventory
            ],
        }


def capture_source_revision(repository: Path = ROOT) -> SourceRevision:
    """Capture Git commit/tree provenance separately from Runtime Lock identity."""

    return capture_actual_source_revision(repository)


def _timestamp_ns(value: datetime) -> int:
    return utc_datetime_to_ns(value)


def select_engine_data_window(
    data: tuple[Any, ...],
    *,
    warmup_start_ns: int,
    scoring_end_exclusive_ns: int,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Select the type-aware half-open economic window before engine ingestion.

    Bar and Mark objects represent completed intervals and are eligible when
    their availability timestamp is in ``(warmup_start, scoring_end]``. Funding
    is a point settlement and is eligible only in
    ``[warmup_start, scoring_end)``. Thus a final completed valuation
    observation at the boundary is allowed while a settlement at that same
    boundary is not.
    """

    if warmup_start_ns < 0 or scoring_end_exclusive_ns <= warmup_start_ns:
        raise DataContractError(
            FailureCode.DATA_TIMESTAMP_INVALID,
            "invalid execution data window",
        )
    selected: list[Any] = []
    dropped_before = 0
    dropped_after = 0
    selected_counts = {"Bar": 0, "MarkPriceUpdate": 0, "FundingRateUpdate": 0}
    for item in data:
        timestamp = int(item.ts_init)
        if isinstance(item, FundingRateUpdate):
            eligible = warmup_start_ns <= timestamp < scoring_end_exclusive_ns
            before = timestamp < warmup_start_ns
        elif isinstance(item, Bar | MarkPriceUpdate):
            eligible = warmup_start_ns < timestamp <= scoring_end_exclusive_ns
            before = timestamp <= warmup_start_ns
        else:
            raise DataContractError(
                FailureCode.DATA_ROLE_MISMATCH,
                f"unsupported engine data object {type(item).__name__}",
            )
        if eligible:
            selected.append(item)
            selected_counts[type(item).__name__] += 1
        elif before:
            dropped_before += 1
        else:
            dropped_after += 1
    if not any(isinstance(item, Bar) for item in selected):
        raise DataContractError(FailureCode.DATA_GAP, "execution window has no eligible Bar")
    valuation_times = [
        int(item.ts_init)
        for item in selected
        if isinstance(item, Bar | MarkPriceUpdate)
    ]
    return (
        tuple(selected),
        {
            "schema": "engine-data-window-v1",
            "warmup_start_ns": warmup_start_ns,
            "scoring_end_exclusive_ns": scoring_end_exclusive_ns,
            "source_object_count": len(data),
            "engine_object_count": len(selected),
            "selected_counts": selected_counts,
            "dropped_before_warmup_count": dropped_before,
            "dropped_after_scoring_count": dropped_after,
            "point_events_at_scoring_end_included": False,
            "completed_interval_observations_at_scoring_end_included": True,
            "latest_qualified_valuation_observation_ns": max(valuation_times),
            "engine_received_post_boundary_data": False,
        },
    )


def _preflight_identity(
    config: LabRunRequest | OfficialLabRunRequest,
) -> tuple[list[str], list[dict[str, Any]]]:
    run = config.lab_run_config
    failures: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    repository_root = config.repository_root if isinstance(config, OfficialLabRunRequest) else ROOT
    observed_runtime_lock_sha256 = sha256_file(repository_root / "runtime.lock.json")
    if observed_runtime_lock_sha256 != run.runtime_lock_sha256:
        failures.append(FailureCode.RUNTIME_LOCK_MISMATCH.value)
        diagnostics.append(
            {
                "phase": "RUNTIME_LOCK_BYTES",
                "code": FailureCode.RUNTIME_LOCK_MISMATCH.value,
                "expected_runtime_lock_sha256": run.runtime_lock_sha256,
                "observed_runtime_lock_sha256": observed_runtime_lock_sha256,
            },
        )
    else:
        lock = RuntimeLock.from_json_bytes((repository_root / "runtime.lock.json").read_bytes())
        try:
            verify_runtime_lock(
                lock,
                dependency_lock_path=repository_root / "requirements.lock.txt",
            )
        except RuntimeLockMismatch as exc:
            failures.append(FailureCode.RUNTIME_LOCK_MISMATCH.value)
            diagnostics.append(
                {
                    "phase": "INSTALLED_RUNTIME_FILES",
                    "code": exc.code,
                    "mismatches": list(exc.mismatches),
                },
            )
        except Exception as exc:
            failures.append(FailureCode.RUNTIME_LOCK_MISMATCH.value)
            diagnostics.append(
                {
                    "phase": "INSTALLED_RUNTIME_FILES",
                    "code": FailureCode.RUNTIME_LOCK_MISMATCH.value,
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                },
            )

    if config.strategy_spec.strategy_spec_id != run.strategy_spec_id:
        failures.append(FailureCode.CONFIG_HASH_MISMATCH.value)
    if isinstance(config, LabRunRequest):
        if "strategy_plan_sha256" in config.strategy_spec.parameters and (
            config.strategy_spec.parameters["strategy_plan_sha256"]
            != config.strategy_plan.strategy_plan_sha256
        ):
            failures.append(FailureCode.CONFIG_HASH_MISMATCH.value)
    else:
        try:
            config.strategy_identity
        except Exception:
            failures.append(FailureCode.CONFIG_INVALID.value)
    if (
        config.strategy_spec.market_profile is not run.market_profile
        or config.strategy_spec.instrument_id != run.instrument_id
        or config.strategy_spec.signal_bar_types != run.signal_bar_types
    ):
        failures.append(FailureCode.CONFIG_INVALID.value)
    release = config.dataset_release
    if release.dataset_release_id != run.dataset_release_id:
        failures.append(FailureCode.DOWNSTREAM_CONTRACT_FAILURE.value)
    if canonical_sha256(release.material_payload()) != release.dataset_release_id:
        failures.append(FailureCode.DATA_HASH_MISMATCH.value)
    if release.market_profile is not run.market_profile or release.instrument_id != run.instrument_id:
        failures.append(FailureCode.DOWNSTREAM_CONTRACT_FAILURE.value)
    if isinstance(release, DatasetRelease):
        if not release.is_current_contract:
            failures.append(FailureCode.DATASET_RELEASE_STALE.value)
        required_start = run.warmup_start
        required_end = run.scoring_end_exclusive
        if (
            required_start < release.normalized_time_range.start_inclusive
            or required_end > release.normalized_time_range.end_exclusive
            or not any(
                interval.start_inclusive <= run.scoring_start
                and interval.end_exclusive >= required_end
                for interval in release.available_signal_bar_intervals
            )
        ):
            failures.append(FailureCode.DOWNSTREAM_CONTRACT_FAILURE.value)
        if run.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
            if run.mark_binding != "NOT_APPLICABLE" or run.funding_binding != "NOT_APPLICABLE":
                failures.append(FailureCode.DATA_ROLE_MISMATCH.value)
        else:
            if run.mark_binding != release.mark_data_identity:
                failures.append(FailureCode.MARK_ROLE_INVALID.value)
            if run.funding_binding != release.funding_data_identity:
                failures.append(FailureCode.FUNDING_MISSING.value)
        expected_catalog = (repository_root / "data/catalog" / release.catalog_identity).resolve()
        configured_catalogs = {
            Path(item.catalog_path).resolve()
            for item in run.nautilus_data_config
        }
        if configured_catalogs != {expected_catalog}:
            failures.append(FailureCode.DOWNSTREAM_CONTRACT_FAILURE.value)
        if isinstance(config, LabRunRequest) and (config.instrument is not None or config.data):
            failures.append(FailureCode.DOWNSTREAM_CONTRACT_FAILURE.value)
    else:
        if run.run_purpose is not RunPurpose.QUALIFICATION:
            failures.append(FailureCode.DATA_SOURCE_INVALID.value)
    try:
        source_tree = subprocess.run(
            ["git", "rev-parse", f"{config.source_revision.git_commit}^{{tree}}"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception:
        failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)
    else:
        if source_tree != config.source_revision.git_tree:
            failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)
    if isinstance(config, LabRunRequest) and (
        config.qualification_control is not QualificationControl.STANDARD
        and run.run_purpose is not RunPurpose.QUALIFICATION
    ):
        failures.append(FailureCode.CONFIG_INVALID.value)

    if isinstance(config, OfficialLabRunRequest):
        try:
            verify_source_revision(
                config.source_revision,
                repository=repository_root,
                require_current_head=True,
                require_clean=True,
            )
        except GitIdentityError:
            failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)
    if config.strategy_spec.parameters.get("m3_profile_qualification") == "true":
        if run.run_purpose is not RunPurpose.QUALIFICATION or not isinstance(
            release,
            DatasetRelease,
        ):
            failures.append(FailureCode.CONFIG_INVALID.value)
        if not config.source_revision.clean_worktree:
            failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)
        try:
            verify_source_revision(
                config.source_revision,
                repository=ROOT,
                require_current_head=True,
                require_clean=True,
            )
        except GitIdentityError:
            failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)
    return list(dict.fromkeys(failures)), diagnostics


def _preflight_data(
    config: LabRunRequest | OfficialLabRunRequest,
    *,
    instrument: Any,
    data: tuple[Any, ...],
    resolved: ResolvedDatasetRelease | None,
) -> list[str]:
    run = config.lab_run_config
    release = config.dataset_release
    failures: list[str] = []
    if instrument is None or str(instrument.id) != run.instrument_id:
        failures.append(FailureCode.INSTRUMENT_METADATA_INVALID.value)
        return failures
    if Decimal(str(instrument.maker_fee)) != run.fee_assumption.maker_fee or Decimal(
        str(instrument.taker_fee),
    ) != run.fee_assumption.taker_fee:
        failures.append(FailureCode.FEE_MISSING.value)

    bars = [item for item in data if isinstance(item, Bar)]
    marks = [item for item in data if isinstance(item, MarkPriceUpdate)]
    funding = [item for item in data if isinstance(item, FundingRateUpdate)]
    if any(
        not isinstance(item, Bar | MarkPriceUpdate | FundingRateUpdate)
        for item in data
    ):
        failures.append(FailureCode.DATA_ROLE_MISMATCH.value)
    if not bars:
        failures.append(FailureCode.DATA_GAP.value)
    bar_timestamps: list[int] = []
    for bar in bars:
        bar_timestamps.append(int(bar.ts_init))
        if isinstance(release, SyntheticQualificationDatasetRelease):
            precision_ok = all(
                price.precision == instrument.price_precision
                for price in (bar.open, bar.high, bar.low, bar.close)
            ) and bar.volume.precision == instrument.size_precision
        else:
            # A current instrument definition is explicitly not presented as
            # historical point-in-time tick/lot metadata.  Canonical Binance
            # Bars retain their exact source Decimal representation, which can
            # be finer than today's order increment (notably 2021 USD-M prices
            # and Spot base volume).  Rounding those values to current metadata
            # would mutate official data.  Order quantities remain guarded
            # separately against the native Instrument precision/increments.
            precision_ok = all(
                Decimal(str(value)).is_finite()
                for value in (bar.open, bar.high, bar.low, bar.close, bar.volume)
            )
        identity_ok = (
            str(bar.bar_type.instrument_id) == run.instrument_id
            and str(bar.bar_type) == run.execution_bar_type
        )
        volume = bar.volume.as_decimal()
        volume_ok = (
            volume > 0
            if isinstance(release, SyntheticQualificationDatasetRelease)
            else volume >= 0
        )
        ohlc_ok = (
            bar.high >= bar.open
            and bar.high >= bar.close
            and bar.low <= bar.open
            and bar.low <= bar.close
            and bar.high >= bar.low
            and volume_ok
        )
        minimum_price = (
            None if instrument.min_price is None else instrument.min_price.as_decimal()
        )
        maximum_price = (
            None if instrument.max_price is None else instrument.max_price.as_decimal()
        )
        increment = instrument.price_increment.as_decimal()
        observed_prices = tuple(
            value.as_decimal() for value in (bar.open, bar.high, bar.low, bar.close)
        )
        executable_price_bounds_ok = bool(
            minimum_price is not None
            and maximum_price is not None
            and all(minimum_price <= value <= maximum_price for value in observed_prices)
            and bar.open.as_decimal() - increment >= minimum_price
            and bar.open.as_decimal() + increment <= maximum_price
        )
        timestamp_ok = int(bar.ts_init) == int(bar.ts_event)
        if not (
            precision_ok
            and identity_ok
            and ohlc_ok
            and timestamp_ok
            and executable_price_bounds_ok
        ):
            failures.append(FailureCode.INSTRUMENT_METADATA_INVALID.value)
    if bar_timestamps != sorted(bar_timestamps) or len(set(bar_timestamps)) != len(bar_timestamps):
        failures.append(FailureCode.DATA_TIMESTAMP_INVALID.value)
    if isinstance(release, SyntheticQualificationDatasetRelease):
        warmup_start_ns = _timestamp_ns(run.warmup_start)
        scoring_end_ns = _timestamp_ns(run.scoring_end_exclusive)
        required_bar_timestamps = list(
            range(
                warmup_start_ns + ONE_MINUTE_NS,
                scoring_end_ns + 1,
                ONE_MINUTE_NS,
            ),
        )
        execution_window_bar_timestamps = [
            timestamp
            for timestamp in bar_timestamps
            if warmup_start_ns < timestamp <= scoring_end_ns
        ]
        if execution_window_bar_timestamps != required_bar_timestamps:
            failures.append(FailureCode.DATA_GAP.value)
        descriptors = tuple(
            {
                "type": type(item).__name__,
                "instrument_id": str(
                    item.instrument_id if not isinstance(item, Bar) else item.bar_type.instrument_id
                ),
                "ts_event": int(item.ts_event),
                "ts_init": int(item.ts_init),
                "value": str(item),
            }
            for item in data
        )
        if descriptors != tuple(item.to_builtins() for item in release.data):
            failures.append(FailureCode.DATA_HASH_MISMATCH.value)
    else:
        expected = list(
            range(
                release.normalized_time_range.start_ns + ONE_MINUTE_NS,
                release.normalized_time_range.end_ns + 1,
                ONE_MINUTE_NS,
            ),
        )
        catalog_bound = bool(
            resolved is not None
            and canonical_sha256(resolved.semantic_inventory) == release.catalog_identity
        )
        if not catalog_bound:
            failures.append(FailureCode.DATASET_RELEASE_STALE.value)
        if run.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
            # The adopted Spot contract has a complete minute-disposition grid,
            # not a synthetic Bar grid.  A VERIFIED_NO_TRADE_INTERVAL therefore
            # appears as an intentional gap in canonical Bars.  Accept that
            # sparse projection only when the exact catalog identity binds the
            # complete coverage identity and all observed Bars remain aligned
            # inside the frozen release window.
            if bar_timestamps != expected:
                role_results = tuple(release.completeness_result.role_results)
                spot_roles = tuple(
                    item
                    for item in role_results
                    if item.source_role is SourceRole.SPOT_EXECUTION_1M
                )
                release_binding = (
                    resolved.semantic_inventory.get("release_binding")
                    if resolved is not None
                    else None
                )
                expected_binding = {
                    "data_window_identity": release.data_window_identity,
                    "partition_geometry_identity": release.partition_geometry_identity,
                    "minute_coverage_identity": release.minute_coverage_identity,
                    "normalized_time_range": release.normalized_time_range.to_builtins(),
                }
                sparse_grid_ok = bool(
                    catalog_bound
                    and release.completeness_result.status == "PASS"
                    and release.completeness_result.no_repairs is True
                    and len(spot_roles) == 1
                    and spot_roles[0].expected_count == len(expected)
                    and spot_roles[0].actual_count == len(expected)
                    and release_binding == expected_binding
                    and bar_timestamps
                    and len(bar_timestamps) < len(expected)
                    and all(
                        release.normalized_time_range.start_ns < timestamp
                        <= release.normalized_time_range.end_ns
                        and (
                            timestamp - release.normalized_time_range.start_ns
                        )
                        % ONE_MINUTE_NS
                        == 0
                        for timestamp in bar_timestamps
                    )
                )
                if not sparse_grid_ok:
                    failures.append(FailureCode.DATA_GAP.value)
        elif bar_timestamps != expected:
            # Perpetual execution retains an exact 1m Bar grid; the Spot
            # verified-no-trade exception never applies to this profile.
            failures.append(FailureCode.DATA_GAP.value)
    if isinstance(config, LabRunRequest) and not set(
        config.strategy_plan.intents_by_bar_ns,
    ).issubset(set(bar_timestamps)):
        failures.append(FailureCode.DATA_TIMESTAMP_INVALID.value)

    if run.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        if marks or funding:
            failures.append(FailureCode.DATA_ROLE_MISMATCH.value)
    else:
        mark_ok = (
            bool(marks) and all(str(mark.instrument_id) == run.instrument_id for mark in marks)
        )
        if isinstance(release, SyntheticQualificationDatasetRelease):
            mark_ok = mark_ok and release.mark_role == "markPriceKlines" and release.mark_complete is True
        else:
            mark_ok = mark_ok and release.mark_data_identity != "NOT_APPLICABLE"
            if [int(mark.ts_init) for mark in marks] != bar_timestamps:
                mark_ok = False
        if not mark_ok:
            failures.append(FailureCode.MARK_ROLE_INVALID.value)
        funding_ok = (
            bool(funding) and all(str(event.instrument_id) == run.instrument_id for event in funding)
        )
        if isinstance(release, SyntheticQualificationDatasetRelease):
            funding_ok = (
                funding_ok
                and release.funding_role == "fundingRate"
                and release.funding_complete is True
            )
        else:
            funding_ok = funding_ok and release.funding_data_identity != "NOT_APPLICABLE"
        if not funding_ok:
            failures.append(FailureCode.FUNDING_MISSING.value)
    all_timestamps = [int(item.ts_init) for item in data]
    if all_timestamps != sorted(all_timestamps):
        failures.append(FailureCode.DATA_TIMESTAMP_INVALID.value)
    return list(dict.fromkeys(failures))


def _write_json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_json_bytes(value) + b"\n")


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _account_rows(account_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event_index, event in enumerate(account_events):
        for balance in event.get("balances", []):
            rows.append(
                {
                    "event_index": event_index,
                    "ts_event": event["ts_event"],
                    "account_id": event["account_id"],
                    "account_type": event["account_type"],
                    "currency": balance["currency"],
                    "total": balance["total"],
                    "locked": balance["locked"],
                    "free": balance["free"],
                    "reported": event["reported"],
                },
            )
    return rows


def _semantic_event(event: dict[str, Any]) -> dict[str, Any]:
    nonsemantic = {
        "event_id",
        "causation_id",
        "client_order_id",
        "venue_order_id",
        "trade_id",
        "position_id",
        "strategy_id",
        "trader_id",
        "run_id",
        "instance_id",
    }
    result = {
        key: value
        for key, value in event.items()
        if key not in nonsemantic
    }
    reason = result.get("reason")
    if isinstance(reason, str) and reason.startswith("funding_settlement:"):
        result["reason"] = "funding_settlement:<NON_SEMANTIC_ID>"
    return result


def _money_projection(value: Any) -> dict[str, str]:
    return {
        "amount": str(value.as_decimal()),
        "currency": str(value.currency),
    }


def _exact_native_signed_position(position: Any) -> Decimal:
    """Project the native fixed-point Quantity with its native direction."""

    quantity = Decimal(str(position.quantity.as_decimal()))
    if position.is_long:
        return quantity
    if position.is_short:
        return -quantity
    if quantity == 0:
        return Decimal(0)
    raise RuntimeError("native Position has quantity but no LONG/SHORT direction")


def _native_statistic_value(value: Any) -> str:
    if isinstance(value, float) and not math.isfinite(value):
        return "UNDEFINED"
    return str(value)


def _native_returns_basis(
    result: Any,
    *,
    native_completed: Any,
    portfolio_snapshots: list[dict[str, Any]],
) -> str:
    """Identify the pinned analyzer's primary returns source without guessing.

    Nautilus 2.0.0rc2 prefers snapshot-backed portfolio daily returns when its
    snapshot gate succeeds, and otherwise exposes closed-Position returns.  We
    reproduce only that source-selection gate here; the return values remain
    the native ``BacktestResult.returns_series`` and are never recalculated.
    """

    day_ns = 86_400_000_000_000
    eligible_days: set[int] = set()
    eligible_currency: str | None = None
    first_eligible_snapshot = True
    portfolio_gate_failed = False
    for snapshot in portfolio_snapshots:
        if snapshot["unpriced_instruments"]:
            continue
        total_equity = snapshot["total_equity"]
        if len(total_equity) != 1:
            portfolio_gate_failed = True
            break
        equity = snapshot["base_currency_equity"] or total_equity[0]
        currency = str(equity["currency"])
        if eligible_currency is not None and currency != eligible_currency:
            portfolio_gate_failed = True
            break
        eligible_currency = currency
        timestamp = int(snapshot["ts_event"])
        day_start = timestamp - timestamp % day_ns
        if first_eligible_snapshot or (timestamp % day_ns == 0 and timestamp > 0):
            day_start = max(0, day_start - day_ns)
        first_eligible_snapshot = False
        eligible_days.add(day_start)

    portfolio_eligible = (
        not portfolio_gate_failed
        and eligible_currency is not None
        and len(eligible_days) >= 2
    )
    native_returns = {
        int(timestamp): float(value)
        for timestamp, value in result.returns_series.items()
    }
    position_returns: dict[int, float] = {}
    for unit in native_completed.units:
        position_returns[unit.closed_ns] = (
            position_returns.get(unit.closed_ns, 0.0) + float(unit.realized_return)
        )

    if portfolio_eligible:
        if not native_returns or any(timestamp % day_ns for timestamp in native_returns):
            raise ValueError(
                "NATIVE_RETURNS_BASIS_AMBIGUOUS: eligible portfolio snapshots disagree "
                "with native daily returns",
            )
        return "PORTFOLIO_DAILY_ACCOUNT_RETURNS"
    if native_returns.keys() != position_returns.keys() or any(
        native_returns[timestamp] != position_returns[timestamp]
        for timestamp in native_returns
    ):
        raise ValueError(
            "NATIVE_RETURNS_BASIS_AMBIGUOUS: primary returns are neither qualified "
            "portfolio daily returns nor native closed-Position returns",
        )
    return "POSITION_RETURNS_FALLBACK"


def _native_statistics(
    result: Any,
    *,
    returns_basis: str,
) -> dict[str, Any]:
    return {
        "schema": "nautilus-native-statistics-v1",
        "stats_pnls": {
            str(currency): {
                str(name): _native_statistic_value(value)
                for name, value in statistics.items()
            }
            for currency, statistics in result.stats_pnls.items()
        },
        "stats_returns": {
            str(name): _native_statistic_value(value)
            for name, value in result.stats_returns.items()
        },
        "stats_general": {
            str(name): _native_statistic_value(value)
            for name, value in result.stats_general.items()
        },
        "returns_series": [
            {
                "ts_event": int(timestamp),
                "return": _native_statistic_value(value),
            }
            for timestamp, value in sorted(result.returns_series.items())
        ],
        "returns_basis": returns_basis,
        "returns_basis_source": (
            "PINNED_PORTFOLIO_ANALYZER_SNAPSHOT_GATE_AT_SOURCE_COMMIT_"
            "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317"
        ),
        "undefined_native_values_preserved": True,
    }


def _native_portfolio_snapshots(engine: BacktestEngine, account: Any | None) -> list[dict[str, Any]]:
    if account is None:
        return []
    rows: list[dict[str, Any]] = []
    for index, snapshot in enumerate(engine.portfolio.snapshots(account.id)):
        rows.append(
            {
                "snapshot_index": index,
                "account_id": str(snapshot.account_id),
                "account_type": str(snapshot.account_type),
                "base_currency": (
                    None if snapshot.base_currency is None else str(snapshot.base_currency)
                ),
                "base_currency_equity": (
                    None
                    if snapshot.base_currency_equity is None
                    else _money_projection(snapshot.base_currency_equity)
                ),
                "total_equity": [_money_projection(item) for item in snapshot.total_equity],
                "realized_pnls": [_money_projection(item) for item in snapshot.realized_pnls],
                "unrealized_pnls": [_money_projection(item) for item in snapshot.unrealized_pnls],
                "is_stale": bool(snapshot.is_stale),
                "stale_instruments": [str(item) for item in snapshot.stale_instruments],
                "stale_currencies": [str(item) for item in snapshot.stale_currencies],
                "unpriced_instruments": [str(item) for item in snapshot.unpriced_instruments],
                "ts_event": int(snapshot.ts_event),
                "ts_init": int(snapshot.ts_init),
            },
        )
    return rows


def _capture_engine(
    engine: BacktestEngine,
    strategy: GuardedCausalStrategy,
    instrument_id: Any,
    *,
    source_run_id: str,
    settlement_currency: str,
    preserved_funding_events: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    orders_native = engine.cache.orders(instrument_id=instrument_id)
    order_rows: list[dict[str, Any]] = []
    order_events: list[dict[str, Any]] = []
    fill_events: list[dict[str, Any]] = []
    for order in orders_native:
        events = [event.to_dict() for event in order.events()]
        order_events.extend(events)
        fill_events.extend(event for event in events if event["type"] == "OrderFilled")
        order_rows.append(
            {
                "client_order_id": str(order.client_order_id),
                "instrument_id": str(order.instrument_id),
                "side": "BUY" if order.is_buy else "SELL",
                "order_type": str(order.order_type),
                "time_in_force": str(order.time_in_force),
                "quantity": str(order.quantity),
                "filled_qty": str(order.filled_qty),
                "leaves_qty": str(order.leaves_qty),
                "status": str(order.status),
                "initialized_ns": events[0]["ts_event"],
                "terminal_ns": events[-1]["ts_event"] if order.is_closed else "",
            },
        )

    positions_native = engine.cache.positions(instrument_id=instrument_id)
    position_rows: list[dict[str, Any]] = []
    for index, position in enumerate(positions_native):
        position_rows.append(
            {
                "row_type": "FINAL_NATIVE_POSITION",
                "event_index": index,
                "ts_event": int(position.ts_last),
                "instrument_id": str(position.instrument_id),
                "side": str(position.side),
                "signed_qty": str(_exact_native_signed_position(position)),
                "quantity": str(position.quantity),
                "avg_px_open": str(position.avg_px_open),
                "realized_pnl": str(position.realized_pnl),
            },
        )
    for index, item in enumerate(strategy.observations["position_sequence"]):
        position_rows.append(
            {
                "row_type": item["event_type"],
                "event_index": index,
                "ts_event": item["timestamp_ns"],
                "instrument_id": str(instrument_id),
                "side": "NATIVE_EVENT_SNAPSHOT",
                "signed_qty": item["signed_position"],
                "quantity": abs(Decimal(item["signed_position"])),
                "avg_px_open": "",
                "realized_pnl": "",
            },
        )

    account = engine.cache.account_for_venue(instrument_id.venue)
    account_events = [] if account is None else [event.to_dict() for event in account.events]
    expected_closed_cycles = sum(
        item.get("event_type") == "PositionClosed"
        for item in strategy.observations["position_sequence"]
    )
    native_completed = capture_native_completed_position_sequence(
        engine.cache,
        instrument_id=instrument_id,
        source_run_id=source_run_id,
        expected_settlement_currency=settlement_currency,
        expected_closed_cycle_count=expected_closed_cycles,
    )
    portfolio_snapshots = _native_portfolio_snapshots(engine, account)
    funding_events: list[dict[str, Any]] = [dict(item) for item in preserved_funding_events]
    for position in positions_native:
        funding_events.extend(
            adjustment.to_dict()
            for adjustment in position.adjustments()
            if str(adjustment.adjustment_type) == "FUNDING"
        )
    funding_events = list(
        {
            str(event.get("event_id", canonical_sha256(event))): event
            for event in funding_events
        }.values(),
    )

    native_result = engine.get_result()
    try:
        unrealized = str(engine.portfolio.unrealized_pnl(instrument_id))
    except Exception:
        unrealized = "UNAVAILABLE"
    try:
        realized = str(engine.portfolio.realized_pnl(instrument_id))
    except Exception:
        realized = "UNAVAILABLE"
    try:
        total = str(engine.portfolio.total_pnl(instrument_id))
    except Exception:
        total = "UNAVAILABLE"
    try:
        equity = {
            str(currency): _money_projection(money)
            for currency, money in engine.portfolio.equity(account_id=account.id).items()
        } if account is not None else {}
    except Exception:
        equity = {}
    semantic = {
        "orders": [_semantic_event(event) for event in order_events],
        "fills": [_semantic_event(event) for event in fill_events],
        "positions": [
            {
                key: row[key]
                for key in (
                    "row_type",
                    "ts_event",
                    "instrument_id",
                    "signed_qty",
                    "quantity",
                    "avg_px_open",
                    "realized_pnl",
                )
            }
            for row in position_rows
        ],
        "account_events": [_semantic_event(event) for event in account_events],
        "funding": [_semantic_event(event) for event in funding_events],
        "native_completed_positions": [
            unit.semantic_payload() for unit in native_completed.units
        ],
        "terminal_portfolio": {"unrealized_pnl": unrealized},
    }
    return {
        "order_rows": order_rows,
        "order_events": order_events,
        "fills": fill_events,
        "positions": position_rows,
        "account_events": account_events,
        "funding_events": funding_events,
        "portfolio_snapshots": portfolio_snapshots,
        "native_completed_trades": native_completed.to_builtins(),
        "native_statistics": _native_statistics(
            native_result,
            returns_basis=_native_returns_basis(
                native_result,
                native_completed=native_completed,
                portfolio_snapshots=portfolio_snapshots,
            ),
        ),
        "backtest_result": {
            "backtest_start": int(native_result.backtest_start),
            "backtest_end": int(native_result.backtest_end),
            "iterations": native_result.iterations,
            "total_events": native_result.total_events,
            "total_orders": native_result.total_orders,
            "total_positions": native_result.total_positions,
        },
        "mark_price_count": engine.cache.mark_price_count(instrument_id),
        "funding_rate_count": engine.cache.funding_rate_count(instrument_id),
        "terminal_portfolio": {
            "realized_pnl": realized,
            "unrealized_pnl": unrealized,
            "total_pnl": total,
            "equity": equity,
            "source": "nautilus_trader.portfolio.Portfolio public API",
        },
        "semantic_sequence": semantic,
        "semantic_digest": canonical_sha256(semantic),
    }


def _run_real_data_with_native_funding_checkpoints(
    engine: BacktestEngine,
    *,
    data: tuple[Any, ...],
    instrument_id: Any,
    funding_source_events: tuple[dict[str, Any], ...] = (),
    start_ns: int | None = None,
    end_ns: int,
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """Run public streaming batches and preserve native adjustments before NETTING reuse.

    The pinned runtime reuses a NETTING position identifier after close-to-flat and
    opposite-side reopen.  Its final cache view therefore no longer carries an
    earlier ``PositionAdjusted(FUNDING)``.  Public streaming mode lets the runner
    take a read-only native checkpoint at each funding boundary, then resume the
    exact same engine.  No account, position, or financial value is changed here.
    """

    boundaries = sorted(
        {
            int(item.next_funding_ns)
            if item.next_funding_ns is not None
            else int(item.ts_init)
            for item in data
            if isinstance(item, FundingRateUpdate)
        },
    )
    if not boundaries:
        engine.add_data(list(data))
        engine.run(start=start_ns, end=end_ns)
        return (), ()
    if boundaries[-1] >= end_ns:
        raise DataContractError(
            FailureCode.DATA_TIMESTAMP_INVALID,
            "funding checkpoint reaches or exceeds scoring_end_exclusive",
        )

    ordered = list(data)
    cursor = 0
    preserved: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []
    first_batch = True
    source_by_boundary = {
        int(item["calc_time_ns"]): item
        for item in funding_source_events
    }
    if len(source_by_boundary) != len(funding_source_events):
        raise DataContractError(
            FailureCode.FUNDING_AMBIGUOUS,
            "duplicate official funding source boundary",
        )
    for boundary_ns in boundaries:
        pre_boundary_start = cursor
        while cursor < len(ordered) and int(ordered[cursor].ts_init) < boundary_ns:
            cursor += 1
        pre_boundary_batch = ordered[pre_boundary_start:cursor]
        if pre_boundary_batch:
            engine.add_data(pre_boundary_batch)
            engine.run(start=start_ns if first_batch else None, streaming=True)
            first_batch = False
            engine.clear_data()

        # This snapshot is the financially eligible position at the instant
        # immediately before the funding boundary.  Orders which first fill on
        # a Bar at the boundary are processed after Mark/Funding data and must
        # not retroactively become eligible for that settlement.
        eligible_positions = [
            {
                "instrument_id": str(position.instrument_id),
                "position_id": str(position.id),
                "signed_qty": str(_exact_native_signed_position(position)),
                "ts_last": int(position.ts_last),
            }
            for position in engine.cache.positions_open(instrument_id=instrument_id)
        ]
        account_before = engine.cache.account_for_venue(instrument_id.venue)
        balances_before = (
            []
            if account_before is None
            else [
                {
                    "currency": str(balance.currency),
                    "total": str(balance.total),
                    "locked": str(balance.locked),
                    "free": str(balance.free),
                }
                for balance in account_before.balances().values()
            ]
        )

        boundary_start = cursor
        while cursor < len(ordered) and int(ordered[cursor].ts_init) == boundary_ns:
            cursor += 1
        boundary_batch = ordered[boundary_start:cursor]
        if not boundary_batch:
            continue
        engine.add_data(boundary_batch)
        engine.run(start=start_ns if first_batch else None, streaming=True)
        first_batch = False
        positions = engine.cache.positions(instrument_id=instrument_id)
        native_adjustments = [
            adjustment.to_dict()
            for position in positions
            for adjustment in position.adjustments()
            if str(adjustment.adjustment_type) == "FUNDING"
            and int(adjustment.ts_event) == boundary_ns
        ]
        preserved.extend(native_adjustments)
        account = engine.cache.account_for_venue(instrument_id.venue)
        account_events = [] if account is None else [event.to_dict() for event in account.events]
        balances_after = (
            []
            if account is None
            else [
                {
                    "currency": str(balance.currency),
                    "total": str(balance.total),
                    "locked": str(balance.locked),
                    "free": str(balance.free),
                }
                for balance in account.balances().values()
            ]
        )
        native_mark = engine.cache.mark_price(instrument_id)
        runtime_updates = [
            item
            for item in boundary_batch
            if isinstance(item, FundingRateUpdate)
            and (
                int(item.next_funding_ns)
                if item.next_funding_ns is not None
                else int(item.ts_init)
            )
            == boundary_ns
        ]
        source_event = source_by_boundary.get(boundary_ns)
        checkpoints.append(
            {
                "boundary_ns": boundary_ns,
                "source_event_key": (
                    None if source_event is None else source_event.get("event_key")
                ),
                "source_funding_rate": (
                    None if source_event is None else source_event.get("funding_rate")
                ),
                "runtime_updates_at_boundary": [
                    {
                        "rate": str(item.rate),
                        "interval": item.interval,
                        "next_funding_ns": item.next_funding_ns,
                        "ts_event": int(item.ts_event),
                        "ts_init": int(item.ts_init),
                    }
                    for item in runtime_updates
                ],
                "native_mark_price": (
                    None
                    if native_mark is None
                    else {
                        "instrument_id": str(native_mark.instrument_id),
                        "value": str(native_mark.value),
                        "ts_event": int(native_mark.ts_event),
                        "ts_init": int(native_mark.ts_init),
                    }
                ),
                "native_mark_age_ns": (
                    None
                    if native_mark is None
                    else boundary_ns - int(native_mark.ts_event)
                ),
                "mark_selection": "LATEST_CAUSAL_AT_OR_BEFORE_FUNDING_TIMESTAMP",
                "native_adjustments": native_adjustments,
                "open_positions": eligible_positions,
                "eligible_position_capture": "IMMEDIATELY_BEFORE_FUNDING_BOUNDARY",
                "positions_after_boundary": [
                    {
                        "instrument_id": str(position.instrument_id),
                        "position_id": str(position.id),
                        "signed_qty": str(_exact_native_signed_position(position)),
                        "ts_last": int(position.ts_last),
                    }
                    for position in engine.cache.positions_open(instrument_id=instrument_id)
                ],
                "account_events_at_boundary": [
                    event for event in account_events if int(event["ts_event"]) == boundary_ns
                ],
                "account_balances_before_boundary": balances_before,
                "account_balances_after_boundary": balances_after,
                "capture_api": "nautilus_trader.backtest.BacktestEngine.run(streaming=True)",
                "financial_state_mutated_by_project": False,
            },
        )
        engine.clear_data()

    remaining = ordered[cursor:]
    if remaining:
        engine.add_data(remaining)
        engine.run(
            start=start_ns if first_batch else None,
            end=end_ns,
            streaming=True,
        )
    else:
        engine.run(
            start=start_ns if first_batch else None,
            end=end_ns,
            streaming=True,
        )
    engine.end()
    return tuple(preserved), tuple(checkpoints)


def _empty_capture() -> dict[str, Any]:
    semantic = {
        "orders": [],
        "fills": [],
        "positions": [],
        "account_events": [],
        "funding": [],
        "native_completed_positions": [],
        "terminal_portfolio": {},
    }
    return {
        "order_rows": [],
        "order_events": [],
        "fills": [],
        "positions": [],
        "account_events": [],
        "funding_events": [],
        "portfolio_snapshots": [],
        "native_statistics": {
            "schema": "nautilus-native-statistics-v1",
            "stats_pnls": {},
            "stats_returns": {},
            "stats_general": {},
            "returns_series": [],
            "returns_basis": "UNAVAILABLE",
            "returns_basis_source": "UNAVAILABLE",
            "undefined_native_values_preserved": True,
        },
        "native_completed_trades": None,
        "backtest_result": None,
        "mark_price_count": 0,
        "funding_rate_count": 0,
        "terminal_portfolio": {},
        "semantic_sequence": semantic,
        "semantic_digest": canonical_sha256(semantic),
    }


def _run_bound(
    config: LabRunRequest | OfficialLabRunRequest,
    *,
    process_isolation: Any | None = None,
) -> RunResult:
    """Execute a request after its qualification/Official boundary is fixed."""

    run = config.lab_run_config
    repository_root = config.repository_root if isinstance(config, OfficialLabRunRequest) else ROOT
    preflight, preflight_diagnostics = _preflight_identity(config)
    instrument = config.instrument if isinstance(config, LabRunRequest) else None
    data = config.data if isinstance(config, LabRunRequest) else ()
    resolved_release: ResolvedDatasetRelease | None = None
    metadata_path: Path | None = None
    funding_path: Path | None = None
    execution_window_evidence: dict[str, Any] = {
        "schema": "engine-data-window-v1",
        "status": "NOT_SELECTED_PREFLIGHT_FAILED",
        "engine_received_post_boundary_data": False,
    }
    if not preflight and isinstance(config.dataset_release, DatasetRelease):
        try:
            resolved_release = config.dataset_release.resolve_runtime_data(repository_root / "data")
        except DataContractError as exc:
            preflight.append(exc.code)
        except Exception:
            preflight.append(FailureCode.DOWNSTREAM_CONTRACT_FAILURE.value)
        else:
            instrument = resolved_release.instrument
            data = resolved_release.data
            metadata_path = (
                repository_root
                / "data/releases"
                / f"{config.dataset_release.instrument_metadata_identity}.metadata.json"
            )
            if config.dataset_release.funding_data_identity != "NOT_APPLICABLE":
                funding_path = (
                    repository_root
                    / "data/releases"
                    / f"{config.dataset_release.funding_data_identity}.funding.json"
                )
    if not preflight:
        preflight.extend(
            _preflight_data(
                config,
                instrument=instrument,
                data=data,
                resolved=resolved_release,
            ),
        )
    if not preflight:
        try:
            data, execution_window_evidence = select_engine_data_window(
                data,
                warmup_start_ns=_timestamp_ns(run.warmup_start),
                scoring_end_exclusive_ns=_timestamp_ns(run.scoring_end_exclusive),
            )
            execution_window_evidence["status"] = "PASS"
        except DataContractError as exc:
            preflight.append(exc.code)

    # Caller-controlled run_id is validated before evidence_root or any other
    # filesystem path is created.  Creation is one atomic mkdir under a resolved
    # root and collisions are never overwritten.
    is_official = isinstance(config, OfficialLabRunRequest)
    run_dir = atomic_create_run_directory(
        config.evidence_root,
        run_id=run.run_id,
        config_sha256=run.config_sha256,
        containment_root=repository_root if is_official else None,
    )
    (run_dir / "lab_run_config.json").write_bytes(run.to_json_bytes() + b"\n")
    (run_dir / "lab_run_config.sha256").write_text(
        run.config_sha256 + "\n",
        encoding="utf-8",
    )
    (run_dir / "runtime.lock.json").write_bytes(
        (repository_root / "runtime.lock.json").read_bytes(),
    )
    (run_dir / "source_revision.json").write_bytes(config.source_revision.to_json_bytes() + b"\n")
    (run_dir / "dataset_release.json").write_bytes(config.dataset_release.to_json_bytes() + b"\n")
    (run_dir / "strategy_spec.json").write_bytes(config.strategy_spec.to_json_bytes() + b"\n")
    strategy_identity = config.strategy_identity if is_official else None
    is_m3_qualification = isinstance(config, LabRunRequest) and (
        config.strategy_spec.parameters.get("m3_profile_qualification") == "true"
    )
    if is_m3_qualification:
        _write_json(
            run_dir / "strategy_plan.json",
            {
                "schema": "strategy-plan-evidence-v1",
                "strategy_plan_sha256": config.strategy_plan.strategy_plan_sha256,
                "material_payload": config.strategy_plan.material_payload(),
            },
        )
    if strategy_identity is not None:
        (run_dir / "strategy_identity.json").write_bytes(
            strategy_identity.to_json_bytes() + b"\n",
        )
        (run_dir / "strategy_identity.sha256").write_text(
            strategy_identity.strategy_identity_sha256 + "\n",
            encoding="utf-8",
        )
    if metadata_path is not None and metadata_path.is_file():
        (run_dir / "instrument_metadata.json").write_bytes(metadata_path.read_bytes())
    if funding_path is not None and funding_path.is_file():
        (run_dir / "funding_source.json").write_bytes(funding_path.read_bytes())
    capture = _empty_capture()
    observations: dict[str, Any] = {
        "bars": [],
        "intents": [],
        "suppressed_intents": [],
        "submitted_intents": [],
        "guard_failures": [],
        "position_sequence": [],
        "lifecycle_clearances": [],
        "scoring_boundary": None,
    }
    engine_error: str | None = None
    engine_started = False
    engine_completed = False
    funding_checkpoints: tuple[dict[str, Any], ...] = ()
    preserved_funding: tuple[dict[str, Any], ...] = ()
    network_guard_evidence: dict[str, Any] = {
        "required": config.strategy_spec.parameters.get("network_access") == "FORBIDDEN",
        "enforced": False,
        "attempts": [],
        "process_isolation": (
            None if process_isolation is None else process_isolation.to_builtins()
        ),
    }
    if not preflight:
        engine: BacktestEngine | None = None
        strategy: GuardedCausalStrategy | None = None
        try:
            with offline_network_guard() as network_evidence:
                network_guard_evidence["enforced"] = True
                engine = BacktestEngine(to_nautilus_engine_config(run.nautilus_engine_config))
                latency_override = None
                if isinstance(config, LabRunRequest) and (
                    config.qualification_control is QualificationControl.ZERO_LATENCY_NEGATIVE_CONTROL
                ):
                    latency_override = StaticLatencyModel(0, 0, 0, 0)
                add_venue_from_config(
                    engine,
                    run.nautilus_venue_config,
                    latency_model_override=latency_override,
                )
                assert instrument is not None
                engine.add_instrument(instrument)
                bars = [item for item in data if isinstance(item, Bar)]
                strategy_configuration = dict(
                    instrument_id=instrument.id,
                    bar_type=BarType.from_str(run.signal_bar_types[0]),
                    execution_bar_type=bars[0].bar_type,
                    profile=run.market_profile,
                    scoring_start_ns=_timestamp_ns(run.scoring_start),
                    scoring_end_exclusive_ns=_timestamp_ns(run.scoring_end_exclusive),
                    effective_insert_latency_ns=(
                        0
                        if isinstance(config, LabRunRequest)
                        and config.qualification_control
                        is QualificationControl.ZERO_LATENCY_NEGATIVE_CONTROL
                        else run.nautilus_venue_config.latency_model.effective_insert_latency_nanos
                    ),
                    size_precision=instrument.size_precision,
                    min_quantity=(
                        None
                        if instrument.min_quantity is None
                        else instrument.min_quantity.as_decimal()
                    ),
                    max_quantity=(
                        None
                        if instrument.max_quantity is None
                        else instrument.max_quantity.as_decimal()
                    ),
                    size_increment=instrument.size_increment.as_decimal(),
                    initial_capital_amount=run.initial_capital.amount,
                    initial_capital_currency=run.initial_capital.currency,
                )
                if isinstance(config, LabRunRequest):
                    strategy = GuardedCausalStrategy()
                    strategy.configure(
                        plan=config.strategy_plan,
                        spot_plan_quote_notional_from_signal_close=(
                            config.strategy_spec.parameters.get("spot_buy_sizing_mode")
                            == "QUOTE_NOTIONAL_FROM_COMPLETED_SIGNAL_CLOSE"
                        ),
                        **strategy_configuration,
                    )
                else:
                    assert strategy_identity is not None
                    strategy = create_registered_strategy(
                        strategy_identity,
                        strategy_spec=config.strategy_spec,
                        source_revision=config.source_revision,
                        configuration=strategy_configuration,
                    )
                engine.add_strategy(strategy)
                engine_started = True
                explicit_time_origin_ns = _timestamp_ns(run.warmup_start)
                if (
                    isinstance(config, LabRunRequest)
                    and config.qualification_control
                    is QualificationControl.NETWORK_ATTEMPT_NEGATIVE_CONTROL
                ):
                    import socket

                    socket.create_connection(("example.invalid", 443))
                if (
                    isinstance(config.dataset_release, DatasetRelease)
                    and any(isinstance(item, FundingRateUpdate) for item in data)
                ):
                    preserved_funding, funding_checkpoints = (
                        _run_real_data_with_native_funding_checkpoints(
                            engine,
                            data=data,
                            instrument_id=instrument.id,
                            funding_source_events=(
                                ()
                                if resolved_release is None
                                else tuple(
                                    item
                                    for item in resolved_release.funding_source_events
                                    if _timestamp_ns(run.warmup_start)
                                    <= int(item["calc_time_ns"])
                                    < _timestamp_ns(run.scoring_end_exclusive)
                                )
                            ),
                            start_ns=explicit_time_origin_ns,
                            end_ns=_timestamp_ns(run.scoring_end_exclusive),
                        )
                    )
                else:
                    preserved_funding = ()
                    engine.add_data(list(data))
                    engine.run(
                        start=explicit_time_origin_ns,
                        end=_timestamp_ns(run.scoring_end_exclusive),
                    )
                network_guard_evidence["attempts"] = list(network_evidence.attempts)
            engine_completed = True
            observations = json.loads(json.dumps(strategy.observations))
            capture = _capture_engine(
                engine,
                strategy,
                instrument.id,
                source_run_id=run.run_id,
                settlement_currency=run.initial_capital.currency,
                preserved_funding_events=preserved_funding,
            )
        except NetworkAttemptBlocked as exc:
            network_guard_evidence["attempts"] = list(network_evidence.attempts)
            engine_error = f"{type(exc).__name__}: {exc}"
            preflight.append(FailureCode.NETWORK_DURING_OFFICIAL_RUN.value)
        except OfflineBoundaryUnavailable as exc:
            engine_error = f"{type(exc).__name__}: {exc}"
            preflight.append(FailureCode.NETWORK_DURING_OFFICIAL_RUN.value)
        except Exception as exc:
            engine_error = f"{type(exc).__name__}: {exc}"
            preflight.append(FailureCode.UNSUPPORTED_RUNTIME.value)
            if engine is not None and strategy is not None:
                try:
                    observations = json.loads(json.dumps(strategy.observations))
                    assert instrument is not None
                    capture = _capture_engine(
                        engine,
                        strategy,
                        instrument.id,
                        source_run_id=run.run_id,
                        settlement_currency=run.initial_capital.currency,
                    )
                except Exception as capture_exc:
                    engine_error += (
                        f"; evidence_capture={type(capture_exc).__name__}: {capture_exc}"
                    )
        finally:
            if engine is not None:
                engine.dispose()

    native_fill_bytes = b"".join(
        canonical_json_bytes(fill) + b"\n" for fill in capture["fills"]
    )
    (run_dir / "native_fills.jsonl").write_bytes(native_fill_bytes)
    _write_csv(
        run_dir / "orders.csv",
        [
            "client_order_id",
            "instrument_id",
            "side",
            "order_type",
            "time_in_force",
            "quantity",
            "filled_qty",
            "leaves_qty",
            "status",
            "initialized_ns",
            "terminal_ns",
        ],
        capture["order_rows"],
    )
    fill_rows = [
        {
            "fill_index": index,
            "event_id": fill["event_id"],
            "client_order_id": fill["client_order_id"],
            "venue_order_id": fill["venue_order_id"],
            "trade_id": fill["trade_id"],
            "position_id": fill["position_id"],
            "account_id": fill["account_id"],
            "instrument_id": fill["instrument_id"],
            "order_side": fill["order_side"],
            "order_type": fill["order_type"],
            "last_qty": fill["last_qty"],
            "last_px": fill["last_px"],
            "commission": fill["commission"],
            "currency": fill["currency"],
            "liquidity_side": fill["liquidity_side"],
            "ts_event": fill["ts_event"],
            "ts_init": fill["ts_init"],
        }
        for index, fill in enumerate(capture["fills"])
    ]
    _write_csv(
        run_dir / "fills.csv",
        [
            "fill_index",
            "event_id",
            "client_order_id",
            "venue_order_id",
            "trade_id",
            "position_id",
            "account_id",
            "instrument_id",
            "order_side",
            "order_type",
            "last_qty",
            "last_px",
            "commission",
            "currency",
            "liquidity_side",
            "ts_event",
            "ts_init",
        ],
        fill_rows,
    )
    _write_csv(
        run_dir / "positions.csv",
        [
            "row_type",
            "event_index",
            "ts_event",
            "instrument_id",
            "side",
            "signed_qty",
            "quantity",
            "avg_px_open",
            "realized_pnl",
        ],
        capture["positions"],
    )
    _write_csv(
        run_dir / "account.csv",
        [
            "event_index",
            "ts_event",
            "account_id",
            "account_type",
            "currency",
            "total",
            "locked",
            "free",
            "reported",
        ],
        _account_rows(capture["account_events"]),
    )
    if run.market_profile is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
        _write_csv(
            run_dir / "funding.csv",
            [
                "adjustment_type",
                "instrument_id",
                "pnl_change",
                "quantity_change",
                "reason",
                "ts_event",
            ],
            capture["funding_events"],
        )
    if is_official:
        native_completed = capture["native_completed_trades"]
        if native_completed is None:
            native_completed = {
                "schema": "nautilus-native-completed-trades-v1",
                "run_id": run.run_id,
                "status": "UNAVAILABLE",
                "completed_trade_count": "UNDEFINED",
                "net_outcomes": [],
                "settlement_currency": run.initial_capital.currency,
                "source": "NATIVE_ENGINE_CAPTURE_UNAVAILABLE",
                "reason": "Run did not reach the native cache capture boundary",
                "project_trade_pairing_used": False,
            }
        _write_json(run_dir / "native_completed_trades.json", native_completed)
        snapshot_bytes = b"".join(
            canonical_json_bytes(item) + b"\n"
            for item in capture["portfolio_snapshots"]
        )
        (run_dir / "native_portfolio_snapshots.jsonl").write_bytes(snapshot_bytes)
        _write_json(run_dir / "native_statistics.json", capture["native_statistics"])

    evidence_bindings = {
        "lab_run_config_sha256": sha256_file(run_dir / "lab_run_config.json"),
        "runtime_lock_sha256": sha256_file(run_dir / "runtime.lock.json"),
        "source_revision_sha256": sha256_file(run_dir / "source_revision.json"),
        "dataset_release_sha256": sha256_file(run_dir / "dataset_release.json"),
        "strategy_spec_sha256": sha256_file(run_dir / "strategy_spec.json"),
    }
    if isinstance(config, LabRunRequest):
        evidence_bindings["strategy_plan_sha256"] = config.strategy_plan.strategy_plan_sha256
    else:
        evidence_bindings["strategy_identity_sha256"] = (
            None if strategy_identity is None else strategy_identity.strategy_identity_sha256
        )
        evidence_bindings["strategy_identity_bytes_sha256"] = (
            None
            if strategy_identity is None
            else sha256_file(run_dir / "strategy_identity.json")
        )
        evidence_bindings["native_portfolio_snapshots_sha256"] = sha256_file(
            run_dir / "native_portfolio_snapshots.jsonl",
        )
        evidence_bindings["native_statistics_sha256"] = sha256_file(
            run_dir / "native_statistics.json",
        )
        evidence_bindings["native_completed_trades_sha256"] = sha256_file(
            run_dir / "native_completed_trades.json",
        )

    nautilus_result = {
        "schema": "m1-nautilus-run-result-v1",
        "run_id": run.run_id,
        "run_purpose": run.run_purpose.value,
        "config_sha256": run.config_sha256,
        "engine_executed": engine_started,
        "engine_completed": engine_completed,
        "engine_error": engine_error,
        "preflight_failure_codes": list(dict.fromkeys(preflight)),
        "preflight_diagnostics": preflight_diagnostics,
        "backtest_result": capture["backtest_result"],
        "strategy_observations": observations,
        "semantic_sequence": capture["semantic_sequence"],
        "semantic_digest": capture["semantic_digest"],
        "native_fill_evidence_sha256": hashlib.sha256(native_fill_bytes).hexdigest(),
        "evidence_bindings": evidence_bindings,
        "mark_price_count": capture["mark_price_count"],
        "funding_rate_count": capture["funding_rate_count"],
        "native_funding_checkpoints": list(funding_checkpoints),
        "terminal_portfolio": capture["terminal_portfolio"],
        "mark_fallback_accepted": False,
        "fee_model": "nautilus_trader.execution:MakerTakerFeeModel",
        "project_fee_postings": 0,
        "project_funding_postings": 0,
        "project_financial_ledger": False,
        "network_guard": network_guard_evidence,
        "execution_data_window": execution_window_evidence,
        "terminal_policy": run.terminal_policy,
        "dataset_contract": {
            "type": type(config.dataset_release).__name__,
            "dataset_release_id": config.dataset_release.dataset_release_id,
            "canonical_material_identity": canonical_sha256(
                config.dataset_release.material_payload(),
            ),
            "source_roles_verified": (
                isinstance(config.dataset_release, DatasetRelease)
                and config.dataset_release.is_current_contract
            ),
            "catalog_identity_verified": (
                True
                if isinstance(config.dataset_release, SyntheticQualificationDatasetRelease)
                else (
                    resolved_release is not None
                    and canonical_sha256(resolved_release.semantic_inventory)
                    == config.dataset_release.catalog_identity
                )
            ),
            "catalog_identity": (
                None
                if not isinstance(config.dataset_release, DatasetRelease)
                else config.dataset_release.catalog_identity
            ),
            "physical_catalog_path": (
                None if resolved_release is None else str(resolved_release.catalog_path)
            ),
            "caller_side_conversion_used": False,
            "instrument": (
                None
                if instrument is None
                else {
                    "native_class": f"{type(instrument).__module__}:{type(instrument).__name__}",
                    "instrument_id": str(instrument.id),
                    "base_currency": str(instrument.base_currency),
                    "quote_currency": str(instrument.quote_currency),
                    "settlement_currency": str(
                        getattr(instrument, "settlement_currency", instrument.quote_currency)
                    ),
                    "maker_fee": str(instrument.maker_fee),
                    "taker_fee": str(instrument.taker_fee),
                    "min_quantity": (
                        None if instrument.min_quantity is None else str(instrument.min_quantity)
                    ),
                    "max_quantity": (
                        None if instrument.max_quantity is None else str(instrument.max_quantity)
                    ),
                    "size_increment": str(instrument.size_increment),
                    "price_increment": str(instrument.price_increment),
                    "min_price": (
                        None if instrument.min_price is None else str(instrument.min_price)
                    ),
                    "max_price": (
                        None if instrument.max_price is None else str(instrument.max_price)
                    ),
                    "price_precision": instrument.price_precision,
                    "size_precision": instrument.size_precision,
                    "project_financial_engine": False,
                }
            ),
            "funding_native_binding": (
                None if resolved_release is None else resolved_release.funding_native_binding
            ),
            "funding_source_event_count": (
                0 if resolved_release is None else resolved_release.funding_source_event_count
            ),
            "funding_runtime_update_count": (
                0 if resolved_release is None else resolved_release.funding_runtime_update_count
            ),
            "execution_funding_source_event_count": len(
                {
                    int(item.ts_init)
                    for item in data
                    if isinstance(item, FundingRateUpdate)
                },
            ),
            "execution_funding_runtime_update_count": sum(
                isinstance(item, FundingRateUpdate) for item in data
            ),
            "market_state_acceptance": (
                None if resolved_release is None else resolved_release.market_state_acceptance
            ),
        },
        "terminal_position_open": any(
            Decimal(str(row["signed_qty"])) != 0
            for row in capture["positions"]
            if row["row_type"] == "FINAL_NATIVE_POSITION"
        ),
        "terminal_non_terminal_strategy_orders": sum(
            1 for row in capture["order_rows"] if not row["terminal_ns"]
        ),
        "synthetic_terminal_close_order": False,
    }
    _write_json(run_dir / "nautilus_result.json", nautilus_result)
    if is_m3_qualification:
        _write_json(run_dir / "strategy_observations.json", observations)

    report = check_evidence_directory(
        run_dir,
        repository_root=repository_root,
        official_source_required=is_official,
    )
    _write_json(run_dir / "checker.json", report.to_builtins())
    all_codes = list(dict.fromkeys([*preflight, *report.failure_codes]))
    if report.outcome is CheckerOutcome.CHECK_PASS and not all_codes:
        state = RunState.COMPLETED
    elif report.outcome is CheckerOutcome.CHECK_FAIL:
        state = RunState.FAILED
    else:
        state = RunState.BLOCKED
    _write_json(
        run_dir / "status.json",
        {
            "run_id": run.run_id,
            "state": state.value,
            "failure_codes": all_codes,
            "checker_outcome": report.outcome.value,
            "started_run_retained": True,
        },
    )
    if is_m3_qualification or is_official:
        manifest_entries = [
            {"path": path.name, "sha256": sha256_file(path), "byte_size": path.stat().st_size}
            for path in sorted(run_dir.iterdir())
            if path.is_file() and path.name != "evidence_manifest.json"
        ]
        _write_json(
            run_dir / "evidence_manifest.json",
            {
                "schema": "run-evidence-manifest-v1",
                "run_id": run.run_id,
                "entries": manifest_entries,
                "inventory_content_sha256": canonical_sha256(manifest_entries),
                "manifest_self_excluded": True,
            },
        )
    inventory = tuple(
        (path.name, sha256_file(path))
        for path in sorted(run_dir.iterdir())
        if path.is_file()
    )
    return RunResult(
        run_id=run.run_id,
        state=state,
        failure_codes=tuple(all_codes),
        checker_outcome=report.outcome,
        config_sha256=run.config_sha256,
        semantic_digest=capture["semantic_digest"],
        evidence_dir=run_dir,
        evidence_inventory=inventory,
        orders=tuple(capture["order_events"]),
        fills=tuple(capture["fills"]),
        positions=tuple(capture["positions"]),
        account_events=tuple(capture["account_events"]),
        funding_events=tuple(capture["funding_events"]),
        strategy_observations=observations,
    )


def run_lab(config: LabRunRequest) -> RunResult:
    """Execute only a Qualification request with a frozen StrategyPlan."""

    if not isinstance(config, LabRunRequest):
        raise TypeError("run_lab accepts only the Qualification LabRunRequest boundary")
    return _run_bound(config)


def run_official_lab(config: OfficialLabRunRequest) -> RunResult:
    """Execute a registered Nautilus Strategy inside the enforced Offline boundary."""

    if not isinstance(config, OfficialLabRunRequest):
        raise TypeError("run_official_lab requires OfficialLabRunRequest")
    # Validate the caller-controlled path component before any side effect,
    # then install the irreversible inherited boundary at the public entry
    # point.  Preflight, data resolution, Nautilus, and every descendant all
    # execute beneath the same kernel-enforced filter.
    validate_safe_component(config.lab_run_config.run_id)
    isolation = activate_process_network_isolation()
    return _run_bound(config, process_isolation=isolation)


__all__ = [
    "LabRunRequest",
    "OfficialLabRunRequest",
    "QualificationControl",
    "RunResult",
    "capture_source_revision",
    "run_lab",
    "run_official_lab",
]
