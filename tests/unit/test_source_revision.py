from __future__ import annotations

import unittest
from datetime import timezone

from crypto_lab.config import ConfigError
from crypto_lab.config import SourceRevision
from tests.helpers import encode_config
from tests.helpers import load_source_revision_dict


class SourceRevisionContractTests(unittest.TestCase):
    def test_source_revision_round_trip_has_exact_separate_fields(self) -> None:
        payload = load_source_revision_dict()
        revision = SourceRevision.from_json_bytes(encode_config(payload))
        round_tripped = SourceRevision.from_json_bytes(revision.to_json_bytes())

        self.assertEqual(round_tripped, revision)
        self.assertEqual(
            set(revision.to_builtins()),
            {
                "repository",
                "branch_ref",
                "git_commit",
                "git_tree",
                "clean_worktree",
                "captured_at_utc",
            },
        )
        self.assertEqual(revision.git_commit, "55338e31e99cfa30683858747faf16a4f5f46287")
        self.assertEqual(revision.git_tree, "0e1316c7c04235431f6001c66fa63bf59f3992dc")
        self.assertTrue(revision.clean_worktree)
        self.assertIs(revision.captured_at_utc.tzinfo, timezone.utc)

    def test_source_revision_rejects_missing_field(self) -> None:
        payload = load_source_revision_dict()
        del payload["git_tree"]
        with self.assertRaises(ConfigError):
            SourceRevision.from_json_bytes(encode_config(payload))

    def test_source_revision_rejects_unknown_field(self) -> None:
        payload = load_source_revision_dict()
        payload["project_source_tree_sha256"] = "0" * 64
        with self.assertRaises(ConfigError):
            SourceRevision.from_json_bytes(encode_config(payload))

    def test_source_revision_rejects_invalid_git_object_id(self) -> None:
        for field in ("git_commit", "git_tree"):
            with self.subTest(field=field):
                payload = load_source_revision_dict()
                payload[field] = "0" * 64
                with self.assertRaises(ConfigError):
                    SourceRevision.from_json_bytes(encode_config(payload))

    def test_source_revision_rejects_non_utc_capture_time(self) -> None:
        payload = load_source_revision_dict()
        payload["captured_at_utc"] = "2026-08-21T02:00:00+02:00"
        with self.assertRaises(ConfigError):
            SourceRevision.from_json_bytes(encode_config(payload))


if __name__ == "__main__":
    unittest.main()
