from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validate_audit_qualification import validate


class AuditQualificationValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = Path(__file__).resolve().parents[2]
        self.evidence = (
            self.repository
            / "evidence/audit/comprehensive-remediation-001/qualification-runtime-proof"
        )

    def test_legacy_runtime_proof_is_preserved_but_warmup_affected_qualification_fails(self) -> None:
        result = validate(self.evidence)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["checks"]["current_checker_revalidation"])
        self.assertTrue(result["checks"]["persisted_runtime_payload_proof"])
        self.assertEqual(len(result["runtime_proof_revalidations"]), 4)
        failure_codes = {
            code
            for item in result["checker_revalidations"].values()
            for code in item["failure_codes"]
        }
        self.assertIn("LOOKAHEAD_DETECTED", failure_codes)

    def test_runtime_proof_and_manifest_tamper_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "qualification"
            shutil.copytree(self.evidence, copied)
            identities = tuple(copied.glob("runs/spot-primary/*/runtime_identity.json"))
            self.assertEqual(len(identities), 1)
            identity = identities[0]
            payload = json.loads(identity.read_text(encoding="utf-8"))
            payload["installed_payload_sha256"] = "0" * 64
            identity.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
            result = validate(copied)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["checks"]["complete_content_addressed_inventory"])
            self.assertFalse(result["checks"]["persisted_runtime_payload_proof"])


if __name__ == "__main__":
    unittest.main()
