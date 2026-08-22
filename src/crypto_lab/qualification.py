"""Real-artifact M0 qualifications for the locked public Rust/PyO3 runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.execution import DefaultFillModel
from nautilus_trader.execution import MakerTakerFeeModel
from nautilus_trader.execution import StaticLatencyModel
from nautilus_trader.model import Venue

from crypto_lab.config import LabRunConfig
from crypto_lab.config import RuntimeLock
from crypto_lab.nautilus_config import add_venue_from_config
from crypto_lab.nautilus_config import to_nautilus_engine_config
from crypto_lab.nautilus_config import to_nautilus_venue_config
from crypto_lab.runtime import verify_runtime_lock


def qualify_runtime_identity(
    lock: RuntimeLock,
    *,
    dependency_lock_path: Path,
) -> dict[str, Any]:
    current = verify_runtime_lock(
        lock,
        dependency_lock_path=dependency_lock_path,
    )
    return {
        "status": "VERIFIED",
        "nautilus_version": current["nautilus_version"],
        "nautilus_source_repository": lock.nautilus_source_repository,
        "nautilus_source_commit": lock.nautilus_source_commit,
        "source_artifact_relationship": lock.nautilus_provenance_status,
        "installed_wheel_filename": current["installed_wheel_filename"],
        "installed_wheel_sha256": current["installed_wheel_sha256"],
        "wheel_file_present_during_qualification": current["wheel_file_present"],
        "wheel_file_size_bytes": current.get("wheel_file_size_bytes"),
        "python_implementation": current["python_implementation"],
        "python_version": current["python_version"],
        "python_abi": current["python_abi"],
        "platform": current["platform"],
        "machine_architecture": current["machine_architecture"],
        "libc_name": current["libc_name"],
        "glibc_version": current["glibc_version"],
        "dependency_lock_sha256": current["dependency_lock_sha256"],
        "dependency_versions": current["dependency_versions"],
        "pip_version": current["pip_version"],
        "qualification_scope": "M0_RUNTIME_ONLY_NO_DATA_LOADING",
        "timezone": current["timezone"],
        "effective_timezone": current["effective_timezone"],
        "locale": current["locale"],
        "effective_locale": current["effective_locale"],
    }


def qualify_latency_contract(config: LabRunConfig) -> dict[str, Any]:
    native_venue = to_nautilus_venue_config(config.nautilus_venue_config)
    native_engine = to_nautilus_engine_config(config.nautilus_engine_config)
    model = native_venue.latency_model
    fill_model = native_venue.fill_model
    fee_model = native_venue.fee_model
    if not isinstance(model, StaticLatencyModel):
        raise RuntimeError("UNSUPPORTED_RUNTIME: venue did not bind StaticLatencyModel")
    if not isinstance(fill_model, DefaultFillModel):
        raise RuntimeError("UNSUPPORTED_RUNTIME: venue did not bind DefaultFillModel")
    if not isinstance(fee_model, MakerTakerFeeModel):
        raise RuntimeError("UNSUPPORTED_RUNTIME: venue did not bind MakerTakerFeeModel")

    model_repr = repr(model)
    repr_proofs = {
        "base_latency_nanos_60000000000": (
            "base_latency_nanos: UnixNanos(60000000000)" in model_repr
        ),
        "effective_insert_latency_nanos_60000000000": (
            "insert_latency_nanos: UnixNanos(60000000000)" in model_repr
        ),
        "effective_update_latency_nanos_60000000000": (
            "update_latency_nanos: UnixNanos(60000000000)" in model_repr
        ),
        "effective_cancel_latency_nanos_60000000000": (
            "delete_latency_nanos: UnixNanos(60000000000)" in model_repr
        ),
    }
    if not all(repr_proofs.values()):
        raise RuntimeError(f"UNSUPPORTED_RUNTIME: latency repr mismatch: {model_repr}")

    engine = BacktestEngine(config=native_engine)
    venue_accepted = False
    try:
        add_venue_from_config(engine, config.nautilus_venue_config)
        venue_accepted = Venue(native_venue.name) in engine.list_venues()
    finally:
        engine.dispose()

    binding = config.nautilus_venue_config.latency_model
    actual_class_path = f"{type(model).__module__}:{type(model).__name__}"
    result = {
        "status": "VERIFIED",
        "actual_class_path": actual_class_path,
        "config_class_path": binding.config_path,
        "venue_accepted": venue_accepted,
        "base_latency_nanos": binding.base_latency_nanos,
        "configured_insert_latency_nanos": binding.insert_latency_nanos,
        "configured_update_latency_nanos": binding.update_latency_nanos,
        "configured_cancel_latency_nanos": binding.cancel_latency_nanos,
        "effective_insert_latency_nanos": binding.effective_insert_latency_nanos,
        "effective_update_latency_nanos": binding.effective_update_latency_nanos,
        "effective_cancel_latency_nanos": binding.effective_cancel_latency_nanos,
        "native_model_repr": model_repr,
        "native_repr_proofs": repr_proofs,
        "fee_model_class_path": f"{type(fee_model).__module__}:{type(fee_model).__name__}",
        "fill_model_class_path": f"{type(fill_model).__module__}:{type(fill_model).__name__}",
        "actual_causal_behavior_evidence": (
            "evidence/m0/v2-runtime-migration-001/causal-qualification.json"
        ),
    }
    if (
        actual_class_path != binding.latency_model_path
        or not venue_accepted
        or binding.base_latency_nanos != 60_000_000_000
        or binding.effective_insert_latency_nanos != 60_000_000_000
        or binding.effective_update_latency_nanos != 60_000_000_000
        or binding.effective_cancel_latency_nanos != 60_000_000_000
    ):
        raise RuntimeError(f"UNSUPPORTED_RUNTIME: latency qualification failed: {result}")
    return result
