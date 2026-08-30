from __future__ import annotations

import base64
import csv
import hashlib
import json
import py_compile
import shutil
import sys
import tempfile
import unittest
import venv
from pathlib import Path

from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import RuntimeLock
from crypto_lab.runtime import RuntimeLockMismatch
from crypto_lab.runtime import inspect_installed_distribution_files
from crypto_lab.runtime import verify_installed_distribution_files
from crypto_lab.runtime import verify_runtime_lock


def _record_hash(payload: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
    return f"sha256={encoded}"


class InstalledRuntimeFileTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, str, str, bytes, bytes]:
        # The fixture lives in an actual temporary venv while staying small and
        # independent of the 66 MB locked Nautilus wheel.
        venv.EnvBuilder(with_pip=False).create(root)
        site = root / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
        package = site / "runtime_probe"
        dist_info = site / "runtime_probe-1.0.dist-info"
        package.mkdir(parents=True)
        dist_info.mkdir(parents=True)
        source = b"VALUE = 1\n"
        native = b"ELF-test-native-extension"
        files = {
            "runtime_probe/__init__.py": source,
            "runtime_probe/native.so": native,
            "runtime_probe-1.0.dist-info/METADATA": b"Name: runtime-probe\nVersion: 1.0\n",
            "runtime_probe-1.0.dist-info/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: false\n",
        }
        for relative, payload in files.items():
            path = site / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        record_relative = "runtime_probe-1.0.dist-info/RECORD"
        with (site / record_relative).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            for relative, payload in files.items():
                writer.writerow((relative, _record_hash(payload), len(payload)))
            writer.writerow((record_relative, "", ""))
        return site, record_relative, "runtime_probe", source, native

    def test_modify_delete_and_add_payload_files_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site, record, package, source, native = self._fixture(Path(temporary) / "venv")
            baseline = inspect_installed_distribution_files(
                site_packages_root=site,
                record_relative_path=record,
                package_relative_path=package,
            )

            def verify() -> None:
                verify_installed_distribution_files(
                    site_packages_root=site,
                    record_relative_path=record,
                    package_relative_path=package,
                    expected_payload_sha256=baseline["installed_payload_sha256"],
                    expected_payload_file_count=baseline["installed_payload_file_count"],
                )

            verify()
            source_path = site / package / "__init__.py"
            source_path.write_bytes(b"VALUE = 2\n")
            with self.assertRaises(RuntimeLockMismatch) as modified:
                verify()
            self.assertEqual(modified.exception.code, "RUNTIME_LOCK_MISMATCH")
            source_path.write_bytes(source)

            native_path = site / package / "native.so"
            native_path.unlink()
            with self.assertRaises(RuntimeLockMismatch) as missing:
                verify()
            self.assertEqual(missing.exception.code, "RUNTIME_LOCK_MISMATCH")
            native_path.write_bytes(native)

            extra = site / package / "unexpected.so"
            extra.write_bytes(b"unrecorded executable")
            with self.assertRaises(RuntimeLockMismatch) as added:
                verify()
            self.assertEqual(added.exception.code, "RUNTIME_LOCK_MISMATCH")

    def test_cache_is_the_only_unrecorded_file_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site, record, package, _source, _native = self._fixture(Path(temporary) / "venv")
            cache = site / package / "__pycache__" / "__init__.cpython-312.pyc"
            cache.parent.mkdir()
            py_compile.compile(
                str(site / package / "__init__.py"),
                cfile=str(cache),
                doraise=True,
            )
            evidence = inspect_installed_distribution_files(
                site_packages_root=site,
                record_relative_path=record,
                package_relative_path=package,
            )
            self.assertEqual(evidence["allowed_cache_file_count"], 1)
            self.assertTrue(evidence["cache_files_recompiled_and_verified"])
            cache.write_bytes(cache.read_bytes()[:-1] + b"X")
            with self.assertRaises(RuntimeLockMismatch):
                inspect_installed_distribution_files(
                    site_packages_root=site,
                    record_relative_path=record,
                    package_relative_path=package,
                )
            py_compile.compile(
                str(site / package / "__init__.py"),
                cfile=str(cache),
                doraise=True,
            )
            (cache.parent / "payload.py").write_bytes(b"not a cache")
            with self.assertRaises(RuntimeLockMismatch):
                inspect_installed_distribution_files(
                    site_packages_root=site,
                    record_relative_path=record,
                    package_relative_path=package,
                )

    def test_official_runtime_proof_is_required_and_payload_tamper_fails_closed(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        matches = sorted(
            (repository / "runs").glob(
                "comprehensive-audit-remediation-001-spot-benchmark-run-*",
            ),
        )
        self.assertEqual(len(matches), 1)
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "run"
            shutil.copytree(matches[0], copied)
            missing = check_evidence_directory(
                copied,
                repository_root=repository,
                official_source_required=True,
                source_revision_current_head_required=False,
            )
            self.assertEqual(missing.outcome.value, "CHECK_BLOCKED")
            self.assertIn("RUNTIME_LOCK_MISMATCH", missing.failure_codes)

            lock = json.loads((copied / "runtime.lock.json").read_text(encoding="utf-8"))
            proof = {
                "installed_files_verified": True,
                "cache_files_recompiled_and_verified": True,
                "installed_payload_sha256": "f" * 64,
                "installed_payload_file_count": lock["nautilus_installed_payload_file_count"],
                "installed_wheel_sha256": lock["nautilus_wheel_sha256"],
                "nautilus_version": lock["nautilus_version"],
                "python_version": lock["python_version"],
                "python_implementation": lock["python_implementation"],
                "python_abi": lock["python_abi"],
                "machine_architecture": lock["machine_architecture"],
                "dependency_lock_sha256": lock["dependency_lock_sha256"],
                "installed_record_sha256": "0" * 64,
                "installed_record_hashed_file_count": 1,
                "installed_native_extension_count": 1,
            }
            (copied / "runtime_identity.json").write_text(
                json.dumps(proof, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            result = json.loads((copied / "nautilus_result.json").read_text(encoding="utf-8"))
            result["runtime_identity_verified"] = True
            result["evidence_bindings"]["runtime_identity_sha256"] = hashlib.sha256(
                (copied / "runtime_identity.json").read_bytes(),
            ).hexdigest()
            (copied / "nautilus_result.json").write_text(
                json.dumps(result, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            tampered = check_evidence_directory(
                copied,
                repository_root=repository,
                official_source_required=True,
                source_revision_current_head_required=False,
            )
            self.assertEqual(tampered.outcome.value, "CHECK_BLOCKED")
            self.assertIn("RUNTIME_LOCK_MISMATCH", tampered.failure_codes)
            proof_check = next(
                item
                for item in tampered.checks
                if item["name"] == "installed_runtime_payload_proof"
            )
            self.assertFalse(proof_check["pass"])
            self.assertIn("installed_payload_sha256", proof_check["mismatches"])

            verified_proof = verify_runtime_lock(
                RuntimeLock.from_json_bytes((copied / "runtime.lock.json").read_bytes()),
                dependency_lock_path=repository / "requirements.lock.txt",
            )
            (copied / "runtime_identity.json").write_text(
                json.dumps(verified_proof, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            result["evidence_bindings"]["runtime_identity_sha256"] = hashlib.sha256(
                (copied / "runtime_identity.json").read_bytes(),
            ).hexdigest()
            (copied / "nautilus_result.json").write_text(
                json.dumps(result, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            verified = check_evidence_directory(
                copied,
                repository_root=repository,
                official_source_required=True,
                source_revision_current_head_required=False,
            )
            proof_check = next(
                item
                for item in verified.checks
                if item["name"] == "installed_runtime_payload_proof"
            )
            self.assertTrue(proof_check["pass"])
            self.assertNotIn("RUNTIME_LOCK_MISMATCH", verified.failure_codes)


if __name__ == "__main__":
    unittest.main()
