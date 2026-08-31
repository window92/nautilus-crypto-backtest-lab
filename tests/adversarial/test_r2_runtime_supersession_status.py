from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from crypto_lab.hashing import canonical_json_bytes, canonical_sha256
from crypto_lab.result_status import (
    FinancialResultStatus,
    HistoricalCopyRole,
    HistoricalResultClass,
    HistoricalRunStatus,
    HistoricalStatusReason,
    R2_AUDITED_BASELINE_COMMIT,
    R2_RUNTIME_SUPERSEDED_RESULTS,
    R2_RUNTIME_SUPERSESSION_AUTHORITY,
    ReplacementRequirement,
    ResultNotActiveError,
    build_runtime_supersession_record_v3,
    build_runtime_supersession_registry_v3,
    load_historical_result_registry,
    require_active_result,
    resolve_result_status,
)


SOURCE = "2" * 40
RECORDED = datetime(2026, 8, 31, 20, 0, tzinfo=UTC)
V3_LEAVES = (
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
    for name in V3_LEAVES:
        (run / name).write_bytes(f"{relative}:{name}\n".encode("utf-8"))
    return run


def _fixture(root: Path) -> tuple[Path, dict[str, Path], bytes]:
    runs: dict[str, Path] = {}
    records: list[dict[str, object]] = []
    for logical_id, expected in R2_RUNTIME_SUPERSEDED_RESULTS.items():
        result_class = HistoricalResultClass(expected["result_class"])
        for copy_role, key in (
            (HistoricalCopyRole.PRIMARY, "primary_path"),
            (HistoricalCopyRole.REPLAY, "replay_path"),
        ):
            run = _make_run(root, expected[key])
            runs[f"{logical_id}:{copy_role.value}"] = run
            records.append(
                build_runtime_supersession_record_v3(
                    run,
                    repository_root=root,
                    logical_result_id=logical_id,
                    market_profile=expected["market_profile"],
                    result_class=result_class,
                    copy_role=copy_role,
                ),
            )
    payload = build_runtime_supersession_registry_v3(
        reversed(records),
        authority_id=R2_RUNTIME_SUPERSESSION_AUTHORITY,
        audited_baseline_commit=R2_AUDITED_BASELINE_COMMIT,
        source_commit=SOURCE,
        recorded_at_utc=RECORDED,
    )
    registry = root / "runtime-supersession.json"
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


class R2RuntimeSupersessionStatusTests(unittest.TestCase):
    def test_exact_primary_replay_scope_is_canonical_and_only_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            registry_path, _runs, payload = _fixture(Path(temporary))
            registry = load_historical_result_registry(registry_path)
            rebuilt = build_runtime_supersession_registry_v3(
                reversed(json.loads(payload)["records"]),
                authority_id=R2_RUNTIME_SUPERSESSION_AUTHORITY,
                audited_baseline_commit=R2_AUDITED_BASELINE_COMMIT,
                source_commit=SOURCE,
                recorded_at_utc=RECORDED,
            )

        self.assertEqual(rebuilt, payload)
        self.assertEqual(len(registry.records), 2 * len(R2_RUNTIME_SUPERSEDED_RESULTS))
        self.assertTrue(
            all(
                record.historical_run_status is HistoricalRunStatus.SUPERSEDED
                and record.financial_result_status is FinancialResultStatus.SUPERSEDED
                and record.reason_code is HistoricalStatusReason.RUNTIME_AUTHORITY_SUPERSESSION
                and record.replacement_requirement
                is ReplacementRequirement.FINAL_PRODUCT_RUNTIME_REBUILD
                for record in registry.records
            ),
        )

    def test_superseded_candidate_and_benchmark_are_both_ineligible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry, runs, _payload = _fixture(root)
            for key in (
                "retry-002-spot-candidate-a:PRIMARY",
                "retry-003-spot-benchmark:REPLAY",
            ):
                with self.subTest(key=key), self.assertRaises(ResultNotActiveError):
                    require_active_result(
                        runs[key],
                        repository_root=root,
                        registry_paths=(registry,),
                    )

    def test_missing_pair_or_rehashed_status_mutation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry, runs, payload = _fixture(root)
            raw = json.loads(payload)
            raw["records"].pop()
            missing_pair = root / "missing-pair.json"
            missing_pair.write_bytes(_recompute(raw))
            with self.assertRaisesRegex(ValueError, "exact runtime-supersession scope"):
                load_historical_result_registry(missing_pair)

            run = runs["retry-002-spot-benchmark:PRIMARY"]
            (run / "runtime_identity.json").write_bytes(b"tampered but rehashed elsewhere\n")
            with self.assertRaisesRegex(ValueError, "evidence binding failed"):
                resolve_result_status(
                    run,
                    repository_root=root,
                    registry_paths=(registry,),
                )


if __name__ == "__main__":
    unittest.main()
