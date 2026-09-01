from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
import unittest
from datetime import UTC
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from crypto_lab.config import MarketProfile
from crypto_lab.data import DatasetRelease
from crypto_lab.m3 import ProfileQualificationState
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.m3 import qualification_dataset_release
from crypto_lab.owner import _qualified_profile
from crypto_lab.owner import _qualified_profile_registry_candidates
from crypto_lab.owner import _require_qualification_executable_closure_current
from crypto_lab.owner import _release
from crypto_lab.owner import _require_scientific_claim_contract
from crypto_lab.owner import DEVELOPMENT_CLAIM_CONTROL_TOKENS
from crypto_lab.owner import OwnerWorkflowPurpose
from crypto_lab.research import PartitionRole
from crypto_lab.research import ResearchError
from scripts.prepare_adversarial_remediation_002_runs import DEVELOPMENT
from scripts.prepare_adversarial_remediation_002_runs import DEVELOPMENT_CLAIM_BASIS
from scripts.prepare_adversarial_remediation_002_runs import EPOCH_FRAGMENT
from scripts.prepare_adversarial_remediation_002_runs import PROFILE_ORDER
from scripts.prepare_adversarial_remediation_002_runs import RESEARCH_FAMILY
from scripts.prepare_adversarial_remediation_002_runs import STRATEGY_FAMILY
from scripts.prepare_adversarial_remediation_002_runs import ROOT
from scripts.prepare_adversarial_remediation_002_runs import WARMUP_START
from scripts.prepare_adversarial_remediation_002_runs import _benchmark_id
from scripts.prepare_adversarial_remediation_002_runs import _build_protocol_and_workflows
from scripts.prepare_adversarial_remediation_002_runs import _execution_item
from scripts.prepare_adversarial_remediation_002_runs import _require_current_registry
from scripts.prepare_adversarial_remediation_002_runs import _require_current_registry_locator
from scripts.prepare_adversarial_remediation_002_runs import _require_external_fresh_output
from scripts.prepare_adversarial_remediation_002_runs import _require_full_release
from scripts.prepare_adversarial_remediation_002_runs import _require_new_workflow_identities
from scripts.prepare_adversarial_remediation_002_runs import _require_rebuild_validation


SPOT_RELEASE = ROOT / (
    "data/releases/fd8542c109cfbf7d6b19d5b7bbb7705c6a161efc807695f3671978c381e34eca.json"
)
PERPETUAL_RELEASE = ROOT / (
    "data/releases/b6c8f5d659f3441c924b613d770342796c90b90a970f42a3dc8227c856198917.json"
)
LEGACY_REGISTRY = ROOT / "evidence/m3/m3-acceptance-001/qualified-profile-registry.json"
FROZEN = datetime(2026, 8, 31, tzinfo=UTC)
EPOCH = "adversarial-remediation-002"


class R2OfficialRebuildPlanTests(unittest.TestCase):
    def test_r2_research_family_is_new_but_strategy_lineage_is_preserved(self) -> None:
        self.assertEqual(STRATEGY_FAMILY, "BTCUSDT_WEEKLY_TSMOM28_V1")
        self.assertEqual(
            RESEARCH_FAMILY,
            "BTCUSDT_WEEKLY_TSMOM28_V1_ADVERSARIAL_REMEDIATION_002",
        )
        self.assertNotIn(
            RESEARCH_FAMILY,
            {
                "BTCUSDT_WEEKLY_TSMOM28_V1",
                "BTCUSDT_WEEKLY_TSMOM28_V1_AUDIT_REMEDIATION_001",
                "BTCUSDT_WEEKLY_TSMOM28_V1_AUDIT_REMEDIATION_002",
                "BTCUSDT_WEEKLY_TSMOM28_V1_AUDIT_REMEDIATION_003",
            },
        )

    @classmethod
    def setUpClass(cls) -> None:
        cls.releases = {
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY: DatasetRelease.from_json_bytes(
                SPOT_RELEASE.read_bytes(),
            ),
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING: (
                DatasetRelease.from_json_bytes(PERPETUAL_RELEASE.read_bytes())
            ),
        }
        cls.legacy_registry = QualifiedProfileRegistry.from_json_bytes(
            LEGACY_REGISTRY.read_bytes(),
        )

    def test_legacy_dataset_releases_fail_the_r2_full_inventory_gate(self) -> None:
        for profile in PROFILE_ORDER:
            with self.subTest(profile=profile.value), self.assertRaisesRegex(
                RuntimeError,
                "v2 full-inventory release",
            ):
                _require_full_release(self.releases[profile], profile=profile)

    def test_m3_qualification_release_cannot_authorize_official_research(self) -> None:
        for profile in PROFILE_ORDER:
            release = qualification_dataset_release(profile, repository_root=ROOT)
            self.assertTrue(release.has_full_raw_inventory)
            with self.subTest(profile=profile.value), self.assertRaisesRegex(
                RuntimeError,
                "v2 full-inventory release",
            ):
                _require_full_release(release, profile=profile)
            qualification_value = SimpleNamespace(
                dataset_release_id=release.dataset_release_id,
                workflow_purpose=OwnerWorkflowPurpose.QUALIFICATION_INTERFACE_FIXTURE,
                protocol=SimpleNamespace(
                    market_profile=profile,
                    dataset_release_ids=(release.dataset_release_id,),
                ),
            )
            self.assertEqual(
                _release(ROOT, qualification_value).dataset_release_id,
                release.dataset_release_id,
            )
            research_value = SimpleNamespace(
                dataset_release_id=release.dataset_release_id,
                workflow_purpose=OwnerWorkflowPurpose.OWNER_STUDY,
                protocol=qualification_value.protocol,
            )
            with self.assertRaisesRegex(
                ResearchError,
                "not eligible for this Owner workflow purpose",
            ):
                _release(ROOT, research_value)

    def test_owner_resolves_the_r2_qualification_registry_before_legacy_authorities(self) -> None:
        candidates = _qualified_profile_registry_candidates(ROOT)
        listing = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "ls-tree",
                "-r",
                "--name-only",
                "HEAD",
                "--",
                "evidence/audit/adversarial-remediation-002",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        tracked = []
        pattern = re.compile(
            r"^evidence/audit/adversarial-remediation-002/"
            r"qualification-retry-([0-9]{3})/qualified-profile-registry[.]json$",
        )
        for relative in listing:
            match = pattern.fullmatch(relative)
            if match is not None:
                tracked.append((int(match.group(1)), ROOT / relative))
        self.assertTrue(tracked)
        self.assertEqual(
            candidates[0],
            max(tracked)[1],
        )
        retry_candidates = candidates[: len(tracked)]
        self.assertEqual(
            retry_candidates,
            tuple(path for _epoch, path in sorted(tracked, reverse=True)),
        )
        self.assertEqual(
            candidates[len(tracked)],
            ROOT
            / "evidence/audit/adversarial-remediation-002/qualification/qualified-profile-registry.json",
        )
        self.assertEqual(len(candidates), len(set(candidates)))

    def test_missing_latest_registry_fails_closed_without_older_fallback(self) -> None:
        current_path = _qualified_profile_registry_candidates(ROOT)[0]
        current = QualifiedProfileRegistry.from_json_bytes(current_path.read_bytes()).records[0]
        value = SimpleNamespace(
            qualified_profile_record_id=current.qualified_profile_record_id,
            protocol=SimpleNamespace(market_profile=current.profile_id),
        )
        missing = (
            ROOT
            / "evidence/audit/adversarial-remediation-002/"
            "qualification-retry-999/qualified-profile-registry.json"
        )
        with (
            patch(
                "crypto_lab.owner._qualified_profile_registry_candidates",
                return_value=(missing, current_path),
            ),
            self.assertRaisesRegex(ResearchError, "Current Qualified Profile registry is unavailable"),
        ):
            _qualified_profile(ROOT, value)

    def test_uncommitted_registry_bytes_cannot_become_current_authority(self) -> None:
        current_path = _qualified_profile_registry_candidates(ROOT)[0]
        current = QualifiedProfileRegistry.from_json_bytes(current_path.read_bytes()).records[0]
        value = SimpleNamespace(
            qualified_profile_record_id=current.qualified_profile_record_id,
            protocol=SimpleNamespace(market_profile=current.profile_id),
        )
        with tempfile.TemporaryDirectory(
            dir=ROOT,
            prefix=".r2-uncommitted-registry-",
        ) as temporary:
            uncommitted = Path(temporary) / "qualified-profile-registry.json"
            uncommitted.write_bytes(current_path.read_bytes())
            with (
                patch(
                    "crypto_lab.owner._qualified_profile_registry_candidates",
                    return_value=(uncommitted, current_path),
                ),
                self.assertRaisesRegex(ResearchError, "differs from Git HEAD"),
            ):
                _qualified_profile(ROOT, value)

    def test_owner_rejects_a_v2_profile_from_an_older_registry(self) -> None:
        candidates = _qualified_profile_registry_candidates(ROOT)
        current_index = next(index for index, path in enumerate(candidates) if path.is_file())
        current_path = candidates[current_index]
        older_path = next(
            path
            for path in candidates[current_index + 1 :]
            if path.is_file()
            and QualifiedProfileRegistry.from_json_bytes(path.read_bytes()).schema_version == 2
        )
        current = QualifiedProfileRegistry.from_json_bytes(current_path.read_bytes()).records[0]
        older = QualifiedProfileRegistry.from_json_bytes(older_path.read_bytes()).records[0]
        current_value = SimpleNamespace(
            qualified_profile_record_id=current.qualified_profile_record_id,
            protocol=SimpleNamespace(market_profile=current.profile_id),
        )
        resolved, resolved_path = _qualified_profile(ROOT, current_value)
        self.assertEqual(resolved.qualified_profile_record_id, current.qualified_profile_record_id)
        self.assertEqual(resolved_path, current_path)
        older_value = SimpleNamespace(
            qualified_profile_record_id=older.qualified_profile_record_id,
            protocol=SimpleNamespace(market_profile=older.profile_id),
        )
        with self.assertRaisesRegex(ResearchError, "current authority"):
            _qualified_profile(ROOT, older_value)

    def test_plan_preparation_rejects_an_older_qualification_registry(self) -> None:
        candidates = _qualified_profile_registry_candidates(ROOT)
        current_path = candidates[0]
        older_path = next(
            path
            for path in candidates[1:]
            if path.is_file()
            and QualifiedProfileRegistry.from_json_bytes(path.read_bytes()).schema_version == 2
        )
        _require_current_registry_locator(current_path)
        with self.assertRaisesRegex(RuntimeError, "current Git-committed authority"):
            _require_current_registry_locator(older_path)

    def test_qualification_executable_closure_rejects_stale_product_bytes(self) -> None:
        candidates = _qualified_profile_registry_candidates(ROOT)
        current = QualifiedProfileRegistry.from_json_bytes(candidates[0].read_bytes()).records[0]
        older = next(
            QualifiedProfileRegistry.from_json_bytes(path.read_bytes()).records[0]
            for path in candidates[1:]
            if path.is_file()
            and QualifiedProfileRegistry.from_json_bytes(path.read_bytes()).schema_version == 2
        )
        _require_qualification_executable_closure_current(
            ROOT,
            current.source_revision.git_commit,
        )
        with self.assertRaisesRegex(
            ResearchError,
            "executable closure differs from current Git HEAD",
        ):
            _require_qualification_executable_closure_current(
                ROOT,
                older.source_revision.git_commit,
            )

    def test_legacy_check_pass_registry_fails_the_component_gate(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "schema version 2"):
            _require_current_registry(
                self.legacy_registry,
                runtime_lock_sha256="a" * 64,
            )

    def test_current_registry_gate_rejects_wrong_component_or_runtime(self) -> None:
        records = tuple(
            SimpleNamespace(
                profile_id=profile,
                schema_version=2,
                qualification_state=ProfileQualificationState.QUALIFIED,
                checker_result="COMPONENT_CHECK_PASS",
                replay_result="PASS",
                runtime_lock_sha256="a" * 64,
                accepted_run_ids=(f"{profile.value}-primary", f"{profile.value}-replay"),
                evidence_references=("runs/primary", "runs/replay"),
            )
            for profile in PROFILE_ORDER
        )
        registry = SimpleNamespace(schema_version=2, records=records)
        accepted = _require_current_registry(
            registry,  # type: ignore[arg-type]
            runtime_lock_sha256="a" * 64,
        )
        self.assertEqual(set(accepted), set(PROFILE_ORDER))

        records[0].checker_result = "CHECK_PASS"
        with self.assertRaisesRegex(RuntimeError, "component-qualified"):
            _require_current_registry(
                registry,  # type: ignore[arg-type]
                runtime_lock_sha256="a" * 64,
            )
        records[0].checker_result = "COMPONENT_CHECK_PASS"
        records[1].runtime_lock_sha256 = "b" * 64
        with self.assertRaisesRegex(RuntimeError, "component-qualified"):
            _require_current_registry(
                registry,  # type: ignore[arg-type]
                runtime_lock_sha256="a" * 64,
            )

    def test_builder_freezes_six_unique_development_only_primary_replay_plans(self) -> None:
        record_ids = {
            record.profile_id: record.qualified_profile_record_id
            for record in self.legacy_registry.records
        }
        protocols = []
        workflows = []
        for profile in PROFILE_ORDER:
            protocol, values = _build_protocol_and_workflows(
                profile=profile,
                release=self.releases[profile],
                qualified_profile_record_id=record_ids[profile],
                frozen_at_utc=FROZEN,
                epoch=EPOCH,
                research_family_id=RESEARCH_FAMILY,
            )
            protocols.append(protocol)
            workflows.extend(values)

        self.assertEqual(len(protocols), 2)
        self.assertEqual(len(workflows), 6)
        self.assertEqual(len({item.trial_id for item in workflows}), 6)
        self.assertEqual(len({item.run_id for item in workflows}), 6)
        self.assertEqual(
            [item.workflow_purpose.value for item in workflows],
            [
                "BENCHMARK_STUDY",
                "OWNER_STUDY",
                "OWNER_STUDY",
                "BENCHMARK_STUDY",
                "OWNER_STUDY",
                "OWNER_STUDY",
            ],
        )
        for workflow in workflows:
            self.assertIs(workflow.partition_role, PartitionRole.DEVELOPMENT)
            self.assertEqual(workflow.warmup_start, WARMUP_START)
            self.assertEqual(workflow.scoring_start, DEVELOPMENT.start_inclusive)
            self.assertEqual(workflow.scoring_end_exclusive, DEVELOPMENT.end_exclusive)
            self.assertIn(EPOCH_FRAGMENT, workflow.trial_id)
            self.assertIn(EPOCH_FRAGMENT, workflow.run_id)
            self.assertNotEqual(
                workflow.protocol.final_holdout_interval,
                workflow.protocol.development_interval,
            )
            self.assertIn("FINAL_HOLDOUT_USED_FALSE", workflow.protocol.claim_basis)
            claim_tokens = {
                token.strip()
                for token in workflow.protocol.claim_basis.split(";")
                if token.strip()
            }
            self.assertTrue(set(DEVELOPMENT_CLAIM_CONTROL_TOKENS).issubset(claim_tokens))

        self.assertEqual(
            protocols[0].required_benchmark.benchmark_id,
            "BUY_AND_HOLD_1X_R2_SPOT_ADVERSARIAL_REMEDIATION_002",
        )
        self.assertEqual(
            protocols[1].required_benchmark.benchmark_id,
            "BUY_AND_HOLD_1X_R2_PERPETUAL_ADVERSARIAL_REMEDIATION_002",
        )

        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            execution = [
                _execution_item(
                    workflow,
                    input_path=root / f"{workflow.trial_id}.json",
                    result_path=root / "results" / f"{workflow.trial_id}.json",
                    sequence=index,
                )
                for index, workflow in enumerate(workflows, start=1)
            ]
        self.assertEqual(sum(item["owner_fresh_child_count"] for item in execution), 12)
        self.assertTrue(all(item["expected_copies"] == ["PRIMARY", "REPLAY"] for item in execution))
        self.assertTrue(
            all(
                len(item["workflow_input_sha256"]) == 64
                and item["workflow_input_sha256"]
                == hashlib.sha256(
                    workflow.to_json_bytes() + b"\n",
                ).hexdigest()
                for item, workflow in zip(execution, workflows, strict=True)
            ),
        )
        self.assertTrue(all(item["partition_role"] == "DEVELOPMENT" for item in execution))
        self.assertTrue(all(item["final_holdout_used"] is False for item in execution))
        for item in execution:
            command = item["command_argv"]
            self.assertNotIn(f"PYTHONPATH={ROOT / 'src'}", command)
            self.assertNotIn(str(ROOT / "scripts/run_owner_workflow.py"), command)
            self.assertIn(str(ROOT / "scripts/isolated_runtime_bootstrap.py"), command)
            self.assertIn(str(ROOT / "runtime-bootstrap-authority.json"), command)
            self.assertIn("crypto_lab.owner:main", command)

    def test_owner_rejects_missing_or_prefixed_development_claim_token(self) -> None:
        for replacement in ("", "NOT_DEVELOPMENT_ONLY_DATA"):
            with self.subTest(replacement=replacement):
                value = SimpleNamespace(
                    workflow_purpose=OwnerWorkflowPurpose.OWNER_STUDY,
                    partition_role=PartitionRole.DEVELOPMENT,
                    protocol=SimpleNamespace(
                        claim_basis=DEVELOPMENT_CLAIM_BASIS.replace(
                            "DEVELOPMENT_ONLY_DATA",
                            replacement,
                        ),
                    ),
                )
                with self.assertRaisesRegex(
                    ResearchError,
                    "DEVELOPMENT_ONLY_DATA",
                ):
                    _require_scientific_claim_contract(value)

    def test_new_epoch_never_overwrites_prior_benchmark_evidence(self) -> None:
        retry_epoch = "adversarial-remediation-002-planner-regression-control"
        record_ids = {
            record.profile_id: record.qualified_profile_record_id
            for record in self.legacy_registry.records
        }
        workflows = []
        for profile in PROFILE_ORDER:
            _protocol, values = _build_protocol_and_workflows(
                profile=profile,
                release=self.releases[profile],
                qualified_profile_record_id=record_ids[profile],
                frozen_at_utc=FROZEN,
                epoch=retry_epoch,
                research_family_id=RESEARCH_FAMILY,
            )
            workflows.extend(values)
        benchmark_ids = {
            item.protocol.required_benchmark.benchmark_id
            for item in workflows
            if item.workflow_purpose is OwnerWorkflowPurpose.BENCHMARK_STUDY
        }
        self.assertEqual(
            benchmark_ids,
            {
                _benchmark_id(epoch=retry_epoch, suffix="spot"),
                _benchmark_id(epoch=retry_epoch, suffix="perpetual"),
            },
        )
        self.assertTrue(
            all(
                not (ROOT / "research/benchmarks" / f"{benchmark_id}.json").exists()
                for benchmark_id in benchmark_ids
            ),
        )
        _require_new_workflow_identities(workflows)

    def test_rebuild_validation_must_bind_both_release_inventory_and_catalogs(self) -> None:
        inventories = {
            profile: SimpleNamespace(
                raw_inventory_identity=("a" if profile is PROFILE_ORDER[0] else "b") * 64,
                raw_object_count=10 if profile is PROFILE_ORDER[0] else 20,
            )
            for profile in PROFILE_ORDER
        }
        value = {
            "schema": (
                "free-official-binance-deterministic-rebuild-validation-"
                "v2-full-raw-inventory"
            ),
            "status": "PASS",
            "strategy_run": False,
            "official_trial": False,
            "network_used": False,
            "comparison": {
                "dataset_release_ids": sorted(
                    release.dataset_release_id for release in self.releases.values()
                ),
            },
            "materialized_release_artifacts": {
                profile.value: {
                    "dataset_release_id": self.releases[profile].dataset_release_id,
                    "catalog_identity": self.releases[profile].catalog_identity,
                    "raw_inventory_identity": inventories[profile].raw_inventory_identity,
                    "raw_inventory_object_count": inventories[profile].raw_object_count,
                }
                for profile in PROFILE_ORDER
            },
            "nautilus_catalog_validation": {
                profile.value: {
                    "status": "PASS",
                    "catalog_identity": self.releases[profile].catalog_identity,
                }
                for profile in PROFILE_ORDER
            },
        }
        _require_rebuild_validation(
            value,
            releases=self.releases,
            inventories=inventories,  # type: ignore[arg-type]
        )
        value["materialized_release_artifacts"][PROFILE_ORDER[0].value][
            "raw_inventory_object_count"
        ] += 1
        with self.assertRaisesRegex(RuntimeError, "proof differs"):
            _require_rebuild_validation(
                value,
                releases=self.releases,
                inventories=inventories,  # type: ignore[arg-type]
            )

    def test_preparation_output_is_fresh_external_and_under_tmp(self) -> None:
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            _require_external_fresh_output(ROOT / "r2-plan-must-not-be-created")
        with self.assertRaisesRegex(ValueError, "under /tmp"):
            _require_external_fresh_output(Path("/var/tmp/r2-plan-must-not-be-created"))
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            existing = Path(temporary)
            with self.assertRaisesRegex(FileExistsError, "refusing to overwrite"):
                _require_external_fresh_output(existing)
            accepted = existing.parent / f"{existing.name}-fresh"
            self.assertEqual(_require_external_fresh_output(accepted), accepted)
            self.assertFalse(accepted.exists())


if __name__ == "__main__":
    unittest.main()
