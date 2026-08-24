"""Pinned-Nautilus native completed-position evidence.

This module reads the public ``Cache`` position APIs only.  It never pairs
fills, calculates position PnL, or reconstructs a ledger.  A completed unit is
either a cache-owned NETTING snapshot created on reopen or a terminal native
closed position which has not subsequently reopened.
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from typing import Any

from crypto_lab.config import StrictModel
from crypto_lab.config import _freeze_field
from crypto_lab.config import _require_nonempty
from crypto_lab.config import _require_sha256
from crypto_lab.hashing import canonical_sha256


NATIVE_COMPLETED_SEQUENCE_SOURCE = (
    "NAUTILUS_2_0_0RC2_CACHE_POSITION_SNAPSHOTS_AND_CLOSED_POSITIONS"
)


class NativePositionSequenceError(ValueError):
    """Fail-closed native completed-position qualification error."""

    code = "NATIVE_POSITION_SEQUENCE_AMBIGUOUS"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.code}: {message}")


class NativeCommission(StrictModel):
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not self.amount.is_finite():
            raise NativePositionSequenceError("native commission must be finite")
        _require_nonempty(self.currency, "native_commission.currency")


class NativeCompletedPositionUnit(StrictModel):
    sequence_index: int
    source_kind: str
    source_run_id: str
    native_position_id: str
    parent_position_id: str
    native_payload_sha256: str
    instrument_id: str
    entry_side: str
    opened_ns: int
    closed_ns: int
    opening_order_id: str
    closing_order_id: str
    average_open_price: Decimal
    average_close_price: Decimal
    peak_quantity: Decimal
    realized_pnl: Decimal
    realized_pnl_currency: str
    realized_return: Decimal
    commissions: tuple[NativeCommission, ...]
    duration_ns: int
    funding_adjustment_count: int
    native_net_after_cost_unambiguous: bool

    def __post_init__(self) -> None:
        if self.sequence_index < 0:
            raise NativePositionSequenceError("sequence index cannot be negative")
        if self.source_kind not in {"CACHE_POSITION_SNAPSHOT", "CACHE_CLOSED_POSITION"}:
            raise NativePositionSequenceError("unknown native completed-position source")
        for name in (
            "source_run_id",
            "native_position_id",
            "parent_position_id",
            "instrument_id",
            "entry_side",
            "opening_order_id",
            "closing_order_id",
            "realized_pnl_currency",
        ):
            _require_nonempty(getattr(self, name), f"native_completed_unit.{name}")
        _require_sha256(self.native_payload_sha256, "native_completed_unit.native_payload_sha256")
        if self.entry_side not in {"BUY", "SELL"}:
            raise NativePositionSequenceError("completed position entry side must be BUY or SELL")
        if self.opened_ns < 0 or self.closed_ns <= self.opened_ns:
            raise NativePositionSequenceError("completed position timestamps are invalid")
        if self.duration_ns != self.closed_ns - self.opened_ns:
            raise NativePositionSequenceError("native duration disagrees with close minus open")
        if self.peak_quantity <= 0:
            raise NativePositionSequenceError("completed position peak quantity must be positive")
        for name in (
            "average_open_price",
            "average_close_price",
            "realized_pnl",
            "realized_return",
        ):
            if not getattr(self, name).is_finite():
                raise NativePositionSequenceError(f"{name} must be finite")
        if self.average_open_price <= 0 or self.average_close_price <= 0:
            raise NativePositionSequenceError("native average prices must be positive")
        if self.funding_adjustment_count < 0:
            raise NativePositionSequenceError("funding adjustment count cannot be negative")

    def semantic_payload(self) -> dict[str, Any]:
        """Return deterministic material fields, excluding runtime-generated IDs."""

        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name
            not in {
                "source_run_id",
                "native_position_id",
                "parent_position_id",
                "native_payload_sha256",
                "opening_order_id",
                "closing_order_id",
            }
        }


class NativeCompletedPositionSequence(StrictModel):
    schema: str
    schema_version: int
    sequence_id: str
    semantic_sequence_sha256: str
    source: str
    source_run_id: str
    instrument_id: str
    settlement_currency: str
    status: str
    completed_trade_count: int
    terminal_open_position_count: int
    terminal_closed_position_count: int
    units: tuple[NativeCompletedPositionUnit, ...]
    net_outcomes: tuple[Decimal, ...]
    realized_returns: tuple[Decimal, ...]
    unambiguous_net_after_cost: bool
    project_trade_pairing_used: bool

    def __post_init__(self) -> None:
        if self.schema != "nautilus-native-completed-trades-v2" or self.schema_version != 2:
            raise NativePositionSequenceError("unknown native completed-position schema")
        _require_sha256(self.sequence_id, "native_completed_sequence.sequence_id")
        _require_sha256(
            self.semantic_sequence_sha256,
            "native_completed_sequence.semantic_sequence_sha256",
        )
        if self.source != NATIVE_COMPLETED_SEQUENCE_SOURCE:
            raise NativePositionSequenceError("completed sequence is not the qualified native source")
        for name in ("source_run_id", "instrument_id", "settlement_currency"):
            _require_nonempty(getattr(self, name), f"native_completed_sequence.{name}")
        if self.status != "AVAILABLE":
            raise NativePositionSequenceError("typed native completed sequence must be AVAILABLE")
        if self.completed_trade_count != len(self.units):
            raise NativePositionSequenceError("completed count disagrees with native units")
        if self.terminal_open_position_count < 0 or self.terminal_closed_position_count < 0:
            raise NativePositionSequenceError("terminal position counts cannot be negative")
        if self.project_trade_pairing_used:
            raise NativePositionSequenceError("project Fill pairing is forbidden")
        if tuple(unit.sequence_index for unit in self.units) != tuple(range(len(self.units))):
            raise NativePositionSequenceError("native completed-unit sequence is not contiguous")
        if any(unit.instrument_id != self.instrument_id for unit in self.units):
            raise NativePositionSequenceError("native completed-unit instrument mismatch")
        if any(unit.source_run_id != self.source_run_id for unit in self.units):
            raise NativePositionSequenceError("native completed-unit Run mismatch")
        if any(unit.realized_pnl_currency != self.settlement_currency for unit in self.units):
            raise NativePositionSequenceError("native completed-unit settlement currency mismatch")
        if self.unambiguous_net_after_cost:
            if len(self.net_outcomes) != len(self.units):
                raise NativePositionSequenceError("native net outcome count is incomplete")
            if any(not unit.native_net_after_cost_unambiguous for unit in self.units):
                raise NativePositionSequenceError("sequence claims net outcomes despite ambiguous unit")
        elif self.net_outcomes:
            raise NativePositionSequenceError("ambiguous native sequence cannot publish net outcomes")
        if len(self.realized_returns) != len(self.units):
            raise NativePositionSequenceError("native realized-return count is incomplete")
        if any(not value.is_finite() for value in (*self.net_outcomes, *self.realized_returns)):
            raise NativePositionSequenceError("native completed-position value is non-finite")
        expected_semantic = canonical_sha256(
            tuple(unit.semantic_payload() for unit in self.units),
        )
        if expected_semantic != self.semantic_sequence_sha256:
            raise NativePositionSequenceError("native semantic sequence identity mismatch")
        if canonical_sha256(self.material_payload()) != self.sequence_id:
            raise NativePositionSequenceError("native completed sequence content identity mismatch")
        _freeze_field(self, "units")
        _freeze_field(self, "net_outcomes")
        _freeze_field(self, "realized_returns")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "sequence_id"
        }

    @classmethod
    def create(cls, **values: Any) -> NativeCompletedPositionSequence:
        material = {
            "schema": "nautilus-native-completed-trades-v2",
            "schema_version": 2,
            **values,
        }
        return cls(sequence_id=canonical_sha256(material), **material)


def _decimal_from_native(value: Any, field_name: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise NativePositionSequenceError(f"{field_name} is not a native decimal value") from exc
    if not result.is_finite():
        raise NativePositionSequenceError(f"{field_name} is not finite")
    return result


def _money_amount(value: Any, field_name: str) -> tuple[Decimal, str]:
    if value is None:
        raise NativePositionSequenceError(f"{field_name} is absent")
    try:
        amount = Decimal(str(value.as_decimal()))
        currency = str(value.currency)
    except Exception as exc:
        raise NativePositionSequenceError(f"{field_name} is not native Money") from exc
    if not amount.is_finite():
        raise NativePositionSequenceError(f"{field_name} is non-finite")
    _require_nonempty(currency, field_name + ".currency")
    return amount, currency


def _unit_from_position(
    position: Any,
    *,
    sequence_index: int,
    source_kind: str,
    source_run_id: str,
    parent_position_id: str,
    expected_instrument_id: str,
) -> NativeCompletedPositionUnit:
    # Import at the extraction boundary so the evidence schema remains usable by
    # M4 without importing a backtest engine or a private Nautilus module.
    from nautilus_trader.model import Position

    if not isinstance(position, Position):
        raise NativePositionSequenceError("forged non-Nautilus completed position")
    if not position.is_closed or position.is_open:
        raise NativePositionSequenceError("native completed unit is not closed-to-flat")
    if str(position.instrument_id) != expected_instrument_id:
        raise NativePositionSequenceError("native completed position instrument mismatch")
    if position.ts_closed is None:
        raise NativePositionSequenceError("native completed position has no close timestamp")
    if position.closing_order_id is None:
        raise NativePositionSequenceError("native completed position has no closing order")
    if position.avg_px_close is None:
        raise NativePositionSequenceError("native completed position has no average close price")

    realized_pnl, realized_currency = _money_amount(
        position.realized_pnl,
        "position.realized_pnl",
    )
    commissions: list[NativeCommission] = []
    settlement_only = True
    for commission in position.commissions():
        amount, currency = _money_amount(commission, "position.commission")
        commissions.append(NativeCommission(amount=amount, currency=currency))
        settlement_only = settlement_only and currency == realized_currency
    commissions.sort(key=lambda item: (item.currency, item.amount))

    funding_adjustment_count = 0
    funding_adjustments_complete = True
    for adjustment in position.adjustments():
        if str(adjustment.adjustment_type) == "FUNDING":
            funding_adjustment_count += 1
            funding_adjustments_complete = (
                funding_adjustments_complete and adjustment.pnl_change is not None
            )

    opened_ns = int(position.ts_opened)
    closed_ns = int(position.ts_closed)
    native_payload = position.to_dict()
    return NativeCompletedPositionUnit(
        sequence_index=sequence_index,
        source_kind=source_kind,
        source_run_id=source_run_id,
        native_position_id=str(position.id),
        parent_position_id=parent_position_id,
        native_payload_sha256=canonical_sha256(native_payload),
        instrument_id=str(position.instrument_id),
        entry_side=str(position.entry),
        opened_ns=opened_ns,
        closed_ns=closed_ns,
        opening_order_id=str(position.opening_order_id),
        closing_order_id=str(position.closing_order_id),
        average_open_price=_decimal_from_native(position.avg_px_open, "position.avg_px_open"),
        average_close_price=_decimal_from_native(position.avg_px_close, "position.avg_px_close"),
        peak_quantity=_decimal_from_native(position.peak_qty, "position.peak_qty"),
        realized_pnl=realized_pnl,
        realized_pnl_currency=realized_currency,
        realized_return=_decimal_from_native(
            position.realized_return,
            "position.realized_return",
        ),
        commissions=tuple(commissions),
        duration_ns=int(position.duration_ns),
        funding_adjustment_count=funding_adjustment_count,
        native_net_after_cost_unambiguous=(
            settlement_only and funding_adjustments_complete
        ),
    )


def capture_native_completed_position_sequence(
    cache: Any,
    *,
    instrument_id: Any,
    source_run_id: str,
    expected_settlement_currency: str,
    expected_closed_cycle_count: int,
) -> NativeCompletedPositionSequence:
    """Capture native closed units without accepting Fills or pairing instructions."""

    from nautilus_trader.model import Position

    _require_nonempty(source_run_id, "native_completed_sequence.source_run_id")
    _require_nonempty(
        expected_settlement_currency,
        "native_completed_sequence.expected_settlement_currency",
    )
    if expected_closed_cycle_count < 0:
        raise NativePositionSequenceError("expected closed-cycle count cannot be negative")
    instrument_text = str(instrument_id)
    current_positions = tuple(cache.positions(instrument_id=instrument_id))
    open_positions = tuple(cache.positions_open(instrument_id=instrument_id))
    closed_positions = tuple(cache.positions_closed(instrument_id=instrument_id))
    global_snapshots = tuple(
        snapshot
        for snapshot in cache.position_snapshots()
        if str(snapshot.instrument_id) == instrument_text
    )
    for label, positions in (
        ("current", current_positions),
        ("open", open_positions),
        ("closed", closed_positions),
        ("snapshot", global_snapshots),
    ):
        if any(not isinstance(position, Position) for position in positions):
            raise NativePositionSequenceError(f"forged non-Nautilus {label} position")
    current_ids = {str(position.id) for position in current_positions}
    open_ids = {str(position.id) for position in open_positions}
    closed_ids = {str(position.id) for position in closed_positions}
    if len(current_ids) != len(current_positions):
        raise NativePositionSequenceError("duplicate current native position identity")
    if not open_ids.issubset(current_ids) or not closed_ids.issubset(current_ids):
        raise NativePositionSequenceError("native open/closed cache view is not bound to current positions")
    if open_ids & closed_ids:
        raise NativePositionSequenceError("native position is simultaneously open and closed")
    if open_ids | closed_ids != current_ids:
        raise NativePositionSequenceError("native current position has no open/closed disposition")

    snapshots: list[tuple[Any, str]] = []
    for current in current_positions:
        parent_id = str(current.id)
        snapshots.extend(
            (snapshot, parent_id)
            for snapshot in cache.position_snapshots(position_id=current.id)
        )
    if len(snapshots) != len(global_snapshots):
        raise NativePositionSequenceError("orphan or foreign native position snapshot detected")

    candidates: list[tuple[Any, str, str]] = [
        (snapshot, "CACHE_POSITION_SNAPSHOT", parent_id)
        for snapshot, parent_id in snapshots
    ]
    candidates.extend(
        (position, "CACHE_CLOSED_POSITION", str(position.id))
        for position in closed_positions
    )
    candidates.sort(
        key=lambda item: (
            -1 if item[0].ts_closed is None else int(item[0].ts_closed),
            int(item[0].ts_opened),
            item[1],
            str(item[0].id),
        ),
    )

    native_ids = [str(item[0].id) for item in candidates]
    if len(native_ids) != len(set(native_ids)):
        raise NativePositionSequenceError("duplicate native completed-position identity")
    if len(candidates) != expected_closed_cycle_count:
        raise NativePositionSequenceError(
            "native snapshots/closed positions disagree with native PositionClosed events",
        )

    units = tuple(
        _unit_from_position(
            position,
            sequence_index=index,
            source_kind=source_kind,
            source_run_id=source_run_id,
            parent_position_id=parent_id,
            expected_instrument_id=instrument_text,
        )
        for index, (position, source_kind, parent_id) in enumerate(candidates)
    )
    currencies = {unit.realized_pnl_currency for unit in units}
    if len(currencies) > 1:
        raise NativePositionSequenceError("native completed sequence has mixed PnL currencies")
    if units:
        settlement_currency = units[0].realized_pnl_currency
    elif current_positions:
        settlement_currency = str(current_positions[0].settlement_currency)
    else:
        settlement_currency = expected_settlement_currency
    if settlement_currency != expected_settlement_currency:
        raise NativePositionSequenceError("native settlement currency disagrees with Run binding")

    unambiguous = all(unit.native_net_after_cost_unambiguous for unit in units)
    semantic_sha = canonical_sha256(tuple(unit.semantic_payload() for unit in units))
    return NativeCompletedPositionSequence.create(
        semantic_sequence_sha256=semantic_sha,
        source=NATIVE_COMPLETED_SEQUENCE_SOURCE,
        source_run_id=source_run_id,
        instrument_id=instrument_text,
        settlement_currency=settlement_currency,
        status="AVAILABLE",
        completed_trade_count=len(units),
        terminal_open_position_count=len(open_positions),
        terminal_closed_position_count=len(closed_positions),
        units=units,
        net_outcomes=(tuple(unit.realized_pnl for unit in units) if unambiguous else ()),
        realized_returns=tuple(unit.realized_return for unit in units),
        unambiguous_net_after_cost=unambiguous,
        project_trade_pairing_used=False,
    )


__all__ = [
    "NATIVE_COMPLETED_SEQUENCE_SOURCE",
    "NativeCommission",
    "NativeCompletedPositionSequence",
    "NativeCompletedPositionUnit",
    "NativePositionSequenceError",
    "capture_native_completed_position_sequence",
]
