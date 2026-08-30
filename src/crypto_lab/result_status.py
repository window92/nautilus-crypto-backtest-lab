"""Strict additive status registry for immutable historical Run evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from crypto_lab.hashing import canonical_sha256


DEFAULT_RESULT_STATUS_REFS = (
    "evidence/audit/comprehensive-remediation-001/historical-result-status.json",
    "evidence/audit/comprehensive-remediation-001/runtime-proof-supersession-status.json",
)


class HistoricalRunStatus(StrEnum):
    REVOKED = "REVOKED"


class FinancialResultStatus(StrEnum):
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True)
class HistoricalResultRecord:
    path: str
    market_profile: str
    historical_run_status: HistoricalRunStatus
    financial_result_status: FinancialResultStatus
    finding_ids: tuple[str, ...]
    current_checker_outcome: str
    current_failure_codes: tuple[str, ...]
    historical_bytes_preserved: bool
    evidence_hashes: dict[str, str]


@dataclass(frozen=True)
class HistoricalResultRegistry:
    source_commit: str
    records: tuple[HistoricalResultRecord, ...]
    final_holdout_authorized: bool
    profitability_claim_authorized: bool

    def for_path(self, relative_path: str) -> HistoricalResultRecord | None:
        matches = [record for record in self.records if record.path == relative_path]
        if len(matches) > 1:
            raise ValueError(f"duplicate historical result status path: {relative_path}")
        return None if not matches else matches[0]


def load_historical_result_registry(path: Path) -> HistoricalResultRegistry:
    """Parse the additive registry without mutating any historical evidence."""

    value = json.loads(Path(path).read_text(encoding="utf-8"))
    required_root = {
        "schema",
        "audit_id",
        "audited_baseline_commit",
        "source_commit",
        "recorded_at_utc",
        "historical_policy",
        "final_holdout_authorized",
        "profitability_claim_authorized",
        "record_count",
        "records",
        "records_identity",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required_root
        or value.get("schema") != "audit-historical-result-status-v1"
        or value.get("audit_id") != "COMPREHENSIVE_AUDIT_REMEDIATION_001"
        or value.get("audited_baseline_commit")
        != "890b9d41cc05ff091f41c82409d196c91b86d452"
        or not isinstance(value.get("historical_policy"), str)
        or not value["historical_policy"]
    ):
        raise ValueError("historical result registry schema is invalid")
    if value.get("final_holdout_authorized") is not False:
        raise ValueError("historical result registry must not authorize Final Holdout")
    if value.get("profitability_claim_authorized") is not False:
        raise ValueError("historical result registry must not authorize profitability claims")
    source_commit = value.get("source_commit")
    raw_records = value.get("records")
    try:
        recorded = datetime.fromisoformat(str(value.get("recorded_at_utc")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("historical result registry timestamp is invalid") from exc
    if (
        not isinstance(source_commit, str)
        or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit)
        or not isinstance(raw_records, list)
        or recorded.tzinfo is None
        or recorded.utcoffset() != UTC.utcoffset(recorded)
        or value.get("record_count") != len(raw_records)
        or value.get("records_identity") != canonical_sha256(raw_records)
    ):
        raise ValueError("historical result registry identity is invalid")
    records: list[HistoricalResultRecord] = []
    seen: set[str] = set()
    for raw in raw_records:
        if not isinstance(raw, dict) or set(raw) != {
            "path",
            "market_profile",
            "historical_run_status",
            "financial_result_status",
            "finding_ids",
            "current_checker_outcome",
            "current_failure_codes",
            "historical_bytes_preserved",
            "evidence_hashes",
        }:
            raise ValueError("historical result record must be an object")
        relative = raw.get("path")
        candidate = Path(str(relative))
        if (
            not isinstance(relative, str)
            or candidate.is_absolute()
            or ".." in candidate.parts
            or not candidate.parts
            or candidate.parts[0] != "runs"
            or relative in seen
        ):
            raise ValueError("historical result path is unsafe or duplicated")
        seen.add(relative)
        finding_ids = raw.get("finding_ids")
        failure_codes = raw.get("current_failure_codes")
        evidence_hashes = raw.get("evidence_hashes")
        if (
            not isinstance(finding_ids, list)
            or not finding_ids
            or not all(isinstance(item, str) and item.startswith("F-00") for item in finding_ids)
            or not isinstance(failure_codes, list)
            or not all(isinstance(item, str) and item for item in failure_codes)
            or not isinstance(evidence_hashes, dict)
            or set(evidence_hashes) != {"checker.json", "status.json", "evidence_manifest.json"}
            or not all(
                isinstance(item, str)
                and len(item) == 64
                and all(character in "0123456789abcdef" for character in item)
                for item in evidence_hashes.values()
            )
        ):
            raise ValueError("historical result record evidence is invalid")
        records.append(
            HistoricalResultRecord(
                path=relative,
                market_profile=str(raw.get("market_profile")),
                historical_run_status=HistoricalRunStatus(raw.get("historical_run_status")),
                financial_result_status=FinancialResultStatus(raw.get("financial_result_status")),
                finding_ids=tuple(finding_ids),
                current_checker_outcome=str(raw.get("current_checker_outcome")),
                current_failure_codes=tuple(failure_codes),
                historical_bytes_preserved=raw.get("historical_bytes_preserved") is True,
                evidence_hashes={str(key): str(item) for key, item in evidence_hashes.items()},
            ),
        )
    if not records or not all(record.historical_bytes_preserved for record in records):
        raise ValueError("historical result registry must preserve every evidence directory")
    return HistoricalResultRegistry(
        source_commit=source_commit,
        records=tuple(records),
        final_holdout_authorized=False,
        profitability_claim_authorized=False,
    )


def revoked_result_for_directory(
    run_directory: Path,
    *,
    repository_root: Path,
    registry_path: Path | None = None,
) -> HistoricalResultRecord | None:
    root = Path(repository_root).resolve(strict=True)
    relative = Path(run_directory).resolve(strict=True).relative_to(root).as_posix()
    registry_files = (
        (Path(registry_path),)
        if registry_path is not None
        else tuple(root / reference for reference in DEFAULT_RESULT_STATUS_REFS)
    )
    matches = [
        record
        for registry_file in registry_files
        if registry_file.is_file()
        for record in (load_historical_result_registry(registry_file).for_path(relative),)
        if record is not None
    ]
    if len(matches) > 1:
        raise ValueError(f"historical result appears in multiple status registries: {relative}")
    return None if not matches else matches[0]


__all__ = [
    "FinancialResultStatus",
    "DEFAULT_RESULT_STATUS_REFS",
    "HistoricalResultRecord",
    "HistoricalResultRegistry",
    "HistoricalRunStatus",
    "load_historical_result_registry",
    "revoked_result_for_directory",
]
