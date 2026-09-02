from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.result_status import FinancialResultStatus
from crypto_lab.result_status import HistoricalRunStatus
from crypto_lab.result_status import HistoricalStatusReason
from crypto_lab.result_status import R2_CLAIM_HOLDOUT_SUPERSEDED_RESULTS
from crypto_lab.result_status import ReplacementRequirement
from crypto_lab.result_status import ResultNotActiveError
from crypto_lab.result_status import load_historical_result_registry
from crypto_lab.result_status import require_active_result
from scripts.build_r2_claim_holdout_supersession_status import (
    ClaimHoldoutSupersessionBuildError,
)
from scripts.build_r2_claim_holdout_supersession_status import (
    EXPECTED_CLAIM_HOLDOUT_EVIDENCE_IDENTITIES,
)
from scripts.build_r2_claim_holdout_supersession_status import build_registry
from tests.helpers import initialize_product_repository


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE = "1" * 40
RECORDED = "2026-09-01T11:00:00Z"
LEAVES = (
    "component_validation.json",
    "evidence_manifest.json",
    "official_seal.json",
    "runtime_identity.json",
    "source_revision.json",
    "status.json",
)


def _copy_fixture(root: Path) -> None:
    initialize_product_repository(root)
    for relative in EXPECTED_CLAIM_HOLDOUT_EVIDENCE_IDENTITIES:
        target = root / relative
        target.mkdir(parents=True)
        for name in LEAVES:
            shutil.copyfile(REPOSITORY / relative / name, target / name)


def _recompute(value: dict[str, object]) -> bytes:
    records = value["records"]
    assert isinstance(records, list)
    value["record_count"] = len(records)
    value["records_identity"] = canonical_sha256(records)
    value.pop("registry_identity", None)
    value["registry_identity"] = canonical_sha256(value)
    return canonical_json_bytes(value) + b"\n"


class R2ClaimHoldoutSupersessionTests(unittest.TestCase):
    def test_partial_retry_010_scope_is_exact_and_only_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy_fixture(root)
            payload = build_registry(
                repository_root=root,
                source_commit=SOURCE,
                recorded_at_utc=RECORDED,
            )
            registry_path = root / "claim-holdout-status.json"
            registry_path.write_bytes(payload)
            registry = load_historical_result_registry(registry_path)
            self.assertEqual(len(registry.records), 6)
            for record in registry.records:
                self.assertIs(record.historical_run_status, HistoricalRunStatus.SUPERSEDED)
                self.assertIs(record.financial_result_status, FinancialResultStatus.SUPERSEDED)
                self.assertIs(
                    record.reason_code,
                    HistoricalStatusReason.CLAIM_HOLDOUT_SEMANTIC_SUPERSESSION,
                )
                self.assertIs(
                    record.replacement_requirement,
                    ReplacementRequirement.CLAIM_HOLDOUT_SEMANTIC_FIX_AND_REBUILD,
                )
                with self.assertRaises(ResultNotActiveError):
                    require_active_result(
                        root / record.path,
                        repository_root=root,
                        registry_paths=(registry_path,),
                    )

    def test_missing_pair_wrong_reason_and_rehashed_evidence_tamper_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy_fixture(root)
            payload = build_registry(
                repository_root=root,
                source_commit=SOURCE,
                recorded_at_utc=RECORDED,
            )
            raw = json.loads(payload)
            raw["records"].pop()
            missing = root / "missing.json"
            missing.write_bytes(_recompute(raw))
            with self.assertRaisesRegex(ValueError, "exact claim/holdout"):
                load_historical_result_registry(missing)

            raw = json.loads(payload)
            raw["records"][0]["reason_code"] = "RUNTIME_AUTHORITY_SUPERSESSION"
            wrong = root / "wrong.json"
            wrong.write_bytes(_recompute(raw))
            with self.assertRaisesRegex(ValueError, "claim/holdout supersession contract"):
                load_historical_result_registry(wrong)

            relative = next(iter(EXPECTED_CLAIM_HOLDOUT_EVIDENCE_IDENTITIES))
            target = root / relative / "status.json"
            target.write_bytes(target.read_bytes() + b"tamper\n")
            with self.assertRaisesRegex(
                ClaimHoldoutSupersessionBuildError,
                "evidence identity mismatch",
            ):
                build_registry(
                    repository_root=root,
                    source_commit=SOURCE,
                    recorded_at_utc=RECORDED,
                )

    def test_real_builder_is_deterministic_and_scope_matches_constants(self) -> None:
        declared = {
            item[key]
            for item in R2_CLAIM_HOLDOUT_SUPERSEDED_RESULTS.values()
            for key in ("primary_path", "replay_path")
        }
        self.assertEqual(declared, set(EXPECTED_CLAIM_HOLDOUT_EVIDENCE_IDENTITIES))
        first = build_registry(
            repository_root=REPOSITORY,
            source_commit=SOURCE,
            recorded_at_utc=RECORDED,
        )
        second = build_registry(
            repository_root=REPOSITORY,
            source_commit=SOURCE,
            recorded_at_utc=RECORDED,
        )
        self.assertEqual(first, second)
        self.assertEqual(first, canonical_json_bytes(json.loads(first)) + b"\n")


if __name__ == "__main__":
    unittest.main()
