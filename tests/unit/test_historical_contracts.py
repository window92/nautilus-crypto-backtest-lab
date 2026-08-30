from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crypto_lab.historical_contracts import HistoricalValidationState
from crypto_lab.historical_contracts import snapshot_for_validator
from crypto_lab.historical_contracts import validate_historical_contract
from crypto_lab.historical_contracts import validate_validator_contract


ROOT = Path(__file__).resolve().parents[2]


class HistoricalContractTests(unittest.TestCase):
    def test_all_declared_validators_resolve_to_immutable_ancestor_snapshots(self) -> None:
        manifest = json.loads(
            (ROOT / "contracts/historical-contract-snapshots.json").read_text(
                encoding="utf-8",
            ),
        )
        declared = set(manifest["validators"])
        actual = {
            path.name
            for path in (ROOT / "scripts").glob("validate_*.py")
            if path.name not in {"validate_m3_evidence.py"} or path.is_file()
        }
        self.assertEqual(declared, actual)
        for validator in sorted(declared):
            snapshot = snapshot_for_validator(validator, repository_root=ROOT)
            result = validate_validator_contract(validator, repository_root=ROOT)
            self.assertEqual(snapshot, result.snapshot_id)
            self.assertTrue(result.acceptable, result.to_builtins())

    def test_current_lock_change_is_distinct_from_historical_corruption(self) -> None:
        result = validate_historical_contract(
            "release-v1-contract",
            repository_root=ROOT,
        )
        self.assertEqual(
            result.state,
            HistoricalValidationState.CURRENT_ROOT_DIFFERS_VALIDLY,
        )
        self.assertTrue(result.snapshot_files_match)
        self.assertFalse(result.current_root_matches_snapshot)
        adopted_status = validate_historical_contract(
            "owner-signoff-adopted-status",
            repository_root=ROOT,
        )
        self.assertEqual(
            adopted_status.state,
            HistoricalValidationState.HISTORICAL_SNAPSHOT_VALID,
        )
        self.assertTrue(adopted_status.snapshot_is_ancestor)
        self.assertTrue(adopted_status.snapshot_files_match)
        self.assertTrue(adopted_status.current_root_matches_snapshot)

    def test_matching_current_file_reports_historical_snapshot_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "historical-contract-snapshots-v1",
                        "snapshots": {
                            "current-lock": {
                                "git_commit": "40cc44a7ec8f7d76565069c205287721578b58a7",
                                "files": {
                                    "requirements.lock.txt": (
                                        "0999d2b764f5949aa145bb931825a6d692428e2fb14afe24ed32a5eb2efd29e4"
                                    ),
                                },
                            },
                        },
                        "validators": {},
                    },
                ),
                encoding="utf-8",
            )
            result = validate_historical_contract(
                "current-lock",
                repository_root=ROOT,
                manifest_path=path,
            )
        self.assertEqual(
            result.state,
            HistoricalValidationState.HISTORICAL_SNAPSHOT_VALID,
        )

    def test_bad_historical_hash_is_evidence_corrupt_not_a_root_delta(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "historical-contract-snapshots-v1",
                        "snapshots": {
                            "corrupt": {
                                "git_commit": "890b9d41cc05ff091f41c82409d196c91b86d452",
                                "files": {"SSOT.md": "0" * 64},
                            },
                        },
                        "validators": {},
                    },
                ),
                encoding="utf-8",
            )
            result = validate_historical_contract(
                "corrupt",
                repository_root=ROOT,
                manifest_path=path,
            )
        self.assertEqual(result.state, HistoricalValidationState.EVIDENCE_CORRUPT)
        self.assertFalse(result.acceptable)


if __name__ == "__main__":
    unittest.main()
