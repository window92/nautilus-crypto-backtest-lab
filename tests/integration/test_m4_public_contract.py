from __future__ import annotations

import ast
import unittest
from pathlib import Path

import crypto_lab
from crypto_lab.hashing import sha256_file
from crypto_lab.m3 import QualificationDownstreamBundle
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.research import M3ResearchBoundary
from crypto_lab.research import ResearchEligibility


ROOT = Path(__file__).resolve().parents[2]
M3_EVIDENCE = ROOT / "evidence/m3/m3-acceptance-001"


class M4PublicContractTests(unittest.TestCase):
    def test_m4_consumes_exact_m3_registry_and_bundles_without_mutation(self) -> None:
        registry_path = M3_EVIDENCE / "qualified-profile-registry.json"
        before = {
            path: sha256_file(path)
            for path in (registry_path, *sorted((M3_EVIDENCE / "downstream").glob("*.json")))
        }
        boundary = M3ResearchBoundary.load(
            registry_path=registry_path,
            downstream_directory=M3_EVIDENCE / "downstream",
            expected_registry_identity="d6124dd7d225818f0de212d74f7d4aae5e3bf08c9f8ff342435baac6228ba6de",
        )
        self.assertEqual(len(boundary.bundles), 2)
        self.assertEqual(
            tuple(bundle.profile_record.qualification_state.value for bundle in boundary.bundles),
            ("QUALIFIED", "QUALIFIED"),
        )
        self.assertTrue(all(
            bundle.claim_evaluation.research_eligibility is ResearchEligibility.INELIGIBLE
            for bundle in boundary.bundles
        ))
        after = {path: sha256_file(path) for path in before}
        self.assertEqual(before, after)

    def test_public_shapes_parse_without_defaults_or_internal_m3_imports(self) -> None:
        for public_name in (
            "ResearchProtocol",
            "TrialRecord",
            "HoldoutLockStore",
            "PerformanceDiagnostics",
            "ClaimEvaluation",
            "ReportInput",
            "ReportOutput",
        ):
            self.assertTrue(hasattr(crypto_lab, public_name), public_name)
        registry = QualifiedProfileRegistry.from_json_bytes(
            (M3_EVIDENCE / "qualified-profile-registry.json").read_bytes(),
        )
        for record in registry.records:
            bundle = QualificationDownstreamBundle.from_json_bytes(
                (M3_EVIDENCE / "downstream" / f"{record.profile_id.value}.json").read_bytes(),
            )
            self.assertEqual(bundle.profile_record, record)
        source = (ROOT / "src/crypto_lab/research.py").read_text(encoding="utf-8")
        imports = [
            node for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        forbidden = ("scripts.", "crypto_lab.runner", "crypto_lab.data")
        rendered = "\n".join(ast.unparse(node) for node in imports)
        self.assertFalse(any(item in rendered for item in forbidden), rendered)

    def test_dependency_direction_has_no_reporting_to_execution_path(self) -> None:
        reporting = (ROOT / "src/crypto_lab/reporting.py").read_text(encoding="utf-8")
        tree = ast.parse(reporting)
        rendered = "\n".join(
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        )
        for forbidden in (
            "crypto_lab.runner",
            "crypto_lab.data",
            "nautilus_trader.backtest",
            "nautilus_trader.execution",
        ):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
