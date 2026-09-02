from __future__ import annotations

import ast
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crypto_lab.git_identity import require_repository_root
from crypto_lab.runner import capture_source_revision
from crypto_lab.sealing import OfficialSealOutcome
from crypto_lab.sealing import verify_official_seal
from tests.adversarial import test_r2_official_sealing as _official_sealing


ROOT = Path(__file__).resolve().parents[2]
SEALING = ROOT / "src/crypto_lab/sealing.py"
AUTHORITY_SENSITIVE_SOURCES = (
    "src/crypto_lab/checker.py",
    "src/crypto_lab/execution_plan.py",
    "src/crypto_lab/exposure.py",
    "src/crypto_lab/git_identity.py",
    "src/crypto_lab/historical_contracts.py",
    "src/crypto_lab/history.py",
    "src/crypto_lab/host_acceptance.py",
    "src/crypto_lab/legacy_publication.py",
    "src/crypto_lab/m3.py",
    "src/crypto_lab/official.py",
    "src/crypto_lab/owner.py",
    "src/crypto_lab/profile_authority.py",
    "src/crypto_lab/result_status.py",
    "src/crypto_lab/runner.py",
    "src/crypto_lab/sealing.py",
    "scripts/build_adversarial_remediation_002_result_status.py",
    "scripts/build_historical_validator_authorities.py",
    "scripts/build_r2_active_inventory_supersession_status.py",
    "scripts/build_r2_claim_holdout_supersession_status.py",
    "scripts/build_r2_claim_schema_supersession_status.py",
    "scripts/build_r2_repository_authority_supersession_status.py",
    "scripts/build_r2_repository_root_supersession_status.py",
    "scripts/build_r2_runtime_supersession_status.py",
    "scripts/build_runtime_bootstrap_authority.py",
    "scripts/generate_owner_workflow_fixture_input.py",
    "scripts/isolated_runtime_bootstrap.py",
    "scripts/prepare_adversarial_remediation_002_runs.py",
    "scripts/run_adversarial_remediation_002_acceptance.py",
    "scripts/run_historical_evidence_acceptance.py",
    "scripts/run_m3_child.py",
    "scripts/run_m3_qualifications.py",
    "scripts/validate_adversarial_remediation_002_runs.py",
    "scripts/validate_audit_qualification.py",
    "scripts/validate_free_official_binance_rebuild.py",
    "scripts/validate_free_official_raw_objects.py",
    "scripts/validate_m3_evidence.py",
    "scripts/verify_host_acceptance_attestation.py",
)
BOOTSTRAP_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TZ": "UTC",
}


class RepositoryRootAuthorityTests(unittest.TestCase):
    @staticmethod
    def _bootstrap_command(*target: str) -> list[str]:
        return [
            str(ROOT / ".venv/bin/python"),
            "-I",
            "-P",
            "-S",
            "-B",
            "-X",
            "pycache_prefix=/dev/null",
            str(ROOT / "scripts/isolated_runtime_bootstrap.py"),
            "--authority",
            str(ROOT / "runtime-bootstrap-authority.json"),
            "--repository",
            str(ROOT),
            *target,
        ]

    def test_authority_sensitive_sources_forbid_root_fallbacks(self) -> None:
        for relative in AUTHORITY_SENSITIVE_SOURCES:
            path = ROOT / relative
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            with self.subTest(path=relative):
                self.assertNotIn("Path.cwd(", source)
                self.assertNotIn(".parents[", source)
                if relative not in {
                    "src/crypto_lab/git_identity.py",
                    "scripts/build_historical_validator_authorities.py",
                    "scripts/build_runtime_bootstrap_authority.py",
                    "scripts/isolated_runtime_bootstrap.py",
                }:
                    self.assertNotIn("show-toplevel", source)
                for node in ast.walk(tree):
                    if (
                        isinstance(node, ast.Name)
                        and node.id == "__file__"
                        and relative != "scripts/isolated_runtime_bootstrap.py"
                    ):
                        self.fail(f"{relative} infers authority from __file__")
                    if isinstance(node, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == "ROOT"
                        for target in node.targets
                    ):
                        self.fail(f"{relative} declares an implicit ROOT")
                    if isinstance(node, ast.FunctionDef):
                        positional = [*node.args.posonlyargs, *node.args.args]
                        default_names = {
                            argument.arg
                            for argument in positional[-len(node.args.defaults) :]
                        } if node.args.defaults else set()
                        if default_names & {"repository", "repository_root"}:
                            self.fail(f"{relative} defaults a repository argument")
                    if (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "add_argument"
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == "--repository"
                    ):
                        required = next(
                            (
                                keyword.value
                                for keyword in node.keywords
                                if keyword.arg == "required"
                            ),
                            None,
                        )
                        self.assertIsInstance(required, ast.Constant)
                        self.assertIs(required.value, True)
                if relative == "scripts/isolated_runtime_bootstrap.py":
                    self.assertEqual(source.count("Path(__file__)"), 1)
                    self.assertIn("def _verify_bootstrap_identity", source)

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

    def test_wrong_repository_and_authority_tree_are_rejected(self) -> None:
        origin = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=ROOT,
            text=True,
        ).strip()
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
        with self.assertRaisesRegex(ValueError, "tree"):
            require_repository_root(
                ROOT,
                expected_repository_identity=origin,
                expected_git_commit=head,
                expected_git_tree="f" * 40,
            )
        with tempfile.TemporaryDirectory() as temporary:
            wrong = Path(temporary) / "wrong-product"
            wrong.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=wrong, check=True)
            subprocess.run(
                ["git", "config", "user.name", "L4 Test"],
                cwd=wrong,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "l4@example.invalid"],
                cwd=wrong,
                check=True,
            )
            subprocess.run(
                ["git", "remote", "add", "origin", "https://example.invalid/wrong.git"],
                cwd=wrong,
                check=True,
            )
            (wrong / "SSOT.md").write_text("wrong product\n", encoding="utf-8")
            package = wrong / "src/crypto_lab"
            package.mkdir(parents=True)
            (package / "sealing.py").write_text("pass\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=wrong, check=True)
            subprocess.run(["git", "commit", "-m", "wrong product"], cwd=wrong, check=True)
            with self.assertRaisesRegex(ValueError, "origin"):
                require_repository_root(
                    wrong,
                    expected_repository_identity=origin,
                )

    def test_capture_source_revision_requires_explicit_repository(self) -> None:
        with self.assertRaises(TypeError):
            capture_source_revision()  # type: ignore[call-arg]
        captured = capture_source_revision(ROOT)
        self.assertEqual(captured.git_commit, subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip())

    def test_real_owner_and_m3_child_require_target_repository_argument(self) -> None:
        owner = subprocess.run(
            self._bootstrap_command(
                "--entrypoint",
                "crypto_lab.owner:main",
                "--",
                "--input",
                "/tmp/l4-owner-input-does-not-exist.json",
            ),
            cwd=ROOT,
            env=BOOTSTRAP_ENVIRONMENT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(owner.returncode, 2, owner.stderr)
        self.assertIn("--repository", owner.stderr)
        m3 = subprocess.run(
            self._bootstrap_command(
                "--script",
                "scripts/run_m3_child.py",
                "--",
                "--profile",
                "spot",
                "--run-id",
                "l4-missing-repository",
                "--evidence-root",
                "/tmp/l4-m3-evidence",
                "--summary",
                "/tmp/l4-m3-summary.json",
            ),
            cwd=ROOT,
            env=BOOTSTRAP_ENVIRONMENT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(m3.returncode, 2, m3.stderr)
        self.assertIn("--repository", m3.stderr)

    def test_owner_bootstrap_binds_positive_and_negative_repository_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            positive_output = root / "positive.json"
            positive = subprocess.run(
                self._bootstrap_command(
                    "--entrypoint",
                    "crypto_lab.owner:main",
                    "--",
                    "--input",
                    str(root / "missing-input.json"),
                    "--repository",
                    str(ROOT),
                    "--output",
                    str(positive_output),
                ),
                cwd=ROOT,
                env=BOOTSTRAP_ENVIRONMENT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(positive.returncode, 2, positive.stderr)
            self.assertEqual(json.loads(positive_output.read_text())["error_type"], "FileNotFoundError")
            negative_output = root / "relative.json"
            negative = subprocess.run(
                self._bootstrap_command(
                    "--entrypoint",
                    "crypto_lab.owner:main",
                    "--",
                    "--input",
                    str(root / "missing-input.json"),
                    "--repository",
                    "relative-root",
                    "--output",
                    str(negative_output),
                ),
                cwd=ROOT,
                env=BOOTSTRAP_ENVIRONMENT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(negative.returncode, 2, negative.stderr)
            negative_payload = json.loads(negative_output.read_text())
            self.assertEqual(negative_payload["status"], "BLOCKED")
            self.assertIn("absolute", negative_payload["detail"])

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
