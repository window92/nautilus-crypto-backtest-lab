from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from crypto_lab.history import HistoryAnchorStore
from crypto_lab.history import AuthoritativeResearchHistory
from crypto_lab.git_identity import capture_actual_source_revision
from crypto_lab.hashing import canonical_sha256
from crypto_lab.owner import build_official_request
from crypto_lab.owner import qualification_workflow_fixture_input
from crypto_lab.research import HoldoutLockStore
from crypto_lab.research import PartitionRole
from crypto_lab.research import ResearchError
from crypto_lab.research import TrialDefinition
from crypto_lab.research import TrialJournal
from scripts.build_runtime_bootstrap_authority import build_authority


ROOT = Path(__file__).resolve().parents[2]
PROJECT_PYTHON = ROOT / ".venv/bin/python"
OWNER_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "TZ": "UTC",
}


def _run(*command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _commit_runtime_authority(repository: Path) -> None:
    source_commit = _run("git", "rev-parse", "HEAD", cwd=repository).stdout.strip()
    authority = build_authority(
        repository=repository,
        python=PROJECT_PYTHON,
        source_commit=source_commit,
        dependency_lock_path=repository / "requirements.lock.txt",
    )
    path = repository / "runtime-bootstrap-authority.json"
    path.write_text(
        json.dumps(
            authority,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    _run("git", "add", "runtime-bootstrap-authority.json", cwd=repository)
    _run("git", "commit", "-m", "fixture runtime bootstrap authority", cwd=repository)
    _run("git", "push", "origin", "main", cwd=repository)


def _owner_process(
    repository: Path,
    *,
    input_path: Path,
    output_path: Path,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(PROJECT_PYTHON),
            "-I",
            "-P",
            "-S",
            "-B",
            "-X",
            "pycache_prefix=/dev/null",
            str(repository / "scripts/isolated_runtime_bootstrap.py"),
            "--authority",
            str(repository / "runtime-bootstrap-authority.json"),
            "--repository",
            str(repository),
            "--entrypoint",
            "crypto_lab.owner:main",
            "--",
            "--input",
            str(input_path),
            "--repository",
            str(repository),
            "--output",
            str(output_path),
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
        env=OWNER_ENVIRONMENT,
    )


def _copy_run_external_dataset_state(
    *,
    run_directory: Path,
    source_repository: Path,
    target_repository: Path,
) -> None:
    """Recreate only the Run-bound ignored Raw/catalog view in a recovery clone."""

    release = json.loads((run_directory / "dataset_release.json").read_text(encoding="utf-8"))
    raw_inventory = release["raw_inventory"]
    raw_objects = raw_inventory["raw_objects"]
    for item in raw_objects:
        identity = item["raw_object_sha256"]
        relative = Path("data/raw/sha256") / identity[:2] / f"{identity}.blob"
        source = source_repository / relative
        target = target_repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    catalog_identity = release["catalog_identity"]
    source_catalog = source_repository / "data/catalog" / catalog_identity
    target_catalog = target_repository / "data/catalog" / catalog_identity
    shutil.copytree(source_catalog, target_catalog)


class Aud009OwnerWorkflowTests(unittest.TestCase):
    def test_qualification_fixture_cannot_designate_final_holdout(self) -> None:
        value = qualification_workflow_fixture_input(
            repository_root=ROOT,
            frozen_at_utc=datetime.now(UTC) - timedelta(seconds=1),
            trial_id="aud009-no-holdout-fixture",
            run_id="aud009-no-holdout-run",
        )
        with self.assertRaisesRegex(ResearchError, "cannot designate or consume Final Holdout"):
            replace(
                value,
                partition_role=PartitionRole.FINAL_HOLDOUT,
                scoring_start=value.protocol.final_holdout_interval.start_inclusive,
                scoring_end_exclusive=value.protocol.final_holdout_interval.end_exclusive,
            )

    def test_public_cli_executes_complete_claim_ineligible_exposed_data_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            repository = base / "repository"
            origin = base / "origin.git"
            shutil.copytree(
                ROOT,
                repository,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".venv",
                    "__pycache__",
                    ".pytest_cache",
                    ".mypy_cache",
                    ".ruff_cache",
                ),
            )
            copied_anchor = repository / "research/history_anchors.jsonl"
            copied_anchor.unlink(missing_ok=True)
            # This is an isolated qualification repository, not a clone of the
            # caller's authoritative research history.  Once the real project
            # contains Trial records, copying those records while deleting only
            # their anchors creates a contradictory fixture and makes the
            # expected single-Trial lifecycle state-dependent.
            (repository / "research/trials.jsonl").write_bytes(b"")
            (repository / "research/holdout_lock.json").write_text("{}\n", encoding="utf-8")
            for relative in (
                "runs",
                "research/workflows",
                "research/replays",
                "research/reports",
            ):
                shutil.rmtree(repository / relative, ignore_errors=True)
            _run("git", "init", "-b", "main", cwd=repository)
            _run("git", "config", "user.name", "Owner Workflow Test", cwd=repository)
            _run(
                "git",
                "config",
                "user.email",
                "owner-workflow@example.invalid",
                cwd=repository,
            )
            _run("git", "add", ".", cwd=repository)
            _run("git", "commit", "-m", "fixture source", cwd=repository)
            _run("git", "init", "--bare", str(origin), cwd=base)
            _run("git", "remote", "add", "origin", str(origin), cwd=repository)
            _run("git", "push", "-u", "origin", "main", cwd=repository)
            HistoryAnchorStore(
                repository_root=repository,
                journal_path=repository / "research/trials.jsonl",
                holdout_path=repository / "research/holdout_lock.json",
                anchor_path=copied_anchor,
            ).initialize(at_utc=datetime.now(UTC) - timedelta(seconds=2))
            _run("git", "add", "research/history_anchors.jsonl", cwd=repository)
            _run("git", "commit", "-m", "initialize history authority", cwd=repository)
            _run("git", "push", "origin", "main", cwd=repository)
            _commit_runtime_authority(repository)

            value = qualification_workflow_fixture_input(
                repository_root=repository,
                frozen_at_utc=datetime.now(UTC) - timedelta(seconds=1),
                trial_id="aud009-public-fixture",
                run_id="aud009-public-run",
            )
            input_path = base / "strict-input.json"
            output_path = base / "workflow-result.json"
            input_path.write_bytes(value.to_json_bytes() + b"\n")
            process = _owner_process(
                repository,
                input_path=input_path,
                output_path=output_path,
            )
            retained_statuses = {
                str(path.relative_to(repository)): json.loads(path.read_text(encoding="utf-8"))
                for path in sorted((repository / "runs").glob("**/status.json"))
            }
            self.assertEqual(
                process.returncode,
                0,
                process.stderr
                + process.stdout
                + output_path.read_text(encoding="utf-8")
                + json.dumps(retained_statuses, sort_keys=True),
            )
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["run_state"], "COMPLETED")
            self.assertEqual(result["checker_outcome"], "COMPONENT_CHECK_PASS")
            self.assertEqual(result["official_seal_outcome"], "OFFICIAL_SEAL_PASS")
            self.assertEqual(result["claim_eligibility"], "INELIGIBLE")
            self.assertFalse(result["real_profitability_claim"])
            self.assertFalse(result["final_holdout_used"])
            self.assertEqual(len(result["commits"]), 5)

            records = TrialJournal(repository / "research/trials.jsonl").read_records()
            self.assertEqual(
                tuple(record.state.value for record in records),
                ("PLANNED", "STARTED", "COMPLETED"),
            )
            self.assertEqual(len(HoldoutLockStore(repository / "research/holdout_lock.json").read().entries), 0)
            run_dir = repository / records[-1].result_ref
            status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
            engine = json.loads((run_dir / "nautilus_result.json").read_text(encoding="utf-8"))
            identity = json.loads((run_dir / "strategy_identity.json").read_text(encoding="utf-8"))
            isolation = engine["network_guard"]["process_isolation"]
            self.assertEqual(status["state"], "COMPLETED")
            self.assertTrue(identity["qualification_fixture_only"])
            self.assertFalse(identity["profitability_claim_eligible"])
            self.assertEqual(isolation["current_process_probe_errno"], 1)
            self.assertEqual(isolation["io_uring_probe_errno"], 1)
            self.assertEqual(isolation["child_python_probe_errno"], 1)
            self.assertTrue(isolation["child_native_probe_blocked"])
            self.assertTrue(isolation["child_dns_probe_blocked"])
            self.assertFalse(isolation["external_endpoint_contacted"])
            report = json.loads(
                (
                    repository / "research/reports/aud009-public-fixture.json"
                ).read_text(encoding="utf-8"),
            )
            self.assertEqual(report["json_payload"]["trial_count"], 1)
            self.assertFalse(report["json_payload"]["profitability_claim_is_real"])
            self.assertEqual(
                report["json_payload"]["report_purpose"],
                "QUALIFICATION_WORKFLOW_FIXTURE",
            )
            self.assertEqual(report["json_payload"]["source_revision"]["branch_ref"], "main")
            self.assertEqual(_run("git", "status", "--porcelain", cwd=repository).stdout, "")
            head = _run("git", "rev-parse", "HEAD", cwd=repository).stdout.strip()
            remote = _run("git", "rev-parse", "origin/main", cwd=repository).stdout.strip()
            self.assertEqual(head, remote)
            cli_source = (repository / "scripts/run_owner_workflow.py").read_text(encoding="utf-8")
            self.assertNotIn("from crypto_lab", cli_source)
            self.assertNotIn("import crypto_lab", cli_source)
            direct = subprocess.run(
                [str(PROJECT_PYTHON), str(repository / "scripts/run_owner_workflow.py")],
                cwd=repository,
                check=False,
                capture_output=True,
                text=True,
                env=OWNER_ENVIRONMENT,
            )
            self.assertEqual(direct.returncode, 120, direct.stderr)
            direct_failure = json.loads(direct.stderr)
            self.assertEqual(direct_failure["failure_code"], "RUNTIME_STARTUP_MISMATCH")

            # A self-consistent uncommitted journal+anchor extension without a
            # workflow authorization committed at HEAD must not be laundered
            # by recovery.
            unauthorized = qualification_workflow_fixture_input(
                repository_root=repository,
                frozen_at_utc=datetime.now(UTC) - timedelta(seconds=1),
                trial_id="aud009-unauthorized-extension",
                run_id="aud009-unauthorized-run",
            )
            unauthorized_source = capture_actual_source_revision(repository)
            unauthorized_request = build_official_request(
                unauthorized,
                repository_root=repository,
                source_revision=unauthorized_source,
            )
            unauthorized_candidate = unauthorized.protocol.ordered_candidates[0]
            unauthorized_definition = TrialDefinition(
                trial_id=unauthorized.trial_id,
                research_family_id=unauthorized.protocol.research_family_id,
                hypothesis_id=unauthorized.protocol.hypothesis_id,
                protocol_id=unauthorized.protocol.protocol_id,
                candidate_id=unauthorized_candidate.candidate_id,
                candidate_parameters_sha256=canonical_sha256(
                    dict(unauthorized_candidate.parameter_values),
                ),
                run_id=unauthorized.run_id,
                config_sha256=unauthorized_request.lab_run_config.config_sha256,
                strategy_spec_id=unauthorized.strategy_spec.strategy_spec_id,
                dataset_release_id=unauthorized.dataset_release_id,
                partition_role=unauthorized.partition_role,
                seed=unauthorized.seed,
                market_profile=unauthorized.protocol.market_profile,
                instrument_id=unauthorized.strategy_spec.instrument_id,
                scored_interval=unauthorized.protocol.validation_interval,
            )
            unauthorized_history = AuthoritativeResearchHistory(
                HistoryAnchorStore(
                    repository_root=repository,
                    journal_path=repository / "research/trials.jsonl",
                    holdout_path=repository / "research/holdout_lock.json",
                    anchor_path=repository / "research/history_anchors.jsonl",
                ),
            )
            unauthorized_history.start_trial(
                unauthorized_definition,
                at_utc=datetime.now(UTC),
            )
            unauthorized_input = base / "unauthorized-recovery-input.json"
            unauthorized_output = base / "unauthorized-recovery-output.json"
            unauthorized_input.write_bytes(unauthorized.to_json_bytes() + b"\n")
            unauthorized_recovery = _owner_process(
                repository,
                input_path=unauthorized_input,
                output_path=unauthorized_output,
            )
            self.assertEqual(unauthorized_recovery.returncode, 2)
            unauthorized_result = json.loads(
                unauthorized_output.read_text(encoding="utf-8"),
            )
            self.assertIn("no immutable workflow authorization", unauthorized_result["detail"])
            _run(
                "git",
                "restore",
                "research/trials.jsonl",
                "research/history_anchors.jsonl",
                cwd=repository,
            )

            # Simulate a process loss after an immutable workflow intent was
            # committed and journal+anchor were fsynced, but before the STARTED
            # checkpoint.  Recovery must terminalize it as ABORTED and must not
            # start the newly requested trial in the same invocation.
            interrupted = qualification_workflow_fixture_input(
                repository_root=repository,
                frozen_at_utc=datetime.now(UTC) - timedelta(seconds=1),
                trial_id="aud009-interrupted-fixture",
                run_id="aud009-interrupted-run",
            )
            interrupted_protocol_path = (
                repository
                / "research/protocols"
                / f"{interrupted.protocol.protocol_id}.json"
            )
            interrupted_workflow_path = (
                repository / "research/workflows" / f"{interrupted.trial_id}.json"
            )
            interrupted_protocol_path.parent.mkdir(parents=True, exist_ok=True)
            interrupted_workflow_path.parent.mkdir(parents=True, exist_ok=True)
            interrupted_protocol_path.write_bytes(interrupted.protocol.to_json_bytes() + b"\n")
            interrupted_workflow_path.write_bytes(interrupted.to_json_bytes() + b"\n")
            _run(
                "git",
                "add",
                str(interrupted_protocol_path.relative_to(repository)),
                str(interrupted_workflow_path.relative_to(repository)),
                cwd=repository,
            )
            _run("git", "commit", "-m", "fixture interrupted intent", cwd=repository)
            _run("git", "push", "origin", "main", cwd=repository)
            source = capture_actual_source_revision(repository)
            interrupted_request = build_official_request(
                interrupted,
                repository_root=repository,
                source_revision=source,
            )
            candidate = interrupted.protocol.ordered_candidates[0]
            definition = TrialDefinition(
                trial_id=interrupted.trial_id,
                research_family_id=interrupted.protocol.research_family_id,
                hypothesis_id=interrupted.protocol.hypothesis_id,
                protocol_id=interrupted.protocol.protocol_id,
                candidate_id=candidate.candidate_id,
                candidate_parameters_sha256=canonical_sha256(dict(candidate.parameter_values)),
                run_id=interrupted.run_id,
                config_sha256=interrupted_request.lab_run_config.config_sha256,
                strategy_spec_id=interrupted.strategy_spec.strategy_spec_id,
                dataset_release_id=interrupted.dataset_release_id,
                partition_role=interrupted.partition_role,
                seed=interrupted.seed,
                market_profile=interrupted.protocol.market_profile,
                instrument_id=interrupted.strategy_spec.instrument_id,
                scored_interval=interrupted.protocol.validation_interval,
            )
            recovery_history = AuthoritativeResearchHistory(
                HistoryAnchorStore(
                    repository_root=repository,
                    journal_path=repository / "research/trials.jsonl",
                    holdout_path=repository / "research/holdout_lock.json",
                    anchor_path=repository / "research/history_anchors.jsonl",
                ),
            )
            recovery_history.start_trial(definition, at_utc=datetime.now(UTC))
            next_value = qualification_workflow_fixture_input(
                repository_root=repository,
                frozen_at_utc=datetime.now(UTC) - timedelta(seconds=1),
                trial_id="aud009-not-started-during-recovery",
                run_id="aud009-not-started-run",
            )
            recovery_input = base / "recovery-input.json"
            recovery_output = base / "recovery-output.json"
            recovery_input.write_bytes(next_value.to_json_bytes() + b"\n")
            recovery = _owner_process(
                repository,
                input_path=recovery_input,
                output_path=recovery_output,
            )
            self.assertEqual(recovery.returncode, 2)
            recovery_result = json.loads(recovery_output.read_text(encoding="utf-8"))
            self.assertEqual(recovery_result["status"], "BLOCKED")
            self.assertIn("terminalized", recovery_result["detail"])
            recovered_records = TrialJournal(repository / "research/trials.jsonl").read_records()
            latest = {record.trial_id: record for record in recovered_records}
            self.assertEqual(latest[interrupted.trial_id].state.value, "ABORTED")
            self.assertNotIn(next_value.trial_id, latest)
            self.assertEqual(_run("git", "status", "--porcelain", cwd=repository).stdout, "")
            self.assertEqual(
                _run("git", "rev-parse", "HEAD", cwd=repository).stdout,
                _run("git", "rev-parse", "origin/main", cwd=repository).stdout,
            )

            # A crash after the immutable intent checkpoint but before the
            # journal append is also visible: recovery creates PLANNED,
            # STARTED, and ABORTED instead of silently discarding the attempt.
            pending = qualification_workflow_fixture_input(
                repository_root=repository,
                frozen_at_utc=datetime.now(UTC) - timedelta(seconds=1),
                trial_id="aud009-pending-intent",
                run_id="aud009-pending-run",
            )
            pending_protocol_path = (
                repository / "research/protocols" / f"{pending.protocol.protocol_id}.json"
            )
            pending_workflow_path = (
                repository / "research/workflows" / f"{pending.trial_id}.json"
            )
            pending_protocol_path.write_bytes(pending.protocol.to_json_bytes() + b"\n")
            pending_workflow_path.write_bytes(pending.to_json_bytes() + b"\n")
            _run(
                "git",
                "add",
                str(pending_protocol_path.relative_to(repository)),
                str(pending_workflow_path.relative_to(repository)),
                cwd=repository,
            )
            _run("git", "commit", "-m", "fixture pending intent", cwd=repository)
            _run("git", "push", "origin", "main", cwd=repository)
            after_pending = qualification_workflow_fixture_input(
                repository_root=repository,
                frozen_at_utc=datetime.now(UTC) - timedelta(seconds=1),
                trial_id="aud009-not-started-after-pending",
                run_id="aud009-not-started-after-pending-run",
            )
            pending_input = base / "pending-recovery-input.json"
            pending_output = base / "pending-recovery-output.json"
            pending_input.write_bytes(after_pending.to_json_bytes() + b"\n")
            pending_recovery = _owner_process(
                repository,
                input_path=pending_input,
                output_path=pending_output,
            )
            self.assertEqual(pending_recovery.returncode, 2)
            pending_result = json.loads(pending_output.read_text(encoding="utf-8"))
            self.assertIn("terminalized", pending_result["detail"])
            pending_records = TrialJournal(repository / "research/trials.jsonl").read_records()
            pending_latest = {record.trial_id: record for record in pending_records}
            self.assertEqual(pending_latest[pending.trial_id].state.value, "ABORTED")
            self.assertNotIn(after_pending.trial_id, pending_latest)
            self.assertEqual(_run("git", "status", "--porcelain", cwd=repository).stdout, "")
            self.assertEqual(
                _run("git", "rev-parse", "HEAD", cwd=repository).stdout,
                _run("git", "rev-parse", "origin/main", cwd=repository).stdout,
            )

            # Recreate the authority exactly at the terminal checkpoint, as if
            # the process stopped before diagnostics/report publication.  A
            # retry must finish from persisted evidence without another Run or
            # another Trial Journal transition.
            terminal_commit = result["commits"][2]
            _run(
                "git",
                f"--git-dir={origin}",
                "update-ref",
                "refs/heads/main",
                terminal_commit,
                cwd=base,
            )
            _run(
                "git",
                f"--git-dir={origin}",
                "symbolic-ref",
                "HEAD",
                "refs/heads/main",
                cwd=base,
            )
            recovery_repository = base / "terminal-recovery-repository"
            _run("git", "clone", str(origin), str(recovery_repository), cwd=base)
            _run("git", "config", "user.name", "Owner Recovery Test", cwd=recovery_repository)
            _run(
                "git",
                "config",
                "user.email",
                "owner-recovery@example.invalid",
                cwd=recovery_repository,
            )
            _copy_run_external_dataset_state(
                run_directory=run_dir,
                source_repository=repository,
                target_repository=recovery_repository,
            )
            resume_output = base / "terminal-resume-output.json"
            resumed = _owner_process(
                recovery_repository,
                input_path=input_path,
                output_path=resume_output,
            )
            self.assertEqual(
                resumed.returncode,
                0,
                resumed.stderr + resumed.stdout + resume_output.read_text(encoding="utf-8"),
            )
            resumed_result = json.loads(resume_output.read_text(encoding="utf-8"))
            self.assertEqual(resumed_result["status"], "PASS")
            self.assertEqual(resumed_result["run_id"], value.run_id)
            self.assertEqual(len(resumed_result["commits"]), 2)
            resumed_records = TrialJournal(
                recovery_repository / "research/trials.jsonl",
            ).read_records()
            self.assertEqual(len(resumed_records), 3)
            self.assertTrue(
                (recovery_repository / f"research/reports/{value.trial_id}.json").is_file(),
            )


if __name__ == "__main__":
    unittest.main()
