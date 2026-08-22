"""Authoritative exposure resolution for Final Holdout designation."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from crypto_lab.config import LabRunConfig
from crypto_lab.config import MarketProfile
from crypto_lab.config import SourceRevision
from crypto_lab.data import DatasetRelease
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.history import AuthoritativeResearchHistory
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.research import ResearchError
from crypto_lab.research import ResultExposure
from crypto_lab.research import TERMINAL_TRIAL_STATES
from crypto_lab.research import UtcInterval


@dataclass(frozen=True)
class ResolvedExposure:
    authority: str
    authority_id: str
    market_profile: MarketProfile
    instrument_id: str
    exposed_interval: UtcInterval
    dataset_lineage: tuple[str, ...]
    evidence_reference: str
    evidence_sha256: str

    def overlaps(self, candidate: ResultExposure) -> bool:
        return (
            self.market_profile is candidate.market_profile
            and self.instrument_id == candidate.instrument_id
            and self.exposed_interval.overlaps(candidate.scored_interval)
        )


class AuthoritativeExposureResolver:
    """Resolve M3, Dataset, Trial Journal, and Holdout exposure without labels."""

    def __init__(
        self,
        *,
        repository_root: Path,
        m3_registry_path: Path | None = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve(strict=True)
        self.m3_root = self.repository_root / "evidence/m3/m3-acceptance-001"
        self.m3_registry_path = (
            self.m3_root / "qualified-profile-registry.json"
            if m3_registry_path is None
            else self._contained(m3_registry_path)
        )

    def _contained(self, path: Path) -> Path:
        resolved = Path(path).resolve(strict=True)
        try:
            resolved.relative_to(self.repository_root)
        except ValueError as exc:
            raise ResearchError(
                "HOLDOUT_HISTORY_VIOLATION",
                f"exposure evidence escapes repository: {resolved}",
            ) from exc
        return resolved

    def _require_committed_authority_unchanged(self, path: Path) -> None:
        """Reject a clean descendant commit that replaced historical M3 authority."""

        current_path = self._contained(path)
        current = current_path.read_bytes()
        relative = current_path.relative_to(self.repository_root).as_posix()
        commits = subprocess.run(
            ["git", "rev-list", "HEAD", "--", relative],
            cwd=self.repository_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        observed = 0
        for commit in commits:
            process = subprocess.run(
                ["git", "show", f"{commit}:{relative}"],
                cwd=self.repository_root,
                check=False,
                capture_output=True,
            )
            if process.returncode != 0:
                continue
            observed += 1
            if process.stdout != current:
                raise ResearchError(
                    "HOLDOUT_HISTORY_VIOLATION",
                    "committed M3 qualification authority was replaced",
                )
        if observed == 0:
            raise ResearchError(
                "HOLDOUT_HISTORY_VIOLATION",
                "M3 qualification authority is not committed in SourceRevision history",
            )

    def _validate_m3_manifest(self) -> frozenset[str]:
        manifest_path = self.m3_root / "qualification-manifest.json"
        self._require_committed_authority_unchanged(manifest_path)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ResearchError("HOLDOUT_HISTORY_VIOLATION", "invalid M3 manifest") from exc
        entries = manifest.get("entries")
        if (
            manifest.get("schema") != "m3-acceptance-manifest-v1"
            or manifest.get("manifest_self_excluded") is not True
            or not isinstance(entries, list)
            or canonical_sha256(entries) != manifest.get("content_sha256")
        ):
            raise ResearchError("HOLDOUT_HISTORY_VIOLATION", "M3 manifest identity mismatch")
        declared: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "byte_size"}:
                raise ResearchError("HOLDOUT_HISTORY_VIOLATION", "invalid M3 manifest entry")
            relative = str(entry["path"])
            candidate = Path(relative)
            if (
                not relative
                or candidate.is_absolute()
                or ".." in candidate.parts
                or "\\" in relative
                or any(ord(character) < 32 or ord(character) == 127 for character in relative)
                or relative in declared
            ):
                raise ResearchError("HOLDOUT_HISTORY_VIOLATION", "unsafe M3 manifest path")
            declared.add(relative)
            lexical = self.m3_root / candidate
            cursor = self.m3_root
            for component in candidate.parts:
                cursor = cursor / component
                if cursor.is_symlink():
                    raise ResearchError(
                        "HOLDOUT_HISTORY_VIOLATION",
                        "M3 manifest path contains a symlink",
                    )
            resolved = self._contained(lexical)
            if (
                not resolved.is_file()
                or resolved.stat().st_size != entry["byte_size"]
                or sha256_file(resolved) != entry["sha256"]
            ):
                raise ResearchError(
                    "HOLDOUT_HISTORY_VIOLATION",
                    f"M3 manifest mismatch: {relative}",
                )
        # The qualification manifest is an immutable epoch manifest.  Later M3
        # acceptance files are intentionally additive and therefore are not an
        # error here; only manifest-declared bytes may be consumed below.
        return frozenset(declared)

    def _m3_exposures(self) -> tuple[ResolvedExposure, ...]:
        declared = self._validate_m3_manifest()
        try:
            registry_relative = self.m3_registry_path.relative_to(self.m3_root).as_posix()
        except ValueError as exc:
            raise ResearchError(
                "HOLDOUT_HISTORY_VIOLATION",
                "M3 registry is outside the immutable qualification-manifest scope",
            ) from exc
        if registry_relative not in declared:
            raise ResearchError(
                "HOLDOUT_HISTORY_VIOLATION",
                "M3 registry is not declared by the immutable qualification manifest",
            )
        registry = QualifiedProfileRegistry.from_json_bytes(self.m3_registry_path.read_bytes())
        resolved: list[ResolvedExposure] = []
        seen: set[tuple[MarketProfile, str, object, object]] = set()
        for profile in registry.records:
            if profile.checker_result != "CHECK_PASS" or profile.replay_result != "PASS":
                raise ResearchError(
                    "HOLDOUT_HISTORY_VIOLATION",
                    f"M3 profile authority is not accepted: {profile.profile_id.value}",
                )
            for reference in profile.evidence_references:
                run_dir = self._contained(self.m3_root / reference)
                consumed = {
                    (Path(reference) / name).as_posix()
                    for name in (
                        "lab_run_config.json",
                        "dataset_release.json",
                        "status.json",
                        "checker.json",
                        "evidence_manifest.json",
                    )
                }
                if not consumed.issubset(declared):
                    raise ResearchError(
                        "HOLDOUT_HISTORY_VIOLATION",
                        f"M3 exposure evidence is outside the qualification manifest: {reference}",
                    )
                config = LabRunConfig.from_json_bytes((run_dir / "lab_run_config.json").read_bytes())
                release = DatasetRelease.from_json_bytes((run_dir / "dataset_release.json").read_bytes())
                status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
                checker = json.loads((run_dir / "checker.json").read_text(encoding="utf-8"))
                if (
                    config.run_id not in profile.accepted_run_ids
                    or config.market_profile is not profile.profile_id
                    or release.market_profile is not profile.profile_id
                    or release.dataset_release_id != profile.dataset_release_id
                    or config.dataset_release_id != release.dataset_release_id
                    or status.get("state") != "COMPLETED"
                    or checker.get("outcome") != "CHECK_PASS"
                ):
                    raise ResearchError(
                        "HOLDOUT_HISTORY_VIOLATION",
                        f"M3 exposure evidence does not reconcile: {reference}",
                    )
                interval = UtcInterval(
                    start_inclusive=release.normalized_time_range.start_inclusive,
                    end_exclusive=release.normalized_time_range.end_exclusive,
                )
                key = (
                    profile.profile_id,
                    release.instrument_id,
                    interval.start_inclusive,
                    interval.end_exclusive,
                )
                if key in seen:
                    continue
                seen.add(key)
                resolved.append(
                    ResolvedExposure(
                        authority="M3_QUALIFIED_PROFILE_AND_RUN_EVIDENCE",
                        authority_id=profile.qualified_profile_record_id,
                        market_profile=profile.profile_id,
                        instrument_id=release.instrument_id,
                        exposed_interval=interval,
                        dataset_lineage=(
                            profile.base_dataset_release_id,
                            profile.dataset_release_id,
                        ),
                        evidence_reference=reference,
                        evidence_sha256=sha256_file(run_dir / "evidence_manifest.json"),
                    ),
                )
        return tuple(resolved)

    def _journal_exposures(
        self,
        history: AuthoritativeResearchHistory,
    ) -> tuple[tuple[ResolvedExposure, ...], dict[str, ResultExposure]]:
        records = history.journal.read_records()
        latest = {record.trial_id: record for record in records}
        resolved: list[ResolvedExposure] = []
        mapping: dict[str, ResultExposure] = {}
        for trial_id, record in latest.items():
            if record.state not in TERMINAL_TRIAL_STATES or not record.result_exposed:
                continue
            result_path = self._contained(self.repository_root / record.result_ref)
            run_dir = result_path if result_path.is_dir() else result_path.parent
            required = (
                "lab_run_config.json",
                "source_revision.json",
                "dataset_release.json",
                "status.json",
                "checker.json",
            )
            if any(not (run_dir / name).is_file() for name in required):
                raise ResearchError(
                    "HOLDOUT_HISTORY_VIOLATION",
                    f"result-bearing trial {trial_id} has incomplete Run evidence",
                )
            config = LabRunConfig.from_json_bytes((run_dir / "lab_run_config.json").read_bytes())
            source = SourceRevision.from_json_bytes((run_dir / "source_revision.json").read_bytes())
            release = DatasetRelease.from_json_bytes((run_dir / "dataset_release.json").read_bytes())
            if (
                config.run_id != record.run_id
                or config.config_sha256 != record.config_sha256
                or config.strategy_spec_id != record.strategy_spec_id
                or release.dataset_release_id != record.dataset_release_id
                or config.market_profile is not record.market_profile
                or config.instrument_id != record.instrument_id
                or config.scoring_start != record.scored_interval.start_inclusive
                or config.scoring_end_exclusive != record.scored_interval.end_exclusive
            ):
                raise ResearchError(
                    "HOLDOUT_HISTORY_VIOLATION",
                    f"trial {trial_id} does not match resolved Run evidence",
                )
            exposure = ResultExposure(
                trial_id=trial_id,
                market_profile=config.market_profile,
                instrument_id=config.instrument_id,
                scored_interval=record.scored_interval,
                research_family_id=record.research_family_id,
                hypothesis_lineage=(record.hypothesis_id,),
                strategy_lineage=(record.strategy_spec_id,),
                dataset_release_id=release.dataset_release_id,
                first_exposure_at_utc=record.finished_at_utc,
                exposure_type=record.partition_role.value,
                evidence_reference=record.result_ref,
                source_branch=source.branch_ref,
                source_commit=source.git_commit,
                seed=record.seed,
                result_bearing=True,
            )
            # Results expose both their causal warmup observations and their
            # scored observations.  Expanding only the Holdout-freshness view
            # (not the Journal's scored partition) prevents a later protocol
            # from relabeling the warmup market history as fresh evidence.
            if (
                config.warmup_start < release.normalized_time_range.start_inclusive
                or config.scoring_end_exclusive > release.normalized_time_range.end_exclusive
            ):
                raise ResearchError(
                    "HOLDOUT_HISTORY_VIOLATION",
                    f"trial {trial_id} Run window escapes its Dataset Release",
                )
            full_run_interval = UtcInterval(
                start_inclusive=config.warmup_start,
                end_exclusive=config.scoring_end_exclusive,
            )
            mapping[trial_id] = exposure
            resolved.append(
                ResolvedExposure(
                    authority="AUTHORITATIVE_TRIAL_JOURNAL_AND_RUN_EVIDENCE",
                    authority_id=record.journal_entry_sha256,
                    market_profile=record.market_profile,
                    instrument_id=record.instrument_id,
                    exposed_interval=full_run_interval,
                    dataset_lineage=(record.dataset_release_id,),
                    evidence_reference=record.result_ref,
                    evidence_sha256=sha256_file(run_dir / "status.json"),
                ),
            )
        return tuple(resolved), mapping

    def require_fresh(
        self,
        candidate: ResultExposure,
        *,
        history: AuthoritativeResearchHistory | None,
    ) -> dict[str, ResultExposure]:
        return self._require_no_conflict(
            candidate,
            history=history,
            accepted_holdout_entry_id=None,
        )

    def reconcile_consumed(
        self,
        candidate: ResultExposure,
        *,
        history: AuthoritativeResearchHistory,
        entry_id: str,
    ) -> None:
        """Prove an anchored consumption did not overlap any earlier authority."""

        snapshot = history.holdout.read()
        if not any(
            entry.entry_id == entry_id and entry.exposure == candidate
            for entry in snapshot.entries
        ):
            raise ResearchError(
                "HOLDOUT_HISTORY_VIOLATION",
                "selected Holdout exposure is absent from authoritative history",
            )
        self._require_no_conflict(
            candidate,
            history=history,
            accepted_holdout_entry_id=entry_id,
        )

    def _require_no_conflict(
        self,
        candidate: ResultExposure,
        *,
        history: AuthoritativeResearchHistory | None,
        accepted_holdout_entry_id: str | None,
    ) -> dict[str, ResultExposure]:
        exposures = list(self._m3_exposures())
        mapping: dict[str, ResultExposure] = {}
        if history is not None:
            history.reconcile()
            journal_exposures, mapping = self._journal_exposures(history)
            exposures.extend(journal_exposures)
            for entry in history.holdout.read().entries:
                if entry.entry_id == accepted_holdout_entry_id:
                    continue
                exposures.append(
                    ResolvedExposure(
                        authority="AUTHORITATIVE_HOLDOUT_HISTORY",
                        authority_id=entry.entry_id,
                        market_profile=entry.exposure.market_profile,
                        instrument_id=entry.exposure.instrument_id,
                        exposed_interval=entry.exposure.scored_interval,
                        dataset_lineage=(entry.exposure.dataset_release_id,),
                        evidence_reference=entry.exposure.evidence_reference,
                        evidence_sha256=entry.entry_id,
                    ),
                )
        for exposure in exposures:
            if exposure.authority == "AUTHORITATIVE_TRIAL_JOURNAL_AND_RUN_EVIDENCE" and (
                candidate.trial_id in mapping
                and mapping[candidate.trial_id] == candidate
                and exposure.authority_id
                == next(
                    record.journal_entry_sha256
                    for record in history.journal.read_records()
                    if record.trial_id == candidate.trial_id
                    and record.state in TERMINAL_TRIAL_STATES
                )
            ):
                continue
            if exposure.overlaps(candidate):
                raise ResearchError(
                    "HOLDOUT_ALREADY_CONSUMED",
                    f"overlap with {exposure.authority}:{exposure.authority_id}",
                )
        return mapping


__all__ = ["AuthoritativeExposureResolver", "ResolvedExposure"]
