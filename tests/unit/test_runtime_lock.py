from __future__ import annotations

import json
import unittest

from crypto_lab.config import ConfigError
from crypto_lab.config import RuntimeLock
from tests.helpers import ROOT


class RuntimeLockSchemaTests(unittest.TestCase):
    def test_runtime_lock_v3_binds_installed_payload_and_excludes_project_source(self) -> None:
        payload = json.loads((ROOT / "runtime.lock.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], 3)
        self.assertEqual(payload["dependency_lock_filename"], "requirements.lock.txt")
        self.assertRegex(payload["nautilus_installed_payload_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(payload["nautilus_installed_payload_file_count"], 0)
        self.assertNotIn("project_git_commit", payload)
        self.assertNotIn("project_worktree_policy", payload)
        self.assertNotIn("project_source_tree_sha256", payload)

    def test_runtime_lock_rejects_project_git_identity_as_unknown(self) -> None:
        payload = json.loads((ROOT / "runtime.lock.json").read_text(encoding="utf-8"))
        payload["project_git_commit"] = "0" * 40
        with self.assertRaises(ConfigError):
            RuntimeLock.from_json_bytes(json.dumps(payload).encode("utf-8"))

    def test_runtime_lock_rejects_unknown_field(self) -> None:
        payload = json.loads((ROOT / "runtime.lock.json").read_text(encoding="utf-8"))
        payload["unrecorded_runtime_default"] = True
        with self.assertRaises(ConfigError):
            RuntimeLock.from_json_bytes(json.dumps(payload).encode("utf-8"))

    def test_runtime_lock_rejects_missing_field(self) -> None:
        payload = json.loads((ROOT / "runtime.lock.json").read_text(encoding="utf-8"))
        del payload["dependency_lock_sha256"]
        with self.assertRaises(ConfigError):
            RuntimeLock.from_json_bytes(json.dumps(payload).encode("utf-8"))

    def test_runtime_lock_rejects_obsolete_schema_version(self) -> None:
        payload = json.loads((ROOT / "runtime.lock.json").read_text(encoding="utf-8"))
        payload["schema_version"] = 1
        with self.assertRaises(ConfigError):
            RuntimeLock.from_json_bytes(json.dumps(payload).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
