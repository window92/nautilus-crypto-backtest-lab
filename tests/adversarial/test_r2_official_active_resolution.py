from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.legacy_publication import LEGACY_HISTORICAL_ONLY_PUBLICATION
from crypto_lab.legacy_publication import require_historical_only_replay
from crypto_lab.legacy_publication import require_historical_only_result
from crypto_lab.official import OfficialEvidenceResolver
from crypto_lab.research import ResearchError
from crypto_lab.result_status import DEFAULT_RESULT_STATUS_REFS
from tests.adversarial.test_r2_historical_result_status import ResultStatusFixture


REPOSITORY = Path(__file__).resolve().parents[2]
LEGACY_PUBLISHERS = (
    "scripts/generate_owner_strategy_research_001_evidence.py",
    "scripts/generate_owner_smoke_report.py",
    "scripts/generate_owner_smoke_002_replacement_evidence.py",
)


def install_default_status_authorities(root: Path, fixture: ResultStatusFixture) -> None:
    current_ref = "evidence/audit/adversarial-remediation-002/historical-result-status.json"
    for relative in DEFAULT_RESULT_STATUS_REFS:
        if relative == current_ref:
            continue
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPOSITORY / relative).read_bytes())
    current = root / current_ref
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(fixture.payload)


def resolver_for(root: Path) -> OfficialEvidenceResolver:
    resolver = object.__new__(OfficialEvidenceResolver)
    resolver.repository_root = root.resolve(strict=True)
    return resolver


def replay_payload(*, trial_id: str, primary_ref: str, replay_ref: str) -> bytes:
    material = {
        "schema": "owner-deterministic-replay-v2",
        "trial_id": trial_id,
        "primary_run_ref": primary_ref,
        "replay_run_ref": replay_ref,
        "result": "PASS",
        "fresh_processes": True,
        "read_only_checker_revalidated": True,
        "primary_component_validation": "COMPONENT_CHECK_PASS",
        "replay_component_validation": "COMPONENT_CHECK_PASS",
        "primary_official_seal": "OFFICIAL_SEAL_PASS",
        "replay_official_seal": "OFFICIAL_SEAL_PASS",
        "primary_config_sha256": "1" * 64,
        "replay_config_sha256": "1" * 64,
        "primary_semantic_digest": "2" * 64,
        "replay_semantic_digest": "2" * 64,
    }
    material["replay_identity"] = canonical_sha256(material)
    return canonical_json_bytes(material) + b"\n"


class R2OfficialActiveResolutionTests(unittest.TestCase):
    def test_primary_is_rejected_as_inactive_before_run_payload_is_consumed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ResultStatusFixture(root)
            install_default_status_authorities(root, fixture)
            record = SimpleNamespace(
                result_ref=fixture.candidate_primary.relative_to(root).as_posix(),
                run_id="historical-primary",
            )
            with self.assertRaises(ResearchError) as caught:
                resolver_for(root)._resolve_selected_run(record, SimpleNamespace())
        self.assertEqual(caught.exception.code, "CLAIM_INELIGIBLE")
        self.assertIn("primary Result is not ACTIVE", caught.exception.message)

    def test_replay_is_rejected_as_inactive_before_native_result_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ResultStatusFixture(root)
            install_default_status_authorities(root, fixture)
            trial_id = "inactive-replay-trial"
            primary_ref = fixture.active.relative_to(root).as_posix()
            replay_ref = fixture.candidate_replay.relative_to(root).as_posix()
            replay_path = root / "research/replays" / f"{trial_id}.json"
            replay_path.parent.mkdir(parents=True)
            replay_path.write_bytes(
                replay_payload(
                    trial_id=trial_id,
                    primary_ref=primary_ref,
                    replay_ref=replay_ref,
                ),
            )
            record = SimpleNamespace(
                trial_id=trial_id,
                result_ref=primary_ref,
                run_id="inactive-replay-run",
            )
            with self.assertRaises(ResearchError) as caught:
                resolver_for(root)._resolve_replay_evidence(
                    record=record,
                    primary_run_dir=fixture.active,
                )
        self.assertEqual(caught.exception.code, "CLAIM_INELIGIBLE")
        self.assertIn("replay Result is not ACTIVE", caught.exception.message)

    def test_active_primary_reaches_the_normal_seal_and_evidence_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ResultStatusFixture(root)
            install_default_status_authorities(root, fixture)
            record = SimpleNamespace(
                result_ref=fixture.active.relative_to(root).as_posix(),
                run_id="active-primary",
            )
            with self.assertRaises(ResearchError) as caught:
                resolver_for(root)._resolve_selected_run(record, SimpleNamespace())
        self.assertEqual(caught.exception.code, "EVIDENCE_INCOMPLETE")
        self.assertIn("selected Run missing", caught.exception.message)

    def test_missing_status_authority_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "runs/current-run"
            run.mkdir(parents=True)
            record = SimpleNamespace(result_ref="runs/current-run")
            with self.assertRaises(ResearchError) as caught:
                resolver_for(root)._run_dir(record)
        self.assertEqual(caught.exception.code, "EVIDENCE_INCOMPLETE")
        self.assertIn("status authority is invalid", caught.exception.message)

    def test_legacy_classification_accepts_only_registered_non_active_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = ResultStatusFixture(root)
            install_default_status_authorities(root, fixture)
            historical = require_historical_only_result(
                fixture.candidate_primary,
                repository_root=root,
            )
            self.assertFalse(historical.is_active)
            with self.assertRaisesRegex(RuntimeError, LEGACY_HISTORICAL_ONLY_PUBLICATION):
                require_historical_only_result(
                    fixture.active,
                    repository_root=root,
                )
            replay = {
                "replay_run_ref": fixture.candidate_replay.relative_to(root).as_posix(),
            }
            self.assertFalse(
                require_historical_only_replay(replay, repository_root=root).is_active,
            )
            with self.assertRaisesRegex(RuntimeError, LEGACY_HISTORICAL_ONLY_PUBLICATION):
                require_historical_only_replay(
                    {"replay_run_ref": fixture.active.relative_to(root).as_posix()},
                    repository_root=root,
                )

    def test_every_legacy_publisher_guards_primary_and_replay_sources(self) -> None:
        for relative in LEGACY_PUBLISHERS:
            with self.subTest(script=relative):
                tree = ast.parse((REPOSITORY / relative).read_text(encoding="utf-8"))
                calls = {
                    node.func.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                }
                assignments = {
                    target.id
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Assign)
                    for target in node.targets
                    if isinstance(target, ast.Name)
                }
                self.assertIn("PUBLICATION_CLASSIFICATION", assignments)
                self.assertIn("require_historical_only_result", calls)
                self.assertIn("require_historical_only_replay", calls)


if __name__ == "__main__":
    unittest.main()
