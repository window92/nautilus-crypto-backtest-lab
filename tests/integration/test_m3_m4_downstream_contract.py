from __future__ import annotations

import unittest
from pathlib import Path

from crypto_lab.m3 import QualificationDownstreamBundle
from crypto_lab.m3 import QualifiedProfileRegistry


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence/m3/m3-acceptance-001"


class M3M4DownstreamContractTests(unittest.TestCase):
    def test_future_m4_can_parse_both_qualified_records_without_internal_imports(self) -> None:
        registry = QualifiedProfileRegistry.from_json_bytes(
            (EVIDENCE / "qualified-profile-registry.json").read_bytes(),
        )
        profiles = []
        for record in registry.records:
            path = EVIDENCE / "downstream" / f"{record.profile_id.value}.json"
            bundle = QualificationDownstreamBundle.from_json_bytes(path.read_bytes())
            profiles.append(bundle.profile_record.profile_id)
            self.assertEqual(bundle.profile_record, record)
            self.assertEqual(bundle.run_result["state"], "COMPLETED")
            self.assertEqual(bundle.run_result["checker_outcome"], "CHECK_PASS")
            self.assertEqual(bundle.mechanical_integrity.state.value, "PASS")
            self.assertEqual(bundle.evidence_manifest["schema"], "run-evidence-manifest-v1")
            self.assertIn(
                "QUALIFICATION_INTERVAL_EXPOSED_NOT_FRESH_HOLDOUT",
                bundle.qualification_limitations,
            )
        self.assertEqual(tuple(profiles), tuple(record.profile_id for record in registry.records))

    def test_downstream_bundle_rejects_unknown_or_missing_fields(self) -> None:
        path = next((EVIDENCE / "downstream").glob("*.json"))
        raw = path.read_bytes()
        import json

        value = json.loads(raw)
        value["unknown"] = True
        with self.assertRaises(ValueError):
            QualificationDownstreamBundle.from_json_bytes(
                json.dumps(value, separators=(",", ":")).encode(),
            )
        del value["unknown"]
        del value["mechanical_integrity"]
        with self.assertRaises(ValueError):
            QualificationDownstreamBundle.from_json_bytes(
                json.dumps(value, separators=(",", ":")).encode(),
            )


if __name__ == "__main__":
    unittest.main()
