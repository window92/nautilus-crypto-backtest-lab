from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from pathlib import Path
import tempfile
from decimal import Decimal
from unittest.mock import patch

from nautilus_trader.model import BarType
from nautilus_trader.model import InstrumentId

from crypto_lab.config import MarketProfile
from crypto_lab.config import RunPurpose
from crypto_lab.config import SourceRevision
from crypto_lab.strategies import StrategySpec
from crypto_lab.strategies import StrategyPlan
from crypto_lab.strategies import OrderIntent
from crypto_lab.strategies import registered_strategy_ids
from crypto_lab.strategies import create_registered_strategy
from crypto_lab.strategies import resolve_registered_strategy_identity
from crypto_lab.runner import LabRunRequest
from tests.m1_helpers import SPOT_ID
from tests.m1_helpers import a4_bars
from tests.m1_helpers import make_request


REGISTRATION = "qualification_fixture_first_eligible_bar_v1"


def source() -> SourceRevision:
    return SourceRevision(
        repository="https://example.invalid/repository.git",
        branch_ref="main",
        git_commit="1" * 40,
        git_tree="2" * 40,
        clean_worktree=True,
        captured_at_utc=datetime(2026, 8, 22, tzinfo=UTC),
    )


def strategy_spec(*, quantity: str = "0.00100") -> StrategySpec:
    return StrategySpec(
        strategy_id="public-boundary-qualification-fixture",
        strategy_version="1",
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        instrument_id="BTCUSDT.BINANCE",
        signal_bar_types=("BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",),
        parameters={
            "fixture_purpose": "PUBLIC_BOUNDARY_QUALIFICATION_ONLY",
            "network_access": "FORBIDDEN",
            "order_quantity": quantity,
            "order_side": "BUY",
            "profitability_claim": "INELIGIBLE",
            "trigger": "FIRST_SCORING_ELIGIBLE_BAR",
        },
        indicator_definitions=(),
        warmup_requirement="NO_INDICATOR_WARMUP",
        sizing_rule="FIXED_EXPLICIT_QUALIFICATION_QUANTITY",
        entry_rule="FIRST_SCORING_ELIGIBLE_BAR_ONLY",
        exit_rule="NO_EXIT_QUALIFICATION_FIXTURE",
        conflict_rule="FIRST_ELIGIBLE_INTENT",
        terminal_behavior="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
        market_order_time_in_force="GTC",
    )


class Aud001StrategyIdentityTests(unittest.TestCase):
    def test_previous_same_identity_different_fill_schedule_exploit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            plan_with_intent = StrategyPlan(
                intents_by_bar_ns={
                    60_000_000_000: (
                        OrderIntent(
                            side="BUY",
                            quantity="1",
                            order_type="MARKET",
                            reason="adversarial prior exploit",
                        ),
                    ),
                },
                conflict_rule="FIRST_ELIGIBLE_INTENT",
                qualification_attempt_all_intents=False,
            )
            base = make_request(
                Path(temporary),
                run_id="aud001-exploit",
                profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                data=a4_bars(SPOT_ID),
                plan=plan_with_intent,
                scoring_start_ns=0,
                scoring_end_ns=180_000_000_000,
            )
            official_config = replace(
                base.lab_run_config,
                run_purpose=RunPurpose.OFFICIAL,
                research_protocol_id="a" * 64,
            )
            plans = (
                plan_with_intent,
                StrategyPlan(
                    intents_by_bar_ns={},
                    conflict_rule="FIRST_ELIGIBLE_INTENT",
                    qualification_attempt_all_intents=False,
                ),
            )
            self.assertNotEqual(plans[0].strategy_plan_sha256, plans[1].strategy_plan_sha256)
            for plan in plans:
                with self.subTest(plan=plan.strategy_plan_sha256):
                    with self.assertRaisesRegex(ValueError, "forbidden outside QUALIFICATION"):
                        LabRunRequest(
                            lab_run_config=official_config,
                            source_revision=base.source_revision,
                            strategy_spec=base.strategy_spec,
                            dataset_release=base.dataset_release,
                            instrument=base.instrument,
                            data=base.data,
                            strategy_plan=plan,
                            evidence_root=base.evidence_root,
                            qualification_control=base.qualification_control,
                        )
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_only_closed_registered_identifiers_are_accepted(self) -> None:
        self.assertEqual(registered_strategy_ids(), (REGISTRATION,))
        with self.assertRaises(ValueError):
            resolve_registered_strategy_identity(
                "module:dynamic_callable",
                strategy_spec=strategy_spec(),
                source_revision=source(),
            )

    def test_parameter_or_source_implementation_change_changes_identity(self) -> None:
        first = resolve_registered_strategy_identity(
            REGISTRATION,
            strategy_spec=strategy_spec(),
            source_revision=source(),
        )
        parameter_change = resolve_registered_strategy_identity(
            REGISTRATION,
            strategy_spec=strategy_spec(quantity="0.00200"),
            source_revision=source(),
        )
        source_change = resolve_registered_strategy_identity(
            REGISTRATION,
            strategy_spec=strategy_spec(),
            source_revision=replace(source(), git_commit="3" * 40),
        )
        self.assertNotEqual(first.strategy_identity_sha256, parameter_change.strategy_identity_sha256)
        self.assertNotEqual(first.strategy_identity_sha256, source_change.strategy_identity_sha256)
        self.assertEqual(
            first,
            resolve_registered_strategy_identity(
                REGISTRATION,
                strategy_spec=strategy_spec(),
                source_revision=source(),
            ),
        )
        self.assertFalse(first.profitability_claim_eligible)
        self.assertTrue(first.qualification_fixture_only)

    def test_registered_official_strategy_constructs_without_any_strategy_plan(self) -> None:
        spec = strategy_spec()
        identity = resolve_registered_strategy_identity(
            REGISTRATION,
            strategy_spec=spec,
            source_revision=source(),
        )
        with patch(
            "crypto_lab.strategies.base.StrategyPlan",
            side_effect=AssertionError("Qualification StrategyPlan entered Official construction"),
        ):
            strategy = create_registered_strategy(
                identity,
                strategy_spec=spec,
                source_revision=source(),
                configuration={
                    "instrument_id": InstrumentId.from_str("BTCUSDT.BINANCE"),
                    "bar_type": BarType.from_str(
                        "BTCUSDT.BINANCE-1-MINUTE-LAST-EXTERNAL",
                    ),
                    "profile": MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                    "scoring_start_ns": 0,
                    "scoring_end_exclusive_ns": 120_000_000_000,
                    "effective_insert_latency_ns": 1,
                    "size_precision": 5,
                    "min_quantity": Decimal("0.00001"),
                    "max_quantity": Decimal("1000.00000"),
                    "size_increment": Decimal("0.00001"),
                    "initial_capital_amount": Decimal("1000.00"),
                    "initial_capital_currency": "USDT",
                },
            )
        self.assertIsNone(strategy._plan)


if __name__ == "__main__":
    unittest.main()
