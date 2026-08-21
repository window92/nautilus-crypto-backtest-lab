"""Real-artifact M0 qualifications; these do not load market data or run a strategy."""

from __future__ import annotations

import importlib.metadata
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.node import get_account_type
from nautilus_trader.backtest.node import get_base_currency
from nautilus_trader.backtest.node import get_book_type
from nautilus_trader.backtest.node import get_fee_model
from nautilus_trader.backtest.node import get_fill_model
from nautilus_trader.backtest.node import get_latency_model
from nautilus_trader.backtest.node import get_leverages
from nautilus_trader.backtest.node import get_margin_model
from nautilus_trader.backtest.node import get_oms_type
from nautilus_trader.backtest.node import get_oto_trigger_mode
from nautilus_trader.backtest.node import get_price_protection_points
from nautilus_trader.backtest.node import get_starting_balances
from nautilus_trader.model.identifiers import Venue

from crypto_lab.config import LabRunConfig
from crypto_lab.config import RuntimeLock
from crypto_lab.hashing import sha256_file
from crypto_lab.nautilus_config import to_nautilus_engine_config
from crypto_lab.nautilus_config import to_nautilus_venue_config
from crypto_lab.runtime import verify_runtime_lock


def _installed_source_path(relative: str) -> Path:
    distribution = importlib.metadata.distribution("nautilus_trader")
    return Path(distribution.locate_file(relative))


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
    model = get_latency_model(native_venue)
    if model is None:
        raise RuntimeError("UNSUPPORTED_RUNTIME: v1.231.0 venue did not create a latency model")

    latency_source = _installed_source_path("nautilus_trader/backtest/models/latency.pyx")
    node_source = _installed_source_path("nautilus_trader/backtest/node.py")
    engine_source = _installed_source_path("nautilus_trader/backtest/engine.pyx")
    latency_text = latency_source.read_text(encoding="utf-8")
    engine_text = engine_source.read_text(encoding="utf-8")
    source_proofs = {
        "constructor_adds_base_to_insert": (
            "self.insert_latency_nanos = base_latency_nanos + insert_latency_nanos"
            in latency_text
        ),
        "inflight_submit_uses_effective_insert": (
            "ts = command.ts_init + self.latency_model.insert_latency_nanos" in engine_text
        ),
        "none_fee_resolves_maker_taker": (
            "if fee_model is None:\n            fee_model = MakerTakerFeeModel()" in engine_text
        ),
    }
    if not all(source_proofs.values()):
        raise RuntimeError("UNSUPPORTED_RUNTIME: pinned artifact source proof is incomplete")

    engine = BacktestEngine(config=native_engine)
    venue_accepted = False
    try:
        engine.add_venue(
            venue=Venue(native_venue.name),
            oms_type=get_oms_type(native_venue),
            account_type=get_account_type(native_venue),
            starting_balances=get_starting_balances(native_venue),
            base_currency=get_base_currency(native_venue),
            default_leverage=Decimal(str(native_venue.default_leverage)),
            leverages=get_leverages(native_venue),
            margin_model=get_margin_model(native_venue),
            modules=[],
            fill_model=get_fill_model(native_venue),
            fee_model=get_fee_model(native_venue),
            latency_model=model,
            book_type=get_book_type(native_venue),
            routing=native_venue.routing,
            reject_stop_orders=native_venue.reject_stop_orders,
            support_gtd_orders=native_venue.support_gtd_orders,
            support_contingent_orders=native_venue.support_contingent_orders,
            oto_trigger_mode=get_oto_trigger_mode(native_venue),
            use_position_ids=native_venue.use_position_ids,
            use_random_ids=native_venue.use_random_ids,
            use_reduce_only=native_venue.use_reduce_only,
            use_message_queue=config.nautilus_venue_config.use_message_queue,
            use_market_order_acks=native_venue.use_market_order_acks,
            bar_execution=native_venue.bar_execution,
            bar_adaptive_high_low_ordering=native_venue.bar_adaptive_high_low_ordering,
            trade_execution=native_venue.trade_execution,
            liquidity_consumption=native_venue.liquidity_consumption,
            queue_position=native_venue.queue_position,
            allow_cash_borrowing=native_venue.allow_cash_borrowing,
            frozen_account=native_venue.frozen_account,
            price_protection_points=get_price_protection_points(native_venue),
            settlement_prices=native_venue.settlement_prices,
        )
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
        "base_latency_nanos": model.base_latency_nanos,
        "configured_insert_latency_nanos": binding.insert_latency_nanos,
        "configured_update_latency_nanos": binding.update_latency_nanos,
        "configured_cancel_latency_nanos": binding.cancel_latency_nanos,
        "effective_insert_latency_nanos": model.insert_latency_nanos,
        "effective_update_latency_nanos": model.update_latency_nanos,
        "effective_cancel_latency_nanos": model.cancel_latency_nanos,
        "source_proofs": source_proofs,
        "artifact_source_files": {
            "latency.pyx": sha256_file(latency_source),
            "node.py": sha256_file(node_source),
            "engine.pyx": sha256_file(engine_source),
        },
    }
    if (
        actual_class_path != binding.latency_model_path
        or not venue_accepted
        or model.base_latency_nanos != 60_000_000_000
        or model.insert_latency_nanos != 60_000_000_000
        or model.update_latency_nanos != 60_000_000_000
        or model.cancel_latency_nanos != 60_000_000_000
    ):
        raise RuntimeError(f"UNSUPPORTED_RUNTIME: latency qualification failed: {result}")
    return result
