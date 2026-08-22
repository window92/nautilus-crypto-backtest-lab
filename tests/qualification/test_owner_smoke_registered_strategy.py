"""Qualification checks for the public registered OWNER_SMOKE strategy boundary."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC
from datetime import datetime

from crypto_lab.config import MarketProfile
from crypto_lab.config import SourceRevision
from crypto_lab.hashing import canonical_sha256
from crypto_lab.strategies import locked_sma20_strategy_spec
from crypto_lab.strategies import resolve_registered_strategy_identity


REGISTRATION = "btcusdt_daily_price_vs_sma20_trend_v1"


def _source(commit: str = "1" * 40) -> SourceRevision:
    return SourceRevision(
        repository="https://github.com/window92/nautilus-crypto-backtest-lab.git",
        branch_ref="main",
        git_commit=commit,
        git_tree="2" * 40,
        clean_worktree=True,
        captured_at_utc=datetime(2026, 8, 22, tzinfo=UTC),
    )


class OwnerSmokeRegisteredStrategyQualificationTests(unittest.TestCase):
    def test_both_locked_profiles_resolve_through_the_public_registry(self) -> None:
        for profile in MarketProfile:
            spec = locked_sma20_strategy_spec(profile)
            identity = resolve_registered_strategy_identity(
                REGISTRATION,
                strategy_spec=spec,
                source_revision=_source(),
            )
            self.assertFalse(identity.qualification_fixture_only)
            self.assertTrue(identity.profitability_claim_eligible)
            self.assertEqual(identity.strategy_spec_id, spec.strategy_spec_id)
            self.assertEqual(identity.parameters_sha256, canonical_sha256(dict(spec.parameters)))
            self.assertEqual(len(identity.implementation_code_sha256), 64)

    def test_material_parameter_mutation_changes_identity_and_cannot_reuse_locked_spec(self) -> None:
        spec = locked_sma20_strategy_spec(MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY)
        original = resolve_registered_strategy_identity(
            REGISTRATION,
            strategy_spec=spec,
            source_revision=_source(),
        )
        parameters = dict(spec.parameters)
        parameters["sma_lookback"] = "21"
        mutated_spec = replace(spec, parameters=parameters)
        mutated = resolve_registered_strategy_identity(
            REGISTRATION,
            strategy_spec=mutated_spec,
            source_revision=_source(),
        )
        self.assertNotEqual(original.strategy_spec_id, mutated.strategy_spec_id)
        self.assertNotEqual(original.strategy_identity_sha256, mutated.strategy_identity_sha256)

    def test_source_revision_mutation_changes_registered_identity(self) -> None:
        spec = locked_sma20_strategy_spec(
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        )
        first = resolve_registered_strategy_identity(
            REGISTRATION,
            strategy_spec=spec,
            source_revision=_source(),
        )
        second = resolve_registered_strategy_identity(
            REGISTRATION,
            strategy_spec=spec,
            source_revision=_source("3" * 40),
        )
        self.assertNotEqual(first.strategy_identity_sha256, second.strategy_identity_sha256)


if __name__ == "__main__":
    unittest.main()
