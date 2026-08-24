"""Closed public registry for Official Nautilus Strategy implementations."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from dataclasses import fields
from pathlib import Path
from typing import Any

from crypto_lab.config import SourceRevision
from crypto_lab.config import StrictModel
from crypto_lab.config import _require_nonempty
from crypto_lab.config import _require_sha256
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.strategies.base import FirstEligibleBarQualificationFixture
from crypto_lab.strategies.base import GuardedCausalStrategy
from crypto_lab.strategies.base import StrategySpec
from crypto_lab.strategies.daily_sma_trend import BtcusdtDailyPriceVsSma20Trend
from crypto_lab.strategies.weekly_tsmom import BUY_AND_HOLD_REGISTRATION_ID
from crypto_lab.strategies.weekly_tsmom import TSMOM_FULL_REGISTRATION_ID
from crypto_lab.strategies.weekly_tsmom import TSMOM_VOL20_REGISTRATION_ID
from crypto_lab.strategies.weekly_tsmom import BtcusdtBuyAndHold1x
from crypto_lab.strategies.weekly_tsmom import BtcusdtWeeklyTsmom28


class RegisteredStrategyIdentity(StrictModel):
    schema_version: int
    strategy_identity_sha256: str
    registration_id: str
    strategy_spec_id: str
    strategy_spec: dict[str, Any]
    parameters_sha256: str
    implementation_module: str
    implementation_qualname: str
    implementation_revision: str
    implementation_code_sha256: str
    source_repository: str
    source_branch_ref: str
    source_git_commit: str
    source_git_tree: str
    qualification_fixture_only: bool
    profitability_claim_eligible: bool

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("registered strategy identity schema must be 1")
        for name in (
            "registration_id",
            "implementation_module",
            "implementation_qualname",
            "implementation_revision",
            "source_repository",
            "source_branch_ref",
        ):
            _require_nonempty(getattr(self, name), f"strategy_identity.{name}")
        for name in (
            "strategy_identity_sha256",
            "strategy_spec_id",
            "parameters_sha256",
            "implementation_code_sha256",
        ):
            _require_sha256(getattr(self, name), f"strategy_identity.{name}")
        if len(self.source_git_commit) != 40 or len(self.source_git_tree) != 40:
            raise ValueError("strategy identity requires full Git commit and tree IDs")
        if self.qualification_fixture_only and self.profitability_claim_eligible:
            raise ValueError("a qualification fixture cannot be profitability-claim eligible")
        if canonical_sha256(self.material_payload()) != self.strategy_identity_sha256:
            raise ValueError("registered strategy identity hash mismatch")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "strategy_identity_sha256"
        }

    @classmethod
    def create(cls, **values: Any) -> RegisteredStrategyIdentity:
        material = {"schema_version": 1, **values}
        return cls(strategy_identity_sha256=canonical_sha256(material), **material)


@dataclass(frozen=True)
class _RegisteredDefinition:
    registration_id: str
    implementation: type[GuardedCausalStrategy]
    implementation_revision: str
    implementation_source_filename: str
    qualification_fixture_only: bool
    profitability_claim_eligible: bool


_REGISTRY = {
    FirstEligibleBarQualificationFixture.REGISTRATION_ID: _RegisteredDefinition(
        registration_id=FirstEligibleBarQualificationFixture.REGISTRATION_ID,
        implementation=FirstEligibleBarQualificationFixture,
        implementation_revision=FirstEligibleBarQualificationFixture.IMPLEMENTATION_REVISION,
        implementation_source_filename="base.py",
        qualification_fixture_only=True,
        profitability_claim_eligible=False,
    ),
    BtcusdtDailyPriceVsSma20Trend.REGISTRATION_ID: _RegisteredDefinition(
        registration_id=BtcusdtDailyPriceVsSma20Trend.REGISTRATION_ID,
        implementation=BtcusdtDailyPriceVsSma20Trend,
        implementation_revision=BtcusdtDailyPriceVsSma20Trend.IMPLEMENTATION_REVISION,
        implementation_source_filename="daily_sma_trend.py",
        qualification_fixture_only=False,
        profitability_claim_eligible=True,
    ),
    TSMOM_FULL_REGISTRATION_ID: _RegisteredDefinition(
        registration_id=TSMOM_FULL_REGISTRATION_ID,
        implementation=BtcusdtWeeklyTsmom28,
        implementation_revision=BtcusdtWeeklyTsmom28.IMPLEMENTATION_REVISION,
        implementation_source_filename="weekly_tsmom.py",
        qualification_fixture_only=False,
        profitability_claim_eligible=True,
    ),
    TSMOM_VOL20_REGISTRATION_ID: _RegisteredDefinition(
        registration_id=TSMOM_VOL20_REGISTRATION_ID,
        implementation=BtcusdtWeeklyTsmom28,
        implementation_revision=BtcusdtWeeklyTsmom28.IMPLEMENTATION_REVISION,
        implementation_source_filename="weekly_tsmom.py",
        qualification_fixture_only=False,
        profitability_claim_eligible=True,
    ),
    BUY_AND_HOLD_REGISTRATION_ID: _RegisteredDefinition(
        registration_id=BUY_AND_HOLD_REGISTRATION_ID,
        implementation=BtcusdtBuyAndHold1x,
        implementation_revision=BtcusdtBuyAndHold1x.IMPLEMENTATION_REVISION,
        implementation_source_filename="weekly_tsmom.py",
        qualification_fixture_only=False,
        profitability_claim_eligible=False,
    ),
}


def registered_strategy_ids() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def resolve_registered_strategy_identity(
    registration_id: str,
    *,
    strategy_spec: StrategySpec,
    source_revision: SourceRevision,
) -> RegisteredStrategyIdentity:
    """Resolve a static registration; callables and dynamic imports are never accepted."""

    definition = _REGISTRY.get(registration_id)
    if definition is None:
        raise ValueError(f"CONFIG_INVALID: unregistered strategy {registration_id!r}")
    # Validate every material fixture parameter at the public boundary before
    # identity creation.  configure_registered repeats this at construction.
    required = definition.implementation.REQUIRED_PARAMETERS
    if set(strategy_spec.parameters) != required:
        raise ValueError("CONFIG_INVALID: registered strategy parameters are incomplete or unknown")
    source_path = Path(__file__).with_name(definition.implementation_source_filename)
    return RegisteredStrategyIdentity.create(
        registration_id=definition.registration_id,
        strategy_spec_id=strategy_spec.strategy_spec_id,
        strategy_spec=strategy_spec.to_builtins(),
        parameters_sha256=canonical_sha256(dict(strategy_spec.parameters)),
        implementation_module=definition.implementation.__module__,
        implementation_qualname=definition.implementation.__qualname__,
        implementation_revision=definition.implementation_revision,
        implementation_code_sha256=sha256_file(source_path),
        source_repository=source_revision.repository,
        source_branch_ref=source_revision.branch_ref,
        source_git_commit=source_revision.git_commit,
        source_git_tree=source_revision.git_tree,
        qualification_fixture_only=definition.qualification_fixture_only,
        profitability_claim_eligible=definition.profitability_claim_eligible,
    )


def registered_strategy_identity_matches_frozen_source(
    identity: RegisteredStrategyIdentity,
    strategy_spec: StrategySpec,
    source_revision: SourceRevision,
    *,
    repository_root: Path,
) -> bool:
    """Verify a persisted identity against its exact frozen Git module bytes."""

    module_parts = identity.implementation_module.split(".")
    if (
        len(module_parts) < 3
        or module_parts[:2] != ["crypto_lab", "strategies"]
        or any(not part.isidentifier() for part in module_parts)
    ):
        return False
    if (
        identity.strategy_spec_id != strategy_spec.strategy_spec_id
        or identity.strategy_spec != strategy_spec.to_builtins()
        or identity.parameters_sha256
        != canonical_sha256(dict(strategy_spec.parameters))
        or identity.source_repository != source_revision.repository
        or identity.source_branch_ref != source_revision.branch_ref
        or identity.source_git_commit != source_revision.git_commit
        or identity.source_git_tree != source_revision.git_tree
    ):
        return False
    relative = "src/" + "/".join(module_parts) + ".py"
    process = subprocess.run(
        ["git", "show", f"{source_revision.git_commit}:{relative}"],
        cwd=repository_root,
        check=False,
        capture_output=True,
    )
    return bool(
        process.returncode == 0
        and hashlib.sha256(process.stdout).hexdigest()
        == identity.implementation_code_sha256
    )


def create_registered_strategy(
    identity: RegisteredStrategyIdentity,
    *,
    strategy_spec: StrategySpec,
    source_revision: SourceRevision,
    configuration: dict[str, Any],
) -> GuardedCausalStrategy:
    resolved = resolve_registered_strategy_identity(
        identity.registration_id,
        strategy_spec=strategy_spec,
        source_revision=source_revision,
    )
    if resolved != identity:
        raise ValueError("CONFIG_HASH_MISMATCH: registered strategy identity changed")
    definition = _REGISTRY[identity.registration_id]
    strategy = definition.implementation()
    strategy.configure_registered(strategy_spec=strategy_spec, **configuration)
    return strategy


__all__ = [
    "RegisteredStrategyIdentity",
    "create_registered_strategy",
    "registered_strategy_identity_matches_frozen_source",
    "registered_strategy_ids",
    "resolve_registered_strategy_identity",
]
