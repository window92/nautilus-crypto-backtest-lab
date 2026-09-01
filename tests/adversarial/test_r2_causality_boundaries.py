from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.model import Bar
from nautilus_trader.model import BarType
from nautilus_trader.model import MarkPriceUpdate
from nautilus_trader.model import Price

from crypto_lab.checker import CheckerOutcome
from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import MarketProfile
from crypto_lab.nautilus_config import add_venue_from_config
from crypto_lab.nautilus_config import to_nautilus_engine_config
from crypto_lab.runner import _bind_engine_callback_window_evidence
from crypto_lab.runner import run_lab
from crypto_lab.runner import select_engine_data_window
from crypto_lab.status import FailureCode
from crypto_lab.strategies import TSMOM_FULL_REGISTRATION_ID
from crypto_lab.strategies import create_registered_strategy
from crypto_lab.strategies import locked_weekly_tsmom_strategy_spec
from crypto_lab.strategies import resolve_registered_strategy_identity
from crypto_lab.strategies.base import signal_interval_is_scoring_eligible
from tests.m1_helpers import PERP_ID
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import complete_perpetual_roles
from tests.m1_helpers import intent
from tests.m1_helpers import make_bars
from tests.m1_helpers import make_instrument
from tests.m1_helpers import make_request
from tests.m1_helpers import plan
from tests.m1_helpers import source_revision

ROOT = Path(__file__).resolve().parents[2]

MINUTE_NS = 60_000_000_000
DAY_NS = 86_400_000_000_000
T0 = 32 * DAY_NS  # 1970-02-02T00:00:00Z, a Monday.


def _weekly_minute_bars(profile: MarketProfile, *, days: int = 42) -> tuple[Bar, ...]:
    instrument_id = (
        SPOT_ID
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else PERP_ID
    )
    rows: list[tuple[int, str, str, str, str]] = []
    for minute in range(1, days * 1_440 + 2):
        day = (minute - 1) // 1_440
        close = Decimal(100 + day) if day < 32 else Decimal(50)
        rows.append(
            (
                minute * MINUTE_NS,
                f"{close:.2f}",
                f"{close + Decimal('0.10'):.2f}",
                f"{close - Decimal('0.10'):.2f}",
                f"{close:.2f}",
            ),
        )
    volume = "1000" if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else "1000"
    return make_bars(instrument_id, tuple(rows), volume=volume)


def _run_weekly_candidate(profile: MarketProfile) -> dict[str, object]:
    bars = _weekly_minute_bars(profile)
    instrument_id = bars[0].bar_type.instrument_id
    seed = (
        bars[:3]
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else complete_perpetual_roles(bars[:3])
    )
    with tempfile.TemporaryDirectory() as temporary:
        request = make_request(
            Path(temporary),
            run_id=f"r2-weekly-boundary-{profile.value.lower()}",
            profile=profile,
            data=seed,
            plan=plan({}),
            scoring_start_ns=0,
            scoring_end_ns=3 * MINUTE_NS,
        )
        instrument = make_instrument(profile)
        engine = BacktestEngine(
            to_nautilus_engine_config(request.lab_run_config.nautilus_engine_config),
        )
        add_venue_from_config(engine, request.lab_run_config.nautilus_venue_config)
        engine.add_instrument(instrument)
        spec = locked_weekly_tsmom_strategy_spec(TSMOM_FULL_REGISTRATION_ID, profile)
        revision = source_revision()
        identity = resolve_registered_strategy_identity(
            TSMOM_FULL_REGISTRATION_ID,
            strategy_spec=spec,
            source_revision=revision,
        )
        strategy = create_registered_strategy(
            identity,
            strategy_spec=spec,
            source_revision=revision,
            configuration={
                "instrument_id": instrument_id,
                "bar_type": BarType.from_str(spec.signal_bar_types[0]),
                "execution_bar_type": bars[0].bar_type,
                "profile": profile,
                "scoring_start_ns": T0,
                "scoring_end_exclusive_ns": 41 * DAY_NS,
                "effective_insert_latency_ns": MINUTE_NS,
                "size_precision": instrument.size_precision,
                "min_quantity": None,
                "max_quantity": None,
                "size_increment": instrument.size_increment.as_decimal(),
                "initial_capital_amount": Decimal("1000"),
                "initial_capital_currency": "USDT",
            },
        )
        engine.add_strategy(strategy)
        data: list[object] = list(bars)
        if profile is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
            data = [
                item
                for bar in bars
                for item in (
                    MarkPriceUpdate(
                        instrument_id,
                        Price.from_str(str(bar.close)),
                        int(bar.ts_init),
                        int(bar.ts_init),
                    ),
                    bar,
                )
            ]
        engine.add_data(data)
        engine.run(start=0)
        observations = json.loads(json.dumps(strategy.observations))
        engine.dispose()
    return observations


def _small_scored_run(
    root: Path,
    profile: MarketProfile,
    *,
    suffix: str,
):
    instrument_id = (
        SPOT_ID
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else PERP_ID
    )
    bars = make_bars(
        instrument_id,
        (
            (MINUTE_NS, "100.00", "101.00", "99.00", "100.00"),
            (2 * MINUTE_NS, "110.00", "111.00", "109.00", "110.00"),
            (3 * MINUTE_NS, "120.00", "121.00", "119.00", "120.00"),
            (4 * MINUTE_NS, "130.00", "131.00", "129.00", "130.00"),
            (5 * MINUTE_NS, "140.00", "141.00", "139.00", "140.00"),
        ),
    )
    data = (
        bars
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else complete_perpetual_roles(bars)
    )
    return run_lab(
        make_request(
            root,
            run_id=f"r2-signal-tamper-{profile.value.lower()}-{suffix}",
            profile=profile,
            data=data,
            plan=plan(
                {
                    2 * MINUTE_NS: (
                        intent("BUY", "1", "R2_VALID_FULL_INTERVAL"),
                    ),
                },
            ),
            scoring_start_ns=MINUTE_NS,
            scoring_end_ns=4 * MINUTE_NS,
        ),
    )


class R2CausalityBoundaryTests(unittest.TestCase):
    def test_qualification_request_never_infers_authority_from_package_location(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("crypto_lab.runner.ROOT", root / "missing-installed-package-root"):
                result = _small_scored_run(
                    root,
                    MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                    suffix="explicit-repository-root",
                )
        self.assertEqual(result.checker_outcome, CheckerOutcome.CHECK_PASS)

    def test_full_signal_interval_not_decision_timestamp_controls_eligibility(self) -> None:
        self.assertFalse(
            signal_interval_is_scoring_eligible(
                interval_start_ns=T0 - DAY_NS,
                interval_end_exclusive_ns=T0,
                scoring_start_ns=T0,
                scoring_end_exclusive_ns=T0 + 2 * DAY_NS,
            ),
        )
        self.assertTrue(
            signal_interval_is_scoring_eligible(
                interval_start_ns=T0,
                interval_end_exclusive_ns=T0 + DAY_NS,
                scoring_start_ns=T0,
                scoring_end_exclusive_ns=T0 + 2 * DAY_NS,
            ),
        )

    def test_weekly_first_eligible_signal_is_one_week_after_scoring_start_for_both_profiles(
        self,
    ) -> None:
        for profile in (
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        ):
            with self.subTest(profile=profile.value):
                observations = _run_weekly_candidate(profile)
                signals = observations["weekly_decisions"]
                self.assertTrue(signals)
                first = signals[0]
                self.assertEqual(first["signal_bar_interval_start_ns"], T0 + 6 * DAY_NS)
                self.assertEqual(
                    first["signal_bar_interval_end_exclusive_ns"],
                    T0 + 7 * DAY_NS,
                )
                self.assertEqual(first["decision_timestamp_ns"], T0 + 7 * DAY_NS)
                self.assertFalse(
                    any(
                        item["decision_timestamp_ns"] == T0
                        for item in observations["submitted_intents"]
                    ),
                    "decision_timestamp=T0 must not promote [T0-day,T0)",
                )
                self.assertTrue(
                    all(
                        item["signal_bar_interval_start_ns"] >= T0
                        for item in signals
                    ),
                )

    def test_checker_rejects_submitted_warmup_interval_for_spot_and_perpetual(self) -> None:
        for profile in (
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        ):
            with (
                self.subTest(profile=profile.value),
                tempfile.TemporaryDirectory() as temporary,
            ):
                result = _small_scored_run(Path(temporary), profile, suffix="start")
                self.assertEqual(result.checker_outcome, CheckerOutcome.CHECK_PASS)
                result_path = result.evidence_dir / "nautilus_result.json"
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                execution_window = payload["execution_data_window"]
                callback_summary = payload["strategy_observations"][
                    "engine_data_callbacks"
                ]
                self.assertTrue(
                    execution_window["engine_received_post_boundary_data_derived"],
                )
                self.assertFalse(execution_window["engine_received_post_boundary_data"])
                self.assertEqual(
                    execution_window["engine_received_post_boundary_data_basis"],
                    "SELECTED_ENGINE_INPUTS_AND_ACTUAL_STRATEGY_CALLBACK_COUNTERS",
                )
                self.assertEqual(
                    execution_window["engine_callback_summary"],
                    callback_summary,
                )
                self.assertGreater(callback_summary["counts"]["Bar"], 0)
                submitted = payload["strategy_observations"]["submitted_intents"][0]
                submitted["signal_bar_interval_start_ns"] = 0
                result_path.write_text(
                    json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                report = check_evidence_directory(
                    result.evidence_dir,
                    repository_root=ROOT,
                    official_source_required=False,
                    source_revision_current_head_required=False,
                )
                self.assertEqual(report.outcome, CheckerOutcome.CHECK_FAIL)
                self.assertIn(
                    FailureCode.WARMUP_SCORING_ELIGIBILITY_VIOLATION.value,
                    report.failure_codes,
                )

    def test_checker_rejects_signal_end_after_scoring_boundary(self) -> None:
        for profile in (
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        ):
            with (
                self.subTest(profile=profile.value),
                tempfile.TemporaryDirectory() as temporary,
            ):
                result = _small_scored_run(
                    Path(temporary),
                    profile,
                    suffix="end",
                )
                self.assertEqual(result.checker_outcome, CheckerOutcome.CHECK_PASS)
                result_path = result.evidence_dir / "nautilus_result.json"
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                submitted = payload["strategy_observations"]["submitted_intents"][0]
                submitted["signal_bar_interval_end_exclusive_ns"] = (
                    4 * MINUTE_NS + 1
                )
                result_path.write_text(
                    json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                report = check_evidence_directory(
                    result.evidence_dir,
                    repository_root=ROOT,
                    official_source_required=False,
                    source_revision_current_head_required=False,
                )
                self.assertEqual(report.outcome, CheckerOutcome.CHECK_FAIL)
                self.assertIn(
                    FailureCode.LOOKAHEAD_DETECTED.value,
                    report.failure_codes,
                )

    def test_post_boundary_flag_is_derived_from_inputs_and_actual_callback_counters(self) -> None:
        bars = make_bars(
            SPOT_ID,
            (
                (MINUTE_NS, "100.00", "101.00", "99.00", "100.00"),
                (2 * MINUTE_NS, "100.00", "101.00", "99.00", "100.00"),
                (3 * MINUTE_NS, "100.00", "101.00", "99.00", "100.00"),
            ),
        )
        _selected, window = select_engine_data_window(
            bars,
            warmup_start_ns=0,
            scoring_end_exclusive_ns=2 * MINUTE_NS,
        )
        self.assertEqual(window["selected_post_boundary_data_count"], 0)
        observations = {
            "engine_data_callbacks": {
                "counts": {"Bar": 1, "MarkPriceUpdate": 0, "FundingRateUpdate": 0},
                "latest_ts_init_by_type": {
                    "Bar": 2 * MINUTE_NS + 1,
                    "MarkPriceUpdate": None,
                    "FundingRateUpdate": None,
                },
                "post_boundary_count": 1,
                "post_boundary_samples": [
                    {
                        "event_type": "Bar",
                        "instrument_id": str(SPOT_ID),
                        "ts_init": 2 * MINUTE_NS + 1,
                    },
                ],
            },
        }
        bound = _bind_engine_callback_window_evidence(window, observations)
        self.assertTrue(bound["engine_callback_summary_valid"])
        self.assertEqual(bound["engine_callback_post_boundary_count"], 1)
        self.assertTrue(bound["engine_received_post_boundary_data"])
        self.assertTrue(bound["engine_received_post_boundary_data_derived"])
        self.assertEqual(
            bound["engine_received_post_boundary_data_basis"],
            "SELECTED_ENGINE_INPUTS_AND_ACTUAL_STRATEGY_CALLBACK_COUNTERS",
        )


if __name__ == "__main__":
    unittest.main()
