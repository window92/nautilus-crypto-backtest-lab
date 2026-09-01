"""Closed status and failure-code vocabulary defined by SSOT.md."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class RunState(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    ABORTED = "ABORTED"


class FailureCode(StrEnum):
    RUNTIME_LOCK_MISMATCH = "RUNTIME_LOCK_MISMATCH"
    RUNTIME_WHEEL_HASH_MISMATCH = "RUNTIME_WHEEL_HASH_MISMATCH"
    RUNTIME_STARTUP_MISMATCH = "RUNTIME_STARTUP_MISMATCH"
    UNSUPPORTED_RUNTIME = "UNSUPPORTED_RUNTIME"
    UNSUPPORTED_MARKET_PROFILE = "UNSUPPORTED_MARKET_PROFILE"
    UNSUPPORTED_V1_ORDER_TYPE = "UNSUPPORTED_V1_ORDER_TYPE"
    CONFIG_INVALID = "CONFIG_INVALID"
    CONFIG_HASH_MISMATCH = "CONFIG_HASH_MISMATCH"
    NETWORK_DURING_OFFICIAL_RUN = "NETWORK_DURING_OFFICIAL_RUN"
    DATA_SOURCE_INVALID = "DATA_SOURCE_INVALID"
    DATA_HASH_MISMATCH = "DATA_HASH_MISMATCH"
    DATA_TIMESTAMP_INVALID = "DATA_TIMESTAMP_INVALID"
    DATA_GAP = "DATA_GAP"
    DATA_DUPLICATE_CONFLICT = "DATA_DUPLICATE_CONFLICT"
    DATA_ROLE_MISMATCH = "DATA_ROLE_MISMATCH"
    DATASET_RELEASE_STALE = "DATASET_RELEASE_STALE"
    DATASET_RAW_INVENTORY_MISMATCH = "DATASET_RAW_INVENTORY_MISMATCH"
    IRRECOVERABLE_OFFICIAL_MARK_DELIVERY_GAP = "IRRECOVERABLE_OFFICIAL_MARK_DELIVERY_GAP"
    DATA_WINDOW_QUALITY_EXHAUSTED = "DATA_WINDOW_QUALITY_EXHAUSTED"
    INSTRUMENT_METADATA_INVALID = "INSTRUMENT_METADATA_INVALID"
    TIMEFRAME_AGGREGATION_UNRESOLVED = "TIMEFRAME_AGGREGATION_UNRESOLVED"
    CAUSAL_EXECUTION_UNRESOLVED = "CAUSAL_EXECUTION_UNRESOLVED"
    LOOKAHEAD_DETECTED = "LOOKAHEAD_DETECTED"
    WARMUP_SCORING_ELIGIBILITY_VIOLATION = "WARMUP_SCORING_ELIGIBILITY_VIOLATION"
    SAME_BAR_EXECUTION_DETECTED = "SAME_BAR_EXECUTION_DETECTED"
    FILL_MUTATION_DETECTED = "FILL_MUTATION_DETECTED"
    SPOT_SHORT_OR_BORROW_DETECTED = "SPOT_SHORT_OR_BORROW_DETECTED"
    PERP_PROFILE_INVALID = "PERP_PROFILE_INVALID"
    PERPETUAL_RECONCILIATION_FAILURE = "PERPETUAL_RECONCILIATION_FAILURE"
    CROSS_ZERO_ORDER_REJECTED = "CROSS_ZERO_ORDER_REJECTED"
    CONCURRENT_STRATEGY_ORDER_REJECTED = "CONCURRENT_STRATEGY_ORDER_REJECTED"
    FEE_MISSING = "FEE_MISSING"
    FEE_DOUBLE_COUNT = "FEE_DOUBLE_COUNT"
    FUNDING_MISSING = "FUNDING_MISSING"
    FUNDING_AMBIGUOUS = "FUNDING_AMBIGUOUS"
    FUNDING_DOUBLE_COUNT = "FUNDING_DOUBLE_COUNT"
    FUNDING_UNEXPECTED_SETTLEMENT = "FUNDING_UNEXPECTED_SETTLEMENT"
    FUNDING_SIGN_INVALID = "FUNDING_SIGN_INVALID"
    FUNDING_RATE_INVALID = "FUNDING_RATE_INVALID"
    FUNDING_MARK_INVALID = "FUNDING_MARK_INVALID"
    FUNDING_POSITION_INVALID = "FUNDING_POSITION_INVALID"
    FUNDING_BOUNDARY_INVALID = "FUNDING_BOUNDARY_INVALID"
    FUNDING_CURRENCY_INVALID = "FUNDING_CURRENCY_INVALID"
    FUNDING_AMOUNT_INVALID = "FUNDING_AMOUNT_INVALID"
    FUNDING_ACCOUNT_DELTA_INVALID = "FUNDING_ACCOUNT_DELTA_INVALID"
    MARK_ROLE_INVALID = "MARK_ROLE_INVALID"
    DETERMINISM_FAILURE = "DETERMINISM_FAILURE"
    DETERMINISTIC_REBUILD_MISMATCH = "DETERMINISTIC_REBUILD_MISMATCH"
    CHECKER_FAILURE = "CHECKER_FAILURE"
    CHECKER_BLOCKED = "CHECKER_BLOCKED"
    OFFICIAL_SEAL_FAILURE = "OFFICIAL_SEAL_FAILURE"
    HISTORICAL_VALIDATOR_IDENTITY_MISMATCH = "HISTORICAL_VALIDATOR_IDENTITY_MISMATCH"
    PERFORMANCE_METRICS_INVALID = "PERFORMANCE_METRICS_INVALID"
    JOURNAL_DURABILITY_FAILURE = "JOURNAL_DURABILITY_FAILURE"
    TRIAL_HISTORY_INCOMPLETE = "TRIAL_HISTORY_INCOMPLETE"
    RESEARCH_PROTOCOL_INVALID = "RESEARCH_PROTOCOL_INVALID"
    PARTITION_LEAKAGE = "PARTITION_LEAKAGE"
    HOLDOUT_ALREADY_CONSUMED = "HOLDOUT_ALREADY_CONSUMED"
    HOLDOUT_HISTORY_VIOLATION = "HOLDOUT_HISTORY_VIOLATION"
    MULTIPLE_TESTING_UNDECLARED = "MULTIPLE_TESTING_UNDECLARED"
    CLAIM_INELIGIBLE = "CLAIM_INELIGIBLE"
    DOWNSTREAM_CONTRACT_FAILURE = "DOWNSTREAM_CONTRACT_FAILURE"
    DEFECT_ROOT_UNRESOLVED = "DEFECT_ROOT_UNRESOLVED"
    RETRY_LIMIT_REACHED = "RETRY_LIMIT_REACHED"
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"


FUNDING_DIAGNOSIS_PRIORITY: tuple[FailureCode, ...] = (
    FailureCode.FUNDING_MISSING,
    FailureCode.FUNDING_DOUBLE_COUNT,
    FailureCode.FUNDING_UNEXPECTED_SETTLEMENT,
    FailureCode.FUNDING_BOUNDARY_INVALID,
    FailureCode.FUNDING_POSITION_INVALID,
    FailureCode.FUNDING_RATE_INVALID,
    FailureCode.FUNDING_MARK_INVALID,
    FailureCode.FUNDING_SIGN_INVALID,
    FailureCode.FUNDING_AMOUNT_INVALID,
    FailureCode.FUNDING_CURRENCY_INVALID,
    FailureCode.FUNDING_ACCOUNT_DELTA_INVALID,
    FailureCode.FUNDING_AMBIGUOUS,
    FailureCode.MARK_ROLE_INVALID,
)


def ordered_funding_failure_codes(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Return unique funding diagnosis codes in the SSOT-fixed priority order."""

    rank = {code.value: index for index, code in enumerate(FUNDING_DIAGNOSIS_PRIORITY)}
    unique = list(dict.fromkeys(values))
    return tuple(sorted(unique, key=lambda item: (rank.get(item, len(rank)), item)))


def validated_failure_codes(
    values: object,
    *,
    field: str = "failure_codes",
) -> tuple[str, ...]:
    """Return unique canonical enum values or reject a Product/schema bug.

    This helper is for values which the Product is about to persist or expose.
    It deliberately does not invent a fallback for programmer-controlled
    output: an unknown code at that boundary is a contract error.
    """

    if not isinstance(values, list | tuple):
        raise ValueError(f"{field}: expected a failure-code list or tuple")
    canonical: list[str] = []
    for index, value in enumerate(values):
        try:
            code = FailureCode(value).value
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{field}[{index}]: unknown failure code",
            ) from exc
        if code not in canonical:
            canonical.append(code)
    return tuple(canonical)


def canonicalize_evidence_failure_codes(values: Any) -> tuple[str, ...]:
    """Fail closed when untrusted evidence contains an unknown code.

    Evidence bytes are not allowed to extend the vocabulary. Known codes are
    retained, duplicates are removed, and any malformed or unknown member is
    represented only by the canonical ``EVIDENCE_INCOMPLETE`` code. The
    attacker-controlled lexeme is never propagated into a report or status.
    """

    if not isinstance(values, list | tuple):
        return (FailureCode.EVIDENCE_INCOMPLETE.value,)
    canonical: list[str] = []
    unknown = False
    for value in values:
        try:
            code = FailureCode(value).value
        except (TypeError, ValueError):
            unknown = True
            continue
        if code not in canonical:
            canonical.append(code)
    if unknown and FailureCode.EVIDENCE_INCOMPLETE.value not in canonical:
        canonical.append(FailureCode.EVIDENCE_INCOMPLETE.value)
    return tuple(canonical)


__all__ = [
    "FailureCode",
    "FUNDING_DIAGNOSIS_PRIORITY",
    "RunState",
    "canonicalize_evidence_failure_codes",
    "ordered_funding_failure_codes",
    "validated_failure_codes",
]
