"""Read-only M1 invariant checker over persisted Nautilus Run evidence."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from bisect import bisect_left
from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

from nautilus_trader.model import MarkPriceUpdate

from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.config import RuntimeLock
from crypto_lab.config import SourceRevision
from crypto_lab.data import DatasetRelease
from crypto_lab.data import FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY
from crypto_lab.data import FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR
from crypto_lab.data import FUNDING_NATIVE_BINDING_SINGLE
from crypto_lab.data import HISTORICAL_NORMALIZER_VERSIONS
from crypto_lab.data import INSTRUMENT_REPAIR_NORMALIZER_VERSION
from crypto_lab.data import SyntheticQualificationDatasetRelease
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.git_identity import verify_source_revision
from crypto_lab.status import FailureCode
from crypto_lab.timestamps import utc_datetime_to_ns
from crypto_lab.strategies import RegisteredStrategyIdentity
from crypto_lab.strategies import StrategySpec
from crypto_lab.strategies import registered_strategy_identity_matches_frozen_source
from crypto_lab.strategies import annualized_realized_volatility_28d
from crypto_lab.strategies import is_monday_utc_boundary
from crypto_lab.strategies import momentum_28d
from crypto_lab.strategies import volatility_target_fraction
from crypto_lab.strategies import weekly_target


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
WEEKLY_TSMOM_STRATEGY_FAMILY = "BTCUSDT_WEEKLY_TSMOM28_V1"
BUY_AND_HOLD_STRATEGY_FAMILY = "BUY_AND_HOLD_1X_V1"
NATIVE_RESEARCH_FAMILIES = {
    OWNER_SMOKE_STRATEGY_FAMILY,
    WEEKLY_TSMOM_STRATEGY_FAMILY,
    BUY_AND_HOLD_STRATEGY_FAMILY,
}
MAX_FUNDING_MARK_STALENESS_NS = ONE_MINUTE_NS


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


def _account_balance_totals(rows: Any) -> dict[str, Decimal] | None:
    """Parse persisted native balance snapshots without depending on formatting."""

    if not isinstance(rows, list):
        return None
    values: dict[str, Decimal] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("currency"), str):
            return None
        currency = str(row["currency"])
        if currency in values:
            return None
        try:
            values[currency] = Decimal(str(row["total"]).split(" ", maxsplit=1)[0])
        except Exception:
            return None
    return values


def validate_spot_cash_reconciliation(
    *,
    fills: list[dict[str, str]],
    account_rows: list[dict[str, str]],
    position_rows: list[dict[str, str]],
    instrument_id: str,
    base_currency: str,
    quote_currency: str,
    initial_quote_balance: Decimal,
) -> tuple[bool, dict[str, Any]]:
    """Independently prove native CASH Fill/Account/Position consistency.

    This is a read-only invariant projection, not an accounting authority.  It
    derives the balance change each native Fill must have caused, then requires
    the persisted native AccountState and Position snapshots to agree exactly.
    """

    errors: list[str] = []
    grouped: dict[int, list[dict[str, str]]] = {}
    for row in account_rows:
        try:
            grouped.setdefault(int(row["event_index"]), []).append(row)
        except Exception:
            errors.append("ACCOUNT_EVENT_INDEX_INVALID")
    groups = [grouped[index] for index in sorted(grouped)]
    if sorted(grouped) != list(range(len(grouped))):
        errors.append("ACCOUNT_EVENT_SEQUENCE_GAP")

    def balances(rows: list[dict[str, str]], *, initial: bool = False) -> dict[str, Decimal]:
        result: dict[str, Decimal] = {}
        for row in rows:
            currency = row.get("currency", "")
            try:
                total = Decimal(row["total"])
                free = Decimal(row["free"])
                locked = Decimal(row["locked"])
            except Exception:
                errors.append("ACCOUNT_BALANCE_INVALID")
                continue
            if currency in result:
                errors.append("ACCOUNT_CURRENCY_DUPLICATE")
            if total != free + locked or min(total, free, locked) < 0:
                errors.append("ACCOUNT_BALANCE_COMPONENT_MISMATCH")
            if row.get("account_type") != "CASH":
                errors.append("ACCOUNT_TYPE_NOT_CASH")
            result[currency] = total
        allowed = {base_currency, quote_currency}
        if not set(result).issubset(allowed):
            errors.append("ACCOUNT_UNEXPECTED_CURRENCY")
        if quote_currency not in result:
            errors.append("ACCOUNT_REQUIRED_CURRENCY_MISSING")
        return result

    def exact_cash_totals(
        actual: dict[str, Decimal],
        *,
        expected_base: Decimal,
        expected_quote: Decimal,
    ) -> bool:
        return bool(
            actual.get(base_currency, Decimal(0)) == expected_base
            and actual.get(quote_currency) == expected_quote
        )

    initial_balances = balances(groups[0], initial=True) if groups else {}
    expected_base = initial_balances.get(base_currency, Decimal(0))
    expected_quote = initial_balances.get(quote_currency, Decimal(0))
    if expected_base != 0 or expected_quote != initial_quote_balance:
        errors.append("INITIAL_CASH_BALANCE_MISMATCH")

    account_timestamps: list[int] = []
    for group in groups:
        try:
            account_timestamps.append(int(group[0]["ts_event"]))
        except Exception:
            errors.append("ACCOUNT_TIMESTAMP_INVALID")
            account_timestamps.append(-1)
    if account_timestamps != sorted(account_timestamps):
        errors.append("ACCOUNT_TIMESTAMP_ORDER_INVALID")

    position_events = [row for row in position_rows if row.get("row_type") != "FINAL_NATIVE_POSITION"]
    if len(position_events) != len(fills):
        errors.append("FILL_POSITION_CARDINALITY_MISMATCH")
    reconciled_fills = 0
    account_cursor = 1
    for index, fill in enumerate(fills):
        try:
            if fill["instrument_id"] != instrument_id:
                errors.append("FILL_INSTRUMENT_MISMATCH")
            quantity = Decimal(fill["last_qty"])
            price = Decimal(fill["last_px"])
            commission_text = fill["commission"]
            commission_amount_text, commission_currency = commission_text.split(" ", maxsplit=1)
            commission = Decimal(commission_amount_text)
            if (
                quantity <= 0
                or price <= 0
                or commission < 0
                or commission_currency != quote_currency
                or fill.get("currency") != quote_currency
            ):
                errors.append("FILL_CURRENCY_OR_AMOUNT_INVALID")
                continue
            side = fill["order_side"]
            notional = quantity * price
            prior_base = expected_base
            prior_quote = expected_quote
            if side == "BUY":
                expected_base += quantity
                expected_quote -= notional + commission
            elif side == "SELL":
                expected_base -= quantity
                expected_quote += notional - commission
            else:
                errors.append("FILL_SIDE_INVALID")
                continue
            if expected_base < 0:
                errors.append("SPOT_SELL_EXCEEDS_AVAILABLE_BASE")
            if expected_quote < 0:
                errors.append("SPOT_BUY_EXCEEDS_AVAILABLE_QUOTE")
            fill_timestamp = int(fill["ts_event"])
            account_match = False
            while account_cursor < len(groups):
                group = groups[account_cursor]
                actual = balances(group)
                account_timestamp = account_timestamps[account_cursor]
                if account_timestamp > fill_timestamp:
                    break
                if account_timestamp < fill_timestamp:
                    if not exact_cash_totals(
                        actual,
                        expected_base=prior_base,
                        expected_quote=prior_quote,
                    ):
                        errors.append("UNBOUND_ACCOUNT_BALANCE_CHANGE")
                    account_cursor += 1
                    continue
                if exact_cash_totals(
                    actual,
                    expected_base=expected_base,
                    expected_quote=expected_quote,
                ):
                    account_match = True
                    account_cursor += 1
                    break
                if not exact_cash_totals(
                    actual,
                    expected_base=prior_base,
                    expected_quote=prior_quote,
                ):
                    if actual.get(base_currency, Decimal(0)) != expected_base:
                        errors.append("BASE_BALANCE_DELTA_MISMATCH")
                    if actual.get(quote_currency) != expected_quote:
                        errors.append("QUOTE_BALANCE_DELTA_MISMATCH")
                account_cursor += 1
            if not account_match:
                errors.append("FILL_ACCOUNT_BINDING_MISSING")
                if account_cursor >= len(groups):
                    errors.append("BASE_BALANCE_DELTA_MISMATCH")
                    errors.append("QUOTE_BALANCE_DELTA_MISMATCH")
            if index < len(position_events):
                position = position_events[index]
                try:
                    signed = Decimal(position["signed_qty"])
                except Exception:
                    errors.append("POSITION_QUANTITY_INVALID")
                else:
                    if (
                        position.get("instrument_id") != instrument_id
                        or int(position.get("ts_event", -1)) != int(fill["ts_event"])
                        or signed != expected_base
                    ):
                        errors.append("FILL_POSITION_DELTA_MISMATCH")
            if account_match:
                reconciled_fills += 1
        except Exception:
            errors.append("FILL_RECONCILIATION_MALFORMED")

    while account_cursor < len(groups):
        actual = balances(groups[account_cursor])
        if not exact_cash_totals(
            actual,
            expected_base=expected_base,
            expected_quote=expected_quote,
        ):
            errors.append("UNBOUND_ACCOUNT_BALANCE_CHANGE")
        account_cursor += 1

    final_positions = [row for row in position_rows if row.get("row_type") == "FINAL_NATIVE_POSITION"]
    final_signed = Decimal(0)
    for row in final_positions:
        try:
            quantity = Decimal(row["quantity"])
            if row.get("side") == "LONG":
                final_signed += quantity
            elif row.get("side") == "SHORT":
                final_signed -= quantity
            elif quantity != 0:
                errors.append("FINAL_POSITION_SIDE_INVALID")
        except Exception:
            errors.append("FINAL_POSITION_QUANTITY_INVALID")
    if final_signed != expected_base:
        errors.append("FINAL_POSITION_BALANCE_MISMATCH")

    unique_errors = tuple(dict.fromkeys(errors))
    return (
        not unique_errors,
        {
            "errors": list(unique_errors),
            "fill_count": len(fills),
            "reconciled_fill_count": reconciled_fills,
            "account_event_count": len(groups),
            "position_event_count": len(position_events),
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "expected_terminal_base": str(expected_base),
            "expected_terminal_quote": str(expected_quote),
            "native_financial_state_mutated_by_checker": False,
        },
    )


def validate_official_funding_binding(
    *,
    source_events: list[dict[str, Any]],
    mark_source_events: list[dict[str, Any]],
    position_rows: list[dict[str, str]],
    checkpoints: list[dict[str, Any]],
    funding_rows: list[dict[str, str]],
    dataset_contract: dict[str, Any],
    instrument_id: str,
    max_mark_staleness_ns: int = MAX_FUNDING_MARK_STALENESS_NS,
) -> tuple[bool, tuple[str, ...], dict[str, Any]]:
    """Validate exact source/runtime/position/mark/account settlement binding.

    Two identical pinned-runtime updates are transport/binding events for one
    official source event.  Financial cardinality is counted only from native
    ``PositionAdjusted(FUNDING)`` rows and their account effect.  Mark lookup is
    latest-causal at-or-before the millisecond-offset source timestamp.
    """

    failures: list[str] = []
    source_by_boundary: dict[int, dict[str, Any]] = {}
    source_keys: set[str] = set()
    for item in source_events:
        try:
            boundary = int(item["calc_time_ns"])
            event_key = str(item["event_key"])
        except Exception:
            failures.append(FailureCode.FUNDING_AMBIGUOUS.value)
            continue
        if boundary in source_by_boundary or event_key in source_keys:
            failures.append(FailureCode.FUNDING_AMBIGUOUS.value)
        if item.get("instrument_id") != instrument_id:
            failures.append(FailureCode.FUNDING_AMBIGUOUS.value)
        source_by_boundary[boundary] = item
        source_keys.add(event_key)

    mark_by_timestamp: dict[int, dict[str, Any]] = {}
    for item in mark_source_events:
        try:
            timestamp = int(item["ts_event"])
            Decimal(str(item["value"]))
        except Exception:
            failures.append(FailureCode.MARK_ROLE_INVALID.value)
            continue
        if (
            item.get("instrument_id") != instrument_id
            or int(item.get("ts_init", -1)) != timestamp
            or timestamp in mark_by_timestamp
        ):
            failures.append(FailureCode.MARK_ROLE_INVALID.value)
        mark_by_timestamp[timestamp] = item
    mark_timestamps = sorted(mark_by_timestamp)

    source_positions: list[tuple[int, int, Decimal]] = []
    for sequence, row in enumerate(position_rows):
        if row.get("row_type") == "FINAL_NATIVE_POSITION":
            continue
        try:
            timestamp = int(row["ts_event"])
            signed_qty = Decimal(str(row["signed_qty"]))
        except Exception:
            failures.append(FailureCode.FUNDING_AMBIGUOUS.value)
            continue
        if row.get("instrument_id") != instrument_id:
            failures.append(FailureCode.FUNDING_AMBIGUOUS.value)
        source_positions.append((timestamp, sequence, signed_qty))
    source_positions.sort()
    position_timestamps = [item[0] for item in source_positions]

    checkpoint_by_boundary: dict[int, dict[str, Any]] = {}
    for item in checkpoints:
        try:
            boundary = int(item["boundary_ns"])
        except Exception:
            failures.append(FailureCode.FUNDING_AMBIGUOUS.value)
            continue
        if boundary in checkpoint_by_boundary:
            failures.append(FailureCode.FUNDING_DOUBLE_COUNT.value)
        checkpoint_by_boundary[boundary] = item

    declared_source_count = int(
        dataset_contract.get(
            "execution_funding_source_event_count",
            dataset_contract.get("funding_source_event_count", -1),
        ),
    )
    declared_runtime_count = int(
        dataset_contract.get(
            "execution_funding_runtime_update_count",
            dataset_contract.get("funding_runtime_update_count", -1),
        ),
    )
    native_binding = dataset_contract.get("funding_native_binding")
    repetitions = {
        FUNDING_NATIVE_BINDING_SINGLE: 1,
        FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY: 2,
        FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR: 2,
    }.get(native_binding)
    if (
        repetitions is None
        or declared_source_count != len(source_events)
        or declared_runtime_count != repetitions * len(source_events)
        or set(source_by_boundary) != set(checkpoint_by_boundary)
    ):
        failures.append(FailureCode.FUNDING_AMBIGUOUS.value)

    expected_adjustments: list[tuple[int, Decimal]] = []
    applicable = 0
    no_position = 0
    mark_ages: list[int] = []
    for boundary, source_event in source_by_boundary.items():
        checkpoint = checkpoint_by_boundary.get(boundary)
        if checkpoint is None:
            continue
        runtime_updates = checkpoint.get("runtime_updates_at_boundary", [])
        rate = Decimal(str(source_event["funding_rate"]))
        expected_next_funding_ns = (
            boundary
            if native_binding == FUNDING_NATIVE_BINDING_RC2_EXPLICIT_BOUNDARY_PAIR
            else None
        )
        runtime_pair_ok = bool(
            repetitions is not None
            and len(runtime_updates) == repetitions
            and checkpoint.get("source_event_key") == source_event["event_key"]
            and all(
                Decimal(str(item.get("rate"))) == rate
                and int(item.get("interval", -1))
                == int(source_event["funding_interval_hours"]) * 60
                and int(item.get("ts_event", -1)) == boundary
                and int(item.get("ts_init", -1)) == boundary
                and item.get("next_funding_ns") == expected_next_funding_ns
                for item in runtime_updates
            )
        )
        if not runtime_pair_ok:
            failures.append(FailureCode.FUNDING_AMBIGUOUS.value)

        positions = checkpoint.get("open_positions", [])
        native = checkpoint.get("native_adjustments", [])
        if (
            checkpoint.get("eligible_position_capture")
            != "IMMEDIATELY_BEFORE_FUNDING_BOUNDARY"
            or not isinstance(positions, list)
            or len(positions) > 1
            or not isinstance(native, list)
        ):
            failures.append(FailureCode.FUNDING_AMBIGUOUS.value)
            continue
        position_index = bisect_left(position_timestamps, boundary) - 1
        expected_signed_qty = (
            Decimal(0)
            if position_index < 0
            else source_positions[position_index][2]
        )
        checkpoint_signed_qty = Decimal(0)
        if positions:
            try:
                checkpoint_signed_qty = Decimal(str(positions[0]["signed_qty"]))
            except Exception:
                failures.append(FailureCode.FUNDING_AMBIGUOUS.value)
                continue
        checkpoint_position_ok = not (
            checkpoint_signed_qty != expected_signed_qty
            or (
                positions
                and (
                    positions[0].get("instrument_id") != instrument_id
                    or int(positions[0].get("ts_last", boundary)) >= boundary
                )
            )
        )
        if expected_signed_qty == 0:
            no_position += 1
        else:
            applicable += 1
        if not checkpoint_position_ok:
            failures.append(FailureCode.FUNDING_AMBIGUOUS.value)
            continue
        if expected_signed_qty == 0:
            if positions or native:
                failures.append(FailureCode.FUNDING_DOUBLE_COUNT.value)
            if checkpoint.get("account_events_at_boundary"):
                failures.append(FailureCode.FUNDING_DOUBLE_COUNT.value)
            before_totals = _account_balance_totals(
                checkpoint.get("account_balances_before_boundary"),
            )
            after_totals = _account_balance_totals(
                checkpoint.get("account_balances_after_boundary"),
            )
            # The pinned engine creates its initial AccountState lazily when
            # the first data batch is processed.  At a first-event funding
            # boundary, an empty pre-boundary cache and unchanged configured
            # opening balances after the batch are initialization, not a
            # settlement.  The absence of a native adjustment, funding row,
            # and boundary AccountState remains mandatory and is cross-checked
            # below.
            lazy_initialization = before_totals == {} and bool(after_totals)
            if (
                before_totals is None
                or after_totals is None
                or (not lazy_initialization and before_totals != after_totals)
            ):
                failures.append(FailureCode.FUNDING_AMBIGUOUS.value)
            continue

        mark = checkpoint.get("native_mark_price")
        mark_index = bisect_right(mark_timestamps, boundary) - 1
        expected_mark = None if mark_index < 0 else mark_by_timestamp[mark_timestamps[mark_index]]
        if (
            not isinstance(mark, dict)
            or expected_mark is None
            or mark.get("instrument_id") != instrument_id
            or int(mark.get("ts_event", -1)) != int(expected_mark["ts_event"])
            or int(mark.get("ts_init", -1)) != int(expected_mark["ts_init"])
            or Decimal(str(mark.get("value"))) != Decimal(str(expected_mark["value"]))
        ):
            failures.append(FailureCode.MARK_ROLE_INVALID.value)
            continue
        mark_ts = int(mark.get("ts_event", -1))
        age = boundary - mark_ts
        mark_ages.append(age)
        if (
            mark_ts > boundary
            or age < 0
            or age > max_mark_staleness_ns
            or checkpoint.get("mark_selection")
            != "LATEST_CAUSAL_AT_OR_BEFORE_FUNDING_TIMESTAMP"
            or int(checkpoint.get("native_mark_age_ns", -1)) != age
        ):
            failures.append(FailureCode.MARK_ROLE_INVALID.value)
            continue

        expected = (
            -expected_signed_qty * Decimal(str(mark["value"])) * rate
        ).quantize(Decimal("0.00000001"))
        if len(native) != 1:
            failures.append(FailureCode.FUNDING_DOUBLE_COUNT.value)
            continue
        adjustment = native[0]
        instrument_contract = dataset_contract.get("instrument")
        settlement_currency = (
            None
            if not isinstance(instrument_contract, dict)
            else instrument_contract.get("settlement_currency")
        )
        adjustment_money = str(adjustment.get("pnl_change", "")).split(" ", maxsplit=1)
        account_events = checkpoint.get("account_events_at_boundary")
        if not (
            adjustment.get("adjustment_type") == "FUNDING"
            and adjustment.get("instrument_id") == instrument_id
            and int(adjustment.get("ts_event", -1)) == boundary
            and _commission_amount(str(adjustment.get("pnl_change"))) == expected
            and len(adjustment_money) == 2
            and adjustment_money[1] == settlement_currency
            and str(adjustment.get("reason", "")).startswith("funding_settlement:")
            and isinstance(account_events, list)
            and bool(account_events)
            and all(int(event.get("ts_event", -1)) == boundary for event in account_events)
        ):
            failures.append(FailureCode.FUNDING_DOUBLE_COUNT.value)
            continue

        before_totals = _account_balance_totals(
            checkpoint.get("account_balances_before_boundary"),
        )
        after_totals = _account_balance_totals(
            checkpoint.get("account_balances_after_boundary"),
        )
        account_delta_ok = bool(
            len(adjustment_money) == 2
            and before_totals is not None
            and after_totals is not None
            and set(before_totals) == set(after_totals)
            and adjustment_money[1] in before_totals
            and after_totals[adjustment_money[1]]
            - before_totals[adjustment_money[1]]
            == expected
            and all(
                after_totals[currency] == before_totals[currency]
                for currency in before_totals
                if currency != adjustment_money[1]
            )
        )
        if not account_delta_ok:
            failures.append(FailureCode.FUNDING_DOUBLE_COUNT.value)
            continue
        expected_adjustments.append((boundary, expected))

    actual_adjustments: list[tuple[int, Decimal]] = []
    for row in funding_rows:
        try:
            if (
                row.get("adjustment_type") != "FUNDING"
                or row.get("instrument_id") != instrument_id
                or not str(row.get("reason", "")).startswith("funding_settlement:")
            ):
                raise ValueError("funding row role mismatch")
            actual_adjustments.append(
                (int(row["ts_event"]), _commission_amount(row["pnl_change"])),
            )
        except Exception:
            failures.append(FailureCode.FUNDING_AMBIGUOUS.value)
    actual_adjustments.sort()
    if actual_adjustments != sorted(expected_adjustments):
        failures.append(FailureCode.FUNDING_DOUBLE_COUNT.value)

    unique_failures = tuple(dict.fromkeys(failures))
    return (
        not unique_failures,
        unique_failures,
        {
            "source_event_count": len(source_events),
            "source_mark_event_count": len(mark_source_events),
            "source_position_event_count": len(source_positions),
            "processed_checkpoint_count": len(checkpoints),
            "applicable_open_position_boundaries": applicable,
            "no_position_boundaries": no_position,
            "native_settlement_count": len(funding_rows),
            "runtime_update_count": declared_runtime_count,
            "mark_age_ns_min": min(mark_ages) if mark_ages else None,
            "mark_age_ns_max": max(mark_ages) if mark_ages else None,
            "maximum_mark_staleness_ns": max_mark_staleness_ns,
            "mark_binding": "LATEST_CAUSAL_AT_OR_BEFORE_FUNDING_TIMESTAMP",
        },
    )


# Historical import compatibility; active code and evidence use the generic name.
validate_owner_smoke_funding_binding = validate_official_funding_binding


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
                "runtime_identity.json",
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
    scoring_end_ns_contract = utc_datetime_to_ns(config.scoring_end_exclusive)
    warmup_start_ns_contract = utc_datetime_to_ns(config.warmup_start)
    execution_window = result.get("execution_data_window")
    observations_for_window = result.get("strategy_observations", {})
    callback_window_ok = bool(
        isinstance(observations_for_window, dict)
        and all(
            int(item.get("ts_init", -1)) <= scoring_end_ns_contract
            for item in observations_for_window.get("bars", [])
        )
        and all(
            int(item.get("ts_init", -1)) <= scoring_end_ns_contract
            for item in observations_for_window.get("mark_price_updates", [])
        )
        and all(
            int(item.get("ts_init", -1)) < scoring_end_ns_contract
            for item in observations_for_window.get("funding_rate_updates", [])
        )
    )
    execution_window_ok = bool(
        isinstance(execution_window, dict)
        and execution_window.get("status") == "PASS"
        and int(execution_window.get("warmup_start_ns", -1)) == warmup_start_ns_contract
        and int(execution_window.get("scoring_end_exclusive_ns", -1))
        == scoring_end_ns_contract
        and execution_window.get("engine_received_post_boundary_data") is False
        and execution_window.get("point_events_at_scoring_end_included") is False
        and execution_window.get("completed_interval_observations_at_scoring_end_included")
        is True
        and int(execution_window.get("latest_qualified_valuation_observation_ns", -1))
        <= scoring_end_ns_contract
        and callback_window_ok
    )
    checks.append(
        {
            "name": "engine_half_open_scoring_window",
            "pass": execution_window_ok,
            "callback_window_pass": callback_window_ok,
            "window": execution_window,
        },
    )
    if not execution_window_ok:
        failures.append(FailureCode.LOOKAHEAD_DETECTED.value)
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
    if official and (run_dir / "runtime_identity.json").is_file():
        binding_paths["runtime_identity_sha256"] = "runtime_identity.json"
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

    runtime_proof_ok = not official
    runtime_proof_mismatches: list[str] = []
    if official and (run_dir / "runtime_identity.json").is_file():
        try:
            runtime_lock = RuntimeLock.from_json_bytes(
                (run_dir / "runtime.lock.json").read_bytes(),
            )
            runtime_identity = _read_json(run_dir / "runtime_identity.json")
            required_runtime_proof = {
                "installed_files_verified": True,
                "cache_files_recompiled_and_verified": True,
                "installed_payload_sha256": runtime_lock.nautilus_installed_payload_sha256,
                "installed_payload_file_count": runtime_lock.nautilus_installed_payload_file_count,
                "installed_wheel_sha256": runtime_lock.nautilus_wheel_sha256,
                "nautilus_version": runtime_lock.nautilus_version,
                "python_version": runtime_lock.python_version,
                "python_implementation": runtime_lock.python_implementation,
                "python_abi": runtime_lock.python_abi,
                "machine_architecture": runtime_lock.machine_architecture,
                "dependency_lock_sha256": runtime_lock.dependency_lock_sha256,
            }
            runtime_proof_mismatches = [
                name
                for name, expected in required_runtime_proof.items()
                if runtime_identity.get(name) != expected
            ]
            record_sha = runtime_identity.get("installed_record_sha256")
            runtime_proof_ok = bool(
                not runtime_proof_mismatches
                and isinstance(record_sha, str)
                and len(record_sha) == 64
                and all(character in "0123456789abcdef" for character in record_sha)
                and int(runtime_identity.get("installed_record_hashed_file_count", 0)) > 0
                and int(runtime_identity.get("installed_native_extension_count", 0)) > 0
                and result.get("runtime_identity_verified") is True
            )
        except Exception as exc:
            runtime_proof_mismatches = [f"{type(exc).__name__}: {exc}"]
            runtime_proof_ok = False
    checks.append(
        {
            "name": "installed_runtime_payload_proof",
            "pass": runtime_proof_ok,
            "mismatches": runtime_proof_mismatches,
        },
    )
    if not runtime_proof_ok:
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
        acceptance = None
        market_state_ok = True
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
        if dataset.normalizer_version == INSTRUMENT_REPAIR_NORMALIZER_VERSION:
            acceptance = contract.get("market_state_acceptance")
            execution_role = next(
                (
                    item
                    for item in role_results
                    if item.source_role.value
                    in {"SPOT_EXECUTION_1M", "USDM_PERPETUAL_EXECUTION_1M"}
                ),
                None,
            )
            mark_role = next(
                (
                    item
                    for item in role_results
                    if item.source_role.value == "USDM_PERPETUAL_MARK_1M"
                ),
                None,
            )
            expected_executable = (
                int(acceptance.get("expected_executable_bars", -1))
                if isinstance(acceptance, dict)
                else -1
            )
            accepted_executable = (
                int(acceptance.get("accepted_executable_bars", -1))
                if isinstance(acceptance, dict)
                else -1
            )
            # Spot role completeness is minute-disposition completeness: a
            # VERIFIED_NO_TRADE_INTERVAL occupies a minute but deliberately
            # has no Nautilus Bar.  The immutable acceptance artifact is bound
            # to the exact catalog identity, so its expected and accepted Bar
            # counts must match each other; only continuously priced Perpetual
            # execution requires equality with the role's minute count.
            execution_counts_ok = bool(
                execution_role is not None
                and expected_executable > 0
                and accepted_executable == expected_executable
                and (
                    config.market_profile.value == "BINANCE_SPOT_CASH_LONG_ONLY"
                    or expected_executable == execution_role.actual_count
                )
            )
            market_state_ok = bool(
                isinstance(acceptance, dict)
                and acceptance.get("status") == "PASS"
                and acceptance.get("gate") == "NAUTILUS_EXECUTABLE_MARKET_STATE_ACCEPTANCE"
                and acceptance.get("dataset_profile") == config.market_profile.value
                and acceptance.get("instrument_id") == config.instrument_id
                and acceptance.get("catalog_identity") == dataset.catalog_identity
                and acceptance.get("instrument_metadata_identity")
                == dataset.instrument_metadata_identity
                and execution_counts_ok
                and int(acceptance.get("precision_skipped_bars", -1)) == 0
                and int(acceptance.get("rejected_precision_events", -1)) == 0
                and int(acceptance.get("no_market_data_precision_warnings", -1)) == 0
                and int(acceptance.get("fatal_runtime_diagnostics", -1)) == 0
                and int(acceptance.get("missing_market_state", -1)) == 0
                and (
                    mark_role is None
                    or (
                        int(acceptance.get("expected_mark_updates", -1))
                        == mark_role.actual_count
                        and int(acceptance.get("accepted_mark_updates", -1))
                        == mark_role.actual_count
                    )
                )
            )
        else:
            acceptance = None
            market_state_ok = True
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
    checks.append(
        {
            "name": "nautilus_executable_market_state_acceptance",
            "pass": market_state_ok,
            "validation": acceptance,
        },
    )
    if not source_roles_ok or not catalog_binding_ok:
        blocked.append(FailureCode.DOWNSTREAM_CONTRACT_FAILURE.value)
    if not market_state_ok:
        blocked.append(FailureCode.INSTRUMENT_METADATA_INVALID.value)

    strategy_spec = _read_json(run_dir / "strategy_spec.json")
    strategy_family = strategy_spec.get("parameters", {}).get("strategy_family")
    is_owner_smoke_sma20 = strategy_family == OWNER_SMOKE_STRATEGY_FAMILY
    is_weekly_tsmom = strategy_family == WEEKLY_TSMOM_STRATEGY_FAMILY
    is_buy_and_hold = strategy_family == BUY_AND_HOLD_STRATEGY_FAMILY
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
            official_identity_ok = (
                registered_strategy_identity_matches_frozen_source(
                    identity,
                    parsed_spec,
                    source,
                    repository_root=repository_root,
                )
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
    if strategy_family in NATIVE_RESEARCH_FAMILIES:
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

    runtime_diagnostics = result.get("runtime_diagnostics", [])
    engine_health_ok = bool(
        result.get("engine_executed") is True
        and result.get("engine_completed") is True
        and result.get("engine_error") is None
        and isinstance(runtime_diagnostics, list)
        and not any(bool(item.get("fatal")) for item in runtime_diagnostics if isinstance(item, dict))
    )
    checks.append(
        {
            "name": "engine_runtime_health",
            "pass": engine_health_ok,
            "engine_error": result.get("engine_error"),
            "fatal_runtime_diagnostics": sum(
                bool(item.get("fatal"))
                for item in runtime_diagnostics
                if isinstance(item, dict)
            ),
        },
    )
    if not engine_health_ok:
        failures.append(FailureCode.UNSUPPORTED_RUNTIME.value)

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
        sma_lookback = int(strategy_spec["parameters"]["sma_lookback"])
        daily_ok = bool(daily) and all(
            int(item["interval_end_exclusive_ns"]) % DAY_NS == 0
            and int(item["interval_end_exclusive_ns"])
            - int(item["interval_start_ns"])
            == DAY_NS
            and int(item["available_at_ns"]) == int(item["interval_end_exclusive_ns"])
            and int(item["sma_count"]) == min(index, sma_lookback)
            and bool(item["sma_initialized"]) == (index >= sma_lookback)
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
            if (
                index >= sma_lookback - 1
                and start_ns >= utc_datetime_to_ns(config.scoring_start)
                and end_ns <= utc_datetime_to_ns(config.scoring_end_exclusive)
            ):
                expected_signals += 1
        if len(signals) != expected_signals:
            signal_ok = False
        for signal in signals:
            try:
                end_ns = int(signal["signal_bar_interval_end_exclusive_ns"])
                index = ends.index(end_ns)
                exact_sma = sum(
                    (
                        close_by_end[item]
                        for item in ends[index - sma_lookback + 1 : index + 1]
                    ),
                    Decimal(0),
                ) / Decimal(sma_lookback)
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
                    index >= sma_lookback - 1
                    and int(signal["completed_daily_bar_count"])
                    == min(index + 1, sma_lookback)
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
                "first_signal_requires_at_least_completed_bars": sma_lookback,
            },
        )
        if not signal_ok:
            failures.append(FailureCode.LOOKAHEAD_DETECTED.value)

    if is_weekly_tsmom:
        daily = observations.get("daily_signal_bars", [])
        signals = observations.get("signals", [])
        daily_ok = bool(daily) and all(
            int(item["interval_end_exclusive_ns"]) % DAY_NS == 0
            and int(item["interval_end_exclusive_ns"])
            - int(item["interval_start_ns"])
            == DAY_NS
            and int(item["available_at_ns"]) == int(item["interval_end_exclusive_ns"])
            and int(item["completed_close_count"]) == min(index, 29)
            for index, item in enumerate(daily, start=1)
        )
        ends = [int(item["interval_end_exclusive_ns"]) for item in daily]
        closes = [Decimal(str(item["close"])) for item in daily]
        scoring_start_ns = utc_datetime_to_ns(config.scoring_start)
        scoring_end_ns = utc_datetime_to_ns(config.scoring_end_exclusive)
        expected_indices = [
            index
            for index, end_ns in enumerate(ends)
            if index >= 28
            and scoring_start_ns <= end_ns < scoring_end_ns
            and is_monday_utc_boundary(end_ns)
        ]
        signal_ok = daily_ok and len(signals) == len(expected_indices)
        mode = strategy_spec["parameters"].get("candidate_mode")
        for signal, index in zip(signals, expected_indices, strict=False):
            try:
                window = tuple(closes[index - 28 : index + 1])
                exact_momentum = momentum_28d(window)
                expected_target = weekly_target(exact_momentum, config.market_profile)
                if mode == "TSMOM28_VOLATILITY_TARGET_20":
                    exact_volatility = annualized_realized_volatility_28d(window)
                    exact_fraction = volatility_target_fraction(exact_volatility)
                    if exact_volatility == 0:
                        expected_target = type(expected_target).FLAT
                    volatility_ok = bool(
                        Decimal(str(signal["annualized_realized_volatility"]))
                        == exact_volatility
                    )
                elif mode == "TSMOM28_FULL_NOTIONAL":
                    exact_fraction = Decimal(1)
                    volatility_ok = signal["annualized_realized_volatility"] == "NOT_APPLICABLE"
                else:
                    signal_ok = False
                    continue
                if expected_target.value == "FLAT":
                    exact_fraction = Decimal(0)
                end_ns = ends[index]
                signal_ok = signal_ok and bool(
                    int(signal["signal_bar_interval_end_exclusive_ns"]) == end_ns
                    and int(signal["signal_bar_available_at_ns"]) == end_ns
                    and int(signal["decision_timestamp_ns"]) == end_ns
                    and int(signal["signal_timestamp_ns"]) >= end_ns
                    and int(signal["completed_close_count"]) == 29
                    and Decimal(str(signal["momentum_28d"])) == exact_momentum
                    and Decimal(str(signal["target_fraction"])) == exact_fraction
                    and signal["target"] == expected_target.value
                    and volatility_ok
                )
            except Exception:
                signal_ok = False
        checks.append(
            {
                "name": "weekly_tsmom28_daily_causality",
                "pass": signal_ok,
                "daily_completed_bars": len(daily),
                "expected_weekly_decisions": len(expected_indices),
                "actual_weekly_decisions": len(signals),
                "completed_closes_required": 29,
            },
        )
        if not signal_ok:
            failures.append(FailureCode.LOOKAHEAD_DETECTED.value)

    if is_buy_and_hold:
        entries = observations.get("benchmark_entries", [])
        submitted_entries = [
            item
            for item in observations.get("submitted_intents", [])
            if item.get("reason") == "BUY_AND_HOLD_1X_INITIAL_ENTRY"
        ]
        benchmark_ok = bool(
            len(entries) == 1
            and len(submitted_entries) == 1
            and int(entries[0]["signal_bar_interval_start_ns"])
            == utc_datetime_to_ns(config.scoring_start)
            and Decimal(str(entries[0]["target_quantity"])) > 0
        )
        checks.append(
            {
                "name": "registered_buy_and_hold_first_eligible_entry",
                "pass": benchmark_ok,
                "entry_count": len(entries),
                "submitted_entry_count": len(submitted_entries),
            },
        )
        if not benchmark_ok:
            failures.append(FailureCode.CAUSAL_EXECUTION_UNRESOLVED.value)

    submitted = {
        item["client_order_id"]: item
        for item in observations.get("submitted_intents", [])
    }
    scoring_start_ns = utc_datetime_to_ns(config.scoring_start)
    scoring_end_ns = utc_datetime_to_ns(config.scoring_end_exclusive)
    eligibility_ok = all(
        int(item.get("decision_timestamp_ns", item["signal_bar_interval_start_ns"]))
        >= scoring_start_ns
        and int(item.get("decision_timestamp_ns", item["signal_bar_interval_end_exclusive_ns"]))
        < scoring_end_ns
        and int(item["signal_bar_interval_end_exclusive_ns"])
        <= int(item.get("decision_timestamp_ns", item["signal_bar_interval_end_exclusive_ns"]))
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
        if (
            intent is None
            or int(fill["ts_event"]) < int(intent["effective_insert_at_ns"])
            or int(fill["ts_event"]) <= int(intent["signal_bar_available_at_ns"])
        ):
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
    rejected_events = [
        item
        for item in result.get("semantic_sequence", {}).get("orders", [])
        if item.get("type") == "OrderRejected"
    ]
    no_market_rejections = [
        item
        for item in rejected_events
        if str(item.get("reason", "")).startswith("No market for ")
    ]
    executable_market_state_ok = bool(
        not no_market_rejections
        and not (
            orders
            and len(rejected_events) == len(orders)
            and not fills
        )
    )
    checks.append(
        {
            "name": "orders_reach_executable_market_state",
            "pass": executable_market_state_ok,
            "order_count": len(orders),
            "rejected_order_count": len(rejected_events),
            "no_market_rejection_count": len(no_market_rejections),
            "fill_count": len(fills),
        },
    )
    if not executable_market_state_ok:
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
        quote_currency = config.initial_capital.currency
        metadata_path = run_dir / "instrument_metadata.json"
        if metadata_path.is_file():
            metadata_raw = _read_json(metadata_path)
            base_currency = str(metadata_raw.get("base_currency", ""))
            metadata_currency_ok = bool(
                metadata_raw.get("instrument_id") == config.instrument_id
                and metadata_raw.get("quote_currency") == quote_currency
                and base_currency
                and base_currency != quote_currency
            )
        else:
            raw_symbol = config.instrument_id.split(".", maxsplit=1)[0]
            metadata_currency_ok = raw_symbol.endswith(quote_currency)
            base_currency = (
                raw_symbol[: -len(quote_currency)] if metadata_currency_ok else ""
            )
        if metadata_currency_ok:
            reconciliation_ok, reconciliation_detail = validate_spot_cash_reconciliation(
                fills=fills,
                account_rows=account_rows,
                position_rows=positions,
                instrument_id=config.instrument_id,
                base_currency=base_currency,
                quote_currency=quote_currency,
                initial_quote_balance=config.initial_capital.amount,
            )
        else:
            reconciliation_ok = False
            reconciliation_detail = {
                "errors": ["INSTRUMENT_CURRENCY_BINDING_INVALID"],
                "native_financial_state_mutated_by_checker": False,
            }
        checks.append(
            {
                "name": "spot_cash_reconciliation",
                "pass": reconciliation_ok,
                **reconciliation_detail,
            },
        )
        if not reconciliation_ok:
            failures.append(FailureCode.SPOT_SHORT_OR_BORROW_DETECTED.value)
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
        if is_owner_smoke_sma20 or is_weekly_tsmom:
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
            close_prefix = (
                "SMA20_CLOSE_TO_FLAT_BEFORE_"
                if is_owner_smoke_sma20
                else "TSMOM28_CLOSE_TO_FLAT_BEFORE_"
            )
            expected_closes = sum(
                str(item.get("reason", "")).startswith(close_prefix)
                for item in observations.get("submitted_intents", [])
            )
            triples_ok = triples_ok and len(reversal) // 3 == expected_closes
            checks.append(
                {
                    "name": "registered_separate_close_then_reverse",
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
                "name": (
                    "native_funding_exactly_once"
                    if isinstance(dataset, SyntheticQualificationDatasetRelease)
                    else "native_funding_output_integrity"
                ),
                "pass": funding_ok,
                "expected_settlements": (
                    len(expected_settlements)
                    if isinstance(dataset, SyntheticQualificationDatasetRelease)
                    else "DERIVED_BY_OFFICIAL_EXACT_BINDING"
                ),
                "actual_settlements": len(funding_rows),
            },
        )
        if not funding_ok:
            failures.append(FailureCode.FUNDING_DOUBLE_COUNT.value)
        if not isinstance(dataset, SyntheticQualificationDatasetRelease):
            source_events: list[dict[str, Any]] = []
            exact_detail: dict[str, Any] = {}
            funding_failure_codes = (FailureCode.FUNDING_AMBIGUOUS.value,)
            exact_funding_ok = False
            exact_mark_ok = False
            try:
                funding_source = _read_json(run_dir / "funding_source.json")
                source_events = [
                    item
                    for item in funding_source["events"]
                    if utc_datetime_to_ns(config.warmup_start)
                    <= int(item["calc_time_ns"])
                    < utc_datetime_to_ns(config.scoring_end_exclusive)
                ]
                independently_resolved = dataset.resolve_runtime_data(
                    repository_root / "data",
                )
                mark_source_events = [
                    {
                        "instrument_id": str(item.instrument_id),
                        "value": str(item.value),
                        "ts_event": int(item.ts_event),
                        "ts_init": int(item.ts_init),
                    }
                    for item in independently_resolved.data
                    if isinstance(item, MarkPriceUpdate)
                    and utc_datetime_to_ns(config.warmup_start) < int(item.ts_init)
                    <= utc_datetime_to_ns(config.scoring_end_exclusive)
                ]
                checkpoints = result["native_funding_checkpoints"]
                exact_funding_ok, funding_failure_codes, exact_detail = (
                    validate_official_funding_binding(
                        source_events=source_events,
                        mark_source_events=mark_source_events,
                        position_rows=positions,
                        checkpoints=checkpoints,
                        funding_rows=funding_rows,
                        dataset_contract=result["dataset_contract"],
                        instrument_id=config.instrument_id,
                    )
                )
                selected_counts = result["execution_data_window"]["selected_counts"]
                observed_mark_count = observations.get("mark_price_update_count")
                if type(observed_mark_count) is not int:
                    observed_mark_count = len(observations.get("mark_price_updates", []))
                exact_mark_ok = bool(
                    observed_mark_count == int(selected_counts["MarkPriceUpdate"])
                    and config.nautilus_engine_config.portfolio.use_mark_prices is True
                    and result.get("mark_fallback_accepted") is False
                )
            except Exception as exc:
                exact_detail = {
                    **exact_detail,
                    "source_event_count": len(source_events),
                    "detail": str(exc),
                }
            checks.append(
                {
                    "name": "official_funding_exact_binding",
                    "pass": exact_funding_ok,
                    **exact_detail,
                },
            )
            checks.append(
                {
                    "name": "official_mark_valuation_binding",
                    "pass": exact_mark_ok,
                    "expected_mark_callback_count": (
                        None
                        if not isinstance(result.get("execution_data_window"), dict)
                        else result["execution_data_window"].get("selected_counts", {}).get(
                            "MarkPriceUpdate",
                        )
                    ),
                    "mark_fallback_accepted": result.get("mark_fallback_accepted"),
                },
            )
            if not exact_funding_ok:
                for code in funding_failure_codes:
                    if code == FailureCode.MARK_ROLE_INVALID.value:
                        blocked.append(code)
                    else:
                        failures.append(code)
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
