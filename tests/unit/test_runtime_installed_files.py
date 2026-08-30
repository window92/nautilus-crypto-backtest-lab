from __future__ import annotations

import base64
import csv
import hashlib
import py_compile
import sys
import tempfile
import unittest
import venv
from pathlib import Path

from crypto_lab.runtime import RuntimeLockMismatch
from crypto_lab.runtime import inspect_installed_distribution_files
from crypto_lab.runtime import verify_installed_distribution_files


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


if __name__ == "__main__":
    unittest.main()
