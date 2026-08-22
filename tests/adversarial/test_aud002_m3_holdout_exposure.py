from __future__ import annotations

import unittest
import subprocess
import tempfile
from datetime import UTC
from datetime import datetime
from pathlib import Path

from crypto_lab.config import MarketProfile
from crypto_lab.exposure import AuthoritativeExposureResolver
from crypto_lab.research import ResearchError
from crypto_lab.research import ResultExposure
from crypto_lab.research import UtcInterval


ROOT = Path(__file__).resolve().parents[2]
MUTATIONS = (
    "new_research_family",
    "new_hypothesis_id",
    "renamed_strategy",
    "new_seed",
    "new_branch",
    "different_dataset_release_label",
    "descendant_strategy",
    "new_protocol_id",
)


class Aud002M3HoldoutExposureTests(unittest.TestCase):
    def test_committed_m3_authority_replacement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aud002-m3-authority-") as temporary:
            repository = Path(temporary).resolve()
            authority = repository / "evidence/m3/m3-acceptance-001/qualification-manifest.json"
            authority.parent.mkdir(parents=True)
            subprocess.run(
                ["git", "init", "--initial-branch=main"],
                cwd=repository,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "AUD-002 Test"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "aud002@example.invalid"],
                cwd=repository,
                check=True,
            )
            authority.write_text('{"authority":"original"}\n', encoding="utf-8")
            subprocess.run(["git", "add", authority], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-m", "record original M3 authority"],
                cwd=repository,
                check=True,
                capture_output=True,
            )

            resolver = AuthoritativeExposureResolver(repository_root=repository)
            resolver._require_committed_authority_unchanged(authority)

            authority.write_text('{"authority":"replacement"}\n', encoding="utf-8")
            subprocess.run(["git", "add", authority], cwd=repository, check=True)
            subprocess.run(
                ["git", "commit", "-m", "replace M3 authority"],
                cwd=repository,
                check=True,
                capture_output=True,
            )
            with self.assertRaises(ResearchError) as caught:
                resolver._require_committed_authority_unchanged(authority)
            self.assertEqual(caught.exception.code, "HOLDOUT_HISTORY_VIOLATION")
            self.assertIn("authority was replaced", str(caught.exception))

    def test_all_sixteen_spot_and_perpetual_relabels_are_rejected(self) -> None:
        resolver = AuthoritativeExposureResolver(repository_root=ROOT)
        profiles = (
            (
                "spot",
                MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                "BTCUSDT.BINANCE",
                "2024-12-31T23:58:00Z",
                "2025-01-01T00:02:00Z",
            ),
            (
                "perpetual",
                MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
                "BTCUSDT-PERP.BINANCE",
                "2025-01-01T07:56:00Z",
                "2025-01-01T08:04:00Z",
            ),
        )
        rejected: list[tuple[str, str]] = []
        for label, profile, instrument, start, end in profiles:
            interval = UtcInterval(
                start_inclusive=datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(UTC),
                end_exclusive=datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(UTC),
            )
            for index, mutation in enumerate(MUTATIONS):
                candidate = ResultExposure(
                    trial_id=f"{label}-{mutation}",
                    market_profile=profile,
                    instrument_id=instrument,
                    scored_interval=interval,
                    research_family_id=f"new-family-{index}",
                    hypothesis_lineage=(f"new-hypothesis-{index}",),
                    strategy_lineage=(f"renamed-descendant-{index}",),
                    dataset_release_id=f"{index + 1:x}" * 64,
                    first_exposure_at_utc=datetime(2026, 8, 22, tzinfo=UTC),
                    exposure_type=f"FINAL_HOLDOUT_{mutation}",
                    evidence_reference=f"runs/{label}/{mutation}",
                    source_branch=f"new-branch-{index}",
                    source_commit="a" * 40,
                    seed=index + 100,
                    result_bearing=True,
                )
                with self.subTest(profile=label, mutation=mutation):
                    with self.assertRaises(ResearchError) as caught:
                        resolver.require_fresh(candidate, history=None)
                    self.assertEqual(caught.exception.code, "HOLDOUT_ALREADY_CONSUMED")
                    rejected.append((label, mutation))
        self.assertEqual(len(rejected), 16)


if __name__ == "__main__":
    unittest.main()
