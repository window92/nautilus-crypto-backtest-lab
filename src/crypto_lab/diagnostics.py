"""Authoritative diagnostic resolution from immutable Run evidence."""

from __future__ import annotations

import os
import tempfile
import json
from dataclasses import fields
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from crypto_lab.config import LabRunConfig
from crypto_lab.config import StrictModel
from crypto_lab.config import _require_sha256
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.native_positions import NativeCompletedPositionSequence
from crypto_lab.reporting import EquityObservation
from crypto_lab.reporting import PerformanceDiagnostics
from crypto_lab.reporting import generate_performance_diagnostics
from crypto_lab.research import CompletedTradeSeries
from crypto_lab.research import MonteCarloStatus
from crypto_lab.research import ResearchError
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import ResamplingMethod
from crypto_lab.research import SampleAdequacy
from crypto_lab.research import evaluate_sample_adequacy
from crypto_lab.status import FailureCode
from crypto_lab.timestamps import unix_ns_to_utc_datetime
from crypto_lab.timestamps import utc_datetime_to_ns


SUPPORTED_RESEARCH_STRATEGY_FAMILIES = {
    "BTCUSDT_DAILY_PRICE_VS_SMA20_TREND",
    "BTCUSDT_WEEKLY_TSMOM28_V1",
    "BUY_AND_HOLD_1X_V1",
}


class BenchmarkEvidence(StrictModel):
    """Typed result of one registered benchmark Run."""

    schema_version: int
    benchmark_evidence_id: str
    benchmark_id: str
    protocol_id: str
    market_profile: str
    instrument_id: str
    dataset_release_id: str
    scored_start: datetime
    scoring_end_exclusive: datetime
    source_trial_id: str
    source_run_id: str
    source_result_ref: str
    strategy_spec_id: str
    strategy_identity_sha256: str
    run_manifest_sha256: str
    performance_diagnostics_id: str
    performance_evidence_sha256: str
    total_return: Decimal
    cost_basis: str
    final_holdout_used: bool

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "invalid benchmark evidence schema")
        for name in (
            "benchmark_evidence_id",
            "protocol_id",
            "dataset_release_id",
            "strategy_spec_id",
            "strategy_identity_sha256",
            "run_manifest_sha256",
            "performance_diagnostics_id",
            "performance_evidence_sha256",
        ):
            _require_sha256(getattr(self, name), f"benchmark.{name}")
        if not self.total_return.is_finite() or self.final_holdout_used:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "benchmark return/holdout status is invalid")
        if canonical_sha256(self.material_payload()) != self.benchmark_evidence_id:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "benchmark evidence identity mismatch")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "benchmark_evidence_id"
        }

    @classmethod
    def create(cls, **values: Any) -> BenchmarkEvidence:
        material = {"schema_version": 1, **values}
        return cls(benchmark_evidence_id=canonical_sha256(material), **material)


class DiagnosticResolution(StrictModel):
    schema_version: int
    diagnostic_resolution_id: str
    run_id: str
    protocol_id: str
    run_evidence_hashes: dict[str, str]
    native_completed_trades_status: str
    native_completed_trade_count: int | str
    performance_diagnostics_status: str
    sample_adequacy: SampleAdequacy
    monte_carlo_status: MonteCarloStatus
    benchmark_status: str
    benchmark_id: str
    claim_scope: str
    complete_for_confirmatory_profitability_claim: bool
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "invalid diagnostic resolution schema")
        _require_sha256(self.diagnostic_resolution_id, "diagnostic_resolution.id")
        _require_sha256(self.protocol_id, "diagnostic_resolution.protocol_id")
        if not self.run_id:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "diagnostic run_id is required")
        for digest in self.run_evidence_hashes.values():
            _require_sha256(digest, "diagnostic_resolution.run_evidence_hashes")
        if self.native_completed_trades_status not in {"AVAILABLE", "UNAVAILABLE"}:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "unknown native trade status")
        if self.performance_diagnostics_status not in {"COMPLETE", "INCOMPLETE"}:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "unknown performance diagnostic status")
        if self.benchmark_status not in {"COMPLETE", "MISSING"}:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "unknown benchmark status")
        if canonical_sha256(self.material_payload()) != self.diagnostic_resolution_id:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "diagnostic resolution identity mismatch")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "diagnostic_resolution_id"
        }

    @classmethod
    def create(cls, **values: Any) -> DiagnosticResolution:
        material = {"schema_version": 1, **values}
        return cls(diagnostic_resolution_id=canonical_sha256(material), **material)


_RUN_DIAGNOSTIC_INPUTS = (
    "account.csv",
    "checker.json",
    "dataset_release.json",
    "fills.csv",
    "lab_run_config.json",
    "native_completed_trades.json",
    "nautilus_result.json",
    "positions.csv",
    "source_revision.json",
    "status.json",
    "strategy_identity.json",
    "strategy_spec.json",
)


def _completed_trade_series(
    path: Path,
    *,
    expected_run_id: str,
    settlement_currency: str,
) -> CompletedTradeSeries:
    payload = json.loads(path.read_text(encoding="utf-8"))
    schema = payload.get("schema")
    if schema == "nautilus-native-completed-trades-v2":
        sequence = NativeCompletedPositionSequence.from_json_bytes(path.read_bytes())
        if sequence.source_run_id != expected_run_id:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "native completed sequence Run mismatch")
        if sequence.settlement_currency != settlement_currency:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE,
                "native completed sequence settlement currency mismatch",
            )
        return CompletedTradeSeries(
            source="NAUTILUS_NATIVE_COMPLETED_TRADES",
            evidence_sha256=sha256_file(path),
            settlement_currency=settlement_currency,
            stable_native_sequence=True,
            native_completed_unit_count=sequence.completed_trade_count,
            realized_pnl_outcomes=tuple(unit.realized_pnl for unit in sequence.units),
            realized_returns=sequence.realized_returns,
            unambiguous_net_after_cost=sequence.unambiguous_net_after_cost,
            net_outcomes=sequence.net_outcomes,
        )
    if (
        schema != "nautilus-native-completed-trades-v1"
        or payload.get("run_id") != expected_run_id
        or payload.get("project_trade_pairing_used") is not False
        or payload.get("status") != "UNAVAILABLE"
    ):
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "native completed-trade evidence is invalid")
    return CompletedTradeSeries(
        source="NAUTILUS_NATIVE_COMPLETED_TRADES",
        evidence_sha256=sha256_file(path),
        settlement_currency=settlement_currency,
        stable_native_sequence=False,
        native_completed_unit_count="UNDEFINED",
        realized_pnl_outcomes=(),
        realized_returns=(),
        unambiguous_net_after_cost=False,
        net_outcomes=(),
    )


def _money_total(items: Any, currency: str) -> Decimal:
    if not isinstance(items, list):
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "native snapshot money list is invalid")
    total = Decimal(0)
    for item in items:
        if not isinstance(item, dict) or set(item) != {"amount", "currency"}:
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "native snapshot money value is invalid")
        amount = Decimal(str(item["amount"]))
        if not amount.is_finite():
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "native snapshot money is non-finite")
        if item["currency"] == currency:
            total += amount
    return total


def _load_benchmark_evidence(
    *,
    protocol: ResearchProtocol,
    benchmark_directory: Path,
) -> tuple[BenchmarkEvidence, Path]:
    path = Path(benchmark_directory) / f"{protocol.required_benchmark.benchmark_id}.json"
    if not path.is_file():
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "frozen benchmark evidence is missing")
    value = BenchmarkEvidence.from_json_bytes(path.read_bytes())
    benchmark = protocol.required_benchmark
    if (
        value.benchmark_id != benchmark.benchmark_id
        or value.protocol_id != protocol.protocol_id
        or value.market_profile != protocol.market_profile.value
        or value.instrument_id not in protocol.instrument_ids
        or value.dataset_release_id not in protocol.dataset_release_ids
        or value.scored_start != benchmark.scored_interval.start_inclusive
        or value.scoring_end_exclusive != benchmark.scored_interval.end_exclusive
        or value.cost_basis != benchmark.cost_basis
    ):
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "benchmark evidence binding mismatch")
    performance_path = path.parent.parent / "performance" / f"{value.source_run_id}.json"
    if not performance_path.is_file() or sha256_file(performance_path) != value.performance_evidence_sha256:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "benchmark performance binding is stale")
    performance = PerformanceDiagnostics.from_json_bytes(performance_path.read_bytes())
    if (
        performance.diagnostics_id != value.performance_diagnostics_id
        or performance.run_id != value.source_run_id
        or performance.total_return.status == "UNDEFINED"
        or Decimal(str(performance.total_return.value)) != value.total_return
    ):
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "benchmark total return is stale")
    return value, path


def derive_benchmark_evidence(
    *,
    run_dir: Path,
    protocol: ResearchProtocol,
    trial_id: str,
    result_ref: str,
    performance_path: Path,
) -> BenchmarkEvidence:
    """Bind a registered Buy-and-Hold result to its frozen protocol benchmark."""

    run_dir = Path(run_dir)
    config = LabRunConfig.from_json_bytes((run_dir / "lab_run_config.json").read_bytes())
    spec = json.loads((run_dir / "strategy_spec.json").read_text(encoding="utf-8"))
    identity = json.loads((run_dir / "strategy_identity.json").read_text(encoding="utf-8"))
    performance = PerformanceDiagnostics.from_json_bytes(Path(performance_path).read_bytes())
    benchmark = protocol.required_benchmark
    if (
        config.research_protocol_id != protocol.protocol_id
        or config.scoring_start != benchmark.scored_interval.start_inclusive
        or config.scoring_end_exclusive != benchmark.scored_interval.end_exclusive
        or spec.get("parameters", {}).get("strategy_family") != "BUY_AND_HOLD_1X_V1"
        or spec.get("parameters", {}).get("benchmark_id") != benchmark.benchmark_id
        or performance.run_id != config.run_id
        or performance.total_return.status == "UNDEFINED"
    ):
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "benchmark Run is not the frozen benchmark")
    return BenchmarkEvidence.create(
        benchmark_id=benchmark.benchmark_id,
        protocol_id=protocol.protocol_id,
        market_profile=config.market_profile.value,
        instrument_id=config.instrument_id,
        dataset_release_id=config.dataset_release_id,
        scored_start=config.scoring_start,
        scoring_end_exclusive=config.scoring_end_exclusive,
        source_trial_id=trial_id,
        source_run_id=config.run_id,
        source_result_ref=result_ref,
        strategy_spec_id=config.strategy_spec_id,
        strategy_identity_sha256=str(identity["strategy_identity_sha256"]),
        run_manifest_sha256=sha256_file(run_dir / "evidence_manifest.json"),
        performance_diagnostics_id=performance.diagnostics_id,
        performance_evidence_sha256=sha256_file(performance_path),
        total_return=Decimal(str(performance.total_return.value)),
        cost_basis=benchmark.cost_basis,
        final_holdout_used=False,
    )


def write_benchmark_evidence(value: BenchmarkEvidence, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        handle.write(value.to_json_bytes() + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(temporary, path)
    finally:
        if not handle.closed:
            handle.close()
        temporary.unlink(missing_ok=True)


def derive_performance_diagnostics(
    *,
    run_dir: Path,
    protocol: ResearchProtocol,
    benchmark_directory: Path | None = None,
    resolve_benchmark: bool = True,
) -> PerformanceDiagnostics:
    """Derive SSOT diagnostics from the finest persisted Nautilus Equity snapshots."""

    run_dir = Path(run_dir)
    config = LabRunConfig.from_json_bytes((run_dir / "lab_run_config.json").read_bytes())
    if config.research_protocol_id != protocol.protocol_id:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "performance protocol binding mismatch")
    spec = json.loads((run_dir / "strategy_spec.json").read_text(encoding="utf-8"))
    strategy_family = spec.get("parameters", {}).get("strategy_family")
    if strategy_family not in SUPPORTED_RESEARCH_STRATEGY_FAMILIES:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE,
            "performance resolver does not recognize the registered research family",
        )
    snapshot_path = run_dir / "native_portfolio_snapshots.jsonl"
    statistics_path = run_dir / "native_statistics.json"
    completed_path = run_dir / "native_completed_trades.json"
    for path in (snapshot_path, statistics_path, completed_path):
        if not path.is_file():
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, f"missing {path.name}")
    snapshots = [
        json.loads(line)
        for line in snapshot_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_timestamp: dict[int, Decimal] = {}
    currency = config.initial_capital.currency
    scored_start_ns = utc_datetime_to_ns(config.scoring_start)
    scoring_end_ns = utc_datetime_to_ns(config.scoring_end_exclusive)
    for row in snapshots:
        if (
            not isinstance(row, dict)
            or int(row.get("ts_event", -1)) != int(row.get("ts_init", -2))
            or row.get("is_stale") is not False
            or row.get("stale_instruments")
            or row.get("unpriced_instruments")
        ):
            raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE, "native portfolio snapshot is stale or malformed")
        timestamp = int(row["ts_event"])
        if scored_start_ns <= timestamp <= scoring_end_ns:
            # SSOT fallback Equity path: the frozen Initial Capital plus the
            # native realized and native unrealized PnL values.  This is a
            # read-only diagnostic; Nautilus remains the PnL owner.
            by_timestamp[timestamp] = (
                config.initial_capital.amount
                + _money_total(row.get("realized_pnls"), currency)
                + _money_total(row.get("unrealized_pnls"), currency)
            )
    if (
        not by_timestamp
        or min(by_timestamp) != scored_start_ns
        or max(by_timestamp) != scoring_end_ns
    ):
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE,
            "native Equity snapshots do not cover both scoring boundaries",
        )
    observations = tuple(
        EquityObservation(
            timestamp=unix_ns_to_utc_datetime(timestamp),
            equity=equity,
        )
        for timestamp, equity in sorted(by_timestamp.items())
    )
    completed = _completed_trade_series(
        completed_path,
        expected_run_id=config.run_id,
        settlement_currency=currency,
    )
    sample = evaluate_sample_adequacy(protocol.sample_adequacy_rule, completed)
    monte_carlo = (
        MonteCarloStatus.NOT_APPLICABLE
        if protocol.monte_carlo_spec.resampling_method is ResamplingMethod.NOT_APPLICABLE
        else MonteCarloStatus.MC_LOW_CONFIDENCE
    )
    benchmark_return = None
    benchmark_hashes: dict[str, str] = {}
    if resolve_benchmark and benchmark_directory is not None:
        benchmark_evidence, benchmark_path = _load_benchmark_evidence(
            protocol=protocol,
            benchmark_directory=benchmark_directory,
        )
        benchmark_return = benchmark_evidence.total_return
        benchmark_hashes[f"benchmark:{benchmark_evidence.benchmark_id}"] = sha256_file(
            benchmark_path,
        )
    return generate_performance_diagnostics(
        run_id=config.run_id,
        scored_start=config.scoring_start,
        scoring_end_exclusive=config.scoring_end_exclusive,
        initial_capital=config.initial_capital.amount,
        settlement_currency=currency,
        equity_observation_basis=(
            "FINEST_PERSISTED_NAUTILUS_PORTFOLIO_SNAPSHOTS; "
            "equity = frozen_initial_capital + native_realized_pnl + native_unrealized_pnl"
        ),
        equity_observations=observations,
        native_metrics={},
        completed_trades=completed,
        benchmark_return=benchmark_return,
        sample_adequacy=sample,
        monte_carlo_status=monte_carlo,
        claim_scope=protocol.intended_claim_scope.value,
        input_evidence_hashes={
            snapshot_path.name: sha256_file(snapshot_path),
            statistics_path.name: sha256_file(statistics_path),
            completed_path.name: sha256_file(completed_path),
            **benchmark_hashes,
        },
    )


def write_performance_diagnostics(value: PerformanceDiagnostics, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        handle.write(value.to_json_bytes() + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(temporary, path)
    finally:
        if not handle.closed:
            handle.close()
        temporary.unlink(missing_ok=True)


def derive_diagnostic_resolution(
    *,
    run_dir: Path,
    protocol: ResearchProtocol,
    benchmark_directory: Path,
) -> DiagnosticResolution:
    """Derive all status facts; no metric/trade assertion is accepted from a caller."""

    run_dir = Path(run_dir)
    missing = [name for name in _RUN_DIAGNOSTIC_INPUTS if not (run_dir / name).is_file()]
    if missing:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE,
            "diagnostic Run inputs are incomplete: " + ",".join(missing),
        )
    config = LabRunConfig.from_json_bytes((run_dir / "lab_run_config.json").read_bytes())
    completed = _completed_trade_series(
        run_dir / "native_completed_trades.json",
        expected_run_id=config.run_id,
        settlement_currency=config.initial_capital.currency,
    )
    available = completed.stable_native_sequence
    sample = evaluate_sample_adequacy(protocol.sample_adequacy_rule, completed)
    monte_carlo = (
        MonteCarloStatus.NOT_APPLICABLE
        if protocol.monte_carlo_spec.resampling_method is ResamplingMethod.NOT_APPLICABLE
        else MonteCarloStatus.MC_LOW_CONFIDENCE
    )
    benchmark_path = Path(benchmark_directory) / f"{protocol.required_benchmark.benchmark_id}.json"
    if benchmark_path.exists():
        benchmark_evidence, benchmark_path = _load_benchmark_evidence(
            protocol=protocol,
            benchmark_directory=benchmark_directory,
        )
        benchmark_status = "COMPLETE"
        benchmark_hash = sha256_file(benchmark_path)
    else:
        benchmark_evidence = None
        benchmark_status = "MISSING"
        benchmark_hash = None
    spec = json.loads((run_dir / "strategy_spec.json").read_text(encoding="utf-8"))
    strategy_family = spec.get("parameters", {}).get("strategy_family")
    if strategy_family in SUPPORTED_RESEARCH_STRATEGY_FAMILIES:
        # This also fails closed on stale/unpriced native snapshots or missing
        # scoring boundaries.  The returned value is written separately by the
        # public Owner workflow and never feeds back into Run state.
        derive_performance_diagnostics(
            run_dir=run_dir,
            protocol=protocol,
            benchmark_directory=benchmark_directory,
            resolve_benchmark=(
                benchmark_status == "COMPLETE" and strategy_family != "BUY_AND_HOLD_1X_V1"
            ),
        )
        performance_status = "COMPLETE"
        extra_inputs = (
            "native_portfolio_snapshots.jsonl",
            "native_statistics.json",
        )
        limitations = (
            *(("NATIVE_COMPLETED_TRADE_SEQUENCE_UNAVAILABLE",) if not available else ()),
            "CURRENT_METADATA_NOT_EXACT_2020_2021_POINT_IN_TIME_METADATA",
            "ACCOUNT_SPECIFIC_HISTORICAL_FEE_TIER_UNAVAILABLE_ESTIMATED_FEE_USED",
            "DEVELOPMENT_EXPOSED_NOT_FINAL_HOLDOUT",
            "EXPLORATORY_NO_REAL_PROFITABILITY_CLAIM",
        )
    else:
        # The qualification fixture deliberately has no qualified Equity
        # diagnostic path and cannot be promoted into research evidence.
        performance_status = "INCOMPLETE"
        extra_inputs = ()
        limitations = (
            *(("NATIVE_COMPLETED_TRADE_SEQUENCE_UNAVAILABLE",) if not available else ()),
            "FULL_NATIVE_EQUITY_CURVE_UNAVAILABLE",
            "QUALIFICATION_FIXTURE_NOT_PROFITABILITY_EVIDENCE",
        )
    complete = bool(
        available
        and performance_status == "COMPLETE"
        and sample is SampleAdequacy.ADEQUATE
        and monte_carlo is MonteCarloStatus.COMPLETED
        and benchmark_status == "COMPLETE"
    )
    evidence_hashes = {
        name: sha256_file(run_dir / name)
        for name in (*_RUN_DIAGNOSTIC_INPUTS, *extra_inputs)
    }
    if benchmark_hash is not None:
        evidence_hashes[f"benchmark:{protocol.required_benchmark.benchmark_id}"] = benchmark_hash
    return DiagnosticResolution.create(
        run_id=config.run_id,
        protocol_id=protocol.protocol_id,
        run_evidence_hashes=evidence_hashes,
        native_completed_trades_status="AVAILABLE" if available else "UNAVAILABLE",
        native_completed_trade_count=(
            completed.native_completed_unit_count if available else "UNDEFINED"
        ),
        performance_diagnostics_status=performance_status,
        sample_adequacy=sample,
        monte_carlo_status=monte_carlo,
        benchmark_status=benchmark_status,
        benchmark_id=protocol.required_benchmark.benchmark_id,
        claim_scope=protocol.intended_claim_scope.value,
        complete_for_confirmatory_profitability_claim=complete,
        limitations=limitations,
    )


def write_diagnostic_resolution(value: DiagnosticResolution, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False)
    temporary = Path(handle.name)
    try:
        handle.write(value.to_json_bytes() + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if not handle.closed:
            handle.close()
        temporary.unlink(missing_ok=True)


def reconcile_diagnostic_resolution(
    *,
    path: Path,
    run_dir: Path,
    protocol: ResearchProtocol,
    benchmark_directory: Path,
) -> DiagnosticResolution:
    persisted = DiagnosticResolution.from_json_bytes(Path(path).read_bytes())
    derived = derive_diagnostic_resolution(
        run_dir=run_dir,
        protocol=protocol,
        benchmark_directory=benchmark_directory,
    )
    if persisted != derived:
        raise ResearchError(FailureCode.EVIDENCE_INCOMPLETE,
            "diagnostics/metrics/trades/Monte Carlo/benchmark evidence is stale or forged",
        )
    return persisted


__all__ = [
    "BenchmarkEvidence",
    "DiagnosticResolution",
    "derive_benchmark_evidence",
    "derive_diagnostic_resolution",
    "derive_performance_diagnostics",
    "reconcile_diagnostic_resolution",
    "write_diagnostic_resolution",
    "write_benchmark_evidence",
    "write_performance_diagnostics",
]
