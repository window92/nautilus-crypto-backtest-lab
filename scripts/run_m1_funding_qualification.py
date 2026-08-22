from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from crypto_lab.m1_qualification import qualify_native_perpetual_funding


ROOT = Path(__file__).resolve().parents[1]
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "evidence/m1/native-funding-v2-qualification.json",
    )
    args = parser.parse_args()
    result = qualify_native_perpetual_funding()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
