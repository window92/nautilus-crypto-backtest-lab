from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from crypto_lab.config import MarketProfile
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.profile_authority import ProfileAuthorityError
from crypto_lab.profile_authority import resolve_profile_authority
from crypto_lab.profile_authority import validate_persisted_profile_authority
from tests.unit.test_m3_contracts import qualified_record


class QualifiedProfileAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = Path(__file__).resolve().parents[2]
        self.registry = (
            self.repository
            / "evidence/audit/comprehensive-remediation-001/qualification-runtime-proof"
            / "qualified-profile-registry.json"
        )
        document = json.loads(self.registry.read_text(encoding="utf-8"))
        self.legacy_spot = next(
            item
            for item in document["records"]
            if item["profile_id"] == "BINANCE_SPOT_CASH_LONG_ONLY"
        )
        self.runtime_lock_sha256 = sha256_file(self.repository / "runtime.lock.json")

    def _write_v2_registry(self, root: Path) -> tuple[Path, QualifiedProfileRegistry]:
        root.mkdir(parents=True, exist_ok=True)
        registry = QualifiedProfileRegistry.create(
            records=(
                qualified_record(
                    MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                    "2",
                    runtime_lock_sha256=self.runtime_lock_sha256,
                ),
                qualified_record(
                    MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
                    "7",
                    runtime_lock_sha256=self.runtime_lock_sha256,
                ),
            ),
        )
        path = root / "registry.json"
        path.write_bytes(registry.to_json_bytes())
        return path, registry

    def test_legacy_v1_registry_parses_but_cannot_be_profile_authority(self) -> None:
        legacy = QualifiedProfileRegistry.from_json_bytes(self.registry.read_bytes())
        self.assertEqual(legacy.schema_version, 1)
        self.assertEqual({record.checker_result for record in legacy.records}, {"CHECK_PASS"})
        with self.assertRaisesRegex(ProfileAuthorityError, "identity is invalid"):
            resolve_profile_authority(
                repository_root=self.repository,
                registry_ref=self.registry.relative_to(self.repository).as_posix(),
                registry_sha256=sha256_file(self.registry),
                qualified_profile_record_id=self.legacy_spot["qualified_profile_record_id"],
                expected_profile_id=self.legacy_spot["profile_id"],
                expected_runtime_lock_sha256=self.runtime_lock_sha256,
            )

    def test_exact_v2_component_registry_and_record_are_bound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, registry = self._write_v2_registry(root)
            spot = registry.records[0]
            resolved = resolve_profile_authority(
                repository_root=root,
                registry_ref="registry.json",
                registry_sha256=sha256_file(path),
                qualified_profile_record_id=spot.qualified_profile_record_id,
                expected_profile_id=spot.profile_id.value,
                expected_runtime_lock_sha256=self.runtime_lock_sha256,
            )
            self.assertEqual(resolved["schema"], "qualified-profile-authority-v2")
            self.assertEqual(
                validate_persisted_profile_authority(
                    resolved,
                    repository_root=root,
                    expected_profile_id=spot.profile_id.value,
                    expected_runtime_lock_sha256=self.runtime_lock_sha256,
                ),
                resolved,
            )

            stale = {**resolved, "unexpected": True}
            with self.assertRaises(ProfileAuthorityError):
                validate_persisted_profile_authority(
                    stale,
                    repository_root=root,
                    expected_profile_id=spot.profile_id.value,
                    expected_runtime_lock_sha256=self.runtime_lock_sha256,
                )

    def test_registry_tamper_wrong_record_and_unsafe_path_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copied, registry = self._write_v2_registry(root)
            spot = registry.records[0]
            document = json.loads(copied.read_text(encoding="utf-8"))
            record = document["records"][0]
            record["checker_result"] = "COMPONENT_CHECK_FAIL"
            material = copy.deepcopy(record)
            material.pop("qualified_profile_record_id")
            material["source_revision"].pop("captured_at_utc")
            record["qualified_profile_record_id"] = canonical_sha256(material)
            document["registry_content_sha256"] = canonical_sha256(
                {"schema_version": 2, "records": document["records"]},
            )
            copied.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ProfileAuthorityError, "not eligible"):
                resolve_profile_authority(
                    repository_root=root,
                    registry_ref="registry.json",
                    registry_sha256=sha256_file(copied),
                    qualified_profile_record_id=record["qualified_profile_record_id"],
                    expected_profile_id=spot.profile_id.value,
                    expected_runtime_lock_sha256=self.runtime_lock_sha256,
                )

            valid_path, valid_registry = self._write_v2_registry(root / "valid")
            valid_spot = valid_registry.records[0]
            for registry_ref, record_id in (
                ("../registry.json", valid_spot.qualified_profile_record_id),
                ("valid/registry.json", "0" * 64),
            ):
                with self.assertRaises(ProfileAuthorityError):
                    resolve_profile_authority(
                        repository_root=root,
                        registry_ref=registry_ref,
                        registry_sha256=sha256_file(valid_path),
                        qualified_profile_record_id=record_id,
                        expected_profile_id=valid_spot.profile_id.value,
                        expected_runtime_lock_sha256=self.runtime_lock_sha256,
                    )


if __name__ == "__main__":
    unittest.main()
