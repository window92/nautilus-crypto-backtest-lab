"""Pinned-Nautilus research-metric qualifications.

The functions in this module delegate metric calculation to the pinned public
Nautilus analysis API.  They do not calculate project PnL or pair Fills.
"""

from __future__ import annotations

import math
from dataclasses import fields
from decimal import Decimal
from typing import Any

from crypto_lab.config import StrictModel
from crypto_lab.config import _require_nonempty
from crypto_lab.config import _require_sha256
from crypto_lab.hashing import canonical_sha256


PINNED_NAUTILUS_VERSION = "2.0.0rc2"
PORTFOLIO_DAILY_RETURNS_BASIS = "PORTFOLIO_DAILY_ACCOUNT_RETURNS"


class NativeMetricQualificationError(ValueError):
    """Fail-closed native metric qualification error."""


class NativeCalmarQualification(StrictModel):
    schema_version: int
    qualification_id: str
    status: str
    value: str
    undefined_reason: str
    period: int
    returns_basis: str
    input_observation_count: int
    input_returns_sha256: str
    scored_start_ns: int
    scoring_end_exclusive_ns: int
    native_api: str
    nautilus_version: str
    project_calmar_calculation_used: bool

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise NativeMetricQualificationError("unknown native Calmar schema")
        _require_sha256(self.qualification_id, "native_calmar.qualification_id")
        _require_sha256(self.input_returns_sha256, "native_calmar.input_returns_sha256")
        if self.status not in {"NATIVE", "UNDEFINED"}:
            raise NativeMetricQualificationError("unknown native Calmar status")
        for name in (
            "value",
            "undefined_reason",
            "returns_basis",
            "native_api",
            "nautilus_version",
        ):
            _require_nonempty(getattr(self, name), f"native_calmar.{name}")
        if self.period <= 0 or self.input_observation_count < 0:
            raise NativeMetricQualificationError("invalid native Calmar period/count")
        if self.scored_start_ns < 0 or self.scoring_end_exclusive_ns <= self.scored_start_ns:
            raise NativeMetricQualificationError("invalid native Calmar scoring interval")
        if self.status == "NATIVE":
            value = Decimal(self.value)
            if not value.is_finite() or self.undefined_reason != "NOT_APPLICABLE":
                raise NativeMetricQualificationError("defined native Calmar is invalid")
            if self.returns_basis != PORTFOLIO_DAILY_RETURNS_BASIS:
                raise NativeMetricQualificationError("native Calmar has non-portfolio basis")
        elif self.value != "UNDEFINED" or self.undefined_reason == "NOT_APPLICABLE":
            raise NativeMetricQualificationError("undefined native Calmar needs a reason")
        if self.nautilus_version != PINNED_NAUTILUS_VERSION:
            raise NativeMetricQualificationError("native Calmar runtime identity mismatch")
        if self.project_calmar_calculation_used:
            raise NativeMetricQualificationError("project Calmar calculation is forbidden")
        if canonical_sha256(self.material_payload()) != self.qualification_id:
            raise NativeMetricQualificationError("native Calmar identity mismatch")

    def material_payload(self) -> dict[str, Any]:
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name != "qualification_id"
        }

    @classmethod
    def create(cls, **values: Any) -> NativeCalmarQualification:
        material = {"schema_version": 1, **values}
        return cls(qualification_id=canonical_sha256(material), **material)


def qualify_native_calmar(
    *,
    returns: tuple[tuple[int, Decimal], ...],
    returns_basis: str,
    scored_start_ns: int,
    scoring_end_exclusive_ns: int,
    period: int = 252,
) -> NativeCalmarQualification:
    """Call pinned Nautilus Calmar only for scored portfolio daily returns."""

    if period <= 0:
        raise NativeMetricQualificationError("Calmar period must be positive")
    if scored_start_ns < 0 or scoring_end_exclusive_ns <= scored_start_ns:
        raise NativeMetricQualificationError("invalid Calmar scoring interval")
    timestamps = tuple(timestamp for timestamp, _ in returns)
    if timestamps != tuple(sorted(timestamps)) or len(timestamps) != len(set(timestamps)):
        raise NativeMetricQualificationError("native returns timestamps are not ordered unique")
    if any(not value.is_finite() for _, value in returns):
        raise NativeMetricQualificationError("native returns contain a non-finite value")
    scored = tuple(
        (timestamp, value)
        for timestamp, value in returns
        if scored_start_ns <= timestamp < scoring_end_exclusive_ns
    )
    common = {
        "period": period,
        "returns_basis": returns_basis,
        "input_observation_count": len(scored),
        "input_returns_sha256": canonical_sha256(scored),
        "scored_start_ns": scored_start_ns,
        "scoring_end_exclusive_ns": scoring_end_exclusive_ns,
        "native_api": "nautilus_trader.analysis.CalmarRatio.calculate_from_returns",
        "nautilus_version": PINNED_NAUTILUS_VERSION,
        "project_calmar_calculation_used": False,
    }
    if returns_basis != PORTFOLIO_DAILY_RETURNS_BASIS:
        return NativeCalmarQualification.create(
            status="UNDEFINED",
            value="UNDEFINED",
            undefined_reason="UNDEFINED_NATIVE_CALMAR_PORTFOLIO_RETURNS_BASIS_UNAVAILABLE",
            **common,
        )
    if not scored:
        return NativeCalmarQualification.create(
            status="UNDEFINED",
            value="UNDEFINED",
            undefined_reason="UNDEFINED_NATIVE_CALMAR_NO_SCORED_RETURNS",
            **common,
        )

    import nautilus_trader
    from nautilus_trader.analysis import CalmarRatio

    if nautilus_trader.__version__ != PINNED_NAUTILUS_VERSION:
        raise NativeMetricQualificationError("pinned Nautilus version is not active")
    # Nautilus's public API accepts f64 returns; this conversion is the pinned
    # native statistic's input contract, not a project-side metric calculation.
    result = CalmarRatio(period).calculate_from_returns(
        {timestamp: float(value) for timestamp, value in scored},
    )
    if result is None or not math.isfinite(result):
        return NativeCalmarQualification.create(
            status="UNDEFINED",
            value="UNDEFINED",
            undefined_reason="UNDEFINED_NATIVE_CALMAR_ZERO_DRAWDOWN_OR_INVALID_CAGR",
            **common,
        )
    return NativeCalmarQualification.create(
        status="NATIVE",
        value=repr(result),
        undefined_reason="NOT_APPLICABLE",
        **common,
    )


__all__ = [
    "NativeCalmarQualification",
    "NativeMetricQualificationError",
    "PINNED_NAUTILUS_VERSION",
    "PORTFOLIO_DAILY_RETURNS_BASIS",
    "qualify_native_calmar",
]
