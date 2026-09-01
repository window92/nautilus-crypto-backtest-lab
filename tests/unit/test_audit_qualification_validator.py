from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from scripts.validate_audit_qualification import validate
from scripts.validate_m3_evidence import validate as validate_m3


class AuditQualificationValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = Path(__file__).resolve().parents[2]
        self.evidence = (
            self.repository
            / "evidence/audit/comprehensive-remediation-001/qualification-runtime-proof"
        )
        self.r2_evidence = (
            self.repository
            / "evidence/audit/adversarial-remediation-002/qualification-retry-007"
        )

    @staticmethod
    def _rebind_manifest(root: Path, relative: str) -> None:
        manifest_path = root / "qualification-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        target = root / relative
        for item in manifest["entries"]:
            if item["path"] == relative:
                item["byte_size"] = target.stat().st_size
                item["sha256"] = sha256_file(target)
                break
        else:
            raise AssertionError(f"manifest does not contain {relative}")
        manifest["content_sha256"] = canonical_sha256(manifest["entries"])
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    def test_r2_current_qualification_uses_current_component_contract(self) -> None:
        result = validate_m3(self.r2_evidence)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["contract_mode"], "R2_CURRENT")
        self.assertTrue(result["checks"]["current_checker_revalidation"])
        self.assertTrue(result["checks"]["downstream_v2_bindings"])

    def test_r2_rehashed_legacy_vocabulary_and_downstream_tamper_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "qualification"
            shutil.copytree(self.r2_evidence, copied)
            relative = "downstream/BINANCE_SPOT_CASH_LONG_ONLY.json"
            path = copied / relative
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["run_result"]["component_validation_outcome"] = "CHECK_PASS"
            path.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            self._rebind_manifest(copied, relative)
            result = validate_m3(copied)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["checks"]["downstream_v2_bindings"])

    def test_r2_rehashed_negative_control_legacy_outcome_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "qualification"
            shutil.copytree(self.r2_evidence, copied)
            relative = "negative-controls.json"
            path = copied / relative
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["DUPLICATE_FUNDING_SETTLEMENT"]["checker"]["outcome"] = "CHECK_FAIL"
            path.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            self._rebind_manifest(copied, relative)
            result = validate_m3(copied)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["checks"]["negative_controls"])

    def test_r2_rehashed_source_branch_mismatch_fails_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "qualification"
            shutil.copytree(self.r2_evidence, copied)
            relative = "baseline.json"
            path = copied / relative
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["branch"] = "forged/source-branch"
            path.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            self._rebind_manifest(copied, relative)
            result = validate_m3(copied)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["checks"]["source_revision_bindings"])

    def test_r2_extra_symlink_fails_complete_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "qualification"
            shutil.copytree(self.r2_evidence, copied)
            (copied / "undeclared-link").symlink_to("acceptance-summary.json")
            result = validate_m3(copied)
            self.assertEqual(result["status"], "FAIL")
            self.assertFalse(result["checks"]["complete_content_addressed_inventory"])

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
