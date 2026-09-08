"""Committed ACTIVE execution-plan pointer. Historical plans are not current."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from crypto_lab.git_identity import require_repository_root
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.status import FailureCode


POINTER_SCHEMA = "adversarial-remediation-002-active-execution-plan-pointer-v1"
PLAN_SCHEMA = "adversarial-remediation-002-official-run-plan-v1"
ACTIVE_POINTER_RELATIVE = Path(
    "evidence/audit/adversarial-remediation-002/execution-plans/ACTIVE.json",
)
HISTORICAL_RETRY_006_PLAN = Path(
    "evidence/audit/adversarial-remediation-002/execution-plan.json",
)


class ExecutionPlanError(ValueError):
    """The ACTIVE pointer or bound plan is missing, stale, or wrongly bound."""

    def __init__(self, code: FailureCode, detail: str) -> None:
        super().__init__(f"{code.value}: {detail}")
        self.code = code
        self.detail = detail


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            f"{label} must not be a symlink",
        )
    if not path.is_file():
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            f"{label} is missing",
        )
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            f"{label} is not readable JSON",
        ) from exc
    if not isinstance(value, dict):
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            f"{label} is not an object",
        )
    if payload != canonical_json_bytes(value) + b"\n":
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            f"{label} is not canonical JSON",
        )
    return value


def validate_active_pointer(pointer: dict[str, Any], plan: dict[str, Any]) -> None:
    """Fail closed on stale, wrongly hashed, or wrongly bound current plans."""

    expected_fields = {
        "schema",
        "status",
        "plan_ref",
        "plan_identity",
        "epoch",
        "workflow_bindings",
        "historical_plans",
    }
    if set(pointer) != expected_fields or pointer.get("schema") != POINTER_SCHEMA:
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            "active execution-plan pointer schema differs",
        )
    if pointer.get("status") != "CURRENT":
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            "active execution-plan pointer is stale",
        )
    material = dict(plan)
    declared = material.pop("plan_identity", None)
    if (
        plan.get("schema") != PLAN_SCHEMA
        or declared != canonical_sha256(material)
        or declared != pointer.get("plan_identity")
        or plan.get("epoch") != pointer.get("epoch")
    ):
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            "active execution-plan hash or epoch differs",
        )
    bindings = pointer.get("workflow_bindings")
    execution = plan.get("execution")
    if not isinstance(bindings, list) or not isinstance(execution, list):
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            "active execution-plan workflow binding is missing",
        )
    actual = [item.get("trial_id") for item in execution if isinstance(item, dict)]
    if bindings != actual:
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            "active execution-plan workflow binding differs",
        )
    historical = pointer.get("historical_plans")
    if not isinstance(historical, list) or not historical:
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            "historical execution plans must remain enumerated",
        )
    if any(
        not isinstance(item, dict)
        or set(item) != {"plan_ref", "plan_identity", "epoch", "status"}
        or item.get("status") != "HISTORICAL"
        or not isinstance(item.get("plan_identity"), str)
        or re.fullmatch(r"[0-9a-f]{64}", item["plan_identity"]) is None
        or item.get("epoch") == pointer.get("epoch")
        or item.get("plan_ref") == pointer.get("plan_ref")
        for item in historical
    ):
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            "historical execution plans must not be marked CURRENT",
        )
    if len({item["epoch"] for item in historical}) != len(historical):
        raise ExecutionPlanError(FailureCode.RESEARCH_PROTOCOL_INVALID, "duplicate historical epoch")


def _committed_plan_file(root: Path, reference: str, *, label: str) -> dict[str, Any]:
    relative = Path(reference)
    if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != reference:
        raise ExecutionPlanError(FailureCode.RESEARCH_PROTOCOL_INVALID, f"unsafe {label} ref")
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ExecutionPlanError(FailureCode.RESEARCH_PROTOCOL_INVALID, f"symlink in {label}")
    value = _read_json(cursor, label=label)
    committed = subprocess.run(
        ["git", "--no-replace-objects", "show", f"HEAD:{reference}"],
        cwd=root, capture_output=True, check=False,
    )
    if committed.returncode != 0 or committed.stdout != cursor.read_bytes():
        raise ExecutionPlanError(FailureCode.RESEARCH_PROTOCOL_INVALID, f"{label} is not committed")
    return value


def load_active_execution_plan(repository_root: Path) -> dict[str, Any]:
    """Resolve the committed ACTIVE pointer. Never rglob /tmp or historical plans."""

    root = require_repository_root(repository_root)
    pointer = _committed_plan_file(root, ACTIVE_POINTER_RELATIVE.as_posix(),
                                   label="active execution-plan pointer")
    plan_ref = pointer.get("plan_ref")
    if not isinstance(plan_ref, str) or plan_ref.startswith("/") or ".." in Path(plan_ref).parts:
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            "active execution-plan ref is unsafe",
        )
    plan_path = root / plan_ref
    try:
        plan_path.relative_to(root)
    except ValueError as exc:
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            "active execution-plan ref escapes the repository",
        ) from exc
    if plan_path.resolve() == (root / HISTORICAL_RETRY_006_PLAN).resolve() and pointer.get(
        "status",
    ) == "CURRENT":
        raise ExecutionPlanError(
            FailureCode.RESEARCH_PROTOCOL_INVALID,
            "retry-006 historical plan is not the current ACTIVE plan",
        )
    if "historical" in Path(plan_ref).parts:
        raise ExecutionPlanError(FailureCode.RESEARCH_PROTOCOL_INVALID, "historical plan cannot be ACTIVE")
    plan = _committed_plan_file(root, plan_ref, label="active execution plan")
    validate_active_pointer(pointer, plan)
    for binding in pointer["historical_plans"]:
        historical = _committed_plan_file(root, binding["plan_ref"], label="historical execution plan")
        material = dict(historical)
        identity = material.pop("plan_identity", None)
        if (identity != binding["plan_identity"] or identity != canonical_sha256(material)
                or historical.get("epoch") != binding["epoch"]):
            raise ExecutionPlanError(FailureCode.RESEARCH_PROTOCOL_INVALID,
                                     "historical execution-plan identity differs")
    return {
        "pointer": pointer,
        "plan": plan,
        "plan_path": plan_path.relative_to(root).as_posix(),
    }


__all__ = [
    "ACTIVE_POINTER_RELATIVE",
    "ExecutionPlanError",
    "HISTORICAL_RETRY_006_PLAN",
    "load_active_execution_plan",
    "validate_active_pointer",
]
