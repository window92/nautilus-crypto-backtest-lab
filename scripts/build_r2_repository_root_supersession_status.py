#!/usr/bin/env python3
"""Build the additive retry-012 explicit-repository-root supersession registry."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from crypto_lab.git_identity import require_repository_root
from crypto_lab.hashing import canonical_sha256
from crypto_lab.result_status import HistoricalCopyRole
from crypto_lab.result_status import HistoricalResultClass
from crypto_lab.result_status import R2_AUDITED_BASELINE_COMMIT
from crypto_lab.result_status import R2_REPOSITORY_ROOT_SUPERSEDED_RESULTS
from crypto_lab.result_status import R2_REPOSITORY_ROOT_SUPERSESSION_AUTHORITY
from crypto_lab.result_status import build_repository_root_supersession_record_v8
from crypto_lab.result_status import build_repository_root_supersession_registry_v8


_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
EXPECTED_REPOSITORY_ROOT_EVIDENCE_IDENTITIES = {
    "runs/adversarial-remediation-002-retry-012-perpetual-benchmark-run-81fe58a28828": (
        "a8f8956654ec385200ccb49634e3a0bb01505028d22ed357cc4ea1774d0e4158"
    ),
    "runs/adversarial-remediation-002-retry-012-perpetual-candidate-a-run-306f69e74b26": (
        "798e5e7b6d7191eacaa4c81514bafcd80f382904455bdb71d67474f99e9bf9d7"
    ),
    "runs/adversarial-remediation-002-retry-012-perpetual-candidate-b-run-bd26bd68a201": (
        "0970f11b0e86609378d110944d88e7d0cd565fa2bf40dc7627da3a83ffe4c832"
    ),
    "runs/adversarial-remediation-002-retry-012-spot-benchmark-run-89a146f07e52": (
        "be4f97063a1ec7cca173563baa757d82c8ef04f3ba47a2ca47dd54a92e18ac0d"
    ),
    "runs/adversarial-remediation-002-retry-012-spot-candidate-a-run-db0f8a5fc93d": (
        "c35dee9cca2dc4d817b5c46439e5bfe5d9463609778d76c19ceeb73bc424356e"
    ),
    "runs/adversarial-remediation-002-retry-012-spot-candidate-b-run-978d34af0dbe": (
        "a7c1109a917cbc8685a798d82c4aa28a859a2aa6badffd12144b73a73300ae60"
    ),
    (
        "runs/replays/adversarial-remediation-002-retry-012-perpetual-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-012-"
        "perpetual-benchmark-run-81fe58a28828"
    ): "987be63875bc4b8ce13ec27f9e021ed3357e9f0b91329bd76cca94f8086ec0af",
    (
        "runs/replays/adversarial-remediation-002-retry-012-perpetual-candidate-a-"
        "development/adversarial-remediation-002-retry-012-perpetual-candidate-a-"
        "run-306f69e74b26"
    ): "664b6d33e36bfa8577eb672802971416628841481b0d6a5c3155f57d112923a3",
    (
        "runs/replays/adversarial-remediation-002-retry-012-perpetual-candidate-b-"
        "development/adversarial-remediation-002-retry-012-perpetual-candidate-b-"
        "run-bd26bd68a201"
    ): "70465b43db9f971d721c0ea36cb0fa9901a7de6ee92fc4810f781363edfe98b3",
    (
        "runs/replays/adversarial-remediation-002-retry-012-spot-benchmark-"
        "buy-and-hold-1x-development/adversarial-remediation-002-retry-012-"
        "spot-benchmark-run-89a146f07e52"
    ): "43e185af99ef5e8dfd9518e29987c6bbaedb152ed5a6492674b0091489bf9deb",
    (
        "runs/replays/adversarial-remediation-002-retry-012-spot-candidate-a-"
        "development/adversarial-remediation-002-retry-012-spot-candidate-a-"
        "run-db0f8a5fc93d"
    ): "724a5899d7fbb29483b499bdf4878f627672887959f24458321291911ddf887a",
    (
        "runs/replays/adversarial-remediation-002-retry-012-spot-candidate-b-"
        "development/adversarial-remediation-002-retry-012-spot-candidate-b-"
        "run-978d34af0dbe"
    ): "40907dfa7e15db0d49f6fc211bf4012c701147df7e02b4b145089e83986f570a",
}


class RepositoryRootSupersessionBuildError(ValueError):
    """The frozen retry-012 supersession registry could not be proven."""


def _recorded_at_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise RepositoryRootSupersessionBuildError(
            "recorded_at_utc must be explicit UTC ending in Z",
        )
    try:
        result = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RepositoryRootSupersessionBuildError("recorded_at_utc is invalid") from exc
    if result.tzinfo is None or result.utcoffset() != UTC.utcoffset(result):
        raise RepositoryRootSupersessionBuildError("recorded_at_utc must use UTC")
    return result


def build_registry(
    *,
    repository_root: Path,
    source_commit: str,
    recorded_at_utc: str,
) -> bytes:
    try:
        root = require_repository_root(repository_root)
    except (TypeError, ValueError) as exc:
        raise RepositoryRootSupersessionBuildError(
            f"repository authority is invalid: {exc}",
        ) from exc
    if _GIT_SHA.fullmatch(source_commit) is None:
        raise RepositoryRootSupersessionBuildError(
            "source_commit must be explicit lowercase 40-hex",
        )
    expected_paths = {
        item[key]
        for item in R2_REPOSITORY_ROOT_SUPERSEDED_RESULTS.values()
        for key in ("primary_path", "replay_path")
    }
    if expected_paths != set(EXPECTED_REPOSITORY_ROOT_EVIDENCE_IDENTITIES):
        raise RepositoryRootSupersessionBuildError(
            "frozen explicit-root supersession scope is inconsistent",
        )
    records: list[dict[str, object]] = []
    for logical_id, expected in sorted(R2_REPOSITORY_ROOT_SUPERSEDED_RESULTS.items()):
        for copy_role, key in (
            (HistoricalCopyRole.PRIMARY, "primary_path"),
            (HistoricalCopyRole.REPLAY, "replay_path"),
        ):
            relative = expected[key]
            try:
                record = build_repository_root_supersession_record_v8(
                    root / relative,
                    repository_root=root,
                    logical_result_id=logical_id,
                    market_profile=expected["market_profile"],
                    result_class=HistoricalResultClass(expected["result_class"]),
                    copy_role=copy_role,
                )
            except (OSError, ValueError) as exc:
                raise RepositoryRootSupersessionBuildError(
                    f"cannot bind immutable superseded result {relative}: {exc}",
                ) from exc
            if (
                canonical_sha256(record["evidence_hashes"])
                != EXPECTED_REPOSITORY_ROOT_EVIDENCE_IDENTITIES[relative]
            ):
                raise RepositoryRootSupersessionBuildError(
                    f"evidence identity mismatch for {relative}",
                )
            records.append(record)
    return build_repository_root_supersession_registry_v8(
        records,
        authority_id=R2_REPOSITORY_ROOT_SUPERSESSION_AUTHORITY,
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
