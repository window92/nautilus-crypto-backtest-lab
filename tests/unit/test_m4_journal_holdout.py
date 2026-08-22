from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from crypto_lab.research import HoldoutLockStore
from crypto_lab.research import ResearchError
from crypto_lab.research import ResultExposure
from crypto_lab.research import TrialDefinition
from crypto_lab.research import TrialJournal
from crypto_lab.research import TrialState
from tests.m4_helpers import HOLDOUT
from tests.m4_helpers import candidate
from tests.m4_helpers import instant
from tests.m4_helpers import valid_protocol


def definition(trial_id: str = "trial-a") -> TrialDefinition:
    protocol = valid_protocol()
    return TrialDefinition.synthetic(
        trial_id=trial_id,
        protocol=protocol,
        candidate=candidate(0),
        run_id=f"run-{trial_id}",
    )


def exposure(trial_id: str = "trial-a", **changes: object) -> ResultExposure:
    base = ResultExposure(
        trial_id=trial_id,
        market_profile=valid_protocol().market_profile,
        instrument_id="BTCUSDT.BINANCE",
        scored_interval=HOLDOUT,
        research_family_id="synthetic-m4-family",
        hypothesis_lineage=("synthetic-m4-hypothesis",),
        strategy_lineage=("strategy-v1",),
        dataset_release_id="d" * 64,
        first_exposure_at_utc=instant("2020-05-02T00:00:00Z"),
        exposure_type="PERFORMANCE_METRIC",
        evidence_reference=f"runs/run-{trial_id}/nautilus_result.json",
        source_branch="main",
        source_commit="1" * 40,
        seed=7,
        result_bearing=True,
    )
    return replace(base, **changes)


class TrialJournalTests(unittest.TestCase):
    def test_g15_failed_blocked_and_aborted_trials_remain_append_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = TrialJournal(Path(temporary) / "trials.jsonl")
            for index, state in enumerate(
                (TrialState.FAILED, TrialState.BLOCKED, TrialState.ABORTED),
            ):
                item = definition(f"trial-{index}")
                journal.start(item, at_utc=instant("2020-05-01T00:00:00Z"))
                journal.finish(
                    item.trial_id,
                    state=state,
                    at_utc=instant("2020-05-01T00:01:00Z"),
                    result_ref="partial/result.json",
                    reason=f"expected {state.value.lower()} fixture",
                    result_exposed=True,
                )
            records = journal.read_records()
            self.assertEqual(len(records), 9)
            self.assertEqual(
                tuple(record.state for record in records[2::3]),
                (TrialState.FAILED, TrialState.BLOCKED, TrialState.ABORTED),
            )
            self.assertEqual(journal.started_trial_ids(), ("trial-0", "trial-1", "trial-2"))

    def test_terminal_transition_cannot_return_to_started_and_retry_is_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            journal = TrialJournal(Path(temporary) / "trials.jsonl")
            item = definition()
            journal.start(item, at_utc=instant("2020-05-01T00:00:00Z"))
            journal.finish(
                item.trial_id,
                state=TrialState.FAILED,
                at_utc=instant("2020-05-01T00:01:00Z"),
                result_ref="NOT_APPLICABLE",
                reason="fixture failure",
                result_exposed=False,
            )
            with self.assertRaisesRegex(ResearchError, "TRIAL_HISTORY_INCOMPLETE"):
                journal.transition(
                    item.trial_id,
                    state=TrialState.STARTED,
                    at_utc=instant("2020-05-01T00:02:00Z"),
                )
            journal.start(
                definition("trial-a-retry-2"),
                at_utc=instant("2020-05-01T00:03:00Z"),
            )
            self.assertEqual(journal.started_trial_ids(), ("trial-a", "trial-a-retry-2"))

    def test_truncated_or_malformed_journal_blocks_eligibility(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trials.jsonl"
            journal = TrialJournal(path)
            journal.start(definition(), at_utc=instant("2020-05-01T00:00:00Z"))
            path.write_bytes(path.read_bytes() + b'{"partial":')
            with self.assertRaisesRegex(ResearchError, "TRIAL_HISTORY_INCOMPLETE"):
                journal.read_records()


class HoldoutTests(unittest.TestCase):
    def test_g16_first_exposure_consumes_and_overlap_cannot_be_relabelled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = TrialJournal(root / "trials.jsonl")
            item = definition()
            journal.start(item, at_utc=instant("2020-05-01T00:00:00Z"))
            journal.finish(
                item.trial_id,
                state=TrialState.COMPLETED,
                at_utc=instant("2020-05-02T00:00:00Z"),
                result_ref="runs/run-trial-a/nautilus_result.json",
                reason="NOT_APPLICABLE",
                result_exposed=True,
            )
            store = HoldoutLockStore(root / "holdout_lock.json")
            entry = store.consume(
                exposure(),
                journal=journal,
                exposure_resolver={item.trial_id: exposure()},
            )
            self.assertEqual(store.read().entries, (entry,))
            for changed in (
                {"trial_id": "renamed-trial"},
                {"dataset_release_id": "e" * 64},
                {"source_branch": "new-branch"},
                {"seed": 99},
                {"hypothesis_lineage": ("renamed-hypothesis",)},
                {"strategy_lineage": ("strategy-v1", "descendant-v2")},
            ):
                with self.subTest(changed=changed):
                    changed_trial_id = str(changed.get("trial_id", "new-trial"))
                    exposure_changes = {
                        key: value for key, value in changed.items() if key != "trial_id"
                    }
                    with self.assertRaisesRegex(ResearchError, "HOLDOUT_ALREADY_CONSUMED"):
                        store.require_fresh(
                            exposure(changed_trial_id, **exposure_changes),
                            journal=journal,
                            exposure_resolver={item.trial_id: exposure()},
                        )

    def test_partial_aborted_exposure_consumes_holdout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = TrialJournal(root / "trials.jsonl")
            item = definition()
            journal.start(item, at_utc=instant("2020-05-01T00:00:00Z"))
            journal.finish(
                item.trial_id,
                state=TrialState.ABORTED,
                at_utc=instant("2020-05-01T00:01:00Z"),
                result_ref="partial/equity.json",
                reason="external interruption after partial Equity exposure",
                result_exposed=True,
            )
            store = HoldoutLockStore(root / "holdout_lock.json")
            with self.assertRaisesRegex(ResearchError, "HOLDOUT_ALREADY_CONSUMED"):
                store.require_fresh(
                    exposure("candidate-holdout"),
                    journal=journal,
                    exposure_resolver={item.trial_id: exposure(exposure_type="PARTIAL_ABORTED_OUTPUT")},
                )

    def test_unknown_result_exposure_blocks_as_history_violation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = TrialJournal(root / "trials.jsonl")
            item = definition()
            journal.start(item, at_utc=instant("2020-05-01T00:00:00Z"))
            journal.finish(
                item.trial_id,
                state=TrialState.COMPLETED,
                at_utc=instant("2020-05-01T00:01:00Z"),
                result_ref="missing/evidence.json",
                reason="NOT_APPLICABLE",
                result_exposed=True,
            )
            with self.assertRaisesRegex(ResearchError, "HOLDOUT_HISTORY_VIOLATION"):
                HoldoutLockStore(root / "holdout_lock.json").require_fresh(
                    exposure("future"),
                    journal=journal,
                    exposure_resolver={},
                )

    def test_holdout_history_is_hash_chained_and_cannot_be_shortened_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            journal = TrialJournal(root / "trials.jsonl")
            first = definition("trial-first")
            journal.start(first, at_utc=instant("2020-05-01T00:00:00Z"))
            journal.finish(
                first.trial_id,
                state=TrialState.COMPLETED,
                at_utc=instant("2020-05-02T00:00:00Z"),
                result_ref="runs/first/result.json",
                reason="NOT_APPLICABLE",
                result_exposed=True,
            )
            store = HoldoutLockStore(root / "holdout_lock.json")
            first_exposure = exposure("trial-first")
            first_entry = store.consume(
                first_exposure,
                journal=journal,
                exposure_resolver={first.trial_id: first_exposure},
            )
            snapshot = store.read()
            self.assertEqual(snapshot.entries[0], first_entry)
            raw = snapshot.to_builtins()
            raw["entries"] = []
            # A shortened snapshot cannot retain the accepted history identity.
            import json

            (root / "holdout_lock.json").write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ResearchError, "HOLDOUT_HISTORY_VIOLATION"):
                store.read()


if __name__ == "__main__":
    unittest.main()
