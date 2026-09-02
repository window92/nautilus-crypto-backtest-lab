from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

from crypto_lab.historical_contracts import HistoricalAuthorityError
from crypto_lab.historical_contracts import HistoricalValidationState
from crypto_lab.historical_contracts import load_historical_authority_manifest
from crypto_lab.historical_contracts import validate_historical_contract
from crypto_lab.historical_contracts import validate_historical_validator_authority
from crypto_lab.historical_executor import execute_historical_validator
from tests.adversarial.test_r2_runtime_bootstrap import BOOTSTRAP
from tests.adversarial.test_r2_runtime_bootstrap import IsolatedRuntimeFixture
from tests.adversarial.test_r2_runtime_bootstrap import _canonical_sha256
from tests.adversarial.test_r2_runtime_bootstrap import _git
from tests.adversarial.test_r2_runtime_bootstrap import _source_identity


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


class HistoricalAuthorityFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.runtime = IsolatedRuntimeFixture(root / "runtime")
        self.repository = root / "historical"
        self.repository.mkdir()
        _git(self.repository, "init", "-b", "main")
        _git(self.repository, "config", "user.name", "R2 Historical Test")
        _git(self.repository, "config", "user.email", "r2-historical@example.invalid")
        _git(
            self.repository,
            "remote",
            "add",
            "origin",
            "https://example.invalid/r2-historical-product.git",
        )
        _write(self.repository / "SSOT.md", "synthetic historical Product authority\n")
        _write(self.repository / ".gitignore", "external/\ndata/\n")
        _write(self.repository / "src/crypto_lab/__init__.py", "\"\"\"Pinned package.\"\"\"\n")
        _write(
            self.repository / "src/crypto_lab/historical_wrapper.py",
            """from __future__ import annotations


def classify(value: dict[str, object]) -> str:
    return "PASS" if value.get("valid") is True else "FAIL"
""",
        )
        _write(
            self.repository / "scripts/validate_fixture.py",
            """from __future__ import annotations

import json
import sys
from pathlib import Path

from crypto_lab.historical_wrapper import classify


input_path = Path(sys.argv[1])
value = json.loads(input_path.read_text(encoding="utf-8"))
root = Path(__file__).resolve().parents[1]
isolated_regular = (
    not input_path.is_symlink()
    and input_path.resolve().is_relative_to(root)
)
if value.get("mutate_tracked") is True:
    (root / "src/crypto_lab/__init__.py").write_text("mutated by validator\\n", encoding="utf-8")
if value.get("mutate_external") is True:
    input_path.chmod(0o600)
    input_path.write_text("mutated by validator\\n", encoding="utf-8")
status = classify(value)
if value.get("require_isolated_regular") is True and not isolated_regular:
    status = "FAIL"
print(json.dumps({"status": status}, sort_keys=True, separators=(",", ":")))
""",
        )
        _git(self.repository, "add", ".")
        _git(self.repository, "commit", "-m", "strict historical semantics")
        self.strict_commit = _git(self.repository, "rev-parse", "HEAD")
        self.strict_tree = _git(self.repository, "rev-parse", "HEAD^{tree}")
        self.invalid_evidence = self.repository / "external/invalid-evidence.json"
        self.invalid_evidence.parent.mkdir()
        self.invalid_evidence.write_text('{"valid":false}\n', encoding="utf-8")
        closure = [
            _source_identity(self.repository, self.strict_commit, relative)
            for relative in (
                "scripts/validate_fixture.py",
                "src/crypto_lab/__init__.py",
                "src/crypto_lab/historical_wrapper.py",
            )
        ]
        self.authority_value = {
            "authority_id": "fixture-strict-v1",
            "validator_name": "validate_fixture.py",
            "source_commit": self.strict_commit,
            "source_tree": self.strict_tree,
            "entrypoint": deepcopy(closure[0]),
            "wrapper": deepcopy(closure[2]),
            "executable_closure": sorted(closure, key=lambda item: item["path"]),
            "arguments": ["{binding:evidence.json}"],
            "external_bindings": [
                {
                    "kind": "FILE",
                    "sha256": hashlib.sha256(self.invalid_evidence.read_bytes()).hexdigest(),
                    "size_bytes": self.invalid_evidence.stat().st_size,
                    "locator": "external/invalid-evidence.json",
                    "target": "evidence.json",
                },
            ],
            "interpreter_profile": "fixture-runtime",
            "expected_exit_code": 0,
            "expected_status": "PASS",
            "expected_stdout_sha256": hashlib.sha256(
                b'{"status":"PASS"}\n',
            ).hexdigest(),
            "expected_stderr_sha256": hashlib.sha256(b"").hexdigest(),
        }
        self.authority_value["bundle_identity"] = _canonical_sha256(self.authority_value)
        self.manifest_path = root / "historical-authorities.json"
        self.write_manifest()

        # HEAD now has the vulnerable semantics.  The strict commit remains an
        # ancestor and must be the only executable authority for old evidence.
        _write(
            self.repository / "src/crypto_lab/historical_wrapper.py",
            """from __future__ import annotations


def classify(value: dict[str, object]) -> str:
    return "PASS"
""",
        )
        _git(self.repository, "add", "src/crypto_lab/historical_wrapper.py")
        _git(self.repository, "commit", "-m", "mutate current validator semantics")
        self.mutated_commit = _git(self.repository, "rev-parse", "HEAD")

    def manifest(self) -> dict[str, Any]:
        return {
            "schema": "historical-validator-authorities-v2",
            "execution_plan": ["validate_fixture.py"],
            "runtime_profiles": {"fixture-runtime": self.runtime.runtime_profile},
            "authorities": {"validate_fixture.py": self.authority_value},
        }

    def write_manifest(self, value: dict[str, Any] | None = None) -> None:
        self.manifest_path.write_text(
            json.dumps(value or self.manifest(), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

    def authority(self):
        return load_historical_authority_manifest(self.manifest_path)["authorities"][
            "validate_fixture.py"
        ]


class HistoricalValidatorIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = HistoricalAuthorityFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_v1_snapshot_is_diagnostic_only_and_never_execution_authority(self) -> None:
        file_identity = _source_identity(
            self.fixture.repository,
            self.fixture.strict_commit,
            "scripts/validate_fixture.py",
        )
        manifest = Path(self.temporary.name) / "legacy.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "historical-contract-snapshots-v1",
                    "snapshots": {
                        "legacy": {
                            "git_commit": self.fixture.strict_commit,
                            "files": {
                                "scripts/validate_fixture.py": file_identity["sha256"],
                            },
                        },
                    },
                    "validators": {"validate_fixture.py": "legacy"},
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        result = validate_historical_contract(
            "legacy",
            repository_root=self.fixture.repository,
            manifest_path=manifest,
        )
        self.assertEqual(result.state, HistoricalValidationState.LEGACY_CONTRACT_ONLY)
        self.assertTrue(result.legacy_snapshot_integrity_valid)
        self.assertFalse(result.acceptable)
        self.assertFalse(result.to_builtins()["executable_validator_bound"])

    def test_execution_plan_must_enumerate_every_authority_once(self) -> None:
        manifest = self.fixture.manifest()
        manifest["execution_plan"] = []
        self.fixture.write_manifest(manifest)
        with self.assertRaisesRegex(HistoricalAuthorityError, "EXECUTION_PLAN_MISMATCH"):
            load_historical_authority_manifest(self.fixture.manifest_path)

    def test_bound_external_input_tamper_fails_before_validator(self) -> None:
        self.fixture.invalid_evidence.write_text('{"valid":true}\n', encoding="utf-8")
        with self.assertRaisesRegex(HistoricalAuthorityError, "EXTERNAL_BINDING_MISMATCH"):
            execute_historical_validator(
                self.fixture.authority(),
                repository_root=self.fixture.repository,
                runtime_profile=self.fixture.runtime.runtime_profile,
                bootstrap_path=BOOTSTRAP,
            )

    def test_repo_root_external_view_is_exact_and_does_not_copy_or_modify_source(self) -> None:
        manifest = self.fixture.manifest()
        authority = manifest["authorities"]["validate_fixture.py"]
        binding = authority["external_bindings"][0]
        binding["target"] = "data/raw/pinned/invalid-evidence.json"
        authority["arguments"] = ["{repository}/data/raw/pinned/invalid-evidence.json"]
        authority.pop("bundle_identity")
        authority["bundle_identity"] = _canonical_sha256(authority)
        self.fixture.write_manifest(manifest)
        before = self.fixture.invalid_evidence.read_bytes()
        before_stat = self.fixture.invalid_evidence.stat()

        result = execute_historical_validator(
            self.fixture.authority(),
            repository_root=self.fixture.repository,
            runtime_profile=self.fixture.runtime.runtime_profile,
            bootstrap_path=BOOTSTRAP,
        )

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.validator_status, "FAIL")
        self.assertFalse(result.passed)
        self.assertEqual(self.fixture.invalid_evidence.read_bytes(), before)
        after_stat = self.fixture.invalid_evidence.stat()
        self.assertEqual((after_stat.st_dev, after_stat.st_ino), (before_stat.st_dev, before_stat.st_ino))

    def test_external_view_is_an_isolated_regular_copy_not_a_symlink(self) -> None:
        payload = b'{"require_isolated_regular":true,"valid":true}\n'
        self.fixture.invalid_evidence.write_bytes(payload)
        manifest = self.fixture.manifest()
        authority = manifest["authorities"]["validate_fixture.py"]
        binding = authority["external_bindings"][0]
        binding["target"] = "data/raw/pinned/valid-evidence.json"
        binding["sha256"] = hashlib.sha256(payload).hexdigest()
        binding["size_bytes"] = len(payload)
        authority["arguments"] = ["{repository}/data/raw/pinned/valid-evidence.json"]
        authority.pop("bundle_identity")
        authority["bundle_identity"] = _canonical_sha256(authority)
        self.fixture.write_manifest(manifest)

        result = execute_historical_validator(
            self.fixture.authority(),
            repository_root=self.fixture.repository,
            runtime_profile=self.fixture.runtime.runtime_profile,
            bootstrap_path=BOOTSTRAP,
        )

        self.assertTrue(result.passed, result.stderr)
        self.assertEqual(result.validator_status, "PASS")
        self.assertTrue(result.to_builtins()["historical_evidence_accepted"])

    def test_matching_status_with_changed_stdout_contract_fails_closed(self) -> None:
        payload = b'{"valid":true}\n'
        self.fixture.invalid_evidence.write_bytes(payload)
        manifest = self.fixture.manifest()
        authority = manifest["authorities"]["validate_fixture.py"]
        binding = authority["external_bindings"][0]
        binding["sha256"] = hashlib.sha256(payload).hexdigest()
        binding["size_bytes"] = len(payload)
        authority["expected_stdout_sha256"] = "f" * 64
        authority.pop("bundle_identity")
        authority["bundle_identity"] = _canonical_sha256(authority)
        self.fixture.write_manifest(manifest)

        result = execute_historical_validator(
            self.fixture.authority(),
            repository_root=self.fixture.repository,
            runtime_profile=self.fixture.runtime.runtime_profile,
            bootstrap_path=BOOTSTRAP,
        )

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.validator_status, "PASS")
        self.assertFalse(result.passed)
        self.assertFalse(result.to_builtins()["output_contract_matched"])
        self.assertFalse(result.to_builtins()["historical_evidence_accepted"])

    def test_validator_cannot_mutate_authoritative_external_bytes_through_view(self) -> None:
        payload = b'{"mutate_external":true,"valid":true}\n'
        self.fixture.invalid_evidence.write_bytes(payload)
        manifest = self.fixture.manifest()
        authority = manifest["authorities"]["validate_fixture.py"]
        binding = authority["external_bindings"][0]
        binding["sha256"] = hashlib.sha256(payload).hexdigest()
        binding["size_bytes"] = len(payload)
        authority.pop("bundle_identity")
        authority["bundle_identity"] = _canonical_sha256(authority)
        self.fixture.write_manifest(manifest)

        with self.assertRaisesRegex(HistoricalAuthorityError, "EXTERNAL_BINDING_MISMATCH"):
            execute_historical_validator(
                self.fixture.authority(),
                repository_root=self.fixture.repository,
                runtime_profile=self.fixture.runtime.runtime_profile,
                bootstrap_path=BOOTSTRAP,
            )
        self.assertEqual(self.fixture.invalid_evidence.read_bytes(), payload)

    def test_validator_cannot_modify_tracked_snapshot_after_bootstrap(self) -> None:
        payload = b'{"mutate_tracked":true,"valid":true}\n'
        self.fixture.invalid_evidence.write_bytes(payload)
        manifest = self.fixture.manifest()
        authority = manifest["authorities"]["validate_fixture.py"]
        binding = authority["external_bindings"][0]
        binding["sha256"] = hashlib.sha256(payload).hexdigest()
        binding["size_bytes"] = len(payload)
        authority.pop("bundle_identity")
        authority["bundle_identity"] = _canonical_sha256(authority)
        self.fixture.write_manifest(manifest)

        with self.assertRaisesRegex(
            HistoricalAuthorityError,
            "validator modified tracked snapshot bytes",
        ):
            execute_historical_validator(
                self.fixture.authority(),
                repository_root=self.fixture.repository,
                runtime_profile=self.fixture.runtime.runtime_profile,
                bootstrap_path=BOOTSTRAP,
            )

    def test_changed_wrapper_identity_cannot_be_rebound_by_manifest_hash_update(self) -> None:
        manifest = self.fixture.manifest()
        authority = manifest["authorities"]["validate_fixture.py"]
        altered = deepcopy(authority["wrapper"])
        altered["sha256"] = "0" * 64
        authority["wrapper"] = altered
        authority["executable_closure"] = [
            altered if item["path"] == altered["path"] else item
            for item in authority["executable_closure"]
        ]
        authority.pop("bundle_identity")
        authority["bundle_identity"] = _canonical_sha256(authority)
        self.fixture.write_manifest(manifest)
        loaded = load_historical_authority_manifest(self.fixture.manifest_path)
        validation = validate_historical_validator_authority(
            loaded["authorities"]["validate_fixture.py"],
            repository_root=self.fixture.repository,
        )
        self.assertFalse(validation.acceptable)
        self.assertFalse(validation.closure_matches)
        wrapper = next(
            item
            for item in validation.verified_files
            if item["path"] == "src/crypto_lab/historical_wrapper.py"
        )
        self.assertFalse(wrapper["match"])

    def test_pinned_execution_rejects_evidence_current_mutated_wrapper_passes(self) -> None:
        current = subprocess.run(
            [
                sys.executable,
                str(self.fixture.repository / "scripts/validate_fixture.py"),
                str(self.fixture.invalid_evidence),
            ],
            cwd=self.fixture.repository,
            env={**os.environ, "PYTHONPATH": str(self.fixture.repository / "src")},
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertEqual(json.loads(current.stdout)["status"], "PASS")

        authority = self.fixture.authority()
        validation = validate_historical_validator_authority(
            authority,
            repository_root=self.fixture.repository,
        )
        self.assertTrue(validation.acceptable, validation.to_builtins())
        result = execute_historical_validator(
            authority,
            repository_root=self.fixture.repository,
            runtime_profile=self.fixture.runtime.runtime_profile,
            bootstrap_path=BOOTSTRAP,
        )
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(result.validator_status, "FAIL")
        self.assertFalse(result.passed)
        self.assertFalse(result.to_builtins()["current_root_validator_executed"])

    def test_authority_cannot_silently_select_a_different_commit(self) -> None:
        manifest = self.fixture.manifest()
        authority = manifest["authorities"]["validate_fixture.py"]
        authority["source_commit"] = self.fixture.mutated_commit
        authority["source_tree"] = _git(
            self.fixture.repository,
            "rev-parse",
            f"{self.fixture.mutated_commit}^{{tree}}",
        )
        authority.pop("bundle_identity")
        authority["bundle_identity"] = _canonical_sha256(authority)
        self.fixture.write_manifest(manifest)
        loaded = self.fixture.authority()
        validation = validate_historical_validator_authority(
            loaded,
            repository_root=self.fixture.repository,
        )
        self.assertTrue(validation.source_commit_is_ancestor)
        self.assertFalse(validation.closure_matches)
        self.assertFalse(validation.acceptable)

    def test_deleted_validator_in_selected_commit_fails_closure(self) -> None:
        _git(self.fixture.repository, "rm", "scripts/validate_fixture.py")
        _git(self.fixture.repository, "commit", "-m", "delete validator")
        selected = _git(self.fixture.repository, "rev-parse", "HEAD")
        manifest = self.fixture.manifest()
        authority = manifest["authorities"]["validate_fixture.py"]
        authority["source_commit"] = selected
        authority["source_tree"] = _git(self.fixture.repository, "rev-parse", "HEAD^{tree}")
        authority.pop("bundle_identity")
        authority["bundle_identity"] = _canonical_sha256(authority)
        self.fixture.write_manifest(manifest)
        validation = validate_historical_validator_authority(
            self.fixture.authority(),
            repository_root=self.fixture.repository,
        )
        self.assertFalse(validation.closure_matches)
        self.assertFalse(validation.acceptable)

    def test_rewritten_history_without_ancestry_fails_closed(self) -> None:
        authority = self.fixture.authority()
        _git(self.fixture.repository, "switch", "--orphan", "rewritten-history")
        _git(self.fixture.repository, "rm", "-rf", "--ignore-unmatch", ".")
        (self.fixture.repository / "README.md").write_text("rewritten\n", encoding="utf-8")
        _write(self.fixture.repository / "SSOT.md", "rewritten Product authority\n")
        _write(self.fixture.repository / "src/crypto_lab/__init__.py", '"""rewritten"""\n')
        _git(self.fixture.repository, "add", "README.md", "SSOT.md", "src")
        _git(self.fixture.repository, "commit", "-m", "rewritten root")
        validation = validate_historical_validator_authority(
            authority,
            repository_root=self.fixture.repository,
        )
        self.assertFalse(validation.source_commit_is_ancestor)
        self.assertFalse(validation.acceptable)
        self.assertEqual(validation.state, HistoricalValidationState.EVIDENCE_CORRUPT)


if __name__ == "__main__":
    unittest.main()
