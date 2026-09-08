from __future__ import annotations

import json
import unittest
from pathlib import Path

from crypto_lab.data import DatasetRelease
from crypto_lab.data import RawObjectStore
from crypto_lab.hashing import sha256_file
from crypto_lab.historical_contracts import HistoricalValidationState
from crypto_lab.historical_contracts import validate_validator_contract


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence/m2/m2-acceptance-001"
EXPECTED_ARCHIVES = {
    "756551b6eb4f0a0173af3333762e6c95d08c7503bb3b7b79807e10a02575a4af",
    "10a12909f1b0e3fcc6b7f502e5ea9be5d1ba3455dd8ab16cc61c8650640ba7c0",
    "0904f4a99249d72991ff1c5aad335508bcaeeb8bc5500131d2489363b9c242fc",
    "ea294dedaabc84215a8d11e9e331831806e8f45f1f624f083aa59900d2fdc855",
    "df725010d55458866eb5a47e9ee708c6796c9d7e77b14c38342cea11f381815e",
}


class M2OfficialSampleQualificationTests(unittest.TestCase):
    def test_all_five_official_archives_match_publisher_checksums_and_local_blobs(self) -> None:
        checksums = json.loads((EVIDENCE / "publisher-checksum-results.json").read_text())
        self.assertEqual(checksums["status"], "PASS")
        self.assertEqual(len(checksums["results"]), 5)
        self.assertEqual({item["local_sha256"] for item in checksums["results"]}, EXPECTED_ARCHIVES)
        self.assertTrue(
            all(item["publisher_sha256"] == item["local_sha256"] for item in checksums["results"]),
        )
        store = RawObjectStore(ROOT / "data/raw")
        self.assertTrue(all(store.read_bytes(digest) for digest in EXPECTED_ARCHIVES))

    def test_official_source_and_timestamp_evidence_are_frozen(self) -> None:
        sources = json.loads((EVIDENCE / "official-source-contract-references.json").read_text())
        timestamps = json.loads((EVIDENCE / "timestamp-unit-evidence.json").read_text())
        probes = json.loads((EVIDENCE / "timestamp-endpoint-probes.json").read_text())
        addendum = json.loads((EVIDENCE / "raw-object-inventory-addendum-001.json").read_text())
        self.assertEqual(sources["status"], "PASS")
        self.assertEqual(timestamps["status"], "PASS")
        self.assertEqual(timestamps["rules"]["SPOT_BEFORE_2025_01_01"], "MILLISECONDS")
        self.assertEqual(timestamps["rules"]["SPOT_FROM_2025_01_01"], "MICROSECONDS")
        self.assertEqual(timestamps["rules"]["USDM_EXECUTION"], "MILLISECONDS")
        self.assertEqual(timestamps["rules"]["USDM_MARK"], "MILLISECONDS")
        self.assertEqual(timestamps["rules"]["USDM_FUNDING"], "MILLISECONDS")
        self.assertIn("never", timestamps["selection_basis"])
        self.assertEqual(probes["status"], "PASS")
        self.assertTrue(probes["execution_archive_row_exact_match"])
        self.assertTrue(probes["mark_archive_row_exact_match"])
        self.assertTrue(probes["funding_archive_event_exact_match"])
        self.assertFalse(probes["digit_length_inference_used"])
        self.assertEqual(addendum["status"], "PASS")
        self.assertEqual(addendum["object_count"], 3)

    def test_official_daily_completeness_and_no_repair(self) -> None:
        result = json.loads((EVIDENCE / "completeness-results.json").read_text())
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["no_repairs"])
        self.assertEqual(len(result["full_official_daily_grids"]), 4)
        self.assertTrue(
            all(
                item["expected_count"] == 1440 and item["actual_count"] == 1440
                for item in result["full_official_daily_grids"]
            ),
        )

    def test_spot_and_perpetual_release_evidence_matches_tracked_manifests(self) -> None:
        summary = json.loads((EVIDENCE / "qualification-summary.json").read_text())
        self.assertEqual(summary["status"], "PASS")
        for profile, evidence_name in (
            ("spot", "spot-qualification-release.json"),
            ("perpetual", "perpetual-qualification-release.json"),
        ):
            evidence_release = DatasetRelease.from_json_bytes(
                (EVIDENCE / evidence_name).read_bytes(),
            )
            release_file = ROOT / summary["release_files"][profile]
            tracked_release = DatasetRelease.from_json_bytes(release_file.read_bytes())
            self.assertEqual(evidence_release, tracked_release)
            self.assertEqual(evidence_release.dataset_release_id, summary[f"{profile}_dataset_release_id"])

    def test_funding_mark_metadata_and_catalog_qualifications_pass(self) -> None:
        for name in (
            "funding-schedule-proof.json",
            "mark-grid-proof.json",
            "instrument-metadata-evidence.json",
            "catalog-rebuild-comparison.json",
        ):
            with self.subTest(name=name):
                self.assertEqual(json.loads((EVIDENCE / name).read_text())["status"], "PASS")
        funding = json.loads((EVIDENCE / "funding-schedule-proof.json").read_text())
        self.assertFalse(funding["hard_coded_eight_hour_schedule"])
        self.assertFalse(funding["page_position_used_as_identity"])
        mark = json.loads((EVIDENCE / "mark-grid-proof.json").read_text())
        self.assertFalse(mark["prohibited_fallback_used"])
        catalog = json.loads((EVIDENCE / "catalog-rebuild-comparison.json").read_text())
        self.assertTrue(catalog["spot"]["semantic_inventory_equal"])
        self.assertTrue(catalog["perpetual"]["semantic_inventory_equal"])

    def test_historical_ssot_and_runtime_v1_snapshot_is_diagnostic_only(self) -> None:
        self.assertNotEqual(
            sha256_file(ROOT / "SSOT.md"),
            "b4deb7048242239234de7eaa353b623b3e45247eb42f1021dbc26ffd910edb99",
        )
        historical = validate_validator_contract(
            "validate_m2_evidence.py",
            repository_root=ROOT,
        )
        self.assertFalse(historical.acceptable, historical.to_builtins())
        self.assertEqual(
            historical.state,
            HistoricalValidationState.LEGACY_CONTRACT_ONLY,
        )
        self.assertTrue(historical.legacy_snapshot_integrity_valid)
        self.assertFalse(historical.to_builtins()["executable_validator_bound"])
        self.assertTrue(historical.files["runtime.lock.json"]["historical_snapshot_match"])
        self.assertFalse(historical.files["runtime.lock.json"]["current_root_match"])


if __name__ == "__main__":
    unittest.main()
