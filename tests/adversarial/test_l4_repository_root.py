from __future__ import annotations

import ast
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crypto_lab.git_identity import require_repository_root
from crypto_lab.sealing import OfficialSealOutcome
from crypto_lab.sealing import verify_official_seal
from tests.adversarial import test_r2_official_sealing as _official_sealing


ROOT = Path(__file__).resolve().parents[2]
SEALING = ROOT / "src/crypto_lab/sealing.py"


class RepositoryRootAuthorityTests(unittest.TestCase):
    def test_sealing_source_has_no_file_fallback(self) -> None:
        tree = ast.parse(SEALING.read_text(encoding="utf-8"), filename=str(SEALING))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "__file__":
                self.fail("verify_official_seal must not infer repository authority from __file__")

    def test_none_relative_missing_and_symlink_roots_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "required"):
            require_repository_root(None)
        with self.assertRaisesRegex(ValueError, "absolute"):
            require_repository_root(Path("relative-root"))
        missing = Path("/tmp/does-not-exist-repo-root-l4")
        with self.assertRaisesRegex(ValueError, "does not exist"):
            require_repository_root(missing)
        with tempfile.TemporaryDirectory() as temporary:
            real = Path(temporary) / "real"
            real.mkdir()
            link = Path(temporary) / "link"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                require_repository_root(link)

    def test_copied_package_and_wrong_commit_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "crypto_lab"
            copied.mkdir()
            (copied / "sealing.py").write_text("pass\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "copied package|product repository"):
                require_repository_root(copied)
        with self.assertRaisesRegex(ValueError, "expected commit"):
            require_repository_root(ROOT, expected_git_commit="0" * 40)

    def test_official_path_uses_the_real_repository_root(self) -> None:
        resolved = require_repository_root(ROOT)
        self.assertEqual(resolved, ROOT.resolve())
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
        self.assertEqual(
            require_repository_root(
                ROOT,
                expected_git_commit=head,
                require_current_head=True,
            ),
            resolved,
        )

    def test_verify_official_seal_requires_repository_root(self) -> None:
        fixture = _official_sealing.OfficialSealingAdversarialTests()
        fixture.setUp()
        try:
            with self.assertRaises(TypeError):
                verify_official_seal(fixture.run_dir)  # type: ignore[misc]
            with self.assertRaisesRegex(ValueError, "required"):
                verify_official_seal(fixture.run_dir, repository_root=None)
            report = fixture._verify()
            self.assertEqual(report.outcome, OfficialSealOutcome.OFFICIAL_SEAL_PASS)
            checker = mock.patch("crypto_lab.checker.check_evidence_directory")
            with checker as patched:
                patched.return_value.to_builtins.return_value = fixture.component
                verify_official_seal(fixture.run_dir, repository_root=ROOT)
                kwargs = patched.call_args.kwargs
                self.assertEqual(kwargs["repository_root"], ROOT.resolve())
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
