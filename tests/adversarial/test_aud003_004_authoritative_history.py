from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from datetime import UTC
from datetime import datetime
from pathlib import Path

from crypto_lab.history import AuthoritativeResearchHistory
from crypto_lab.history import HistoryAnchor
from crypto_lab.history import HistoryAnchorStore
from crypto_lab.research import HoldoutEntry
from crypto_lab.research import HoldoutLockSnapshot
from crypto_lab.research import ResearchError
from crypto_lab.research import ResultExposure
from crypto_lab.research import TrialDefinition
from crypto_lab.research import TrialJournal
from crypto_lab.research import TrialState
from crypto_lab.research import UtcInterval
from tests.m4_helpers import candidate
from tests.m4_helpers import instant
from tests.m4_helpers import valid_protocol


class HistoryAttackFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Repair Test"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "repair@example.invalid"], check=True)
        subprocess.run(
            ["git", "-C", str(root), "remote", "add", "origin", "https://example.invalid/history.git"],
            check=True,
        )
        (root / "SSOT.md").write_text("synthetic history authority\n", encoding="utf-8")
        package = root / "src/crypto_lab"
        package.mkdir(parents=True)
        (package / "sealing.py").write_text("# authority marker\n", encoding="utf-8")
        research = root / "research"
        research.mkdir()
        (research / "trials.jsonl").write_bytes(b"")
        (research / "holdout_lock.json").write_bytes(b"{}\n")
        self.commit("genesis files")
        self.store = HistoryAnchorStore(
            repository_root=root,
            journal_path=research / "trials.jsonl",
            holdout_path=research / "holdout_lock.json",
            anchor_path=research / "history_anchors.jsonl",
            require_remote_tip=True,
        )
        self.store.initialize(at_utc=instant("2020-01-01T00:00:00Z"))
        self.commit("anchor genesis")
        self.history = AuthoritativeResearchHistory(self.store)
        self.protocol = valid_protocol(candidate_count=2)

    def commit(self, message: str) -> str:
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-m", message],
            check=True,
            capture_output=True,
        )
        head = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(self.root), "update-ref", "refs/remotes/origin/main", head],
            check=True,
        )
        return head

    def trial(self, index: int, terminal: TrialState) -> str:
        definition = TrialDefinition.synthetic(
            trial_id=f"trial-{index}",
            protocol=self.protocol,
            candidate=candidate(index),
            run_id=f"run-{index}",
        )
        self.history.start_trial(definition, at_utc=instant("2020-05-01T00:00:00Z"))
        self.commit(f"start trial {index}")
        self.history.finish_trial(
            definition.trial_id,
            state=terminal,
            at_utc=instant("2020-05-01T00:01:00Z"),
            result_ref=f"runs/run-{index}",
            reason="adversarial fixture",
            result_exposed=True,
        )
        return self.commit(f"finish trial {index}")

    def git_bytes(self, commit: str, relative: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(self.root), "show", f"{commit}:{relative}"],
            check=True,
            capture_output=True,
        ).stdout

    def append_forged_current_anchor(self, operation: str) -> None:
        anchors = self.store.read_anchors()
        journal_records = self.history.journal.read_records()
        holdout = self.history.holdout.read()
        commit = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        record = HistoryAnchor.create(
            anchor_sequence=len(anchors) + 1,
            previous_anchor_sha256=("GENESIS" if not anchors else anchors[-1].anchor_sha256),
            trial_journal_sha256=hashlib.sha256(
                self.store.journal_path.read_bytes(),
            ).hexdigest(),
            trial_record_count=len(journal_records),
            trial_head_sha256=(
                "GENESIS" if not journal_records else journal_records[-1].journal_entry_sha256
            ),
            holdout_lock_sha256=hashlib.sha256(
                self.store.holdout_path.read_bytes(),
            ).hexdigest(),
            holdout_entry_count=len(holdout.entries),
            holdout_history_sha256=holdout.history_sha256,
            operation=operation,
            source_git_commit=commit,
            source_git_tree=tree,
            created_at_utc=instant("2031-01-01T00:00:00Z"),
        )
        with self.store.anchor_path.open("ab") as stream:
            stream.write(record.to_json_bytes() + b"\n")

    def replace_with_forged_genesis_anchor(self, operation: str) -> None:
        self.store.anchor_path.write_bytes(b"")
        self.append_forged_current_anchor(operation)


class Aud003AuthoritativeJournalTests(unittest.TestCase):
    def _fixture(self, temporary: str) -> tuple[HistoryAttackFixture, str, str]:
        fixture = HistoryAttackFixture(Path(temporary))
        first = fixture.trial(0, TrialState.FAILED)
        second = fixture.trial(1, TrialState.COMPLETED)
        return fixture, first, second

    def test_delete_failed_attempt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, _first, _second = self._fixture(temporary)
            replacement = Path(temporary) / "replacement.jsonl"
            journal = TrialJournal(replacement)
            definition = TrialDefinition.synthetic(
                trial_id="trial-1",
                protocol=fixture.protocol,
                candidate=candidate(1),
                run_id="run-1",
            )
            journal.start(definition, at_utc=instant("2020-05-01T00:00:00Z"))
            journal.finish(
                "trial-1",
                state=TrialState.COMPLETED,
                at_utc=instant("2020-05-01T00:01:00Z"),
                result_ref="runs/run-1",
                reason="replacement",
                result_exposed=True,
            )
            fixture.store.journal_path.write_bytes(replacement.read_bytes())
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_prefix_truncation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, _first, _second = self._fixture(temporary)
            lines = fixture.store.journal_path.read_bytes().splitlines(keepends=True)
            fixture.store.journal_path.write_bytes(b"".join(lines[:-1]))
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_shorter_self_consistent_replacement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, first, _second = self._fixture(temporary)
            fixture.store.journal_path.write_bytes(fixture.git_bytes(first, "research/trials.jsonl"))
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_rollback_to_old_head_files_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, first, _second = self._fixture(temporary)
            fixture.store.journal_path.write_bytes(fixture.git_bytes(first, "research/trials.jsonl"))
            fixture.store.anchor_path.write_bytes(fixture.git_bytes(first, "research/history_anchors.jsonl"))
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_reordered_records_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, _first, _second = self._fixture(temporary)
            lines = fixture.store.journal_path.read_bytes().splitlines(keepends=True)
            lines[0], lines[1] = lines[1], lines[0]
            fixture.store.journal_path.write_bytes(b"".join(lines))
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_genesis_reset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, first, _second = self._fixture(temporary)
            initial_anchor = fixture.git_bytes(first, "research/history_anchors.jsonl").splitlines(keepends=True)[0]
            fixture.store.journal_path.write_bytes(b"")
            fixture.store.anchor_path.write_bytes(initial_anchor)
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_simultaneous_journal_and_anchor_replacement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, first, _second = self._fixture(temporary)
            fixture.store.journal_path.write_bytes(fixture.git_bytes(first, "research/trials.jsonl"))
            fixture.store.anchor_path.write_bytes(fixture.git_bytes(first, "research/history_anchors.jsonl"))
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_committed_descendant_journal_and_anchor_genesis_reset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, _first, _second = self._fixture(temporary)
            fixture.store.journal_path.write_bytes(b"")
            fixture.replace_with_forged_genesis_anchor("ATTACKER_COMMITTED_GENESIS_RESET")
            fixture.commit("attacker commits journal and anchor reset")
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_longer_self_consistent_journal_replacement_with_new_anchor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, _first, _second = self._fixture(temporary)
            replacement = Path(temporary) / "longer-replacement.jsonl"
            journal = TrialJournal(replacement)
            for index in range(3):
                definition = TrialDefinition.synthetic(
                    trial_id=f"replacement-trial-{index}",
                    protocol=fixture.protocol,
                    candidate=candidate(0),
                    run_id=f"replacement-run-{index}",
                )
                journal.start(definition, at_utc=instant("2020-05-01T00:00:00Z"))
                journal.finish(
                    definition.trial_id,
                    state=TrialState.COMPLETED,
                    at_utc=instant("2020-05-01T00:01:00Z"),
                    result_ref=f"runs/{definition.run_id}",
                    reason="longer replacement",
                    result_exposed=True,
                )
            fixture.store.journal_path.write_bytes(replacement.read_bytes())
            fixture.append_forged_current_anchor("ATTACKER_LONGER_JOURNAL_REPLACEMENT")
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_normal_push_merge_cannot_hide_authority_in_second_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, _first, authoritative = self._fixture(temporary)
            root_commit = subprocess.run(
                ["git", "-C", temporary, "rev-list", "--max-parents=0", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "-C", temporary, "checkout", "-b", "attacker", root_commit],
                check=True,
                capture_output=True,
            )
            fixture.replace_with_forged_genesis_anchor("ATTACKER_FIRST_PARENT_RESET")
            fixture.commit("attacker replacement branch")
            subprocess.run(
                [
                    "git",
                    "-C",
                    temporary,
                    "merge",
                    "--no-ff",
                    "-s",
                    "ours",
                    authoritative,
                    "-m",
                    "retain attacker tree with authority as second parent",
                ],
                check=True,
                capture_output=True,
            )
            merge = subprocess.run(
                ["git", "-C", temporary, "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            subprocess.run(
                ["git", "-C", temporary, "branch", "-f", "main", merge],
                check=True,
            )
            subprocess.run(
                ["git", "-C", temporary, "checkout", "main"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", temporary, "update-ref", "refs/remotes/origin/main", merge],
                check=True,
            )
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()


class Aud004AuthoritativeHoldoutTests(unittest.TestCase):
    def _fixture(self, temporary: str) -> tuple[HistoryAttackFixture, str]:
        fixture = HistoryAttackFixture(Path(temporary))
        before = subprocess.run(
            ["git", "-C", temporary, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        exposure = ResultExposure(
            trial_id="holdout-trial",
            market_profile=fixture.protocol.market_profile,
            instrument_id=fixture.protocol.instrument_ids[0],
            scored_interval=UtcInterval(
                start_inclusive=datetime(2030, 1, 1, tzinfo=UTC),
                end_exclusive=datetime(2030, 1, 2, tzinfo=UTC),
            ),
            research_family_id="holdout-family",
            hypothesis_lineage=("holdout-hypothesis",),
            strategy_lineage=("holdout-strategy",),
            dataset_release_id="d" * 64,
            first_exposure_at_utc=datetime(2030, 1, 3, tzinfo=UTC),
            exposure_type="FINAL_HOLDOUT",
            evidence_reference="runs/holdout-trial",
            source_branch="main",
            source_commit="a" * 40,
            seed=1,
            result_bearing=True,
        )
        entry = HoldoutEntry.create(exposure, "GENESIS")
        fixture.history.holdout._write(HoldoutLockSnapshot.create((entry,)))
        fixture.store.anchor_mutation(
            operation="HOLDOUT_CONSUMED_TEST",
            at_utc=datetime(2030, 1, 3, tzinfo=UTC),
        )
        fixture.commit("consume holdout")
        return fixture, before

    def test_empty_holdout_replacement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, _before = self._fixture(temporary)
            fixture.store.holdout_path.write_bytes(b"{}\n")
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_holdout_rollback_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, before = self._fixture(temporary)
            fixture.store.holdout_path.write_bytes(fixture.git_bytes(before, "research/holdout_lock.json"))
            fixture.store.anchor_path.write_bytes(fixture.git_bytes(before, "research/history_anchors.jsonl"))
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_holdout_genesis_reset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, before = self._fixture(temporary)
            fixture.store.holdout_path.write_bytes(HoldoutLockSnapshot.create(()).to_json_bytes() + b"\n")
            fixture.store.anchor_path.write_bytes(fixture.git_bytes(before, "research/history_anchors.jsonl"))
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_committed_descendant_holdout_and_anchor_reset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, _before = self._fixture(temporary)
            fixture.store.holdout_path.write_bytes(b"{}\n")
            fixture.replace_with_forged_genesis_anchor("ATTACKER_COMMITTED_HOLDOUT_RESET")
            fixture.commit("attacker commits Holdout and anchor reset")
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()

    def test_longer_self_consistent_holdout_replacement_with_new_anchor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture, _before = self._fixture(temporary)
            entries = []
            previous = "GENESIS"
            for index in range(2):
                exposure = ResultExposure(
                    trial_id=f"replacement-holdout-{index}",
                    market_profile=fixture.protocol.market_profile,
                    instrument_id=fixture.protocol.instrument_ids[0],
                    scored_interval=UtcInterval(
                        start_inclusive=datetime(2032 + index, 1, 1, tzinfo=UTC),
                        end_exclusive=datetime(2032 + index, 1, 2, tzinfo=UTC),
                    ),
                    research_family_id="replacement-holdout-family",
                    hypothesis_lineage=("replacement-hypothesis",),
                    strategy_lineage=("replacement-strategy",),
                    dataset_release_id="e" * 64,
                    first_exposure_at_utc=datetime(2034, 1, 1, tzinfo=UTC),
                    exposure_type="FINAL_HOLDOUT",
                    evidence_reference=f"runs/replacement-holdout-{index}",
                    source_branch="main",
                    source_commit="b" * 40,
                    seed=index,
                    result_bearing=True,
                )
                entry = HoldoutEntry.create(exposure, previous)
                entries.append(entry)
                previous = entry.entry_id
            fixture.history.holdout._write(HoldoutLockSnapshot.create(tuple(entries)))
            fixture.append_forged_current_anchor("ATTACKER_LONGER_HOLDOUT_REPLACEMENT")
            with self.assertRaises(ResearchError):
                fixture.store.reconcile()


if __name__ == "__main__":
    unittest.main()
