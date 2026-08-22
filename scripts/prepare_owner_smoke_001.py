#!/usr/bin/env python3
"""Acquire and freeze the exact OWNER_OPERATIONAL_SMOKE_001 research inputs.

This setup command is intentionally network-capable and must run before any
Official process.  Official runs resolve only the immutable releases produced
here and execute beneath the process-level offline boundary.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from crypto_lab.config import FeeAssumption
from crypto_lab.config import MarketProfile
from crypto_lab.config import MoneyAmount
from crypto_lab.config import NOT_APPLICABLE
from crypto_lab.data import AcquisitionRequest
from crypto_lab.data import FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY
from crypto_lab.data import OfficialBinanceAcquirer
from crypto_lab.data import RawObjectRecord
from crypto_lab.data import RawObjectStore
from crypto_lab.data import SourceObjectBinding
from crypto_lab.data import SourceRole
from crypto_lab.data import TimeRange
from crypto_lab.data import build_dataset_release
from crypto_lab.data import build_nautilus_catalog
from crypto_lab.data import extract_single_csv_archive
from crypto_lab.data import parse_funding_csv
from crypto_lab.data import parse_kline_csv
from crypto_lab.data import parse_spot_instrument_metadata
from crypto_lab.data import parse_usdm_instrument_metadata
from crypto_lab.data import prove_funding_schedule_from_official_objects
from crypto_lab.data import to_nautilus_instrument
from crypto_lab.data import validate_market_order_quantity
from crypto_lab.exposure import AuthoritativeExposureResolver
from crypto_lab.git_identity import capture_actual_source_revision
from crypto_lab.hashing import canonical_json_bytes
from crypto_lab.hashing import canonical_sha256
from crypto_lab.hashing import sha256_file
from crypto_lab.history import AuthoritativeResearchHistory
from crypto_lab.history import HistoryAnchorStore
from crypto_lab.m3 import QualifiedProfileRegistry
from crypto_lab.owner import OwnerWorkflowInput
from crypto_lab.owner import OwnerWorkflowPurpose
from crypto_lab.research import BenchmarkSpec
from crypto_lab.research import CandidateSpec
from crypto_lab.research import ClaimScope
from crypto_lab.research import InstrumentScope
from crypto_lab.research import MonteCarloSpec
from crypto_lab.research import PartitionRole
from crypto_lab.research import PurgeEmbargoRule
from crypto_lab.research import ResearchIntent
from crypto_lab.research import ResearchProtocol
from crypto_lab.research import ResamplingMethod
from crypto_lab.research import ResultExposure
from crypto_lab.research import SampleAdequacyRule
from crypto_lab.research import UtcInterval
from crypto_lab.strategies import locked_sma20_strategy_spec
from nautilus_trader.model import Quantity


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "data/raw"
CATALOG_ROOT = ROOT / "data/catalog"
RELEASE_ROOT = ROOT / "data/releases"
EVIDENCE_ROOT = ROOT / "evidence/research/owner-smoke-001"
INPUT_ROOT = EVIDENCE_ROOT / "workflow-inputs"

SYMBOL = "BTCUSDT"
WINDOW = TimeRange(
    start_inclusive=datetime(2020, 12, 1, tzinfo=UTC),
    end_exclusive=datetime(2021, 7, 1, tzinfo=UTC),
)
SCORING = UtcInterval(
    start_inclusive=datetime(2021, 1, 1, tzinfo=UTC),
    end_exclusive=datetime(2021, 7, 1, tzinfo=UTC),
)
FEE_RATE = Decimal("0.001")
FEE_REASON = "SSOT Appendix A qualification-only observable estimated fee"

RESEARCH_SOURCES = (
    {
        "title": "Liu and Tsyvinski, Risks and Returns of Cryptocurrency",
        "url": "https://www.nber.org/papers/w24877",
    },
    {
        "title": "Detzel et al., Learning and Predictability via Technical Analysis",
        "url": "https://onlinelibrary.wiley.com/doi/10.1111/fima.12310",
    },
    {
        "title": "Hudson and Urquhart, Technical Trading and Cryptocurrencies",
        "url": "https://link.springer.com/article/10.1007/s10479-019-03357-1",
    },
)


def _month_ranges() -> tuple[tuple[str, TimeRange], ...]:
    result: list[tuple[str, TimeRange]] = []
    current = WINDOW.start_inclusive
    while current < WINDOW.end_exclusive:
        following = (
            datetime(current.year + 1, 1, 1, tzinfo=UTC)
            if current.month == 12
            else datetime(current.year, current.month + 1, 1, tzinfo=UTC)
        )
        result.append((current.strftime("%Y-%m"), TimeRange(start_inclusive=current, end_exclusive=following)))
        current = following
    return tuple(result)


def _preserve(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise RuntimeError(f"immutable evidence collision: {path}")
        return
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _archive(
    acquirer: OfficialBinanceAcquirer,
    *,
    role: SourceRole,
    profile: MarketProfile,
    url: str,
    filename: str,
    interval: str,
    time_range: TimeRange,
) -> tuple[RawObjectRecord, RawObjectRecord, bytes]:
    request = AcquisitionRequest(
        source_role=role,
        source_locator=url,
        exact_filename=filename,
        instrument=SYMBOL,
        market_profile=profile.value,
        requested_interval=interval,
        requested_time_range=time_range,
    )
    checksum = AcquisitionRequest(
        source_role=SourceRole.PUBLISHER_CHECKSUM,
        source_locator=url + ".CHECKSUM",
        exact_filename=filename + ".CHECKSUM",
        instrument=SYMBOL,
        market_profile=profile.value,
        requested_interval=interval,
        requested_time_range=time_range,
    )
    record, checksum_record = acquirer.acquire(
        request,
        checksum_request=checksum,
        acquired_at_utc=datetime.now(UTC),
    )
    if checksum_record is None or record.publisher_checksum != record.sha256:
        raise RuntimeError(f"publisher checksum not proven for {filename}")
    csv_name = filename.removesuffix(".zip") + ".csv"
    csv_bytes = extract_single_csv_archive(
        acquirer.store.read_bytes(record.sha256),
        expected_filename=csv_name,
    )
    return record, checksum_record, csv_bytes


def _object(
    acquirer: OfficialBinanceAcquirer,
    *,
    role: SourceRole,
    url: str,
    filename: str,
    instrument: str = SYMBOL,
    profile: str = NOT_APPLICABLE,
) -> tuple[RawObjectRecord, bytes]:
    request = AcquisitionRequest(
        source_role=role,
        source_locator=url,
        exact_filename=filename,
        instrument=instrument,
        market_profile=profile,
        requested_interval=NOT_APPLICABLE,
        requested_time_range=NOT_APPLICABLE,
    )
    record, checksum = acquirer.acquire(request, acquired_at_utc=datetime.now(UTC))
    if checksum is not None:
        raise RuntimeError("unexpected checksum side object")
    return record, acquirer.store.read_bytes(record.sha256)


def _catalog_twice(*, label: str, **kwargs: Any) -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix=f"owner-smoke-{label}-a-", dir="/tmp") as first_name:
        first = build_nautilus_catalog(Path(first_name), **kwargs)
        first_identity = first.catalog_identity
        destination = CATALOG_ROOT / first_identity
        if destination.exists():
            raise RuntimeError(f"catalog identity path already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(first_name, destination)
    # Do not retain two full row-level inventories in memory. Their canonical
    # identities are the independently compared semantic values.
    del first
    with tempfile.TemporaryDirectory(prefix=f"owner-smoke-{label}-b-", dir="/tmp") as second_name:
        second = build_nautilus_catalog(Path(second_name), **kwargs)
        if first_identity != second.catalog_identity:
            raise RuntimeError(f"{label} independent semantic catalog rebuild mismatch")
        return first_identity, second.catalog_identity


def _protocol(
    *,
    frozen_at: datetime,
    profile: MarketProfile,
    release_id: str,
) -> tuple[ResearchProtocol, CandidateSpec]:
    spec = locked_sma20_strategy_spec(profile)
    candidate = CandidateSpec.create(
        candidate_label="SMA20_ONLY_PRE_REGISTERED_CANDIDATE",
        strategy_spec_id=spec.strategy_spec_id,
        parameter_values=dict(spec.parameters),
    )
    validation = UtcInterval(
        start_inclusive=datetime(2099, 1, 1, tzinfo=UTC),
        end_exclusive=datetime(2099, 1, 2, tzinfo=UTC),
    )
    oos = UtcInterval(
        start_inclusive=datetime(2099, 1, 2, tzinfo=UTC),
        end_exclusive=datetime(2099, 1, 3, tzinfo=UTC),
    )
    holdout = UtcInterval(
        start_inclusive=datetime(2099, 1, 3, tzinfo=UTC),
        end_exclusive=datetime(2099, 1, 4, tzinfo=UTC),
    )
    suffix = "spot" if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else "perpetual"
    instrument = "BTCUSDT.BINANCE" if suffix == "spot" else "BTCUSDT-PERP.BINANCE"
    protocol = ResearchProtocol.create(
        frozen_at_utc=frozen_at,
        research_family_id=f"owner-smoke-001-{suffix}-daily-price-vs-sma20",
        hypothesis_id=(
            "causal-reproducible-complete-owner-workflow-price-vs-sma20-"
            f"{suffix}-not-profitability"
        ),
        research_intent=ResearchIntent.EXPLORATORY,
        market_profile=profile,
        instrument_scope=InstrumentScope.SINGLE_INSTRUMENT,
        instrument_ids=(instrument,),
        instrument_selection_basis="OWNER_OPERATIONAL_SMOKE_001 locked BTCUSDT profile",
        universe_selection_rule=NOT_APPLICABLE,
        universe_as_of_rule=NOT_APPLICABLE,
        universe_membership_sha256=NOT_APPLICABLE,
        dataset_release_ids=(release_id,),
        strategy_family="BTCUSDT_DAILY_PRICE_VS_SMA20_TREND",
        ordered_candidates=(candidate,),
        parameter_domain={name: (value,) for name, value in spec.parameters.items()},
        search_budget=1,
        candidate_ordering="AS_LISTED",
        deterministic_generator="NOT_APPLICABLE_ONE_EXPLICIT_CANDIDATE_NO_STRATEGY_RANDOMNESS",
        random_seeds=(0,),
        primary_metric="EXPLORATORY_OPERATIONAL_VALIDATION",
        required_benchmark=BenchmarkSpec(
            benchmark_id=f"OWNER_SMOKE_001_{suffix.upper()}_STRUCTURAL_NOT_EXECUTED",
            definition="Structural protocol field only; no benchmark or Final Holdout is executed",
            scored_interval=holdout,
            cost_basis="SAME_EXPLICIT_ESTIMATED_FEE_BASIS_IF_A_FUTURE_PROTOCOL_EXECUTES_IT",
            frozen_before_result_exposure=True,
        ),
        selection_rule="ONLY_PREDECLARED_SMA20_CANDIDATE_NO_RANKING",
        tie_break_rule="NOT_APPLICABLE_SINGLE_CANDIDATE",
        development_interval=SCORING,
        validation_interval=validation,
        oos_interval=oos,
        final_holdout_interval=holdout,
        purge_embargo_rule=PurgeEmbargoRule(
            mode=NOT_APPLICABLE,
            reason="No trained model, label, or forward target in this operational validation",
            purge_seconds=0,
            embargo_seconds=0,
            max_forward_dependency_seconds=0,
        ),
        time_series_split="CHRONOLOGICAL",
        multiple_testing_treatment=NOT_APPLICABLE,
        sample_adequacy_rule=SampleAdequacyRule(
            counted_observation=NOT_APPLICABLE,
            minimum_completed_trades=NOT_APPLICABLE,
            rationale="Exploratory operational validation makes no trade-based profitability claim",
        ),
        monte_carlo_spec=MonteCarloSpec(
            resampling_method=ResamplingMethod.NOT_APPLICABLE,
            simulation_count=0,
            random_seed=0,
            block_length=NOT_APPLICABLE,
            quantile_method="R7_LINEAR_INTERPOLATION",
            decimal_places=8,
            not_applicable_reason="Exploratory operational validation makes no profitability claim",
        ),
        intended_claim_scope=ClaimScope.INSTRUMENT_ONLY,
        claim_basis=(
            "EXPLORATORY_OPERATIONAL_VALIDATION_ONLY; DEVELOPMENT_EXPOSED; "
            "NO_FINAL_HOLDOUT; NO_REAL_PROFITABILITY_CLAIM"
        ),
        kill_criteria=(
            "MECHANICAL_INTEGRITY_NOT_PASS",
            "CHECKER_NOT_PASS",
            "DETERMINISTIC_REPLAY_NOT_PASS",
            "OFFLINE_BOUNDARY_NOT_PASS",
        ),
        terminal_policy="MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE",
    )
    return protocol, candidate


def _workflow_input(
    *,
    profile: MarketProfile,
    release_id: str,
    qualified_profile_id: str,
    protocol: ResearchProtocol,
    candidate: CandidateSpec,
) -> OwnerWorkflowInput:
    suffix = "spot" if profile is MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY else "perpetual"
    return OwnerWorkflowInput(
        schema_version=1,
        workflow_purpose=OwnerWorkflowPurpose.OWNER_STUDY,
        protocol=protocol,
        trial_id=f"owner-smoke-001-{suffix}-sma20-development",
        candidate_id=candidate.candidate_id,
        run_id=f"owner-smoke-001-{suffix}-run",
        registered_strategy_id="btcusdt_daily_price_vs_sma20_trend_v1",
        strategy_spec=locked_sma20_strategy_spec(profile),
        dataset_release_id=release_id,
        qualified_profile_record_id=qualified_profile_id,
        partition_role=PartitionRole.DEVELOPMENT,
        warmup_start=WINDOW.start_inclusive,
        scoring_start=SCORING.start_inclusive,
        scoring_end_exclusive=SCORING.end_exclusive,
        initial_capital=MoneyAmount(amount=Decimal("10000"), currency="USDT"),
        fee_assumption=FeeAssumption(
            maker_fee=FEE_RATE,
            taker_fee=FEE_RATE,
            explicit_zero_fee=False,
            reason=FEE_REASON,
            claim_class="ESTIMATED_FEE",
        ),
        seed=0,
    )


def main() -> int:
    frozen_at = datetime.now(UTC)
    store = RawObjectStore(RAW_ROOT)
    acquirer = OfficialBinanceAcquirer(store)
    all_records: list[RawObjectRecord] = []
    checksum_records: list[RawObjectRecord] = []

    contract_objects = (
        (
            SourceRole.BINANCE_PUBLIC_DATA_CONTRACT,
            "https://raw.githubusercontent.com/binance/binance-public-data/master/README.md",
            "binance-public-data-README.md",
        ),
        (
            SourceRole.BINANCE_OFFICIAL_API_CONTRACT,
            "https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/rest-api.md",
            "binance-spot-rest-api.md",
        ),
        (
            SourceRole.BINANCE_OFFICIAL_API_CONTRACT,
            "https://raw.githubusercontent.com/binance/binance-futures-connector-python/main/binance/um_futures/market.py",
            "binance-usdm-market-api.py",
        ),
    )
    for role, url, filename in contract_objects:
        record, _payload = _object(acquirer, role=role, url=url, filename=filename)
        all_records.append(record)

    spot_metadata_record, spot_metadata_bytes = _object(
        acquirer,
        role=SourceRole.SPOT_INSTRUMENT_METADATA,
        url="https://api.binance.com/api/v3/exchangeInfo?symbol=BTCUSDT",
        filename="spot-exchangeInfo-BTCUSDT.json",
        profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY.value,
    )
    perp_metadata_record, perp_metadata_bytes = _object(
        acquirer,
        role=SourceRole.USDM_PERPETUAL_INSTRUMENT_METADATA,
        url="https://fapi.binance.com/fapi/v1/exchangeInfo",
        filename="usdm-exchangeInfo.json",
        profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING.value,
    )
    all_records.extend((spot_metadata_record, perp_metadata_record))
    spot_metadata = parse_spot_instrument_metadata(
        spot_metadata_bytes,
        raw_symbol=SYMBOL,
        instrument_id="BTCUSDT.BINANCE",
        source_object_sha256=spot_metadata_record.sha256,
        maker_fee_rate=FEE_RATE,
        taker_fee_rate=FEE_RATE,
        fee_rate_basis=FEE_REASON,
    )
    perp_metadata = parse_usdm_instrument_metadata(
        perp_metadata_bytes,
        raw_symbol=SYMBOL,
        instrument_id="BTCUSDT-PERP.BINANCE",
        source_object_sha256=perp_metadata_record.sha256,
        maker_fee_rate=FEE_RATE,
        taker_fee_rate=FEE_RATE,
        fee_rate_basis=FEE_REASON,
    )
    validate_market_order_quantity(
        to_nautilus_instrument(spot_metadata),
        Quantity.from_str("0.10000"),
    )
    validate_market_order_quantity(
        to_nautilus_instrument(perp_metadata),
        Quantity.from_str("0.100"),
    )

    spot_bars = []
    perp_bars = []
    mark_bars = []
    funding_events = []
    spot_archive_records: list[RawObjectRecord] = []
    perp_archive_records: list[RawObjectRecord] = []
    funding_archive_records: list[RawObjectRecord] = []
    for month, month_range in _month_ranges():
        spot_filename = f"BTCUSDT-1m-{month}.zip"
        record, checksum, payload = _archive(
            acquirer,
            role=SourceRole.SPOT_EXECUTION_1M,
            profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            url=(
                "https://data.binance.vision/data/spot/monthly/klines/"
                f"BTCUSDT/1m/{spot_filename}"
            ),
            filename=spot_filename,
            interval="1m",
            time_range=month_range,
        )
        spot_archive_records.append(record)
        all_records.append(record)
        checksum_records.append(checksum)
        spot_bars.extend(
            item
            for item in parse_kline_csv(
                payload,
                source_role=SourceRole.SPOT_EXECUTION_1M,
                instrument_id="BTCUSDT.BINANCE",
                market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
                source_date=month_range.start_inclusive.date(),
            )
            if WINDOW.start_ns <= item.interval_start_ns < WINDOW.end_ns
        )

        perp_filename = f"BTCUSDT-1m-{month}.zip"
        record, checksum, payload = _archive(
            acquirer,
            role=SourceRole.USDM_PERPETUAL_EXECUTION_1M,
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            url=(
                "https://data.binance.vision/data/futures/um/monthly/klines/"
                f"BTCUSDT/1m/{perp_filename}"
            ),
            filename=perp_filename,
            interval="1m",
            time_range=month_range,
        )
        perp_archive_records.append(record)
        all_records.append(record)
        checksum_records.append(checksum)
        perp_bars.extend(
            item
            for item in parse_kline_csv(
                payload,
                source_role=SourceRole.USDM_PERPETUAL_EXECUTION_1M,
                instrument_id="BTCUSDT-PERP.BINANCE",
                market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
                source_date=month_range.start_inclusive.date(),
            )
            if WINDOW.start_ns <= item.interval_start_ns < WINDOW.end_ns
        )

        mark_filename = f"BTCUSDT-1m-{month}.zip"
        record, checksum, payload = _archive(
            acquirer,
            role=SourceRole.USDM_PERPETUAL_MARK_1M,
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            url=(
                "https://data.binance.vision/data/futures/um/monthly/markPriceKlines/"
                f"BTCUSDT/1m/{mark_filename}"
            ),
            filename=mark_filename,
            interval="1m",
            time_range=month_range,
        )
        perp_archive_records.append(record)
        all_records.append(record)
        checksum_records.append(checksum)
        mark_bars.extend(
            item
            for item in parse_kline_csv(
                payload,
                source_role=SourceRole.USDM_PERPETUAL_MARK_1M,
                instrument_id="BTCUSDT-PERP.BINANCE",
                market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
                source_date=month_range.start_inclusive.date(),
            )
            if WINDOW.start_ns <= item.interval_start_ns < WINDOW.end_ns
        )

        funding_filename = f"BTCUSDT-fundingRate-{month}.zip"
        record, checksum, payload = _archive(
            acquirer,
            role=SourceRole.USDM_PERPETUAL_FUNDING,
            profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            url=(
                "https://data.binance.vision/data/futures/um/monthly/fundingRate/"
                f"BTCUSDT/{funding_filename}"
            ),
            filename=funding_filename,
            interval="EVENT",
            time_range=month_range,
        )
        funding_archive_records.append(record)
        perp_archive_records.append(record)
        all_records.append(record)
        checksum_records.append(checksum)
        funding_events.extend(
            item
            for item in parse_funding_csv(payload, instrument_id="BTCUSDT-PERP.BINANCE")
            if WINDOW.start_ns <= item.calc_time_ns < WINDOW.end_ns
        )

    schedule = prove_funding_schedule_from_official_objects(
        tuple(funding_events),
        source_object_sha256s=tuple(item.sha256 for item in funding_archive_records),
        time_range=WINDOW,
    )
    spot_catalog_identity, spot_catalog_rebuild_identity = _catalog_twice(
        label="spot",
        metadata=spot_metadata,
        execution_bars=tuple(spot_bars),
    )
    perp_catalog_identity, perp_catalog_rebuild_identity = _catalog_twice(
        label="perpetual",
        metadata=perp_metadata,
        execution_bars=tuple(perp_bars),
        mark_bars=tuple(mark_bars),
        funding_events=tuple(funding_events),
        funding_native_binding=FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
    )

    spot_release = build_dataset_release(
        market_profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        instrument_id="BTCUSDT.BINANCE",
        source_objects=[
            *(SourceObjectBinding.from_raw(item) for item in spot_archive_records),
            SourceObjectBinding.from_raw(spot_metadata_record),
        ],
        normalized_time_range=WINDOW,
        instrument_metadata=spot_metadata,
        execution_bars=tuple(spot_bars),
        catalog_identity=spot_catalog_identity,
        created_at_utc=frozen_at,
    )
    perp_release = build_dataset_release(
        market_profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        instrument_id="BTCUSDT-PERP.BINANCE",
        source_objects=[
            *(SourceObjectBinding.from_raw(item) for item in perp_archive_records),
            SourceObjectBinding.from_raw(perp_metadata_record),
        ],
        normalized_time_range=WINDOW,
        instrument_metadata=perp_metadata,
        execution_bars=tuple(perp_bars),
        catalog_identity=perp_catalog_identity,
        created_at_utc=frozen_at,
        mark_bars=tuple(mark_bars),
        funding_events=tuple(funding_events),
        funding_schedule=schedule,
        funding_native_binding=FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
    )

    _preserve(
        RELEASE_ROOT / f"{spot_release.dataset_release_id}.json",
        spot_release.to_json_bytes() + b"\n",
    )
    _preserve(
        RELEASE_ROOT / f"{perp_release.dataset_release_id}.json",
        perp_release.to_json_bytes() + b"\n",
    )
    _preserve(
        RELEASE_ROOT / f"{spot_metadata.instrument_metadata_identity}.metadata.json",
        spot_metadata.to_json_bytes() + b"\n",
    )
    _preserve(
        RELEASE_ROOT / f"{perp_metadata.instrument_metadata_identity}.metadata.json",
        perp_metadata.to_json_bytes() + b"\n",
    )
    funding_material = {
        "schedule_identity": schedule.schedule_identity,
        "events": [item.semantic_payload() for item in funding_events],
        "native_binding": FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
    }
    if canonical_sha256(funding_material) != perp_release.funding_data_identity:
        raise RuntimeError("persisted funding identity differs from Dataset Release")
    _preserve(
        RELEASE_ROOT / f"{perp_release.funding_data_identity}.funding.json",
        canonical_json_bytes(
            {"funding_data_identity": perp_release.funding_data_identity, **funding_material},
        )
        + b"\n",
    )

    registry_path = ROOT / "evidence/m3/m3-acceptance-001/qualified-profile-registry.json"
    registry = QualifiedProfileRegistry.from_json_bytes(registry_path.read_bytes())
    profile_ids = {item.profile_id: item.qualified_profile_record_id for item in registry.records}
    source_revision = capture_actual_source_revision(ROOT)
    history = AuthoritativeResearchHistory(
        HistoryAnchorStore(
            repository_root=ROOT,
            journal_path=ROOT / "research/trials.jsonl",
            holdout_path=ROOT / "research/holdout_lock.json",
            anchor_path=ROOT / "research/history_anchors.jsonl",
            require_remote_tip=True,
        ),
    )
    resolver = AuthoritativeExposureResolver(repository_root=ROOT)
    exposure_results = []
    for profile, release, instrument, suffix in (
        (
            MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
            spot_release,
            "BTCUSDT.BINANCE",
            "spot",
        ),
        (
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
            perp_release,
            "BTCUSDT-PERP.BINANCE",
            "perpetual",
        ),
    ):
        candidate_exposure = ResultExposure(
            trial_id=f"owner-smoke-001-{suffix}-pre-freeze-check",
            market_profile=profile,
            instrument_id=instrument,
            scored_interval=UtcInterval(
                start_inclusive=WINDOW.start_inclusive,
                end_exclusive=WINDOW.end_exclusive,
            ),
            research_family_id=f"owner-smoke-001-{suffix}-daily-price-vs-sma20",
            hypothesis_lineage=("operational-validation-not-profitability",),
            strategy_lineage=(locked_sma20_strategy_spec(profile).strategy_spec_id,),
            dataset_release_id=release.dataset_release_id,
            first_exposure_at_utc=frozen_at,
            exposure_type=PartitionRole.DEVELOPMENT.value,
            evidence_reference="PRE_FREEZE_EXPOSURE_RESOLVER_CHECK_NO_RESULT_VIEWED",
            source_branch=source_revision.branch_ref,
            source_commit=source_revision.git_commit,
            seed=0,
            result_bearing=False,
        )
        mapping = resolver.require_fresh(candidate_exposure, history=history)
        exposure_results.append(
            {
                "market_profile": profile.value,
                "instrument_id": instrument,
                "interval": WINDOW.to_builtins(),
                "designated_partition": PartitionRole.DEVELOPMENT.value,
                "final_holdout_designated": False,
                "resolver_result": "NO_PRIOR_OVERLAP_FOUND_FOR_PROSPECTIVE_RANGE",
                "authoritative_trial_exposure_count": len(mapping),
            },
        )

    spot_protocol, spot_candidate = _protocol(
        frozen_at=frozen_at,
        profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        release_id=spot_release.dataset_release_id,
    )
    perp_protocol, perp_candidate = _protocol(
        frozen_at=frozen_at,
        profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        release_id=perp_release.dataset_release_id,
    )
    spot_input = _workflow_input(
        profile=MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY,
        release_id=spot_release.dataset_release_id,
        qualified_profile_id=profile_ids[MarketProfile.BINANCE_SPOT_CASH_LONG_ONLY],
        protocol=spot_protocol,
        candidate=spot_candidate,
    )
    perp_input = _workflow_input(
        profile=MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING,
        release_id=perp_release.dataset_release_id,
        qualified_profile_id=profile_ids[
            MarketProfile.BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
        ],
        protocol=perp_protocol,
        candidate=perp_candidate,
    )
    _preserve(INPUT_ROOT / "spot.json", spot_input.to_json_bytes() + b"\n")
    _preserve(INPUT_ROOT / "perpetual.json", perp_input.to_json_bytes() + b"\n")

    research_basis = {
        "schema": "owner-smoke-research-basis-v1",
        "authorization_id": "OWNER_OPERATIONAL_SMOKE_001",
        "research_purpose": "EXPLORATORY_OPERATIONAL_VALIDATION",
        "hypothesis": (
            "A simple pre-registered price-versus-SMA trend rule can be executed causally "
            "and reproducibly through the complete repaired laboratory workflow on both locked "
            "Market Profiles."
        ),
        "hypothesis_is_strategy_profitable": False,
        "published_evidence": (
            "MIXED; prior literature includes negative Bitcoin out-of-sample performance "
            "in some samples"
        ),
        "primary_references": RESEARCH_SOURCES,
        "development_exposed_not_final_holdout": True,
        "real_profitability_claim": False,
    }
    _preserve(
        EVIDENCE_ROOT / "research-basis.json",
        canonical_json_bytes(research_basis) + b"\n",
    )
    exposure_payload = {
        "schema": "owner-smoke-exposure-preflight-v1",
        "authorization_id": "OWNER_OPERATIONAL_SMOKE_001",
        "checked_before_protocol_freeze": True,
        "checked_at_utc": frozen_at,
        "resolver": "crypto_lab.exposure.AuthoritativeExposureResolver",
        "results": exposure_results,
        "holdout_lock_sha256": sha256_file(ROOT / "research/holdout_lock.json"),
        "holdout_entry_count": len(history.holdout.read().entries),
        "permanent_classification": [
            PartitionRole.DEVELOPMENT.value,
            "EXPOSED",
            "NOT_FINAL_HOLDOUT",
        ],
    }
    _preserve(
        EVIDENCE_ROOT / "exposure-preflight.json",
        canonical_json_bytes(exposure_payload) + b"\n",
    )
    acquisition_payload = {
        "schema": "owner-smoke-binance-acquisition-v1",
        "authorization_id": "OWNER_OPERATIONAL_SMOKE_001",
        "acquired_before_official_runs": True,
        "official_runs_network_policy": "PROCESS_LEVEL_OFFLINE",
        "normalized_time_range": WINDOW.to_builtins(),
        "publisher_checksum_objects_preserved": len(checksum_records),
        "publisher_checksums_verified": all(
            item.publisher_checksum == item.sha256
            for item in (*spot_archive_records, *perp_archive_records)
        ),
        "raw_objects": [item.to_builtins() for item in all_records],
        "checksum_objects": [item.to_builtins() for item in checksum_records],
        "raw_bytes_committed": False,
        "catalog_payloads_committed": False,
        "spot": {
            "instrument_id": spot_release.instrument_id,
            "dataset_release_id": spot_release.dataset_release_id,
            "catalog_identity": spot_catalog_identity,
            "independent_rebuild_identity": spot_catalog_rebuild_identity,
            "execution_bar_count": len(spot_bars),
            "metadata_identity": spot_metadata.instrument_metadata_identity,
            "historical_metadata_exact": spot_metadata.historical_exact,
            "metadata_limitations": spot_metadata.limitations,
        },
        "perpetual": {
            "instrument_id": perp_release.instrument_id,
            "dataset_release_id": perp_release.dataset_release_id,
            "catalog_identity": perp_catalog_identity,
            "independent_rebuild_identity": perp_catalog_rebuild_identity,
            "execution_bar_count": len(perp_bars),
            "mark_bar_count": len(mark_bars),
            "funding_event_count": len(funding_events),
            "funding_schedule_identity": schedule.schedule_identity,
            "funding_source_collection_identity": schedule.source_object_sha256,
            "funding_source_object_sha256s": [item.sha256 for item in funding_archive_records],
            "funding_native_binding": FUNDING_NATIVE_BINDING_RC2_INTERVAL_BOUNDARY,
            "metadata_identity": perp_metadata.instrument_metadata_identity,
            "historical_metadata_exact": perp_metadata.historical_exact,
            "metadata_limitations": perp_metadata.limitations,
        },
        "fee_basis": {
            "maker": FEE_RATE,
            "taker": FEE_RATE,
            "claim_class": "ESTIMATED_FEE",
            "historical_account_tier_exact": False,
        },
        "no_repair": True,
        "no_interpolation": True,
        "no_missing_bar_fill": True,
        "no_role_substitution": True,
    }
    _preserve(
        EVIDENCE_ROOT / "data-acquisition.json",
        canonical_json_bytes(acquisition_payload) + b"\n",
    )
    print(
        canonical_json_bytes(
            {
                "status": "PASS",
                "spot_dataset_release_id": spot_release.dataset_release_id,
                "perpetual_dataset_release_id": perp_release.dataset_release_id,
                "spot_protocol_id": spot_protocol.protocol_id,
                "perpetual_protocol_id": perp_protocol.protocol_id,
                "spot_candidate_id": spot_candidate.candidate_id,
                "perpetual_candidate_id": perp_candidate.candidate_id,
            },
        ).decode("utf-8"),
    )
    return 0


def _entrypoint() -> int:
    try:
        return main()
    except Exception as exc:
        observed = datetime.now(UTC)
        material = {
            "schema": "owner-smoke-data-acquisition-failed-attempt-v1",
            "authorization_id": "OWNER_OPERATIONAL_SMOKE_001",
            "observed_at_utc": observed,
            "error_type": type(exc).__name__,
            "detail": str(exc),
            "raw_bytes_preserved_before_parsing_where_downloaded": True,
            "failure_not_erased": True,
        }
        identity = canonical_sha256(material)
        _preserve(
            EVIDENCE_ROOT / "data-acquisition-failures" / f"{identity}.json",
            canonical_json_bytes({"attempt_identity": identity, **material}) + b"\n",
        )
        raise


if __name__ == "__main__":
    raise SystemExit(_entrypoint())
