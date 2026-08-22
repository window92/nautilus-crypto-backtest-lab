"""Official claim/report trust boundary resolving identities into evidence facts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crypto_lab.checker import CheckerOutcome
from crypto_lab.checker import check_evidence_directory
from crypto_lab.config import LabRunConfig
from crypto_lab.config import NOT_APPLICABLE
from crypto_lab.config import SourceRevision
from crypto_lab.config import StrictModel
from crypto_lab.config import _require_sha256
from crypto_lab.data import DatasetRelease
from crypto_lab.diagnostics import DiagnosticResolution
from crypto_lab.diagnostics import derive_performance_diagnostics
from crypto_lab.diagnostics import reconcile_diagnostic_resolution
from crypto_lab.exposure import AuthoritativeExposureResolver
from crypto_lab.git_identity import verify_source_revision
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.history import AuthoritativeResearchHistory
from crypto_lab.history import HistoryAnchorStore
from crypto_lab.m3 import MechanicalIntegrity
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.paths import validate_safe_component
from crypto_lab.reporting import ReportInput
from crypto_lab.reporting import ReportOutput
from crypto_lab.reporting import PerformanceDiagnostics
from crypto_lab.reporting import _build_report_from_resolved_evidence
from crypto_lab.reporting import _write_resolved_report
from crypto_lab.research import ClaimEvaluation
from crypto_lab.research import ClaimEvaluationInput
from crypto_lab.research import ClaimScope
from crypto_lab.research import InstrumentScope
from crypto_lab.research import PartitionRole
from crypto_lab.research import ResearchError
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import ResultExposure
from crypto_lab.research import TERMINAL_TRIAL_STATES
from crypto_lab.research import TrialRecord
from crypto_lab.research import TrialState
from crypto_lab.research import _evaluate_claim_from_resolved_evidence
from crypto_lab.strategies import RegisteredStrategyIdentity
from crypto_lab.strategies import StrategySpec
from crypto_lab.strategies import resolve_registered_strategy_identity


def _candidate_schedule_complete(
    protocol: ResearchProtocol,
    records: tuple[TrialRecord, ...],
) -> bool:
    observed = tuple(record.candidate_id for record in records)
    expected = tuple(
        candidate.candidate_id
        for candidate in protocol.ordered_candidates[: protocol.search_budget]
    )
    return observed == expected


class OfficialEvidenceLocator(StrictModel):
    """Only caller-controlled Official claim/report input: immutable identities."""

    schema_version: int
    protocol_id: str
    selected_trial_id: str
    expected_history_anchor_sha256: str
    report_purpose: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ResearchError("EVIDENCE_INCOMPLETE", "Official locator schema must be 1")
        _require_sha256(self.protocol_id, "official_locator.protocol_id")
        _require_sha256(
            self.expected_history_anchor_sha256,
            "official_locator.expected_history_anchor_sha256",
        )
        validate_safe_component(self.selected_trial_id, field="selected_trial_id")
        if self.report_purpose not in {
            "OFFICIAL_RESEARCH_REPORT",
            "QUALIFICATION_WORKFLOW_FIXTURE",
        }:
            raise ResearchError("EVIDENCE_INCOMPLETE", "unknown Official report purpose")


@dataclass(frozen=True)
class ResolvedOfficialEvidence:
    locator: OfficialEvidenceLocator
    protocol: ResearchProtocol
    all_family_trial_records: tuple[TrialRecord, ...]
    selected_trial: TrialRecord
    selected_run_dir: Path
    diagnostic_resolution: DiagnosticResolution
    claim_evaluation: ClaimEvaluation
    report_input: ReportInput


class OfficialEvidenceResolver:
    """Read and recompute every eligibility fact from authoritative evidence."""

    def __init__(self, *, repository_root: Path, require_remote_tip: bool = True) -> None:
        self.repository_root = Path(repository_root).resolve(strict=True)
        self.history = AuthoritativeResearchHistory(
            HistoryAnchorStore(
                repository_root=self.repository_root,
                journal_path=self.repository_root / "research/trials.jsonl",
                holdout_path=self.repository_root / "research/holdout_lock.json",
                anchor_path=self.repository_root / "research/history_anchors.jsonl",
                require_remote_tip=require_remote_tip,
            ),
        )

    def _contained(self, path: Path) -> Path:
        resolved = Path(path).resolve(strict=True)
        try:
            resolved.relative_to(self.repository_root)
        except ValueError as exc:
            raise ResearchError("EVIDENCE_INCOMPLETE", "evidence locator escapes repository") from exc
        return resolved

    def _run_dir(self, record: TrialRecord) -> Path:
        located = self._contained(self.repository_root / record.result_ref)
        return located if located.is_dir() else located.parent

    def _protocol(self, protocol_id: str) -> tuple[ResearchProtocol, Path]:
        path = self._contained(
            self.repository_root / "research/protocols" / f"{protocol_id}.json",
        )
        protocol = ResearchProtocol.from_json_bytes(path.read_bytes())
        if protocol.protocol_id != protocol_id:
            raise ResearchError("RESEARCH_PROTOCOL_INVALID", "protocol locator identity mismatch")
        return protocol, path

    @staticmethod
    def _partition_interval(protocol: ResearchProtocol, role: PartitionRole) -> Any:
        return {
            PartitionRole.DEVELOPMENT: protocol.development_interval,
            PartitionRole.VALIDATION: protocol.validation_interval,
            PartitionRole.OOS: protocol.oos_interval,
            PartitionRole.FINAL_HOLDOUT: protocol.final_holdout_interval,
        }[role]

    @staticmethod
    def _json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ResearchError("EVIDENCE_INCOMPLETE", f"{path.name} must be an object")
        return value

    def _verify_manifest(self, run_dir: Path, run_id: str) -> str:
        manifest_path = run_dir / "evidence_manifest.json"
        if manifest_path.is_symlink() or manifest_path.resolve(strict=True).parent != run_dir:
            raise ResearchError("EVIDENCE_INCOMPLETE", "Run manifest escapes its evidence directory")
        manifest = self._json(manifest_path)
        entries = manifest.get("entries")
        if (
            manifest.get("schema") != "run-evidence-manifest-v1"
            or manifest.get("run_id") != run_id
            or not isinstance(entries, list)
        ):
            raise ResearchError("EVIDENCE_INCOMPLETE", "invalid Run evidence manifest")
        declared: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "byte_size"}:
                raise ResearchError("EVIDENCE_INCOMPLETE", "invalid manifest entry")
            name = str(entry["path"])
            if (
                not name
                or name in {".", ".."}
                or Path(name).name != name
                or "/" in name
                or "\\" in name
                or any(ord(character) < 32 or ord(character) == 127 for character in name)
            ):
                raise ResearchError("EVIDENCE_INCOMPLETE", "unsafe manifest path")
            if name in declared:
                raise ResearchError("EVIDENCE_INCOMPLETE", "duplicate manifest path")
            declared.add(name)
            path = run_dir / name
            if (
                path.is_symlink()
                or not path.is_file()
                or path.resolve(strict=True).parent != run_dir
                or sha256_file(path) != entry["sha256"]
                or path.stat().st_size != entry["byte_size"]
            ):
                raise ResearchError("EVIDENCE_INCOMPLETE", f"manifest mismatch: {name}")
        children = tuple(run_dir.iterdir())
        if any(path.is_symlink() or not path.is_file() for path in children):
            raise ResearchError(
                "EVIDENCE_INCOMPLETE",
                "Run evidence contains a symlink or unmanifested non-file entry",
            )
        actual = {path.name for path in children}
        if declared != actual - {"evidence_manifest.json"}:
            raise ResearchError("EVIDENCE_INCOMPLETE", "Run manifest inventory is incomplete")
        if canonical_sha256(entries) != manifest.get("inventory_content_sha256"):
            raise ResearchError("EVIDENCE_INCOMPLETE", "Run manifest identity mismatch")
        return sha256_file(manifest_path)

    def _resolve_selected_run(
        self,
        record: TrialRecord,
        protocol: ResearchProtocol,
    ) -> tuple[Path, LabRunConfig, SourceRevision, RegisteredStrategyIdentity, str]:
        run_dir = self._run_dir(record)
        required = {
            "checker.json",
            "dataset_release.json",
            "evidence_manifest.json",
            "lab_run_config.json",
            "native_completed_trades.json",
            "source_revision.json",
            "status.json",
            "strategy_identity.json",
            "strategy_spec.json",
        }
        missing = sorted(name for name in required if not (run_dir / name).is_file())
        if missing:
            raise ResearchError("EVIDENCE_INCOMPLETE", "selected Run missing: " + ",".join(missing))
        config = LabRunConfig.from_json_bytes((run_dir / "lab_run_config.json").read_bytes())
        source = SourceRevision.from_json_bytes((run_dir / "source_revision.json").read_bytes())
        release = DatasetRelease.from_json_bytes((run_dir / "dataset_release.json").read_bytes())
        spec = StrategySpec.from_json_bytes((run_dir / "strategy_spec.json").read_bytes())
        identity = RegisteredStrategyIdentity.from_json_bytes(
            (run_dir / "strategy_identity.json").read_bytes(),
        )
        status = self._json(run_dir / "status.json")
        persisted_checker = self._json(run_dir / "checker.json")
        manifest_sha = self._verify_manifest(run_dir, record.run_id)
        if (
            config.run_id != record.run_id
            or config.config_sha256 != record.config_sha256
            or config.research_protocol_id != protocol.protocol_id
            or config.strategy_spec_id != record.strategy_spec_id
            or release.dataset_release_id != record.dataset_release_id
            or status.get("state") != record.state.value
            or status.get("checker_outcome") != persisted_checker.get("outcome")
        ):
            raise ResearchError("EVIDENCE_INCOMPLETE", "selected trial and Run evidence mismatch")
        verify_source_revision(
            source,
            repository=self.repository_root,
            require_current_head=False,
            require_clean=True,
        )
        if resolve_registered_strategy_identity(
            identity.registration_id,
            strategy_spec=spec,
            source_revision=source,
        ) != identity:
            raise ResearchError("EVIDENCE_INCOMPLETE", "registered strategy identity is forged")
        regenerated = check_evidence_directory(
            run_dir,
            repository_root=self.repository_root,
            official_source_required=True,
            source_revision_current_head_required=False,
        )
        if regenerated.to_builtins() != persisted_checker:
            raise ResearchError("EVIDENCE_INCOMPLETE", "persisted checker is stale or forged")
        return run_dir, config, source, identity, manifest_sha

    def _resolve_replay_evidence(
        self,
        *,
        record: TrialRecord,
        primary_run_dir: Path,
    ) -> Path:
        replay_path = self._contained(
            self.repository_root / "research/replays" / f"{record.trial_id}.json",
        )
        payload = self._json(replay_path)
        declared = payload.pop("replay_identity", None)
        if (
            declared != canonical_sha256(payload)
            or payload.get("schema") != "owner-deterministic-replay-v1"
            or payload.get("trial_id") != record.trial_id
            or payload.get("primary_run_ref") != record.result_ref
            or payload.get("result") != "PASS"
            or payload.get("fresh_processes") is not True
            or payload.get("read_only_checker_revalidated") is not True
        ):
            raise ResearchError("EVIDENCE_INCOMPLETE", "deterministic replay evidence is invalid")
        replay_dir = self._contained(self.repository_root / str(payload["replay_run_ref"]))
        if not replay_dir.is_dir():
            raise ResearchError("EVIDENCE_INCOMPLETE", "deterministic replay Run is absent")
        primary_result = self._json(primary_run_dir / "nautilus_result.json")
        replay_result = self._json(replay_dir / "nautilus_result.json")
        replay_status = self._json(replay_dir / "status.json")
        persisted_checker = self._json(replay_dir / "checker.json")
        regenerated = check_evidence_directory(
            replay_dir,
            repository_root=self.repository_root,
            official_source_required=True,
            source_revision_current_head_required=False,
        )
        self._verify_manifest(replay_dir, record.run_id)
        if (
            regenerated.to_builtins() != persisted_checker
            or replay_status.get("state") != "COMPLETED"
            or replay_status.get("checker_outcome") != "CHECK_PASS"
            or replay_result.get("config_sha256") != primary_result.get("config_sha256")
            or replay_result.get("semantic_digest") != primary_result.get("semantic_digest")
            or payload.get("primary_config_sha256") != primary_result.get("config_sha256")
            or payload.get("replay_config_sha256") != replay_result.get("config_sha256")
            or payload.get("primary_semantic_digest") != primary_result.get("semantic_digest")
            or payload.get("replay_semantic_digest") != replay_result.get("semantic_digest")
        ):
            raise ResearchError("EVIDENCE_INCOMPLETE", "deterministic replay no longer matches")
        return replay_path

    def resolve(self, locator: OfficialEvidenceLocator) -> ResolvedOfficialEvidence:
        latest_anchor = self.history.anchors.reconcile_committed()
        if latest_anchor.anchor_sha256 != locator.expected_history_anchor_sha256:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "stale authoritative history head")
        protocol, protocol_path = self._protocol(locator.protocol_id)
        records = self.history.journal.read_records()
        family_records = tuple(
            record for record in records if record.research_family_id == protocol.research_family_id
        )
        started_ids = tuple(
            dict.fromkeys(
                record.trial_id for record in family_records if record.state is TrialState.STARTED
            ),
        )
        latest = {record.trial_id: record for record in family_records}
        if locator.selected_trial_id not in started_ids:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "selected trial was not started")
        selected = latest[locator.selected_trial_id]
        if selected.state not in TERMINAL_TRIAL_STATES or selected.result_ref == "NOT_APPLICABLE":
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "selected trial is not terminal evidence")
        complete_history = bool(started_ids) and all(
            latest[trial_id].state in TERMINAL_TRIAL_STATES for trial_id in started_ids
        )
        if not complete_history:
            raise ResearchError("TRIAL_HISTORY_INCOMPLETE", "family contains a non-terminal trial")
        resolved_runs: dict[str, tuple[Path, LabRunConfig, SourceRevision, RegisteredStrategyIdentity, str]] = {}
        for trial_id in started_ids:
            record = latest[trial_id]
            if record.result_ref == NOT_APPLICABLE:
                continue
            record_protocol, _record_protocol_path = self._protocol(record.protocol_id)
            resolved_runs[trial_id] = self._resolve_selected_run(record, record_protocol)
        if locator.selected_trial_id not in resolved_runs:
            raise ResearchError("EVIDENCE_INCOMPLETE", "selected trial has no resolved Run evidence")
        run_dir, config, source, strategy_identity, manifest_sha = resolved_runs[selected.trial_id]
        replay_evidence_path: Path | None = None
        if (
            strategy_identity.strategy_spec.get("parameters", {}).get("strategy_family")
            == "BTCUSDT_DAILY_PRICE_VS_SMA20_TREND"
        ):
            replay_evidence_path = self._resolve_replay_evidence(
                record=selected,
                primary_run_dir=run_dir,
            )
        registry_path = self.repository_root / "evidence/m3/m3-acceptance-001/qualified-profile-registry.json"
        registry = QualifiedProfileRegistry.from_json_bytes(registry_path.read_bytes())
        qualified = next(
            (record for record in registry.records if record.profile_id is config.market_profile),
            None,
        )
        if qualified is None or qualified.checker_result != "CHECK_PASS" or qualified.replay_result != "PASS":
            raise ResearchError("DOWNSTREAM_CONTRACT_FAILURE", "Market Profile is not qualified")
        diagnostic_path = self.repository_root / "research/diagnostics" / f"{config.run_id}.json"
        diagnostic = reconcile_diagnostic_resolution(
            path=diagnostic_path,
            run_dir=run_dir,
            protocol=protocol,
            benchmark_directory=self.repository_root / "research/benchmarks",
        )
        performance: PerformanceDiagnostics | None = None
        performance_path = self.repository_root / "research/performance" / f"{config.run_id}.json"
        if diagnostic.performance_diagnostics_status == "COMPLETE":
            if not performance_path.is_file():
                raise ResearchError("EVIDENCE_INCOMPLETE", "selected performance evidence is missing")
            performance = PerformanceDiagnostics.from_json_bytes(performance_path.read_bytes())
            if performance != derive_performance_diagnostics(run_dir=run_dir, protocol=protocol):
                raise ResearchError("EVIDENCE_INCOMPLETE", "selected performance evidence is stale")
        holdout = self.history.holdout.read()
        matching_holdout = next(
            (
                entry
                for entry in holdout.entries
                if entry.exposure.trial_id == selected.trial_id
                and entry.exposure.market_profile is selected.market_profile
                and entry.exposure.instrument_id == selected.instrument_id
                and entry.exposure.scored_interval == selected.scored_interval
            ),
            None,
        )
        holdout_valid = False
        if selected.partition_role is PartitionRole.FINAL_HOLDOUT and matching_holdout is not None:
            expected_exposure = ResultExposure(
                trial_id=selected.trial_id,
                market_profile=selected.market_profile,
                instrument_id=selected.instrument_id,
                scored_interval=selected.scored_interval,
                research_family_id=selected.research_family_id,
                hypothesis_lineage=(selected.hypothesis_id,),
                strategy_lineage=(selected.strategy_spec_id,),
                dataset_release_id=selected.dataset_release_id,
                first_exposure_at_utc=selected.finished_at_utc,
                exposure_type=selected.partition_role.value,
                evidence_reference=selected.result_ref,
                source_branch=source.branch_ref,
                source_commit=source.git_commit,
                seed=selected.seed,
                result_bearing=True,
            )
            try:
                AuthoritativeExposureResolver(
                    repository_root=self.repository_root,
                ).reconcile_consumed(
                    expected_exposure,
                    history=self.history,
                    entry_id=matching_holdout.entry_id,
                )
            except ResearchError:
                holdout_valid = False
            else:
                holdout_valid = True
        persisted_checker = self._json(run_dir / "checker.json")
        status = self._json(run_dir / "status.json")
        mechanical = (
            MechanicalIntegrity.PASS
            if status.get("state") == "COMPLETED"
            and persisted_checker.get("outcome") == CheckerOutcome.CHECK_PASS.value
            else MechanicalIntegrity.FAIL
        )
        exact_protocol_trials = tuple(
            latest[trial_id]
            for trial_id in started_ids
            if latest[trial_id].protocol_id == protocol.protocol_id
        )
        protocol_frozen = bool(exact_protocol_trials) and all(
            record.protocol_id == protocol.protocol_id
            and protocol.frozen_at_utc <= record.started_at_utc
            for record in (latest[trial_id] for trial_id in started_ids)
        )
        candidates = {candidate.candidate_id: candidate for candidate in protocol.ordered_candidates}
        candidate_schedule_complete = _candidate_schedule_complete(
            protocol,
            exact_protocol_trials,
        )
        partitions_valid = candidate_schedule_complete
        for record in exact_protocol_trials:
            candidate = candidates.get(record.candidate_id)
            resolved = resolved_runs.get(record.trial_id)
            if candidate is None or resolved is None:
                partitions_valid = False
                continue
            trial_config = resolved[1]
            expected_interval = self._partition_interval(protocol, record.partition_role)
            partitions_valid = partitions_valid and bool(
                record.scored_interval == expected_interval
                and record.candidate_parameters_sha256
                == canonical_sha256(dict(candidate.parameter_values))
                and record.strategy_spec_id == candidate.strategy_spec_id
                and record.dataset_release_id in protocol.dataset_release_ids
                and record.seed in protocol.random_seeds
                and trial_config.config_sha256 == record.config_sha256
                and trial_config.research_protocol_id == protocol.protocol_id
                and trial_config.scoring_start == expected_interval.start_inclusive
                and trial_config.scoring_end_exclusive == expected_interval.end_exclusive
            )
        underlying_runs_valid = bool(exact_protocol_trials) and all(
            record.state is TrialState.COMPLETED
            and record.trial_id in resolved_runs
            and self._json(resolved_runs[record.trial_id][0] / "checker.json").get("outcome")
            == CheckerOutcome.CHECK_PASS.value
            for record in exact_protocol_trials
        )
        required_instruments = set(protocol.instrument_ids)
        selected_instrument_present = selected.instrument_id in required_instruments
        scope_supported = (
            protocol.instrument_scope is InstrumentScope.SINGLE_INSTRUMENT
            and protocol.intended_claim_scope is ClaimScope.INSTRUMENT_ONLY
            and selected_instrument_present
        ) or (
            protocol.instrument_scope is InstrumentScope.FROZEN_INSTRUMENT_SET
            and protocol.intended_claim_scope is ClaimScope.FROZEN_SET_ONLY
        )
        sample_by_instrument = {
            instrument: (
                diagnostic.sample_adequacy.value
                if instrument == selected.instrument_id
                else "LOW_CONFIDENCE"
            )
            for instrument in protocol.instrument_ids
        }
        mc_by_instrument = {
            instrument: (
                diagnostic.monte_carlo_status.value
                if instrument == selected.instrument_id
                else "MC_LOW_CONFIDENCE"
            )
            for instrument in protocol.instrument_ids
        }
        diagnostics_by_instrument = {
            instrument: bool(
                instrument == selected.instrument_id
                and diagnostic.performance_diagnostics_status == "COMPLETE"
            )
            for instrument in protocol.instrument_ids
        }
        claim = _evaluate_claim_from_resolved_evidence(
            ClaimEvaluationInput(
                protocol=protocol,
                mechanical_integrity=mechanical,
                checker_result=str(persisted_checker.get("outcome")),
                underlying_official_runs_valid=(
                    underlying_runs_valid
                    and mechanical is MechanicalIntegrity.PASS
                    and selected_instrument_present
                ),
                qualification_only=(
                    strategy_identity.qualification_fixture_only
                    or not strategy_identity.profitability_claim_eligible
                ),
                protocol_frozen_before_results=protocol_frozen,
                supporting_trial_protocol_ids=tuple(
                    latest[trial_id].protocol_id for trial_id in started_ids
                ),
                complete_trial_history=complete_history,
                partitions_valid=partitions_valid,
                holdout_valid=holdout_valid,
                benchmark_valid=diagnostic.benchmark_status == "COMPLETE",
                multiple_testing_valid=(
                    len(protocol.ordered_candidates) == 1
                    or protocol.multiple_testing_treatment not in {"NOT_APPLICABLE", "UNDECLARED"}
                ),
                sample_adequacy_by_instrument=sample_by_instrument,
                monte_carlo_by_instrument=mc_by_instrument,
                diagnostics_complete_by_instrument=diagnostics_by_instrument,
                claim_scope_supported=scope_supported,
                universe_evidence_valid=(
                    protocol.instrument_scope is not InstrumentScope.POINT_IN_TIME_UNIVERSE
                ),
                unresolved_material_ambiguities=(),
                synthetic_contract_fixture=(
                    locator.report_purpose == "QUALIFICATION_WORKFLOW_FIXTURE"
                ),
            ),
        )
        source_hashes = {
            "history_anchors.jsonl": sha256_file(self.history.anchors.anchor_path),
            "holdout_lock.json": sha256_file(self.history.anchors.holdout_path),
            "m3_qualified_profile_registry": sha256_file(registry_path),
            "protocol": sha256_file(protocol_path),
            "selected_diagnostics": sha256_file(diagnostic_path),
            "selected_run_manifest": manifest_sha,
            "trials.jsonl": sha256_file(self.history.anchors.journal_path),
        }
        if performance is not None:
            source_hashes["selected_performance"] = sha256_file(performance_path)
        if replay_evidence_path is not None:
            source_hashes["selected_deterministic_replay"] = sha256_file(
                replay_evidence_path,
            )
        report_input = ReportInput(
            schema_version=1,
            protocol=protocol,
            claim_evaluation=claim,
            trial_records=family_records,
            included_trial_ids=started_ids,
            selected_trial_id=selected.trial_id,
            performance_diagnostics=(() if performance is None else (performance,)),
            monte_carlo_results=(),
            sample_adequacy_by_instrument=sample_by_instrument,
            holdout_state={
                "state": "CONSUMED_AND_RECONCILED" if holdout_valid else "NOT_USED_BY_SELECTED_TRIAL",
                "history_sha256": holdout.history_sha256,
                "entry_count": len(holdout.entries),
            },
            benchmark_result={
                "benchmark_id": protocol.required_benchmark.benchmark_id,
                "state": diagnostic.benchmark_status,
            },
            multiple_testing_treatment=protocol.multiple_testing_treatment,
            qualification_limitations=tuple(
                dict.fromkeys((*diagnostic.limitations, *claim.limitations)),
            ),
            open_terminal_positions={
                selected.instrument_id: str(
                    self._json(run_dir / "nautilus_result.json").get(
                        "terminal_position_open",
                        "UNKNOWN",
                    ),
                ),
            },
            source_evidence_hashes=source_hashes,
            source_revision={
                "repository": source.repository,
                "branch_ref": source.branch_ref,
                "git_commit": source.git_commit,
                "git_tree": source.git_tree,
            },
            report_purpose=locator.report_purpose,
        )
        return ResolvedOfficialEvidence(
            locator=locator,
            protocol=protocol,
            all_family_trial_records=family_records,
            selected_trial=selected,
            selected_run_dir=run_dir,
            diagnostic_resolution=diagnostic,
            claim_evaluation=claim,
            report_input=report_input,
        )

    def build_report(self, locator: OfficialEvidenceLocator) -> ReportOutput:
        return _build_report_from_resolved_evidence(self.resolve(locator).report_input)

    def write_report(
        self,
        locator: OfficialEvidenceLocator,
        *,
        json_path: Path,
        markdown_path: Path,
    ) -> ReportOutput:
        output = self.build_report(locator)
        _write_resolved_report(output, json_path=json_path, markdown_path=markdown_path)
        return output


__all__ = [
    "OfficialEvidenceLocator",
    "OfficialEvidenceResolver",
    "ResolvedOfficialEvidence",
]
