#!/usr/bin/env python3
"""Build the additive retry-008 scientific-claim supersession registry."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from crypto_lab.hashing import canonical_sha256
from crypto_lab.result_status import HistoricalCopyRole
from crypto_lab.result_status import HistoricalResultClass
from crypto_lab.result_status import R2_AUDITED_BASELINE_COMMIT
from crypto_lab.result_status import R2_CLAIM_SCHEMA_SUPERSEDED_RESULTS
from crypto_lab.result_status import R2_CLAIM_SCHEMA_SUPERSESSION_AUTHORITY
from crypto_lab.result_status import build_claim_schema_supersession_record_v4
from crypto_lab.result_status import build_claim_schema_supersession_registry_v4


_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
EXPECTED_CLAIM_SCHEMA_EVIDENCE_IDENTITIES = {
    "runs/adversarial-remediation-002-retry-008-perpetual-benchmark-run-6252b1a621c4": (
        "96ddd6be1209d0fd7526eb5254fbc58883c47a4bb79b5ccc982f2994dd867a83"
    ),
    "runs/adversarial-remediation-002-retry-008-perpetual-candidate-a-run-fa6092949cf3": (
        "2ee68bb52ac2a3a224698c943e9f5dd4e549a1822c1a566e1d7bbfea8aa8803b"
    ),
    "runs/adversarial-remediation-002-retry-008-perpetual-candidate-b-run-4386ce883214": (
        "d679cc1cfca640d48b6d535b49b1572a946fd9f5c3eaa4ed93cc46281f670512"
    ),
    "runs/adversarial-remediation-002-retry-008-spot-benchmark-run-a330b33e395f": (
        "93abf45694ab931ec62a7bc5a18529e6d5f291a35f0dbeedab03a3c8b72a4159"
    ),
    "runs/adversarial-remediation-002-retry-008-spot-candidate-a-run-14716a0da267": (
        "68f74665b2835f95289a1fec2415d56833a9d34a24c261e78db2a3d0160e103a"
    ),
    "runs/adversarial-remediation-002-retry-008-spot-candidate-b-run-746ec9cdc2d9": (
        "c9cb2151a9d6220e6af4b83a839ab99e8098f0fcaabcadf647aa73d08b5df199"
    ),
    (
        "runs/replays/adversarial-remediation-002-retry-008-perpetual-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-008-"
        "perpetual-benchmark-run-6252b1a621c4"
    ): "bb6fb40a243d1ce2cae7835749cc9c91670a07f32f0dd2bb85e019e63efa912d",
    (
        "runs/replays/adversarial-remediation-002-retry-008-perpetual-candidate-a-"
        "development/adversarial-remediation-002-retry-008-perpetual-candidate-a-"
        "run-fa6092949cf3"
    ): "1d37527572b75c8a816ab4a60f9d987e24ec97472a020a8c9aa29cd156c77e13",
    (
        "runs/replays/adversarial-remediation-002-retry-008-perpetual-candidate-b-"
        "development/adversarial-remediation-002-retry-008-perpetual-candidate-b-"
        "run-4386ce883214"
    ): "155a2b9dbc274d9dad3557f2757d306c9cab579172cc57f230b06c34e37dcb78",
    (
        "runs/replays/adversarial-remediation-002-retry-008-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-008-"
        "spot-benchmark-run-a330b33e395f"
    ): "b3172da7e00f25cfe7e9c35256445125a1ffc97095946e35f6e456df3997360f",
    (
        "runs/replays/adversarial-remediation-002-retry-008-spot-candidate-a-"
        "development/adversarial-remediation-002-retry-008-spot-candidate-a-"
        "run-14716a0da267"
    ): "1270c7b00e95bc7bff8a0f3985bf71d75a320d389cef3c0051a4073b32de2e7a",
    (
        "runs/replays/adversarial-remediation-002-retry-008-spot-candidate-b-"
        "development/adversarial-remediation-002-retry-008-spot-candidate-b-"
        "run-746ec9cdc2d9"
    ): "f1de51b1e07ef39357c7f851060bbb3069a1d61f63248b01a48f8db620e34b62",
}


class ClaimSchemaSupersessionBuildError(ValueError):
    """The frozen claim-schema supersession registry could not be proven."""


def _recorded_at_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ClaimSchemaSupersessionBuildError(
            "recorded_at_utc must be explicit UTC ending in Z",
        )
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ClaimSchemaSupersessionBuildError("recorded_at_utc is invalid") from exc
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise ClaimSchemaSupersessionBuildError("recorded_at_utc must use UTC")
    return result


def build_registry(
    *,
    repository_root: Path,
    source_commit: str,
    recorded_at_utc: str,
) -> bytes:
    root = Path(repository_root).resolve(strict=True)
    if root.is_symlink() or not root.is_dir():
        raise ClaimSchemaSupersessionBuildError("repository root must be an exact directory")
    if _GIT_SHA.fullmatch(source_commit) is None:
        raise ClaimSchemaSupersessionBuildError(
            "source_commit must be explicit lowercase 40-hex",
        )
    expected_paths = {
        item[key]
        for item in R2_CLAIM_SCHEMA_SUPERSEDED_RESULTS.values()
        for key in ("primary_path", "replay_path")
    }
    if expected_paths != set(EXPECTED_CLAIM_SCHEMA_EVIDENCE_IDENTITIES):
        raise ClaimSchemaSupersessionBuildError(
            "frozen claim-schema supersession scope is inconsistent",
        )
    records: list[dict[str, object]] = []
    for logical_id, expected in sorted(R2_CLAIM_SCHEMA_SUPERSEDED_RESULTS.items()):
        for copy_role, key in (
            (HistoricalCopyRole.PRIMARY, "primary_path"),
            (HistoricalCopyRole.REPLAY, "replay_path"),
        ):
            relative = expected[key]
            try:
                record = build_claim_schema_supersession_record_v4(
                    root / relative,
                    repository_root=root,
                    logical_result_id=logical_id,
                    market_profile=expected["market_profile"],
                    result_class=HistoricalResultClass(expected["result_class"]),
                    copy_role=copy_role,
                )
            except (OSError, ValueError) as exc:
                raise ClaimSchemaSupersessionBuildError(
                    f"cannot bind immutable superseded result {relative}: {exc}",
                ) from exc
            if (
                canonical_sha256(record["evidence_hashes"])
                != EXPECTED_CLAIM_SCHEMA_EVIDENCE_IDENTITIES[relative]
            ):
                raise ClaimSchemaSupersessionBuildError(
                    f"claim-schema supersession evidence identity mismatch: {relative}",
                )
            records.append(record)
    return build_claim_schema_supersession_registry_v4(
        records,
        authority_id=R2_CLAIM_SCHEMA_SUPERSESSION_AUTHORITY,
        audited_baseline_commit=R2_AUDITED_BASELINE_COMMIT,
        source_commit=source_commit,
        recorded_at_utc=_recorded_at_utc(recorded_at_utc),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--recorded-at-utc", required=True)
    arguments = parser.parse_args(argv)
    sys.stdout.buffer.write(
        build_registry(
            repository_root=arguments.repository,
            source_commit=arguments.source_commit,
            recorded_at_utc=arguments.recorded_at_utc,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
