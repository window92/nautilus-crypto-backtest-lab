from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from crypto_lab.hashing import sha256_file
from crypto_lab.profile_authority import ProfileAuthorityError
from crypto_lab.profile_authority import resolve_profile_authority
from crypto_lab.profile_authority import validate_persisted_profile_authority


class QualifiedProfileAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = Path(__file__).resolve().parents[2]
        self.registry = (
            self.repository
            / "evidence/audit/comprehensive-remediation-001/qualification-runtime-proof"
            / "qualified-profile-registry.json"
        )
        document = json.loads(self.registry.read_text(encoding="utf-8"))
        self.spot = next(
            item
            for item in document["records"]
            if item["profile_id"] == "BINANCE_SPOT_CASH_LONG_ONLY"
        )
        self.runtime_lock_sha256 = sha256_file(self.repository / "runtime.lock.json")

    def test_exact_registry_and_record_are_bound(self) -> None:
        resolved = resolve_profile_authority(
            repository_root=self.repository,
            registry_ref=self.registry.relative_to(self.repository).as_posix(),
            registry_sha256=sha256_file(self.registry),
            qualified_profile_record_id=self.spot["qualified_profile_record_id"],
            expected_profile_id=self.spot["profile_id"],
            expected_runtime_lock_sha256=self.runtime_lock_sha256,
        )
        self.assertEqual(resolved["schema"], "qualified-profile-authority-v1")
        self.assertEqual(
            validate_persisted_profile_authority(
                resolved,
                repository_root=self.repository,
                expected_profile_id=self.spot["profile_id"],
                expected_runtime_lock_sha256=self.runtime_lock_sha256,
            ),
            resolved,
        )

        stale = {**resolved, "unexpected": True}
        with self.assertRaises(ProfileAuthorityError):
            validate_persisted_profile_authority(
                stale,
                repository_root=self.repository,
                expected_profile_id=self.spot["profile_id"],
                expected_runtime_lock_sha256=self.runtime_lock_sha256,
            )

    def test_registry_tamper_wrong_record_and_unsafe_path_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copied = root / "registry.json"
            shutil.copyfile(self.registry, copied)
            document = json.loads(copied.read_text(encoding="utf-8"))
            document["records"][0]["checker_result"] = "CHECK_FAIL"
            copied.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaises(ProfileAuthorityError):
                resolve_profile_authority(
                    repository_root=root,
                    registry_ref="registry.json",
                    registry_sha256=sha256_file(copied),
                    qualified_profile_record_id=self.spot["qualified_profile_record_id"],
                    expected_profile_id=self.spot["profile_id"],
                    expected_runtime_lock_sha256=self.runtime_lock_sha256,
                )

        for registry_ref, record_id in (
            ("../qualified-profile-registry.json", self.spot["qualified_profile_record_id"]),
            (
                self.registry.relative_to(self.repository).as_posix(),
                "0" * 64,
            ),
        ):
            with self.assertRaises(ProfileAuthorityError):
                resolve_profile_authority(
                    repository_root=self.repository,
                    registry_ref=registry_ref,
                    registry_sha256=sha256_file(self.registry),
                    qualified_profile_record_id=record_id,
                    expected_profile_id=self.spot["profile_id"],
                    expected_runtime_lock_sha256=self.runtime_lock_sha256,
                )


if __name__ == "__main__":
    unittest.main()
