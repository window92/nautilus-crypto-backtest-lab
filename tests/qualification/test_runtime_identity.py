from __future__ import annotations

import unittest

from crypto_lab.config import RuntimeLock
from crypto_lab.qualification import qualify_runtime_identity
from tests.helpers import ROOT


class RuntimeIdentityQualificationTests(unittest.TestCase):
    def test_current_process_matches_complete_runtime_lock(self) -> None:
        lock = RuntimeLock.from_json_bytes((ROOT / "runtime.lock.json").read_bytes())
        evidence = qualify_runtime_identity(
            lock,
            dependency_lock_path=ROOT / "requirements.lock.txt",
        )

        self.assertEqual(evidence["status"], "VERIFIED")
        self.assertEqual(evidence["nautilus_version"], "1.231.0")
        self.assertEqual(evidence["python_implementation"], "CPython")
        self.assertEqual(evidence["python_version"], "3.12.3")
        self.assertEqual(evidence["machine_architecture"], "x86_64")
        self.assertEqual(
            evidence["installed_wheel_sha256"],
            "8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216",
        )


if __name__ == "__main__":
    unittest.main()
