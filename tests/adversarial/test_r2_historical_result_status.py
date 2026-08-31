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
    R2_EXPECTED_HISTORICAL_RESULTS,
    R2_RESULT_STATUS_AUTHORITY,
    ResultNotActiveError,
    build_historical_result_record_v2,
    build_historical_result_registry_v2,
    load_historical_result_registry,
    require_active_result,
    resolve_result_status,
    revoked_result_for_directory,
)


SOURCE = "1" * 40
RECORDED = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def make_run(root: Path, relative_path: str) -> Path:
    run = root / relative_path
    run.mkdir(parents=True)
    for filename in ("checker.json", "evidence_manifest.json", "status.json"):
        (run / filename).write_bytes(f"{relative_path}:{filename}\n".encode("utf-8"))
    return run


def recompute(value: dict[str, object]) -> bytes:
    records = value["records"]
    assert isinstance(records, list)
    value["record_count"] = len(records)
    value["records_identity"] = canonical_sha256(records)
    value.pop("registry_identity", None)
    value["registry_identity"] = canonical_sha256(value)
    return canonical_json_bytes(value) + b"\n"


class ResultStatusFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.runs: dict[tuple[str, HistoricalCopyRole], Path] = {}
        records: list[dict[str, object]] = []
        for logical_result_id, expected in R2_EXPECTED_HISTORICAL_RESULTS.items():
            result_class = HistoricalResultClass(expected["result_class"])
            for copy_role, path_key in (
                (HistoricalCopyRole.PRIMARY, "primary_path"),
                (HistoricalCopyRole.REPLAY, "replay_path"),
            ):
                run = make_run(root, expected[path_key])
                self.runs[(logical_result_id, copy_role)] = run
                records.append(
                    build_historical_result_record_v2(
                        run,
                        repository_root=root,
                        logical_result_id=logical_result_id,
                        market_profile=expected["market_profile"],
                        result_class=result_class,
                        copy_role=copy_role,
                    ),
                )
        self.candidate_primary = self.runs[
            ("spot-candidate-a", HistoricalCopyRole.PRIMARY)
        ]
        self.candidate_replay = self.runs[("spot-candidate-a", HistoricalCopyRole.REPLAY)]
        self.benchmark_primary = self.runs[("spot-benchmark", HistoricalCopyRole.PRIMARY)]
        self.benchmark_replay = self.runs[("spot-benchmark", HistoricalCopyRole.REPLAY)]
        self.active = make_run(root, "runs/new-active-result")
        self.payload = build_historical_result_registry_v2(
            reversed(records),
            authority_id=R2_RESULT_STATUS_AUTHORITY,
            audited_baseline_commit=R2_AUDITED_BASELINE_COMMIT,
            source_commit=SOURCE,
            recorded_at_utc=RECORDED,
        )
        status_root = root / "status"
        status_root.mkdir()
        self.registry = status_root / "r2-result-status.json"
        self.registry.write_bytes(self.payload)


class R2HistoricalResultStatusTests(unittest.TestCase):
    def test_candidate_is_invalidated_and_benchmark_is_only_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResultStatusFixture(Path(temporary))
            registry = load_historical_result_registry(fixture.registry)
        candidate = registry.for_path(
            R2_EXPECTED_HISTORICAL_RESULTS["spot-candidate-a"]["primary_path"],
        )
        benchmark = registry.for_path(
            R2_EXPECTED_HISTORICAL_RESULTS["spot-benchmark"]["primary_path"],
        )
        assert candidate is not None and benchmark is not None
        self.assertEqual(candidate.historical_run_status, HistoricalRunStatus.REVOKED)
        self.assertEqual(candidate.financial_result_status, FinancialResultStatus.INVALIDATED)
        self.assertEqual(
            candidate.reason_code,
            HistoricalStatusReason.WARMUP_SCORING_ELIGIBILITY_VIOLATION,
        )
        self.assertEqual(benchmark.historical_run_status, HistoricalRunStatus.SUPERSEDED)
        self.assertEqual(benchmark.financial_result_status, FinancialResultStatus.SUPERSEDED)
        self.assertEqual(
            benchmark.reason_code,
            HistoricalStatusReason.RESULT_CONTRACT_V2_SUPERSESSION,
        )
        self.assertFalse(registry.final_holdout_authorized)
        self.assertFalse(registry.profitability_claim_authorized)

    def test_builder_is_canonical_deterministic_and_performs_no_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResultStatusFixture(Path(temporary))
            value = json.loads(fixture.payload)
            rebuilt = build_historical_result_registry_v2(
                reversed(value["records"]),
                authority_id=value["authority_id"],
                audited_baseline_commit=value["audited_baseline_commit"],
                source_commit=value["source_commit"],
                recorded_at_utc=RECORDED,
            )
        self.assertEqual(rebuilt, fixture.payload)
        self.assertEqual(fixture.payload, canonical_json_bytes(value) + b"\n")

    def test_general_resolver_rejects_every_non_active_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResultStatusFixture(Path(temporary))
            paths = (fixture.registry,)
            with self.assertRaises(ResultNotActiveError) as candidate:
                require_active_result(
                    fixture.candidate_primary,
                    repository_root=fixture.root,
                    registry_paths=paths,
                )
            with self.assertRaises(ResultNotActiveError) as benchmark:
                require_active_result(
                    fixture.benchmark_replay,
                    repository_root=fixture.root,
                    registry_paths=paths,
                )
            active = require_active_result(
                fixture.active,
                repository_root=fixture.root,
                registry_paths=paths,
            )
            legacy_lookup = revoked_result_for_directory(
                fixture.benchmark_primary,
                repository_root=fixture.root,
                registry_path=fixture.registry,
            )
        self.assertEqual(
            candidate.exception.resolution.historical_run_status,
            HistoricalRunStatus.REVOKED,
        )
        self.assertEqual(
            benchmark.exception.resolution.historical_run_status,
            HistoricalRunStatus.SUPERSEDED,
        )
        self.assertTrue(active.is_active)
        self.assertIsNotNone(legacy_lookup)

    def test_registry_and_record_tampering_fail_even_when_hashes_are_recomputed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResultStatusFixture(Path(temporary))
            mutations: list[bytes] = []
            raw = json.loads(fixture.payload)
            raw["records"][0]["historical_run_status"] = "ACTIVE"
            mutations.append(recompute(raw))
            raw = json.loads(fixture.payload)
            raw["records"][0]["reason_code"] = "ARBITRARY_REASON"
            mutations.append(recompute(raw))
            raw = json.loads(fixture.payload)
            raw["profitability_claim_authorized"] = True
            mutations.append(recompute(raw))
            raw = json.loads(fixture.payload)
            raw["records"][0]["path"] = "../runs/escape"
            mutations.append(recompute(raw))
            raw = json.loads(fixture.payload)
            raw["records"][0]["path"] = "runs/safe-but-not-authorized-substitute"
            mutations.append(recompute(raw))
            raw = json.loads(fixture.payload)
            raw["authority_id"] = "ADVERSARIAL_AUDIT_REMEDIATION_999"
            mutations.append(recompute(raw))
            raw = json.loads(fixture.payload)
            raw["audited_baseline_commit"] = "9" * 40
            mutations.append(recompute(raw))
            for index, payload in enumerate(mutations):
                path = fixture.root / "status" / f"tampered-{index}.json"
                path.write_bytes(payload)
                with self.subTest(index=index), self.assertRaises(ValueError):
                    load_historical_result_registry(path)

    def test_noncanonical_duplicate_json_and_stale_root_identity_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResultStatusFixture(Path(temporary))
            value = json.loads(fixture.payload)
            noncanonical = fixture.root / "status/noncanonical.json"
            noncanonical.write_text(json.dumps(value, indent=2), encoding="utf-8")
            duplicate = fixture.root / "status/duplicate-key.json"
            duplicate.write_bytes(
                fixture.payload.replace(
                    b'{"audited_baseline_commit"',
                    b'{"schema":"historical-result-status-registry-v2",'
                    b'"audited_baseline_commit"',
                    1,
                ),
            )
            stale = fixture.root / "status/stale.json"
            stale.write_bytes(fixture.payload.replace(b"R2-001", b"R2-009", 1))
            for path in (noncanonical, duplicate, stale):
                with self.subTest(path=path.name), self.assertRaises(ValueError):
                    load_historical_result_registry(path)

    def test_duplicate_incomplete_and_conflicting_pairs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResultStatusFixture(Path(temporary))
            mutations: list[bytes] = []
            raw = json.loads(fixture.payload)
            raw["records"].append(dict(raw["records"][0]))
            mutations.append(recompute(raw))
            raw = json.loads(fixture.payload)
            raw["records"] = [
                record
                for record in raw["records"]
                if not (
                    record["logical_result_id"] == "spot-candidate-a"
                    and record["copy_role"] == "REPLAY"
                )
            ]
            mutations.append(recompute(raw))
            raw = json.loads(fixture.payload)
            raw["records"] = [
                record
                for record in raw["records"]
                if record["logical_result_id"] != "spot-candidate-a"
            ]
            mutations.append(recompute(raw))
            raw = json.loads(fixture.payload)
            replay = next(
                item
                for item in raw["records"]
                if item["logical_result_id"] == "spot-candidate-a"
                and item["copy_role"] == "REPLAY"
            )
            replay["market_profile"] = "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING"
            mutations.append(recompute(raw))
            for index, payload in enumerate(mutations):
                path = fixture.root / "status" / f"pair-{index}.json"
                path.write_bytes(payload)
                with self.subTest(index=index), self.assertRaises(ValueError):
                    load_historical_result_registry(path)

    def test_cross_registry_conflict_and_missing_registry_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResultStatusFixture(Path(temporary))
            duplicate = fixture.root / "status/duplicate-registry.json"
            duplicate.write_bytes(fixture.payload)
            with self.assertRaises(ValueError):
                resolve_result_status(
                    fixture.candidate_primary,
                    repository_root=fixture.root,
                    registry_paths=(fixture.registry, duplicate),
                )
            with self.assertRaises(ValueError):
                resolve_result_status(
                    fixture.active,
                    repository_root=fixture.root,
                    registry_paths=(fixture.root / "status/missing.json",),
                )
            with self.assertRaises(ValueError):
                resolve_result_status(
                    fixture.active,
                    repository_root=fixture.root,
                    registry_paths=(),
                )

    def test_evidence_tamper_deletion_and_symlink_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = ResultStatusFixture(Path(temporary))
            checker = fixture.candidate_primary / "checker.json"
            checker.write_bytes(b"tampered\n")
            with self.assertRaises(ValueError):
                resolve_result_status(
                    fixture.candidate_primary,
                    repository_root=fixture.root,
                    registry_paths=(fixture.registry,),
                )
            status = fixture.benchmark_primary / "status.json"
            status.unlink()
            with self.assertRaises(ValueError):
                resolve_result_status(
                    fixture.benchmark_primary,
                    repository_root=fixture.root,
                    registry_paths=(fixture.registry,),
                )
            replacement = fixture.root / "replacement-status.json"
            replacement.write_bytes(b"replacement\n")
            manifest = fixture.candidate_replay / "evidence_manifest.json"
            manifest.unlink()
            manifest.symlink_to(replacement)
            with self.assertRaises(ValueError):
                resolve_result_status(
                    fixture.candidate_replay,
                    repository_root=fixture.root,
                    registry_paths=(fixture.registry,),
                )


if __name__ == "__main__":
    unittest.main()
