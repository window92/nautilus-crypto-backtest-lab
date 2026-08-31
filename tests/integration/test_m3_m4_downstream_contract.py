from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from crypto_lab.config import ConfigError
from crypto_lab.config import MarketProfile
from crypto_lab.hashing import canonical_sha256
from crypto_lab.m3 import MechanicalIntegrity
from crypto_lab.m3 import MechanicalIntegrityResult
from crypto_lab.m3 import QualificationDownstreamBundle
from crypto_lab.m3 import QualifiedProfileRegistry
from tests.unit.test_m3_contracts import qualified_record


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence/m3/m3-acceptance-001"


def write_v2_downstream_fixture(
    root: Path,
) -> tuple[Path, Path, QualifiedProfileRegistry]:
    records = (
        qualified_record(MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY, "2"),
        qualified_record(
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            "7",
        ),
    )
    registry = QualifiedProfileRegistry.create(records=records)
    registry_path = root / "qualified-profile-registry.json"
    downstream = root / "downstream"
    downstream.mkdir(parents=True)
    registry_path.write_bytes(registry.to_json_bytes())
    for record in records:
        bundle = QualificationDownstreamBundle(
            schema_version=2,
            profile_record=record,
            run_result={
                "component_validation_outcome": "COMPONENT_CHECK_PASS",
                "run_id": record.accepted_run_ids[0],
                "state": "COMPLETED",
            },
            evidence_manifest={
                "entries": [],
                "inventory_content_sha256": canonical_sha256([]),
                "run_id": record.accepted_run_ids[0],
                "schema": "run-evidence-manifest-v1",
            },
            mechanical_integrity=MechanicalIntegrityResult(
                state=MechanicalIntegrity.PASS,
                checker_result="COMPONENT_CHECK_PASS",
                replay_result="PASS",
                run_ids=record.accepted_run_ids,
                failure_codes=(),
            ),
            qualification_limitations=record.qualification_limitations,
        )
        (downstream / f"{record.profile_id.value}.json").write_bytes(bundle.to_json_bytes())
    return registry_path, downstream, registry


class M3M4DownstreamContractTests(unittest.TestCase):
    def test_legacy_v1_registry_parses_but_downstream_is_not_current_authority(self) -> None:
        registry = QualifiedProfileRegistry.from_json_bytes(
            (EVIDENCE / "qualified-profile-registry.json").read_bytes(),
        )
        self.assertEqual(registry.schema_version, 1)
        self.assertEqual({record.checker_result for record in registry.records}, {"CHECK_PASS"})
        for record in registry.records:
            path = EVIDENCE / "downstream" / f"{record.profile_id.value}.json"
            with self.assertRaisesRegex(
                ConfigError,
                "mechanical_integrity.component_validation",
            ):
                QualificationDownstreamBundle.from_json_bytes(path.read_bytes())

    def test_future_m4_parses_v2_component_bundles_without_internal_imports(self) -> None:
        with TemporaryDirectory() as temporary:
            registry_path, downstream, registry = write_v2_downstream_fixture(Path(temporary))
            reparsed = QualifiedProfileRegistry.from_json_bytes(registry_path.read_bytes())
            self.assertEqual(reparsed, registry)
            profiles = []
            for record in registry.records:
                path = downstream / f"{record.profile_id.value}.json"
                bundle = QualificationDownstreamBundle.from_json_bytes(path.read_bytes())
                profiles.append(bundle.profile_record.profile_id)
                self.assertEqual(bundle.schema_version, 2)
                self.assertEqual(bundle.profile_record, record)
                self.assertEqual(bundle.run_result["state"], "COMPLETED")
                self.assertNotIn("checker_outcome", bundle.run_result)
                self.assertEqual(
                    bundle.run_result["component_validation_outcome"],
                    "COMPONENT_CHECK_PASS",
                )
                self.assertEqual(bundle.mechanical_integrity.state.value, "PASS")
                self.assertEqual(
                    bundle.mechanical_integrity.checker_result,
                    "COMPONENT_CHECK_PASS",
                )
                self.assertEqual(bundle.evidence_manifest["schema"], "run-evidence-manifest-v1")
                self.assertIn(
                    "QUALIFICATION_INTERVAL_EXPOSED_NOT_FRESH_HOLDOUT",
                    bundle.qualification_limitations,
                )
            self.assertEqual(
                tuple(profiles),
                tuple(record.profile_id for record in registry.records),
            )

    def test_downstream_bundle_rejects_unknown_or_missing_fields(self) -> None:
        import json

        with TemporaryDirectory() as temporary:
            _registry_path, downstream, _registry = write_v2_downstream_fixture(
                Path(temporary),
            )
            path = next(downstream.glob("*.json"))
            value = json.loads(path.read_bytes())
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
