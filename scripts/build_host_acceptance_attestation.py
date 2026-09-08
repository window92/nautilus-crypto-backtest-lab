#!/usr/bin/env python3
"""Build a Host Acceptance attestation only from verified committed evidence."""
from __future__ import annotations

import argparse
from pathlib import Path

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.host_acceptance import build_host_acceptance_attestation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--acceptance-ref', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.output.is_absolute() or args.output.exists() or args.output.is_symlink():
        raise ValueError('attestation output must be a fresh explicit absolute path')
    payload = build_host_acceptance_attestation(args.repository, acceptance_ref=args.acceptance_ref)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as stream:
        stream.write(canonical_json_bytes(payload) + b'\n')
    print(payload['attestation_identity'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
