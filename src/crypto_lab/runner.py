"""Minimal M1 causal Nautilus runner and stable persisted evidence interface."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.execution import StaticLatencyModel
from nautilus_trader.model import Bar
from nautilus_trader.model import FundingRateUpdate
from nautilus_trader.model import MarkPriceUpdate

from crypto_lab.checker import CheckerOutcome
from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.config import RunPurpose
from crypto_lab.config import RuntimeLock
from crypto_lab.config import SourceRevision
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.nautilus_config import add_venue_from_config
from crypto_lab.nautilus_config import to_nautilus_engine_config
from crypto_lab.runtime import verify_runtime_lock
from crypto_lab.status import FailureCode
from crypto_lab.status import RunState
from crypto_lab.strategies import GuardedCausalStrategy
from crypto_lab.strategies import StrategyPlan
from crypto_lab.strategies import StrategySpec


ROOT = Path(__file__).resolve().parents[2]
ONE_MINUTE_NS = 60_000_000_000


class QualificationControl(StrEnum):
    STANDARD = "STANDARD"
    ZERO_LATENCY_NEGATIVE_CONTROL = "ZERO_LATENCY_NEGATIVE_CONTROL"


@dataclass(frozen=True)
class LabRunRequest:
    """One fully bound M1 call; M2 can later supply its validated native data objects."""

    lab_run_config: LabRunConfig
    source_revision: SourceRevision
    strategy_spec: StrategySpec
    dataset_release: dict[str, Any]
    instrument: Any
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
        object.__setattr__(
            self,
            "dataset_release",
            MappingProxyType(json.loads(json.dumps(self.dataset_release))),
        )
        object.__setattr__(self, "data", tuple(self.data))


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

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    clean = not git("status", "--porcelain=v1")
    return SourceRevision(
        repository=git("config", "--get", "remote.origin.url"),
        branch_ref=git("symbolic-ref", "--short", "HEAD"),
        git_commit=git("rev-parse", "HEAD"),
        git_tree=git("rev-parse", "HEAD^{tree}"),
        clean_worktree=clean,
        captured_at_utc=datetime.now(UTC),
    )


def _timestamp_ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _preflight(config: LabRunRequest) -> list[str]:
    run = config.lab_run_config
    failures: list[str] = []
    if sha256_file(ROOT / "runtime.lock.json") != run.runtime_lock_sha256:
        failures.append(FailureCode.RUNTIME_LOCK_MISMATCH.value)
    else:
        lock = RuntimeLock.from_json_bytes((ROOT / "runtime.lock.json").read_bytes())
        try:
            verify_runtime_lock(lock, dependency_lock_path=ROOT / "requirements.lock.txt")
        except Exception:
            failures.append(FailureCode.RUNTIME_LOCK_MISMATCH.value)

    if config.strategy_spec.strategy_spec_id != run.strategy_spec_id:
        failures.append(FailureCode.CONFIG_HASH_MISMATCH.value)
    if (
        config.strategy_spec.market_profile is not run.market_profile
        or config.strategy_spec.instrument_id != run.instrument_id
        or config.strategy_spec.signal_bar_types != run.signal_bar_types
    ):
        failures.append(FailureCode.CONFIG_INVALID.value)
    if config.dataset_release.get("dataset_release_id") != run.dataset_release_id:
        failures.append(FailureCode.DATASET_RELEASE_STALE.value)
    release_material = {
        key: value
        for key, value in config.dataset_release.items()
        if key != "dataset_release_id"
    }
    if canonical_sha256(release_material) != run.dataset_release_id:
        failures.append(FailureCode.DATA_HASH_MISMATCH.value)
    try:
        source_tree = subprocess.run(
            ["git", "rev-parse", f"{config.source_revision.git_commit}^{{tree}}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except Exception:
        failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)
    else:
        if source_tree != config.source_revision.git_tree:
            failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)
    if str(config.instrument.id) != run.instrument_id:
        failures.append(FailureCode.INSTRUMENT_METADATA_INVALID.value)
    if Decimal(str(config.instrument.maker_fee)) != run.fee_assumption.maker_fee or Decimal(
        str(config.instrument.taker_fee),
    ) != run.fee_assumption.taker_fee:
        failures.append(FailureCode.FEE_MISSING.value)

    if config.qualification_control is QualificationControl.ZERO_LATENCY_NEGATIVE_CONTROL:
        if run.run_purpose is not RunPurpose.QUALIFICATION:
            failures.append(FailureCode.CONFIG_INVALID.value)

    bars = [item for item in config.data if isinstance(item, Bar)]
    marks = [item for item in config.data if isinstance(item, MarkPriceUpdate)]
    funding = [item for item in config.data if isinstance(item, FundingRateUpdate)]
    if any(
        not isinstance(item, Bar | MarkPriceUpdate | FundingRateUpdate)
        for item in config.data
    ):
        failures.append(FailureCode.DATA_ROLE_MISMATCH.value)
    if not bars:
        failures.append(FailureCode.DATA_GAP.value)
    bar_timestamps: list[int] = []
    for bar in bars:
        bar_timestamps.append(int(bar.ts_init))
        precision_ok = all(
            price.precision == config.instrument.price_precision
            for price in (bar.open, bar.high, bar.low, bar.close)
        ) and bar.volume.precision == config.instrument.size_precision
        identity_ok = (
            str(bar.bar_type.instrument_id) == run.instrument_id
            and str(bar.bar_type) == run.execution_bar_type
        )
        ohlc_ok = (
            bar.high >= bar.open
            and bar.high >= bar.close
            and bar.low <= bar.open
            and bar.low <= bar.close
            and bar.high >= bar.low
            and bar.volume.as_decimal() > 0
        )
        timestamp_ok = int(bar.ts_init) == int(bar.ts_event)
        if not (precision_ok and identity_ok and ohlc_ok and timestamp_ok):
            failures.append(FailureCode.INSTRUMENT_METADATA_INVALID.value)
    if bar_timestamps != sorted(bar_timestamps) or len(set(bar_timestamps)) != len(bar_timestamps):
        failures.append(FailureCode.DATA_TIMESTAMP_INVALID.value)
    if config.dataset_release.get("qualification_scope") == "M1_SYNTHETIC":
        warmup_start_ns = _timestamp_ns(run.warmup_start)
        scoring_end_ns = _timestamp_ns(run.scoring_end_exclusive)
        required_bar_timestamps = list(
            range(
                warmup_start_ns + ONE_MINUTE_NS,
                scoring_end_ns + 1,
                ONE_MINUTE_NS,
            ),
        )
        if bar_timestamps != required_bar_timestamps:
            failures.append(FailureCode.DATA_GAP.value)
    if not set(config.strategy_plan.intents_by_bar_ns).issubset(set(bar_timestamps)):
        failures.append(FailureCode.DATA_TIMESTAMP_INVALID.value)

    if run.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        if marks or funding:
            failures.append(FailureCode.DATA_ROLE_MISMATCH.value)
    else:
        mark_ok = (
            config.dataset_release.get("mark_role") == "markPriceKlines"
            and config.dataset_release.get("mark_complete") is True
            and bool(marks)
            and all(str(mark.instrument_id) == run.instrument_id for mark in marks)
        )
        if not mark_ok:
            failures.append(FailureCode.MARK_ROLE_INVALID.value)
        funding_ok = (
            config.dataset_release.get("funding_role") == "fundingRate"
            and config.dataset_release.get("funding_complete") is True
            and bool(funding)
            and all(str(event.instrument_id) == run.instrument_id for event in funding)
        )
        if not funding_ok:
            failures.append(FailureCode.FUNDING_MISSING.value)
        if any(int(event.ts_event) >= _timestamp_ns(run.scoring_end_exclusive) for event in funding):
            failures.append(FailureCode.DATA_TIMESTAMP_INVALID.value)

    all_timestamps = [int(item.ts_init) for item in config.data]
    if all_timestamps != sorted(all_timestamps):
        failures.append(FailureCode.DATA_TIMESTAMP_INVALID.value)
    if run.run_purpose is RunPurpose.OFFICIAL:
        if not config.source_revision.clean_worktree:
            failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)
        actual = capture_source_revision(ROOT)
        if (
            not actual.clean_worktree
            or actual.git_commit != config.source_revision.git_commit
            or actual.git_tree != config.source_revision.git_tree
        ):
            failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)
        if config.dataset_release.get("qualification_scope") == "M1_SYNTHETIC":
            failures.append(FailureCode.DATA_SOURCE_INVALID.value)
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


def _capture_engine(
    engine: BacktestEngine,
    strategy: GuardedCausalStrategy,
    instrument_id: Any,
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
                "signed_qty": str(position.signed_qty),
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
    funding_events: list[dict[str, Any]] = []
    for position in positions_native:
        funding_events.extend(
            adjustment.to_dict()
            for adjustment in position.adjustments()
            if str(adjustment.adjustment_type) == "FUNDING"
        )

    native_result = engine.get_result()
    try:
        unrealized = str(engine.portfolio.unrealized_pnl(instrument_id))
    except Exception:
        unrealized = "UNAVAILABLE"
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
        "terminal_portfolio": {"unrealized_pnl": unrealized},
    }
    return {
        "order_rows": order_rows,
        "order_events": order_events,
        "fills": fill_events,
        "positions": position_rows,
        "account_events": account_events,
        "funding_events": funding_events,
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
        "terminal_portfolio": {"unrealized_pnl": unrealized},
        "semantic_sequence": semantic,
        "semantic_digest": canonical_sha256(semantic),
    }


def _empty_capture() -> dict[str, Any]:
    semantic = {
        "orders": [],
        "fills": [],
        "positions": [],
        "account_events": [],
        "funding": [],
        "terminal_portfolio": {},
    }
    return {
        "order_rows": [],
        "order_events": [],
        "fills": [],
        "positions": [],
        "account_events": [],
        "funding_events": [],
        "backtest_result": None,
        "mark_price_count": 0,
        "funding_rate_count": 0,
        "terminal_portfolio": {},
        "semantic_sequence": semantic,
        "semantic_digest": canonical_sha256(semantic),
    }


def run_lab(config: LabRunRequest) -> RunResult:
    """Run one isolated Nautilus engine and check its persisted evidence."""

    run = config.lab_run_config
    run_dir = config.evidence_root / f"{run.run_id}-{run.config_sha256[:12]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "lab_run_config.json").write_bytes(run.to_json_bytes() + b"\n")
    (run_dir / "lab_run_config.sha256").write_text(
        run.config_sha256 + "\n",
        encoding="utf-8",
    )
    (run_dir / "runtime.lock.json").write_bytes((ROOT / "runtime.lock.json").read_bytes())
    (run_dir / "source_revision.json").write_bytes(config.source_revision.to_json_bytes() + b"\n")
    _write_json(run_dir / "dataset_release.json", dict(config.dataset_release))
    (run_dir / "strategy_spec.json").write_bytes(config.strategy_spec.to_json_bytes() + b"\n")

    preflight = _preflight(config)
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
    if not preflight:
        engine: BacktestEngine | None = None
        strategy: GuardedCausalStrategy | None = None
        try:
            engine = BacktestEngine(to_nautilus_engine_config(run.nautilus_engine_config))
            latency_override = None
            if config.qualification_control is QualificationControl.ZERO_LATENCY_NEGATIVE_CONTROL:
                latency_override = StaticLatencyModel(0, 0, 0, 0)
            add_venue_from_config(
                engine,
                run.nautilus_venue_config,
                latency_model_override=latency_override,
            )
            engine.add_instrument(config.instrument)
            bars = [item for item in config.data if isinstance(item, Bar)]
            strategy = GuardedCausalStrategy()
            strategy.configure(
                instrument_id=config.instrument.id,
                bar_type=bars[0].bar_type,
                profile=run.market_profile,
                plan=config.strategy_plan,
                scoring_start_ns=_timestamp_ns(run.scoring_start),
                scoring_end_exclusive_ns=_timestamp_ns(run.scoring_end_exclusive),
                effective_insert_latency_ns=(
                    0
                    if config.qualification_control
                    is QualificationControl.ZERO_LATENCY_NEGATIVE_CONTROL
                    else run.nautilus_venue_config.latency_model.effective_insert_latency_nanos
                ),
                size_precision=config.instrument.size_precision,
                initial_capital_amount=run.initial_capital.amount,
                initial_capital_currency=run.initial_capital.currency,
            )
            engine.add_strategy(strategy)
            engine.add_data(list(config.data))
            engine_started = True
            engine.run()
            engine_completed = True
            observations = json.loads(json.dumps(strategy.observations))
            capture = _capture_engine(engine, strategy, config.instrument.id)
        except Exception as exc:
            engine_error = f"{type(exc).__name__}: {exc}"
            preflight.append(FailureCode.UNSUPPORTED_RUNTIME.value)
            if engine is not None and strategy is not None:
                try:
                    observations = json.loads(json.dumps(strategy.observations))
                    capture = _capture_engine(engine, strategy, config.instrument.id)
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

    nautilus_result = {
        "schema": "m1-nautilus-run-result-v1",
        "run_id": run.run_id,
        "run_purpose": run.run_purpose.value,
        "config_sha256": run.config_sha256,
        "engine_executed": engine_started,
        "engine_completed": engine_completed,
        "engine_error": engine_error,
        "preflight_failure_codes": list(dict.fromkeys(preflight)),
        "backtest_result": capture["backtest_result"],
        "strategy_observations": observations,
        "semantic_sequence": capture["semantic_sequence"],
        "semantic_digest": capture["semantic_digest"],
        "native_fill_evidence_sha256": hashlib.sha256(native_fill_bytes).hexdigest(),
        "evidence_bindings": {
            "lab_run_config_sha256": sha256_file(run_dir / "lab_run_config.json"),
            "runtime_lock_sha256": sha256_file(run_dir / "runtime.lock.json"),
            "source_revision_sha256": sha256_file(run_dir / "source_revision.json"),
            "dataset_release_sha256": sha256_file(run_dir / "dataset_release.json"),
            "strategy_spec_sha256": sha256_file(run_dir / "strategy_spec.json"),
        },
        "mark_price_count": capture["mark_price_count"],
        "funding_rate_count": capture["funding_rate_count"],
        "terminal_portfolio": capture["terminal_portfolio"],
        "mark_fallback_accepted": False,
        "fee_model": "nautilus_trader.execution:MakerTakerFeeModel",
        "project_fee_postings": 0,
        "project_funding_postings": 0,
        "project_financial_ledger": False,
        "terminal_policy": run.terminal_policy,
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

    report = check_evidence_directory(run_dir)
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


__all__ = [
    "LabRunRequest",
    "QualificationControl",
    "RunResult",
    "capture_source_revision",
    "run_lab",
]
