#!/usr/bin/env python3
"""Create an additive exact-input retry for a retained failed research Trial."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import sha256_file
from crypto_lab.owner import OwnerWorkflowInput
from crypto_lab.research import TERMINAL_TRIAL_STATES
from crypto_lab.research import TrialJournal
from crypto_lab.research import TrialState


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-workflow", type=Path, required=True)
    parser.add_argument("--failed-trial-id", required=True)
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source_path = args.source_workflow.resolve()
    output_path = args.output.resolve()
    manifest_path = output_path.with_suffix(".manifest.json")
    if output_path.exists() or manifest_path.exists():
        raise FileExistsError("retry workflow output already exists")
    source = OwnerWorkflowInput.from_json_bytes(source_path.read_bytes())
    history = TrialJournal(ROOT / "research/trials.jsonl").read_records()
    failed = [item for item in history if item.trial_id == args.failed_trial_id]
    if (
        not failed
        or failed[-1].state not in TERMINAL_TRIAL_STATES
        or failed[-1].state is TrialState.COMPLETED
        or failed[-1].candidate_id != source.candidate_id
        or failed[-1].protocol_id != source.protocol.protocol_id
        or failed[-1].strategy_spec_id != source.strategy_spec.strategy_spec_id
        or failed[-1].dataset_release_id != source.dataset_release_id
    ):
        raise RuntimeError("retry is not bound to an exact retained non-completed Trial")
    if args.trial_id == source.trial_id or args.run_id == source.run_id:
        raise ValueError("retry requires new trial_id and run_id")

    retry = replace(source, trial_id=args.trial_id, run_id=args.run_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(retry.to_json_bytes() + b"\n")
    manifest = {
        "schema": "owner-strategy-research-001-exact-retry-v1",
        "source_workflow_path": str(source_path.relative_to(ROOT)),
        "source_workflow_sha256": sha256_file(source_path),
        "failed_trial_id": args.failed_trial_id,
        "failed_terminal_state": failed[-1].state.value,
        "failed_result_ref": failed[-1].result_ref,
        "retry_workflow_path": str(output_path.relative_to(ROOT)),
        "retry_workflow_sha256": sha256_file(output_path),
        "retry_trial_id": retry.trial_id,
        "retry_run_id": retry.run_id,
        "protocol_id": retry.protocol.protocol_id,
        "candidate_id": retry.candidate_id,
        "strategy_spec_id": retry.strategy_spec.strategy_spec_id,
        "dataset_release_id": retry.dataset_release_id,
        "material_strategy_inputs_changed": False,
        "reason": "PRODUCT_MECHANICAL_DEFECT_FIXED_WITH_FAILED_ATTEMPT_PRESERVED",
    }
    manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
