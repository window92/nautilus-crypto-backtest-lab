"""Fail-closed additive trust status for immutable historical Run evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from crypto_lab.hashing import canonical_json_bytes, canonical_sha256
from crypto_lab.status import canonicalize_evidence_failure_codes
from crypto_lab.status import validated_failure_codes


DEFAULT_RESULT_STATUS_REFS = (
    "evidence/audit/comprehensive-remediation-001/historical-result-status.json",
    "evidence/audit/comprehensive-remediation-001/runtime-proof-supersession-status.json",
    "evidence/audit/comprehensive-remediation-001/owner-child-entrypoint-supersession-status.json",
    "evidence/audit/adversarial-remediation-002/historical-result-status.json",
    (
        "evidence/audit/adversarial-remediation-002/"
        "runtime-authority-supersession-status.json"
    ),
)
RESULT_STATUS_V2_SCHEMA = "historical-result-status-registry-v2"
RESULT_STATUS_V3_SCHEMA = "historical-result-status-registry-v3"
RESULT_STATUS_V2_POLICY = (
    "HISTORICAL_BYTES_IMMUTABLE;NON_ACTIVE_RESULTS_INELIGIBLE;"
    "FINAL_HOLDOUT_AND_PROFITABILITY_CLAIMS_NOT_AUTHORIZED"
)
V2_EVIDENCE_FILES = ("checker.json", "evidence_manifest.json", "status.json")
V3_EVIDENCE_FILES = (
    "component_validation.json",
    "evidence_manifest.json",
    "official_seal.json",
    "runtime_identity.json",
    "source_revision.json",
    "status.json",
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_LOGICAL_RESULT = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_FINDING_ID = re.compile(r"R2-[0-9]{3}\Z")
_MARKET_PROFILES = frozenset(
    {
        "BINANCE_SPOT_CASH_LONG_ONLY",
        "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING",
    },
)
R2_RESULT_STATUS_AUTHORITY = "ADVERSARIAL_AUDIT_REMEDIATION_002"
R2_RUNTIME_SUPERSESSION_AUTHORITY = (
    "ADVERSARIAL_AUDIT_REMEDIATION_002_RUNTIME_SUPERSESSION"
)
R2_AUDITED_BASELINE_COMMIT = "b5c865c28b83526ffab38152e7e6821f39b77014"


class HistoricalRunStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"


class FinancialResultStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"


class HistoricalResultClass(StrEnum):
    CANDIDATE = "CANDIDATE"
    BENCHMARK = "BENCHMARK"
    LEGACY = "LEGACY"


class HistoricalCopyRole(StrEnum):
    PRIMARY = "PRIMARY"
    REPLAY = "REPLAY"
    LEGACY = "LEGACY"


class HistoricalStatusReason(StrEnum):
    WARMUP_SCORING_ELIGIBILITY_VIOLATION = "WARMUP_SCORING_ELIGIBILITY_VIOLATION"
    RESULT_CONTRACT_V2_SUPERSESSION = "RESULT_CONTRACT_V2_SUPERSESSION"
    RUNTIME_AUTHORITY_SUPERSESSION = "RUNTIME_AUTHORITY_SUPERSESSION"
    LEGACY_AUDIT_FINDING = "LEGACY_AUDIT_FINDING"


class ReplacementRequirement(StrEnum):
    WARMUP_SCORING_FIX_AND_V2_REBUILD = "WARMUP_SCORING_FIX_AND_V2_REBUILD"
    DATASET_RELEASE_CHECKER_RESULT_SCHEMA_V2_REBUILD = (
        "DATASET_RELEASE_CHECKER_RESULT_SCHEMA_V2_REBUILD"
    )
    FINAL_PRODUCT_RUNTIME_REBUILD = "FINAL_PRODUCT_RUNTIME_REBUILD"
    LEGACY_UNSPECIFIED = "LEGACY_UNSPECIFIED"


R2_EXPECTED_HISTORICAL_RESULTS: dict[str, dict[str, str]] = {
    "perpetual-benchmark": {
        "market_profile": "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING",
        "result_class": HistoricalResultClass.BENCHMARK.value,
        "primary_path": (
            "runs/comprehensive-audit-remediation-003-perpetual-benchmark-run-a0e2b2553ed4"
        ),
        "replay_path": (
            "runs/replays/comprehensive-audit-remediation-003-perpetual-benchmark-"
            "buy-and-hold-1x-development/comprehensive-audit-remediation-003-"
            "perpetual-benchmark-run-a0e2b2553ed4"
        ),
    },
    "perpetual-candidate-a": {
        "market_profile": "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING",
        "result_class": HistoricalResultClass.CANDIDATE.value,
        "primary_path": (
            "runs/comprehensive-audit-remediation-003-perpetual-candidate-a-run-5b7c5dba7f8b"
        ),
        "replay_path": (
            "runs/replays/comprehensive-audit-remediation-003-perpetual-candidate-a-development/"
            "comprehensive-audit-remediation-003-perpetual-candidate-a-run-5b7c5dba7f8b"
        ),
    },
    "perpetual-candidate-b": {
        "market_profile": "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING",
        "result_class": HistoricalResultClass.CANDIDATE.value,
        "primary_path": (
            "runs/comprehensive-audit-remediation-003-perpetual-candidate-b-run-85bb3192f559"
        ),
        "replay_path": (
            "runs/replays/comprehensive-audit-remediation-003-perpetual-candidate-b-development/"
            "comprehensive-audit-remediation-003-perpetual-candidate-b-run-85bb3192f559"
        ),
    },
    "spot-benchmark": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.BENCHMARK.value,
        "primary_path": "runs/comprehensive-audit-remediation-003-spot-benchmark-run-d3e25d52686e",
        "replay_path": (
            "runs/replays/comprehensive-audit-remediation-003-spot-benchmark-"
            "buy-and-hold-1x-development/comprehensive-audit-remediation-003-"
            "spot-benchmark-run-d3e25d52686e"
        ),
    },
    "spot-candidate-a": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.CANDIDATE.value,
        "primary_path": "runs/comprehensive-audit-remediation-003-spot-candidate-a-run-253086685e94",
        "replay_path": (
            "runs/replays/comprehensive-audit-remediation-003-spot-candidate-a-development/"
            "comprehensive-audit-remediation-003-spot-candidate-a-run-253086685e94"
        ),
    },
    "spot-candidate-b": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.CANDIDATE.value,
        "primary_path": "runs/comprehensive-audit-remediation-003-spot-candidate-b-run-736f07f7755e",
        "replay_path": (
            "runs/replays/comprehensive-audit-remediation-003-spot-candidate-b-development/"
            "comprehensive-audit-remediation-003-spot-candidate-b-run-736f07f7755e"
        ),
    },
}


R2_RUNTIME_SUPERSEDED_RESULTS: dict[str, dict[str, str]] = {
    "retry-002-spot-benchmark": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.BENCHMARK.value,
        "primary_path": (
            "runs/adversarial-remediation-002-retry-002-spot-benchmark-run-7da743fdaa06"
        ),
        "replay_path": (
            "runs/replays/adversarial-remediation-002-retry-002-spot-benchmark-"
            "buy-and-hold-1x-development/adversarial-remediation-002-retry-002-"
            "spot-benchmark-run-7da743fdaa06"
        ),
    },
    "retry-002-spot-candidate-a": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.CANDIDATE.value,
        "primary_path": (
            "runs/adversarial-remediation-002-retry-002-spot-candidate-a-run-e1cacf032f78"
        ),
        "replay_path": (
            "runs/replays/adversarial-remediation-002-retry-002-spot-candidate-a-"
            "development/adversarial-remediation-002-retry-002-spot-candidate-a-"
            "run-e1cacf032f78"
        ),
    },
    "retry-002-spot-candidate-b": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.CANDIDATE.value,
        "primary_path": (
            "runs/adversarial-remediation-002-retry-002-spot-candidate-b-run-9bbdbc35e204"
        ),
        "replay_path": (
            "runs/replays/adversarial-remediation-002-retry-002-spot-candidate-b-"
            "development/adversarial-remediation-002-retry-002-spot-candidate-b-"
            "run-9bbdbc35e204"
        ),
    },
    "retry-003-spot-benchmark": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.BENCHMARK.value,
        "primary_path": (
            "runs/adversarial-remediation-002-retry-003-spot-benchmark-run-f28ac747c930"
        ),
        "replay_path": (
            "runs/replays/adversarial-remediation-002-retry-003-spot-benchmark-"
            "buy-and-hold-1x-development/adversarial-remediation-002-retry-003-"
            "spot-benchmark-run-f28ac747c930"
        ),
    },
    "retry-004-spot-benchmark": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.BENCHMARK.value,
        "primary_path": (
            "runs/adversarial-remediation-002-retry-004-spot-benchmark-run-524a71ec1f23"
        ),
        "replay_path": (
            "runs/replays/adversarial-remediation-002-retry-004-spot-benchmark-"
            "buy-and-hold-1x-development/adversarial-remediation-002-retry-004-"
            "spot-benchmark-run-524a71ec1f23"
        ),
    },
    "retry-005-spot-benchmark": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.BENCHMARK.value,
        "primary_path": (
            "runs/adversarial-remediation-002-retry-005-spot-benchmark-run-2c31e21fea1f"
        ),
        "replay_path": (
            "runs/replays/adversarial-remediation-002-retry-005-spot-benchmark-"
            "buy-and-hold-1x-development/adversarial-remediation-002-retry-005-"
            "spot-benchmark-run-2c31e21fea1f"
        ),
    },
    "retry-005-spot-candidate-a": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.CANDIDATE.value,
        "primary_path": (
            "runs/adversarial-remediation-002-retry-005-spot-candidate-a-run-c14c350c3c6c"
        ),
        "replay_path": (
            "runs/replays/adversarial-remediation-002-retry-005-spot-candidate-a-"
            "development/adversarial-remediation-002-retry-005-spot-candidate-a-"
            "run-c14c350c3c6c"
        ),
    },
    "retry-005-spot-candidate-b": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.CANDIDATE.value,
        "primary_path": (
            "runs/adversarial-remediation-002-retry-005-spot-candidate-b-run-cdd40a577711"
        ),
        "replay_path": (
            "runs/replays/adversarial-remediation-002-retry-005-spot-candidate-b-"
            "development/adversarial-remediation-002-retry-005-spot-candidate-b-"
            "run-cdd40a577711"
        ),
    },
    "retry-005-perpetual-benchmark": {
        "market_profile": "BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING",
        "result_class": HistoricalResultClass.BENCHMARK.value,
        "primary_path": (
            "runs/adversarial-remediation-002-retry-005-perpetual-benchmark-run-"
            "2a0ab6ee5579"
        ),
        "replay_path": (
            "runs/replays/adversarial-remediation-002-retry-005-perpetual-benchmark-"
            "buy-and-hold-1x-development/adversarial-remediation-002-retry-005-"
            "perpetual-benchmark-run-2a0ab6ee5579"
        ),
    },
    "retry-006-spot-benchmark": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.BENCHMARK.value,
        "primary_path": (
            "runs/adversarial-remediation-002-retry-006-spot-benchmark-run-9602e7984645"
        ),
        "replay_path": (
            "runs/replays/adversarial-remediation-002-retry-006-spot-benchmark-"
            "buy-and-hold-1x-development/adversarial-remediation-002-retry-006-"
            "spot-benchmark-run-9602e7984645"
        ),
    },
    "retry-006-spot-candidate-a": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.CANDIDATE.value,
        "primary_path": (
            "runs/adversarial-remediation-002-retry-006-spot-candidate-a-run-1a928b3db2d3"
        ),
        "replay_path": (
            "runs/replays/adversarial-remediation-002-retry-006-spot-candidate-a-"
            "development/adversarial-remediation-002-retry-006-spot-candidate-a-"
            "run-1a928b3db2d3"
        ),
    },
    "retry-006-spot-candidate-b": {
        "market_profile": "BINANCE_SPOT_CASH_LONG_ONLY",
        "result_class": HistoricalResultClass.CANDIDATE.value,
        "primary_path": (
            "runs/adversarial-remediation-002-retry-006-spot-candidate-b-run-c5ea2b43962f"
        ),
        "replay_path": (
            "runs/replays/adversarial-remediation-002-retry-006-spot-candidate-b-"
            "development/adversarial-remediation-002-retry-006-spot-candidate-b-"
            "run-c5ea2b43962f"
        ),
    },
}


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
    logical_result_id: str = "LEGACY"
    result_class: HistoricalResultClass = HistoricalResultClass.LEGACY
    copy_role: HistoricalCopyRole = HistoricalCopyRole.LEGACY
    reason_code: HistoricalStatusReason = HistoricalStatusReason.LEGACY_AUDIT_FINDING
    replacement_requirement: ReplacementRequirement = ReplacementRequirement.LEGACY_UNSPECIFIED

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "current_failure_codes",
            validated_failure_codes(
                self.current_failure_codes,
                field="historical_result.current_failure_codes",
            ),
        )


@dataclass(frozen=True)
class HistoricalResultRegistry:
    source_commit: str
    records: tuple[HistoricalResultRecord, ...]
    final_holdout_authorized: bool
    profitability_claim_authorized: bool
    registry_schema: str = "audit-historical-result-status-v1"
    registry_identity: str = "NOT_APPLICABLE"
    authority_id: str = "COMPREHENSIVE_AUDIT_REMEDIATION_001"

    def for_path(self, relative_path: str) -> HistoricalResultRecord | None:
        matches = [record for record in self.records if record.path == relative_path]
        if len(matches) > 1:
            raise ValueError(f"duplicate historical result status path: {relative_path}")
        return None if not matches else matches[0]


@dataclass(frozen=True)
class ResultStatusResolution:
    relative_path: str
    historical_run_status: HistoricalRunStatus
    financial_result_status: FinancialResultStatus
    record: HistoricalResultRecord | None
    registry_path: str | None

    @property
    def is_active(self) -> bool:
        return (
            self.historical_run_status is HistoricalRunStatus.ACTIVE
            and self.financial_result_status is FinancialResultStatus.ACTIVE
            and self.record is None
        )


class ResultNotActiveError(ValueError):
    """Raised when a consumer attempts to use a revoked or superseded result."""

    def __init__(self, resolution: ResultStatusResolution) -> None:
        self.resolution = resolution
        reason = (
            resolution.record.reason_code.value
            if resolution.record is not None
            else "UNRESOLVED_NON_ACTIVE_STATUS"
        )
        super().__init__(
            f"result is not ACTIVE: {resolution.relative_path} "
            f"status={resolution.historical_run_status.value} reason={reason}",
        )


def _strict_json(payload: bytes) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate JSON field: {key}")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    try:
        return json.loads(
            payload,
            object_pairs_hook=pairs,
            parse_constant=invalid_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("historical result registry JSON is invalid") from exc


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _valid_git_sha(value: Any) -> bool:
    return isinstance(value, str) and _GIT_SHA.fullmatch(value) is not None


def _utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("historical result registry timestamp must be explicit UTC")
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("historical result registry timestamp is invalid") from exc
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise ValueError("historical result registry timestamp must use UTC")
    return result


def _safe_result_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("historical result path is unsafe")
    candidate = Path(value)
    if (
        candidate.is_absolute()
        or len(candidate.parts) < 2
        or candidate.parts[0] != "runs"
        or ".." in candidate.parts
        or candidate.as_posix() != value
    ):
        raise ValueError("historical result path is unsafe")
    return value


def _evidence_hashes(
    value: Any,
    *,
    expected_files: tuple[str, ...] = V2_EVIDENCE_FILES,
) -> dict[str, str]:
    if (
        not isinstance(value, dict)
        or tuple(sorted(value)) != expected_files
        or not all(_valid_sha256(item) for item in value.values())
    ):
        raise ValueError("historical result evidence hashes are invalid")
    return {name: str(value[name]) for name in expected_files}


def _load_v1_registry(value: dict[str, Any]) -> HistoricalResultRegistry:
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
        set(value) != required_root
        or value.get("schema") != "audit-historical-result-status-v1"
        or value.get("audit_id") != "COMPREHENSIVE_AUDIT_REMEDIATION_001"
        or value.get("audited_baseline_commit")
        != "890b9d41cc05ff091f41c82409d196c91b86d452"
        or not isinstance(value.get("historical_policy"), str)
        or not value["historical_policy"]
        or value.get("final_holdout_authorized") is not False
        or value.get("profitability_claim_authorized") is not False
        or not _valid_git_sha(value.get("source_commit"))
    ):
        raise ValueError("historical result registry schema is invalid")
    _utc_timestamp(value.get("recorded_at_utc"))
    raw_records = value.get("records")
    if (
        not isinstance(raw_records, list)
        or value.get("record_count") != len(raw_records)
        or value.get("records_identity") != canonical_sha256(raw_records)
    ):
        raise ValueError("historical result registry identity is invalid")
    records: list[HistoricalResultRecord] = []
    seen: set[str] = set()
    expected_fields = {
        "path",
        "market_profile",
        "historical_run_status",
        "financial_result_status",
        "finding_ids",
        "current_checker_outcome",
        "current_failure_codes",
        "historical_bytes_preserved",
        "evidence_hashes",
    }
    for raw in raw_records:
        if not isinstance(raw, dict) or set(raw) != expected_fields:
            raise ValueError("historical result record must be an object")
        relative = _safe_result_path(raw.get("path"))
        if relative in seen:
            raise ValueError("historical result path is duplicated")
        seen.add(relative)
        finding_ids = raw.get("finding_ids")
        failure_codes = raw.get("current_failure_codes")
        if (
            raw.get("historical_run_status") != HistoricalRunStatus.REVOKED.value
            or raw.get("financial_result_status") != FinancialResultStatus.INVALIDATED.value
            or not isinstance(finding_ids, list)
            or not finding_ids
            or not all(isinstance(item, str) and item.startswith("F-00") for item in finding_ids)
            or not isinstance(failure_codes, list)
            or not all(isinstance(item, str) and item for item in failure_codes)
            or raw.get("historical_bytes_preserved") is not True
        ):
            raise ValueError("historical result record status is invalid")
        evidence_hashes = _evidence_hashes(raw.get("evidence_hashes"))
        canonical_failure_codes = canonicalize_evidence_failure_codes(failure_codes)
        records.append(
            HistoricalResultRecord(
                path=relative,
                market_profile=str(raw.get("market_profile")),
                historical_run_status=HistoricalRunStatus.REVOKED,
                financial_result_status=FinancialResultStatus.INVALIDATED,
                finding_ids=tuple(finding_ids),
                current_checker_outcome=str(raw.get("current_checker_outcome")),
                current_failure_codes=canonical_failure_codes,
                historical_bytes_preserved=True,
                evidence_hashes=evidence_hashes,
            ),
        )
    if not records:
        raise ValueError("historical result registry must contain records")
    return HistoricalResultRegistry(
        source_commit=str(value["source_commit"]),
        records=tuple(records),
        final_holdout_authorized=False,
        profitability_claim_authorized=False,
    )


def _v2_record(raw: Any) -> HistoricalResultRecord:
    expected_fields = {
        "copy_role",
        "evidence_hashes",
        "financial_result_status",
        "finding_ids",
        "historical_bytes_preserved",
        "historical_run_status",
        "logical_result_id",
        "market_profile",
        "path",
        "reason_code",
        "replacement_requirement",
        "result_class",
    }
    if not isinstance(raw, dict) or set(raw) != expected_fields:
        raise ValueError("v2 historical result record schema is invalid")
    relative = _safe_result_path(raw.get("path"))
    logical_result_id = raw.get("logical_result_id")
    market_profile = raw.get("market_profile")
    finding_ids = raw.get("finding_ids")
    if (
        not isinstance(logical_result_id, str)
        or _LOGICAL_RESULT.fullmatch(logical_result_id) is None
        or market_profile not in _MARKET_PROFILES
        or not isinstance(finding_ids, list)
        or not finding_ids
        or tuple(sorted(set(finding_ids))) != tuple(finding_ids)
        or not all(isinstance(item, str) and _FINDING_ID.fullmatch(item) for item in finding_ids)
        or raw.get("historical_bytes_preserved") is not True
    ):
        raise ValueError("v2 historical result record material is invalid")
    try:
        result_class = HistoricalResultClass(raw.get("result_class"))
        copy_role = HistoricalCopyRole(raw.get("copy_role"))
        run_status = HistoricalRunStatus(raw.get("historical_run_status"))
        financial_status = FinancialResultStatus(raw.get("financial_result_status"))
        reason = HistoricalStatusReason(raw.get("reason_code"))
        replacement = ReplacementRequirement(raw.get("replacement_requirement"))
    except ValueError as exc:
        raise ValueError("v2 historical result record vocabulary is invalid") from exc
    if result_class is HistoricalResultClass.CANDIDATE:
        expected = (
            HistoricalRunStatus.REVOKED,
            FinancialResultStatus.INVALIDATED,
            HistoricalStatusReason.WARMUP_SCORING_ELIGIBILITY_VIOLATION,
            ReplacementRequirement.WARMUP_SCORING_FIX_AND_V2_REBUILD,
        )
        if (
            (run_status, financial_status, reason, replacement) != expected
            or finding_ids != ["R2-001", "R2-002"]
        ):
            raise ValueError("Candidate warm-up invalidation contract is inconsistent")
    elif result_class is HistoricalResultClass.BENCHMARK:
        expected = (
            HistoricalRunStatus.SUPERSEDED,
            FinancialResultStatus.SUPERSEDED,
            HistoricalStatusReason.RESULT_CONTRACT_V2_SUPERSESSION,
            ReplacementRequirement.DATASET_RELEASE_CHECKER_RESULT_SCHEMA_V2_REBUILD,
        )
        if (
            (run_status, financial_status, reason, replacement) != expected
            or finding_ids != ["R2-002"]
        ):
            raise ValueError("Benchmark v2 supersession contract is inconsistent")
    else:
        raise ValueError("v2 registries forbid LEGACY result records")
    if copy_role not in {HistoricalCopyRole.PRIMARY, HistoricalCopyRole.REPLAY}:
        raise ValueError("v2 result copy role must be PRIMARY or REPLAY")
    return HistoricalResultRecord(
        path=relative,
        market_profile=str(market_profile),
        historical_run_status=run_status,
        financial_result_status=financial_status,
        finding_ids=tuple(finding_ids),
        current_checker_outcome="NOT_APPLICABLE",
        current_failure_codes=(),
        historical_bytes_preserved=True,
        evidence_hashes=_evidence_hashes(raw.get("evidence_hashes")),
        logical_result_id=logical_result_id,
        result_class=result_class,
        copy_role=copy_role,
        reason_code=reason,
        replacement_requirement=replacement,
    )


def _v3_record(raw: Any) -> HistoricalResultRecord:
    expected_fields = {
        "copy_role",
        "evidence_hashes",
        "financial_result_status",
        "finding_ids",
        "historical_bytes_preserved",
        "historical_run_status",
        "logical_result_id",
        "market_profile",
        "path",
        "reason_code",
        "replacement_requirement",
        "result_class",
    }
    if not isinstance(raw, dict) or set(raw) != expected_fields:
        raise ValueError("v3 historical result record schema is invalid")
    relative = _safe_result_path(raw.get("path"))
    logical_result_id = raw.get("logical_result_id")
    market_profile = raw.get("market_profile")
    if (
        not isinstance(logical_result_id, str)
        or _LOGICAL_RESULT.fullmatch(logical_result_id) is None
        or market_profile not in _MARKET_PROFILES
        or raw.get("finding_ids") != ["R2-002", "R2-007"]
        or raw.get("historical_bytes_preserved") is not True
    ):
        raise ValueError("v3 historical result record material is invalid")
    try:
        result_class = HistoricalResultClass(raw.get("result_class"))
        copy_role = HistoricalCopyRole(raw.get("copy_role"))
        run_status = HistoricalRunStatus(raw.get("historical_run_status"))
        financial_status = FinancialResultStatus(raw.get("financial_result_status"))
        reason = HistoricalStatusReason(raw.get("reason_code"))
        replacement = ReplacementRequirement(raw.get("replacement_requirement"))
    except ValueError as exc:
        raise ValueError("v3 historical result record vocabulary is invalid") from exc
    if (
        result_class not in {HistoricalResultClass.CANDIDATE, HistoricalResultClass.BENCHMARK}
        or copy_role not in {HistoricalCopyRole.PRIMARY, HistoricalCopyRole.REPLAY}
        or run_status is not HistoricalRunStatus.SUPERSEDED
        or financial_status is not FinancialResultStatus.SUPERSEDED
        or reason is not HistoricalStatusReason.RUNTIME_AUTHORITY_SUPERSESSION
        or replacement is not ReplacementRequirement.FINAL_PRODUCT_RUNTIME_REBUILD
    ):
        raise ValueError("v3 runtime supersession contract is inconsistent")
    return HistoricalResultRecord(
        path=relative,
        market_profile=str(market_profile),
        historical_run_status=run_status,
        financial_result_status=financial_status,
        finding_ids=("R2-002", "R2-007"),
        current_checker_outcome="NOT_APPLICABLE",
        current_failure_codes=(),
        historical_bytes_preserved=True,
        evidence_hashes=_evidence_hashes(
            raw.get("evidence_hashes"),
            expected_files=V3_EVIDENCE_FILES,
        ),
        logical_result_id=str(logical_result_id),
        result_class=result_class,
        copy_role=copy_role,
        reason_code=reason,
        replacement_requirement=replacement,
    )


def _load_v2_registry(value: dict[str, Any], payload: bytes) -> HistoricalResultRegistry:
    required_root = {
        "audited_baseline_commit",
        "authority_id",
        "final_holdout_authorized",
        "historical_policy",
        "profitability_claim_authorized",
        "record_count",
        "recorded_at_utc",
        "records",
        "records_identity",
        "registry_identity",
        "schema",
        "source_commit",
    }
    if (
        set(value) != required_root
        or value.get("schema") != RESULT_STATUS_V2_SCHEMA
        or value.get("authority_id") != R2_RESULT_STATUS_AUTHORITY
        or value.get("audited_baseline_commit") != R2_AUDITED_BASELINE_COMMIT
        or not _valid_git_sha(value.get("source_commit"))
        or value.get("historical_policy") != RESULT_STATUS_V2_POLICY
        or value.get("final_holdout_authorized") is not False
        or value.get("profitability_claim_authorized") is not False
    ):
        raise ValueError("v2 historical result registry schema is invalid")
    _utc_timestamp(value.get("recorded_at_utc"))
    raw_records = value.get("records")
    if (
        not isinstance(raw_records, list)
        or not raw_records
        or value.get("record_count") != len(raw_records)
        or value.get("records_identity") != canonical_sha256(raw_records)
    ):
        raise ValueError("v2 historical result registry records identity is invalid")
    material = dict(value)
    declared_identity = material.pop("registry_identity", None)
    if not _valid_sha256(declared_identity) or canonical_sha256(material) != declared_identity:
        raise ValueError("v2 historical result registry root identity is invalid")
    if payload != canonical_json_bytes(value) + b"\n":
        raise ValueError("v2 historical result registry bytes are not canonical")
    records = tuple(_v2_record(raw) for raw in raw_records)
    ordering = tuple(
        sorted(
            records,
            key=lambda item: (item.logical_result_id, item.copy_role.value, item.path),
        ),
    )
    if records != ordering or len({item.path for item in records}) != len(records):
        raise ValueError("v2 historical result records are unordered or duplicated")
    by_logical: dict[str, list[HistoricalResultRecord]] = {}
    for record in records:
        by_logical.setdefault(record.logical_result_id, []).append(record)
    if set(by_logical) != set(R2_EXPECTED_HISTORICAL_RESULTS) or len(records) != 12:
        raise ValueError("v2 historical result registry does not cover the exact R2-002 scope")
    for logical_result_id, pair in by_logical.items():
        if (
            {item.copy_role for item in pair}
            != {HistoricalCopyRole.PRIMARY, HistoricalCopyRole.REPLAY}
            or len(pair) != 2
        ):
            raise ValueError(f"v2 primary/replay pair is incomplete: {logical_result_id}")
        first, second = pair
        stable = lambda item: (
            item.market_profile,
            item.result_class,
            item.historical_run_status,
            item.financial_result_status,
            item.finding_ids,
            item.reason_code,
            item.replacement_requirement,
        )
        if stable(first) != stable(second):
            raise ValueError(f"v2 primary/replay statuses conflict: {logical_result_id}")
        expected_result = R2_EXPECTED_HISTORICAL_RESULTS[logical_result_id]
        actual_paths = {item.copy_role: item.path for item in pair}
        if (
            first.market_profile != expected_result["market_profile"]
            or first.result_class.value != expected_result["result_class"]
            or actual_paths[HistoricalCopyRole.PRIMARY] != expected_result["primary_path"]
            or actual_paths[HistoricalCopyRole.REPLAY] != expected_result["replay_path"]
        ):
            raise ValueError(f"v2 historical result authority scope mismatch: {logical_result_id}")
    return HistoricalResultRegistry(
        source_commit=str(value["source_commit"]),
        records=records,
        final_holdout_authorized=False,
        profitability_claim_authorized=False,
        registry_schema=RESULT_STATUS_V2_SCHEMA,
        registry_identity=str(declared_identity),
        authority_id=str(value["authority_id"]),
    )


def _load_v3_registry(value: dict[str, Any], payload: bytes) -> HistoricalResultRegistry:
    required_root = {
        "audited_baseline_commit",
        "authority_id",
        "final_holdout_authorized",
        "historical_policy",
        "profitability_claim_authorized",
        "record_count",
        "recorded_at_utc",
        "records",
        "records_identity",
        "registry_identity",
        "schema",
        "source_commit",
    }
    if (
        set(value) != required_root
        or value.get("schema") != RESULT_STATUS_V3_SCHEMA
        or value.get("authority_id") != R2_RUNTIME_SUPERSESSION_AUTHORITY
        or value.get("audited_baseline_commit") != R2_AUDITED_BASELINE_COMMIT
        or not _valid_git_sha(value.get("source_commit"))
        or value.get("historical_policy") != RESULT_STATUS_V2_POLICY
        or value.get("final_holdout_authorized") is not False
        or value.get("profitability_claim_authorized") is not False
    ):
        raise ValueError("v3 historical result registry schema is invalid")
    _utc_timestamp(value.get("recorded_at_utc"))
    raw_records = value.get("records")
    if (
        not isinstance(raw_records, list)
        or not raw_records
        or value.get("record_count") != len(raw_records)
        or value.get("records_identity") != canonical_sha256(raw_records)
    ):
        raise ValueError("v3 historical result registry records identity is invalid")
    material = dict(value)
    declared_identity = material.pop("registry_identity", None)
    if not _valid_sha256(declared_identity) or canonical_sha256(material) != declared_identity:
        raise ValueError("v3 historical result registry root identity is invalid")
    if payload != canonical_json_bytes(value) + b"\n":
        raise ValueError("v3 historical result registry bytes are not canonical")
    records = tuple(_v3_record(raw) for raw in raw_records)
    ordering = tuple(
        sorted(
            records,
            key=lambda item: (item.logical_result_id, item.copy_role.value, item.path),
        ),
    )
    if records != ordering or len({item.path for item in records}) != len(records):
        raise ValueError("v3 historical result records are unordered or duplicated")
    by_logical: dict[str, list[HistoricalResultRecord]] = {}
    for record in records:
        by_logical.setdefault(record.logical_result_id, []).append(record)
    if (
        set(by_logical) != set(R2_RUNTIME_SUPERSEDED_RESULTS)
        or len(records) != 2 * len(R2_RUNTIME_SUPERSEDED_RESULTS)
    ):
        raise ValueError("v3 registry does not cover the exact runtime-supersession scope")
    for logical_result_id, pair in by_logical.items():
        if (
            {item.copy_role for item in pair}
            != {HistoricalCopyRole.PRIMARY, HistoricalCopyRole.REPLAY}
            or len(pair) != 2
        ):
            raise ValueError(f"v3 primary/replay pair is incomplete: {logical_result_id}")
        expected = R2_RUNTIME_SUPERSEDED_RESULTS[logical_result_id]
        paths = {item.copy_role: item.path for item in pair}
        first, second = pair
        stable = lambda item: (
            item.market_profile,
            item.result_class,
            item.historical_run_status,
            item.financial_result_status,
            item.finding_ids,
            item.reason_code,
            item.replacement_requirement,
        )
        if (
            stable(first) != stable(second)
            or first.market_profile != expected["market_profile"]
            or first.result_class.value != expected["result_class"]
            or paths[HistoricalCopyRole.PRIMARY] != expected["primary_path"]
            or paths[HistoricalCopyRole.REPLAY] != expected["replay_path"]
        ):
            raise ValueError(f"v3 runtime-supersession scope mismatch: {logical_result_id}")
    return HistoricalResultRegistry(
        source_commit=str(value["source_commit"]),
        records=records,
        final_holdout_authorized=False,
        profitability_claim_authorized=False,
        registry_schema=RESULT_STATUS_V3_SCHEMA,
        registry_identity=str(declared_identity),
        authority_id=str(value["authority_id"]),
    )


def load_historical_result_registry(path: Path) -> HistoricalResultRegistry:
    """Parse one immutable additive registry, preserving legacy v1 authority."""

    payload = Path(path).read_bytes()
    value = _strict_json(payload)
    if not isinstance(value, dict):
        raise ValueError("historical result registry must be a JSON object")
    if value.get("schema") == "audit-historical-result-status-v1":
        return _load_v1_registry(value)
    if value.get("schema") == RESULT_STATUS_V2_SCHEMA:
        return _load_v2_registry(value, payload)
    if value.get("schema") == RESULT_STATUS_V3_SCHEMA:
        return _load_v3_registry(value, payload)
    raise ValueError("historical result registry schema is unsupported")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolved_run_path(run_directory: Path, repository_root: Path) -> tuple[Path, str]:
    root = Path(repository_root).resolve(strict=True)
    run = Path(run_directory)
    if run.is_symlink():
        raise ValueError("historical result directory must not be a symlink")
    resolved = run.resolve(strict=True)
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("historical result directory escapes repository root") from exc
    _safe_result_path(relative)
    if not resolved.is_dir():
        raise ValueError("historical result path must be a directory")
    return resolved, relative


def _registry_files(
    root: Path,
    registry_paths: Iterable[Path] | None,
) -> tuple[Path, ...]:
    references = (
        tuple(Path(item) for item in registry_paths)
        if registry_paths is not None
        else tuple(Path(item) for item in DEFAULT_RESULT_STATUS_REFS)
    )
    if not references:
        raise ValueError("at least one required result-status registry must be declared")
    resolved: list[Path] = []
    for reference in references:
        candidate = reference if reference.is_absolute() else root / reference
        if candidate.is_symlink():
            raise ValueError(f"result-status registry must not be a symlink: {candidate}")
        try:
            path = candidate.resolve(strict=True)
            path.relative_to(root)
        except (FileNotFoundError, ValueError) as exc:
            raise ValueError(f"required result-status registry is missing or outside root: {candidate}") from exc
        if not path.is_file():
            raise ValueError(f"required result-status registry is not a file: {path}")
        resolved.append(path)
    if len(set(resolved)) != len(resolved):
        raise ValueError("duplicate result-status registry reference")
    return tuple(resolved)


def _verify_recorded_evidence(run: Path, record: HistoricalResultRecord) -> None:
    for name, expected in record.evidence_hashes.items():
        path = run / name
        if path.is_symlink() or not path.is_file() or _hash_file(path) != expected:
            raise ValueError(f"historical result status evidence binding failed: {record.path}/{name}")


def resolve_result_status(
    run_directory: Path,
    *,
    repository_root: Path,
    registry_paths: Iterable[Path] | None = None,
    verify_recorded_evidence: bool = True,
) -> ResultStatusResolution:
    """Resolve ACTIVE/REVOKED/SUPERSEDED across every required additive registry."""

    root = Path(repository_root).resolve(strict=True)
    run, relative = _resolved_run_path(run_directory, root)
    matches: list[tuple[HistoricalResultRecord, Path]] = []
    for registry_path in _registry_files(root, registry_paths):
        record = load_historical_result_registry(registry_path).for_path(relative)
        if record is not None:
            matches.append((record, registry_path))
    if len(matches) > 1:
        raise ValueError(f"historical result appears in conflicting registries: {relative}")
    if not matches:
        return ResultStatusResolution(
            relative_path=relative,
            historical_run_status=HistoricalRunStatus.ACTIVE,
            financial_result_status=FinancialResultStatus.ACTIVE,
            record=None,
            registry_path=None,
        )
    record, registry_path = matches[0]
    if (
        record.historical_run_status is HistoricalRunStatus.ACTIVE
        or record.financial_result_status is FinancialResultStatus.ACTIVE
    ):
        raise ValueError("additive historical registries must not contain ACTIVE records")
    if verify_recorded_evidence:
        _verify_recorded_evidence(run, record)
    return ResultStatusResolution(
        relative_path=relative,
        historical_run_status=record.historical_run_status,
        financial_result_status=record.financial_result_status,
        record=record,
        registry_path=registry_path.relative_to(root).as_posix(),
    )


def require_active_result(
    run_directory: Path,
    *,
    repository_root: Path,
    registry_paths: Iterable[Path] | None = None,
) -> ResultStatusResolution:
    """Return only an independently resolved ACTIVE result; reject every other state."""

    resolution = resolve_result_status(
        run_directory,
        repository_root=repository_root,
        registry_paths=registry_paths,
    )
    if not resolution.is_active:
        raise ResultNotActiveError(resolution)
    return resolution


def revoked_result_for_directory(
    run_directory: Path,
    *,
    repository_root: Path,
    registry_path: Path | None = None,
) -> HistoricalResultRecord | None:
    """Backward-compatible lookup; returns any non-ACTIVE record, including SUPERSEDED."""

    resolution = resolve_result_status(
        run_directory,
        repository_root=repository_root,
        registry_paths=(registry_path,) if registry_path is not None else None,
    )
    return resolution.record


def build_historical_result_record_v2(
    run_directory: Path,
    *,
    repository_root: Path,
    logical_result_id: str,
    market_profile: str,
    result_class: HistoricalResultClass,
    copy_role: HistoricalCopyRole,
) -> dict[str, Any]:
    """Build one canonical record from immutable bytes without writing a registry."""

    run, relative = _resolved_run_path(run_directory, repository_root)
    evidence_hashes: dict[str, str] = {}
    for name in V2_EVIDENCE_FILES:
        path = run / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"required historical evidence file is missing: {relative}/{name}")
        evidence_hashes[name] = _hash_file(path)
    if result_class is HistoricalResultClass.CANDIDATE:
        status_values = {
            "historical_run_status": HistoricalRunStatus.REVOKED.value,
            "financial_result_status": FinancialResultStatus.INVALIDATED.value,
            "finding_ids": ["R2-001", "R2-002"],
            "reason_code": HistoricalStatusReason.WARMUP_SCORING_ELIGIBILITY_VIOLATION.value,
            "replacement_requirement": (
                ReplacementRequirement.WARMUP_SCORING_FIX_AND_V2_REBUILD.value
            ),
        }
    elif result_class is HistoricalResultClass.BENCHMARK:
        status_values = {
            "historical_run_status": HistoricalRunStatus.SUPERSEDED.value,
            "financial_result_status": FinancialResultStatus.SUPERSEDED.value,
            "finding_ids": ["R2-002"],
            "reason_code": HistoricalStatusReason.RESULT_CONTRACT_V2_SUPERSESSION.value,
            "replacement_requirement": (
                ReplacementRequirement.DATASET_RELEASE_CHECKER_RESULT_SCHEMA_V2_REBUILD.value
            ),
        }
    else:
        raise ValueError("v2 helper supports only Candidate or Benchmark results")
    record = {
        "path": relative,
        "logical_result_id": logical_result_id,
        "market_profile": market_profile,
        "result_class": result_class.value,
        "copy_role": copy_role.value,
        **status_values,
        "historical_bytes_preserved": True,
        "evidence_hashes": evidence_hashes,
    }
    _v2_record(record)
    return record


def build_historical_result_registry_v2(
    records: Iterable[dict[str, Any]],
    *,
    authority_id: str,
    audited_baseline_commit: str,
    source_commit: str,
    recorded_at_utc: datetime,
) -> bytes:
    """Return canonical immutable registry bytes; this helper performs no write."""

    if recorded_at_utc.tzinfo is None or recorded_at_utc.utcoffset() != UTC.utcoffset(recorded_at_utc):
        raise ValueError("recorded_at_utc must use timezone-aware UTC")
    copied = json.loads(canonical_json_bytes(list(records)))
    copied.sort(
        key=lambda item: (
            str(item.get("logical_result_id")),
            str(item.get("copy_role")),
            str(item.get("path")),
        ),
    )
    manifest: dict[str, Any] = {
        "schema": RESULT_STATUS_V2_SCHEMA,
        "authority_id": authority_id,
        "audited_baseline_commit": audited_baseline_commit,
        "source_commit": source_commit,
        "recorded_at_utc": recorded_at_utc.isoformat().replace("+00:00", "Z"),
        "historical_policy": RESULT_STATUS_V2_POLICY,
        "final_holdout_authorized": False,
        "profitability_claim_authorized": False,
        "record_count": len(copied),
        "records": copied,
        "records_identity": canonical_sha256(copied),
    }
    manifest["registry_identity"] = canonical_sha256(manifest)
    payload = canonical_json_bytes(manifest) + b"\n"
    _load_v2_registry(manifest, payload)
    return payload


def build_runtime_supersession_record_v3(
    run_directory: Path,
    *,
    repository_root: Path,
    logical_result_id: str,
    market_profile: str,
    result_class: HistoricalResultClass,
    copy_role: HistoricalCopyRole,
) -> dict[str, Any]:
    """Bind one immutable current-schema Run as runtime-superseded evidence."""

    run, relative = _resolved_run_path(run_directory, repository_root)
    evidence_hashes: dict[str, str] = {}
    for name in V3_EVIDENCE_FILES:
        path = run / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"required historical evidence file is missing: {relative}/{name}")
        evidence_hashes[name] = _hash_file(path)
    record = {
        "path": relative,
        "logical_result_id": logical_result_id,
        "market_profile": market_profile,
        "result_class": result_class.value,
        "copy_role": copy_role.value,
        "historical_run_status": HistoricalRunStatus.SUPERSEDED.value,
        "financial_result_status": FinancialResultStatus.SUPERSEDED.value,
        "finding_ids": ["R2-002", "R2-007"],
        "reason_code": HistoricalStatusReason.RUNTIME_AUTHORITY_SUPERSESSION.value,
        "replacement_requirement": ReplacementRequirement.FINAL_PRODUCT_RUNTIME_REBUILD.value,
        "historical_bytes_preserved": True,
        "evidence_hashes": evidence_hashes,
    }
    _v3_record(record)
    return record


def build_runtime_supersession_registry_v3(
    records: Iterable[dict[str, Any]],
    *,
    authority_id: str,
    audited_baseline_commit: str,
    source_commit: str,
    recorded_at_utc: datetime,
) -> bytes:
    """Return the canonical closed R2 runtime-supersession registry bytes."""

    if recorded_at_utc.tzinfo is None or recorded_at_utc.utcoffset() != UTC.utcoffset(
        recorded_at_utc,
    ):
        raise ValueError("recorded_at_utc must use timezone-aware UTC")
    copied = json.loads(canonical_json_bytes(list(records)))
    copied.sort(
        key=lambda item: (
            str(item.get("logical_result_id")),
            str(item.get("copy_role")),
            str(item.get("path")),
        ),
    )
    manifest: dict[str, Any] = {
        "schema": RESULT_STATUS_V3_SCHEMA,
        "authority_id": authority_id,
        "audited_baseline_commit": audited_baseline_commit,
        "source_commit": source_commit,
        "recorded_at_utc": recorded_at_utc.isoformat().replace("+00:00", "Z"),
        "historical_policy": RESULT_STATUS_V2_POLICY,
        "final_holdout_authorized": False,
        "profitability_claim_authorized": False,
        "record_count": len(copied),
        "records": copied,
        "records_identity": canonical_sha256(copied),
    }
    manifest["registry_identity"] = canonical_sha256(manifest)
    payload = canonical_json_bytes(manifest) + b"\n"
    _load_v3_registry(manifest, payload)
    return payload


__all__ = [
    "DEFAULT_RESULT_STATUS_REFS",
    "FinancialResultStatus",
    "HistoricalCopyRole",
    "HistoricalResultClass",
    "HistoricalResultRecord",
    "HistoricalResultRegistry",
    "HistoricalRunStatus",
    "HistoricalStatusReason",
    "ReplacementRequirement",
    "R2_AUDITED_BASELINE_COMMIT",
    "R2_EXPECTED_HISTORICAL_RESULTS",
    "R2_RESULT_STATUS_AUTHORITY",
    "R2_RUNTIME_SUPERSEDED_RESULTS",
    "R2_RUNTIME_SUPERSESSION_AUTHORITY",
    "ResultNotActiveError",
    "ResultStatusResolution",
    "build_historical_result_record_v2",
    "build_historical_result_registry_v2",
    "build_runtime_supersession_record_v3",
    "build_runtime_supersession_registry_v3",
    "load_historical_result_registry",
    "require_active_result",
    "resolve_result_status",
    "revoked_result_for_directory",
]
