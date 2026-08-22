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
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.status import FailureCode


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


def check_evidence_directory(run_dir: Path) -> CheckerReport:
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

    result = _read_json(run_dir / "nautilus_result.json")
    bindings = result.get("evidence_bindings", {})
    binding_paths = {
        "lab_run_config_sha256": "lab_run_config.json",
        "runtime_lock_sha256": "runtime.lock.json",
        "source_revision_sha256": "source_revision.json",
        "dataset_release_sha256": "dataset_release.json",
        "strategy_spec_sha256": "strategy_spec.json",
    }
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
        resolved_tree = subprocess.run(
            ["git", "rev-parse", f"{source.git_commit}^{{tree}}"],
            cwd=ROOT,
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
        if config.run_purpose.value == "OFFICIAL" and not source.clean_worktree:
            source_ok = False
        checks.append(
            {
                "name": "source_revision",
                "pass": source_ok,
                "clean_worktree": source.clean_worktree,
                "qualification_clean_required": config.run_purpose.value == "OFFICIAL",
                "git_tree_resolves_from_commit": resolved_tree == source.git_tree,
            },
        )
    if not source_ok:
        blocked.append(FailureCode.EVIDENCE_INCOMPLETE.value)

    dataset = _read_json(run_dir / "dataset_release.json")
    dataset_material = {
        key: value
        for key, value in dataset.items()
        if key != "dataset_release_id"
    }
    dataset_ok = (
        dataset.get("dataset_release_id") == config.dataset_release_id
        and canonical_sha256(dataset_material) == config.dataset_release_id
    )
    checks.append(
        {
            "name": "dataset_binding",
            "pass": dataset_ok,
            "content_identity_resolved": canonical_sha256(dataset_material),
        },
    )
    if not dataset_ok:
        blocked.append(FailureCode.DATA_HASH_MISMATCH.value)

    data_rows = dataset.get("data", [])
    bar_times = sorted(
        int(row["ts_init"])
        for row in data_rows
        if row.get("type") == "Bar"
    )
    no_ignored_gap = len(bar_times) == len(set(bar_times)) and all(
        current - previous == ONE_MINUTE_NS
        for previous, current in zip(bar_times, bar_times[1:], strict=False)
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

    strategy_spec = _read_json(run_dir / "strategy_spec.json")
    strategy_material = dict(strategy_spec)
    strategy_material.pop("strategy_id", None)
    strategy_ok = canonical_sha256(strategy_material) == config.strategy_spec_id
    checks.append({"name": "strategy_spec_binding", "pass": strategy_ok})
    if not strategy_ok:
        blocked.append(FailureCode.CONFIG_HASH_MISMATCH.value)

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
            dataset.get("mark_role") == "markPriceKlines"
            and dataset.get("mark_complete") is True
            and int(result.get("mark_price_count", 0)) > 0
            and result.get("mark_fallback_accepted") is False
        )
        checks.append({"name": "perpetual_mark_role", "pass": mark_ok})
        if not mark_ok:
            blocked.append(FailureCode.MARK_ROLE_INVALID.value)
        funding_rows = _read_csv(run_dir / "funding.csv")
        expected_settlements = dataset.get("expected_funding_settlements", [])
        funding_ok = (
            int(result.get("project_funding_postings", -1)) == 0
            and result.get("project_financial_ledger") is False
            and len(funding_rows) == len(expected_settlements)
            and (
                not expected_settlements
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
                if int(row["ts_event"]) == int(expected["boundary_ns"])
                and row["pnl_change"] == expected["pnl_change"]
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
