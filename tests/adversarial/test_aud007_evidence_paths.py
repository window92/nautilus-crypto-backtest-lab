from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from crypto_lab.paths import EvidencePathError
from crypto_lab.paths import atomic_create_run_directory


CONFIG_ID = "a" * 64


class Aud007EvidencePathTests(unittest.TestCase):
    def test_invalid_components_are_rejected_before_root_creation(self) -> None:
        invalid = ("../escaped-run", "/tmp/absolute-run", "nested/run", "nested\\run", "", "bad\x00id")
        for index, run_id in enumerate(invalid):
            with self.subTest(run_id=repr(run_id)), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / f"missing-{index}"
                with self.assertRaises(EvidencePathError):
                    atomic_create_run_directory(root, run_id=run_id, config_sha256=CONFIG_ID)
                self.assertFalse(root.exists())

    def test_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "evidence"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            os.symlink(outside, root / f"safe-{CONFIG_ID[:12]}")
            with self.assertRaises(EvidencePathError):
                atomic_create_run_directory(root, run_id="safe", config_sha256=CONFIG_ID)
            self.assertEqual(tuple(outside.iterdir()), ())

    def test_symlinked_evidence_root_and_outside_root_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            outside = base / "outside"
            repository.mkdir()
            outside.mkdir()
            linked_root = repository / "runs"
            os.symlink(outside, linked_root)
            with self.assertRaises(EvidencePathError):
                atomic_create_run_directory(
                    linked_root,
                    run_id="safe",
                    config_sha256=CONFIG_ID,
                    containment_root=repository,
                )
            with self.assertRaises(EvidencePathError):
                atomic_create_run_directory(
                    outside,
                    run_id="safe",
                    config_sha256=CONFIG_ID,
                    containment_root=repository,
                )
            self.assertEqual(tuple(outside.iterdir()), ())

    def test_collision_is_rejected_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            created = atomic_create_run_directory(root, run_id="safe", config_sha256=CONFIG_ID)
            marker = created / "marker"
            marker.write_text("preserve", encoding="utf-8")
            with self.assertRaises(EvidencePathError):
                atomic_create_run_directory(root, run_id="safe", config_sha256=CONFIG_ID)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")


if __name__ == "__main__":
    unittest.main()
