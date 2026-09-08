"""Acyclic Official evidence manifest and root-seal contract.

The component validator runs before this module.  A component result proves
only local causal/financial invariants.  The root seal subsequently proves the
complete immutable directory inventory and binds the component result, status,
configuration, DatasetRelease, Runtime, and SourceRevision together.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from crypto_lab.config import MarketProfile
from crypto_lab.data import FULL_RAW_INVENTORY_NORMALIZER_VERSION
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.status import FailureCode


class OfficialSealOutcome(StrEnum):
    OFFICIAL_SEAL_PASS = "OFFICIAL_SEAL_PASS"
    OFFICIAL_SEAL_FAIL = "OFFICIAL_SEAL_FAIL"
    OFFICIAL_SEAL_BLOCKED = "OFFICIAL_SEAL_BLOCKED"


@dataclass(frozen=True)
class OfficialSealReport:
    outcome: OfficialSealOutcome
    failure_codes: tuple[str, ...]
    checks: tuple[dict[str, Any], ...]

    def to_builtins(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "failure_codes": list(self.failure_codes),
            "checks": list(self.checks),
        }


ROOT_FILES = frozenset(
    {
        "evidence_manifest.json",
        "status.json",
        "official_seal.json",
    },
)

COMMON_OFFICIAL_LEAVES = frozenset(
    {
        "account.csv",
        "component_validation.json",
        "dataset_release.json",
        "fills.csv",
        "instrument_metadata.json",
        "lab_run_config.json",
        "lab_run_config.sha256",
        "native_completed_trades.json",
        "native_fills.jsonl",
        "native_portfolio_snapshots.jsonl",
        "native_statistics.json",
        "nautilus_result.json",
        "orders.csv",
        "positions.csv",
        "qualification_authority.json",
        "runtime.lock.json",
        "runtime_identity.json",
        "source_revision.json",
        "strategy_identity.json",
        "strategy_identity.sha256",
        "strategy_spec.json",
    },
)

PERPETUAL_ONLY_LEAVES = frozenset({"funding.csv", "funding_source.json"})
RESEARCH_ONLY_LEAVES = frozenset({"dataset_rebuild_validation.json"})
CANONICALLY_EMPTY_ALLOWED = frozenset({"native_fills.jsonl"})

MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "run_id",
        "entries",
        "inventory_content_sha256",
        "root_files_excluded",
        "manifest_self_excluded",
    },
)
STATUS_FIELDS = frozenset(
    {
        "schema",
        "run_id",
        "state",
        "failure_codes",
        "component_validation_outcome",
        "component_validation_sha256",
        "evidence_manifest_sha256",
        "official_publication_state",
        "started_run_retained",
    },
)
SEAL_FIELDS = frozenset(
    {
        "schema",
        "run_id",
        "manifest_sha256",
        "manifest_inventory_content_sha256",
        "status_sha256",
        "component_validation_sha256",
        "config_sha256",
        "dataset_release_id",
        "runtime_identity_sha256",
        "runtime_payload_sha256",
        "source_revision_sha256",
        "source_git_commit",
        "source_git_tree",
        "status_state",
        "component_validation_outcome",
        "root_dependency_graph",
        "verifier_outcome_persisted",
        "seal_identity",
    },
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value


def _regular_child(run_dir: Path, name: str) -> Path:
    if (
        not name
        or Path(name).name != name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise ValueError("unsafe evidence path")
    path = run_dir / name
    if path.is_symlink() or not path.is_file() or path.resolve(strict=True).parent != run_dir:
        raise ValueError(f"unsafe or missing evidence file: {name}")
    return path


def _leaf_role(name: str) -> str:
    if name == "component_validation.json":
        return "COMPONENT_VALIDATION"
    if name in {
        "lab_run_config.json",
        "lab_run_config.sha256",
        "runtime.lock.json",
        "runtime_identity.json",
        "source_revision.json",
        "dataset_release.json",
        "strategy_spec.json",
        "strategy_identity.json",
        "strategy_identity.sha256",
        "qualification_authority.json",
        "instrument_metadata.json",
        "funding_source.json",
    }:
        return "IMMUTABLE_INPUT_OR_AUTHORITY"
    return "NATIVE_OR_DERIVED_RUN_EVIDENCE"


def build_evidence_manifest(run_dir: Path, *, run_id: str) -> dict[str, Any]:
    """Build the leaf manifest before status and seal exist."""

    run_dir = Path(run_dir).resolve(strict=True)
    children = tuple(run_dir.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in children):
        raise ValueError("Run evidence contains a symlink or non-file entry")
    unexpected_roots = ROOT_FILES & {path.name for path in children}
    if unexpected_roots:
        raise ValueError(
            "root files already exist before manifest creation: "
            + ",".join(sorted(unexpected_roots)),
        )
    config = _json(_regular_child(run_dir, "lab_run_config.json"))
    dataset = _json(_regular_child(run_dir, "dataset_release.json"))
    required, _forbidden = _profile_required_leaves(config, dataset)
    actual_leaves = {path.name for path in children}
    if actual_leaves != required:
        raise ValueError(
            "pre-seal leaf inventory is not the closed profile contract: "
            f"missing={sorted(required-actual_leaves)}, extra={sorted(actual_leaves-required)}",
        )
    entries = [
        {
            "path": path.name,
            "sha256": sha256_file(path),
            "byte_size": path.stat().st_size,
            "content_role": _leaf_role(path.name),
        }
        for path in sorted(children, key=lambda item: item.name)
    ]
    return {
        "schema": "run-evidence-manifest-v2",
        "run_id": run_id,
        "entries": entries,
        "inventory_content_sha256": canonical_sha256(entries),
        "root_files_excluded": sorted(ROOT_FILES),
        "manifest_self_excluded": True,
    }


def build_official_status(
    *,
    run_id: str,
    state: str,
    failure_codes: tuple[str, ...] | list[str],
    component_outcome: str,
    component_validation_sha256: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    codes = tuple(dict.fromkeys(str(code) for code in failure_codes))
    # Persisted codes are a closed vocabulary at the final boundary.
    for code in codes:
        FailureCode(code)
    if component_outcome not in {
        "COMPONENT_CHECK_PASS",
        "COMPONENT_CHECK_FAIL",
        "COMPONENT_CHECK_BLOCKED",
    }:
        raise ValueError("unknown component-validation outcome")
    if state not in {"COMPLETED", "FAILED", "BLOCKED", "ABORTED"}:
        raise ValueError("unknown Run state")
    root_eligible = state == "COMPLETED" and component_outcome == "COMPONENT_CHECK_PASS"
    return {
        "schema": "run-status-v2",
        "run_id": run_id,
        "state": state,
        "failure_codes": list(codes),
        "component_validation_outcome": component_outcome,
        "component_validation_sha256": component_validation_sha256,
        "evidence_manifest_sha256": manifest_sha256,
        "official_publication_state": (
            "ROOT_ATTESTATION_READY"
            if root_eligible
            else "INELIGIBLE_COMPONENT_RESULT_RETAINED"
        ),
        "started_run_retained": True,
    }


def build_official_seal(
    run_dir: Path,
    *,
    run_id: str,
) -> dict[str, Any]:
    """Build a root attestation; PASS is produced only by the verifier.

    Persisting an ``OFFICIAL_SEAL_PASS`` claim before verifying the final
    directory would merely move the old false-PASS cycle.  The attestation is
    deterministic and binds all roots; ``verify_official_seal`` is the only
    authority which emits an OfficialSealOutcome.
    """

    run_dir = Path(run_dir).resolve(strict=True)
    manifest = _json(_regular_child(run_dir, "evidence_manifest.json"))
    status = _json(_regular_child(run_dir, "status.json"))
    component_path = _regular_child(run_dir, "component_validation.json")
    config = _json(_regular_child(run_dir, "lab_run_config.json"))
    source = _json(_regular_child(run_dir, "source_revision.json"))
    dataset = _json(_regular_child(run_dir, "dataset_release.json"))
    runtime = _json(_regular_child(run_dir, "runtime_identity.json"))
    material = {
        "schema": "official-run-root-attestation-v1",
        "run_id": run_id,
        "manifest_sha256": sha256_file(run_dir / "evidence_manifest.json"),
        "manifest_inventory_content_sha256": manifest.get("inventory_content_sha256"),
        "status_sha256": sha256_file(run_dir / "status.json"),
        "component_validation_sha256": sha256_file(component_path),
        "config_sha256": _regular_child(
            run_dir,
            "lab_run_config.sha256",
        ).read_text(encoding="utf-8").strip(),
        "dataset_release_id": dataset.get("dataset_release_id"),
        "runtime_identity_sha256": sha256_file(run_dir / "runtime_identity.json"),
        "runtime_payload_sha256": runtime.get("installed_payload_sha256"),
        "source_revision_sha256": sha256_file(run_dir / "source_revision.json"),
        "source_git_commit": source.get("git_commit"),
        "source_git_tree": source.get("git_tree"),
        "status_state": status.get("state"),
        "component_validation_outcome": status.get("component_validation_outcome"),
        "root_dependency_graph": (
            "LEAF_FILES->COMPONENT_VALIDATION->MANIFEST->STATUS->OFFICIAL_SEAL"
        ),
        "verifier_outcome_persisted": False,
    }
    return {**material, "seal_identity": canonical_sha256(material)}


def _profile_required_leaves(
    config: dict[str, Any],
    dataset: dict[str, Any],
) -> tuple[set[str], set[str]]:
    profile = MarketProfile(str(config["market_profile"]))
    required = set(COMMON_OFFICIAL_LEAVES)
    forbidden: set[str] = set()
    if profile is MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING:
        required.update(PERPETUAL_ONLY_LEAVES)
    else:
        forbidden.update(PERPETUAL_ONLY_LEAVES)
    if dataset.get("normalizer_version") == FULL_RAW_INVENTORY_NORMALIZER_VERSION:
        required.update(RESEARCH_ONLY_LEAVES)
    else:
        forbidden.update(RESEARCH_ONLY_LEAVES)
    return required, forbidden


def verify_official_seal(
    run_dir: Path,
    *,
    repository_root: Path,
    source_revision_current_head_required: bool = True,
) -> OfficialSealReport:
    """Independently verify exact final inventory and all root bindings.

    The component validator is deliberately not injectable.  An Official
    outcome is an authority boundary, so every public invocation must execute
    the repository's fixed checker over the final bytes.  Tests which replace
    that checker do so only through process-local monkeypatching; callers
    cannot supply an alternative PASS oracle through this API.
    """

    from crypto_lab.git_identity import require_repository_root

    root = require_repository_root(repository_root)
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    try:
        supplied_run_dir = Path(run_dir)
        if supplied_run_dir.is_symlink():
            raise ValueError("Run evidence root must not be a symlink")
        run_dir = supplied_run_dir.resolve(strict=True)
        children = tuple(run_dir.iterdir())
        regular = all(
            not path.is_symlink()
            and path.is_file()
            and path.resolve(strict=True).parent == run_dir
            for path in children
        )
    except Exception as exc:
        return OfficialSealReport(
            OfficialSealOutcome.OFFICIAL_SEAL_BLOCKED,
            (
                FailureCode.OFFICIAL_SEAL_FAILURE.value,
                FailureCode.EVIDENCE_INCOMPLETE.value,
            ),
            ({"name": "root_directory", "pass": False, "detail": str(exc)},),
        )
    checks.append({"name": "root_regular_files_only", "pass": regular})
    if not regular:
        failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)

    names = {path.name for path in children}
    missing_roots = sorted(ROOT_FILES - names)
    checks.append(
        {"name": "root_file_inventory", "pass": not missing_roots, "missing": missing_roots},
    )
    if missing_roots:
        failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)
        return OfficialSealReport(
            OfficialSealOutcome.OFFICIAL_SEAL_BLOCKED,
            tuple(
                dict.fromkeys(
                    [FailureCode.OFFICIAL_SEAL_FAILURE.value, *failures],
                ),
            ),
            tuple(checks),
        )

    try:
        manifest_path = _regular_child(run_dir, "evidence_manifest.json")
        status_path = _regular_child(run_dir, "status.json")
        seal_path = _regular_child(run_dir, "official_seal.json")
        manifest = _json(manifest_path)
        status = _json(status_path)
        seal = _json(seal_path)
        config = _json(_regular_child(run_dir, "lab_run_config.json"))
        dataset = _json(_regular_child(run_dir, "dataset_release.json"))
    except Exception as exc:
        checks.append({"name": "root_json", "pass": False, "detail": str(exc)})
        return OfficialSealReport(
            OfficialSealOutcome.OFFICIAL_SEAL_BLOCKED,
            (
                FailureCode.OFFICIAL_SEAL_FAILURE.value,
                FailureCode.EVIDENCE_INCOMPLETE.value,
            ),
            tuple(checks),
        )

    entries = manifest.get("entries")
    manifest_shape_ok = bool(
        set(manifest) == MANIFEST_FIELDS
        and manifest.get("schema") == "run-evidence-manifest-v2"
        and manifest.get("run_id") == config.get("run_id")
        and manifest.get("root_files_excluded") == sorted(ROOT_FILES)
        and manifest.get("manifest_self_excluded") is True
        and isinstance(entries, list)
    )
    checks.append({"name": "manifest_schema", "pass": manifest_shape_ok})
    if not manifest_shape_ok:
        failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)
        entries = []

    declared: set[str] = set()
    entries_valid = True
    for entry in entries:
        try:
            if not isinstance(entry, dict) or set(entry) != {
                "path",
                "sha256",
                "byte_size",
                "content_role",
            }:
                raise ValueError("invalid entry shape")
            name = str(entry["path"])
            if name in ROOT_FILES or name in declared:
                raise ValueError("duplicate/root manifest entry")
            path = _regular_child(run_dir, name)
            if (
                entry["content_role"] != _leaf_role(name)
                or entry["sha256"] != sha256_file(path)
                or type(entry["byte_size"]) is not int
                or entry["byte_size"] != path.stat().st_size
                or (
                    path.stat().st_size <= 0
                    and name not in CANONICALLY_EMPTY_ALLOWED
                )
            ):
                raise ValueError("entry content mismatch")
            declared.add(name)
        except Exception:
            entries_valid = False
    actual_leaves = names - ROOT_FILES
    if declared != actual_leaves:
        entries_valid = False
    if canonical_sha256(entries) != manifest.get("inventory_content_sha256"):
        entries_valid = False
    checks.append(
        {
            "name": "exact_leaf_inventory",
            "pass": entries_valid,
            "declared_count": len(declared),
            "actual_count": len(actual_leaves),
        },
    )
    if not entries_valid:
        failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)

    try:
        required, forbidden = _profile_required_leaves(config, dataset)
    except Exception:
        required, forbidden = set(COMMON_OFFICIAL_LEAVES), set()
        failures.append(FailureCode.CONFIG_INVALID.value)
    profile_files_ok = actual_leaves == required and not (forbidden & actual_leaves)
    checks.append(
        {
            "name": "profile_file_contract",
            "pass": profile_files_ok,
            "missing": sorted(required - actual_leaves),
            "extra": sorted(actual_leaves - required),
            "forbidden_present": sorted(forbidden & actual_leaves),
            "spot_funding_contract": "NOT_APPLICABLE_ABSENT",
            "perpetual_funding_contract": "REQUIRED_CANONICAL_CSV",
        },
    )
    if not profile_files_ok:
        failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)

    status_shape_ok = bool(
        set(status) == STATUS_FIELDS
        and status.get("schema") == "run-status-v2"
        and status.get("started_run_retained") is True
    )
    checks.append({"name": "status_schema", "pass": status_shape_ok})
    if not status_shape_ok:
        failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)

    try:
        component_path = _regular_child(run_dir, "component_validation.json")
        component = _json(component_path)
        component_ok = bool(
            status_shape_ok
            and component.get("outcome") == "COMPONENT_CHECK_PASS"
            and component.get("failure_codes") == []
            and component.get("mutated_run_evidence") is False
            and status.get("schema") == "run-status-v2"
            and status.get("run_id") == config.get("run_id")
            and status.get("state") == "COMPLETED"
            and status.get("failure_codes") == []
            and status.get("component_validation_outcome") == "COMPONENT_CHECK_PASS"
            and status.get("component_validation_sha256") == sha256_file(component_path)
            and status.get("evidence_manifest_sha256") == sha256_file(manifest_path)
            and status.get("official_publication_state") == "ROOT_ATTESTATION_READY"
        )
    except Exception:
        component_ok = False
    checks.append({"name": "component_and_status_binding", "pass": component_ok})
    if not component_ok:
        failures.append(FailureCode.CHECKER_FAILURE.value)

    # A persisted component result is evidence, not authority.  Re-run the
    # read-only validator over final bytes and require byte-semantic equality.
    try:
        from crypto_lab.checker import check_evidence_directory

        regenerated = check_evidence_directory(
            run_dir,
            repository_root=root,
            official_source_required=True,
            source_revision_current_head_required=(
                source_revision_current_head_required
            ),
        ).to_builtins()
        component_revalidation_ok = regenerated == component
    except Exception:
        component_revalidation_ok = False
    checks.append(
        {
            "name": "component_validation_reexecuted",
            "pass": component_revalidation_ok,
        },
    )
    if not component_revalidation_ok:
        failures.append(FailureCode.CHECKER_FAILURE.value)

    try:
        material = dict(seal)
        declared_identity = material.pop("seal_identity")
        expected = build_official_seal(run_dir, run_id=str(config["run_id"]))
        seal_binding_ok = bool(
            set(seal) == SEAL_FIELDS
            and seal.get("schema") == "official-run-root-attestation-v1"
            and declared_identity == canonical_sha256(material)
            and expected == seal
            and seal.get("verifier_outcome_persisted") is False
        )
    except Exception:
        seal_binding_ok = False
    checks.append({"name": "root_seal_binding", "pass": seal_binding_ok})
    if not seal_binding_ok:
        failures.append(FailureCode.EVIDENCE_INCOMPLETE.value)

    unique_failures = tuple(
        dict.fromkeys(
            ([FailureCode.OFFICIAL_SEAL_FAILURE.value] if failures else [])
            + failures,
        ),
    )
    return OfficialSealReport(
        (
            OfficialSealOutcome.OFFICIAL_SEAL_PASS
            if not unique_failures
            else OfficialSealOutcome.OFFICIAL_SEAL_FAIL
        ),
        unique_failures,
        tuple(checks),
    )


def write_canonical_json(path: Path, value: Any) -> None:
    """Write one new root artifact; callers own atomic directory publication."""

    path.write_bytes(canonical_json_bytes(value) + b"\n")


__all__ = [
    "OfficialSealOutcome",
    "OfficialSealReport",
    "build_evidence_manifest",
    "build_official_seal",
    "build_official_status",
    "verify_official_seal",
    "write_canonical_json",
]
