"""Read-only M1 invariant checker over persisted Nautilus Run evidence."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.config import SourceRevision
from crypto_lab.data import DatasetRelease
from crypto_lab.data import HISTORICAL_NORMALIZER_VERSIONS
from crypto_lab.data import NORMALIZER_VERSION
from crypto_lab.data import SyntheticQualificationDatasetRelease
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.git_identity import verify_source_revision
from crypto_lab.status import FailureCode
from crypto_lab.strategies import RegisteredStrategyIdentity
from crypto_lab.strategies import StrategySpec
from crypto_lab.strategies import resolve_registered_strategy_identity


class CheckerOutcome(StrEnum):
    CHECK_PASS = "CHECK_PASS"
    CHECK_FAIL = "CHECK_FAIL"
    CHECK_BLOCKED = "CHECK_BLOCKED"


@dataclass(frozen=True)
class CheckerReport:
    outcome: CheckerOutcome
    failure_codes: tuple[str, ...]
    checks: tuple[dict[str, Any], ...]

    def to_builtins(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "failure_codes": list(self.failure_codes),
            "checks": list(self.checks),
            "mutated_run_evidence": False,
        }


_REQUIRED_COMMON = {
    "lab_run_config.json",
    "lab_run_config.sha256",
    "runtime.lock.json",
    "source_revision.json",
    "dataset_release.json",
    "strategy_spec.json",
    "orders.csv",
    "fills.csv",
    "positions.csv",
    "account.csv",
    "native_fills.jsonl",
    "nautilus_result.json",
}

ROOT = Path(__file__).resolve().parents[2]
ONE_MINUTE_NS = 60_000_000_000
DAY_NS = 86_400_000_000_000
OWNER_SMOKE_STRATEGY_FAMILY = "BTCUSDT_DAILY_PRICE_VS_SMA20_TREND"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _commission_amount(value: str) -> Decimal:
    amount, _currency = value.split(" ", maxsplit=1)
    return Decimal(amount)


def check_evidence_directory(
    run_dir: Path,
    *,
    repository_root: Path = ROOT,
    official_source_required: bool | None = None,
    source_revision_current_head_required: bool = True,
) -> CheckerReport:
    """Check immutable files without writing to the directory or engine state."""

    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    blocked: list[str] = []

    present = {path.name for path in run_dir.iterdir() if path.is_file()}
    required = set(_REQUIRED_COMMON)
    if (run_dir / "funding.csv").exists():
        required.add("funding.csv")
    missing = sorted(required - present)
    checks.append({"name": "evidence_inventory", "pass": not missing, "missing": missing})
    if missing:
        return CheckerReport(
            CheckerOutcome.CHECK_BLOCKED,
            (FailureCode.EVIDENCE_INCOMPLETE.value,),
            tuple(checks),
        )

    try:
        config_bytes = (run_dir / "lab_run_config.json").read_bytes()
        config = LabRunConfig.from_json_bytes(config_bytes)
        declared_config_hash = (run_dir / "lab_run_config.sha256").read_text(
            encoding="utf-8",
        ).strip()
        config_ok = declared_config_hash == config.config_sha256
    except Exception as exc:
        checks.append({"name": "config_identity", "pass": False, "detail": str(exc)})
        return CheckerReport(
            CheckerOutcome.CHECK_BLOCKED,
            (FailureCode.CONFIG_HASH_MISMATCH.value,),
            tuple(checks),
        )
    checks.append({"name": "config_identity", "pass": config_ok})
    if not config_ok:
        blocked.append(FailureCode.CONFIG_HASH_MISMATCH.value)
    official = (
        config.run_purpose.value in {"RESEARCH", "OFFICIAL"}
        if official_source_required is None
        else official_source_required
    )
    if official:
        official_missing = sorted(
            {
                "native_completed_trades.json",
                "strategy_identity.json",
                "strategy_identity.sha256",
            }
            - present,
        )
        checks.append(
            {
                "name": "official_strategy_identity_inventory",
                "pass": not official_missing,
                "missing": official_missing,
            },
        )
        if official_missing:
            blocked.append(FailureCode.EVIDENCE_INCOMPLETE.value)

    result = _read_json(run_dir / "nautilus_result.json")
    bindings = result.get("evidence_bindings", {})
    binding_paths = {
        "lab_run_config_sha256": "lab_run_config.json",
        "runtime_lock_sha256": "runtime.lock.json",
        "source_revision_sha256": "source_revision.json",
        "dataset_release_sha256": "dataset_release.json",
        "strategy_spec_sha256": "strategy_spec.json",
    }
    if official and (run_dir / "strategy_identity.json").is_file():
        binding_paths["strategy_identity_bytes_sha256"] = "strategy_identity.json"
    binding_mismatches = [
        name
        for name, filename in binding_paths.items()
        if bindings.get(name) != sha256_file(run_dir / filename)
    ]
    checks.append(
        {
            "name": "immutable_input_bindings",
            "pass": not binding_mismatches,
            "mismatches": binding_mismatches,
        },
    )
    if binding_mismatches:
        blocked.append(FailureCode.EVIDENCE_INCOMPLETE.value)

    runtime_ok = (
        sha256_file(run_dir / "runtime.lock.json") == config.runtime_lock_sha256
        and bindings.get("runtime_lock_sha256") == config.runtime_lock_sha256
    )
    checks.append({"name": "runtime_lock_binding", "pass": runtime_ok})
    if not runtime_ok:
        blocked.append(FailureCode.RUNTIME_LOCK_MISMATCH.value)

    try:
        source = SourceRevision.from_json_bytes((run_dir / "source_revision.json").read_bytes())
        if official:
            verification = verify_source_revision(
                source,
                repository=repository_root,
                require_current_head=source_revision_current_head_required,
                require_clean=True,
                allowed_output_paths=(run_dir,),
            )
            resolved_tree = source.git_tree
            source_ok = (
                verification.frozen_commit_tree_valid
                and verification.frozen_commit_on_branch
                and source.clean_worktree
            )
        else:
            resolved_tree = subprocess.run(
                ["git", "rev-parse", f"{source.git_commit}^{{tree}}"],
                cwd=repository_root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            source_ok = bool(
                source.repository
                and source.git_commit
                and source.git_tree
                and resolved_tree == source.git_tree
            )
    except Exception as exc:
        source_ok = False
        checks.append({"name": "source_revision", "pass": False, "detail": str(exc)})
    else:
        if official and not source.clean_worktree:
            source_ok = False
        checks.append(
            {
                "name": "source_revision",
                "pass": source_ok,
                "clean_worktree": source.clean_worktree,
                "official_clean_required": official,
                "git_tree_resolves_from_commit": resolved_tree == source.git_tree,
            },
        )
    if not source_ok:
        blocked.append(FailureCode.EVIDENCE_INCOMPLETE.value)

    dataset_bytes = (run_dir / "dataset_release.json").read_bytes()
    dataset_raw = _read_json(run_dir / "dataset_release.json")
    declared_dataset_identity = dataset_raw.get("dataset_release_id")
    raw_material = dict(dataset_raw)
    raw_material.pop("dataset_release_id", None)
    if "qualification_scope" not in dataset_raw:
        raw_material.pop("created_at_utc", None)
        if dataset_raw.get("normalizer_version") in HISTORICAL_NORMALIZER_VERSIONS:
            for source_object in raw_material.get("source_objects", []):
                if isinstance(source_object, dict):
                    source_object.pop("conflicts_with_sha256", None)
    raw_identity_mismatch = (
        not isinstance(declared_dataset_identity, str)
        or canonical_sha256(raw_material) != declared_dataset_identity
    )
    try:
        dataset = (
            SyntheticQualificationDatasetRelease.from_json_bytes(dataset_bytes)
            if "qualification_scope" in dataset_raw
            else DatasetRelease.from_json_bytes(dataset_bytes)
        )
    except Exception as exc:
        detail = str(exc)
        failure_code = (
            FailureCode.DATA_HASH_MISMATCH.value
            if raw_identity_mismatch or "identity" in detail or "dataset_release_id" in detail
            else FailureCode.DOWNSTREAM_CONTRACT_FAILURE.value
        )
        checks.append({"name": "dataset_binding", "pass": False, "detail": detail})
        return CheckerReport(
            CheckerOutcome.CHECK_BLOCKED,
            tuple(dict.fromkeys([*blocked, failure_code])),
            tuple(checks),
        )
    resolved_identity = canonical_sha256(dataset.material_payload())
    dataset_ok = (
        dataset.dataset_release_id == config.dataset_release_id
        and resolved_identity == config.dataset_release_id
    )
    checks.append(
        {
            "name": "dataset_binding",
            "pass": dataset_ok,
            "content_identity_resolved": resolved_identity,
        },
    )
    if not dataset_ok:
        blocked.append(FailureCode.DATA_HASH_MISMATCH.value)

    if isinstance(dataset, SyntheticQualificationDatasetRelease):
        bar_times = sorted(
            item.ts_init for item in dataset.data if item.type == "Bar"
        )
        no_ignored_gap = len(bar_times) == len(set(bar_times)) and all(
            current - previous == ONE_MINUTE_NS
            for previous, current in zip(bar_times, bar_times[1:], strict=False)
        )
        source_roles_ok = True
        catalog_binding_ok = True
    else:
        role_results = dataset.completeness_result.role_results
        bar_times = []
        no_ignored_gap = (
            dataset.completeness_result.status == "PASS"
            and dataset.completeness_result.no_repairs
            and all(item.actual_count == item.expected_count for item in role_results)
        )
        contract = result.get("dataset_contract", {})
        source_roles_ok = (
            dataset.is_current_contract
            and contract.get("source_roles_verified") is True
            and contract.get("dataset_release_id") == dataset.dataset_release_id
        )
        catalog_binding_ok = (
            contract.get("catalog_identity_verified") is True
            and contract.get("catalog_identity") == dataset.catalog_identity
            and contract.get("caller_side_conversion_used") is False
        )
    checks.append(
        {
            "name": "no_required_data_gap_ignored",
            "pass": no_ignored_gap,
            "bar_count": len(bar_times),
        },
    )
    if not no_ignored_gap:
        blocked.append(FailureCode.DATA_GAP.value)
    checks.append({"name": "dataset_source_roles", "pass": source_roles_ok})
    checks.append({"name": "dataset_catalog_binding", "pass": catalog_binding_ok})
    if not source_roles_ok or not catalog_binding_ok:
        blocked.append(FailureCode.DOWNSTREAM_CONTRACT_FAILURE.value)

    strategy_spec = _read_json(run_dir / "strategy_spec.json")
    is_owner_smoke_sma20 = (
        strategy_spec.get("parameters", {}).get("strategy_family")
        == OWNER_SMOKE_STRATEGY_FAMILY
    )
    strategy_material = dict(strategy_spec)
    strategy_material.pop("strategy_id", None)
    strategy_ok = canonical_sha256(strategy_material) == config.strategy_spec_id
    checks.append({"name": "strategy_spec_binding", "pass": strategy_ok})
    if not strategy_ok:
        blocked.append(FailureCode.CONFIG_HASH_MISMATCH.value)
    if official and not any(
        code == FailureCode.EVIDENCE_INCOMPLETE.value for code in blocked
    ):
        try:
            parsed_spec = StrategySpec.from_json_bytes(
                (run_dir / "strategy_spec.json").read_bytes(),
            )
            identity = RegisteredStrategyIdentity.from_json_bytes(
                (run_dir / "strategy_identity.json").read_bytes(),
            )
            declared_identity = (run_dir / "strategy_identity.sha256").read_text(
                encoding="utf-8",
            ).strip()
            resolved_identity = resolve_registered_strategy_identity(
                identity.registration_id,
                strategy_spec=parsed_spec,
                source_revision=source,
            )
            official_identity_ok = (
                identity == resolved_identity
                and identity.strategy_spec == strategy_spec
                and identity.strategy_spec_id == config.strategy_spec_id
                and declared_identity == identity.strategy_identity_sha256
                and bindings.get("strategy_identity_sha256")
                == identity.strategy_identity_sha256
            )
        except Exception as exc:
            official_identity_ok = False
            identity_detail = str(exc)
        else:
            identity_detail = identity.strategy_identity_sha256
        checks.append(
            {
                "name": "official_registered_strategy_identity",
                "pass": official_identity_ok,
                "detail": identity_detail,
            },
        )
        if not official_identity_ok:
            blocked.append(FailureCode.CONFIG_HASH_MISMATCH.value)
    if is_owner_smoke_sma20:
        native_paths = {
            "native_completed_trades.json": "native_completed_trades_sha256",
            "native_portfolio_snapshots.jsonl": "native_portfolio_snapshots_sha256",
            "native_statistics.json": "native_statistics_sha256",
        }
        native_missing = sorted(name for name in native_paths if name not in present)
        native_mismatches = [
            name
            for name, binding in native_paths.items()
            if name in present and bindings.get(binding) != sha256_file(run_dir / name)
        ]
        native_evidence_ok = not native_missing and not native_mismatches
        checks.append(
            {
                "name": "owner_smoke_native_financial_evidence",
                "pass": native_evidence_ok,
                "missing": native_missing,
                "binding_mismatches": native_mismatches,
            },
        )
        if not native_evidence_ok:
            blocked.append(FailureCode.EVIDENCE_INCOMPLETE.value)
    is_m3_qualification = (
        strategy_spec.get("parameters", {}).get("m3_profile_qualification") == "true"
    )
    if is_m3_qualification:
        plan_path = run_dir / "strategy_plan.json"
        try:
            plan_evidence = _read_json(plan_path)
            plan_identity = canonical_sha256(plan_evidence["material_payload"])
            plan_ok = (
                plan_evidence.get("schema") == "strategy-plan-evidence-v1"
                and plan_evidence.get("strategy_plan_sha256") == plan_identity
                and strategy_spec["parameters"].get("strategy_plan_sha256") == plan_identity
            )
        except Exception as exc:
            plan_ok = False
            plan_identity = f"INVALID: {exc}"
        checks.append(
            {
                "name": "m3_strategy_plan_binding",
                "pass": plan_ok,
                "strategy_plan_sha256": plan_identity,
            },
        )
        if not plan_ok:
            blocked.append(FailureCode.CONFIG_HASH_MISMATCH.value)
        m3_source_ok = source.clean_worktree
        checks.append(
            {
                "name": "m3_clean_source_revision",
                "pass": m3_source_ok,
                "git_commit": source.git_commit,
                "git_tree": source.git_tree,
            },
        )
        if not m3_source_ok:
            blocked.append(FailureCode.EVIDENCE_INCOMPLETE.value)

    preflight_codes = [str(code) for code in result.get("preflight_failure_codes", [])]
    if preflight_codes:
        checks.append(
            {
                "name": "preflight",
                "pass": False,
                "failure_codes": preflight_codes,
            },
        )
        blocked.extend(preflight_codes)
        return CheckerReport(
            CheckerOutcome.CHECK_BLOCKED,
            tuple(dict.fromkeys(blocked)),
            tuple(checks),
        )
    else:
        checks.append({"name": "preflight", "pass": True})

    if official:
        isolation = result.get("network_guard", {}).get("process_isolation")
        offline_ok = bool(
            isinstance(isolation, dict)
            and isolation.get("mechanism") == "LINUX_SECCOMP_BPF_TSYNC_ERRNO_EPERM"
            and isolation.get("no_new_privs") is True
            and isolation.get("seccomp_mode") == 2
            and isolation.get("filters_after", 0) > isolation.get("filters_before", -1)
            and isinstance(isolation.get("closed_inherited_socket_descriptors"), list)
            and isolation.get("current_process_probe_errno") == 1
            and isolation.get("io_uring_probe_errno") == 1
            and isolation.get("child_python_probe_errno") == 1
            and isolation.get("child_native_probe_blocked") is True
            and isolation.get("child_dns_probe_blocked") is True
            and isolation.get("inherited_by_fork_exec") is True
            and isolation.get("external_endpoint_contacted") is False
        )
        checks.append({"name": "official_process_network_isolation", "pass": offline_ok})
        if not offline_ok:
            blocked.append(FailureCode.NETWORK_DURING_OFFICIAL_RUN.value)

    observations = result.get("strategy_observations", {})
    bars = observations.get("bars", [])
    visibility_ok = all(
        int(item["callback_clock_ns"]) >= int(item["ts_init"])
        and int(item["ts_init"]) == int(item["ts_event"])
        for item in bars
    )
    checks.append({"name": "completed_bar_visibility", "pass": visibility_ok})
    if not visibility_ok:
        failures.append(FailureCode.LOOKAHEAD_DETECTED.value)

    if is_owner_smoke_sma20:
        daily = observations.get("daily_signal_bars", [])
        signals = observations.get("signals", [])
        daily_ok = bool(daily) and all(
            int(item["interval_end_exclusive_ns"]) % DAY_NS == 0
            and int(item["interval_end_exclusive_ns"])
            - int(item["interval_start_ns"])
            == DAY_NS
            and int(item["available_at_ns"]) == int(item["interval_end_exclusive_ns"])
            and int(item["sma_count"]) == index
            and bool(item["sma_initialized"]) == (index >= 20)
            for index, item in enumerate(daily, start=1)
        )
        close_by_end = {
            int(item["interval_end_exclusive_ns"]): Decimal(str(item["close"]))
            for item in daily
        }
        ends = [int(item["interval_end_exclusive_ns"]) for item in daily]
        signal_ok = daily_ok
        expected_signals = 0
        for index, end_ns in enumerate(ends):
            start_ns = end_ns - DAY_NS
            if index >= 19 and start_ns >= int(config.scoring_start.timestamp() * 1e9) and end_ns <= int(
                config.scoring_end_exclusive.timestamp() * 1e9,
            ):
                expected_signals += 1
        if len(signals) != expected_signals:
            signal_ok = False
        for signal in signals:
            try:
                end_ns = int(signal["signal_bar_interval_end_exclusive_ns"])
                index = ends.index(end_ns)
                exact_sma = sum(
                    (close_by_end[item] for item in ends[index - 19 : index + 1]),
                    Decimal(0),
                ) / Decimal(20)
                close = close_by_end[end_ns]
                native_sma = Decimal(str(signal["sma20"]))
                expected_target = (
                    "LONG"
                    if close > native_sma
                    else (
                        "FLAT"
                        if close == native_sma
                        or config.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
                        else "SHORT"
                    )
                )
                signal_ok = signal_ok and bool(
                    index >= 19
                    and int(signal["completed_daily_bar_count"]) == index + 1
                    and abs(native_sma - exact_sma) <= Decimal("0.0000000001")
                    and signal["target"] == expected_target
                    and int(signal["signal_timestamp_ns"]) >= end_ns
                )
            except Exception:
                signal_ok = False
        checks.append(
            {
                "name": "owner_smoke_sma20_daily_causality",
                "pass": signal_ok,
                "daily_completed_bars": len(daily),
                "expected_scored_signals": expected_signals,
                "actual_scored_signals": len(signals),
                "first_signal_requires_at_least_completed_bars": 20,
            },
        )
        if not signal_ok:
            failures.append(FailureCode.LOOKAHEAD_DETECTED.value)

    submitted = {
        item["client_order_id"]: item
        for item in observations.get("submitted_intents", [])
    }
    scoring_start_ns = int(config.scoring_start.timestamp() * 1e9)
    scoring_end_ns = int(config.scoring_end_exclusive.timestamp() * 1e9)
    eligibility_ok = all(
        int(item["signal_bar_interval_start_ns"]) >= scoring_start_ns
        and int(item["signal_bar_interval_end_exclusive_ns"]) <= scoring_end_ns
        and int(item["signal_timestamp_ns"]) >= int(item["signal_bar_available_at_ns"])
        for item in submitted.values()
    )
    checks.append(
        {
            "name": "submitted_signal_bar_eligibility",
            "pass": eligibility_ok,
            "submitted_count": len(submitted),
        },
    )
    if not eligibility_ok:
        failures.append(FailureCode.LOOKAHEAD_DETECTED.value)

    fills = _read_csv(run_dir / "fills.csv")
    causal_ok = True
    terminal_ok = True
    instrument_ok = True
    for fill in fills:
        intent = submitted.get(fill["client_order_id"])
        if intent is None or int(fill["ts_event"]) <= int(intent["signal_bar_available_at_ns"]):
            causal_ok = False
        if int(fill["ts_event"]) >= scoring_end_ns:
            terminal_ok = False
        if fill["instrument_id"] != config.instrument_id:
            instrument_ok = False
    checks.append({"name": "causal_fills", "pass": causal_ok, "fill_count": len(fills)})
    checks.append({"name": "terminal_fill_boundary", "pass": terminal_ok})
    checks.append({"name": "fill_instrument", "pass": instrument_ok})
    if not causal_ok:
        failures.append(FailureCode.SAME_BAR_EXECUTION_DETECTED.value)
    if not terminal_ok:
        failures.append(FailureCode.CAUSAL_EXECUTION_UNRESOLVED.value)
    if not instrument_ok:
        failures.append(FailureCode.PERP_PROFILE_INVALID.value)

    native_fill_bytes = (run_dir / "native_fills.jsonl").read_bytes()
    native_fills = [
        json.loads(line)
        for line in native_fill_bytes.splitlines()
        if line.strip()
    ]
    preserved_fields = (
        "event_id",
        "client_order_id",
        "venue_order_id",
        "trade_id",
        "position_id",
        "account_id",
        "instrument_id",
        "order_side",
        "last_qty",
        "last_px",
        "commission",
        "ts_event",
    )
    projection_ok = len(native_fills) == len(fills) and all(
        all(str(native[field]) == row[field] for field in preserved_fields)
        for native, row in zip(native_fills, fills, strict=True)
    )
    fill_digest_ok = (
        hashlib.sha256(native_fill_bytes).hexdigest()
        == result.get("native_fill_evidence_sha256")
        and projection_ok
    )
    checks.append(
        {
            "name": "native_fill_immutability",
            "pass": fill_digest_ok,
            "native_projection_matches": projection_ok,
        },
    )
    if not fill_digest_ok:
        failures.append(FailureCode.FILL_MUTATION_DETECTED.value)

    guard_codes = [
        item["failure_code"] for item in observations.get("guard_failures", [])
    ]
    checks.append(
        {
            "name": "pre_submit_guards",
            "pass": not guard_codes,
            "failure_codes": guard_codes,
        },
    )
    blocked.extend(guard_codes)

    orders = _read_csv(run_dir / "orders.csv")
    lifecycle_ok = all(
        row["order_type"] == "MARKET"
        and row["time_in_force"] == "GTC"
        and bool(row["terminal_ns"])
        for row in orders
    )
    checks.append(
        {
            "name": "market_order_lifecycle_and_tif",
            "pass": lifecycle_ok,
            "order_count": len(orders),
        },
    )
    if not lifecycle_ok:
        failures.append(FailureCode.CAUSAL_EXECUTION_UNRESOLVED.value)
    intervals = sorted(
        (
            int(row["initialized_ns"]),
            int(row["terminal_ns"]) if row["terminal_ns"] else None,
        )
        for row in orders
    )
    overlap = False
    for index, (started, terminal) in enumerate(intervals[:-1]):
        next_started = intervals[index + 1][0]
        if terminal is None or next_started < terminal:
            overlap = True
            break
    checks.append({"name": "single_non_terminal_order", "pass": not overlap})
    if overlap:
        failures.append(FailureCode.CONCURRENT_STRATEGY_ORDER_REJECTED.value)

    positions = _read_csv(run_dir / "positions.csv")
    account_rows = _read_csv(run_dir / "account.csv")
    if config.market_profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        no_short = all(Decimal(row["signed_qty"]) >= 0 for row in positions)
        no_borrow = all(
            Decimal(row["total"]) >= 0 and Decimal(row["free"]) >= 0
            for row in account_rows
            if row.get("total") and row.get("free")
        )
        spot_ok = no_short and no_borrow
        checks.append({"name": "spot_cash_no_short_or_borrow", "pass": spot_ok})
        if not spot_ok:
            failures.append(FailureCode.SPOT_SHORT_OR_BORROW_DETECTED.value)
        if is_m3_qualification:
            spot_profile_ok = (
                config.nautilus_venue_config.account_type == "CASH"
                and config.nautilus_venue_config.oms_type == "NETTING"
                and config.nautilus_venue_config.allow_cash_borrowing is False
                and config.nautilus_engine_config.portfolio.use_mark_prices is False
                and config.mark_binding == "NOT_APPLICABLE"
                and config.funding_binding == "NOT_APPLICABLE"
            )
            checks.append({"name": "m3_spot_profile_binding", "pass": spot_profile_ok})
            if not spot_profile_ok:
                failures.append(FailureCode.SPOT_SHORT_OR_BORROW_DETECTED.value)
    else:
        venue = config.nautilus_venue_config
        one_way_ok = (
            venue.account_type == "MARGIN"
            and venue.oms_type == "NETTING"
            and venue.default_leverage == Decimal("1")
            and all(item.leverage == Decimal("1") for item in venue.instrument_leverages)
        )
        cross_zero_ok = all(
            not (
                Decimal(item["position_before"]) > 0
                and item["side"] == "SELL"
                and Decimal(item["quantity"]) > Decimal(item["position_before"])
            )
            and not (
                Decimal(item["position_before"]) < 0
                and item["side"] == "BUY"
                and Decimal(item["quantity"]) > abs(Decimal(item["position_before"]))
            )
            for item in submitted.values()
        )
        checks.append({"name": "perpetual_one_way_netting", "pass": one_way_ok})
        checks.append({"name": "no_submitted_cross_zero_order", "pass": cross_zero_ok})
        if not one_way_ok or not cross_zero_ok:
            failures.append(FailureCode.PERP_PROFILE_INVALID.value)
        if is_owner_smoke_sma20:
            reversal = observations.get("reversal_sequence", [])
            triples_ok = len(reversal) % 3 == 0
            for offset in range(0, len(reversal), 3):
                group = reversal[offset : offset + 3]
                if len(group) != 3:
                    triples_ok = False
                    continue
                triples_ok = triples_ok and bool(
                    [item.get("event") for item in group]
                    == [
                        "CLOSE_TO_FLAT_SUBMITTED",
                        "NATIVE_FLAT_CONFIRMED",
                        "SEPARATE_REOPEN_SUBMITTED",
                    ]
                    and group[0].get("client_order_id") != group[2].get("client_order_id")
                    and Decimal(str(group[1].get("signed_position"))) == 0
                    and int(group[0].get("observed_at_ns"))
                    < int(group[2].get("observed_at_ns"))
                )
            expected_closes = sum(
                str(item.get("reason", "")).startswith("SMA20_CLOSE_TO_FLAT_BEFORE_")
                for item in observations.get("submitted_intents", [])
            )
            triples_ok = triples_ok and len(reversal) // 3 == expected_closes
            checks.append(
                {
                    "name": "owner_smoke_separate_close_then_reverse",
                    "pass": triples_ok,
                    "reversal_count": len(reversal) // 3,
                    "close_to_flat_orders": expected_closes,
                },
            )
            if not triples_ok:
                failures.append(FailureCode.PERP_PROFILE_INVALID.value)
        if is_m3_qualification:
            perp_profile_ok = (
                config.nautilus_engine_config.portfolio.use_mark_prices is True
                and config.nautilus_venue_config.liquidation_enabled is False
                and config.nautilus_venue_config.allow_cash_borrowing is False
                and config.mark_binding == dataset.mark_data_identity
                and config.funding_binding == dataset.funding_data_identity
            )
            checks.append({"name": "m3_perpetual_profile_binding", "pass": perp_profile_ok})
            if not perp_profile_ok:
                failures.append(FailureCode.PERP_PROFILE_INVALID.value)

    fee_rate = config.fee_assumption.taker_fee
    fee_ok = True
    for fill in fills:
        expected = (Decimal(fill["last_px"]) * Decimal(fill["last_qty"]) * fee_rate).quantize(
            Decimal("0.00000001"),
        )
        if _commission_amount(fill["commission"]) != expected:
            fee_ok = False
    if int(result.get("project_fee_postings", -1)) != 0:
        fee_ok = False
    if result.get("fee_model") != "nautilus_trader.execution:MakerTakerFeeModel":
        fee_ok = False
    checks.append({"name": "maker_taker_fee_exactly_once", "pass": fee_ok})
    if not fee_ok:
        failures.append(FailureCode.FEE_DOUBLE_COUNT.value)

    if config.market_profile is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
        mark_ok = (
            int(result.get("mark_price_count", 0)) > 0
            and result.get("mark_fallback_accepted") is False
        )
        if isinstance(dataset, SyntheticQualificationDatasetRelease):
            mark_ok = mark_ok and dataset.mark_role == "markPriceKlines" and dataset.mark_complete is True
        else:
            mark_ok = mark_ok and dataset.mark_data_identity != "NOT_APPLICABLE" and source_roles_ok
        checks.append({"name": "perpetual_mark_role", "pass": mark_ok})
        if not mark_ok:
            blocked.append(FailureCode.MARK_ROLE_INVALID.value)
        funding_rows = _read_csv(run_dir / "funding.csv")
        expected_settlements = (
            dataset.expected_funding_settlements
            if isinstance(dataset, SyntheticQualificationDatasetRelease)
            else ()
        )
        unique_native_settlements = {
            (
                row["instrument_id"],
                row["ts_event"],
                row["pnl_change"],
                row["quantity_change"],
                row["reason"],
            )
            for row in funding_rows
        }
        funding_ok = (
            int(result.get("project_funding_postings", -1)) == 0
            and result.get("project_financial_ledger") is False
            and len(unique_native_settlements) == len(funding_rows)
            and (
                not isinstance(dataset, SyntheticQualificationDatasetRelease)
                or len(funding_rows) == len(expected_settlements)
            )
            and (
                not expected_settlements and isinstance(dataset, SyntheticQualificationDatasetRelease)
                or (
                    int(result.get("funding_rate_count", 0)) > 0
                    and int(result.get("mark_price_count", 0)) > 0
                )
            )
            and all(
                row["adjustment_type"] == "FUNDING"
                and row["reason"].startswith("funding_settlement:")
                for row in funding_rows
            )
        )
        for expected in expected_settlements:
            matches = [
                row
                for row in funding_rows
                if int(row["ts_event"]) == expected.boundary_ns
                and row["pnl_change"] == expected.pnl_change
                and row["adjustment_type"] == "FUNDING"
            ]
            if len(matches) != 1:
                funding_ok = False
        checks.append(
            {
                "name": "native_funding_exactly_once",
                "pass": funding_ok,
                "expected_settlements": len(expected_settlements),
                "actual_settlements": len(funding_rows),
            },
        )
        if not funding_ok:
            failures.append(FailureCode.FUNDING_DOUBLE_COUNT.value)
        if is_owner_smoke_sma20 and not isinstance(dataset, SyntheticQualificationDatasetRelease):
            try:
                funding_source = _read_json(run_dir / "funding_source.json")
                source_events = funding_source["events"]
                checkpoints = result["native_funding_checkpoints"]
                source_by_boundary = {
                    int(item["calc_time_ns"]): item for item in source_events
                }
                checkpoint_by_boundary = {
                    int(item["boundary_ns"]): item for item in checkpoints
                }
                exact_funding_ok = bool(
                    len(source_by_boundary) == len(source_events)
                    and len(checkpoint_by_boundary) == len(checkpoints)
                    and set(source_by_boundary) == set(checkpoint_by_boundary)
                    and result["dataset_contract"]["funding_source_event_count"]
                    == len(source_events)
                    and result["dataset_contract"]["funding_runtime_update_count"]
                    == 2 * len(source_events)
                )
                applicable_boundaries = 0
                expected_adjustments: list[tuple[int, Decimal]] = []
                for boundary_ns, source_event in source_by_boundary.items():
                    checkpoint = checkpoint_by_boundary[boundary_ns]
                    mark = checkpoint.get("native_mark_price")
                    positions_at_boundary = checkpoint.get("open_positions", [])
                    native = checkpoint.get("native_adjustments", [])
                    exact_funding_ok = exact_funding_ok and bool(
                        isinstance(mark, dict)
                        and mark.get("instrument_id") == config.instrument_id
                        and int(mark.get("ts_event", -1)) == boundary_ns
                        and int(mark.get("ts_init", -1)) == boundary_ns
                        and len(positions_at_boundary) <= 1
                    )
                    if positions_at_boundary:
                        applicable_boundaries += 1
                        signed_qty = Decimal(str(positions_at_boundary[0]["signed_qty"]))
                        expected = (
                            -signed_qty
                            * Decimal(str(mark["value"]))
                            * Decimal(str(source_event["funding_rate"]))
                        ).quantize(Decimal("0.00000001"))
                        exact_funding_ok = exact_funding_ok and bool(
                            len(native) == 1
                            and native[0]["adjustment_type"] == "FUNDING"
                            and int(native[0]["ts_event"]) == boundary_ns
                            and _commission_amount(native[0]["pnl_change"]) == expected
                        )
                        expected_adjustments.append((boundary_ns, expected))
                    else:
                        exact_funding_ok = exact_funding_ok and not native
                actual_adjustments = sorted(
                    (int(row["ts_event"]), _commission_amount(row["pnl_change"]))
                    for row in funding_rows
                )
                exact_funding_ok = exact_funding_ok and actual_adjustments == sorted(
                    expected_adjustments,
                )
                mark_role = next(
                    item
                    for item in dataset.completeness_result.role_results
                    if item.source_role.value == "USDM_PERPETUAL_MARK_1M"
                )
                exact_mark_ok = bool(
                    observations.get("mark_price_update_count") == mark_role.actual_count
                    and mark_role.actual_count == mark_role.expected_count
                    and config.nautilus_engine_config.portfolio.use_mark_prices is True
                    and result.get("mark_fallback_accepted") is False
                )
                exact_detail = {
                    "source_event_count": len(source_events),
                    "processed_checkpoint_count": len(checkpoints),
                    "applicable_open_position_boundaries": applicable_boundaries,
                    "native_settlement_count": len(funding_rows),
                    "runtime_update_count": result["dataset_contract"][
                        "funding_runtime_update_count"
                    ],
                }
            except Exception as exc:
                exact_funding_ok = False
                exact_mark_ok = False
                exact_detail = {"detail": str(exc)}
            checks.append(
                {
                    "name": "owner_smoke_all_official_funding_processed",
                    "pass": exact_funding_ok,
                    **exact_detail,
                },
            )
            checks.append(
                {
                    "name": "owner_smoke_official_mark_valuation",
                    "pass": exact_mark_ok,
                    "mark_fallback_accepted": result.get("mark_fallback_accepted"),
                },
            )
            if not exact_funding_ok:
                failures.append(FailureCode.FUNDING_DOUBLE_COUNT.value)
            if not exact_mark_ok:
                blocked.append(FailureCode.MARK_ROLE_INVALID.value)
        if is_m3_qualification and not isinstance(dataset, SyntheticQualificationDatasetRelease):
            try:
                funding_source = _read_json(run_dir / "funding_source.json")
                checkpoints = result["native_funding_checkpoints"]
                source_events = funding_source["events"]
                source_event = source_events[0]
                boundary_ns = int(source_event["calc_time_ns"])
                checkpoint = next(
                    item for item in checkpoints if int(item["boundary_ns"]) == boundary_ns
                )
                native = checkpoint["native_adjustments"]
                open_positions = checkpoint["open_positions"]
                mark_rows = [
                    item
                    for item in observations.get("mark_price_updates", [])
                    if int(item["ts_event"]) == boundary_ns
                ]
                signed_qty = Decimal(open_positions[0]["signed_qty"])
                mark = Decimal(mark_rows[0]["value"])
                rate = Decimal(source_event["funding_rate"])
                expected = (-signed_qty * mark * rate).quantize(Decimal("0.00000001"))
                actual = _commission_amount(native[0]["pnl_change"])
                account_before = max(
                    (row for row in account_rows if int(row["ts_event"]) < boundary_ns),
                    key=lambda row: int(row["ts_event"]),
                )
                boundary_totals = {
                    Decimal(row["total"])
                    for row in account_rows
                    if int(row["ts_event"]) == boundary_ns
                }
                account_delta = next(iter(boundary_totals)) - Decimal(account_before["total"])
                real_funding_ok = (
                    len(source_events) == 1
                    and len(checkpoints) == 1
                    and len(native) == 1
                    and len(open_positions) == 1
                    and signed_qty > 0
                    and len(mark_rows) == 1
                    and len(boundary_totals) == 1
                    and int(native[0]["ts_event"]) == boundary_ns
                    and native[0]["adjustment_type"] == "FUNDING"
                    and actual == expected
                    and account_delta == expected
                    and result["dataset_contract"]["funding_source_event_count"] == 1
                    and result["dataset_contract"]["funding_runtime_update_count"] == 2
                    and result["project_funding_postings"] == 0
                    and result["project_financial_ledger"] is False
                )
                funding_detail = {
                    "boundary_ns": boundary_ns,
                    "position_before": str(signed_qty),
                    "mark": str(mark),
                    "rate": str(rate),
                    "expected_native_cash_effect": str(expected),
                    "actual_native_adjustment": str(actual),
                    "actual_account_delta": str(account_delta),
                    "source_events": len(source_events),
                    "native_settlements": len(native),
                }
            except Exception as exc:
                real_funding_ok = False
                funding_detail = {"detail": str(exc)}
            checks.append(
                {"name": "m3_real_native_funding", "pass": real_funding_ok, **funding_detail},
            )
            if not real_funding_ok:
                failures.append(FailureCode.FUNDING_DOUBLE_COUNT.value)

            expected_lifecycle = [Decimal("0.004"), Decimal("0.003"), Decimal("0"), Decimal("-0.001")]
            actual_lifecycle = [
                Decimal(item["signed_position"])
                for item in observations.get("position_sequence", [])
            ]
            lifecycle_ok = actual_lifecycle == expected_lifecycle
            checks.append(
                {
                    "name": "m3_perpetual_netting_lifecycle",
                    "pass": lifecycle_ok,
                    "expected": [str(item) for item in expected_lifecycle],
                    "actual": [str(item) for item in actual_lifecycle],
                },
            )
            if not lifecycle_ok:
                failures.append(FailureCode.PERP_PROFILE_INVALID.value)

    boundary = observations.get("scoring_boundary")
    boundary_ok = boundary is not None and (
        Decimal(boundary["signed_position"]) == 0
        and int(boundary["non_terminal_strategy_orders"]) == 0
        and Decimal(boundary["account_total"]) == config.initial_capital.amount
        and boundary["currency"] == config.initial_capital.currency
    )
    checks.append({"name": "scoring_start_state", "pass": boundary_ok})
    if not boundary_ok:
        failures.append(FailureCode.CAUSAL_EXECUTION_UNRESOLVED.value)

    terminal_policy_ok = (
        result.get("terminal_policy") == "MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE"
        and result.get("synthetic_terminal_close_order") is False
        and int(result.get("terminal_non_terminal_strategy_orders", -1)) == 0
    )
    checks.append({"name": "terminal_policy", "pass": terminal_policy_ok})
    if not terminal_policy_ok:
        failures.append(FailureCode.CAUSAL_EXECUTION_UNRESOLVED.value)

    failures = list(dict.fromkeys(failures))
    blocked = list(dict.fromkeys(blocked))
    if failures:
        return CheckerReport(CheckerOutcome.CHECK_FAIL, tuple(failures), tuple(checks))
    if blocked:
        return CheckerReport(CheckerOutcome.CHECK_BLOCKED, tuple(blocked), tuple(checks))
    return CheckerReport(CheckerOutcome.CHECK_PASS, (), tuple(checks))


__all__ = ["CheckerOutcome", "CheckerReport", "check_evidence_directory"]
