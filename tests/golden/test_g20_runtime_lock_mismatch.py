from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from crypto_lab.config import RuntimeLock
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.runtime import RuntimeLockMismatch
from crypto_lab.runtime import run_after_runtime_preflight
from tests.helpers import ROOT


class G20RuntimeLockMismatchTests(unittest.TestCase):
    def test_wheel_mismatch_blocks_before_data_loading(self) -> None:
        lock = RuntimeLock.from_json_bytes((ROOT / "runtime.lock.json").read_bytes())
        payload = lock.to_builtins()
        payload["nautilus_wheel_sha256"] = "0" * 64
        mismatched = RuntimeLock.from_json_bytes(canonical_json_bytes(payload))
        data_loader_called = False

        def data_loader() -> str:
            nonlocal data_loader_called
            data_loader_called = True
            return "loaded"

        with self.assertRaises(RuntimeLockMismatch) as raised:
            run_after_runtime_preflight(
                mismatched,
                dependency_lock_path=ROOT / "requirements.lock.txt",
                operation=data_loader,
            )

        self.assertEqual(raised.exception.code, "RUNTIME_WHEEL_HASH_MISMATCH")
        self.assertFalse(data_loader_called)

    def test_dependency_lock_mismatch_blocks_before_data_loading(self) -> None:
        lock = RuntimeLock.from_json_bytes((ROOT / "runtime.lock.json").read_bytes())
        data_loader_called = False

        def data_loader() -> str:
            nonlocal data_loader_called
            data_loader_called = True
            return "loaded"

        with TemporaryDirectory() as temporary_directory:
            altered_lock = Path(temporary_directory) / "requirements.lock.txt"
            altered_lock.write_text("pip==24.0 --hash=sha256:" + "0" * 64 + "\n")
            with self.assertRaises(RuntimeLockMismatch) as raised:
                run_after_runtime_preflight(
                    lock,
                    dependency_lock_path=altered_lock,
                    operation=data_loader,
                )

        self.assertEqual(raised.exception.code, "RUNTIME_LOCK_MISMATCH")
        self.assertFalse(data_loader_called)


if __name__ == "__main__":
    unittest.main()
