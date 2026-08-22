from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from crypto_lab.config import MarketProfile
from crypto_lab.config import SourceRevision
from crypto_lab.data import DatasetRelease
from crypto_lab.m3 import PERPETUAL_BASE_RELEASE_ID
from crypto_lab.m3 import SPOT_BASE_RELEASE_ID
from crypto_lab.m3 import build_m3_request
from crypto_lab.m3 import qualification_dataset_release


def source_revision() -> SourceRevision:
    return SourceRevision(
        repository="https://github.com/window92/nautilus-crypto-backtest-lab.git",
        branch_ref="main",
        git_commit="0" * 40,
        git_tree="1" * 40,
        clean_worktree=True,
        captured_at_utc=datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
    )


class M3DatasetInterfaceTests(unittest.TestCase):
    def test_public_run_request_receives_strict_dataset_release_without_conversion(self) -> None:
        for profile, base_id in (
            (MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY, SPOT_BASE_RELEASE_ID),
            (
                MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
                PERPETUAL_BASE_RELEASE_ID,
            ),
        ):
            release = qualification_dataset_release(profile)
            with tempfile.TemporaryDirectory() as temporary:
                request = build_m3_request(
                    release,
                    source_revision=source_revision(),
                    evidence_root=Path(temporary),
                    run_id=f"m3-interface-{profile.value.lower()}",
                )
            self.assertIs(request.dataset_release, release)
            self.assertIsInstance(request.dataset_release, DatasetRelease)
            self.assertEqual(
                request.strategy_spec.parameters["base_dataset_release_id"],
                base_id,
            )
            self.assertEqual(request.lab_run_config.dataset_release_id, release.dataset_release_id)
            self.assertIsNone(request.instrument)
            self.assertEqual(request.data, ())

    def test_profile_configs_bind_runtime_fee_mark_funding_and_limits_explicitly(self) -> None:
        spot = qualification_dataset_release(MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY)
        perp = qualification_dataset_release(
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        )
        with tempfile.TemporaryDirectory() as temporary:
            spot_request = build_m3_request(
                spot,
                source_revision=source_revision(),
                evidence_root=Path(temporary),
                run_id="m3-spot-contract",
            )
            perp_request = build_m3_request(
                perp,
                source_revision=source_revision(),
                evidence_root=Path(temporary),
                run_id="m3-perp-contract",
            )
        self.assertEqual(spot_request.lab_run_config.nautilus_venue_config.account_type, "CASH")
        self.assertFalse(spot_request.lab_run_config.nautilus_venue_config.allow_cash_borrowing)
        self.assertFalse(spot_request.lab_run_config.nautilus_engine_config.portfolio.use_mark_prices)
        self.assertEqual(spot_request.lab_run_config.mark_binding, "NOT_APPLICABLE")
        self.assertEqual(spot_request.lab_run_config.funding_binding, "NOT_APPLICABLE")
        self.assertEqual(perp_request.lab_run_config.nautilus_venue_config.account_type, "MARGIN")
        self.assertEqual(perp_request.lab_run_config.nautilus_venue_config.oms_type, "NETTING")
        self.assertFalse(perp_request.lab_run_config.nautilus_venue_config.liquidation_enabled)
        self.assertTrue(perp_request.lab_run_config.nautilus_engine_config.portfolio.use_mark_prices)
        self.assertEqual(perp_request.lab_run_config.mark_binding, perp.mark_data_identity)
        self.assertEqual(perp_request.lab_run_config.funding_binding, perp.funding_data_identity)
        for request in (spot_request, perp_request):
            self.assertEqual(request.lab_run_config.fee_assumption.maker_fee, request.lab_run_config.fee_assumption.taker_fee)
            self.assertEqual(str(request.lab_run_config.fee_assumption.taker_fee), "0.001")
            self.assertEqual(
                request.lab_run_config.nautilus_venue_config.latency_model.effective_insert_latency_nanos,
                60_000_000_000,
            )
