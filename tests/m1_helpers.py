from __future__ import annotations

import copy
import json
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_trader.model import AggregationSource
from nautilus_trader.model import Bar
from nautilus_trader.model import BarAggregation
from nautilus_trader.model import BarSpecification
from nautilus_trader.model import BarType
from nautilus_trader.model import CryptoPerpetual
from nautilus_trader.model import Currency
from nautilus_trader.model import CurrencyPair
from nautilus_trader.model import FundingRateUpdate
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import MarkPriceUpdate
from nautilus_trader.model import Price
from nautilus_trader.model import PriceType
from nautilus_trader.model import Quantity
from nautilus_trader.model import Symbol

from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.config import SourceRevision
from crypto_lab.hashing import canonical_sha256
from crypto_lab.data import SyntheticDataDescriptor
from crypto_lab.data import SyntheticFundingExpectation
from crypto_lab.data import SyntheticQualificationDatasetRelease
from crypto_lab.runner import LabRunRequest
from crypto_lab.runner import QualificationControl
from crypto_lab.strategies import OrderIntent
from crypto_lab.strategies import StrategyPlan
from crypto_lab.strategies import StrategySpec
from tests.helpers import load_spot_config_dict


MIGRATION_COMMIT = "c305417a38f9e0acdcd611c9c211f24fc73ccdcf"
MIGRATION_TREE = "d31ab84af7d79bb14b715d2967552057718e746e"
SPOT_ID = InstrumentId.from_str("BTCUSDT.BINANCE")
PERP_ID = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
BTC = Currency.from_str("BTC")
USDT = Currency.from_str("USDT")


def iso_ns(timestamp_ns: int) -> str:
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=UTC).isoformat().replace(
        "+00:00",
        "Z",
    )


def source_revision() -> SourceRevision:
    return SourceRevision(
        repository="https://github.com/window92/nautilus-crypto-backtest-lab.git",
        branch_ref="main",
        git_commit=MIGRATION_COMMIT,
        git_tree=MIGRATION_TREE,
        clean_worktree=False,
        captured_at_utc=datetime(2026, 8, 22, 1, 30, tzinfo=UTC),
    )


def make_strategy_spec(profile: MarketProfile, instrument_id: str) -> StrategySpec:
    return StrategySpec(
        strategy_id="m1-synthetic-guarded-strategy",
        strategy_version="1",
        market_profile=profile,
        instrument_id=instrument_id,
        signal_bar_types=(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL",),
        parameters={"fixture": "M1_SYNTHETIC"},
        indicator_definitions=(),
        warmup_requirement="EXPLICIT_SCORING_WINDOW_ONLY",
        sizing_rule="EXPLICIT_INTENT_QUANTITY",
        entry_rule="SYNTHETIC_TIMESTAMP_PLAN",
        exit_rule="SYNTHETIC_TIMESTAMP_PLAN",
        conflict_rule="FIRST_ELIGIBLE_INTENT",
        terminal_behavior="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
        market_order_time_in_force="GTC",
    )


def bar_type(instrument_id: InstrumentId) -> BarType:
    return BarType(
        instrument_id,
        BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
        AggregationSource.EXTERNAL,
    )


def make_bars(
    instrument_id: InstrumentId,
    rows: tuple[tuple[int, str, str, str, str], ...],
    *,
    volume: str = "1000",
) -> tuple[Bar, ...]:
    kind = bar_type(instrument_id)
    return tuple(
        Bar(
            kind,
            Price.from_str(open_),
            Price.from_str(high),
            Price.from_str(low),
            Price.from_str(close),
            Quantity.from_str(volume),
            timestamp,
            timestamp,
        )
        for timestamp, open_, high, low, close in rows
    )


def a4_bars(instrument_id: InstrumentId, count: int = 3) -> tuple[Bar, ...]:
    rows = (
        (60_000_000_000, "100.00", "101.00", "99.00", "100.00"),
        (120_000_000_000, "200.00", "201.00", "199.00", "200.00"),
        (180_000_000_000, "300.00", "301.00", "299.00", "300.00"),
        (240_000_000_000, "90.01", "91.01", "89.01", "90.01"),
        (300_000_000_000, "89.99", "90.99", "88.99", "89.99"),
        (360_000_000_000, "80.00", "81.00", "79.00", "80.00"),
    )
    return make_bars(instrument_id, rows[:count])


def lifecycle_bars() -> tuple[Bar, ...]:
    """A.2 prices: open +2 at 100, reduce/close/reopen with SELL Fills at 90."""

    rows = (
        (60_000_000_000, "50.00", "51.00", "49.00", "50.00"),
        (120_000_000_000, "99.99", "100.99", "98.99", "99.99"),
        (180_000_000_000, "110.00", "111.00", "109.00", "110.00"),
        (240_000_000_000, "90.01", "91.01", "89.01", "90.01"),
        (300_000_000_000, "95.00", "96.00", "94.00", "95.00"),
        (360_000_000_000, "90.01", "91.01", "89.01", "90.01"),
        (420_000_000_000, "95.00", "96.00", "94.00", "95.00"),
        (480_000_000_000, "90.01", "91.01", "89.01", "90.01"),
        (540_000_000_000, "80.00", "81.00", "79.00", "80.00"),
        (600_000_000_000, "79.99", "80.99", "78.99", "79.99"),
    )
    return make_bars(PERP_ID, rows)


def make_instrument(
    profile: MarketProfile,
    *,
    maker_fee: Decimal = Decimal("0"),
    taker_fee: Decimal = Decimal("0"),
) -> Any:
    common = dict(
        price_precision=2,
        size_precision=0,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("1"),
        ts_event=0,
        ts_init=0,
        multiplier=Quantity.from_str("1"),
        margin_init=Decimal("1"),
        margin_maint=Decimal("1"),
        maker_fee=maker_fee,
        taker_fee=taker_fee,
    )
    if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        return CurrencyPair(
            SPOT_ID,
            Symbol("BTCUSDT"),
            BTC,
            USDT,
            **common,
        )
    return CryptoPerpetual(
        PERP_ID,
        Symbol("BTCUSDT"),
        BTC,
        USDT,
        USDT,
        False,
        **common,
    )


def complete_perpetual_roles(
    bars: tuple[Bar, ...],
    *,
    mark_value: str = "100.00",
) -> tuple[Any, ...]:
    instrument_id = bars[0].bar_type.instrument_id
    last_ts = int(bars[-1].ts_init)
    values: list[Any] = []
    for bar in bars:
        values.append(bar)
        values.append(
            MarkPriceUpdate(
                instrument_id,
                Price.from_str(mark_value),
                int(bar.ts_init),
                int(bar.ts_init),
            ),
        )
        if int(bar.ts_init) == int(bars[0].ts_init):
            values.append(
                FundingRateUpdate(
                    instrument_id,
                    Decimal("0"),
                    int(bar.ts_init),
                    int(bar.ts_init),
                    interval=480,
                    next_funding_ns=last_ts + 10 * 60_000_000_000,
                ),
            )
    return tuple(values)


def make_request(
    evidence_root: Path,
    *,
    run_id: str,
    profile: MarketProfile,
    data: tuple[Any, ...],
    plan: StrategyPlan,
    scoring_start_ns: int,
    scoring_end_ns: int,
    fee: Decimal = Decimal("0"),
    qualification_control: QualificationControl = QualificationControl.STANDARD,
    mark_complete: bool = True,
    expected_funding_settlements: tuple[dict[str, Any], ...] = (),
) -> LabRunRequest:
    instrument = make_instrument(profile, maker_fee=fee, taker_fee=fee)
    instrument_id = str(instrument.id)
    spec = make_strategy_spec(profile, instrument_id)
    data_material = tuple(
        SyntheticDataDescriptor(
            type=type(item).__name__,
            instrument_id=str(item.instrument_id if not isinstance(item, Bar) else item.bar_type.instrument_id),
            ts_event=int(item.ts_event),
            ts_init=int(item.ts_init),
            value=str(item),
        )
        for item in data
    )
    release = SyntheticQualificationDatasetRelease.create(
        qualification_scope="M1_SYNTHETIC_QUALIFICATION_ONLY",
        market_profile=profile,
        instrument_id=instrument_id,
        data=data_material,
        mark_role=(
            "NOT_APPLICABLE"
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else "markPriceKlines"
        ),
        mark_complete=(
            "NOT_APPLICABLE"
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else mark_complete
        ),
        funding_role=(
            "NOT_APPLICABLE"
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else "fundingRate"
        ),
        funding_complete=(
            "NOT_APPLICABLE"
            if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
            else True
        ),
        expected_funding_settlements=tuple(
            SyntheticFundingExpectation(
                boundary_ns=int(item["boundary_ns"]),
                pnl_change=str(item["pnl_change"]),
            )
            for item in expected_funding_settlements
        ),
    )
    release_id = release.dataset_release_id

    raw = copy.deepcopy(load_spot_config_dict())
    raw["run_id"] = run_id
    raw["market_profile"] = profile.value
    raw["instrument_id"] = instrument_id
    raw["dataset_release_id"] = release_id
    raw["strategy_spec_id"] = spec.strategy_spec_id
    raw["warmup_start"] = iso_ns(0)
    raw["scoring_start"] = iso_ns(scoring_start_ns)
    raw["scoring_end_exclusive"] = iso_ns(scoring_end_ns)
    raw["execution_bar_type"] = f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"
    raw["signal_bar_types"] = [raw["execution_bar_type"]]
    raw["fee_assumption"] = {
        "maker_fee": str(fee),
        "taker_fee": str(fee),
        "explicit_zero_fee": fee == 0,
        "reason": "M1 independently specified synthetic fee qualification",
        "claim_class": "ESTIMATED_FEE",
    }
    venue = raw["nautilus_venue_config"]
    venue["account_type"] = (
        "CASH"
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else "MARGIN"
    )
    venue["instrument_leverages"] = (
        []
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else [{"instrument_id": instrument_id, "leverage": "1"}]
    )
    raw["nautilus_engine_config"]["portfolio"]["use_mark_prices"] = (
        profile is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
    )
    if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY:
        raw["funding_binding"] = "NOT_APPLICABLE"
        raw["mark_binding"] = "NOT_APPLICABLE"
    else:
        raw["funding_binding"] = canonical_sha256({"role": "fundingRate"})
        raw["mark_binding"] = canonical_sha256({"role": "markPriceKlines"})
    raw["nautilus_data_config"] = [
        {
            "catalog_path": str(evidence_root / "synthetic-catalog"),
            "catalog_fs_protocol": "file",
            "catalog_fs_storage_options": {},
            "catalog_fs_rust_storage_options": {},
            "data_type": "Bar",
            "instrument_id": instrument_id,
            "start_time": iso_ns(0),
            "end_time": iso_ns(scoring_end_ns),
            "filter_expr": "NOT_APPLICABLE",
            "client_id": "NOT_APPLICABLE",
            "metadata": {},
            "bar_spec": "1-MINUTE-LAST",
            "instrument_ids": [],
            "bar_types": [],
            "optimize_file_loading": False,
        },
    ]
    config = LabRunConfig.from_json_bytes(
        json.dumps(raw, separators=(",", ":")).encode("utf-8"),
    )
    return LabRunRequest(
        lab_run_config=config,
        source_revision=source_revision(),
        strategy_spec=spec,
        dataset_release=release,
        instrument=instrument,
        data=data,
        strategy_plan=plan,
        evidence_root=evidence_root,
        qualification_control=qualification_control,
    )


def intent(side: str, quantity: str, reason: str, order_type: str = "MARKET") -> OrderIntent:
    return OrderIntent(side=side, quantity=quantity, order_type=order_type, reason=reason)


def plan(
    mapping: dict[int, tuple[OrderIntent, ...]],
    *,
    attempt_all: bool = False,
) -> StrategyPlan:
    return StrategyPlan(
        intents_by_bar_ns=mapping,
        conflict_rule="FIRST_ELIGIBLE_INTENT",
        qualification_attempt_all_intents=attempt_all,
    )
