from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from crypto_lab.hashing import canonical_json_bytes, canonical_sha256
from crypto_lab.result_status import FinancialResultStatus
from crypto_lab.result_status import HistoricalCopyRole
from crypto_lab.result_status import HistoricalResultClass
from crypto_lab.result_status import HistoricalRunStatus
from crypto_lab.result_status import HistoricalStatusReason
from crypto_lab.result_status import R2_ACTIVE_INVENTORY_SUPERSEDED_RESULTS
from crypto_lab.result_status import R2_ACTIVE_INVENTORY_SUPERSESSION_AUTHORITY
from crypto_lab.result_status import R2_AUDITED_BASELINE_COMMIT
from crypto_lab.result_status import ReplacementRequirement
from crypto_lab.result_status import ResultNotActiveError
from crypto_lab.result_status import build_active_inventory_supersession_record_v7
from crypto_lab.result_status import build_active_inventory_supersession_registry_v7
from crypto_lab.result_status import load_historical_result_registry
from crypto_lab.result_status import require_active_result
from crypto_lab.result_status import resolve_result_status
from scripts.build_r2_active_inventory_supersession_status import (
    EXPECTED_ACTIVE_INVENTORY_EVIDENCE_IDENTITIES,
    ActiveInventorySupersessionBuildError,
    build_registry,
    main,
)
from tests.helpers import initialize_product_repository


REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE = "5" * 40
RECORDED_TEXT = "2026-09-01T21:00:00Z"
RECORDED = datetime(2026, 9, 1, 21, tzinfo=UTC)
V7_LEAVES = (
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
    for name in V7_LEAVES:
        (run / name).write_bytes(f"{relative}:{name}\n".encode())
    return run


def _synthetic_fixture(root: Path) -> tuple[Path, dict[str, Path], bytes]:
    initialize_product_repository(root)
    runs: dict[str, Path] = {}
    records: list[dict[str, object]] = []
    for logical_id, expected in R2_ACTIVE_INVENTORY_SUPERSEDED_RESULTS.items():
        result_class = HistoricalResultClass(expected["result_class"])
        for copy_role, key in (
            (HistoricalCopyRole.PRIMARY, "primary_path"),
            (HistoricalCopyRole.REPLAY, "replay_path"),
        ):
            run = _make_run(root, expected[key])
            runs[f"{logical_id}:{copy_role.value}"] = run
            records.append(
                build_active_inventory_supersession_record_v7(
                    run,
                    repository_root=root,
                    logical_result_id=logical_id,
                    market_profile=expected["market_profile"],
                    result_class=result_class,
                    copy_role=copy_role,
                ),
            )
    payload = build_active_inventory_supersession_registry_v7(
        reversed(records),
        authority_id=R2_ACTIVE_INVENTORY_SUPERSESSION_AUTHORITY,
        audited_baseline_commit=R2_AUDITED_BASELINE_COMMIT,
        source_commit=SOURCE,
        recorded_at_utc=RECORDED,
    )
    registry = root / "active-inventory-supersession.json"
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


class R2ActiveInventorySupersessionTests(unittest.TestCase):
    def test_exact_retry_011_scope_is_canonical_and_only_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry_path, _runs, payload = _synthetic_fixture(Path(temporary))
            registry = load_historical_result_registry(registry_path)
            rebuilt = build_active_inventory_supersession_registry_v7(
                reversed(json.loads(payload)["records"]),
                authority_id=R2_ACTIVE_INVENTORY_SUPERSESSION_AUTHORITY,
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
                is HistoricalStatusReason.OFFICIAL_ACTIVE_INVENTORY_SUPERSESSION
                and record.replacement_requirement
                is ReplacementRequirement.OFFICIAL_ACTIVE_INVENTORY_REBUILD
                for record in registry.records
            ),
        )

    def test_every_retry_011_primary_and_replay_is_ineligible(self) -> None:
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
            with self.assertRaisesRegex(ValueError, "exact active-inventory"):
                load_historical_result_registry(missing_pair)

            raw = json.loads(payload)
            raw["records"][0]["reason_code"] = "CLAIM_HOLDOUT_SEMANTIC_SUPERSESSION"
            wrong_reason = root / "wrong-reason.json"
            wrong_reason.write_bytes(_recompute(raw))
            with self.assertRaisesRegex(ValueError, "active-inventory supersession contract"):
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
            for item in R2_ACTIVE_INVENTORY_SUPERSEDED_RESULTS.values()
            for key in ("primary_path", "replay_path")
        }
        self.assertEqual(declared, set(EXPECTED_ACTIVE_INVENTORY_EVIDENCE_IDENTITIES))
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
            initialize_product_repository(root)
            for relative in EXPECTED_ACTIVE_INVENTORY_EVIDENCE_IDENTITIES:
                target_dir = root / relative
                target_dir.mkdir(parents=True)
                for name in V7_LEAVES:
                    shutil.copyfile(REPOSITORY / relative / name, target_dir / name)
            relative = next(iter(EXPECTED_ACTIVE_INVENTORY_EVIDENCE_IDENTITIES))
            (root / relative / "runtime_identity.json").write_bytes(
                (root / relative / "runtime_identity.json").read_bytes() + b"tamper\n",
            )
            with self.assertRaisesRegex(
                ActiveInventorySupersessionBuildError,
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
