"""Independent read-only reconciliation of native linear-Perpetual evidence.

NautilusTrader remains the execution and accounting authority.  This module
does not participate in a Run and never creates or mutates financial state.  It
replays the elementary linear-contract equations over persisted native events
so a self-consistent hash manifest cannot make an impossible result valid.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.native_positions import NativeCompletedPositionSequence


_FILL_FIELDS = {
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
}
_POSITION_FIELDS = {
    "row_type",
    "event_index",
    "ts_event",
    "instrument_id",
    "position_id",
    "side",
    "signed_qty",
    "quantity",
    "avg_px_open",
    "realized_pnl",
}
_ACCOUNT_FIELDS = {
    "event_index",
    "ts_event",
    "account_id",
    "account_type",
    "currency",
    "total",
    "locked",
    "free",
    "reported",
}
_FUNDING_FIELDS = {
    "adjustment_type",
    "instrument_id",
    "pnl_change",
    "quantity_change",
    "reason",
    "ts_event",
}
_NATIVE_CLOSED_SNAPSHOT_FIELDS = {
    "type",
    "events",
    "adjustments",
    "position_id",
    "instrument_id",
    "strategy_id",
    "trader_id",
    "account_id",
    "opening_order_id",
    "closing_order_id",
    "entry",
    "side",
    "base_currency",
    "quote_currency",
    "settlement_currency",
    "is_inverse",
    "multiplier",
    "price_precision",
    "size_precision",
    "quantity",
    "signed_qty",
    "peak_qty",
    "buy_qty",
    "sell_qty",
    "ts_init",
    "ts_opened",
    "ts_last",
    "ts_closed",
    "duration_ns",
    "avg_px_open",
    "avg_px_close",
    "realized_return",
    "realized_pnl",
    "commissions",
    "trade_ids",
    "venue_order_ids",
}

_PINNED_FIXED_PRECISION = 16


@dataclass(frozen=True)
class PerpetualReconciliationReport:
    passed: bool
    errors: tuple[str, ...]
    detail: dict[str, Any]


@dataclass(frozen=True)
class PerpetualValuationState:
    """Read-only linear-contract state at one causal valuation boundary."""

    timestamp_ns: int
    mark: Decimal
    signed_position: Decimal
    average_entry: Decimal
    gross_realized_price_pnl: Decimal
    commissions: Decimal
    funding: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_pnl: Decimal
    equity: Decimal


@dataclass(frozen=True)
class _AccountTransition:
    timestamp_ns: int
    delta: Decimal
    signed_position: Decimal
    average_entry: Decimal


@dataclass
class _Lifecycle:
    opened_ns: int
    opening_order_id: str
    entry_side: str
    average_open_price: Decimal
    peak_quantity: Decimal
    close_quantity: Decimal = Decimal(0)
    close_notional: Decimal = Decimal(0)
    gross_price_pnl: Decimal = Decimal(0)
    commissions: Decimal = Decimal(0)
    funding: Decimal = Decimal(0)
    funding_count: int = 0
    fill_rows: list[dict[str, str]] | None = None
    closed_ns: int | None = None
    closing_order_id: str | None = None

    @property
    def average_close_price(self) -> Decimal:
        return (
            Decimal(0)
            if self.close_quantity == 0
            else self.close_notional / self.close_quantity
        )

    def net_realized(self, quantum: Decimal) -> Decimal:
        return (self.gross_price_pnl - self.commissions + self.funding).quantize(
            quantum,
        )


def _money(value: Any) -> tuple[Decimal, str]:
    parts = str(value).split(" ", maxsplit=1)
    if len(parts) != 2:
        raise ValueError("money value has no currency")
    amount = Decimal(parts[0])
    if not amount.is_finite() or not parts[1]:
        raise ValueError("money value is invalid")
    return amount, parts[1]


def _pinned_money_from_f64(value: float, money_quantum: Decimal) -> Decimal:
    """Project a finite ``f64`` through the pinned Nautilus Money boundary.

    NautilusTrader 2.0.0rc2 ``Money::new_checked`` delegates to
    ``f64_to_fixed_i128``.  That function first performs the currency-scale
    multiplication in IEEE-754 binary64, then applies Rust ``f64::round``
    (nearest, with exact ties away from zero), and finally rescales the integer
    to the wheel's 16-decimal fixed representation.  Replaying that explicit
    boundary is necessary: Decimal half-even and unconditional Decimal
    half-up both disagree with real native output for legitimate inputs.

    The result remains verification-only.  It never enters the engine or
    replaces a native Money value.
    """

    if not isinstance(value, float) or not math.isfinite(value):
        raise ValueError("native Money input must be a finite f64")
    if not money_quantum.is_finite() or money_quantum <= 0:
        raise ValueError("money quantum must be finite and positive")
    exponent = money_quantum.as_tuple().exponent
    if not isinstance(exponent, int) or exponent > 0:
        raise ValueError("money quantum must be one decimal precision unit")
    precision = -exponent
    if (
        precision > _PINNED_FIXED_PRECISION
        or money_quantum != Decimal(1).scaleb(-precision)
    ):
        raise ValueError("money quantum is incompatible with the pinned fixed precision")

    # Python ``float`` and Rust ``f64`` use the same IEEE-754 binary64
    # operations here.  Use truncation plus an explicit fractional comparison
    # instead of Python round(), whose midpoint rule is ties-to-even.
    scaled = value * float(10**precision)
    if not math.isfinite(scaled):
        raise ValueError("native Money scale overflow")
    rounded = math.trunc(scaled)
    fraction = scaled - rounded
    if fraction >= 0.5:
        rounded += 1
    elif fraction <= -0.5:
        rounded -= 1
    fixed_raw = rounded * 10 ** (_PINNED_FIXED_PRECISION - precision)
    if not -(1 << 127) <= fixed_raw <= (1 << 127) - 1:
        raise ValueError("native Money fixed-point overflow")
    return Decimal(rounded).scaleb(-precision)


def _pinned_linear_pnl_money(
    *,
    signed_quantity: Decimal,
    average_entry: Decimal,
    close_price: Decimal,
    multiplier: Decimal,
    money_quantum: Decimal,
) -> Decimal:
    """Replay the pinned native linear-position ``f64 -> Money`` operation."""

    if not all(
        item.is_finite()
        for item in (signed_quantity, average_entry, close_price, multiplier)
    ):
        raise ValueError("linear PnL components must be finite")
    if average_entry < 0 or close_price < 0 or multiplier <= 0:
        raise ValueError("linear PnL components are outside the supported domain")
    if signed_quantity == 0:
        return _pinned_money_from_f64(0.0, money_quantum)

    quantity_f64 = float(abs(signed_quantity))
    multiplier_f64 = float(multiplier)
    entry_f64 = float(average_entry)
    close_f64 = float(close_price)
    points_f64 = (
        close_f64 - entry_f64
        if signed_quantity > 0
        else entry_f64 - close_f64
    )
    pnl_f64 = quantity_f64 * multiplier_f64 * points_f64
    return _pinned_money_from_f64(pnl_f64, money_quantum)


def _native_money_list_total(value: Any, currency: str) -> Decimal:
    if not isinstance(value, list):
        raise ValueError("native money list is invalid")
    result = Decimal(0)
    for item in value:
        if not isinstance(item, dict) or set(item) != {"amount", "currency"}:
            raise ValueError("native money projection is invalid")
        if item["currency"] != currency:
            raise ValueError("native commission currency set is invalid")
        amount = _decimal_text(item["amount"])
        if amount < 0:
            raise ValueError("native commission amount is negative")
        result += amount
    return result


def _decimal_text(value: Any) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite Decimal")
    return result


def _append(errors: list[str], code: str) -> None:
    if code not in errors:
        errors.append(code)


def validate_perpetual_native_account_projection(
    *,
    native_account_events: Any,
    account_rows: list[dict[str, str]],
    instrument_id: str,
    settlement_currency: str,
) -> tuple[bool, dict[str, Any]]:
    """Bind the human account projection to exact native ``AccountState`` events.

    The pinned MarginAccount exposes maintenance margin through both the
    native ``margins`` collection and ``AccountBalance.locked``.  Preserve and
    cross-check that native relationship instead of treating the CSV as an
    independent assertion.
    """

    errors: list[str] = []
    projected: list[dict[str, str]] = []
    account_id: str | None = None
    if not isinstance(native_account_events, list):
        return False, {
            "errors": ["PERP_NATIVE_ACCOUNT_EVENTS_INVALID"],
            "native_account_event_count": -1,
            "projected_account_row_count": len(account_rows),
        }
    for event_index, event in enumerate(native_account_events):
        try:
            if not isinstance(event, dict) or set(event) != {
                "account_id",
                "account_type",
                "balances",
                "base_currency",
                "info",
                "margins",
                "reported",
                "ts_event",
                "ts_init",
                "type",
            }:
                raise ValueError("native AccountState field set mismatch")
            if (
                event.get("type") != "AccountState"
                or event.get("account_type") != "MARGIN"
                or event.get("base_currency") != "None"
                or not isinstance(event.get("reported"), bool)
                or event.get("info") != {}
                or int(event["ts_event"]) < 0
                or int(event["ts_init"]) != int(event["ts_event"])
            ):
                raise ValueError("native AccountState role mismatch")
            current_account_id = str(event["account_id"])
            if not current_account_id or (
                account_id is not None and current_account_id != account_id
            ):
                raise ValueError("native AccountState account changed")
            account_id = current_account_id
            balances = event["balances"]
            margins = event["margins"]
            if not isinstance(balances, list) or len(balances) != 1:
                raise ValueError("native settlement balance cardinality mismatch")
            if not isinstance(margins, list):
                raise ValueError("native margin collection is invalid")
            balance = balances[0]
            if not isinstance(balance, dict) or set(balance) != {
                "currency",
                "free",
                "locked",
                "total",
                "type",
            }:
                raise ValueError("native AccountBalance field set mismatch")
            if (
                balance.get("type") != "AccountBalance"
                or balance.get("currency") != settlement_currency
            ):
                raise ValueError("native AccountBalance currency mismatch")
            total = _decimal_text(balance["total"])
            locked = _decimal_text(balance["locked"])
            free = _decimal_text(balance["free"])
            if total != locked + free or min(total, locked, free) < 0:
                raise ValueError("native AccountBalance components mismatch")
            maintenance_total = Decimal(0)
            for margin in margins:
                if not isinstance(margin, dict) or set(margin) != {
                    "currency",
                    "initial",
                    "instrument_id",
                    "maintenance",
                    "type",
                }:
                    raise ValueError("native MarginBalance field set mismatch")
                initial = _decimal_text(margin["initial"])
                maintenance = _decimal_text(margin["maintenance"])
                if (
                    margin.get("type") != "MarginBalance"
                    or margin.get("instrument_id") != instrument_id
                    or margin.get("currency") != settlement_currency
                    or initial < 0
                    or maintenance < 0
                ):
                    raise ValueError("native MarginBalance role mismatch")
                maintenance_total += maintenance
            if maintenance_total != locked:
                raise ValueError("native maintenance margin differs from locked balance")
            projected.append(
                {
                    "event_index": str(event_index),
                    "ts_event": str(event["ts_event"]),
                    "account_id": current_account_id,
                    "account_type": "MARGIN",
                    "currency": settlement_currency,
                    "total": str(balance["total"]),
                    "locked": str(balance["locked"]),
                    "free": str(balance["free"]),
                    "reported": str(event["reported"]),
                },
            )
        except Exception:
            _append(errors, "PERP_NATIVE_ACCOUNT_EVENTS_INVALID")
    if projected != account_rows:
        _append(errors, "PERP_NATIVE_ACCOUNT_PROJECTION_MISMATCH")
    return not errors, {
        "errors": errors,
        "native_account_event_count": len(native_account_events),
        "projected_account_row_count": len(account_rows),
    }


def _account_changes(
    rows: list[dict[str, str]],
    *,
    settlement_currency: str,
    initial_balance: Decimal,
    transitions: list[_AccountTransition],
    margin_maint: Decimal,
    multiplier: Decimal,
    money_quantum: Decimal,
    errors: list[str],
) -> tuple[dict[int, Decimal], Decimal, int]:
    grouped: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        try:
            grouped[int(row["event_index"])].append(row)
        except Exception:
            _append(errors, "PERP_ACCOUNT_EVENT_INDEX_INVALID")
    if sorted(grouped) != list(range(len(grouped))):
        _append(errors, "PERP_ACCOUNT_EVENT_SEQUENCE_GAP")
    if not grouped:
        _append(errors, "PERP_ACCOUNT_EVIDENCE_EMPTY")
        return {}, Decimal(0), 0

    states: list[tuple[int, int, Decimal, Decimal, Decimal, str]] = []
    account_id: str | None = None
    for index in sorted(grouped):
        group = grouped[index]
        if len(group) != 1:
            _append(errors, "PERP_ACCOUNT_CURRENCY_CARDINALITY_INVALID")
            continue
        row = group[0]
        try:
            if set(row) != _ACCOUNT_FIELDS:
                raise ValueError("account row field set mismatch")
            timestamp = int(row["ts_event"])
            total = _decimal_text(row["total"])
            locked = _decimal_text(row["locked"])
            free = _decimal_text(row["free"])
            reported = str(row["reported"])
        except Exception:
            _append(errors, "PERP_ACCOUNT_BALANCE_INVALID")
            continue
        if (
            row.get("account_type") != "MARGIN"
            or row.get("currency") != settlement_currency
            or reported not in {"True", "False"}
            or timestamp < 0
            or (index == 0 and reported != "True")
            or total != locked + free
            or locked < 0
            or free < 0
        ):
            _append(errors, "PERP_ACCOUNT_COMPONENT_MISMATCH")
        if account_id is None:
            account_id = row.get("account_id")
            if not account_id:
                _append(errors, "PERP_ACCOUNT_ID_CHANGED")
        elif row.get("account_id") != account_id:
            _append(errors, "PERP_ACCOUNT_ID_CHANGED")
        states.append((index, timestamp, total, locked, free, reported))

    if not states:
        return {}, Decimal(0), 0
    if states[0][2] != initial_balance:
        _append(errors, "PERP_INITIAL_ACCOUNT_BALANCE_MISMATCH")
    if states[0][3] != 0 or states[0][4] != initial_balance:
        _append(errors, "PERP_ACCOUNT_MARGIN_STATE_MISMATCH")
    if [item[1] for item in states] != sorted(item[1] for item in states):
        _append(errors, "PERP_ACCOUNT_TIMESTAMP_ORDER_INVALID")
    if transitions and states[0][1] > transitions[0].timestamp_ns:
        _append(errors, "PERP_ACCOUNT_TRANSITION_MISSING")

    changes: dict[int, Decimal] = defaultdict(Decimal)
    previous = states[0][2]
    signed_position = Decimal(0)
    average_entry = Decimal(0)
    transition_index = 0
    pending_report_mirror: tuple[int, Decimal, Decimal, Decimal] | None = None
    for _index, timestamp, total, locked, free, reported in states[1:]:
        state_core = (timestamp, total, locked, free)
        if pending_report_mirror is not None:
            if state_core == pending_report_mirror and reported == "False":
                pending_report_mirror = None
                expected_locked = (
                    abs(signed_position)
                    * average_entry
                    * multiplier
                    * margin_maint
                ).quantize(money_quantum)
                if locked != expected_locked or free != total - expected_locked:
                    _append(errors, "PERP_ACCOUNT_MARGIN_STATE_MISMATCH")
                continue
            _append(errors, "PERP_ACCOUNT_COMPONENT_MISMATCH")
            pending_report_mirror = None
        while (
            transition_index < len(transitions)
            and transitions[transition_index].timestamp_ns < timestamp
        ):
            # A financial transition occurred without a matching native
            # AccountState row.  Advance only to prevent one omission from
            # disguising every later state mismatch.
            missing = transitions[transition_index]
            _append(errors, "PERP_ACCOUNT_TRANSITION_MISSING")
            previous = (previous + missing.delta).quantize(money_quantum)
            signed_position = missing.signed_position
            average_entry = missing.average_entry
            transition_index += 1

        consumed = False
        if (
            transition_index < len(transitions)
            and transitions[transition_index].timestamp_ns == timestamp
        ):
            candidate = transitions[transition_index]
            candidate_total = (previous + candidate.delta).quantize(money_quantum)
            candidate_locked = (
                abs(candidate.signed_position)
                * candidate.average_entry
                * multiplier
                * margin_maint
            ).quantize(money_quantum)
            if total == candidate_total and locked == candidate_locked:
                if total != previous:
                    changes[timestamp] += total - previous
                previous = total
                signed_position = candidate.signed_position
                average_entry = candidate.average_entry
                transition_index += 1
                consumed = True
                if reported == "True":
                    pending_report_mirror = state_core

        expected_locked = (
            abs(signed_position)
            * average_entry
            * multiplier
            * margin_maint
        ).quantize(money_quantum)
        if total != previous:
            _append(errors, "PERP_ACCOUNT_DELTA_MISMATCH")
            previous = total
        elif not consumed:
            _append(errors, "PERP_ACCOUNT_COMPONENT_MISMATCH")
        if reported == "True" and not consumed:
            _append(errors, "PERP_ACCOUNT_COMPONENT_MISMATCH")
        if locked != expected_locked or free != total - expected_locked:
            _append(errors, "PERP_ACCOUNT_MARGIN_STATE_MISMATCH")

    if pending_report_mirror is not None:
        _append(errors, "PERP_ACCOUNT_COMPONENT_MISMATCH")
    if transition_index != len(transitions):
        _append(errors, "PERP_ACCOUNT_TRANSITION_MISSING")
    return dict(changes), states[-1][2], len(states)


def validate_perpetual_reconciliation(
    *,
    fills: list[dict[str, str]],
    account_rows: list[dict[str, str]],
    position_rows: list[dict[str, str]],
    funding_rows: list[dict[str, str]],
    native_completed_trades: dict[str, Any],
    native_closed_position_snapshots: list[dict[str, Any]],
    terminal_portfolio: dict[str, Any],
    terminal_mark: dict[str, Any],
    run_id: str,
    instrument_id: str,
    settlement_currency: str,
    initial_balance: Decimal,
    taker_fee: Decimal,
    quantity_increment: Decimal,
    margin_maint: Decimal,
    multiplier: Decimal,
    money_quantum: Decimal,
    scoring_end_exclusive_ns: int,
) -> PerpetualReconciliationReport:
    """Reconcile a linear ``multiplier=1`` NETTING account with ``Decimal``.

    Every account delta is derived from a native Fill or Funding adjustment.
    The terminal mark is supplied by a separately resolved DatasetRelease and
    must be the causal observation at the scoring boundary.
    """

    errors: list[str] = []
    if (
        not run_id
        or not instrument_id
        or not settlement_currency
        or not isinstance(native_closed_position_snapshots, list)
        or initial_balance <= 0
        or taker_fee < 0
        or quantity_increment <= 0
        or margin_maint <= 0
        or multiplier <= 0
        or money_quantum <= 0
        or scoring_end_exclusive_ns <= 0
    ):
        return PerpetualReconciliationReport(
            False,
            ("PERP_RECONCILIATION_INPUT_INVALID",),
            {"native_financial_state_mutated_by_validator": False},
        )

    expected_account_changes: dict[int, Decimal] = defaultdict(Decimal)
    expected_account_transitions: list[_AccountTransition] = []
    signed_position = Decimal(0)
    average_entry = Decimal(0)
    gross_realized = Decimal(0)
    total_commissions = Decimal(0)
    total_funding = Decimal(0)
    current: _Lifecycle | None = None
    completed: list[_Lifecycle] = []
    signed_after_fill: list[
        tuple[int, Decimal, Decimal, Decimal, str, str]
    ] = []
    fill_ids: set[str] = set()

    ordered_events: list[tuple[int, int, int, str, dict[str, str]]] = []
    for index, row in enumerate(funding_rows):
        try:
            ordered_events.append((int(row["ts_event"]), 0, index, "FUNDING", row))
        except Exception:
            _append(errors, "PERP_FUNDING_TIMESTAMP_INVALID")
    for index, row in enumerate(fills):
        try:
            if int(row.get("fill_index", -1)) != index:
                _append(errors, "PERP_FILL_INDEX_INVALID")
            ordered_events.append((int(row["ts_event"]), 1, index, "FILL", row))
        except Exception:
            _append(errors, "PERP_FILL_TIMESTAMP_INVALID")
    fill_timestamps = [item[0] for item in ordered_events if item[3] == "FILL"]
    funding_timestamps = [item[0] for item in ordered_events if item[3] == "FUNDING"]
    if fill_timestamps != sorted(fill_timestamps) or funding_timestamps != sorted(
        funding_timestamps,
    ):
        _append(errors, "PERP_FINANCIAL_EVENT_ORDER_INVALID")
    ordered_events.sort()

    for timestamp, _priority, index, kind, row in ordered_events:
        if kind == "FUNDING":
            try:
                if set(row) != _FUNDING_FIELDS:
                    raise ValueError("funding field set mismatch")
                amount, currency = _money(row["pnl_change"])
                if (
                    row.get("adjustment_type") != "FUNDING"
                    or row.get("instrument_id") != instrument_id
                    or currency != settlement_currency
                    or row.get("quantity_change") not in {"", None}
                    or not str(row.get("reason", "")).startswith("funding_settlement:")
                    or amount != amount.quantize(money_quantum)
                ):
                    raise ValueError("funding role mismatch")
                if signed_position == 0 or current is None:
                    _append(errors, "PERP_FUNDING_WITHOUT_ELIGIBLE_POSITION")
                else:
                    current.funding += amount
                    current.funding_count += 1
                total_funding += amount
                expected_account_changes[timestamp] += amount
                expected_account_transitions.append(
                    _AccountTransition(
                        timestamp_ns=timestamp,
                        delta=amount.quantize(money_quantum),
                        signed_position=signed_position,
                        average_entry=average_entry,
                    ),
                )
            except Exception:
                _append(errors, "PERP_FUNDING_ROW_INVALID")
            continue

        try:
            if set(row) != _FILL_FIELDS:
                raise ValueError("fill field set mismatch")
            event_id = str(row["event_id"])
            if not event_id or event_id in fill_ids:
                raise ValueError("duplicate fill")
            fill_ids.add(event_id)
            if (
                row.get("instrument_id") != instrument_id
                or row.get("order_type") != "MARKET"
                or row.get("liquidity_side") != "TAKER"
                or row.get("currency") != settlement_currency
                or int(row.get("ts_init", -1)) != timestamp
                or any(
                    not str(row.get(name, ""))
                    for name in (
                        "client_order_id",
                        "venue_order_id",
                        "trade_id",
                        "position_id",
                        "account_id",
                    )
                )
            ):
                raise ValueError("fill role mismatch")
            quantity = _decimal_text(row["last_qty"])
            price = _decimal_text(row["last_px"])
            commission, commission_currency = _money(row["commission"])
            if (
                quantity <= 0
                or price <= 0
                or commission < 0
                or quantity % quantity_increment != 0
                or commission_currency != settlement_currency
            ):
                raise ValueError("fill amount/currency mismatch")
            expected_commission = (quantity * price * taker_fee).quantize(money_quantum)
            if commission != expected_commission:
                _append(errors, "PERP_COMMISSION_AMOUNT_MISMATCH")
            direction = Decimal(1) if row.get("order_side") == "BUY" else Decimal(-1)
            if row.get("order_side") not in {"BUY", "SELL"}:
                raise ValueError("unknown order side")
            delta = direction * quantity
        except Exception:
            _append(errors, "PERP_FILL_ROW_INVALID")
            continue

        realized = Decimal(0)
        signed_before = signed_position
        average_before = average_entry
        if signed_position == 0:
            current = _Lifecycle(
                opened_ns=timestamp,
                opening_order_id=str(row["client_order_id"]),
                entry_side="BUY" if delta > 0 else "SELL",
                average_open_price=price,
                peak_quantity=quantity,
                commissions=commission,
                fill_rows=[row],
            )
            average_entry = price
            signed_position = delta
        elif signed_position * delta > 0:
            assert current is not None
            average_entry = (
                abs(signed_position) * average_entry + quantity * price
            ) / (abs(signed_position) + quantity)
            signed_position += delta
            current.average_open_price = average_entry
            current.peak_quantity = max(current.peak_quantity, abs(signed_position))
            current.commissions += commission
            assert current.fill_rows is not None
            current.fill_rows.append(row)
        else:
            assert current is not None
            assert current.fill_rows is not None
            current.fill_rows.append(row)
            closed_quantity = min(abs(signed_position), quantity)
            realized = _pinned_linear_pnl_money(
                signed_quantity=(
                    closed_quantity
                    if signed_position > 0
                    else -closed_quantity
                ),
                average_entry=average_entry,
                close_price=price,
                multiplier=multiplier,
                money_quantum=money_quantum,
            )
            gross_realized += realized
            current.gross_price_pnl += realized
            current.close_quantity += closed_quantity
            current.close_notional += closed_quantity * price
            # Cross-zero orders are outside the Official V1 strategy contract,
            # but the replay equations remain total and deterministic.  Split
            # commission by exact executed quantity across old/new lifecycles.
            old_commission = commission * closed_quantity / quantity
            current.commissions += old_commission
            next_position = signed_position + delta
            if signed_position * next_position < 0:
                _append(errors, "PERP_CROSS_ZERO_FILL_INVALID")
            if next_position == 0 or signed_position * next_position < 0:
                current.closed_ns = timestamp
                current.closing_order_id = str(row["client_order_id"])
                completed.append(current)
                current = None
            if next_position == 0:
                average_entry = Decimal(0)
            elif signed_position * next_position < 0:
                remaining = abs(next_position)
                current = _Lifecycle(
                    opened_ns=timestamp,
                    opening_order_id=str(row["client_order_id"]),
                    entry_side="BUY" if next_position > 0 else "SELL",
                    average_open_price=price,
                    peak_quantity=remaining,
                    commissions=commission - old_commission,
                    fill_rows=[row],
                )
                average_entry = price
            signed_position = next_position

        total_commissions += commission
        account_delta = (realized - commission).quantize(money_quantum)
        expected_account_changes[timestamp] += account_delta
        expected_account_transitions.append(
            _AccountTransition(
                timestamp_ns=timestamp,
                delta=account_delta,
                signed_position=signed_position,
                average_entry=average_entry,
            ),
        )
        native_position_realized = (
            current.net_realized(money_quantum)
            if current is not None
            else (
                completed[-1].net_realized(money_quantum)
                if completed
                else Decimal(0)
            )
        )
        expected_event_type = (
            "PositionOpened"
            if signed_before == 0 and signed_position != 0
            else "PositionClosed"
            if signed_before != 0 and signed_position == 0
            else "PositionChanged"
        )
        expected_side = (
            "LONG" if signed_position > 0 else "SHORT" if signed_position < 0 else "FLAT"
        )
        native_event_average = (
            average_before if signed_position == 0 else average_entry
        )
        signed_after_fill.append(
            (
                timestamp,
                signed_position,
                native_event_average,
                native_position_realized,
                expected_event_type,
                expected_side,
            ),
        )

    # Bind each native position event directly to the corresponding native Fill.
    event_positions = [
        row for row in position_rows if row.get("row_type") != "FINAL_NATIVE_POSITION"
    ]
    if len(event_positions) != len(signed_after_fill):
        _append(errors, "PERP_FILL_POSITION_CARDINALITY_MISMATCH")
    for index, (
        (
            timestamp,
            expected_signed,
            expected_average,
            expected_position_realized,
            expected_event_type,
            expected_side,
        ),
        row,
    ) in enumerate(
        zip(signed_after_fill, event_positions, strict=False),
    ):
        try:
            if (
                set(row) != _POSITION_FIELDS
                or int(row.get("event_index", -1)) != index
                or int(row["ts_event"]) != timestamp
                or row.get("instrument_id") != instrument_id
                or row.get("position_id") != fills[index].get("position_id")
                or row.get("row_type") != expected_event_type
                or row.get("side") != expected_side
                or _decimal_text(row["signed_qty"]) != expected_signed
                or _decimal_text(row["quantity"]) != abs(expected_signed)
            ):
                _append(errors, "PERP_FILL_POSITION_DELTA_MISMATCH")
            if not row.get("avg_px_open") or _decimal_text(
                row["avg_px_open"],
            ) != expected_average:
                _append(errors, "PERP_POSITION_AVERAGE_ENTRY_MISMATCH")
            if not row.get("realized_pnl"):
                _append(errors, "PERP_POSITION_REALIZED_PNL_MISMATCH")
            else:
                amount, currency = _money(row["realized_pnl"])
                if (
                    amount != expected_position_realized
                    or currency != settlement_currency
                ):
                    _append(errors, "PERP_POSITION_REALIZED_PNL_MISMATCH")
        except Exception:
            _append(errors, "PERP_POSITION_EVENT_INVALID")

    final_positions = [
        row for row in position_rows if row.get("row_type") == "FINAL_NATIVE_POSITION"
    ]
    if not fills and not final_positions:
        final_position_realized = Decimal(0)
        if signed_position != 0 or event_positions:
            _append(errors, "PERP_NO_FILL_POSITION_MISMATCH")
    elif len(final_positions) != 1:
        _append(errors, "PERP_FINAL_POSITION_CARDINALITY_INVALID")
        final_position_realized = Decimal(0)
    else:
        final = final_positions[0]
        try:
            final_realized_amount, final_realized_currency = _money(final["realized_pnl"])
            final_position_realized = final_realized_amount
            expected_side = "LONG" if signed_position > 0 else "SHORT" if signed_position < 0 else "FLAT"
            expected_final_average = (
                average_entry
                if signed_position != 0
                else completed[-1].average_open_price
                if completed
                else Decimal(0)
            )
            expected_final_timestamp = max(
                [int(row["ts_event"]) for row in fills]
                + [int(row["ts_event"]) for row in funding_rows],
            )
            if (
                set(final) != _POSITION_FIELDS
                or int(final.get("event_index", -1)) != 0
                or int(final.get("ts_event", -1)) != expected_final_timestamp
                or final.get("instrument_id") != instrument_id
                or not fills
                or final.get("position_id") != fills[-1].get("position_id")
                or final.get("side") != expected_side
                or _decimal_text(final["signed_qty"]) != signed_position
                or _decimal_text(final["quantity"]) != abs(signed_position)
                or _decimal_text(final["avg_px_open"]) != expected_final_average
                or final_realized_currency != settlement_currency
            ):
                _append(errors, "PERP_FINAL_POSITION_MISMATCH")
        except Exception:
            final_position_realized = Decimal(0)
            _append(errors, "PERP_FINAL_POSITION_INVALID")

    expected_current_realized = (
        current.net_realized(money_quantum)
        if current is not None
        else completed[-1].net_realized(money_quantum)
        if completed
        else Decimal(0)
    )
    if final_positions and final_position_realized != expected_current_realized:
        _append(errors, "PERP_FINAL_POSITION_REALIZED_PNL_MISMATCH")

    # Parse the complete typed v2 schema first.  This binds sequence/source/Run
    # identities and rejects unknown, missing, or self-inconsistent fields
    # before comparing the native values with the independent Fill replay.
    try:
        native_sequence = NativeCompletedPositionSequence.from_json_bytes(
            canonical_json_bytes(native_completed_trades),
        )
    except Exception:
        native_sequence = None
        _append(errors, "PERP_NATIVE_COMPLETED_SEQUENCE_INVALID")
    if native_sequence is not None:
        try:
            ordered_native_snapshots = sorted(
                native_closed_position_snapshots,
                key=lambda item: (
                    int(item["ts_closed"]),
                    int(item["ts_opened"]),
                    str(item["position_id"]),
                ),
            )
        except Exception:
            ordered_native_snapshots = []
            _append(errors, "PERP_NATIVE_COMPLETED_SNAPSHOT_INVALID")
        terminal_open_count = 1 if signed_position != 0 else 0
        terminal_closed_count = 1 if fills and signed_position == 0 else 0
        if (
            native_sequence.source_run_id != run_id
            or native_sequence.instrument_id != instrument_id
            or native_sequence.settlement_currency != settlement_currency
            or native_sequence.completed_trade_count != len(completed)
            or native_sequence.terminal_open_position_count != terminal_open_count
            or native_sequence.terminal_closed_position_count != terminal_closed_count
            or not native_sequence.unambiguous_net_after_cost
            or len(native_sequence.units) != len(completed)
            or len(native_sequence.net_outcomes) != len(completed)
            or len(native_sequence.realized_returns) != len(completed)
            or len(ordered_native_snapshots) != len(completed)
        ):
            _append(errors, "PERP_NATIVE_COMPLETED_SEQUENCE_MISMATCH")
        fill_position_by_order = {
            str(row["client_order_id"]): str(row["position_id"])
            for row in fills
        }
        for index, (expected, unit, native_snapshot) in enumerate(
            zip(
                completed,
                native_sequence.units,
                ordered_native_snapshots,
                strict=False,
            ),
        ):
            try:
                if set(native_snapshot) != _NATIVE_CLOSED_SNAPSHOT_FIELDS:
                    raise ValueError("native closed snapshot field set mismatch")
                if expected.fill_rows is None:
                    raise ValueError("completed lifecycle has no Fill evidence")
                snapshot_events = native_snapshot.get("events")
                snapshot_adjustments = native_snapshot.get("adjustments")
                if (
                    not isinstance(snapshot_events, list)
                    or not isinstance(snapshot_adjustments, list)
                    or len(snapshot_events) != len(expected.fill_rows)
                    or len(snapshot_adjustments) != expected.funding_count
                ):
                    raise ValueError("native snapshot event cardinality mismatch")
                events_by_fill_id = {
                    str(item.get("event_id", "")): item
                    for item in snapshot_events
                    if isinstance(item, dict)
                }
                if len(events_by_fill_id) != len(snapshot_events):
                    raise ValueError("native snapshot Fill identity mismatch")
                for expected_fill in expected.fill_rows:
                    native_fill = events_by_fill_id.get(expected_fill["event_id"])
                    if native_fill is None or native_fill.get("type") != "OrderFilled":
                        raise ValueError("native snapshot Fill is absent")
                    for field in _FILL_FIELDS - {"fill_index"}:
                        if str(native_fill.get(field)) != expected_fill[field]:
                            raise ValueError(
                                f"native snapshot Fill projection mismatch: {field}",
                            )
                if any(
                    not isinstance(item, dict)
                    or item.get("adjustment_type") != "FUNDING"
                    for item in snapshot_adjustments
                ):
                    raise ValueError("native snapshot adjustment role mismatch")
                expected_buy = sum(
                    (
                        Decimal(item["last_qty"])
                        for item in expected.fill_rows
                        if item["order_side"] == "BUY"
                    ),
                    Decimal(0),
                )
                expected_sell = sum(
                    (
                        Decimal(item["last_qty"])
                        for item in expected.fill_rows
                        if item["order_side"] == "SELL"
                    ),
                    Decimal(0),
                )
                expected_account_ids = {
                    item["account_id"] for item in expected.fill_rows
                }
                raw_realized, raw_realized_currency = _money(
                    native_snapshot.get("realized_pnl"),
                )
                raw_commissions = [
                    _money(item) for item in native_snapshot.get("commissions", [])
                ]
                raw_commission_total = sum(
                    (item[0] for item in raw_commissions),
                    Decimal(0),
                )
                expected_net = expected.net_realized(money_quantum)
                expected_return = expected_net / (
                    expected.average_open_price * expected.peak_quantity * multiplier
                )
                if (
                    len(expected_account_ids) != 1
                    or native_snapshot.get("account_id") not in expected_account_ids
                    or not str(native_snapshot.get("strategy_id", ""))
                    or not str(native_snapshot.get("trader_id", ""))
                    or native_snapshot.get("settlement_currency") != settlement_currency
                    or native_snapshot.get("quote_currency") != settlement_currency
                    or native_snapshot.get("is_inverse") is not False
                    or _decimal_text(native_snapshot.get("multiplier")) != multiplier
                    or _decimal_text(native_snapshot.get("quantity")) != 0
                    or _decimal_text(native_snapshot.get("signed_qty")) != 0
                    or _decimal_text(native_snapshot.get("buy_qty")) != expected_buy
                    or _decimal_text(native_snapshot.get("sell_qty")) != expected_sell
                    or native_snapshot.get("entry") != expected.entry_side
                    or _decimal_text(native_snapshot.get("avg_px_open"))
                    != expected.average_open_price
                    or _decimal_text(native_snapshot.get("avg_px_close"))
                    != expected.average_close_price
                    or _decimal_text(native_snapshot.get("peak_qty"))
                    != expected.peak_quantity
                    or raw_realized != expected_net
                    or raw_realized_currency != settlement_currency
                    or not raw_commissions
                    or any(item[1] != settlement_currency for item in raw_commissions)
                    or raw_commission_total.quantize(money_quantum)
                    != expected.commissions.quantize(money_quantum)
                    or _decimal_text(native_snapshot.get("realized_return")).quantize(
                        Decimal("0.0000000000000001"),
                    )
                    != expected_return.quantize(Decimal("0.0000000000000001"))
                    or int(native_snapshot.get("ts_init", -1)) != expected.opened_ns
                    or int(native_snapshot.get("ts_last", -1)) != expected.closed_ns
                    or int(native_snapshot.get("duration_ns", -1))
                    != expected.closed_ns - expected.opened_ns
                    or set(native_snapshot.get("trade_ids", []))
                    != {item["trade_id"] for item in expected.fill_rows}
                    or set(native_snapshot.get("venue_order_ids", []))
                    != {item["venue_order_id"] for item in expected.fill_rows}
                ):
                    raise ValueError("native closed snapshot state mismatch")
                if any(
                    commission.currency != settlement_currency
                    for commission in unit.commissions
                ):
                    raise ValueError("additional native commission currency")
                native_commission = sum(
                    (commission.amount for commission in unit.commissions),
                    Decimal(0),
                )
                expected_net = expected.net_realized(money_quantum)
                expected_parent = fill_position_by_order[expected.opening_order_id]
                if (
                    unit.sequence_index != index
                    or unit.source_kind != "DIRECT_POSITION_CLOSED_SNAPSHOT"
                    or unit.source_run_id != run_id
                    or unit.parent_position_id != expected_parent
                    or unit.native_payload_sha256
                    != canonical_sha256(native_snapshot)
                    or native_snapshot.get("type") != "Position"
                    or native_snapshot.get("side") != "FLAT"
                    or native_snapshot.get("instrument_id") != instrument_id
                    or native_snapshot.get("position_id") != unit.native_position_id
                    or int(native_snapshot.get("ts_opened", -1)) != unit.opened_ns
                    or int(native_snapshot.get("ts_closed", -1)) != unit.closed_ns
                    or native_snapshot.get("opening_order_id") != unit.opening_order_id
                    or native_snapshot.get("closing_order_id") != unit.closing_order_id
                    or unit.instrument_id != instrument_id
                    or unit.opened_ns != expected.opened_ns
                    or unit.closed_ns != expected.closed_ns
                    or unit.opening_order_id != expected.opening_order_id
                    or unit.closing_order_id != expected.closing_order_id
                    or unit.entry_side != expected.entry_side
                    or unit.average_open_price.quantize(money_quantum)
                    != expected.average_open_price.quantize(money_quantum)
                    or unit.average_close_price.quantize(money_quantum)
                    != expected.average_close_price.quantize(money_quantum)
                    or unit.peak_quantity != expected.peak_quantity
                    or native_commission.quantize(money_quantum)
                    != expected.commissions.quantize(money_quantum)
                    or unit.funding_adjustment_count != expected.funding_count
                    or unit.realized_pnl != expected_net
                    or unit.realized_pnl_currency != settlement_currency
                    or native_sequence.net_outcomes[index] != expected_net
                    or native_sequence.realized_returns[index] != unit.realized_return
                ):
                    _append(errors, "PERP_NATIVE_COMPLETED_SEQUENCE_MISMATCH")
            except Exception:
                _append(errors, "PERP_NATIVE_COMPLETED_UNIT_INVALID")

    fill_account_ids = {row.get("account_id") for row in fills}
    evidence_account_ids = {row.get("account_id") for row in account_rows}
    if fills and (
        len(fill_account_ids) != 1
        or len(evidence_account_ids) != 1
        or fill_account_ids != evidence_account_ids
    ):
        _append(errors, "PERP_ACCOUNT_ID_CHANGED")

    actual_changes, ending_account, account_state_count = _account_changes(
        account_rows,
        settlement_currency=settlement_currency,
        initial_balance=initial_balance,
        transitions=expected_account_transitions,
        margin_maint=margin_maint,
        multiplier=multiplier,
        money_quantum=money_quantum,
        errors=errors,
    )
    expected_changes = {
        timestamp: delta.quantize(money_quantum)
        for timestamp, delta in expected_account_changes.items()
        if delta != 0
    }
    if actual_changes != expected_changes:
        _append(errors, "PERP_ACCOUNT_DELTA_MISMATCH")

    expected_realized = ending_account - initial_balance
    replay_realized = (
        gross_realized - total_commissions + total_funding
    ).quantize(money_quantum)
    if expected_realized != replay_realized:
        _append(errors, "PERP_REALIZED_PNL_MISMATCH")

    try:
        mark_value = _decimal_text(terminal_mark["value"])
        mark_ts = int(terminal_mark["ts_event"])
        if (
            terminal_mark.get("instrument_id") != instrument_id
            or int(terminal_mark.get("ts_init", -1)) != mark_ts
            or mark_ts != scoring_end_exclusive_ns
            or mark_value <= 0
        ):
            raise ValueError("terminal mark is not the causal boundary observation")
        expected_unrealized = _pinned_linear_pnl_money(
            signed_quantity=signed_position,
            average_entry=average_entry,
            close_price=mark_value,
            multiplier=multiplier,
            money_quantum=money_quantum,
        )
    except Exception:
        mark_value = Decimal(0)
        mark_ts = -1
        expected_unrealized = Decimal(0)
        _append(errors, "PERP_TERMINAL_MARK_INVALID")

    try:
        reported_realized, realized_currency = _money(terminal_portfolio["realized_pnl"])
        reported_unrealized, unrealized_currency = _money(terminal_portfolio["unrealized_pnl"])
        reported_total, total_currency = _money(terminal_portfolio["total_pnl"])
        equity = terminal_portfolio["equity"]
        if not isinstance(equity, dict) or set(equity) != {settlement_currency}:
            raise ValueError("terminal equity currency set mismatch")
        equity_item = equity[settlement_currency]
        reported_equity = _decimal_text(equity_item["amount"])
        if equity_item.get("currency") != settlement_currency:
            raise ValueError("terminal equity currency mismatch")
        if {realized_currency, unrealized_currency, total_currency} != {settlement_currency}:
            raise ValueError("terminal PnL currency mismatch")
        if reported_realized != expected_realized:
            _append(errors, "PERP_REPORTED_REALIZED_PNL_MISMATCH")
        if reported_unrealized != expected_unrealized:
            _append(errors, "PERP_REPORTED_UNREALIZED_PNL_MISMATCH")
        if reported_total != reported_realized + reported_unrealized:
            _append(errors, "PERP_REPORTED_TOTAL_PNL_MISMATCH")
        if reported_equity != initial_balance + reported_total or reported_equity != ending_account + reported_unrealized:
            _append(errors, "PERP_ENDING_EQUITY_MISMATCH")
    except Exception:
        reported_equity = Decimal(0)
        _append(errors, "PERP_TERMINAL_PORTFOLIO_INVALID")

    unique_errors = tuple(errors)
    return PerpetualReconciliationReport(
        not unique_errors,
        unique_errors,
        {
            "fill_count": len(fills),
            "position_event_count": len(event_positions),
            "completed_lifecycle_count": len(completed),
            "funding_settlement_count": len(funding_rows),
            "account_state_count": account_state_count,
            "reconciled_account_delta_count": len(expected_changes),
            "terminal_signed_position": str(signed_position),
            "terminal_average_entry": str(average_entry),
            "gross_realized_price_pnl": str(gross_realized.quantize(money_quantum)),
            "commissions": str(total_commissions.quantize(money_quantum)),
            "funding": str(total_funding.quantize(money_quantum)),
            "realized_pnl": str(expected_realized),
            "terminal_mark": str(mark_value),
            "terminal_mark_ns": mark_ts,
            "unrealized_pnl": str(expected_unrealized),
            "ending_equity": str(reported_equity),
            "settlement_currency": settlement_currency,
            "native_financial_state_mutated_by_validator": False,
        },
    )


def replay_perpetual_valuation_states(
    *,
    fills: list[dict[str, str]],
    funding_rows: list[dict[str, str]],
    valuation_marks: list[dict[str, Any]],
    instrument_id: str,
    settlement_currency: str,
    initial_balance: Decimal,
    taker_fee: Decimal,
    quantity_increment: Decimal,
    multiplier: Decimal,
    money_quantum: Decimal,
) -> tuple[PerpetualValuationState, ...]:
    """Replay immutable native effects at multiple causal mark boundaries.

    This API is intentionally read-only and has no dependency on a Strategy,
    engine, account, or report.  It exists so diagnostics can reject a forged
    native portfolio snapshot at *each* daily boundary, rather than trusting
    only a reconciled terminal value.
    """

    if (
        not instrument_id
        or not settlement_currency
        or initial_balance <= 0
        or taker_fee < 0
        or quantity_increment <= 0
        or multiplier <= 0
        or money_quantum <= 0
        or not valuation_marks
    ):
        raise ValueError("invalid Perpetual valuation replay input")

    financial_events: list[tuple[int, int, int, str, dict[str, str]]] = []
    for index, row in enumerate(funding_rows):
        if set(row) != _FUNDING_FIELDS:
            raise ValueError("funding field set mismatch")
        financial_events.append((int(row["ts_event"]), 0, index, "FUNDING", row))
    for index, row in enumerate(fills):
        if set(row) != _FILL_FIELDS or int(row["fill_index"]) != index:
            raise ValueError("fill sequence/field set mismatch")
        financial_events.append((int(row["ts_event"]), 1, index, "FILL", row))
    financial_events.sort()

    parsed_marks: list[tuple[int, Decimal]] = []
    for row in valuation_marks:
        timestamp = int(row.get("timestamp_ns", row.get("ts_event", -1)))
        if (
            row.get("instrument_id") != instrument_id
            or int(row.get("ts_init", timestamp)) != timestamp
        ):
            raise ValueError("valuation mark identity/timestamp mismatch")
        mark = _decimal_text(row["value"])
        if timestamp < 0 or mark <= 0:
            raise ValueError("invalid valuation mark")
        parsed_marks.append((timestamp, mark))
    if parsed_marks != sorted(parsed_marks) or len({item[0] for item in parsed_marks}) != len(
        parsed_marks,
    ):
        raise ValueError("valuation marks must be strictly timestamp ordered")

    signed_position = Decimal(0)
    average_entry = Decimal(0)
    gross_realized = Decimal(0)
    commissions = Decimal(0)
    funding = Decimal(0)
    event_index = 0
    states: list[PerpetualValuationState] = []

    for timestamp, mark in parsed_marks:
        while (
            event_index < len(financial_events)
            and financial_events[event_index][0] <= timestamp
        ):
            event_timestamp, _priority, _source_index, kind, row = financial_events[
                event_index
            ]
            event_index += 1
            if kind == "FUNDING":
                amount, currency = _money(row["pnl_change"])
                if (
                    row.get("adjustment_type") != "FUNDING"
                    or row.get("instrument_id") != instrument_id
                    or currency != settlement_currency
                    or signed_position == 0
                    or amount != amount.quantize(money_quantum)
                ):
                    raise ValueError("invalid funding event in valuation replay")
                funding += amount
                continue

            if (
                row.get("instrument_id") != instrument_id
                or row.get("order_type") != "MARKET"
                or row.get("liquidity_side") != "TAKER"
                or row.get("currency") != settlement_currency
                or int(row.get("ts_init", -1)) != event_timestamp
            ):
                raise ValueError("invalid Fill role in valuation replay")
            quantity = _decimal_text(row["last_qty"])
            price = _decimal_text(row["last_px"])
            commission, commission_currency = _money(row["commission"])
            if (
                quantity <= 0
                or price <= 0
                or quantity % quantity_increment != 0
                or commission_currency != settlement_currency
                or commission
                != (quantity * price * taker_fee).quantize(money_quantum)
            ):
                raise ValueError("invalid Fill amount in valuation replay")
            direction = Decimal(1) if row.get("order_side") == "BUY" else Decimal(-1)
            if row.get("order_side") not in {"BUY", "SELL"}:
                raise ValueError("invalid Fill side in valuation replay")
            delta = direction * quantity
            if signed_position == 0:
                signed_position = delta
                average_entry = price
            elif signed_position * delta > 0:
                average_entry = (
                    abs(signed_position) * average_entry + quantity * price
                ) / (abs(signed_position) + quantity)
                signed_position += delta
            else:
                next_position = signed_position + delta
                if signed_position * next_position < 0:
                    raise ValueError("cross-zero Fill in valuation replay")
                closed_quantity = min(abs(signed_position), quantity)
                gross_realized += _pinned_linear_pnl_money(
                    signed_quantity=(
                        closed_quantity
                        if signed_position > 0
                        else -closed_quantity
                    ),
                    average_entry=average_entry,
                    close_price=price,
                    multiplier=multiplier,
                    money_quantum=money_quantum,
                )
                signed_position = next_position
                if signed_position == 0:
                    average_entry = Decimal(0)
            commissions += commission

        realized = (gross_realized - commissions + funding).quantize(money_quantum)
        unrealized = _pinned_linear_pnl_money(
            signed_quantity=signed_position,
            average_entry=average_entry,
            close_price=mark,
            multiplier=multiplier,
            money_quantum=money_quantum,
        )
        total = realized + unrealized
        states.append(
            PerpetualValuationState(
                timestamp_ns=timestamp,
                mark=mark,
                signed_position=signed_position,
                average_entry=average_entry,
                gross_realized_price_pnl=gross_realized.quantize(money_quantum),
                commissions=commissions.quantize(money_quantum),
                funding=funding.quantize(money_quantum),
                realized_pnl=realized,
                unrealized_pnl=unrealized,
                total_pnl=total,
                equity=initial_balance + total,
            ),
        )

    return tuple(states)


__all__ = [
    "PerpetualReconciliationReport",
    "PerpetualValuationState",
    "replay_perpetual_valuation_states",
    "validate_perpetual_reconciliation",
]
