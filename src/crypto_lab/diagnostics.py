"""Authoritative diagnostic resolution from immutable Run evidence."""

from __future__ import annotations

import os
import tempfile
import json
from dataclasses import fields
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from crypto_lab.config import NOT_APPLICABLE
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
            raise ResearchError("EVIDENCE_INCOMPLETE", "invalid diagnostic resolution schema")
        _require_sha256(self.diagnostic_resolution_id, "diagnostic_resolution.id")
        _require_sha256(self.protocol_id, "diagnostic_resolution.protocol_id")
        if not self.run_id:
            raise ResearchError("EVIDENCE_INCOMPLETE", "diagnostic run_id is required")
        for digest in self.run_evidence_hashes.values():
            _require_sha256(digest, "diagnostic_resolution.run_evidence_hashes")
        if self.native_completed_trades_status not in {"AVAILABLE", "UNAVAILABLE"}:
            raise ResearchError("EVIDENCE_INCOMPLETE", "unknown native trade status")
        if self.performance_diagnostics_status not in {"COMPLETE", "INCOMPLETE"}:
            raise ResearchError("EVIDENCE_INCOMPLETE", "unknown performance diagnostic status")
        if self.benchmark_status not in {"COMPLETE", "MISSING"}:
            raise ResearchError("EVIDENCE_INCOMPLETE", "unknown benchmark status")
        if canonical_sha256(self.material_payload()) != self.diagnostic_resolution_id:
            raise ResearchError("EVIDENCE_INCOMPLETE", "diagnostic resolution identity mismatch")

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
            raise ResearchError("EVIDENCE_INCOMPLETE", "native completed sequence Run mismatch")
        if sequence.settlement_currency != settlement_currency:
            raise ResearchError(
                "EVIDENCE_INCOMPLETE",
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
        raise ResearchError("EVIDENCE_INCOMPLETE", "native completed-trade evidence is invalid")
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
        raise ResearchError("EVIDENCE_INCOMPLETE", "native snapshot money list is invalid")
    total = Decimal(0)
    for item in items:
        if not isinstance(item, dict) or set(item) != {"amount", "currency"}:
            raise ResearchError("EVIDENCE_INCOMPLETE", "native snapshot money value is invalid")
        amount = Decimal(str(item["amount"]))
        if not amount.is_finite():
            raise ResearchError("EVIDENCE_INCOMPLETE", "native snapshot money is non-finite")
        if item["currency"] == currency:
            total += amount
    return total


def derive_performance_diagnostics(
    *,
    run_dir: Path,
    protocol: ResearchProtocol,
) -> PerformanceDiagnostics:
    """Derive SSOT diagnostics from the finest persisted Nautilus Equity snapshots."""

    run_dir = Path(run_dir)
    config = LabRunConfig.from_json_bytes((run_dir / "lab_run_config.json").read_bytes())
    if config.research_protocol_id != protocol.protocol_id:
        raise ResearchError("EVIDENCE_INCOMPLETE", "performance protocol binding mismatch")
    spec = json.loads((run_dir / "strategy_spec.json").read_text(encoding="utf-8"))
    if spec.get("parameters", {}).get("strategy_family") != "BTCUSDT_DAILY_PRICE_VS_SMA20_TREND":
        raise ResearchError(
            "EVIDENCE_INCOMPLETE",
            "performance resolver is qualified only for OWNER_SMOKE SMA20 evidence",
        )
    snapshot_path = run_dir / "native_portfolio_snapshots.jsonl"
    statistics_path = run_dir / "native_statistics.json"
    completed_path = run_dir / "native_completed_trades.json"
    for path in (snapshot_path, statistics_path, completed_path):
        if not path.is_file():
            raise ResearchError("EVIDENCE_INCOMPLETE", f"missing {path.name}")
    snapshots = [
        json.loads(line)
        for line in snapshot_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_timestamp: dict[int, Decimal] = {}
    currency = config.initial_capital.currency
    scored_start_ns = int(config.scoring_start.timestamp() * 1_000_000_000)
    scoring_end_ns = int(config.scoring_end_exclusive.timestamp() * 1_000_000_000)
    for row in snapshots:
        if (
            not isinstance(row, dict)
            or int(row.get("ts_event", -1)) != int(row.get("ts_init", -2))
            or row.get("is_stale") is not False
            or row.get("stale_instruments")
            or row.get("unpriced_instruments")
        ):
            raise ResearchError("EVIDENCE_INCOMPLETE", "native portfolio snapshot is stale or malformed")
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
        raise ResearchError(
            "EVIDENCE_INCOMPLETE",
            "native Equity snapshots do not cover both scoring boundaries",
        )
    observations = tuple(
        EquityObservation(
            timestamp=datetime.fromtimestamp(timestamp / 1_000_000_000, tz=UTC),
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
        benchmark_return=None,
        sample_adequacy=sample,
        monte_carlo_status=monte_carlo,
        claim_scope=protocol.intended_claim_scope.value,
        input_evidence_hashes={
            snapshot_path.name: sha256_file(snapshot_path),
            statistics_path.name: sha256_file(statistics_path),
            completed_path.name: sha256_file(completed_path),
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
        raise ResearchError(
            "EVIDENCE_INCOMPLETE",
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
        # No V1 repair qualification established an authoritative benchmark
        # execution/result schema.  A caller-created JSON file is not evidence.
        raise ResearchError(
            "EVIDENCE_INCOMPLETE",
            "benchmark evidence requires a separately qualified typed resolver",
        )
    benchmark_status = "MISSING"
    spec = json.loads((run_dir / "strategy_spec.json").read_text(encoding="utf-8"))
    is_owner_smoke_sma20 = (
        spec.get("parameters", {}).get("strategy_family")
        == "BTCUSDT_DAILY_PRICE_VS_SMA20_TREND"
    )
    if is_owner_smoke_sma20:
        # This also fails closed on stale/unpriced native snapshots or missing
        # scoring boundaries.  The returned value is written separately by the
        # public Owner workflow and never feeds back into Run state.
        derive_performance_diagnostics(run_dir=run_dir, protocol=protocol)
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
    return DiagnosticResolution.create(
        run_id=config.run_id,
        protocol_id=protocol.protocol_id,
        run_evidence_hashes={
            name: sha256_file(run_dir / name)
            for name in (*_RUN_DIAGNOSTIC_INPUTS, *extra_inputs)
        },
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
        raise ResearchError(
            "EVIDENCE_INCOMPLETE",
            "diagnostics/metrics/trades/Monte Carlo/benchmark evidence is stale or forged",
        )
    return persisted


__all__ = [
    "DiagnosticResolution",
    "derive_diagnostic_resolution",
    "derive_performance_diagnostics",
    "reconcile_diagnostic_resolution",
    "write_diagnostic_resolution",
    "write_performance_diagnostics",
]
