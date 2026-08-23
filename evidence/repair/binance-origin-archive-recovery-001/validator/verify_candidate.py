#!/usr/bin/env python3
"""Generate and independently round-trip Candidate 003 without touching root SSOT."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path


EXPECTED_BASE_SHA256 = "f51971ed7a09b172c82ff5965f2899d2a302dd71a2af60eb7c920133567b4354"
EXPECTED_HEAD = "f379a411bfd45ee566fd99d72ea402776ed48a85"
PHASE = Path("evidence/repair/binance-origin-archive-recovery-001")
CANDIDATE_REL = PHASE / "ssot-candidate-003/SSOT.candidate-003.md"
DIFF_REL = PHASE / "ssot-candidate-003/SSOT.candidate-003.diff"
SHA_REL = PHASE / "ssot-candidate-003/SSOT.candidate-003.sha256"
ROUND_TRIP_REL = PHASE / "ssot-candidate-003/round-trip-verification.json"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def run(
    argv: list[str],
    *,
    cwd: Path,
    expected: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(argv, cwd=cwd, check=False, capture_output=True)
    if completed.returncode not in expected:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {argv!r}\n"
            f"stdout={completed.stdout.decode('utf-8', 'replace')}\n"
            f"stderr={completed.stderr.decode('utf-8', 'replace')}"
        )
    return completed


def validate_text(name: str, raw: bytes) -> dict[str, object]:
    decoded = raw.decode("utf-8")
    return {
        "path_role": name,
        "utf8_valid": True,
        "lf_only": b"\r" not in raw,
        "final_lf": raw.endswith(b"\n"),
        "line_count": len(decoded.splitlines()),
        "size_bytes": len(raw),
        "sha256": sha256_bytes(raw),
    }


def one_round_trip(repo: Path, patch: Path, ordinal: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix=f"candidate-003-roundtrip-{ordinal}-") as raw_tmp:
        temp_root = Path(raw_tmp)
        checkout = temp_root / "checkout"
        run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(repo), str(checkout)],
            cwd=temp_root,
        )
        head = run(["git", "rev-parse", "HEAD"], cwd=checkout).stdout.decode().strip()
        branch = run(["git", "branch", "--show-current"], cwd=checkout).stdout.decode().strip()
        status_before = run(["git", "status", "--porcelain"], cwd=checkout).stdout.decode()
        base_before = (checkout / "SSOT.md").read_bytes()

        check_forward = run(
            ["git", "apply", "--check", "--verbose", "--whitespace=error-all", str(patch)],
            cwd=checkout,
        )
        apply_forward = run(
            ["git", "apply", "--verbose", "--whitespace=error-all", str(patch)],
            cwd=checkout,
        )
        forward = (checkout / "SSOT.md").read_bytes()
        diff_check = run(["git", "diff", "--check"], cwd=checkout)

        check_reverse = run(
            ["git", "apply", "--reverse", "--check", "--verbose", str(patch)],
            cwd=checkout,
        )
        apply_reverse = run(
            ["git", "apply", "--reverse", "--verbose", str(patch)],
            cwd=checkout,
        )
        reverse = (checkout / "SSOT.md").read_bytes()
        status_after = run(["git", "status", "--porcelain"], cwd=checkout).stdout.decode()

        diagnostics = b"\n".join(
            (
                check_forward.stdout,
                check_forward.stderr,
                apply_forward.stdout,
                apply_forward.stderr,
                check_reverse.stdout,
                check_reverse.stderr,
                apply_reverse.stdout,
                apply_reverse.stderr,
            )
        ).decode("utf-8", "replace")
        lower_diagnostics = diagnostics.lower()
        no_fuzz_or_offset = "fuzz" not in lower_diagnostics and "offset" not in lower_diagnostics

        return {
            "process_ordinal": ordinal,
            "clean_checkout_before_apply": status_before == "",
            "clean_checkout_after_reverse": status_after == "",
            "checkout_head": head,
            "checkout_branch": branch,
            "checkout_head_matches_expected": head == EXPECTED_HEAD,
            "base_before_sha256": sha256_bytes(base_before),
            "forward_result_sha256": sha256_bytes(forward),
            "reverse_result_sha256": sha256_bytes(reverse),
            "forward_apply_check_passed": check_forward.returncode == 0,
            "forward_apply_passed": apply_forward.returncode == 0,
            "reverse_apply_check_passed": check_reverse.returncode == 0,
            "reverse_apply_passed": apply_reverse.returncode == 0,
            "git_diff_check_passed": diff_check.returncode == 0,
            "no_fuzz_or_offset_reported": no_fuzz_or_offset,
            "apply_diagnostics": diagnostics.strip().splitlines(),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    repo = args.repo.resolve()

    base_path = repo / "SSOT.md"
    candidate_path = repo / CANDIDATE_REL
    patch_path = repo / DIFF_REL
    sha_path = repo / SHA_REL
    round_trip_path = repo / ROUND_TRIP_REL

    base = base_path.read_bytes()
    candidate = candidate_path.read_bytes()
    base_text = validate_text("base_ssot", base)
    candidate_text = validate_text("complete_candidate_ssot", candidate)
    if base_text["sha256"] != EXPECTED_BASE_SHA256:
        raise RuntimeError(f"base SSOT identity mismatch: {base_text['sha256']}")
    if not all(base_text[key] for key in ("utf8_valid", "lf_only", "final_lf")):
        raise RuntimeError("base SSOT encoding/newline validation failed")
    if not all(candidate_text[key] for key in ("utf8_valid", "lf_only", "final_lf")):
        raise RuntimeError("candidate encoding/newline validation failed")
    if base == candidate:
        raise RuntimeError("candidate is byte-identical to base")

    generated = run(
        [
            "diff",
            "-u",
            "--label",
            "a/SSOT.md",
            "--label",
            "b/SSOT.md",
            str(base_path),
            str(candidate_path),
        ],
        cwd=repo,
        expected=(1,),
    ).stdout
    patch_path.write_bytes(generated)
    sha_path.write_text(
        f"{candidate_text['sha256']}  SSOT.candidate-003.md\n",
        encoding="utf-8",
        newline="\n",
    )

    first = one_round_trip(repo, patch_path, 1)
    second = one_round_trip(repo, patch_path, 2)
    for result in (first, second):
        required_true = (
            "clean_checkout_before_apply",
            "clean_checkout_after_reverse",
            "checkout_head_matches_expected",
            "forward_apply_check_passed",
            "forward_apply_passed",
            "reverse_apply_check_passed",
            "reverse_apply_passed",
            "git_diff_check_passed",
            "no_fuzz_or_offset_reported",
        )
        if not all(result[key] for key in required_true):
            raise RuntimeError(f"round-trip process {result['process_ordinal']} failed")
        if result["base_before_sha256"] != EXPECTED_BASE_SHA256:
            raise RuntimeError("temporary checkout base mismatch")
        if result["forward_result_sha256"] != candidate_text["sha256"]:
            raise RuntimeError("forward result differs from complete candidate")
        if result["reverse_result_sha256"] != EXPECTED_BASE_SHA256:
            raise RuntimeError("reverse result differs from base")

    if sha256_bytes(base_path.read_bytes()) != EXPECTED_BASE_SHA256:
        raise RuntimeError("root SSOT changed during validation")

    payload = {
        "contract": "DATA_PROVENANCE_SSOT_CANDIDATE_003_ROUND_TRIP_V1",
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "base": base_text,
        "candidate": candidate_text,
        "patch": {
            "path": DIFF_REL.as_posix(),
            "size_bytes": len(generated),
            "sha256": sha256_bytes(generated),
            "unified_diff_generated_automatically": True,
        },
        "independent_process_count": 2,
        "processes": [first, second],
        "forward_application_result": "PASS",
        "reverse_round_trip_result": "PASS",
        "candidate_bytes_match_forward_result": True,
        "base_bytes_match_reverse_result": True,
        "no_fuzz_or_offset": True,
        "root_ssot_preserved": True,
        "status": "PASS",
    }
    round_trip_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(payload["patch"], sort_keys=True))
    print(json.dumps(candidate_text, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
