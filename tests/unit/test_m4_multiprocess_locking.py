from __future__ import annotations

import multiprocessing
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from crypto_lab.locking import interprocess_file_lock
from crypto_lab.research import HoldoutEntry
from crypto_lab.research import HoldoutLockStore
from crypto_lab.research import HoldoutLockSnapshot
from crypto_lab.research import ResearchError
from crypto_lab.research import TrialJournal
from crypto_lab.research import UtcInterval
from tests.m4_helpers import instant
from tests.unit.test_m4_journal_holdout import definition
from tests.unit.test_m4_journal_holdout import exposure


def _journal_worker(path: str, trial_id: str, barrier: object, queue: object) -> None:
    try:
        barrier.wait()
        TrialJournal(Path(path)).start(
            definition(trial_id),
            at_utc=instant("2020-05-01T00:00:00Z"),
        )
        queue.put((trial_id, "OK"))
    except Exception as exc:  # pragma: no cover - asserted in parent
        queue.put((trial_id, f"{type(exc).__name__}:{exc}"))


def _holdout_worker(
    holdout_path: str,
    journal_path: str,
    candidate: object,
    barrier: object,
    queue: object,
) -> None:
    try:
        barrier.wait()
        entry = HoldoutLockStore(Path(holdout_path)).consume(
            candidate,
            journal=TrialJournal(Path(journal_path)),
            exposure_resolver={},
        )
        queue.put((candidate.trial_id, "OK", entry.entry_id))
    except Exception as exc:  # pragma: no cover - asserted in parent
        queue.put((candidate.trial_id, getattr(exc, "code", type(exc).__name__), str(exc)))


def _crash_with_partial_journal(path: str, ready: object) -> None:
    target = Path(path)
    lock_path = target.parent / f".{target.name}.lock"
    with interprocess_file_lock(lock_path):
        target.write_bytes(b'{"partial":')
        ready.set()
        os._exit(73)


def _crash_before_holdout_replace(path: str, candidate: object, ready: object) -> None:
    import crypto_lab.research as research_module

    store = HoldoutLockStore(Path(path))
    with interprocess_file_lock(store.lock_path):
        current = store._read_unlocked()
        previous = "GENESIS" if not current.entries else current.entries[-1].entry_id
        pending = HoldoutEntry.create(candidate, previous)

        def crash_replace(_source: object, _target: object) -> None:
            ready.set()
            os._exit(74)

        research_module.os.replace = crash_replace
        store._write_unlocked(HoldoutLockSnapshot.create((*current.entries, pending)))


class MultiprocessHistoryLockTests(unittest.TestCase):
    @staticmethod
    def _context():
        return multiprocessing.get_context("fork")

    def test_trial_start_sequence_and_hash_chain_are_atomic_across_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trials.jsonl"
            context = self._context()
            count = 8
            barrier = context.Barrier(count)
            queue = context.Queue()
            processes = [
                context.Process(
                    target=_journal_worker,
                    args=(str(path), f"trial-{index}", barrier, queue),
                )
                for index in range(count)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(15)
                self.assertEqual(process.exitcode, 0)
            outcomes = [queue.get(timeout=2) for _ in processes]
            self.assertTrue(all(item[1] == "OK" for item in outcomes), outcomes)
            records = TrialJournal(path).read_records()
            self.assertEqual(len(records), count * 2)
            self.assertEqual(
                [item.journal_sequence for item in records],
                list(range(1, count * 2 + 1)),
            )

    def test_overlapping_holdout_consumers_have_exactly_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = self._context()
            candidates = (exposure("race-a"), exposure("race-b"))
            barrier = context.Barrier(2)
            queue = context.Queue()
            processes = [
                context.Process(
                    target=_holdout_worker,
                    args=(
                        str(root / "holdout_lock.json"),
                        str(root / "trials.jsonl"),
                        candidate,
                        barrier,
                        queue,
                    ),
                )
                for candidate in candidates
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(15)
                self.assertEqual(process.exitcode, 0)
            outcomes = [queue.get(timeout=2) for _ in processes]
            self.assertEqual(sum(item[1] == "OK" for item in outcomes), 1, outcomes)
            self.assertEqual(
                sum(item[1] == "HOLDOUT_ALREADY_CONSUMED" for item in outcomes),
                1,
                outcomes,
            )
            self.assertEqual(len(HoldoutLockStore(root / "holdout_lock.json").read().entries), 1)

    def test_nonoverlapping_holdout_updates_are_not_lost(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = self._context()
            second_interval = UtcInterval(
                start_inclusive=instant("2020-05-01T00:00:00Z"),
                end_exclusive=instant("2020-06-01T00:00:00Z"),
            )
            candidates = (
                exposure("nonoverlap-a"),
                replace(exposure("nonoverlap-b"), scored_interval=second_interval),
            )
            barrier = context.Barrier(2)
            queue = context.Queue()
            processes = [
                context.Process(
                    target=_holdout_worker,
                    args=(
                        str(root / "holdout_lock.json"),
                        str(root / "trials.jsonl"),
                        candidate,
                        barrier,
                        queue,
                    ),
                )
                for candidate in candidates
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(15)
                self.assertEqual(process.exitcode, 0)
            outcomes = [queue.get(timeout=2) for _ in processes]
            self.assertTrue(all(item[1] == "OK" for item in outcomes), outcomes)
            self.assertEqual(len(HoldoutLockStore(root / "holdout_lock.json").read().entries), 2)

    def test_process_crash_releases_lock_but_partial_state_remains_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trials.jsonl"
            context = self._context()
            ready = context.Event()
            process = context.Process(target=_crash_with_partial_journal, args=(str(path), ready))
            process.start()
            self.assertTrue(ready.wait(5))
            process.join(10)
            self.assertEqual(process.exitcode, 73)
            with self.assertRaisesRegex(ResearchError, "TRIAL_HISTORY_INCOMPLETE"):
                TrialJournal(path).read_records()

    def test_holdout_crash_before_atomic_replace_preserves_head_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "holdout_lock.json"
            store = HoldoutLockStore(path)
            first = exposure("holdout-before-crash")
            store._write(
                HoldoutLockSnapshot.create(
                    (HoldoutEntry.create(first, "GENESIS"),),
                ),
            )
            second = replace(
                exposure("holdout-after-crash"),
                scored_interval=UtcInterval(
                    start_inclusive=instant("2020-05-01T00:00:00Z"),
                    end_exclusive=instant("2020-06-01T00:00:00Z"),
                ),
            )
            context = self._context()
            ready = context.Event()
            process = context.Process(
                target=_crash_before_holdout_replace,
                args=(str(path), second, ready),
            )
            process.start()
            self.assertTrue(ready.wait(5))
            process.join(10)
            self.assertEqual(process.exitcode, 74)
            self.assertEqual(store.read().entries[0].exposure.trial_id, first.trial_id)
            committed = store.consume(
                second,
                journal=TrialJournal(root / "trials.jsonl"),
                exposure_resolver={},
            )
            self.assertEqual(committed.exposure.trial_id, second.trial_id)
            self.assertEqual(len(store.read().entries), 2)


if __name__ == "__main__":
    unittest.main()
