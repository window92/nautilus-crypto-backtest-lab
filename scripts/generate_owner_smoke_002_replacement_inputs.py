#!/usr/bin/env python3
"""Generate immutable public workflow inputs for OWNER_SMOKE_002 replacement 001."""

from __future__ import annotations

import argparse
from datetime import UTC
from datetime import datetime
from pathlib import Path

from crypto_lab.config import MarketProfile
from crypto_lab.owner import OwnerWorkflowInput
from generate_owner_smoke_002_inputs import build_owner_smoke_workflow


EPOCH_ID = "owner-smoke-002-replacement-001"
SPOT_RELEASE_ID = "fd8542c109cfbf7d6b19d5b7bbb7705c6a161efc807695f3671978c381e34eca"
PERPETUAL_RELEASE_ID = "b6c8f5d659f3441c924b613d770342796c90b90a970f42a3dc8227c856198917"
SUPERSESSION_REASON = "INSTRUMENT_REPRESENTATION_PREVENTED_EXECUTABLE_MARKET_STATE"


def _claim_addendum(profile: MarketProfile) -> str:
    superseded = (
        "owner-smoke-002-spot-sma20-development,"
        "owner-smoke-002-spot-sma20-development-retry-001"
        if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY
        else "owner-smoke-002-perpetual-sma20-development"
    )
    return (
        f"; SUPERSEDES_FAILED_TRIALS={superseded}"
        f"; SUPERSESSION_REASON={SUPERSESSION_REASON}"
        "; SAME_STRATEGY_PARAMETERS_WINDOW_LATENCY_FEES_BALANCE_AND_SIZING"
        "; NO_CANONICAL_MARKET_VALUE_CHANGE"
    )


def replacement_workflows(*, frozen_at_utc: datetime) -> tuple[OwnerWorkflowInput, ...]:
    common = {
        "frozen_at_utc": frozen_at_utc,
        "retry_sequence": 0,
        "epoch_id": EPOCH_ID,
        "instrument_selection_basis": (
            "Owner-authorized additive replacement after exact Nautilus instrument "
            "representation repair; locked BTCUSDT profile and inspected window unchanged"
        ),
    }
    return (
        build_owner_smoke_workflow(
            **common,
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            release_id=SPOT_RELEASE_ID,
            claim_addendum=_claim_addendum(MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY),
        ),
        build_owner_smoke_workflow(
            **common,
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            release_id=PERPETUAL_RELEASE_ID,
            claim_addendum=_claim_addendum(
                MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            ),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frozen-at-utc", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    frozen = datetime.fromisoformat(args.frozen_at_utc.replace("Z", "+00:00"))
    if frozen.tzinfo is None or frozen.utcoffset() != UTC.utcoffset(frozen):
        raise ValueError("frozen-at-utc must be explicit UTC")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for value in replacement_workflows(frozen_at_utc=frozen):
        path = output / f"{value.trial_id}.json"
        path.write_bytes(value.to_json_bytes() + b"\n")
        if OwnerWorkflowInput.from_json_bytes(path.read_bytes()) != value:
            raise RuntimeError(f"Owner Workflow input round-trip failed: {path}")
        print(
            f"{value.trial_id} protocol={value.protocol.protocol_id} "
            f"strategy_spec={value.strategy_spec.strategy_spec_id} "
            f"release={value.dataset_release_id}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
