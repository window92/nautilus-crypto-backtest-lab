"""Authoritative diagnostic resolution from immutable Run evidence."""

from __future__ import annotations

import os
import tempfile
from dataclasses import fields
from pathlib import Path
from typing import Any

from crypto_lab.config import NOT_APPLICABLE
from crypto_lab.config import LabRunConfig
from crypto_lab.config import StrictModel
from crypto_lab.config import _require_sha256
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.research import MonteCarloStatus
from crypto_lab.research import ResearchError
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import ResamplingMethod
from crypto_lab.research import SampleAdequacy


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
    native_truth = __import__("json").loads(
        (run_dir / "native_completed_trades.json").read_text(encoding="utf-8"),
    )
    if (
        native_truth.get("schema") != "nautilus-native-completed-trades-v1"
        or native_truth.get("run_id") != config.run_id
        or native_truth.get("project_trade_pairing_used") is not False
    ):
        raise ResearchError("EVIDENCE_INCOMPLETE", "native completed-trade evidence is invalid")
    available = native_truth.get("status") == "AVAILABLE"
    if available:
        # V1 has no qualified path producing AVAILABLE yet.  Fail closed instead
        # of accepting a caller-created sequence as native truth.
        raise ResearchError(
            "EVIDENCE_INCOMPLETE",
            "AVAILABLE native trade evidence requires a separately qualified resolver",
        )
    sample = (
        SampleAdequacy.NOT_APPLICABLE
        if protocol.sample_adequacy_rule.minimum_completed_trades == NOT_APPLICABLE
        else SampleAdequacy.LOW_CONFIDENCE
    )
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
    # The current runner preserves native account events but does not claim that
    # they are a complete mark-valued Equity curve or native completed-trade
    # sequence.  Reporting that limitation is safer than manufacturing metrics.
    performance_status = "INCOMPLETE"
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
            name: sha256_file(run_dir / name) for name in _RUN_DIAGNOSTIC_INPUTS
        },
        native_completed_trades_status="AVAILABLE" if available else "UNAVAILABLE",
        native_completed_trade_count=(
            int(native_truth["completed_trade_count"]) if available else "UNDEFINED"
        ),
        performance_diagnostics_status=performance_status,
        sample_adequacy=sample,
        monte_carlo_status=monte_carlo,
        benchmark_status=benchmark_status,
        benchmark_id=protocol.required_benchmark.benchmark_id,
        claim_scope=protocol.intended_claim_scope.value,
        complete_for_confirmatory_profitability_claim=complete,
        limitations=(
            "NATIVE_COMPLETED_TRADE_SEQUENCE_UNAVAILABLE",
            "FULL_NATIVE_EQUITY_CURVE_UNAVAILABLE",
            "QUALIFICATION_FIXTURE_NOT_PROFITABILITY_EVIDENCE",
        ),
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
    "reconcile_diagnostic_resolution",
    "write_diagnostic_resolution",
]
