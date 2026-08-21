from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SPOT_CONFIG_FIXTURE = ROOT / "tests/golden/fixtures/spot-lab-run-config.json"
SOURCE_REVISION_FIXTURE = ROOT / "tests/golden/fixtures/source-revision.json"


def load_spot_config_dict() -> dict[str, Any]:
    return json.loads(SPOT_CONFIG_FIXTURE.read_text(encoding="utf-8"))


def load_source_revision_dict() -> dict[str, Any]:
    return json.loads(SOURCE_REVISION_FIXTURE.read_text(encoding="utf-8"))


def encode_config(data: dict[str, Any]) -> bytes:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
