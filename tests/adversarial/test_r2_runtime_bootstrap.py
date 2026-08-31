from __future__ import annotations

import base64
import contextlib
import csv
import hashlib
import io
import json
import os
import py_compile
import shutil
import struct
import subprocess
import tempfile
import unittest
import venv
from pathlib import Path
from typing import Any
from unittest import mock

from scripts.build_runtime_bootstrap_authority import build_authority
from scripts.build_runtime_bootstrap_authority import main as build_runtime_authority_main


ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "scripts/isolated_runtime_bootstrap.py"
CHILD_ENVIRONMENT = {
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "TZ": "UTC",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _record_hash(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
    return "sha256=" + encoded.decode("ascii")


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _source_identity(repository: Path, commit: str, relative: str) -> dict[str, Any]:
    payload = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    listing = _git(repository, "ls-tree", commit, "--", relative)
    return {
        "mode": listing.split(maxsplit=1)[0],
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


class IsolatedRuntimeFixture:
    """Small, content-addressed Python/Git fixture shared by R2 identity tests."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.repository = root / "product"
        self.repository.mkdir()
        _git(self.repository, "init", "-b", "main")
        _git(self.repository, "config", "user.name", "R2 Runtime Test")
        _git(self.repository, "config", "user.email", "r2-runtime@example.invalid")
        _git(
            self.repository,
            "remote",
            "add",
            "origin",
            "https://example.invalid/r2-runtime-product.git",
        )
        package = self.repository / "src/crypto_lab"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("\"\"\"Pinned test product.\"\"\"\n", encoding="utf-8")
        (package / "probe.py").write_text(
            """from __future__ import annotations

import json
from pathlib import Path


def main(argv: list[str]) -> int:
    import runtime_probe
    from _crypto_lab_verified_bootstrap import ATTESTATION

    output = {
        "attestation_identity": ATTESTATION["attestation_identity"],
        "environment": dict(ATTESTATION["environment"]),
        "initial_sys_path": list(ATTESTATION["initial_sys_path"]),
        "effective_sys_path": list(ATTESTATION["effective_sys_path"]),
        "module_file": __file__,
        "module_origin": __spec__.origin,
        "product_commit": ATTESTATION["product"]["source_commit"],
        "runtime_probe": runtime_probe.IDENTITY,
    }
    Path(argv[0]).write_text(json.dumps(output, sort_keys=True), encoding="utf-8")
    return 0
""",
            encoding="utf-8",
        )
        bootstrap_copy = self.repository / "scripts/isolated_runtime_bootstrap.py"
        bootstrap_copy.parent.mkdir(parents=True)
        shutil.copyfile(BOOTSTRAP, bootstrap_copy)
        (self.repository / "requirements.lock.txt").write_text(
            "runtime-probe==1.0 --hash=sha256:" + "a" * 64 + "\n",
            encoding="utf-8",
        )
        (self.repository / "research").mkdir()
        (self.repository / "research/trials.jsonl").write_bytes(b"")
        (self.repository / "research/history_anchors.jsonl").write_bytes(b"")
        (self.repository / "research/holdout_lock.json").write_text("{}\n", encoding="utf-8")
        _git(self.repository, "add", ".")
        _git(self.repository, "commit", "-m", "pinned product")
        self.commit = _git(self.repository, "rev-parse", "HEAD")
        self.tree = _git(self.repository, "rev-parse", "HEAD^{tree}")

        self.venv = root / "venv"
        venv.EnvBuilder(with_pip=False, clear=True).create(self.venv)
        self.python = self.venv / "bin/python"
        self.site = self.venv / "lib/python3.12/site-packages"
        self.site.mkdir(parents=True, exist_ok=True)
        package_root = self.site / "runtime_probe"
        dist_info = self.site / "runtime_probe-1.0.dist-info"
        package_root.mkdir()
        dist_info.mkdir()
        files = {
            "../../../bin/runtime-probe-cli": b"#!/bin/sh\nexit 0\n",
            "runtime_probe/__init__.py": b'IDENTITY = "record-verified-runtime-probe"\n',
            "runtime_probe/native_stub.so": b"ELF-fixture-not-executed\n",
            "runtime_probe_native.so": b"ELF-top-level-fixture-not-executed\n",
            "runtime_probe-1.0.dist-info/METADATA": (
                b"Metadata-Version: 2.1\nName: runtime-probe\nVersion: 1.0\n"
            ),
            "runtime_probe-1.0.dist-info/WHEEL": (
                b"Wheel-Version: 1.0\n"
                b"Generator: r2-test\n"
                b"Root-Is-Purelib: true\n"
                b"Tag: py3-none-any\n"
            ),
        }
        for relative, payload in files.items():
            path = self.site / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        record_relative = "runtime_probe-1.0.dist-info/RECORD"
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        for relative, payload in sorted(files.items()):
            writer.writerow((relative, _record_hash(payload), str(len(payload))))
        writer.writerow((record_relative, "", ""))
        record_path = self.site / record_relative
        record_path.write_text(stream.getvalue(), encoding="utf-8", newline="")

        material = [
            {
                "path": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
            }
            for relative, payload in sorted(files.items())
        ]
        self.distribution = {
            "dist_info_relative_path": "runtime_probe-1.0.dist-info",
            "package_relative_paths": ["runtime_probe", "runtime_probe_native.so"],
            "payload_file_count": len(material),
            "payload_identity": _canonical_sha256(material),
            "record_relative_path": record_relative,
            "record_sha256": _sha256(record_path),
        }
        initial = subprocess.run(
            [
                str(self.python),
                "-I",
                "-P",
                "-S",
                "-B",
                "-X",
                "pycache_prefix=/dev/null",
                "-c",
                "import json,sys;print(json.dumps(sys.path))",
            ],
            env=CHILD_ENVIRONMENT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.initial_sys_path = json.loads(initial.stdout)
        git_executable = Path(shutil.which("git") or "/usr/bin/git").resolve(strict=True)
        real_python = self.python.resolve(strict=True)
        self.runtime_profile = {
            "bootstrap_sha256": _sha256(BOOTSTRAP),
            "initial_sys_path": self.initial_sys_path,
            "python": {
                "executable_realpath": str(real_python),
                "executable_sha256": _sha256(real_python),
                "git_executable": str(git_executable),
                "git_executable_sha256": _sha256(git_executable),
                "pyvenv_cfg_sha256": _sha256(self.venv / "pyvenv.cfg"),
                "venv_executable": str(self.python),
            },
            "site_packages": {
                "dependency_lock_sha256": _sha256(
                    self.repository / "requirements.lock.txt",
                ),
                "distributions": [self.distribution],
                "root": str(self.site),
                "top_level_entries": [
                    "runtime_probe",
                    "runtime_probe-1.0.dist-info",
                    "runtime_probe_native.so",
                ],
            },
        }
        self.authority_path = root / "authority.json"
        self.output = root / "probe-output.json"
        self.write_authority()

    def authority(self) -> dict[str, Any]:
        return {
            "schema": "isolated-runtime-bootstrap-authority-v1",
            **self.runtime_profile,
            "product": {
                "package_prefix": "crypto_lab",
                "repository_identity": _git(self.repository, "remote", "get-url", "origin"),
                "source_commit": self.commit,
                "source_files": [
                    _source_identity(self.repository, self.commit, relative)
                    for relative in (
                        "src/crypto_lab/__init__.py",
                        "src/crypto_lab/probe.py",
                    )
                ],
                "source_root": "src",
                "source_tree": self.tree,
            },
            "allowed_targets": {
                "entrypoints": ["crypto_lab.probe:main"],
                "scripts": [],
            },
        }

    def write_authority(self) -> None:
        self.authority_path.write_text(
            json.dumps(self.authority(), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

    def run(
        self,
        *,
        environment: dict[str, str] | None = None,
        authority_path: Path | None = None,
        repository: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(self.python),
                "-I",
                "-P",
                "-S",
                "-B",
                "-X",
                "pycache_prefix=/dev/null",
                str(BOOTSTRAP),
                "--authority",
                str(authority_path or self.authority_path),
                "--repository",
                str(repository or self.repository),
                "--entrypoint",
                "crypto_lab.probe:main",
                "--",
                str(self.output),
            ],
            cwd=self.repository,
            env=environment or CHILD_ENVIRONMENT,
            check=False,
            capture_output=True,
            text=True,
        )


def _failure_reason(process: subprocess.CompletedProcess[str]) -> str:
    value = json.loads(process.stderr.strip().splitlines()[-1])
    if value.get("failure_code") != "RUNTIME_STARTUP_MISMATCH":
        raise AssertionError(value)
    return str(value["reason"])


class IsolatedRuntimeBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = IsolatedRuntimeFixture(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_exact_isolated_runtime_executes_pinned_product_bytes(self) -> None:
        process = self.fixture.run()
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(self.fixture.output.read_text(encoding="utf-8"))
        self.assertEqual(result["product_commit"], self.fixture.commit)
        self.assertEqual(result["environment"], CHILD_ENVIRONMENT)
        self.assertEqual(result["initial_sys_path"], self.fixture.initial_sys_path)
        self.assertEqual(
            result["effective_sys_path"],
            [*self.fixture.initial_sys_path, str(self.fixture.site)],
        )
        expected_module = str(self.fixture.repository / "src/crypto_lab/probe.py")
        self.assertEqual(result["module_file"], expected_module)
        self.assertEqual(result["module_origin"], expected_module)
        self.assertEqual(result["runtime_probe"], "record-verified-runtime-probe")
        self.assertEqual(len(result["attestation_identity"]), 64)

    def test_runtime_authority_builder_is_deterministic_complete_and_reports_resolved_commit(
        self,
    ) -> None:
        first = build_authority(
            repository=self.fixture.repository,
            python=self.fixture.python,
            source_commit="HEAD",
        )
        second = build_authority(
            repository=self.fixture.repository,
            python=self.fixture.python,
            source_commit=self.fixture.commit,
        )
        self.assertEqual(first, second)
        self.assertEqual(first["product"]["source_commit"], self.fixture.commit)
        self.assertEqual(
            [item["path"] for item in first["product"]["source_files"]],
            ["src/crypto_lab/__init__.py", "src/crypto_lab/probe.py"],
        )

        output = Path(self.temporary.name) / "built-runtime-authority.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = build_runtime_authority_main(
                [
                    "--repository",
                    str(self.fixture.repository),
                    "--python",
                    str(self.fixture.python),
                    "--source-commit",
                    "HEAD",
                    "--output",
                    str(output),
                ],
            )
        self.assertEqual(exit_code, 0)
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["product_source_commit"], self.fixture.commit)
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), first)

    def test_owner_child_uses_exact_environment_and_bootstrap_flags(self) -> None:
        from crypto_lab.owner import _official_child_command
        from crypto_lab.owner import _official_child_environment

        with mock.patch.dict(
            os.environ,
            {
                "PYTHONPATH": "/tmp/shadow",
                "PYTHONHOME": "/tmp/fake-home",
                "LD_PRELOAD": "/tmp/preload.so",
            },
            clear=False,
        ):
            environment = _official_child_environment()
        self.assertEqual(environment, CHILD_ENVIRONMENT)
        workflow = ROOT / "research/workflows/does-not-need-to-exist.json"
        command = _official_child_command(ROOT, workflow)
        self.assertEqual(
            command[1:8],
            [
                "-I",
                "-P",
                "-S",
                "-B",
                "-X",
                "pycache_prefix=/dev/null",
                str(BOOTSTRAP),
            ],
        )
        self.assertEqual(
            command[8:14],
            [
                "--authority",
                str(ROOT / "runtime-bootstrap-authority.json"),
                "--repository",
                str(ROOT),
                "--entrypoint",
                "crypto_lab.owner:main",
            ],
        )

    def test_m3_child_also_enters_through_the_exact_isolated_bootstrap(self) -> None:
        from scripts import run_m3_qualifications as qualification

        observed: dict[str, object] = {}

        def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
            observed["command"] = command
            observed["environment"] = kwargs["env"]
            summary = Path(command[command.index("--summary") + 1])
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text("{}\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        staging = Path(self.temporary.name) / "m3-staging"
        with mock.patch.object(qualification.subprocess, "run", side_effect=fake_run):
            self.assertEqual(
                qualification._run_child(
                    staging,
                    label="spot-primary",
                    profile="spot",
                    run_id="r2-runtime-qualification",
                ),
                {},
            )
        command = observed["command"]
        self.assertIsInstance(command, list)
        assert isinstance(command, list)
        self.assertEqual(
            command[1:8],
            [
                "-I",
                "-P",
                "-S",
                "-B",
                "-X",
                "pycache_prefix=/dev/null",
                str(BOOTSTRAP),
            ],
        )
        self.assertIn("--script", command)
        self.assertEqual(command[command.index("--script") + 1], "scripts/run_m3_child.py")
        self.assertEqual(observed["environment"], CHILD_ENVIRONMENT)

    def test_m3_positive_qualification_requires_persisted_startup_attestation(self) -> None:
        from scripts import run_m3_qualifications as qualification

        summary = {
            "state": "COMPLETED",
            "component_validation_outcome": "COMPONENT_CHECK_PASS",
            "failure_codes": [],
            "fills_count": 1,
            "runtime_startup_verified": True,
            "runtime_startup_target": "scripts/run_m3_child.py",
            "runtime_startup_attestation_sha256": "a" * 64,
        }
        qualification._assert_positive(summary, "spot-primary")
        for field, value in (
            ("runtime_startup_verified", False),
            ("runtime_startup_target", "crypto_lab.owner:main"),
            ("runtime_startup_attestation_sha256", "not-a-hash"),
        ):
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                qualification._assert_positive(
                    {**summary, field: value},
                    "spot-primary",
                )

    def test_pythonpath_sitecustomize_and_shadow_product_fail_before_import(self) -> None:
        shadow = Path(self.temporary.name) / "shadow"
        (shadow / "crypto_lab").mkdir(parents=True)
        marker = Path(self.temporary.name) / "startup-marker"
        (shadow / "sitecustomize.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
            encoding="utf-8",
        )
        (shadow / "crypto_lab/__init__.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('shadowed')\n",
            encoding="utf-8",
        )
        process = self.fixture.run(
            environment={**CHILD_ENVIRONMENT, "PYTHONPATH": str(shadow)},
        )
        self.assertEqual(process.returncode, 120, process.stderr)
        self.assertEqual(_failure_reason(process), "ENVIRONMENT_NOT_ALLOWLISTED")
        self.assertFalse(marker.exists())
        self.assertFalse(self.fixture.output.exists())

    def test_extra_pth_fails_exact_site_inventory(self) -> None:
        (self.fixture.site / "startup-injection.pth").write_text(
            "import definitely_not_authorized\n",
            encoding="utf-8",
        )
        process = self.fixture.run()
        self.assertEqual(process.returncode, 120, process.stderr)
        self.assertEqual(_failure_reason(process), "SITE_PACKAGES_INVENTORY_MISMATCH")

    def test_sitecustomize_usercustomize_and_shadow_dependency_fail_inventory(self) -> None:
        for name in ("sitecustomize.py", "usercustomize.py", "runtime_probe.py"):
            with self.subTest(name=name):
                path = self.fixture.site / name
                path.write_text("raise AssertionError('startup shadow executed')\n", encoding="utf-8")
                process = self.fixture.run()
                self.assertEqual(process.returncode, 120, process.stderr)
                self.assertEqual(_failure_reason(process), "SITE_PACKAGES_INVENTORY_MISMATCH")
                self.assertFalse(self.fixture.output.exists())
                path.unlink()

    def test_authority_builder_cannot_bless_unlocked_distribution(self) -> None:
        package = self.fixture.site / "surprise_dependency"
        dist = self.fixture.site / "surprise_dependency-9.9.dist-info"
        package.mkdir()
        dist.mkdir()
        files = {
            "surprise_dependency/__init__.py": b"VALUE = 'unlocked'\n",
            "surprise_dependency-9.9.dist-info/METADATA": (
                b"Metadata-Version: 2.1\nName: surprise-dependency\nVersion: 9.9\n"
            ),
        }
        for relative, payload in files.items():
            (self.fixture.site / relative).write_bytes(payload)
        record_relative = "surprise_dependency-9.9.dist-info/RECORD"
        with (self.fixture.site / record_relative).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            for relative, payload in sorted(files.items()):
                writer.writerow((relative, _record_hash(payload), str(len(payload))))
            writer.writerow((record_relative, "", ""))
        with self.assertRaisesRegex(ValueError, "differ from dependency lock"):
            build_authority(
                repository=self.fixture.repository,
                python=self.fixture.python,
                source_commit=self.fixture.commit,
            )

    def test_different_executable_identity_fails_before_import(self) -> None:
        authority = self.fixture.authority()
        authority["python"]["venv_executable"] = "/usr/bin/python3.12"
        self.fixture.authority_path.write_text(
            json.dumps(authority, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        process = self.fixture.run()
        self.assertEqual(process.returncode, 120, process.stderr)
        self.assertEqual(_failure_reason(process), "EXECUTABLE_IDENTITY_MISMATCH")
        self.assertFalse(self.fixture.output.exists())

    def test_symlinked_authority_is_rejected_before_read(self) -> None:
        link = Path(self.temporary.name) / "authority-link.json"
        link.symlink_to(self.fixture.authority_path)
        process = self.fixture.run(authority_path=link)
        self.assertEqual(process.returncode, 120, process.stderr)
        self.assertEqual(_failure_reason(process), "FILE_IDENTITY_MISMATCH")
        self.assertFalse(self.fixture.output.exists())

    def test_product_change_after_authority_fails_before_target(self) -> None:
        (self.fixture.repository / "src/crypto_lab/probe.py").write_text(
            "raise AssertionError('mutated worktree code executed')\n",
            encoding="utf-8",
        )
        process = self.fixture.run()
        self.assertEqual(process.returncode, 120, process.stderr)
        self.assertEqual(_failure_reason(process), "PRODUCT_SOURCE_IDENTITY_MISMATCH")
        self.assertFalse(self.fixture.output.exists())

    def test_only_declared_recovery_state_may_be_dirty_and_staging_may_not(self) -> None:
        authority = self.fixture.authority()
        authority["product"]["mutable_worktree"] = {
            "tracked_files": [
                "research/history_anchors.jsonl",
                "research/holdout_lock.json",
                "research/trials.jsonl",
            ],
            "untracked_roots": [".owner-runtime", "runs"],
        }
        self.fixture.authority_path.write_text(
            json.dumps(authority, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        (self.fixture.repository / "research/trials.jsonl").write_text(
            '{"state":"STARTED"}\n',
            encoding="utf-8",
        )
        runtime_state = self.fixture.repository / ".owner-runtime/fixture/state.json"
        runtime_state.parent.mkdir(parents=True)
        runtime_state.write_text("{}\n", encoding="utf-8")
        process = self.fixture.run()
        self.assertEqual(process.returncode, 0, process.stderr)

        _git(self.fixture.repository, "add", "research/trials.jsonl")
        self.fixture.output.unlink()
        staged = self.fixture.run()
        self.assertEqual(staged.returncode, 120, staged.stderr)
        self.assertEqual(_failure_reason(staged), "PRODUCT_SOURCE_IDENTITY_MISMATCH")

    def test_direct_owner_api_requires_bootstrap_state_before_side_effects(self) -> None:
        from crypto_lab.owner import _require_verified_startup
        from crypto_lab.research import ResearchError

        with self.assertRaises(ResearchError) as missing:
            _require_verified_startup()
        self.assertEqual(missing.exception.code, "RUNTIME_STARTUP_MISMATCH")
        direct = subprocess.run(
            [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/run_owner_workflow.py")],
            cwd=ROOT,
            env=CHILD_ENVIRONMENT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(direct.returncode, 120, direct.stderr)
        failure = json.loads(direct.stderr)
        self.assertEqual(failure["failure_code"], "RUNTIME_STARTUP_MISMATCH")

    def test_real_owner_entrypoint_observes_verified_frozen_attestation(self) -> None:
        process = subprocess.run(
            [
                str(ROOT / ".venv/bin/python"),
                "-I",
                "-P",
                "-S",
                "-B",
                "-X",
                "pycache_prefix=/dev/null",
                str(BOOTSTRAP),
                "--authority",
                str(ROOT / "runtime-bootstrap-authority.json"),
                "--repository",
                str(ROOT),
                "--entrypoint",
                "crypto_lab.owner:main",
                "--",
                "--help",
            ],
            cwd=ROOT,
            env=CHILD_ENVIRONMENT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("usage:", process.stdout)
        self.assertNotIn("RUNTIME_STARTUP_MISMATCH", process.stderr)

    def test_record_payload_mutation_fails_before_dependency_import(self) -> None:
        (self.fixture.site / "runtime_probe/__init__.py").write_text(
            "raise AssertionError('mutated dependency executed')\n",
            encoding="utf-8",
        )
        process = self.fixture.run()
        self.assertEqual(process.returncode, 120, process.stderr)
        self.assertEqual(_failure_reason(process), "DISTRIBUTION_RECORD_MISMATCH")
        self.assertFalse(self.fixture.output.exists())

    def test_stale_dependency_pyc_is_ignored_even_when_header_matches_source(self) -> None:
        source = self.fixture.site / "runtime_probe/__init__.py"
        original = source.read_bytes()
        original_stat = source.stat()
        marker = Path(self.temporary.name) / "stale-pyc-executed"
        source.write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed')\n"
            "IDENTITY = 'stale-bytecode'\n",
            encoding="utf-8",
        )
        os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        cache = Path(py_compile.compile(str(source), doraise=True))
        source.write_bytes(original)
        os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        cache_bytes = bytearray(cache.read_bytes())
        cache_bytes[12:16] = struct.pack("<I", len(original))
        cache.write_bytes(cache_bytes)
        process = self.fixture.run()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertFalse(marker.exists())
        result = json.loads(self.fixture.output.read_text(encoding="utf-8"))
        self.assertEqual(result["runtime_probe"], "record-verified-runtime-probe")

    def test_native_extension_payload_mutation_fails_before_product_import(self) -> None:
        (self.fixture.site / "runtime_probe/native_stub.so").write_bytes(
            b"mutated-native-payload\n",
        )
        process = self.fixture.run()
        self.assertEqual(process.returncode, 120, process.stderr)
        self.assertEqual(_failure_reason(process), "DISTRIBUTION_RECORD_MISMATCH")
        self.assertFalse(self.fixture.output.exists())


if __name__ == "__main__":
    unittest.main()
