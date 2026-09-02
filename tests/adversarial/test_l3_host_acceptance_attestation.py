from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.host_acceptance import ATTESTATION_RELATIVE
from crypto_lab.host_acceptance import ATTESTATION_SCHEMA
from crypto_lab.host_acceptance import build_host_acceptance_attestation
from crypto_lab.host_acceptance import product_source_identity
from crypto_lab.host_acceptance import verify_host_acceptance_attestation


ROOT = Path(__file__).resolve().parents[2]


class HostAcceptanceAttestationTests(unittest.TestCase):
    def test_product_source_identity_is_stable_and_excludes_attestation_bytes(self) -> None:
        first = product_source_identity(ROOT)
        second = product_source_identity(ROOT)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_material_edit_invalidates_attestation(self) -> None:
        attestation = build_host_acceptance_attestation(
            ROOT,
            data_identities={"official_active_raw_object_count": 2231},
            acceptance={"runner": "scripts/run_adversarial_remediation_002_acceptance.py"},
        )
        self.assertEqual(attestation["schema"], ATTESTATION_SCHEMA)
        self.assertFalse(attestation["portable_ci_is_official_acceptance"])
        mutated = dict(attestation)
        mutated["product_source_identity"] = "0" * 64
        material = dict(mutated)
        material.pop("attestation_identity")
        mutated["attestation_identity"] = canonical_sha256(material)
        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary) / ATTESTATION_RELATIVE
            fake.parent.mkdir(parents=True)
            fake.write_bytes(canonical_json_bytes(mutated) + b"\n")
            with mock.patch(
                "crypto_lab.host_acceptance.ATTESTATION_RELATIVE",
                ATTESTATION_RELATIVE,
            ), mock.patch(
                "crypto_lab.host_acceptance.load_attestation",
                return_value=mutated,
            ):
                with self.assertRaisesRegex(ValueError, "stale"):
                    verify_host_acceptance_attestation(ROOT, portable_only=True)

    def test_missing_attestation_fails_closed(self) -> None:
        with mock.patch(
            "crypto_lab.host_acceptance.load_attestation",
            side_effect=FileNotFoundError("host acceptance attestation is missing"),
        ):
            with self.assertRaises(FileNotFoundError):
                verify_host_acceptance_attestation(ROOT, portable_only=True)


if __name__ == "__main__":
    unittest.main()
