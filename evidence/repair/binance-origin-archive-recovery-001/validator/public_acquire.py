#!/usr/bin/env python3
"""Acquire a public, credential-free HTTP object before any parsing.

This evidence-only helper never accepts authorization headers, query secrets, or
signed URLs. It preserves the response body first, then records its identity and
the response metadata needed by BINANCE_ORIGIN_ARCHIVE_RECOVERY_001.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request


FORBIDDEN_QUERY_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "signature",
    "token",
    "x-api-key",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("url")
    parser.add_argument("output_dir", type=pathlib.Path)
    parser.add_argument("--accept-encoding", choices=("identity", "gzip"), default="identity")
    args = parser.parse_args()

    parsed = urllib.parse.urlsplit(args.url)
    query_keys = {key.lower() for key, _ in urllib.parse.parse_qsl(parsed.query)}
    if parsed.scheme != "https" or query_keys & FORBIDDEN_QUERY_KEYS:
        raise SystemExit("only public HTTPS URLs without credential query keys are allowed")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    body_path = args.output_dir / f"{args.slug}.body"
    headers_path = args.output_dir / f"{args.slug}.headers.json"
    observation_path = args.output_dir / f"{args.slug}.observation.json"
    if body_path.exists() or headers_path.exists() or observation_path.exists():
        raise SystemExit(f"refusing to overwrite existing acquisition: {args.slug}")

    request_headers = {"User-Agent": "nautilus-data-provenance-qualification/1.0"}
    if args.accept_encoding != "identity":
        request_headers["Accept-Encoding"] = args.accept_encoding
    request = urllib.request.Request(args.url, headers=request_headers, method="GET")
    captured_at = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    try:
        response = urllib.request.urlopen(request, timeout=60)
        status = response.status
        final_url = response.geturl()
        headers = list(response.headers.items())
        body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        final_url = exc.geturl()
        headers = list(exc.headers.items())
        body = exc.read()

    # The response bytes are durably written before any caller is allowed to parse.
    body_path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    headers_path.write_text(
        json.dumps({"headers": headers}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    observation = {
        "capture_timestamp_utc": captured_at,
        "request_method": "GET",
        "request_url": args.url,
        "request_accept_encoding": args.accept_encoding,
        "final_url": final_url,
        "status_code": status,
        "body_path": str(body_path),
        "body_size_bytes": len(body),
        "body_sha256": digest,
        "headers_path": str(headers_path),
        "credentials_used": False,
        "parsed_before_body_saved": False,
    }
    observation_path.write_text(
        json.dumps(observation, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"slug": args.slug, "status_code": status, "size": len(body), "sha256": digest}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
