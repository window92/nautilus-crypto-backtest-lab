from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.result_status import FinancialResultStatus
from crypto_lab.result_status import HistoricalCopyRole
from crypto_lab.result_status import HistoricalResultClass
from crypto_lab.result_status import HistoricalRunStatus
from crypto_lab.result_status import HistoricalStatusReason
from crypto_lab.result_status import R2_AUDITED_BASELINE_COMMIT
from crypto_lab.result_status import R2_CLAIM_SCHEMA_SUPERSEDED_RESULTS
from crypto_lab.result_status import R2_CLAIM_SCHEMA_SUPERSESSION_AUTHORITY
from crypto_lab.result_status import ReplacementRequirement
from crypto_lab.result_status import ResultNotActiveError
from crypto_lab.result_status import build_claim_schema_supersession_record_v4
from crypto_lab.result_status import build_claim_schema_supersession_registry_v4
from crypto_lab.result_status import load_historical_result_registry
from crypto_lab.result_status import require_active_result
from crypto_lab.result_status import resolve_result_status
from scripts.build_r2_claim_schema_supersession_status import (
    ClaimSchemaSupersessionBuildError,
)
from scripts.build_r2_claim_schema_supersession_status import (
    EXPECTED_CLAIM_SCHEMA_EVIDENCE_IDENTITIES,
)
from scripts.build_r2_claim_schema_supersession_status import build_registry
from scripts.build_r2_claim_schema_supersession_status import main


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE = "2" * 40
RECORDED = datetime(2026, 9, 1, 6, 0, tzinfo=UTC)
RECORDED_TEXT = "2026-09-01T06:00:00Z"
V4_LEAVES = (
    "component_validation.json",
    "evidence_manifest.json",
    "official_seal.json",
    "runtime_identity.json",
    "source_revision.json",
    "status.json",
)


def _make_run(root: Path, relative: str) -> Path:
    run = root / relative
    run.mkdir(parents=True)
    for name in V4_LEAVES:
        (run / name).write_bytes(f"{relative}:{name}\n".encode())
    return run


def _synthetic_fixture(root: Path) -> tuple[Path, dict[str, Path], bytes]:
    runs: dict[str, Path] = {}
    records: list[dict[str, object]] = []
    for logical_id, expected in R2_CLAIM_SCHEMA_SUPERSEDED_RESULTS.items():
        result_class = HistoricalResultClass(expected["result_class"])
        for copy_role, key in (
            (HistoricalCopyRole.PRIMARY, "primary_path"),
            (HistoricalCopyRole.REPLAY, "replay_path"),
        ):
            run = _make_run(root, expected[key])
            runs[f"{logical_id}:{copy_role.value}"] = run
            records.append(
                build_claim_schema_supersession_record_v4(
                    run,
                    repository_root=root,
                    logical_result_id=logical_id,
                    market_profile=expected["market_profile"],
                    result_class=result_class,
                    copy_role=copy_role,
                ),
            )
    payload = build_claim_schema_supersession_registry_v4(
        reversed(records),
        authority_id=R2_CLAIM_SCHEMA_SUPERSESSION_AUTHORITY,
        audited_baseline_commit=R2_AUDITED_BASELINE_COMMIT,
        source_commit=SOURCE,
        recorded_at_utc=RECORDED,
    )
    registry = root / "claim-schema-supersession.json"
    registry.write_bytes(payload)
    return registry, runs, payload


def _recompute(value: dict[str, object]) -> bytes:
    records = value["records"]
    assert isinstance(records, list)
    value["record_count"] = len(records)
    value["records_identity"] = canonical_sha256(records)
    value.pop("registry_identity", None)
    value["registry_identity"] = canonical_sha256(value)
    return canonical_json_bytes(value) + b"\n"


def _copy_real_fixture(root: Path) -> None:
    for relative in EXPECTED_CLAIM_SCHEMA_EVIDENCE_IDENTITIES:
        target = root / relative
        target.mkdir(parents=True)
        for name in V4_LEAVES:
            shutil.copyfile(REPOSITORY / relative / name, target / name)


class R2ClaimSchemaSupersessionTests(unittest.TestCase):
    def test_exact_retry_008_scope_is_canonical_and_only_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry_path, _runs, payload = _synthetic_fixture(Path(temporary))
            registry = load_historical_result_registry(registry_path)
            rebuilt = build_claim_schema_supersession_registry_v4(
                reversed(json.loads(payload)["records"]),
                authority_id=R2_CLAIM_SCHEMA_SUPERSESSION_AUTHORITY,
                audited_baseline_commit=R2_AUDITED_BASELINE_COMMIT,
                source_commit=SOURCE,
                recorded_at_utc=RECORDED,
            )

        self.assertEqual(rebuilt, payload)
        self.assertEqual(len(registry.records), 12)
        self.assertTrue(
            all(
                record.historical_run_status is HistoricalRunStatus.SUPERSEDED
                and record.financial_result_status is FinancialResultStatus.SUPERSEDED
                and record.reason_code
                is HistoricalStatusReason.SCIENTIFIC_LIMITATION_SCHEMA_SUPERSESSION
                and record.replacement_requirement
                is ReplacementRequirement.SCIENTIFIC_LIMITATION_SCHEMA_FIX_AND_REBUILD
                for record in registry.records
            ),
        )

    def test_every_retry_008_primary_and_replay_is_ineligible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry, runs, _payload = _synthetic_fixture(root)
            for run in runs.values():
                with self.assertRaises(ResultNotActiveError):
                    require_active_result(
                        run,
                        repository_root=root,
                        registry_paths=(registry,),
                    )

    def test_missing_pair_or_rehashed_semantic_mutation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry, runs, payload = _synthetic_fixture(root)
            raw = json.loads(payload)
            raw["records"].pop()
            missing_pair = root / "missing-pair.json"
            missing_pair.write_bytes(_recompute(raw))
            with self.assertRaisesRegex(ValueError, "exact claim-schema supersession scope"):
                load_historical_result_registry(missing_pair)

            raw = json.loads(payload)
            raw["records"][0]["reason_code"] = "RUNTIME_AUTHORITY_SUPERSESSION"
            wrong_reason = root / "wrong-reason.json"
            wrong_reason.write_bytes(_recompute(raw))
            with self.assertRaisesRegex(ValueError, "claim-schema supersession contract"):
                load_historical_result_registry(wrong_reason)

            run = next(iter(runs.values()))
            (run / "status.json").write_bytes(b"tampered but rehashed elsewhere\n")
            with self.assertRaisesRegex(ValueError, "evidence binding failed"):
                resolve_result_status(
                    run,
                    repository_root=root,
                    registry_paths=(registry,),
                )

    def test_real_builder_is_deterministic_and_does_not_rebless_tamper(self) -> None:
        declared = {
            item[key]
            for item in R2_CLAIM_SCHEMA_SUPERSEDED_RESULTS.values()
            for key in ("primary_path", "replay_path")
        }
        self.assertEqual(declared, set(EXPECTED_CLAIM_SCHEMA_EVIDENCE_IDENTITIES))
        first = build_registry(
            repository_root=REPOSITORY,
            source_commit=SOURCE,
            recorded_at_utc=RECORDED_TEXT,
        )
        second = build_registry(
            repository_root=REPOSITORY,
            source_commit=SOURCE,
            recorded_at_utc=RECORDED_TEXT,
        )
        self.assertEqual(first, second)
        self.assertEqual(first, canonical_json_bytes(json.loads(first)) + b"\n")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _copy_real_fixture(root)
            relative = next(iter(EXPECTED_CLAIM_SCHEMA_EVIDENCE_IDENTITIES))
            target = root / relative / "runtime_identity.json"
            target.write_bytes(target.read_bytes() + b"tamper\n")
            with self.assertRaisesRegex(
                ClaimSchemaSupersessionBuildError,
                "evidence identity mismatch",
            ):
                build_registry(
                    repository_root=root,
                    source_commit=SOURCE,
                    recorded_at_utc=RECORDED_TEXT,
                )

    def test_cli_emits_only_canonical_registry(self) -> None:
        expected = build_registry(
            repository_root=REPOSITORY,
            source_commit=SOURCE,
            recorded_at_utc=RECORDED_TEXT,
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
                        SOURCE,
                        "--recorded-at-utc",
                        RECORDED_TEXT,
                    ],
                ),
                0,
            )
        self.assertEqual(output.buffer.getvalue(), expected)


if __name__ == "__main__":
    unittest.main()
