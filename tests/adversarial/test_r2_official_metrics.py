from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal

from crypto_lab.reporting import EquityObservation
from crypto_lab.reporting import NATIVE_STATISTICS_DIAGNOSTIC_ROLE
from crypto_lab.reporting import REQUIRED_SCIENTIFIC_LIMITATIONS
from crypto_lab.reporting import generate_performance_diagnostics
from crypto_lab.diagnostics import _money_total
from crypto_lab.diagnostics import _spot_daily_ledger_equity
from crypto_lab.hashing import canonical_sha256
from crypto_lab.research import CompletedTradeSeries
from crypto_lab.research import MonteCarloStatus
from crypto_lab.research import ResearchError
from crypto_lab.research import SampleAdequacy


class OfficialDailyMetricsAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = datetime(2024, 1, 1, tzinfo=UTC)
        equity = Decimal("1000")
        values = [equity]
        for index in range(30):
            daily_return = Decimal("0.01") if index % 2 == 0 else Decimal("-0.005")
            equity *= Decimal(1) + daily_return
            values.append(equity)
        self.observations = tuple(
            EquityObservation(
                timestamp=self.start + timedelta(days=index),
                equity=value,
            )
            for index, value in enumerate(values)
        )
        self.completed = CompletedTradeSeries(
            source="NAUTILUS_NATIVE_COMPLETED_TRADES",
            evidence_sha256="a" * 64,
            settlement_currency="USDT",
            stable_native_sequence=True,
            native_completed_unit_count=0,
            realized_pnl_outcomes=(),
            realized_returns=(),
            unambiguous_net_after_cost=True,
            net_outcomes=(),
        )

    def _generate(self, **changes: object):
        ending = self.observations[-1].equity
        values = {
            "run_id": "r2-metrics-known-result",
            "scored_start": self.start,
            "scoring_end_exclusive": self.start + timedelta(days=30),
            "initial_capital": Decimal("1000"),
            "settlement_currency": "USDT",
            "equity_observations": self.observations,
            "completed_trades": self.completed,
            "benchmark_return": None,
            "sample_adequacy": SampleAdequacy.LOW_CONFIDENCE,
            "monte_carlo_status": MonteCarloStatus.MC_LOW_CONFIDENCE,
            "claim_scope": "INSTRUMENT_ONLY",
            "input_evidence_hashes": {"native_portfolio_snapshots.jsonl": "b" * 64},
            "financial_components": {
                "fees": Decimal("3"),
                "funding": Decimal("-1"),
                "realized_pnl": ending - Decimal("1000"),
                "unrealized_pnl": Decimal(0),
                "total_pnl": ending - Decimal("1000"),
            },
        }
        values.update(changes)
        return generate_performance_diagnostics(**values)

    def test_daily_marked_equity_is_the_single_official_metric_basis(self) -> None:
        result = self._generate()
        self.assertEqual(result.schema_version, 2)
        self.assertEqual(result.valuation_frequency, "DAILY_MARKED_PORTFOLIO_EQUITY_UTC")
        self.assertEqual(result.annualization_days, Decimal("365.2425"))
        self.assertEqual(result.daily_return_sample_count, 30)
        self.assertEqual(result.minimum_risk_sample_count, 30)
        self.assertEqual(result.native_statistics_role, NATIVE_STATISTICS_DIAGNOSTIC_ROLE)
        self.assertEqual(result.scientific_limitations, REQUIRED_SCIENTIFIC_LIMITATIONS)
        self.assertEqual(result.total_return.source, "OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY")
        self.assertEqual(result.cagr.source, "OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY")
        self.assertEqual(result.max_drawdown.source, "OFFICIAL_DAILY_MARKED_PORTFOLIO_EQUITY")
        self.assertEqual(result.sharpe.status, "CALCULATED")
        self.assertEqual(result.sortino.status, "CALCULATED")
        self.assertFalse(result.intraday_drawdown_captured)
        self.assertEqual(result.ending_equity.value, f"{self.observations[-1].equity:.8f}")

    def test_known_result_metrics_are_bound_to_independently_calculated_golden_values(self) -> None:
        """Catch a shared generator/validator mutation such as n-1 becoming n.

        These constants were derived directly from the 30-return fixture using
        the formulas written in SSOT, not by calling a project metric helper.
        """

        result = self._generate()
        self.assertEqual(result.ending_equity.value, "1076.87877676")
        self.assertEqual(result.total_return.value, "0.07687878")
        self.assertEqual(result.cagr.value, "1.46389937")
        self.assertEqual(result.max_drawdown.value, "0.00500000")
        self.assertEqual(result.sharpe.value, "6.26336571")
        self.assertEqual(result.sortino.value, "13.51374300")

    def test_small_sample_and_zero_variance_are_not_published_as_numbers(self) -> None:
        short = self.observations[:4]
        result = self._generate(
            scoring_end_exclusive=self.start + timedelta(days=3),
            equity_observations=short,
            financial_components=None,
        )
        self.assertEqual(result.sharpe.status, "UNDEFINED")
        self.assertIn("MINIMUM_30", result.sharpe.undefined_reason)
        flat = tuple(
            EquityObservation(timestamp=self.start + timedelta(days=i), equity=Decimal("1000"))
            for i in range(31)
        )
        result = self._generate(equity_observations=flat, financial_components=None)
        self.assertEqual(result.sharpe.undefined_reason, "UNDEFINED_ZERO_DAILY_RETURN_VARIANCE")
        self.assertEqual(result.sortino.undefined_reason, "UNDEFINED_ZERO_DOWNSIDE_DEVIATION")

    def test_nonpositive_daily_equity_makes_risk_metrics_explicitly_undefined(self) -> None:
        observations = list(self.observations)
        observations[10] = replace(observations[10], equity=Decimal(0))
        result = self._generate(
            equity_observations=tuple(observations),
            financial_components=None,
        )
        self.assertEqual(result.daily_return_sample_count, 0)
        self.assertEqual(result.sharpe.status, "UNDEFINED")
        self.assertEqual(result.sortino.status, "UNDEFINED")
        self.assertIn("NON_POSITIVE_PREVIOUS_DAILY_EQUITY", result.sharpe.undefined_reason)
        self.assertIn("NON_POSITIVE_PREVIOUS_DAILY_EQUITY", result.sortino.undefined_reason)

    def test_warmup_or_missing_daily_boundary_is_rejected(self) -> None:
        warmup = (
            EquityObservation(timestamp=self.start - timedelta(days=1), equity=Decimal("1000")),
            *self.observations,
        )
        with self.assertRaises(ResearchError):
            self._generate(equity_observations=warmup)
        with self.assertRaises(ResearchError):
            self._generate(equity_observations=self.observations[:-1])

    def test_cash_only_or_inconsistent_pnl_cannot_define_total_portfolio_return(self) -> None:
        components = {
            "fees": Decimal("3"),
            "funding": Decimal("-1"),
            "realized_pnl": Decimal("10"),
            "unrealized_pnl": Decimal("5"),
            "total_pnl": Decimal("14"),
        }
        with self.assertRaises(ResearchError):
            self._generate(financial_components=components)

    def test_native_statistics_and_sample_threshold_cannot_override_official_metrics(self) -> None:
        with self.assertRaises(TypeError):
            self._generate(
                native_metrics={
                    "total_return": "999",
                    "cagr": "999",
                    "max_drawdown": "0",
                },
            )
        with self.assertRaises(TypeError):
            self._generate(minimum_risk_sample_count=2)

    def test_rehashed_official_metric_tamper_fails_the_metric_contract(self) -> None:
        result = self._generate()
        tampered_total = replace(result.total_return, value="999.00000000")
        material = result.material_payload()
        material["total_return"] = tampered_total
        with self.assertRaises(ResearchError) as raised:
            replace(
                result,
                total_return=tampered_total,
                diagnostics_id=canonical_sha256(material),
            )
        self.assertEqual(raised.exception.code, "PERFORMANCE_METRICS_INVALID")

    def test_rehashed_basis_or_limitation_tamper_fails_the_metric_contract(self) -> None:
        result = self._generate()
        for field_name, tampered_value in (
            ("minimum_risk_sample_count", 2),
            ("scientific_limitations", result.scientific_limitations[:-1]),
        ):
            with self.subTest(field_name=field_name):
                material = result.material_payload()
                material[field_name] = tampered_value
                with self.assertRaises(ResearchError) as raised:
                    replace(
                        result,
                        **{
                            field_name: tampered_value,
                            "diagnostics_id": canonical_sha256(material),
                        },
                    )
                self.assertEqual(raised.exception.code, "PERFORMANCE_METRICS_INVALID")

    def test_spot_daily_equity_is_independently_replayed_from_fills_and_bars(self) -> None:
        day_ns = 86_400_000_000_000
        timestamps = (day_ns, 2 * day_ns, 3 * day_ns)
        bars = [
            {
                "bar_type": "BTCUSDT.BINANCE-1-DAY-LAST-INTERNAL",
                "ts_event": timestamp,
                "ts_init": timestamp,
                "callback_clock_ns": timestamp,
                "close": close,
            }
            for timestamp, close in zip(timestamps, ("10", "12", "11"), strict=True)
        ]
        fills = [
            {
                "fill_index": "0",
                "event_id": "fill-1",
                "instrument_id": "BTCUSDT.BINANCE",
                "order_type": "MARKET",
                "order_side": "BUY",
                "last_qty": "2",
                "last_px": "10",
                "commission": "1 USDT",
                "currency": "USDT",
                "ts_event": str(day_ns + 1),
                "ts_init": str(day_ns + 1),
            },
        ]
        equity, balances = _spot_daily_ledger_equity(
            fills=fills,
            bars=bars,
            valuation_timestamps=timestamps,
            instrument_id="BTCUSDT.BINANCE",
            base_currency="BTC",
            quote_currency="USDT",
            initial_quote_balance=Decimal("100"),
        )
        self.assertEqual(equity, {day_ns: Decimal("100"), 2 * day_ns: Decimal("103"), 3 * day_ns: Decimal("101")})
        self.assertEqual(balances[2 * day_ns], {"BTC": Decimal("2"), "USDT": Decimal("79")})
        tampered = [dict(item) for item in bars]
        tampered[1]["close"] = "999"
        altered, _ = _spot_daily_ledger_equity(
            fills=fills,
            bars=tampered,
            valuation_timestamps=timestamps,
            instrument_id="BTCUSDT.BINANCE",
            base_currency="BTC",
            quote_currency="USDT",
            initial_quote_balance=Decimal("100"),
        )
        self.assertNotEqual(altered[2 * day_ns], equity[2 * day_ns])

    def test_stale_or_wrong_currency_snapshot_money_is_rejected(self) -> None:
        with self.assertRaises(ResearchError):
            _money_total([{"amount": "1", "currency": "BTC"}], "USDT")
        with self.assertRaises(ResearchError):
            _money_total(
                [
                    {"amount": "1", "currency": "USDT"},
                    {"amount": "2", "currency": "USDT"},
                ],
                "USDT",
            )


if __name__ == "__main__":
    unittest.main()
