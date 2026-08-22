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
        self.assertEqual(evidence["nautilus_version"], "2.0.0rc2")
        self.assertEqual(evidence["python_implementation"], "CPython")
        self.assertEqual(evidence["python_version"], "3.12.3")
        self.assertEqual(evidence["machine_architecture"], "x86_64")
        self.assertEqual(
            evidence["installed_wheel_sha256"],
            "716169aca15bfb615a27610a9230e670dec5be3d4606fea591fe64eca145a5ac",
        )


if __name__ == "__main__":
    unittest.main()
