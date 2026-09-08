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
LEGACY_V1_VALIDATORS = frozenset(
    {
        "validate_audit_qualification.py",
        "validate_audit_research_runs.py",
        "validate_data_provenance_evidence.py",
        "validate_free_official_binance_rebuild.py",
        "validate_free_official_raw_objects.py",
        "validate_instrument_repair_evidence.py",
        "validate_instrument_representation_continuity.py",
        "validate_m1_evidence.py",
        "validate_m2_evidence.py",
        "validate_m3_evidence.py",
        "validate_m4_evidence.py",
        "validate_native_research_metrics_readiness_evidence.py",
        "validate_owner_smoke_002_replacement_evidence.py",
        "validate_owner_strategy_research_001_evidence.py",
    },
)


class HistoricalContractTests(unittest.TestCase):
    def test_all_declared_v1_snapshots_are_integrity_diagnostics_not_authority(self) -> None:
        manifest = json.loads(
            (ROOT / "contracts/historical-contract-snapshots.json").read_text(
                encoding="utf-8",
            ),
        )
        declared = set(manifest["validators"])
        self.assertEqual(declared, LEGACY_V1_VALIDATORS)
        for validator in sorted(declared):
            snapshot = snapshot_for_validator(validator, repository_root=ROOT)
            result = validate_validator_contract(validator, repository_root=ROOT)
            self.assertEqual(snapshot, result.snapshot_id)
            self.assertEqual(result.state, HistoricalValidationState.LEGACY_CONTRACT_ONLY)
            self.assertTrue(result.legacy_snapshot_integrity_valid, result.to_builtins())
            self.assertFalse(result.acceptable, result.to_builtins())
            self.assertFalse(result.to_builtins()["executable_validator_bound"])

    def test_current_root_match_or_delta_never_upgrades_v1_to_execution_authority(self) -> None:
        result = validate_historical_contract(
            "release-v1-contract",
            repository_root=ROOT,
        )
        self.assertEqual(
            result.state,
            HistoricalValidationState.LEGACY_CONTRACT_ONLY,
        )
        self.assertTrue(result.snapshot_files_match)
        self.assertFalse(result.current_root_matches_snapshot)
        self.assertTrue(result.legacy_snapshot_integrity_valid)
        self.assertFalse(result.acceptable)
        adopted_status = validate_historical_contract(
            "owner-signoff-adopted-status",
            repository_root=ROOT,
        )
        self.assertEqual(
            adopted_status.state,
            HistoricalValidationState.LEGACY_CONTRACT_ONLY,
        )
        self.assertTrue(adopted_status.snapshot_is_ancestor)
        self.assertTrue(adopted_status.snapshot_files_match)
        self.assertFalse(adopted_status.current_root_matches_snapshot)
        self.assertTrue(adopted_status.legacy_snapshot_integrity_valid)
        self.assertFalse(adopted_status.acceptable)

    def test_matching_current_file_remains_legacy_diagnostic_only(self) -> None:
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
            HistoricalValidationState.LEGACY_CONTRACT_ONLY,
        )
        self.assertTrue(result.current_root_matches_snapshot)
        self.assertTrue(result.legacy_snapshot_integrity_valid)
        self.assertFalse(result.acceptable)

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
