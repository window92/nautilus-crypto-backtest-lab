"""Git-anchored append-only authority for Trial Journal and Holdout history."""

from __future__ import annotations

import hashlib
import os
import subprocess
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Iterator

from crypto_lab.config import StrictModel
from crypto_lab.config import _require_sha256
from crypto_lab.config import _require_utc
from crypto_lab.git_identity import require_repository_root
from crypto_lab.hashing import canonical_sha256
from crypto_lab.locking import interprocess_file_lock
from crypto_lab.locking import safe_append_bytes
from crypto_lab.locking import safe_read_bytes
from crypto_lab.research import HoldoutEntry
from crypto_lab.research import HoldoutLockStore
from crypto_lab.research import ResearchError
from crypto_lab.research import ResultExposure
from crypto_lab.research import TrialDefinition
from crypto_lab.research import TrialJournal
from crypto_lab.research import TrialRecord
from crypto_lab.research import TrialState
from crypto_lab.status import FailureCode


@contextmanager
def _history_file_lock(path: Path, *, shared: bool = False) -> Iterator[None]:
    try:
        with interprocess_file_lock(path, shared=shared):
            yield
    except OSError as exc:
        raise ResearchError(
            FailureCode.JOURNAL_DURABILITY_FAILURE,
            f"unsafe authoritative-history lock path: {exc}",
        ) from exc


class HistoryAnchor(StrictModel):
    schema_version: int
    anchor_sequence: int
    previous_anchor_sha256: str
    anchor_sha256: str
    trial_journal_sha256: str
    trial_record_count: int
    trial_head_sha256: str
    holdout_lock_sha256: str
    holdout_entry_count: int
    holdout_history_sha256: str
    operation: str
    source_git_commit: str
    source_git_tree: str
    created_at_utc: datetime

    def __post_init__(self) -> None:
        if self.schema_version != 1 or self.anchor_sequence <= 0:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "invalid history anchor schema")
        if self.previous_anchor_sha256 != "GENESIS":
            _require_sha256(self.previous_anchor_sha256, "anchor.previous_anchor_sha256")
        for name in (
            "anchor_sha256",
            "trial_journal_sha256",
            "holdout_lock_sha256",
            "holdout_history_sha256",
        ):
            _require_sha256(getattr(self, name), f"anchor.{name}")
        if self.trial_head_sha256 != "GENESIS":
            _require_sha256(self.trial_head_sha256, "anchor.trial_head_sha256")
        if self.trial_record_count < 0 or self.holdout_entry_count < 0:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "negative history anchor count")
        if not self.operation:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "anchor operation is required")
        if len(self.source_git_commit) != 40 or len(self.source_git_tree) != 40:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "anchor needs full Git identities")
        _require_utc(self.created_at_utc, "anchor.created_at_utc")
        if canonical_sha256(self.material_payload()) != self.anchor_sha256:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "history anchor hash mismatch")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "anchor_sha256"
        }

    @classmethod
    def create(cls, **values: Any) -> HistoryAnchor:
        material = {"schema_version": 1, **values}
        return cls(anchor_sha256=canonical_sha256(material), **material)


class HistoryAnchorStore:
    """Reconcile replaceable files against the anchor prefix committed at HEAD."""

    def __init__(
        self,
        *,
        repository_root: Path,
        journal_path: Path,
        holdout_path: Path,
        anchor_path: Path,
        require_remote_tip: bool = True,
    ) -> None:
        self.repository_root = require_repository_root(repository_root)
        self.journal_path = self._contained(journal_path)
        self.holdout_path = self._contained(holdout_path)
        self.anchor_path = self._contained(anchor_path)
        self.require_remote_tip = require_remote_tip

    def _contained(self, path: Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.repository_root / candidate
        resolved = Path(os.path.abspath(candidate))
        try:
            resolved.relative_to(self.repository_root)
        except ValueError as exc:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                f"authoritative history path escapes repository: {resolved}",
            ) from exc
        return resolved

    def _git(self, *args: str, check: bool = True) -> str:
        process = subprocess.run(
            ["git", *args],
            cwd=self.repository_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if check and process.returncode != 0:
            detail = process.stderr.strip() or process.stdout.strip() or "git command failed"
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, detail)
        return process.stdout.strip()

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.repository_root).as_posix()

    @staticmethod
    def _sha(path: Path) -> str:
        try:
            payload = safe_read_bytes(path, missing_ok=True)
        except OSError as exc:
            raise ResearchError(
                FailureCode.JOURNAL_DURABILITY_FAILURE,
                f"unsafe authoritative-history path: {exc}",
            ) from exc
        if payload is None:
            payload = b""
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _read_safe(path: Path, *, missing_ok: bool = False) -> bytes | None:
        try:
            return safe_read_bytes(path, missing_ok=missing_ok)
        except OSError as exc:
            raise ResearchError(
                FailureCode.JOURNAL_DURABILITY_FAILURE,
                f"unsafe authoritative-history path: {exc}",
            ) from exc

    def _records_from_bytes(self, payload: bytes) -> tuple[HistoryAnchor, ...]:
        if payload and not payload.endswith(b"\n"):
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "truncated anchor line")
        records: list[HistoryAnchor] = []
        for number, line in enumerate(payload.splitlines(), start=1):
            if not line:
                raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "blank anchor line")
            try:
                record = HistoryAnchor.from_json_bytes(line)
            except Exception as exc:
                raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                    f"malformed history anchor {number}: {exc}",
                ) from exc
            expected_previous = "GENESIS" if not records else records[-1].anchor_sha256
            if (
                record.anchor_sequence != number
                or record.previous_anchor_sha256 != expected_previous
            ):
                raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "history anchor chain reset")
            if records:
                previous = records[-1]
                if (
                    record.trial_record_count < previous.trial_record_count
                    or record.holdout_entry_count < previous.holdout_entry_count
                ):
                    raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                        "history anchor counts rolled back",
                    )
                if record.trial_record_count == previous.trial_record_count and (
                    record.trial_journal_sha256 != previous.trial_journal_sha256
                    or record.trial_head_sha256 != previous.trial_head_sha256
                ):
                    raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                        "Trial Journal changed without an append",
                    )
                if record.holdout_entry_count == previous.holdout_entry_count and (
                    record.holdout_lock_sha256 != previous.holdout_lock_sha256
                    or record.holdout_history_sha256 != previous.holdout_history_sha256
                ):
                    raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                        "Holdout history changed without an append",
                    )
            records.append(record)
        return tuple(records)

    def read_anchors(self) -> tuple[HistoryAnchor, ...]:
        payload = self._read_safe(self.anchor_path, missing_ok=True) or b""
        return self._records_from_bytes(payload)

    def committed_anchors(self) -> tuple[HistoryAnchor, ...]:
        """Return the immutable anchor prefix stored at Git HEAD."""

        return self._committed_anchors()

    def _committed_anchors(self) -> tuple[HistoryAnchor, ...]:
        relative = self._relative(self.anchor_path)
        process = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=self.repository_root,
            check=False,
            capture_output=True,
        )
        if process.returncode != 0:
            return ()
        return self._records_from_bytes(process.stdout)

    def _committed_anchor_history(self) -> tuple[tuple[HistoryAnchor, ...], ...]:
        """Read every distinct anchor version reachable from authoritative HEAD."""

        commits = self._git("rev-list", "--reverse", "HEAD").splitlines()
        relative = self._relative(self.anchor_path)
        versions: list[tuple[HistoryAnchor, ...]] = []
        for commit in commits:
            process = subprocess.run(
                ["git", "show", f"{commit}:{relative}"],
                cwd=self.repository_root,
                check=False,
                capture_output=True,
            )
            if process.returncode != 0:
                continue
            version = self._records_from_bytes(process.stdout)
            if not versions or version != versions[-1]:
                versions.append(version)
        return tuple(versions)

    def _verify_committed_anchor_prefixes(
        self,
        current: tuple[HistoryAnchor, ...],
    ) -> None:
        for version in self._committed_anchor_history():
            if len(current) < len(version) or current[: len(version)] != version:
                raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                    "current anchors do not extend every reachable committed Git anchor version",
                )

    def _verify_state_prefixes(self, anchors: tuple[HistoryAnchor, ...]) -> None:
        """Prove every historical anchor still identifies a prefix of current state."""

        journal_records = TrialJournal(self.journal_path).read_records()
        holdout_entries = HoldoutLockStore(self.holdout_path).read().entries
        empty_journal_sha256 = hashlib.sha256(b"").hexdigest()
        for anchor in anchors:
            if anchor.trial_record_count > len(journal_records):
                raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                    "Trial Journal is shorter than an authoritative anchor",
                )
            journal_prefix = journal_records[: anchor.trial_record_count]
            prefix_bytes = b"".join(record.to_json_bytes() + b"\n" for record in journal_prefix)
            prefix_sha256 = hashlib.sha256(prefix_bytes).hexdigest()
            prefix_head = (
                "GENESIS" if not journal_prefix else journal_prefix[-1].journal_entry_sha256
            )
            if (
                prefix_sha256 != anchor.trial_journal_sha256
                or prefix_head != anchor.trial_head_sha256
                or (not journal_prefix and prefix_sha256 != empty_journal_sha256)
            ):
                raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                    "Trial Journal no longer contains an anchored byte-identical prefix",
                )
            if anchor.holdout_entry_count > len(holdout_entries):
                raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                    "Holdout history is shorter than an authoritative anchor",
                )
            holdout_prefix = holdout_entries[: anchor.holdout_entry_count]
            if canonical_sha256(holdout_prefix) != anchor.holdout_history_sha256:
                raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                    "Holdout history no longer contains an anchored semantic prefix",
                )

    def _remote_tip_matches(self) -> None:
        if not self.require_remote_tip:
            return
        branch = self._git("symbolic-ref", "--quiet", "--short", "HEAD")
        local = self._git("rev-parse", "HEAD")
        remote_ref = f"refs/remotes/origin/{branch}"
        process = subprocess.run(
            ["git", "show-ref", "--verify", remote_ref],
            cwd=self.repository_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                f"authoritative remote tracking ref is absent: {remote_ref}",
            )
        remote = self._git("rev-parse", remote_ref)
        if local != remote:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                "HEAD rollback/divergence from authoritative origin branch",
            )

    def _current_state(self) -> dict[str, Any]:
        journal_records = TrialJournal(self.journal_path).read_records()
        holdout = HoldoutLockStore(self.holdout_path).read()
        return {
            "trial_journal_sha256": self._sha(self.journal_path),
            "trial_record_count": len(journal_records),
            "trial_head_sha256": (
                "GENESIS" if not journal_records else journal_records[-1].journal_entry_sha256
            ),
            "holdout_lock_sha256": self._sha(self.holdout_path),
            "holdout_entry_count": len(holdout.entries),
            "holdout_history_sha256": holdout.history_sha256,
        }

    def reconcile(self) -> HistoryAnchor:
        self._remote_tip_matches()
        current = self.read_anchors()
        committed = self._committed_anchors()
        if not current:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "authoritative history anchor is absent")
        if len(current) < len(committed) or current[: len(committed)] != committed:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                "history anchor replacement, truncation, reorder, rollback, or genesis reset",
            )
        self._verify_committed_anchor_prefixes(current)
        self._verify_state_prefixes(current)
        for anchor in current:
            resolved_tree = self._git("rev-parse", f"{anchor.source_git_commit}^{{tree}}")
            if resolved_tree != anchor.source_git_tree:
                raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "anchor Git tree mismatch")
            if subprocess.run(
                ["git", "merge-base", "--is-ancestor", anchor.source_git_commit, "HEAD"],
                cwd=self.repository_root,
                check=False,
                capture_output=True,
            ).returncode != 0:
                raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "anchor Git lineage rollback")
        state = self._current_state()
        latest = current[-1]
        mismatches = [name for name, value in state.items() if getattr(latest, name) != value]
        if mismatches:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                "authoritative state differs from latest anchor: " + ",".join(mismatches),
            )
        return latest

    def reconcile_committed(self) -> HistoryAnchor:
        """Require the reconciled current head to be present at Git HEAD exactly."""

        latest = self.reconcile()
        current = self.read_anchors()
        committed = self._committed_anchors()
        if current != committed:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                "current history/anchor update is not committed before Official resolution",
            )
        return latest

    def initialize(self, *, at_utc: datetime) -> HistoryAnchor:
        _require_utc(at_utc, "anchor.initialize")
        if self.read_anchors() or self._committed_anchors():
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "history anchor is already initialized")
        for path in (self.journal_path, self.holdout_path):
            relative = self._relative(path)
            committed = subprocess.run(
                ["git", "show", f"HEAD:{relative}"],
                cwd=self.repository_root,
                check=False,
                capture_output=True,
            )
            current = self._read_safe(path)
            if committed.returncode != 0 or committed.stdout != current:
                raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE,
                    f"genesis authority requires committed current bytes for {relative}",
                )
        return self._append_current(operation="GENESIS_FROM_COMMITTED_HISTORY", at_utc=at_utc)

    def _append_current(self, *, operation: str, at_utc: datetime) -> HistoryAnchor:
        _require_utc(at_utc, "anchor.append")
        anchors = self.read_anchors()
        committed = self._committed_anchors()
        if len(anchors) < len(committed) or anchors[: len(committed)] != committed:
            raise ResearchError(FailureCode.TRIAL_HISTORY_INCOMPLETE, "committed anchor prefix was replaced")
        commit = self._git("rev-parse", "HEAD")
        tree = self._git("rev-parse", "HEAD^{tree}")
        record = HistoryAnchor.create(
            anchor_sequence=len(anchors) + 1,
            previous_anchor_sha256="GENESIS" if not anchors else anchors[-1].anchor_sha256,
            operation=operation,
            source_git_commit=commit,
            source_git_tree=tree,
            created_at_utc=at_utc,
            **self._current_state(),
        )
        try:
            safe_append_bytes(self.anchor_path, record.to_json_bytes() + b"\n")
        except OSError as exc:
            raise ResearchError(
                FailureCode.JOURNAL_DURABILITY_FAILURE,
                f"unsafe History Anchor append path: {exc}",
            ) from exc
        return record

    def anchor_mutation(self, *, operation: str, at_utc: datetime) -> HistoryAnchor:
        """Anchor one already-fsynced authorized mutation after prefix reconciliation."""

        return self._append_current(operation=operation, at_utc=at_utc)

    @staticmethod
    def _record_definition(record: TrialRecord) -> TrialDefinition:
        return TrialDefinition(
            **{
                field.name: getattr(record, field.name)
                for field in fields(TrialDefinition)
            },
        )

    @staticmethod
    def _same_record_bytes(
        left: tuple[TrialRecord, ...],
        right: tuple[TrialRecord, ...],
    ) -> bool:
        return tuple(item.to_json_bytes() for item in left) == tuple(
            item.to_json_bytes() for item in right
        )

    def _recover_unanchored_journal_records(
        self,
        *,
        expected_suffix: tuple[TrialRecord, ...],
        operation: str,
        at_utc: datetime,
    ) -> HistoryAnchor | None:
        """Recover one exact fsynced Journal suffix missing only its Anchor.

        Recovery is deliberately narrow: the current Anchor file must still be
        byte-identical to committed HEAD, Holdout state must be unchanged, and
        the complete Journal suffix must equal records independently derived by
        the caller from its already-authorized operation.
        """

        _require_utc(at_utc, "anchor.recovery")
        self._remote_tip_matches()
        anchors = self.read_anchors()
        committed = self._committed_anchors()
        if not anchors or anchors != committed:
            return None
        self._verify_committed_anchor_prefixes(anchors)
        self._verify_state_prefixes(anchors)
        latest = anchors[-1]
        state = self._current_state()
        anchored_state = {
            "trial_journal_sha256": latest.trial_journal_sha256,
            "trial_record_count": latest.trial_record_count,
            "trial_head_sha256": latest.trial_head_sha256,
            "holdout_lock_sha256": latest.holdout_lock_sha256,
            "holdout_entry_count": latest.holdout_entry_count,
            "holdout_history_sha256": latest.holdout_history_sha256,
        }
        if state == anchored_state:
            return None
        for name in (
            "holdout_lock_sha256",
            "holdout_entry_count",
            "holdout_history_sha256",
        ):
            if state[name] != anchored_state[name]:
                raise ResearchError(
                    FailureCode.JOURNAL_DURABILITY_FAILURE,
                    "unanchored Journal recovery found a concurrent Holdout mutation",
                )
        records = TrialJournal(self.journal_path).read_records()
        suffix = records[latest.trial_record_count :]
        if (
            not self._same_record_bytes(tuple(suffix), expected_suffix)
            or len(records) != latest.trial_record_count + len(expected_suffix)
            or not expected_suffix
            or state["trial_head_sha256"] != expected_suffix[-1].journal_entry_sha256
        ):
            raise ResearchError(
                FailureCode.JOURNAL_DURABILITY_FAILURE,
                "unanchored Journal suffix is not the exact recoverable mutation",
            )
        return self._append_current(operation=operation, at_utc=at_utc)

    def recover_unanchored_trial_start(
        self,
        definition: TrialDefinition,
    ) -> tuple[TrialRecord, TrialRecord] | None:
        """Recover an exact PLANNED/STARTED batch after Anchor-process loss."""

        anchors = self.read_anchors()
        committed = self._committed_anchors()
        if not anchors or anchors != committed:
            return None
        records = TrialJournal(self.journal_path).read_records()
        base_count = anchors[-1].trial_record_count
        suffix = records[base_count:]
        if not suffix:
            return None
        if (
            len(suffix) != 2
            or suffix[0].state is not TrialState.PLANNED
            or suffix[1].state is not TrialState.STARTED
            or suffix[0].trial_id != suffix[1].trial_id
            or suffix[0].trial_id != definition.trial_id
            or self._record_definition(suffix[0]) != definition
            or self._record_definition(suffix[1]) != definition
            or suffix[0].started_at_utc != suffix[1].started_at_utc
            or suffix[0].recorded_at_utc != suffix[1].recorded_at_utc
        ):
            raise ResearchError(
                FailureCode.JOURNAL_DURABILITY_FAILURE,
                "unanchored Trial start is not one exact authorized PLANNED/STARTED batch",
            )
        self._recover_unanchored_journal_records(
            expected_suffix=(suffix[0], suffix[1]),
            operation=f"TRIAL_STARTED:{definition.trial_id}",
            at_utc=suffix[0].recorded_at_utc,
        )
        return suffix[0], suffix[1]

    def recover_unanchored_trial_terminal(
        self,
        trial_id: str,
        *,
        state: TrialState,
        at_utc: datetime,
        result_ref: str,
        reason: str,
        result_exposed: bool,
    ) -> TrialRecord | None:
        """Recover one exact terminal record after its Anchor append was lost."""

        anchors = self.read_anchors()
        committed = self._committed_anchors()
        if not anchors or anchors != committed:
            return None
        records = TrialJournal(self.journal_path).read_records()
        base_count = anchors[-1].trial_record_count
        suffix = records[base_count:]
        if not suffix:
            return None
        prior = [item for item in records[:base_count] if item.trial_id == trial_id]
        if (
            not prior
            or prior[-1].state is not TrialState.STARTED
            or not records[:base_count]
            or records[base_count - 1] != prior[-1]
        ):
            raise ResearchError(
                FailureCode.JOURNAL_DURABILITY_FAILURE,
                "terminal recovery has no anchored STARTED predecessor",
            )
        expected = TrialRecord.create(
            definition=self._record_definition(prior[-1]),
            journal_sequence=base_count + 1,
            previous_entry_sha256=prior[-1].journal_entry_sha256,
            state=state,
            started_at_utc=prior[0].started_at_utc,
            recorded_at_utc=at_utc,
            finished_at_utc=at_utc,
            result_ref=result_ref,
            failure_or_block_reason=reason,
            result_exposed=result_exposed,
        )
        if not self._same_record_bytes(tuple(suffix), (expected,)):
            raise ResearchError(
                FailureCode.JOURNAL_DURABILITY_FAILURE,
                "unanchored terminal record differs from the authorized retry",
            )
        self._recover_unanchored_journal_records(
            expected_suffix=(expected,),
            operation=f"TRIAL_TERMINAL:{trial_id}:{state.value}",
            at_utc=at_utc,
        )
        return expected


class AuthoritativeResearchHistory:
    """Only public mutation boundary for trial and Holdout history."""

    def __init__(self, anchors: HistoryAnchorStore) -> None:
        self.anchors = anchors
        self.journal = TrialJournal(anchors.journal_path)
        self.holdout = HoldoutLockStore(anchors.holdout_path)
        self.mutation_lock_path = anchors.anchor_path.parent / f".{anchors.anchor_path.name}.lock"

    def reconcile(self) -> HistoryAnchor:
        return self.anchors.reconcile()

    def start_trial(
        self,
        definition: TrialDefinition,
        *,
        at_utc: datetime,
    ) -> tuple[TrialRecord, TrialRecord]:
        with _history_file_lock(self.mutation_lock_path):
            recovered = self.anchors.recover_unanchored_trial_start(definition)
            if recovered is not None:
                return recovered
            self.anchors.reconcile_committed()
            result = self.journal.start(definition, at_utc=at_utc)
            self.anchors.anchor_mutation(
                operation=f"TRIAL_STARTED:{definition.trial_id}",
                at_utc=at_utc,
            )
            return result

    def finish_trial(
        self,
        trial_id: str,
        *,
        state: TrialState,
        at_utc: datetime,
        result_ref: str,
        reason: str,
        result_exposed: bool,
    ) -> TrialRecord:
        with _history_file_lock(self.mutation_lock_path):
            recovered = self.anchors.recover_unanchored_trial_terminal(
                trial_id,
                state=state,
                at_utc=at_utc,
                result_ref=result_ref,
                reason=reason,
                result_exposed=result_exposed,
            )
            if recovered is not None:
                return recovered
            self.anchors.reconcile_committed()
            return self._finish_reconciled(
                trial_id,
                state=state,
                at_utc=at_utc,
                result_ref=result_ref,
                reason=reason,
                result_exposed=result_exposed,
            )

    def _finish_reconciled(
        self,
        trial_id: str,
        *,
        state: TrialState,
        at_utc: datetime,
        result_ref: str,
        reason: str,
        result_exposed: bool,
    ) -> TrialRecord:
        record = self.journal.finish(
            trial_id,
            state=state,
            at_utc=at_utc,
            result_ref=result_ref,
            reason=reason,
            result_exposed=result_exposed,
        )
        self.anchors.anchor_mutation(operation=f"TRIAL_TERMINAL:{trial_id}:{state.value}", at_utc=at_utc)
        return record

    def recover_started_trial(
        self,
        trial_id: str,
        *,
        state: TrialState,
        at_utc: datetime,
        result_ref: str,
        reason: str,
        result_exposed: bool,
    ) -> TrialRecord:
        """Terminalize a reconciled fsynced-but-uncommitted crash extension."""

        with _history_file_lock(self.mutation_lock_path):
            self.reconcile()
            return self._finish_reconciled(
                trial_id,
                state=state,
                at_utc=at_utc,
                result_ref=result_ref,
                reason=reason,
                result_exposed=result_exposed,
            )

    def consume_holdout(
        self,
        exposure: ResultExposure,
        *,
        exposure_resolver: Any,
        at_utc: datetime,
    ) -> HoldoutEntry:
        with _history_file_lock(self.mutation_lock_path):
            self.anchors.reconcile_committed()
            mapping = exposure_resolver.require_fresh(exposure, history=self)
            entry = self.holdout.consume(
                exposure,
                journal=self.journal,
                exposure_resolver=mapping,
            )
            self.anchors.anchor_mutation(
                operation=f"HOLDOUT_CONSUMED:{entry.entry_id}",
                at_utc=at_utc,
            )
            return entry


__all__ = ["AuthoritativeResearchHistory", "HistoryAnchor", "HistoryAnchorStore"]
