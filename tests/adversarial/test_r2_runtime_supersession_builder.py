from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.result_status import R2_RUNTIME_SUPERSEDED_RESULTS
from scripts.build_r2_runtime_supersession_status import (
    EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES,
    RuntimeSupersessionBuildError,
    build_registry,
    main,
)


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_COMMIT = "2" * 40
RECORDED_AT = "2026-08-31T20:00:00Z"


def _copy_fixture(root: Path) -> None:
    for relative, hashes in EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES.items():
        target = root / relative
        target.mkdir(parents=True)
        for name in hashes:
            shutil.copyfile(REPOSITORY / relative / name, target / name)


class R2RuntimeSupersessionBuilderTests(unittest.TestCase):
    def test_frozen_inventory_matches_all_actual_terminal_bytes(self) -> None:
        declared = {
            item[key]
            for item in R2_RUNTIME_SUPERSEDED_RESULTS.values()
            for key in ("primary_path", "replay_path")
        }
        self.assertEqual(declared, set(EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES))
        first = build_registry(
            repository_root=REPOSITORY,
            source_commit=SOURCE_COMMIT,
            recorded_at_utc=RECORDED_AT,
        )
        second = build_registry(
            repository_root=REPOSITORY,
            source_commit=SOURCE_COMMIT,
            recorded_at_utc=RECORDED_AT,
        )
        self.assertEqual(first, second)
        self.assertEqual(first, canonical_json_bytes(json.loads(first)) + b"\n")

    def test_missing_or_tampered_bound_leaf_is_not_reblessed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy_fixture(root)
            relative = next(iter(EXPECTED_RUNTIME_SUPERSESSION_EVIDENCE_HASHES))
            target = root / relative / "runtime_identity.json"
            target.write_bytes(target.read_bytes() + b"tamper\n")
            with self.assertRaisesRegex(
                RuntimeSupersessionBuildError,
                "evidence identity mismatch",
            ):
                build_registry(
                    repository_root=root,
                    source_commit=SOURCE_COMMIT,
                    recorded_at_utc=RECORDED_AT,
                )

    def test_cli_emits_only_the_canonical_registry(self) -> None:
        expected = build_registry(
            repository_root=REPOSITORY,
            source_commit=SOURCE_COMMIT,
            recorded_at_utc=RECORDED_AT,
        )
        class CapturedStdout(io.StringIO):
            def __init__(self) -> None:
                super().__init__()
                self.buffer = io.BytesIO()

        output = CapturedStdout()
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                main(
                    [
                        "--repository",
                        str(REPOSITORY),
                        "--source-commit",
                        SOURCE_COMMIT,
                        "--recorded-at-utc",
                        RECORDED_AT,
                    ],
                ),
                0,
            )
        self.assertEqual(output.buffer.getvalue(), expected)


if __name__ == "__main__":
    unittest.main()
