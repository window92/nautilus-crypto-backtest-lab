from __future__ import annotations

import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import crypto_lab.research as research_module
from crypto_lab.history import AuthoritativeResearchHistory
from crypto_lab.history import HistoryAnchorStore
from crypto_lab.research import HoldoutLockStore
from crypto_lab.research import ResearchError
from crypto_lab.research import TrialDefinition
from crypto_lab.research import TrialJournal
from crypto_lab.research import TrialState
from crypto_lab.status import FailureCode
from tests.adversarial.test_aud003_004_authoritative_history import HistoryAttackFixture
from tests.m4_helpers import candidate
from tests.m4_helpers import instant


def _definition(fixture: HistoryAttackFixture, trial_id: str) -> TrialDefinition:
    return TrialDefinition.synthetic(
        trial_id=trial_id,
        protocol=fixture.protocol,
        candidate=candidate(0),
        run_id=f"run-{trial_id}",
    )


def _recovery_worker(
    repository: str,
    definition: TrialDefinition,
    barrier: object,
    queue: object,
) -> None:
    root = Path(repository)
    research = root / "research"
    history = AuthoritativeResearchHistory(
        HistoryAnchorStore(
            repository_root=root,
            journal_path=research / "trials.jsonl",
            holdout_path=research / "holdout_lock.json",
            anchor_path=research / "history_anchors.jsonl",
        ),
    )
    try:
        barrier.wait()
        history.start_trial(
            definition,
            at_utc=instant("2030-01-01T00:00:00Z"),
        )
        queue.put("RECOVERED")
    except Exception as exc:  # pragma: no cover - asserted in the parent
        queue.put(getattr(exc, "code", type(exc).__name__))


class R2LockingAndDurabilityTests(unittest.TestCase):
    def assert_durability_failure(self, context: object) -> None:
        error = context.exception
        self.assertIsInstance(error, ResearchError)
        self.assertEqual(
            error.code,
            FailureCode.JOURNAL_DURABILITY_FAILURE.value,
        )

    def test_lock_file_symlink_is_rejected_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = TrialJournal(root / "trials.jsonl")
            target = root / "outside-lock-target"
            target.write_bytes(b"sentinel")
            os.symlink(target, journal.lock_path)

            with self.assertRaises(ResearchError) as caught:
                journal.read_records()

            self.assert_durability_failure(caught)
            self.assertEqual(target.read_bytes(), b"sentinel")

    def test_journal_holdout_and_anchor_symlinks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "redirect-target"
            target.write_bytes(b"sentinel")

            journal_path = root / "trials.jsonl"
            os.symlink(target, journal_path)
            with self.assertRaises(ResearchError) as journal_caught:
                TrialJournal(journal_path).read_records()
            self.assert_durability_failure(journal_caught)

            holdout_path = root / "holdout_lock.json"
            os.symlink(target, holdout_path)
            with self.assertRaises(ResearchError) as holdout_caught:
                HoldoutLockStore(holdout_path).read()
            self.assert_durability_failure(holdout_caught)

            anchor_path = root / "history_anchors.jsonl"
            os.symlink(target, anchor_path)
            store = HistoryAnchorStore(
                repository_root=root,
                journal_path=journal_path,
                holdout_path=holdout_path,
                anchor_path=anchor_path,
                require_remote_tip=False,
            )
            with self.assertRaises(ResearchError) as anchor_caught:
                store.read_anchors()
            self.assert_durability_failure(anchor_caught)
            self.assertEqual(target.read_bytes(), b"sentinel")

    def test_symlinked_parent_and_nonregular_authority_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            real_parent = root / "real"
            real_parent.mkdir()
            linked_parent = root / "linked"
            os.symlink(real_parent, linked_parent)

            with self.assertRaises(ResearchError) as parent_caught:
                TrialJournal(linked_parent / "trials.jsonl").read_records()
            self.assert_durability_failure(parent_caught)

            directory_authority = root / "directory-as-journal"
            directory_authority.mkdir()
            with self.assertRaises(ResearchError) as regular_caught:
                TrialJournal(directory_authority).read_records()
            self.assert_durability_failure(regular_caught)

    def test_planned_and_started_are_one_durable_append_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HistoryAttackFixture(Path(temporary))
            definition = _definition(fixture, "batched-start")
            calls: list[bytes] = []
            actual = research_module.safe_append_bytes

            def capture(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
                calls.append(payload)
                actual(path, payload, mode=mode)

            with patch.object(research_module, "safe_append_bytes", capture):
                fixture.history.journal.start(
                    definition,
                    at_utc=instant("2020-05-01T00:00:00Z"),
                )

            self.assertEqual(len(calls), 1)
            self.assertEqual(len(calls[0].splitlines()), 2)
            self.assertEqual(
                tuple(item.state for item in fixture.history.journal.read_records()),
                (TrialState.PLANNED, TrialState.STARTED),
            )

    def test_exact_start_suffix_recovers_missing_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HistoryAttackFixture(Path(temporary))
            definition = _definition(fixture, "start-crash")
            original = fixture.history.journal.start(
                definition,
                at_utc=instant("2020-05-01T00:00:00Z"),
            )
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

            recovered = fixture.history.start_trial(
                definition,
                at_utc=instant("2030-01-01T00:00:00Z"),
            )

            self.assertEqual(
                tuple(item.to_json_bytes() for item in recovered),
                tuple(item.to_json_bytes() for item in original),
            )
            self.assertEqual(
                fixture.store.read_anchors()[-1].operation,
                f"TRIAL_STARTED:{definition.trial_id}",
            )
            self.assertEqual(fixture.store.reconcile(), fixture.store.read_anchors()[-1])

    def test_exact_terminal_suffix_recovers_missing_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HistoryAttackFixture(Path(temporary))
            definition = _definition(fixture, "terminal-crash")
            fixture.history.start_trial(
                definition,
                at_utc=instant("2020-05-01T00:00:00Z"),
            )
            fixture.commit("publish start before terminal crash")
            terminal_at = instant("2020-05-01T00:01:00Z")
            original = fixture.history.journal.finish(
                definition.trial_id,
                state=TrialState.ABORTED,
                at_utc=terminal_at,
                result_ref="NOT_APPLICABLE",
                reason="simulated process loss",
                result_exposed=False,
            )

            recovered = fixture.history.finish_trial(
                definition.trial_id,
                state=TrialState.ABORTED,
                at_utc=terminal_at,
                result_ref="NOT_APPLICABLE",
                reason="simulated process loss",
                result_exposed=False,
            )

            self.assertEqual(recovered.to_json_bytes(), original.to_json_bytes())
            self.assertEqual(
                fixture.store.read_anchors()[-1].operation,
                f"TRIAL_TERMINAL:{definition.trial_id}:ABORTED",
            )
            self.assertEqual(fixture.store.reconcile(), fixture.store.read_anchors()[-1])

    def test_recovery_rejects_nonmatching_suffix_with_structured_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HistoryAttackFixture(Path(temporary))
            fixture.history.journal.start(
                _definition(fixture, "actual-crash"),
                at_utc=instant("2020-05-01T00:00:00Z"),
            )

            with self.assertRaises(ResearchError) as caught:
                fixture.history.start_trial(
                    _definition(fixture, "forged-recovery"),
                    at_utc=instant("2020-05-01T00:00:00Z"),
                )

            self.assert_durability_failure(caught)
            self.assertEqual(len(fixture.store.read_anchors()), 1)

    def test_concurrent_recovery_appends_at_most_one_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = HistoryAttackFixture(Path(temporary))
            definition = _definition(fixture, "concurrent-recovery")
            fixture.history.journal.start(
                definition,
                at_utc=instant("2020-05-01T00:00:00Z"),
            )
            context = multiprocessing.get_context("fork")
            barrier = context.Barrier(2)
            queue = context.Queue()
            processes = [
                context.Process(
                    target=_recovery_worker,
                    args=(str(fixture.root), definition, barrier, queue),
                )
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(15)
                self.assertEqual(process.exitcode, 0)
            outcomes = [queue.get(timeout=2) for _ in processes]

            self.assertEqual(outcomes.count("RECOVERED"), 1, outcomes)
            self.assertEqual(
                outcomes.count(FailureCode.TRIAL_HISTORY_INCOMPLETE.value),
                1,
                outcomes,
            )
            self.assertEqual(len(fixture.store.read_anchors()), 2)
            self.assertEqual(
                fixture.store.read_anchors()[-1].operation,
                f"TRIAL_STARTED:{definition.trial_id}",
            )


if __name__ == "__main__":
    unittest.main()
