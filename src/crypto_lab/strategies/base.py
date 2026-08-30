"""M1 strategy contracts and pre-submit safety restrictions.

The strategy delegates orders, lifecycle, matching, fills, positions, and accounts to
Nautilus.  This module only constrains which V1 intents are eligible for submission.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from nautilus_trader.model import Bar
from nautilus_trader.model import Currency
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import OrderSide
from nautilus_trader.model import Quantity
from nautilus_trader.model import TimeInForce
from nautilus_trader.trading import Strategy

from crypto_lab.config import MarketProfile
from crypto_lab.config import StrictModel
from crypto_lab.config import _freeze_field
from crypto_lab.config import _require_equal
from crypto_lab.config import _require_nonempty
from crypto_lab.hashing import canonical_sha256
from crypto_lab.status import FailureCode
from crypto_lab.data import DataContractError
from crypto_lab.data import lossless_runtime_quantity_text
from crypto_lab.data import validate_market_order_quantity


ONE_MINUTE_NS = 60_000_000_000


class StrategySpec(StrictModel):
    """Frozen material StrategySpec fields required by SSOT Section 5.1."""

    strategy_id: str
    strategy_version: str
    market_profile: MarketProfile
    instrument_id: str
    signal_bar_types: tuple[str, ...]
    parameters: dict[str, str]
    indicator_definitions: tuple[str, ...]
    warmup_requirement: str
    sizing_rule: str
    entry_rule: str
    exit_rule: str
    conflict_rule: str
    terminal_behavior: str
    market_order_time_in_force: str

    def __post_init__(self) -> None:
        for name in (
            "strategy_id",
            "strategy_version",
            "instrument_id",
            "warmup_requirement",
            "sizing_rule",
            "entry_rule",
            "exit_rule",
            "conflict_rule",
            "terminal_behavior",
        ):
            _require_nonempty(getattr(self, name), f"strategy_spec.{name}")
        if not self.signal_bar_types:
            raise ValueError("strategy_spec.signal_bar_types: must not be empty")
        _require_equal(
            self.market_order_time_in_force,
            "GTC",
            "strategy_spec.market_order_time_in_force",
        )
        _require_equal(
            self.terminal_behavior,
            "MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
            "strategy_spec.terminal_behavior",
        )
        _require_equal(
            self.conflict_rule,
            "FIRST_ELIGIBLE_INTENT",
            "strategy_spec.conflict_rule",
        )
        _freeze_field(self, "parameters")

    @property
    def material_payload(self) -> dict[str, Any]:
        payload = self.to_builtins()
        payload.pop("strategy_id")
        return payload

    @property
    def strategy_spec_id(self) -> str:
        return canonical_sha256(self.material_payload)


@dataclass(frozen=True)
class OrderIntent:
    """A qualification intent; it is evidence, never an order or Fill substitute."""

    side: str
    quantity: str
    order_type: str
    reason: str

    def __post_init__(self) -> None:
        if self.side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported intent side {self.side!r}")
        if not self.quantity:
            raise ValueError("intent quantity is required")
        try:
            quantity = Decimal(self.quantity)
        except Exception as exc:
            raise ValueError("intent quantity must be a Decimal string") from exc
        if not quantity.is_finite() or quantity <= 0:
            raise ValueError("intent quantity must be finite and positive")
        if not self.order_type or not self.reason:
            raise ValueError("intent order_type and reason are required")


@dataclass(frozen=True)
class StrategyPlan:
    """Synthetic M1 signal plan with an explicit conflict behavior."""

    intents_by_bar_ns: dict[int, tuple[OrderIntent, ...]]
    conflict_rule: str
    qualification_attempt_all_intents: bool

    def __post_init__(self) -> None:
        if self.conflict_rule != "FIRST_ELIGIBLE_INTENT":
            raise ValueError("unsupported conflict rule")
        normalized: dict[int, tuple[OrderIntent, ...]] = {}
        for timestamp, intents in self.intents_by_bar_ns.items():
            if type(timestamp) is not int or timestamp < 0:
                raise ValueError("plan timestamps must be non-negative integers")
            normalized[timestamp] = tuple(intents)
        object.__setattr__(self, "intents_by_bar_ns", MappingProxyType(normalized))

    def material_payload(self) -> dict[str, Any]:
        """Return the frozen signal schedule bound into an M3 StrategySpec."""

        return {
            "conflict_rule": self.conflict_rule,
            "intents_by_bar_ns": {
                str(timestamp): [
                    {
                        "order_type": intent.order_type,
                        "quantity": intent.quantity,
                        "reason": intent.reason,
                        "side": intent.side,
                    }
                    for intent in intents
                ]
                for timestamp, intents in sorted(self.intents_by_bar_ns.items())
            },
            "qualification_attempt_all_intents": self.qualification_attempt_all_intents,
        }

    @property
    def strategy_plan_sha256(self) -> str:
        return canonical_sha256(self.material_payload())


class GuardedCausalStrategy(Strategy):
    """Actual Nautilus Strategy with only the SSOT's pre-submit controls."""

    def __init__(self) -> None:
        super().__init__()
        self._configured = False
        self._instrument_id: InstrumentId | None = None
        self._bar_type = None
        self._profile: MarketProfile | None = None
        self._plan: StrategyPlan | None = None
        self._scoring_start_ns = 0
        self._scoring_end_exclusive_ns = 0
        self._effective_insert_latency_ns = 0
        self._size_precision = 0
        self._min_quantity: Decimal | None = None
        self._max_quantity: Decimal | None = None
        self._size_increment = Decimal(0)
        self._initial_capital_amount = Decimal(0)
        self._initial_capital_currency = ""
        self._spot_plan_quote_notional_from_signal_close = False
        self._live_client_order_id = None
        self.observations: dict[str, Any] = {
            "bars": [],
            "mark_price_updates": [],
            "funding_rate_updates": [],
            "intents": [],
            "suppressed_intents": [],
            "submitted_intents": [],
            "guard_failures": [],
            "position_sequence": [],
            "lifecycle_clearances": [],
            "scoring_boundary": None,
        }

    def configure(
        self,
        *,
        instrument_id: InstrumentId,
        bar_type: Any,
        execution_bar_type: Any,
        profile: MarketProfile,
        plan: StrategyPlan,
        scoring_start_ns: int,
        scoring_end_exclusive_ns: int,
        effective_insert_latency_ns: int,
        size_precision: int,
        min_quantity: Decimal | None,
        max_quantity: Decimal | None,
        size_increment: Decimal,
        initial_capital_amount: Decimal,
        initial_capital_currency: str,
        spot_plan_quote_notional_from_signal_close: bool = False,
    ) -> None:
        if not isinstance(plan, StrategyPlan):
            raise TypeError("qualification strategy requires StrategyPlan")
        self._spot_plan_quote_notional_from_signal_close = (
            spot_plan_quote_notional_from_signal_close
        )
        self._configure_runtime(
            instrument_id=instrument_id,
            bar_type=bar_type,
            execution_bar_type=execution_bar_type,
            profile=profile,
            plan=plan,
            scoring_start_ns=scoring_start_ns,
            scoring_end_exclusive_ns=scoring_end_exclusive_ns,
            effective_insert_latency_ns=effective_insert_latency_ns,
            size_precision=size_precision,
            min_quantity=min_quantity,
            max_quantity=max_quantity,
            size_increment=size_increment,
            initial_capital_amount=initial_capital_amount,
            initial_capital_currency=initial_capital_currency,
        )

    def _configure_runtime(
        self,
        *,
        instrument_id: InstrumentId,
        bar_type: Any,
        execution_bar_type: Any,
        profile: MarketProfile,
        plan: StrategyPlan | None,
        scoring_start_ns: int,
        scoring_end_exclusive_ns: int,
        effective_insert_latency_ns: int,
        size_precision: int,
        min_quantity: Decimal | None,
        max_quantity: Decimal | None,
        size_increment: Decimal,
        initial_capital_amount: Decimal,
        initial_capital_currency: str,
    ) -> None:
        if self._configured:
            raise RuntimeError("strategy already configured")
        self._instrument_id = instrument_id
        self._bar_type = bar_type
        self._execution_bar_type = execution_bar_type
        self._profile = profile
        self._plan = plan
        self._scoring_start_ns = scoring_start_ns
        self._scoring_end_exclusive_ns = scoring_end_exclusive_ns
        self._effective_insert_latency_ns = effective_insert_latency_ns
        self._size_precision = size_precision
        self._min_quantity = min_quantity
        self._max_quantity = max_quantity
        self._size_increment = size_increment
        self._initial_capital_amount = initial_capital_amount
        self._initial_capital_currency = initial_capital_currency
        self._configured = True

    def on_start(self) -> None:
        if not self._configured or self._bar_type is None:
            raise RuntimeError("strategy must be configured before registration")
        self.subscribe_bars(self._bar_type)
        if (
            self._profile
            is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
        ):
            assert self._instrument_id is not None
            self.subscribe_mark_prices(self._instrument_id)
            self.subscribe_funding_rates(self._instrument_id)
        self._boundary_snapshot(int(self.clock.timestamp_ns()))

    def on_mark_price(self, event: Any) -> None:
        self.observations["mark_price_updates"].append(
            {
                "instrument_id": str(event.instrument_id),
                "value": str(event.value),
                "ts_event": int(event.ts_event),
                "ts_init": int(event.ts_init),
            },
        )

    def on_funding_rate(self, event: Any) -> None:
        self.observations["funding_rate_updates"].append(
            {
                "instrument_id": str(event.instrument_id),
                "rate": str(event.rate),
                "interval": event.interval,
                "next_funding_ns": event.next_funding_ns,
                "ts_event": int(event.ts_event),
                "ts_init": int(event.ts_init),
            },
        )

    def _signed_position(self) -> Decimal:
        assert self._instrument_id is not None
        positions = self.cache.positions_open(instrument_id=self._instrument_id)
        values: list[Decimal] = []
        for position in positions:
            # ``signed_qty`` is a float in the pinned 2.0.0rc2 public API and
            # cannot be used by an exact order guard. ``quantity`` preserves
            # the native fixed-point value; direction is a separate native
            # property. This changes no market or order-grid value.
            quantity = Decimal(str(position.quantity.as_decimal()))
            if position.is_long:
                values.append(quantity)
            elif position.is_short:
                values.append(-quantity)
            else:
                raise RuntimeError("open native Position has no LONG/SHORT direction")
        return sum(values, Decimal(0))

    def _refresh_live_order(self) -> None:
        if self._live_client_order_id is None:
            return
        order = self.cache.order(self._live_client_order_id)
        if order is not None and order.is_closed:
            assert self._instrument_id is not None
            account = self.cache.account_for_venue(self._instrument_id.venue)
            self.observations["lifecycle_clearances"].append(
                {
                    "observed_at_ns": int(self.clock.timestamp_ns()),
                    "client_order_id": str(order.client_order_id),
                    "terminal_status": str(order.status),
                    "native_fill_events": sum(
                        type(event).__name__ == "OrderFilled"
                        for event in order.events()
                    ),
                    "signed_position": str(self._signed_position()),
                    "account_state_events": 0 if account is None else len(account.events),
                },
            )
            self._live_client_order_id = None

    def _boundary_snapshot(self, observed_at_ns: int) -> None:
        if (
            self.observations["scoring_boundary"] is not None
            or observed_at_ns < self._scoring_start_ns
        ):
            return
        self._refresh_live_order()
        assert self._instrument_id is not None
        account = self.cache.account_for_venue(self._instrument_id.venue)
        if account is None:
            return
        balance = account.balance(Currency.from_str(self._initial_capital_currency))
        if balance is None:
            return
        self.observations["scoring_boundary"] = {
            "timestamp_ns": self._scoring_start_ns,
            "observed_before_first_eligible_decision_at_ns": observed_at_ns,
            "signed_position": str(self._signed_position()),
            "non_terminal_strategy_orders": int(self._live_client_order_id is not None),
            "account_total": str(balance.total.as_decimal()),
            "expected_initial_capital": str(self._initial_capital_amount),
            "currency": self._initial_capital_currency,
        }

    def _record_guard_failure(
        self,
        code: FailureCode,
        *,
        intent: OrderIntent,
        timestamp_ns: int,
        detail: str,
    ) -> None:
        self.observations["guard_failures"].append(
            {
                "failure_code": code.value,
                "timestamp_ns": timestamp_ns,
                "intent": {
                    "side": intent.side,
                    "quantity": intent.quantity,
                    "order_type": intent.order_type,
                    "reason": intent.reason,
                },
                "detail": detail,
            },
        )

    def _submit_guarded(
        self,
        intent: OrderIntent,
        bar: Bar,
        *,
        decision_timestamp_ns: int | None = None,
        spot_quote_notional: Decimal | None = None,
    ) -> None:
        assert self._instrument_id is not None
        assert self._profile is not None
        now = int(self.clock.timestamp_ns())
        interval_end = int(bar.ts_init)
        interval_start = interval_end - int(bar.bar_type.spec.get_interval_ns())
        decision_at = interval_end if decision_timestamp_ns is None else decision_timestamp_ns
        scoring_eligibility_at = (
            interval_start if decision_timestamp_ns is None else decision_timestamp_ns
        )
        if decision_at < interval_end:
            raise ValueError("decision timestamp precedes signal availability")
        record = {
            "side": intent.side,
            "quantity": intent.quantity,
            "order_type": intent.order_type,
            "reason": intent.reason,
            "signal_bar_interval_start_ns": interval_start,
            "signal_bar_interval_end_exclusive_ns": interval_end,
            "signal_bar_available_at_ns": interval_end,
            "signal_timestamp_ns": now,
            "decision_timestamp_ns": decision_at,
        }
        self.observations["intents"].append(record)

        if (
            scoring_eligibility_at < self._scoring_start_ns
            or scoring_eligibility_at >= self._scoring_end_exclusive_ns
            or interval_end > decision_at
        ):
            self.observations["suppressed_intents"].append(
                {**record, "reason_code": "SIGNAL_BAR_NOT_SCORING_ELIGIBLE"},
            )
            return
        if now + self._effective_insert_latency_ns >= self._scoring_end_exclusive_ns:
            self.observations["suppressed_intents"].append(
                {**record, "reason_code": "TERMINAL_INSERT_BOUNDARY"},
            )
            return
        if intent.order_type != "MARKET":
            self._record_guard_failure(
                FailureCode.UNSUPPORTED_V1_ORDER_TYPE,
                intent=intent,
                timestamp_ns=now,
                detail="excluded order type rejected before Nautilus submission",
            )
            return

        self._refresh_live_order()
        if self._live_client_order_id is not None:
            self._record_guard_failure(
                FailureCode.CONCURRENT_STRATEGY_ORDER_REJECTED,
                intent=intent,
                timestamp_ns=now,
                detail="another strategy order is non-terminal",
            )
            return

        try:
            runtime_quantity_text = lossless_runtime_quantity_text(
                intent.quantity,
                self._size_precision,
            )
            quantity = Quantity.from_str(runtime_quantity_text)
        except DataContractError:
            self._record_guard_failure(
                FailureCode.INSTRUMENT_METADATA_INVALID,
                intent=intent,
                timestamp_ns=now,
                detail="order quantity cannot be represented losslessly at Instrument precision",
            )
            return
        requested = Decimal(intent.quantity)
        instrument = self.cache.instrument(self._instrument_id)
        try:
            if instrument is None:
                raise DataContractError(
                    FailureCode.INSTRUMENT_METADATA_INVALID,
                    "native Instrument is unavailable before order submission",
                )
            validate_market_order_quantity(instrument, quantity)
        except DataContractError as exc:
            self._record_guard_failure(
                FailureCode.INSTRUMENT_METADATA_INVALID,
                intent=intent,
                timestamp_ns=now,
                detail=f"MARKET quantity violates Binance filters: {exc}",
            )
            return
        signed = self._signed_position()
        runtime_order_quantity = quantity
        runtime_quantity_text = str(quantity)
        quote_quantity = False
        affordability: dict[str, str | bool] = {
            "cash_affordability_proven": self._profile is not MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            "order_quantity_unit": "BASE",
        }
        if self._profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
            account = self.cache.account_for_venue(self._instrument_id.venue)
            if account is None:
                self._record_guard_failure(
                    FailureCode.SPOT_SHORT_OR_BORROW_DETECTED,
                    intent=intent,
                    timestamp_ns=now,
                    detail="native CASH Account is unavailable before order submission",
                )
                return
            base_balance = account.balance(instrument.base_currency)
            quote_balance = account.balance(instrument.quote_currency)
            available_base = (
                Decimal(0)
                if base_balance is None
                else Decimal(str(base_balance.free.as_decimal()))
            )
            available_quote = (
                Decimal(0)
                if quote_balance is None
                else Decimal(str(quote_balance.free.as_decimal()))
            )
            if signed < 0 or (
                intent.side == "SELL"
                and (requested > signed or requested > available_base)
            ):
                self._record_guard_failure(
                    FailureCode.SPOT_SHORT_OR_BORROW_DETECTED,
                    intent=intent,
                    timestamp_ns=now,
                    detail="Spot sell would exceed native Position or free base balance",
                )
                return
            if intent.side == "BUY":
                maximum = instrument.max_price
                if maximum is None:
                    self._record_guard_failure(
                        FailureCode.SPOT_SHORT_OR_BORROW_DETECTED,
                        intent=intent,
                        timestamp_ns=now,
                        detail="Spot buy has no Instrument maximum-price bound",
                    )
                    return
                maximum_fill_price = Decimal(str(maximum.as_decimal())) + Decimal(
                    str(instrument.price_increment.as_decimal()),
                )
                fee_rate = Decimal(str(instrument.taker_fee))
                money_quantum = Decimal(1).scaleb(-instrument.quote_currency.precision)
                if spot_quote_notional is None:
                    maximum_cost = requested * maximum_fill_price * (Decimal(1) + fee_rate)
                    if maximum_cost > available_quote:
                        self._record_guard_failure(
                            FailureCode.SPOT_SHORT_OR_BORROW_DETECTED,
                            intent=intent,
                            timestamp_ns=now,
                            detail=(
                                "base-quantity Spot MARKET buy is not provably funded at the "
                                "Instrument maximum executable price"
                            ),
                        )
                        return
                    affordability = {
                        "cash_affordability_proven": True,
                        "order_quantity_unit": "BASE",
                        "available_quote_before": str(available_quote),
                        "maximum_fill_price_bound": str(maximum_fill_price),
                        "maximum_cost_bound": str(maximum_cost),
                    }
                else:
                    if not spot_quote_notional.is_finite() or spot_quote_notional <= 0:
                        raise ValueError("Spot quote notional must be finite and positive")
                    rounding_reserve = (
                        Decimal(str(instrument.size_increment.as_decimal()))
                        * maximum_fill_price
                    )
                    maximum_quote_notional = (
                        (available_quote - money_quantum) / (Decimal(1) + fee_rate)
                        - rounding_reserve
                    )
                    quantity_quantum = Decimal(1).scaleb(-self._size_precision)
                    safe_quote_notional = min(spot_quote_notional, maximum_quote_notional)
                    safe_quote_notional = (
                        safe_quote_notional // quantity_quantum
                    ) * quantity_quantum
                    if safe_quote_notional <= 0:
                        self._record_guard_failure(
                            FailureCode.SPOT_SHORT_OR_BORROW_DETECTED,
                            intent=intent,
                            timestamp_ns=now,
                            detail="no positive quote-denominated Spot buy is provably funded",
                        )
                        return
                    runtime_quantity_text = f"{safe_quote_notional:.{self._size_precision}f}"
                    runtime_order_quantity = Quantity.from_str(runtime_quantity_text)
                    quote_quantity = True
                    affordability = {
                        "cash_affordability_proven": True,
                        "order_quantity_unit": "QUOTE",
                        "available_quote_before": str(available_quote),
                        "requested_quote_notional": str(spot_quote_notional),
                        "submitted_quote_notional": str(safe_quote_notional),
                        "maximum_fill_price_bound": str(maximum_fill_price),
                        "base_rounding_reserve": str(rounding_reserve),
                        "commission_rounding_reserve": str(money_quantum),
                    }
            elif spot_quote_notional is not None:
                raise ValueError("Spot quote notional is valid only for BUY intents")
        elif (signed > 0 and intent.side == "SELL" and requested > signed) or (
            signed < 0 and intent.side == "BUY" and requested > abs(signed)
        ):
            self._record_guard_failure(
                FailureCode.CROSS_ZERO_ORDER_REJECTED,
                intent=intent,
                timestamp_ns=now,
                detail="opposing Perpetual order would cross a non-flat position through zero",
            )
            return

        side = OrderSide.BUY if intent.side == "BUY" else OrderSide.SELL
        order = self.order_factory.market(
            self._instrument_id,
            side,
            runtime_order_quantity,
            time_in_force=TimeInForce.GTC,
            quote_quantity=quote_quantity,
        )
        self._live_client_order_id = order.client_order_id
        self.observations["submitted_intents"].append(
            {
                **record,
                "client_order_id": str(order.client_order_id),
                "effective_insert_at_ns": now + self._effective_insert_latency_ns,
                "position_before": str(signed),
                "time_in_force": "GTC",
                "canonical_quantity": intent.quantity,
                "runtime_quantity": runtime_quantity_text,
                "runtime_zero_padding_only": (
                    not quote_quantity and Decimal(runtime_quantity_text) == requested
                ),
                "quote_quantity": quote_quantity,
                **affordability,
            },
        )
        self.submit_order(order)

    def on_bar(self, bar: Bar) -> None:
        if not self._configured:
            raise RuntimeError("strategy is not configured")
        now = int(self.clock.timestamp_ns())
        self._boundary_snapshot(now)
        self.observations["bars"].append(
            {
                "bar_type": str(bar.bar_type),
                "ts_event": int(bar.ts_event),
                "ts_init": int(bar.ts_init),
                "callback_clock_ns": now,
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": str(bar.volume),
            },
        )
        if self._plan is None:
            return
        intents = self._plan.intents_by_bar_ns.get(int(bar.ts_init), ())
        if not intents:
            return
        if self._plan.qualification_attempt_all_intents:
            for intent in intents:
                self._submit_guarded(intent, bar)
            return
        intent = intents[0]
        self._submit_guarded(
            intent,
            bar,
            spot_quote_notional=(
                Decimal(intent.quantity) * Decimal(str(bar.close))
                if self._spot_plan_quote_notional_from_signal_close
                and self._profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
                and intent.side == "BUY"
                else None
            ),
        )
        for intent in intents[1:]:
            self.observations["suppressed_intents"].append(
                {
                    "side": intent.side,
                    "quantity": intent.quantity,
                    "order_type": intent.order_type,
                    "reason": intent.reason,
                    "signal_timestamp_ns": now,
                    "reason_code": "CONFLICT_RULE_REDUCED",
                },
            )

    def _record_position(self, event: Any) -> None:
        self.observations["position_sequence"].append(
            {
                "event_type": type(event).__name__,
                "timestamp_ns": int(event.ts_event),
                "signed_position": str(self._signed_position()),
            },
        )

    def on_position_opened(self, event: Any) -> None:
        self._record_position(event)

    def on_position_changed(self, event: Any) -> None:
        self._record_position(event)

    def on_position_closed(self, event: Any) -> None:
        self._record_position(event)


class FirstEligibleBarQualificationFixture(GuardedCausalStrategy):
    """Registered Nautilus Strategy used only to qualify the Official boundary.

    This is deliberately not an economic strategy and is permanently
    ineligible for a profitability claim.  Unlike ``StrategyPlan``, its order
    behavior is implemented by this registered Strategy class and resolved from
    the complete frozen StrategySpec.
    """

    REGISTRATION_ID = "qualification_fixture_first_eligible_bar_v1"
    IMPLEMENTATION_REVISION = "QUALIFICATION_FIXTURE_FIRST_ELIGIBLE_BAR_V1"
    REQUIRED_PARAMETERS = {
        "fixture_purpose",
        "network_access",
        "order_quantity",
        "order_side",
        "profitability_claim",
        "trigger",
    }

    def __init__(self) -> None:
        super().__init__()
        self._fixture_intent: OrderIntent | None = None
        self._fixture_attempted = False

    def configure_registered(self, *, strategy_spec: StrategySpec, **configuration: Any) -> None:
        parameters = dict(strategy_spec.parameters)
        if set(parameters) != self.REQUIRED_PARAMETERS:
            missing = sorted(self.REQUIRED_PARAMETERS - set(parameters))
            unknown = sorted(set(parameters) - self.REQUIRED_PARAMETERS)
            raise ValueError(
                f"registered strategy parameters differ; missing={missing}, unknown={unknown}",
            )
        expected = {
            "fixture_purpose": "PUBLIC_BOUNDARY_QUALIFICATION_ONLY",
            "network_access": "FORBIDDEN",
            "profitability_claim": "INELIGIBLE",
            "trigger": "FIRST_SCORING_ELIGIBLE_BAR",
        }
        mismatches = [name for name, value in expected.items() if parameters[name] != value]
        if mismatches:
            raise ValueError(
                "registered qualification fixture contract mismatch: " + ",".join(mismatches),
            )
        side = parameters["order_side"]
        if side not in {"BUY", "SELL"}:
            raise ValueError("registered strategy order_side must be BUY or SELL")
        self._fixture_intent = OrderIntent(
            side=side,
            quantity=parameters["order_quantity"],
            order_type="MARKET",
            reason="REGISTERED_FIRST_SCORING_ELIGIBLE_BAR_FIXTURE",
        )
        self._configure_runtime(
            plan=None,
            execution_bar_type=configuration.pop("execution_bar_type"),
            **configuration,
        )

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        if self._fixture_attempted or self._fixture_intent is None:
            return
        interval_end = int(bar.ts_init)
        interval_start = interval_end - ONE_MINUTE_NS
        if (
            interval_start >= self._scoring_start_ns
            and interval_end <= self._scoring_end_exclusive_ns
        ):
            self._fixture_attempted = True
            self._submit_guarded(
                self._fixture_intent,
                bar,
                spot_quote_notional=(
                    Decimal(self._fixture_intent.quantity) * Decimal(str(bar.close))
                    if self._profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
                    and self._fixture_intent.side == "BUY"
                    else None
                ),
            )
