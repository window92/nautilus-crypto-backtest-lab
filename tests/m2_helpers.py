from __future__ import annotations

import json
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from crypto_lab.config import MarketProfile
from crypto_lab.data import NOT_APPLICABLE
from crypto_lab.data import InstrumentMetadata
from crypto_lab.data import SourceObjectBinding
from crypto_lab.data import SourceRole
from crypto_lab.data import TimeRange
from crypto_lab.data import parse_kline_csv
from crypto_lab.data import parse_spot_instrument_metadata
from crypto_lab.data import parse_usdm_instrument_metadata


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/golden/fixtures/m2"
ZERO_FEE_BASIS = "QUALIFICATION_ONLY_EXPLICIT_ZERO_NO_ACCOUNT_TIER_CLAIM"


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def spot_range() -> TimeRange:
    return TimeRange(
        start_inclusive=utc("2024-12-31T23:58:00Z"),
        end_exclusive=utc("2025-01-01T00:02:00Z"),
    )


def perp_range() -> TimeRange:
    return TimeRange(
        start_inclusive=utc("2025-01-01T00:00:00Z"),
        end_exclusive=utc("2025-01-01T00:04:00Z"),
    )


def spot_bars():
    pre = parse_kline_csv(
        FIXTURES.joinpath("spot-pre-transition.csv").read_bytes(),
        source_role=SourceRole.SPOT_EXECUTION_1M,
        instrument_id="BTCUSDT.BINANCE",
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        source_date=datetime(2024, 12, 31, tzinfo=UTC).date(),
    )
    post = parse_kline_csv(
        FIXTURES.joinpath("spot-post-transition.csv").read_bytes(),
        source_role=SourceRole.SPOT_EXECUTION_1M,
        instrument_id="BTCUSDT.BINANCE",
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        source_date=datetime(2025, 1, 1, tzinfo=UTC).date(),
    )
    return (*pre, *post)


def perp_execution_bars():
    return parse_kline_csv(
        FIXTURES.joinpath("usdm-execution.csv").read_bytes(),
        source_role=SourceRole.USDM_PERPETUAL_EXECUTION_1M,
        instrument_id="BTCUSDT-PERP.BINANCE",
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        source_date=datetime(2025, 1, 1, tzinfo=UTC).date(),
    )


def perp_mark_bars():
    return parse_kline_csv(
        FIXTURES.joinpath("usdm-mark.csv").read_bytes(),
        source_role=SourceRole.USDM_PERPETUAL_MARK_1M,
        instrument_id="BTCUSDT-PERP.BINANCE",
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        source_date=datetime(2025, 1, 1, tzinfo=UTC).date(),
    )


def spot_metadata_payload(server_time: int = 1_787_365_504_874) -> bytes:
    return json.dumps(
        {
            "timezone": "UTC",
            "serverTime": server_time,
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "baseAssetPrecision": 8,
                    "quotePrecision": 8,
                    "filters": [
                        {
                            "filterType": "PRICE_FILTER",
                            "minPrice": "0.01000000",
                            "maxPrice": "1000000.00000000",
                            "tickSize": "0.01000000",
                        },
                        {
                            "filterType": "LOT_SIZE",
                            "minQty": "0.00001000",
                            "maxQty": "9000.00000000",
                            "stepSize": "0.00001000",
                        },
                        {
                            "filterType": "MARKET_LOT_SIZE",
                            "minQty": "0.00000000",
                            "maxQty": "107.65653775",
                            "stepSize": "0.00000000",
                        },
                        {
                            "filterType": "NOTIONAL",
                            "minNotional": "5.00000000",
                            "maxNotional": "9000000.00000000",
                        },
                    ],
                },
            ],
        },
        separators=(",", ":"),
    ).encode()


def perp_metadata_payload(server_time: int = 1_787_364_010_802) -> bytes:
    return json.dumps(
        {
            "timezone": "UTC",
            "serverTime": server_time,
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "marginAsset": "USDT",
                    "pricePrecision": 2,
                    "quantityPrecision": 3,
                    "maintMarginPercent": "2.5000",
                    "requiredMarginPercent": "5.0000",
                    "filters": [
                        {
                            "filterType": "PRICE_FILTER",
                            "minPrice": "556.80",
                            "maxPrice": "4529764",
                            "tickSize": "0.10",
                        },
                        {
                            "filterType": "LOT_SIZE",
                            "minQty": "0.001",
                            "maxQty": "1000",
                            "stepSize": "0.001",
                        },
                        {
                            "filterType": "MARKET_LOT_SIZE",
                            "minQty": "0.001",
                            "maxQty": "120",
                            "stepSize": "0.001",
                        },
                        {"filterType": "MIN_NOTIONAL", "notional": "50"},
                    ],
                },
            ],
        },
        separators=(",", ":"),
    ).encode()


def spot_metadata() -> InstrumentMetadata:
    return parse_spot_instrument_metadata(
        spot_metadata_payload(),
        raw_symbol="BTCUSDT",
        instrument_id="BTCUSDT.BINANCE",
        source_object_sha256="1" * 64,
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"),
        fee_rate_basis=ZERO_FEE_BASIS,
    )


def perp_metadata() -> InstrumentMetadata:
    return parse_usdm_instrument_metadata(
        perp_metadata_payload(),
        raw_symbol="BTCUSDT",
        instrument_id="BTCUSDT-PERP.BINANCE",
        source_object_sha256="2" * 64,
        maker_fee_rate=Decimal("0"),
        taker_fee_rate=Decimal("0"),
        fee_rate_basis=ZERO_FEE_BASIS,
    )


def binding(role: SourceRole, digest_char: str) -> SourceObjectBinding:
    if role is SourceRole.SPOT_EXECUTION_1M:
        locator = (
            "https://data.binance.vision/data/spot/daily/klines/"
            "BTCUSDT/1m/BTCUSDT-1m-2025-01-01.zip"
        )
        profile = MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY.value
        interval = "1m"
        filename = "BTCUSDT-1m-2025-01-01.zip"
        requested = spot_range()
    elif role is SourceRole.SPOT_INSTRUMENT_METADATA:
        locator = "https://api.binance.com/api/v3/exchangeInfo?symbol=BTCUSDT"
        profile = MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY.value
        interval = NOT_APPLICABLE
        filename = "spot-exchangeInfo-BTCUSDT.json"
        requested = NOT_APPLICABLE
    elif role is SourceRole.USDM_PERPETUAL_EXECUTION_1M:
        locator = (
            "https://data.binance.vision/data/futures/um/daily/klines/"
            "BTCUSDT/1m/BTCUSDT-1m-2025-01-01.zip"
        )
        profile = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value
        interval = "1m"
        filename = "BTCUSDT-1m-2025-01-01.zip"
        requested = perp_range()
    elif role is SourceRole.USDM_PERPETUAL_MARK_1M:
        locator = (
            "https://data.binance.vision/data/futures/um/daily/markPriceKlines/"
            "BTCUSDT/1m/BTCUSDT-1m-2025-01-01.zip"
        )
        profile = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value
        interval = "1m"
        filename = "BTCUSDT-1m-2025-01-01.zip"
        requested = perp_range()
    elif role is SourceRole.USDM_PERPETUAL_FUNDING:
        locator = (
            "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
            "BTCUSDT/BTCUSDT-fundingRate-2025-01.zip"
        )
        profile = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value
        interval = "EVENT"
        filename = "BTCUSDT-fundingRate-2025-01.zip"
        requested = perp_range()
    elif role is SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA:
        locator = "https://fapi.binance.com/fapi/v1/exchangeInfo"
        profile = MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value
        interval = NOT_APPLICABLE
        filename = "usdm-exchangeInfo.json"
        requested = NOT_APPLICABLE
    else:
        raise AssertionError(role)
    return SourceObjectBinding(
        source_role=role,
        source_locator=locator,
        exact_filename=filename,
        byte_size=1,
        sha256=digest_char * 64,
        publisher_checksum=NOT_APPLICABLE if False else "a" * 64,
        instrument="BTCUSDT",
        market_profile=profile,
        requested_interval=interval,
        requested_time_range=requested,
        conflicts_with_sha256=(),
    )


def spot_bindings() -> tuple[SourceObjectBinding, ...]:
    return (
        binding(SourceRole.SPOT_EXECUTION_1M, "3"),
        binding(SourceRole.SPOT_INSTRUMENT_METADATA, "4"),
    )


def perp_bindings() -> tuple[SourceObjectBinding, ...]:
    return (
        binding(SourceRole.USDM_PERPETUAL_EXECUTION_1M, "5"),
        binding(SourceRole.USDM_PERPETUAL_MARK_1M, "6"),
        binding(SourceRole.USDM_PERPETUAL_FUNDING, "7"),
        binding(SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA, "8"),
    )
