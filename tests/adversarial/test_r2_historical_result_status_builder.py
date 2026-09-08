from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.result_status import R2_EXPECTED_HISTORICAL_RESULTS
from scripts.build_adversarial_remediation_002_result_status import (
    EXPECTED_HISTORICAL_EVIDENCE_HASHES,
    OUTPUT_RELATIVE_PATH,
    HistoricalResultStatusBuildError,
    build_registry,
    main,
    write_fresh_registry,
)
from tests.helpers import initialize_product_repository


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE_COMMIT = "1" * 40
RECORDED_AT = "2026-08-31T12:00:00Z"
EXPECTED_HASH_INVENTORY_IDENTITY = (
    "4644d95d963c7708d9ccd18c24bfae4a94099a2721ef67406bdc39d5b20bd201"
)
EXPECTED_PATHS = (
    "runs/comprehensive-audit-remediation-003-perpetual-benchmark-run-a0e2b2553ed4",
    "runs/comprehensive-audit-remediation-003-perpetual-candidate-a-run-5b7c5dba7f8b",
    "runs/comprehensive-audit-remediation-003-perpetual-candidate-b-run-85bb3192f559",
    "runs/comprehensive-audit-remediation-003-spot-benchmark-run-d3e25d52686e",
    "runs/comprehensive-audit-remediation-003-spot-candidate-a-run-253086685e94",
    "runs/comprehensive-audit-remediation-003-spot-candidate-b-run-736f07f7755e",
    (
        "runs/replays/comprehensive-audit-remediation-003-perpetual-benchmark-"
        "buy-and-hold-1x-development/comprehensive-audit-remediation-003-"
        "perpetual-benchmark-run-a0e2b2553ed4"
    ),
    (
        "runs/replays/comprehensive-audit-remediation-003-perpetual-candidate-a-"
        "development/comprehensive-audit-remediation-003-perpetual-candidate-a-"
        "run-5b7c5dba7f8b"
    ),
    (
        "runs/replays/comprehensive-audit-remediation-003-perpetual-candidate-b-"
        "development/comprehensive-audit-remediation-003-perpetual-candidate-b-"
        "run-85bb3192f559"
    ),
    (
        "runs/replays/comprehensive-audit-remediation-003-spot-benchmark-"
        "buy-and-hold-1x-development/comprehensive-audit-remediation-003-"
        "spot-benchmark-run-d3e25d52686e"
    ),
    (
        "runs/replays/comprehensive-audit-remediation-003-spot-candidate-a-"
        "development/comprehensive-audit-remediation-003-spot-candidate-a-"
        "run-253086685e94"
    ),
    (
        "runs/replays/comprehensive-audit-remediation-003-spot-candidate-b-"
        "development/comprehensive-audit-remediation-003-spot-candidate-b-"
        "run-736f07f7755e"
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def frozen_inventory_identity() -> str:
    payload = json.dumps(
        EXPECTED_HISTORICAL_EVIDENCE_HASHES,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def copy_historical_fixture(destination: Path) -> None:
    initialize_product_repository(destination)
    for relative in EXPECTED_PATHS:
        target = destination / relative
        target.mkdir(parents=True)
        for filename in ("checker.json", "evidence_manifest.json", "status.json"):
            shutil.copyfile(REPOSITORY / relative / filename, target / filename)


class R2HistoricalResultStatusBuilderTests(unittest.TestCase):
    def test_exact_twelve_paths_and_all_frozen_hashes_match_historical_bytes(self) -> None:
        declared_paths = {
            material[key]
            for material in R2_EXPECTED_HISTORICAL_RESULTS.values()
            for key in ("primary_path", "replay_path")
        }
        self.assertEqual(tuple(sorted(declared_paths)), EXPECTED_PATHS)
        self.assertEqual(
            set(EXPECTED_HISTORICAL_EVIDENCE_HASHES),
            set(EXPECTED_PATHS),
        )
        self.assertEqual(frozen_inventory_identity(), EXPECTED_HASH_INVENTORY_IDENTITY)
        for relative, expected in EXPECTED_HISTORICAL_EVIDENCE_HASHES.items():
            with self.subTest(relative=relative):
                self.assertEqual(
                    {
                        filename: sha256_file(REPOSITORY / relative / filename)
                        for filename in (
                            "checker.json",
                            "evidence_manifest.json",
                            "status.json",
                        )
                    },
                    expected,
                )

    def test_builder_is_canonical_deterministic_and_covers_status_contract(self) -> None:
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
        value = json.loads(first)
        self.assertEqual(first, canonical_json_bytes(value) + b"\n")
        self.assertEqual(value["record_count"], 12)
        self.assertEqual({item["path"] for item in value["records"]}, set(EXPECTED_PATHS))

        candidates = [item for item in value["records"] if item["result_class"] == "CANDIDATE"]
        benchmarks = [item for item in value["records"] if item["result_class"] == "BENCHMARK"]
        self.assertEqual(len(candidates), 8)
        self.assertEqual(len(benchmarks), 4)
        self.assertTrue(
            all(
                item["historical_run_status"] == "REVOKED"
                and item["financial_result_status"] == "INVALIDATED"
                and item["reason_code"] == "WARMUP_SCORING_ELIGIBILITY_VIOLATION"
                for item in candidates
            ),
        )
        self.assertTrue(
            all(
                item["historical_run_status"] == "SUPERSEDED"
                and item["financial_result_status"] == "SUPERSEDED"
                and item["reason_code"] == "RESULT_CONTRACT_V2_SUPERSESSION"
                for item in benchmarks
            ),
        )

    def test_missing_or_tampered_historical_leaf_fails_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing_root = Path(temporary) / "missing"
            missing_root.mkdir()
            copy_historical_fixture(missing_root)
            missing = missing_root / EXPECTED_PATHS[0] / "checker.json"
            missing.unlink()
            with self.assertRaisesRegex(
                HistoricalResultStatusBuildError,
                "cannot bind immutable historical result",
            ):
                build_registry(
                    repository_root=missing_root,
                    source_commit=SOURCE_COMMIT,
                    recorded_at_utc=RECORDED_AT,
                )
            self.assertFalse((missing_root / OUTPUT_RELATIVE_PATH).exists())

            tampered_root = Path(temporary) / "tampered"
            tampered_root.mkdir()
            copy_historical_fixture(tampered_root)
            tampered = tampered_root / EXPECTED_PATHS[-1] / "status.json"
            tampered.write_bytes(tampered.read_bytes() + b"tampered\n")
            with self.assertRaisesRegex(
                HistoricalResultStatusBuildError,
                "historical evidence identity mismatch",
            ):
                build_registry(
                    repository_root=tampered_root,
                    source_commit=SOURCE_COMMIT,
                    recorded_at_utc=RECORDED_AT,
                )
            self.assertFalse((tampered_root / OUTPUT_RELATIVE_PATH).exists())

    def test_cli_creates_fixed_output_once_and_never_overwrites_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_historical_fixture(root)
            arguments = [
                "--repository",
                str(root),
                "--source-commit",
                SOURCE_COMMIT,
                "--recorded-at-utc",
                RECORDED_AT,
            ]
            with contextlib.redirect_stdout(io.StringIO()) as standard_output:
                self.assertEqual(main(arguments), 0)
            output = root / OUTPUT_RELATIVE_PATH
            original = output.read_bytes()
            summary = json.loads(standard_output.getvalue())
            self.assertEqual(summary["output"], OUTPUT_RELATIVE_PATH.as_posix())
            self.assertEqual(summary["record_count"], 12)

            with self.assertRaisesRegex(
                HistoricalResultStatusBuildError,
                "immutable output collision",
            ):
                main(arguments)
            self.assertEqual(output.read_bytes(), original)

    def test_output_symlink_and_symlinked_parent_are_rejected_without_mutation(self) -> None:
        payload = b"canonical-registry-bytes\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "target-link"
            initialize_product_repository(root)
            parent = root / OUTPUT_RELATIVE_PATH.parent
            parent.mkdir(parents=True)
            outside = Path(temporary) / "outside.json"
            outside.write_bytes(b"outside\n")
            (root / OUTPUT_RELATIVE_PATH).symlink_to(outside)
            with self.assertRaisesRegex(
                HistoricalResultStatusBuildError,
                "immutable output collision",
            ):
                write_fresh_registry(repository_root=root, payload=payload)
            self.assertEqual(outside.read_bytes(), b"outside\n")

            parent_link_root = Path(temporary) / "parent-link"
            initialize_product_repository(parent_link_root)
            (parent_link_root / "evidence").symlink_to(Path(temporary))
            with self.assertRaisesRegex(
                HistoricalResultStatusBuildError,
                "output directory is not an exact directory",
            ):
                write_fresh_registry(repository_root=parent_link_root, payload=payload)

    def test_source_commit_and_recorded_time_must_be_explicit(self) -> None:
        for source in ("HEAD", "A" * 40, "1" * 39, "1" * 41):
            with self.subTest(source=source), self.assertRaises(
                HistoricalResultStatusBuildError,
            ):
                build_registry(
                    repository_root=REPOSITORY,
                    source_commit=source,
                    recorded_at_utc=RECORDED_AT,
                )
        for recorded in (
            "2026-08-31T12:00:00",
            "2026-08-31T12:00:00+00:00",
            "2026-08-31T12:00:00+02:00",
            "not-a-time",
        ):
            with self.subTest(recorded=recorded), self.assertRaises(
                HistoricalResultStatusBuildError,
            ):
                build_registry(
                    repository_root=REPOSITORY,
                    source_commit=SOURCE_COMMIT,
                    recorded_at_utc=recorded,
                )


if __name__ == "__main__":
    unittest.main()
