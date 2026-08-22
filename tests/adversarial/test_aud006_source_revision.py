from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from crypto_lab.git_identity import GitIdentityError
from crypto_lab.git_identity import capture_actual_source_revision
from crypto_lab.git_identity import verify_source_revision


ROOT = Path(__file__).resolve().parents[2]


class Aud006SourceRevisionTests(unittest.TestCase):
    def _repository(self, root: Path) -> Path:
        subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Repair Test"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "repair@example.invalid"], check=True)
        subprocess.run(
            ["git", "-C", str(root), "remote", "add", "origin", "https://example.invalid/exact.git"],
            check=True,
        )
        (root / "tracked.txt").write_text("source\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-m", "source"], check=True, capture_output=True)
        return root

    def test_forged_repository_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._repository(Path(temporary))
            actual = capture_actual_source_revision(root)
            forged = replace(actual, repository="https://example.invalid/forged.git")
            with self.assertRaises(GitIdentityError):
                verify_source_revision(
                    forged,
                    repository=root,
                    require_current_head=True,
                    require_clean=True,
                )

    def test_forged_branch_ref_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._repository(Path(temporary))
            actual = capture_actual_source_revision(root)
            forged = replace(actual, branch_ref="forged")
            with self.assertRaises(GitIdentityError):
                verify_source_revision(
                    forged,
                    repository=root,
                    require_current_head=True,
                    require_clean=True,
                )

    def test_official_runner_rejects_forged_repository_and_ref_before_engine(self) -> None:
        child_code = """
import json
import sys
from dataclasses import replace
from pathlib import Path
from crypto_lab.git_identity import capture_actual_source_revision
from crypto_lab.owner import OwnerWorkflowInput, build_official_request
from crypto_lab.runner import run_official_lab

repository = Path(sys.argv[1])
value = OwnerWorkflowInput.from_json_bytes(Path(sys.argv[2]).read_bytes())
source = capture_actual_source_revision(repository)
source = replace(
    source,
    repository=("https://example.invalid/forged.git" if sys.argv[3] == "repository" else source.repository),
    branch_ref=("forged-branch" if sys.argv[3] == "branch" else source.branch_ref),
)
request = build_official_request(value, repository_root=repository, source_revision=source)
result = run_official_lab(request)
engine = json.loads((result.evidence_dir / "nautilus_result.json").read_text(encoding="utf-8"))
Path(sys.argv[4]).write_text(json.dumps({
    "state": result.state.value,
    "checker": result.checker_outcome.value,
    "failure_codes": list(result.failure_codes),
    "engine_executed": engine["engine_executed"],
    "engine_completed": engine["engine_completed"],
    "fill_count": len(result.fills),
    "isolation": engine["network_guard"]["process_isolation"],
}, sort_keys=True), encoding="utf-8")
"""
        for mutation in ("repository", "branch"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                repository = base / "repository"
                shutil.copytree(
                    ROOT,
                    repository,
                    ignore=shutil.ignore_patterns(
                        ".git",
                        ".venv",
                        "__pycache__",
                        ".pytest_cache",
                    ),
                )
                subprocess.run(
                    ["git", "init", "-b", "main", str(repository)],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", str(repository), "config", "user.name", "Repair Test"],
                    check=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repository),
                        "config",
                        "user.email",
                        "repair@example.invalid",
                    ],
                    check=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repository),
                        "remote",
                        "add",
                        "origin",
                        "https://example.invalid/exact.git",
                    ],
                    check=True,
                )
                subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
                subprocess.run(
                    ["git", "-C", str(repository), "commit", "-m", "source"],
                    check=True,
                    capture_output=True,
                )
                from crypto_lab.owner import qualification_workflow_fixture_input

                from datetime import UTC
                from datetime import datetime
                from datetime import timedelta

                value = qualification_workflow_fixture_input(
                    repository_root=repository,
                    frozen_at_utc=datetime.now(UTC) - timedelta(seconds=1),
                    trial_id=f"aud006-{mutation}-trial",
                    run_id=f"aud006-{mutation}-run",
                )
                input_path = base / "input.json"
                output_path = base / "output.json"
                input_path.write_bytes(value.to_json_bytes() + b"\n")
                environment = dict(os.environ)
                environment.update(
                    {
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONPATH": str(repository / "src"),
                        "TZ": "UTC",
                    },
                )
                process = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        child_code,
                        str(repository),
                        str(input_path),
                        mutation,
                        str(output_path),
                    ],
                    cwd=repository,
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
                result = json.loads(output_path.read_text(encoding="utf-8"))
                self.assertEqual(result["state"], "BLOCKED")
                self.assertEqual(result["checker"], "CHECK_BLOCKED")
                self.assertIn("EVIDENCE_INCOMPLETE", result["failure_codes"])
                self.assertFalse(result["engine_executed"])
                self.assertFalse(result["engine_completed"])
                self.assertEqual(result["fill_count"], 0)
                self.assertEqual(result["isolation"]["current_process_probe_errno"], 1)


if __name__ == "__main__":
    unittest.main()
