from __future__ import annotations

import unittest

from crypto_lab.execution_plan import ExecutionPlanError
from crypto_lab.execution_plan import validate_active_pointer
from crypto_lab.hashing import canonical_sha256
from crypto_lab.status import FailureCode


def _plan(*, trial_ids: list[str], epoch: str = "adversarial-remediation-002-retry-012") -> dict:
    material = {
        "schema": "adversarial-remediation-002-official-run-plan-v1",
        "epoch": epoch,
        "execution": [{"trial_id": item} for item in trial_ids],
    }
    payload = dict(material)
    payload["plan_identity"] = canonical_sha256(material)
    return payload


def _pointer(plan: dict, *, status: str = "CURRENT", bindings=None, identity=None) -> dict:
    return {
        "schema": "adversarial-remediation-002-active-execution-plan-pointer-v1",
        "status": status,
        "plan_ref": "evidence/audit/adversarial-remediation-002/execution-plans/current/execution-plan.json",
        "plan_identity": identity or plan["plan_identity"],
        "epoch": plan["epoch"],
        "workflow_bindings": bindings or [item["trial_id"] for item in plan["execution"]],
        "historical_plans": [
            {
                "plan_ref": "evidence/audit/adversarial-remediation-002/execution-plan.json",
                "epoch": "adversarial-remediation-002-retry-006",
                "status": "HISTORICAL",
                "plan_identity": "c43341484ce90c4cba19b6158ef89d563f14faa45399633958aa60d487066e66",
            },
        ],
    }


class ActiveExecutionPlanPointerTests(unittest.TestCase):
    def test_current_pointer_accepts_matching_plan(self) -> None:
        plan = _plan(trial_ids=["a", "b", "c", "d", "e", "f"])
        validate_active_pointer(_pointer(plan), plan)

    def test_missing_wrong_hash_wrong_binding_and_stale_pointer_fail(self) -> None:
        plan = _plan(trial_ids=["a", "b", "c", "d", "e", "f"])
        with self.assertRaises(ExecutionPlanError) as stale:
            validate_active_pointer(_pointer(plan, status="STALE"), plan)
        self.assertEqual(stale.exception.code, FailureCode.RESEARCH_PROTOCOL_INVALID)
        self.assertIn("stale", stale.exception.detail)

        with self.assertRaises(ExecutionPlanError) as hashed:
            validate_active_pointer(_pointer(plan, identity="0" * 64), plan)
        self.assertEqual(hashed.exception.code, FailureCode.RESEARCH_PROTOCOL_INVALID)
        self.assertIn("hash", hashed.exception.detail)

        with self.assertRaises(ExecutionPlanError) as binding:
            validate_active_pointer(
                _pointer(plan, bindings=["wrong"] * 6),
                plan,
            )
        self.assertEqual(binding.exception.code, FailureCode.RESEARCH_PROTOCOL_INVALID)
        self.assertIn("workflow binding", binding.exception.detail)

        with self.assertRaises(ExecutionPlanError) as missing:
            validate_active_pointer(
                {
                    "schema": "adversarial-remediation-002-active-execution-plan-pointer-v1",
                    "status": "CURRENT",
                },
                plan,
            )
        self.assertEqual(missing.exception.code, FailureCode.RESEARCH_PROTOCOL_INVALID)


if __name__ == "__main__":
    unittest.main()
