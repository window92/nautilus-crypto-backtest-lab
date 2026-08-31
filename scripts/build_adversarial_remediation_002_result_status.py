#!/usr/bin/env python3
"""Build the immutable R2 additive status registry from frozen historical bytes.

The source revision and recording time are explicit inputs.  The historical
scope and its three evidence-file identities are frozen below so rebuilding a
registry cannot silently bless missing, substituted, or modified old bytes.
The output path is fixed and is created once without following symlinks or
overwriting an existing filesystem entry.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from crypto_lab.result_status import (
    HistoricalCopyRole,
    HistoricalResultClass,
    R2_AUDITED_BASELINE_COMMIT,
    R2_EXPECTED_HISTORICAL_RESULTS,
    R2_RESULT_STATUS_AUTHORITY,
    build_historical_result_record_v2,
    build_historical_result_registry_v2,
)


OUTPUT_RELATIVE_PATH = Path(
    "evidence/audit/adversarial-remediation-002/historical-result-status.json",
)
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0)
if hasattr(os, "O_NOFOLLOW"):
    _DIRECTORY_FLAGS |= os.O_NOFOLLOW

# Independent, immutable bindings to the three v1 leaves used by result-status
# resolution.  These are historical bytes, not values inferred from a future
# registry or from R2 Product output.
EXPECTED_HISTORICAL_EVIDENCE_HASHES: dict[str, dict[str, str]] = {
    "runs/comprehensive-audit-remediation-003-perpetual-benchmark-run-a0e2b2553ed4": {
        "checker.json": "7f6d80cf661354b05f7c3f140fa8e5a3fcd0f7f5287a705fb95d399d9b9b9112",
        "evidence_manifest.json": "aff415aeb04bcbb7b5a071748567a3a44cb24bf4f70e154a8468707edfc459d6",
        "status.json": "9049629e8308fb90efaac1c32098da42aeec8794f603c446e0e910da50710a4c",
    },
    "runs/comprehensive-audit-remediation-003-perpetual-candidate-a-run-5b7c5dba7f8b": {
        "checker.json": "3c56bfc929d692bb0168dc458e7f2fb024d760bb1f51c123b2cf1edc251f14bd",
        "evidence_manifest.json": "db1a70e56b6e2df049ed2d3015b0fb609937667e29a33b2aa2fd34f7ded9353f",
        "status.json": "15e7f57463c14d5cd7f05edf80d7d8de308cfabfa5980c9d35b01ffe7d119c6e",
    },
    "runs/comprehensive-audit-remediation-003-perpetual-candidate-b-run-85bb3192f559": {
        "checker.json": "c4a5e4cbc98bb900f7d8ea1281a054f7ae6265262d425d03e1b5f7d61006dbc2",
        "evidence_manifest.json": "648b03680cfdb4b502ae3ce92452cddc4189d76c589154d1bf0c9f7a65f47169",
        "status.json": "64458d0c0eb2b76be4aa02bdcfa0fa6fa777cbd7f6e69ace86052f825cc87280",
    },
    "runs/comprehensive-audit-remediation-003-spot-benchmark-run-d3e25d52686e": {
        "checker.json": "9734d3e74fabbf2f36e55949a6bc0630975392ef40306120a5db3d3b2056ef57",
        "evidence_manifest.json": "4cacc105cb448cf86a6072d58b5834f1a6cf229b814c65260c05e8e98856e835",
        "status.json": "70b1f0c8b75e8239c218456b30706841cc143750f7bb4c0ec9c9acef856dc61d",
    },
    "runs/comprehensive-audit-remediation-003-spot-candidate-a-run-253086685e94": {
        "checker.json": "0cfb33a3dcb533776743e9627214120ed30a71a517165f060502b1105c8c5a4b",
        "evidence_manifest.json": "8b8628323f212ada7b68e439d56d08cb64f3c83d86942284752db17b6cfc41b7",
        "status.json": "2b61c45055cf90be9710832b4d501ab310231ef476a454b0a034a645bb3616ec",
    },
    "runs/comprehensive-audit-remediation-003-spot-candidate-b-run-736f07f7755e": {
        "checker.json": "47e6a42c080cda517248060cbdaf89b6dacef3f34506dee1386dd07a6fd8d17a",
        "evidence_manifest.json": "e3d1de388e7db1f4158a32a6fd2660186c60e69943c4143a71e196249b4faa1c",
        "status.json": "8ed046d4e5258b010976236fa0169b20c6230d5ae731280d0aa70bdc64c9c77e",
    },
    "runs/replays/comprehensive-audit-remediation-003-perpetual-benchmark-buy-and-hold-1x-development/comprehensive-audit-remediation-003-perpetual-benchmark-run-a0e2b2553ed4": {
        "checker.json": "7f6d80cf661354b05f7c3f140fa8e5a3fcd0f7f5287a705fb95d399d9b9b9112",
        "evidence_manifest.json": "0a1635b851486558adb77bfcd7e93b20018547f9d0c61f8295ca7fc7f6eb6252",
        "status.json": "9049629e8308fb90efaac1c32098da42aeec8794f603c446e0e910da50710a4c",
    },
    "runs/replays/comprehensive-audit-remediation-003-perpetual-candidate-a-development/comprehensive-audit-remediation-003-perpetual-candidate-a-run-5b7c5dba7f8b": {
        "checker.json": "3c56bfc929d692bb0168dc458e7f2fb024d760bb1f51c123b2cf1edc251f14bd",
        "evidence_manifest.json": "bebd892164f03390818c63d74ab1797e11f420c5020d1afc244b1221210d3dcc",
        "status.json": "15e7f57463c14d5cd7f05edf80d7d8de308cfabfa5980c9d35b01ffe7d119c6e",
    },
    "runs/replays/comprehensive-audit-remediation-003-perpetual-candidate-b-development/comprehensive-audit-remediation-003-perpetual-candidate-b-run-85bb3192f559": {
        "checker.json": "c4a5e4cbc98bb900f7d8ea1281a054f7ae6265262d425d03e1b5f7d61006dbc2",
        "evidence_manifest.json": "ff407b7fe260a37ccc049b21ca350156d2512529cbf9cc799a6c2e922eeac6a0",
        "status.json": "64458d0c0eb2b76be4aa02bdcfa0fa6fa777cbd7f6e69ace86052f825cc87280",
    },
    "runs/replays/comprehensive-audit-remediation-003-spot-benchmark-buy-and-hold-1x-development/comprehensive-audit-remediation-003-spot-benchmark-run-d3e25d52686e": {
        "checker.json": "9734d3e74fabbf2f36e55949a6bc0630975392ef40306120a5db3d3b2056ef57",
        "evidence_manifest.json": "eddc64cb6162e26a06fa4286dbf5ea61d9f074c3a3af5d828f77aafa56e7a1f8",
        "status.json": "70b1f0c8b75e8239c218456b30706841cc143750f7bb4c0ec9c9acef856dc61d",
    },
    "runs/replays/comprehensive-audit-remediation-003-spot-candidate-a-development/comprehensive-audit-remediation-003-spot-candidate-a-run-253086685e94": {
        "checker.json": "0cfb33a3dcb533776743e9627214120ed30a71a517165f060502b1105c8c5a4b",
        "evidence_manifest.json": "eb7843e47a560b4451ed355beb916a3731a7d71c78d3ca105540e59b2ad8d112",
        "status.json": "2b61c45055cf90be9710832b4d501ab310231ef476a454b0a034a645bb3616ec",
    },
    "runs/replays/comprehensive-audit-remediation-003-spot-candidate-b-development/comprehensive-audit-remediation-003-spot-candidate-b-run-736f07f7755e": {
        "checker.json": "47e6a42c080cda517248060cbdaf89b6dacef3f34506dee1386dd07a6fd8d17a",
        "evidence_manifest.json": "95cb7266399a57864971a01b7ce3369415d3eee6e49c0d5adc42b95258d687de",
        "status.json": "8ed046d4e5258b010976236fa0169b20c6230d5ae731280d0aa70bdc64c9c77e",
    },
}


class HistoricalResultStatusBuildError(ValueError):
    """Raised when immutable R2 status material cannot be proven."""


def _repository_root(value: Path) -> Path:
    lexical = Path(os.path.abspath(value))
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise HistoricalResultStatusBuildError("repository root is missing") from exc
    if lexical != resolved or lexical.is_symlink() or not lexical.is_dir():
        raise HistoricalResultStatusBuildError("repository root must be an exact directory")
    return lexical


def _source_commit(value: str) -> str:
    if _GIT_SHA.fullmatch(value) is None:
        raise HistoricalResultStatusBuildError(
            "source_commit must be an explicit lowercase 40-hex commit identity",
        )
    return value


def _recorded_at_utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise HistoricalResultStatusBuildError("recorded_at_utc must be explicit UTC ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise HistoricalResultStatusBuildError("recorded_at_utc is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise HistoricalResultStatusBuildError("recorded_at_utc must use UTC")
    return parsed


def _expected_paths() -> set[str]:
    return {
        material[key]
        for material in R2_EXPECTED_HISTORICAL_RESULTS.values()
        for key in ("primary_path", "replay_path")
    }


def build_registry(
    *,
    repository_root: Path,
    source_commit: str,
    recorded_at_utc: str,
) -> bytes:
    """Return canonical v2 registry bytes without writing them."""

    root = _repository_root(repository_root)
    source = _source_commit(source_commit)
    recorded = _recorded_at_utc(recorded_at_utc)
    expected_paths = _expected_paths()
    if (
        len(expected_paths) != 12
        or set(EXPECTED_HISTORICAL_EVIDENCE_HASHES) != expected_paths
    ):
        raise HistoricalResultStatusBuildError(
            "frozen evidence inventory does not equal the exact 12-result R2 scope",
        )

    records: list[dict[str, Any]] = []
    for logical_result_id, material in sorted(R2_EXPECTED_HISTORICAL_RESULTS.items()):
        result_class = HistoricalResultClass(material["result_class"])
        for copy_role, path_key in (
            (HistoricalCopyRole.PRIMARY, "primary_path"),
            (HistoricalCopyRole.REPLAY, "replay_path"),
        ):
            relative = material[path_key]
            try:
                record = build_historical_result_record_v2(
                    root / relative,
                    repository_root=root,
                    logical_result_id=logical_result_id,
                    market_profile=material["market_profile"],
                    result_class=result_class,
                    copy_role=copy_role,
                )
            except (OSError, ValueError) as exc:
                raise HistoricalResultStatusBuildError(
                    f"cannot bind immutable historical result {relative}: {exc}",
                ) from exc
            if record.get("path") != relative:
                raise HistoricalResultStatusBuildError(
                    f"historical result resolved to an unexpected path: {relative}",
                )
            if record.get("evidence_hashes") != EXPECTED_HISTORICAL_EVIDENCE_HASHES[relative]:
                raise HistoricalResultStatusBuildError(
                    f"historical evidence identity mismatch: {relative}",
                )
            records.append(record)

    try:
        return build_historical_result_registry_v2(
            records,
            authority_id=R2_RESULT_STATUS_AUTHORITY,
            audited_baseline_commit=R2_AUDITED_BASELINE_COMMIT,
            source_commit=source,
            recorded_at_utc=recorded,
        )
    except ValueError as exc:
        raise HistoricalResultStatusBuildError(
            f"v2 registry contract rejected generated material: {exc}",
        ) from exc


def _open_output_parent(repository_root: Path) -> int:
    """Open/create the fixed output parent without traversing a symlink."""

    try:
        descriptor = os.open(repository_root, _DIRECTORY_FLAGS)
    except OSError as exc:
        raise HistoricalResultStatusBuildError(
            f"cannot open repository root safely: {exc}",
        ) from exc
    try:
        for component in OUTPUT_RELATIVE_PATH.parent.parts:
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                try:
                    os.mkdir(component, mode=0o755, dir_fd=descriptor)
                    os.fsync(descriptor)
                    child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
                except OSError as exc:
                    raise HistoricalResultStatusBuildError(
                        f"cannot create output directory safely: {component}: {exc}",
                    ) from exc
            except OSError as exc:
                raise HistoricalResultStatusBuildError(
                    f"output directory is not an exact directory: {component}: {exc}",
                ) from exc
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def write_fresh_registry(*, repository_root: Path, payload: bytes) -> Path:
    """Durably create the fixed output once; never replace an existing entry."""

    root = _repository_root(repository_root)
    parent_descriptor = _open_output_parent(root)
    target_name = OUTPUT_RELATIVE_PATH.name
    temporary_name = f".{target_name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    temporary_descriptor: int | None = None
    linked = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        temporary_descriptor = os.open(
            temporary_name,
            flags,
            0o600,
            dir_fd=parent_descriptor,
        )
        view = memoryview(payload)
        while view:
            written = os.write(temporary_descriptor, view)
            if written <= 0:
                raise HistoricalResultStatusBuildError("short write while creating registry")
            view = view[written:]
        os.fchmod(temporary_descriptor, 0o644)
        os.fsync(temporary_descriptor)
        os.close(temporary_descriptor)
        temporary_descriptor = None
        try:
            os.link(
                temporary_name,
                target_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            linked = True
        except FileExistsError as exc:
            raise HistoricalResultStatusBuildError(
                f"immutable output collision: {OUTPUT_RELATIVE_PATH.as_posix()}",
            ) from exc
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                detail = "output target is a symlink"
            else:
                detail = str(exc)
            raise HistoricalResultStatusBuildError(
                f"cannot publish immutable registry: {detail}",
            ) from exc
        os.fsync(parent_descriptor)
        os.unlink(temporary_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        return root / OUTPUT_RELATIVE_PATH
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        if not linked:
            # No target was published; only the private temporary name existed.
            os.fsync(parent_descriptor)
        os.close(parent_descriptor)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--recorded-at-utc", required=True)
    arguments = parser.parse_args(argv)

    payload = build_registry(
        repository_root=arguments.repository,
        source_commit=arguments.source_commit,
        recorded_at_utc=arguments.recorded_at_utc,
    )
    output = write_fresh_registry(
        repository_root=arguments.repository,
        payload=payload,
    )
    value = json.loads(payload)
    print(
        json.dumps(
            {
                "output": output.relative_to(_repository_root(arguments.repository)).as_posix(),
                "record_count": value["record_count"],
                "registry_identity": value["registry_identity"],
                "source_commit": value["source_commit"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
