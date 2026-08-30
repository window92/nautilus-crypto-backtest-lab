from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from crypto_lab.hashing import canonical_sha256
from crypto_lab.result_status import FinancialResultStatus
from crypto_lab.result_status import HistoricalRunStatus
from crypto_lab.result_status import load_historical_result_registry
from crypto_lab.result_status import revoked_result_for_directory


class HistoricalResultStatusTests(unittest.TestCase):
    def _manifest(self) -> dict[str, object]:
        records = [
            {
                "path": "runs/historical-run",
                "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
                "historical_run_status": "REVOKED",
                "financial_result_status": "INVALIDATED",
                "finding_ids": ["F-001", "F-002", "F-003"],
                "current_checker_outcome": "CHECK_FAIL",
                "current_failure_codes": ["SPOT_SHORT_OR_BORROW_DETECTED"],
                "historical_bytes_preserved": True,
                "evidence_hashes": {
                    "checker.json": "1" * 64,
                    "status.json": "2" * 64,
                    "evidence_manifest.json": "3" * 64,
                },
            },
        ]
        return {
            "schema": "audit-historical-result-status-v1",
            "audit_id": "COMPREHENSIVE_AUDIT_REMEDIATION_001",
            "audited_baseline_commit": "890b9d41cc05ff091f41c82409d196c91b86d452",
            "source_commit": "8" * 40,
            "recorded_at_utc": "2026-08-30T00:00:00Z",
            "historical_policy": "Historical bytes are immutable.",
            "final_holdout_authorized": False,
            "profitability_claim_authorized": False,
            "record_count": len(records),
            "records": records,
            "records_identity": canonical_sha256(records),
        }

    def test_registry_exposes_additive_revocation_without_authorizing_claims(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.json"
            path.write_text(json.dumps(self._manifest()), encoding="utf-8")
            registry = load_historical_result_registry(path)
        record = registry.for_path("runs/historical-run")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.historical_run_status, HistoricalRunStatus.REVOKED)
        self.assertEqual(record.financial_result_status, FinancialResultStatus.INVALIDATED)
        self.assertFalse(registry.final_holdout_authorized)
        self.assertFalse(registry.profitability_claim_authorized)

    def test_duplicate_unsafe_or_claim_authorizing_registry_fails_closed(self) -> None:
        mutations = []
        duplicate = self._manifest()
        duplicate["records"] = [*duplicate["records"], *duplicate["records"]]
        duplicate["record_count"] = len(duplicate["records"])
        duplicate["records_identity"] = canonical_sha256(duplicate["records"])
        mutations.append(duplicate)
        unsafe = self._manifest()
        unsafe["records"][0]["path"] = "../runs/historical-run"
        unsafe["records_identity"] = canonical_sha256(unsafe["records"])
        mutations.append(unsafe)
        claim = self._manifest()
        claim["profitability_claim_authorized"] = True
        mutations.append(claim)
        for index, value in enumerate(mutations):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "registry.json"
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_historical_result_registry(path)

    def test_superseded_remediation_run_is_revoked_by_default_lookup(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        run = (
            repository
            / "runs/comprehensive-audit-remediation-001-spot-benchmark-run-28567cfbf8de"
        )
        record = revoked_result_for_directory(run, repository_root=repository)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.finding_ids, ("F-003",))
        self.assertEqual(record.historical_run_status, HistoricalRunStatus.REVOKED)
        self.assertEqual(record.financial_result_status, FinancialResultStatus.INVALIDATED)

    def test_warned_owner_child_run_is_revoked_by_default_lookup(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        run = (
            repository
            / "runs/comprehensive-audit-remediation-002-spot-benchmark-run-301913ec060a"
        )
        record = revoked_result_for_directory(run, repository_root=repository)
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.finding_ids, ("F-003",))
        self.assertEqual(record.historical_run_status, HistoricalRunStatus.REVOKED)
        self.assertEqual(record.financial_result_status, FinancialResultStatus.INVALIDATED)


if __name__ == "__main__":
    unittest.main()
