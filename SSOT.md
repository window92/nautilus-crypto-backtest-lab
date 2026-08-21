# Nautilus Crypto Backtest Lab — SSOT v1

Status: `OWNER_REVIEW_CANDIDATE`

Adoption: `PENDING_OWNER_ADOPTION`

This document defines one strict, offline crypto backtesting laboratory built on NautilusTrader. It is the complete engineering specification for V1.

This document does not authorize implementation. Implementation starts only after the Owner adopts the exact document bytes and gives a separate implementation instruction.

## 0. Read this first

### 0.1 Goal

Build the smallest laboratory that can answer one question with defensible evidence:

> Did this strategy survive a causal, reproducible historical test under the frozen data, execution, cost, and research rules?

The laboratory MUST prevent a good-looking result from becoming an Official Result when the result depends on look-ahead, same-bar execution, hidden defaults, silent data repair, omitted failed trials, reused Holdout data, non-reproducible settings, or unsupported claims.

The laboratory MUST NOT rebuild functionality that the pinned NautilusTrader runtime already provides correctly for the selected profile.

### 0.2 Design rule

Use NautilusTrader first.

If NautilusTrader v1.231.0 already owns a behavior in the official backtest path, use that behavior. Do not create a second matching engine, order state machine, position engine, account ledger, PnL engine, margin engine, fee engine, funding engine, portfolio engine, or report engine.

Add project code only when NautilusTrader does not provide the control that the laboratory needs for data integrity, causality, reproducibility, research governance, validation, or evidence.

A project wrapper MUST NOT silently replace a Nautilus result with a project-derived result.

### 0.3 Agent rule: proceed or stop

The implementation agent MUST continue autonomously when this SSOT and the pinned NautilusTrader runtime determine the answer.

The agent MUST NOT ask the Owner about naming, helper functions, internal module boundaries, test organization, formatting, or another implementation detail that does not change material behavior.

The agent MUST stop and ask the Owner before making a choice that can change any of these:

- a Fill price, quantity, side, time, or eligibility;
- data availability or timestamp meaning;
- instrument identity or market profile;
- fees, funding, margin, PnL, Equity, or position state;
- a research partition, Holdout state, trial history, metric, or claim;
- the pinned NautilusTrader runtime;
- an Official Run input after that input is frozen;
- a dependency or architecture boundary defined by this SSOT;
- a required network, secret, privilege, or host change that is not already authorized.

If a required Nautilus capability is absent or materially incompatible with this SSOT, the agent MUST stop. It MUST NOT build a replacement engine behavior unless the Owner changes this SSOT.

### 0.4 Normative words

`MUST` and `MUST NOT` are acceptance rules.

`MAY` marks implementation freedom only. A `MAY` rule MUST NOT change a Fill, money, data semantics, research exposure, reproducibility, or claim eligibility.

`BLOCKED` means the run or build step stops without inventing a fallback.

`REJECTED` means an input or order is rejected before the prohibited state occurs.

### 0.5 Authority

After adoption, the repository-root `SSOT.md` is the sole normative project specification.

Code, tests, configuration files, generated schemas, reports, README files, and comments implement or explain this SSOT. They do not override it.

If code and this SSOT disagree, the code is wrong unless the Owner first adopts a new SSOT version.

------------------------------------------------------------------------

## 1. Locked V1 scope

### 1.1 Official engine

The only official backtest engine is NautilusTrader.

The V1 runtime is pinned to:

| Field             | Required value                                                     |
|-------------------|--------------------------------------------------------------------|
| Package           | `nautilus_trader`                                                  |
| Version           | `1.231.0`                                                          |
| Source repository | `nautechsystems/nautilus_trader`                                   |
| Source commit     | `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`                         |
| Linux wheel       | `nautilus_trader-1.231.0-cp312-cp312-manylinux_2_35_x86_64.whl`    |
| Wheel SHA-256     | `8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216` |
| Python ABI        | CPython `3.12`                                                     |
| Host class        | Linux x86-64, glibc 2.35 or newer                                  |

The implementation MUST use the pinned official wheel. Do not build Nautilus from source for V1. The published wheel provenance binds the artifact to the pinned source commit.

The implementation MUST verify the wheel SHA-256 before installation.

The project MUST NOT fork, patch, vendor, or modify NautilusTrader.

The project MUST NOT run `pip install -U` or otherwise float the Nautilus version.

A different Nautilus version, wheel, source commit, Python major/minor version, or architecture creates a new laboratory runtime version. It cannot silently reuse V1 Official Results.

The exact Python patch version and the complete resolved dependency set MUST be recorded in `runtime.lock.json` before the first Official Run. Every Official Run MUST match that lock.

### 1.2 Market profiles

V1 supports exactly two Official Run profiles:

``` text
BINANCE_SPOT_CASH_LONG_ONLY
BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING
```

`BINANCE_SPOT_CASH_LONG_ONLY`:

- uses a Nautilus `CASH` account;
- permits long inventory only;
- permits buys and position-reducing sells;
- forbids borrowing, margin, short inventory, and funding.

`BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING`:

- uses a Nautilus margin account;
- uses one-way `NETTING` position semantics;
- permits long and short exposure;
- permits increase, reduce, and close;
- permits reversal only as two separate Nautilus orders: first close the current position exactly to `FLAT`, then submit a new order in the opposite direction after Nautilus has reported the position `FLAT`;
- forbids one order from crossing an existing non-flat position through zero;
- uses linear USDT settlement;
- forbids Hedge Mode, inverse contracts, and COIN-M;
- sets research leverage to `1` unless a future SSOT version explicitly adds another leverage policy.

### 1.3 Official Run isolation

One Official Run MUST contain exactly:

- one Market Profile;
- one Instrument;
- one Strategy Specification;
- one Dataset Release;
- one frozen Run Configuration;
- one Initial Capital allocation.

Two Official Runs MUST NOT share mutable cash, positions, orders, strategy state, or account state.

A multi-instrument research report MAY compare completed independent Runs. It MUST NOT turn those Runs into a shared portfolio unless a future SSOT version defines portfolio accounting.

### 1.4 V1 execution-data class

Official V1 execution uses external one-minute bars.

The external execution bar interval is exactly `1m`.

V1 does not claim tick-level, queue-level, or order-book reconstruction.

V1 Official Results MUST identify the execution model as bar-based and `ESTIMATED_EXECUTION`.

### 1.5 V1 order scope

Official V1 strategy execution permits `MARKET` orders only.

V1 permits at most one strategy-created order in a non-terminal Nautilus lifecycle state for the Run Instrument at any time. A strategy MAY produce several internal signals, but its frozen `conflict_rule` MUST deterministically reduce them to zero or one order intent before submission. After an order is submitted, no later strategy order may be submitted until Nautilus has emitted a terminal lifecycle outcome for that order and the resulting Fill, position, and account state are observable to the strategy. “Terminal” means terminal according to the pinned Nautilus order lifecycle API; the project MUST NOT invent a parallel order-state enum. A second submission while a prior strategy order is still non-terminal is rejected before Nautilus submission with `CONCURRENT_STRATEGY_ORDER_REJECTED`.

This single-live-order rule applies whether the current position is `FLAT` or non-flat. It prevents order-arrival races from creating a position transition that no individual pre-submit check could prove safe. It does not replace Nautilus order lifecycle handling; it only constrains which strategy orders V1 is allowed to submit.

Limit, stop, stop-limit, market-if-touched, trailing, bracket, contingent, and resting orders are outside V1 because bar OHLC paths cannot prove the historical intrabar sequence.

If a strategy requires an excluded order type, the agent MUST stop and report `UNSUPPORTED_V1_ORDER_TYPE`. It MUST NOT approximate the order with another type.

### 1.6 Out of scope

V1 excludes:

- live trading;
- paper or shadow trading;
- HFT or latency-sensitive claims;
- order-book reconstruction;
- queue-position claims;
- Spot margin or borrowing;
- COIN-M or inverse perpetuals;
- Hedge Mode;
- multi-instrument shared portfolios;
- liquidation modeling;
- a second official backtest engine;
- a custom replacement for a Nautilus engine subsystem;
- a trading UI.

------------------------------------------------------------------------

## 2. Ownership boundaries

### 2.1 Nautilus-owned truth

For an Official Run, NautilusTrader owns:

- the backtest event loop and clock;
- market-state processing;
- strategy callbacks;
- order creation and lifecycle;
- risk checks that belong to the configured Nautilus runtime;
- simulated exchange behavior;
- matching and Fill generation;
- Fill identity, side, quantity, price, and time;
- position lifecycle;
- account balances;
- realized and unrealized PnL;
- margin accounting;
- instrument fee accounting when configured through the Nautilus instrument or fee model;
- perpetual funding settlement when driven by the required Nautilus funding data path;
- portfolio state;
- native backtest reports and statistics.

The project MUST NOT maintain a second authoritative ledger for these values.

### 2.2 Project-owned controls

The project owns only the controls that sit around Nautilus:

- runtime locking and configuration validation;
- raw-data acquisition records and checksums;
- Dataset Release construction and completeness checks;
- timestamp normalization at the data boundary;
- point-in-time instrument metadata records;
- frozen Run Configuration;
- strategy safety restrictions defined by this SSOT;
- causal execution invariants;
- independent invariant checks over Nautilus outputs;
- complete trial history;
- research partitions and Holdout consumption;
- claim eligibility;
- evidence packaging;
- deterministic rerun checks.

### 2.3 No duplicate financial truth

The project MUST NOT recompute a second Official cash balance, position book, realized PnL, or Equity path and then choose between it and Nautilus.

The project MAY compute independent expected values in tests and invariant checks. Those values are verification evidence only.

If a checker and Nautilus disagree on a material invariant, the Run is `BLOCKED`. The project MUST NOT replace Nautilus output with the checker output.

### 2.4 One owner per financial effect

Every financial effect MUST have one owner.

For V1:

| Effect                  | Owner                                           |
|-------------------------|-------------------------------------------------|
| Fill price and quantity | Nautilus                                        |
| Exchange fee            | Nautilus                                        |
| Position and PnL        | Nautilus                                        |
| Margin                  | Nautilus                                        |
| Perpetual funding       | Nautilus                                        |
| Portfolio and Equity    | Nautilus                                        |
| Cost-stress scenario    | Nautilus in a separate frozen Run Configuration |

An Official Run MUST NOT charge the same fee, funding payment, or execution cost through two paths.

------------------------------------------------------------------------

## 3. Runtime and configuration contract

### 3.1 `runtime.lock.json`

The build MUST create one runtime lock before the first Official Run.

It MUST contain at least:

``` text
nautilus_version
nautilus_source_commit
nautilus_wheel_filename
nautilus_wheel_sha256
python_version
platform
machine_architecture
dependency_lock_sha256
timezone
locale
```

`timezone` MUST be `UTC`.

Runtime Lock identity covers the locked execution runtime, platform, dependency set, timezone, and locale. It is not the project's Git Source Revision identity. `runtime.lock.json` MUST NOT contain `project_git_commit` or a project-source-tree digest invented by this project.

Project source identity is recorded separately as immutable Source Revision evidence for each Run. That evidence contains `repository`, `branch_ref`, the full `git_commit` returned by `git rev-parse HEAD`, the full `git_tree` returned by `git rev-parse HEAD^{tree}`, `clean_worktree`, and `captured_at_utc`. The capture timestamp is evidence metadata; Git commit and tree object IDs are the source identities. Section 10.5 defines the persisted Run evidence shape.

A missing or mismatched Runtime Lock field makes an Official Run `BLOCKED` before data loading. Missing, dirty, changed-after-freeze, or otherwise invalid Source Revision evidence also blocks an Official Run before data loading, but it does not change Runtime Lock identity.

### 3.2 Explicit Nautilus configuration

A material Nautilus default MUST NOT remain implicit.

`LabRunConfig` MUST serialize every setting that can change data visibility, order eligibility, matching, Fill behavior, account behavior, fees, funding, margin, portfolio valuation, or determinism.

At minimum, the resolved configuration MUST record the effective values for:

``` text
book_type
oms_type
account_type
starting_balances
base_currency
default_leverage
instrument leverage when applicable
margin_model
fill_model
fee_model
latency_model
bar_execution
bar_adaptive_high_low_ordering
trade_execution
liquidity_consumption
queue_position
allow_cash_borrowing
frozen_account
reject_stop_orders
support_gtd_orders
support_contingent_orders
use_position_ids
use_random_ids
use_reduce_only
use_market_order_acks
price_protection_points
risk-engine configuration
data-engine time-bar configuration
portfolio valuation configuration
random seeds used by any enabled model
```

If v1.231.0 exposes a material setting under a different exact name, use the v1.231.0 name and record it. Do not use a newer-version name as a substitute.

### 3.3 Required V1 execution settings

For the bar-based V1 profile:

``` text
book_type = L1_MBP
bar_execution = true
trade_execution = false
bar_adaptive_high_low_ordering = false
liquidity_consumption = true
queue_position = false
routing = false
reject_stop_orders = true
support_gtd_orders = false
support_contingent_orders = false
use_random_ids = false
use_reduce_only = true
use_market_order_acks = false
allow_cash_borrowing = false
frozen_account = false
price_protection_points = 0
time_bars_timestamp_on_close = true
time_bars_build_with_no_updates = false
time_bars_skip_first_non_full_bar = true
Spot: PortfolioConfig.use_mark_prices = false
Perpetual: PortfolioConfig.use_mark_prices = true
```

Any material v1.231.0 venue or engine field not listed above MUST still be serialized with its effective value. If this SSOT does not constrain that field, use the v1.231.0 default and record it explicitly. Do not choose a non-default value without a material requirement in this SSOT.

The V1 exchange-fee path is singular. `BacktestVenueConfig.fee_model` MUST be serialized as `None` so the pinned v1.231.0 venue resolves its standard `MakerTakerFeeModel`, and the Instrument `maker_fee` and `taker_fee` MUST be populated from the frozen `fee_assumption`. The resolved Run evidence MUST record that the effective fee model is `MakerTakerFeeModel`. `FixedFeeModel`, another custom fee model, or a project-side exchange-fee debit is forbidden in an Official Run.

`price_protection_points=0` means price protection is disabled. Do not encode this value as `null`.

The Fill Model MUST be deterministic. Any probabilistic behavior MUST use a frozen seed and MUST be declared in the Run Configuration.

Use the pinned runtime's standard Fill Model with these effective values: `prob_fill_on_limit=1.0`, `prob_slippage=1.0`, and `random_seed=0`. For L1 execution this applies the runtime's deterministic one-tick adverse slippage behavior to every Fill. Record the exact v1.231.0 class name in the resolved Run Configuration.

The project MUST NOT implement a second Fill Model outside Nautilus.

### 3.4 Causal latency rule

A strategy may create an order only after the signal input is available.

An order created from completed bar `N` MUST NOT fill against the market state of bar `N`.

The V1 run configuration MUST use the latency-model implementation exposed by the pinned v1.231.0 wheel and accepted by the v1.231.0 backtest venue path. M0 MUST resolve and record the exact import path and class name from that pinned artifact; it MUST NOT copy a class name from another Nautilus release.

The normative latency semantics are:

``` text
base_latency_nanos = 60_000_000_000
insert_latency_nanos = 0
update_latency_nanos = 0
cancel_latency_nanos = 0
effective_insert_latency_nanos = base_latency_nanos + insert_latency_nanos = 60_000_000_000
```

The model MUST be the pinned runtime's standard constant/static latency implementation; a custom, stochastic, or data-dependent latency model is forbidden in V1. Record the exact resolved class path and all four latency components in material configuration as required by Section 3.2. The one-minute effective insert latency is fixed V1 behavior and MUST NOT be tuned from strategy results. If the pinned wheel cannot construct the required constant-latency model with these semantics, M0 is `BLOCKED` with `UNSUPPORTED_RUNTIME`.

The acceptance test uses synthetic bars whose prices are disjoint across adjacent minutes. It MUST prove both:

``` text
fill_time > signal_bar_available_at
fill_price is not a price from signal bar N
```

The test MUST include a negative control that removes the causal delay. The negative control MUST fail the invariant.

The exact later market state used by Nautilus is part of the pinned engine behavior. The project MUST NOT rewrite the Fill to force a preferred next-open or next-close price.

If the pinned Nautilus configuration cannot satisfy the invariant, implementation MUST stop with `CAUSAL_EXECUTION_UNRESOLVED`.

### 3.5 Content identity

Frozen specifications and manifests use SHA-256 over canonical JSON material payloads. Human labels, creation timestamps, filesystem paths, and the identity field itself are excluded unless this SSOT says they are material.

The same material payload MUST produce the same content identity. A material change MUST produce a different identity.

`run_id` and `trial_id` identify occurrences; they are not inputs to the semantic configuration hash.

### 3.6 Network rule

Network access is allowed only during an explicit setup or data-acquisition step.

An Official Run MUST execute with network access disabled.

A strategy MUST NOT open a network connection during an Official Run.

A network attempt during an Official Run makes the Run `BLOCKED`.

------------------------------------------------------------------------

## 4. Data contract

### 4.1 Source

The V1 Official market-data source is Binance Public Data.

Use official Binance public archives and public market-data endpoints only for V1 Dataset Releases.

For the V1 historical archive roles, the allowed Binance Public Data prefixes are:

``` text
Spot execution 1m:
data/spot/daily/klines/{SYMBOL}/1m/
data/spot/monthly/klines/{SYMBOL}/1m/

USDⓈ-M Perpetual execution 1m:
data/futures/um/daily/klines/{SYMBOL}/1m/
data/futures/um/monthly/klines/{SYMBOL}/1m/

USDⓈ-M Perpetual mark 1m:
data/futures/um/daily/markPriceKlines/{SYMBOL}/1m/
data/futures/um/monthly/markPriceKlines/{SYMBOL}/1m/

USDⓈ-M Perpetual funding:
data/futures/um/monthly/fundingRate/{SYMBOL}/
```

`indexPriceKlines`, `premiumIndexKlines`, Spot prices, last prices, and third-party datasets MUST NOT substitute for the Perpetual mark or funding roles above.

Do not use a third-party repaired dataset as Official input.

### 4.2 Raw bytes are immutable

For every downloaded source object, preserve:

``` text
source_role
source_locator
acquired_at_utc
exact_filename
byte_size
sha256
publisher_checksum when available
instrument
market_profile
requested_interval
requested_time_range
```

Store the exact downloaded bytes before parsing.

Never overwrite a raw object in place. If Binance later republishes the same path with different bytes, store both objects with different hashes and record the replacement relationship.

A publisher checksum match proves transfer integrity. It does not prove completeness or semantic correctness.

### 4.3 Dataset Release

A `DatasetRelease` is an immutable manifest over exact raw-object hashes plus the Nautilus catalog derived from them.

It MUST bind:

``` text
dataset_release_id
market_profile
instrument_id
source_objects
normalized_time_range
execution_bar_interval
available_signal_bar_intervals
instrument_metadata_identity
funding_data_identity or NOT_APPLICABLE
mark_data_identity or NOT_APPLICABLE
normalizer_version
catalog_identity
completeness_result
created_at_utc
```

The release ID MUST be a SHA-256 over canonical JSON for the material fields. `created_at_utc` and physical storage paths are evidence metadata and are excluded from the content identity. JSON keys are sorted. Decimal values are strings. NaN and Infinity are forbidden.

Changing any raw object, parser rule, timestamp rule, instrument metadata, or derived catalog creates a new Dataset Release.

### 4.4 Time semantics

All internal timestamps use UTC.

All study windows use half-open intervals:

``` text
[start_inclusive, end_exclusive)
```

For every source field, the parser MUST know the source timestamp unit from the source contract. It MUST NOT infer milliseconds, microseconds, or nanoseconds from the numeric magnitude alone.

For Binance Spot archive data, timestamps before `2025-01-01T00:00:00Z` use milliseconds and Spot timestamps from that boundary onward use microseconds, as defined by Binance Public Data. The parser MUST select the unit from the source role and date, not from digit count.

The Spot timestamp transition MUST NOT be copied to USDⓈ-M Futures. Each USDⓈ-M source role MUST use the timestamp unit defined by its own verified official source schema. If that unit cannot be established for a required role, the Dataset Release is `BLOCKED` with `DATA_TIMESTAMP_INVALID`.

For a one-minute kline:

``` text
interval_start = source open time
interval_end_exclusive = interval_start + 60 seconds
available_at = interval_end_exclusive
```

A completed bar MUST NOT become visible to a strategy before `available_at`.

The Nautilus bar initialization time used for causal availability MUST equal the normalized completion boundary required by the pinned runtime path.

A parser or catalog conversion that makes the full OHLCV bar visible before `available_at` is a fatal data error.

### 4.5 Completeness

The release builder MUST calculate the expected one-minute UTC grid for every execution window.

For each expected minute, exactly one valid execution bar MUST exist.

For a Perpetual Dataset Release, the required `markPriceKlines` role MUST also satisfy an exact one-minute UTC grid for every interval in which Official valuation can occur. Each required minute MUST resolve to exactly one valid mark bar.

For Perpetual funding, the release builder MUST derive the expected funding-event schedule only from verified official Binance funding data or metadata for the exact Instrument and historical interval. V1 MUST NOT hard-code an eight-hour cadence unless the official evidence proves that cadence for the tested interval. Every expected funding slot in `[start_inclusive, end_exclusive)` MUST resolve to exactly one funding event. If the expected schedule itself cannot be proven, the Dataset Release is `BLOCKED` with `FUNDING_AMBIGUOUS`; if a proven required event is absent, it is `BLOCKED` with `FUNDING_MISSING`.

A duplicate, missing required row, conflicting duplicate, malformed row, non-monotonic timestamp, invalid OHLC relation, negative volume, or unresolved source conflict makes the affected interval unusable.

The project MUST NOT:

- interpolate a missing bar;
- forward-fill or backward-fill a price;
- use the nearest row;
- invent a zero-volume bar;
- copy a Spot row into a Perpetual role;
- copy a last-price row into a mark-price role;
- silently deduplicate conflicting rows.

If a required execution or mark minute is unusable, the Official Run is `BLOCKED` with `DATA_GAP` unless a more specific role-conflict code applies. A conflicting or semantically unresolved funding event is `FUNDING_AMBIGUOUS`; a proven missing required funding event is `FUNDING_MISSING`.

### 4.6 Higher timeframes

The one-minute external bars are the execution source.

Strategies MAY use higher timeframes only when those bars are produced causally by NautilusTrader from already available lower-timeframe data or loaded as separately validated completed external bars.

Use Nautilus's supported internal aggregation when the pinned runtime supports the required source and target bar types. Do not build a project-owned bar aggregation engine merely to duplicate a supported Nautilus path.

For any internally aggregated bar:

- the bar MUST close on the intended UTC boundary;
- the strategy MUST receive it only after all source minutes are available;
- a missing required source minute MUST prevent the derived bar from becoming valid Official input;
- no partial higher-timeframe bar may be used as a completed signal bar.

If the pinned runtime cannot produce a required higher-timeframe bar with these semantics, stop with `TIMEFRAME_AGGREGATION_UNRESOLVED` instead of inventing a second aggregation path.

### 4.7 Instrument metadata

Every Dataset Release MUST contain the instrument definition used by Nautilus.

The definition MUST include every field that can change quantity, price, notional, fees, margin, or settlement. This includes the available price and size precision, increments, limits, currencies, contract type, multiplier, margin rates, and fee rates.

Metadata MUST carry source and observation time.

If exact historical metadata is unavailable, the Run MUST disclose that limitation. It MUST NOT claim exact historical venue-rule reconstruction.

A current instrument definition MUST NOT be silently presented as a historical point-in-time fact.

### 4.8 Spot data roles

A Spot Dataset Release requires:

- external `1m` price bars from the exact Spot daily or monthly `klines/{SYMBOL}/1m/` prefixes listed in Section 4.1;
- the instrument definition;
- the source checksums or locally calculated SHA-256 values.

The Spot parser MUST apply the timestamp-unit transition in Section 4.4.

Funding and derivative mark-price roles are forbidden for Spot.

### 4.9 Perpetual data roles

A USDⓈ-M Perpetual Dataset Release requires:

- external `1m` contract price bars from the exact USDⓈ-M daily or monthly `klines/{SYMBOL}/1m/` prefixes listed in Section 4.1;
- the instrument definition;
- historical funding-rate data from `data/futures/um/monthly/fundingRate/{SYMBOL}/`;
- `1m` mark-price bars from the exact USDⓈ-M daily or monthly `markPriceKlines/{SYMBOL}/1m/` prefixes listed in Section 4.1;
- source checksums or locally calculated SHA-256 values.

The funding and mark roles MUST pass the completeness rules in Section 4.5 before the release can support an Official Perpetual Run.

A missing or ambiguous funding or mark event MUST NOT be replaced with a last price, index price, premium-index price, nearest mark, or synthetic value.

### 4.10 Funding identity and duplicates

Do not identify a funding event by page position.

Do not deduplicate funding rows by timestamp alone unless the official source contract proves timestamp uniqueness for the selected data class.

Preserve raw duplicates until their semantic identity is resolved.

If two materially different rows compete for the same required funding event and the source contract does not resolve them, the affected Run is `BLOCKED`.

### 4.11 Catalog is derived data

The Nautilus catalog is derived from the immutable raw source objects.

The raw bytes and Dataset Release manifest are the provenance authority.

If the catalog is deleted or corrupted, rebuild it from the same raw objects and the same normalizer version.

A rebuilt catalog MUST produce the same semantic data inventory. If it does not, the Dataset Release is stale and cannot support an Official Run.

------------------------------------------------------------------------

## 5. Strategy contract

### 5.1 Strategy interface

An Official Strategy is a normal Nautilus `Strategy` implementation plus an immutable `StrategySpec`.

`StrategySpec` MUST contain:

``` text
strategy_id
strategy_version
market_profile
instrument_id
signal_bar_types
parameters
indicator definitions and lookbacks
warmup requirement
sizing rule
entry rule
exit rule
conflict rule
terminal behavior
market_order_time_in_force
```

`strategy_spec_id` is the content identity of the frozen material `StrategySpec` payload. A material rule, parameter, sizing, timing, or indicator change creates a new identity.

Every material parameter MUST be explicit. Missing material parameters are invalid. The strategy MUST NOT invent defaults that are absent from the frozen `StrategySpec`.

`market_order_time_in_force` MUST record the exact effective Time-In-Force used by the pinned v1.231.0 MARKET-order path. Do not assume `IOC` or another value from a newer version. M1 MUST verify the effective value and prove that partial-fill or remainder behavior cannot create a Fill outside the causal or terminal contract; otherwise the affected profile is `BLOCKED`.

### 5.2 Allowed inputs

During an Official Run, strategy decisions MAY use only:

- completed subscribed market data whose availability time is not in the future;
- Nautilus indicator state built only from those data;
- the strategy's own prior state;
- the Nautilus cache and portfolio state for the current isolated Run when the strategy rule explicitly needs them;
- frozen `StrategySpec` values.

### 5.3 Forbidden inputs and effects

During an Official Run, the strategy MUST NOT:

- read future bars or future timestamps;
- read files for new trading information;
- call the network;
- query an external database or service;
- use wall-clock time as market time;
- read results from another Run;
- change the Dataset Release;
- change its own frozen parameters;
- mutate Nautilus Fill events;
- write directly to account, position, PnL, or portfolio state;
- create an excluded V1 order type.

### 5.4 Warmup

Warmup exists only to initialize causal strategy and indicator state.

Warmup MUST NOT submit any strategy order to Nautilus. It may update indicators and causal strategy state only.

A bar is eligible to participate as a contemporaneous scored order trigger only if its entire interval lies inside the scoring window. Every material signal bar used by that order-decision event MUST satisfy:

``` text
for each material signal_bar:
    signal_bar.interval_start >= scoring_start
    and signal_bar.interval_end_exclusive <= scoring_end_exclusive
```

A warmup bar whose interval is `[scoring_start - bar_interval, scoring_start)` is still a warmup bar even though its `available_at` equals `scoring_start`; it MUST NOT trigger an order. Warmup data MAY influence indicator state used by a later eligible signal bar.

At `scoring_start`, the Official Run MUST have the frozen Initial Capital, a `FLAT` position, and zero submitted, in-flight, or otherwise non-terminal strategy orders. The runner MUST assert this boundary before the first scored order is allowed.

If the strategy needs position carry from a prior period, that is a different research contract and is outside V1.

### 5.5 Determinism

Given the same Runtime Lock, Dataset Release, StrategySpec, and LabRunConfig, the strategy MUST generate the same order-intent sequence.

Any strategy randomness MUST use a frozen seed recorded in `StrategySpec`.

Unseeded randomness makes the Run `BLOCKED`.

------------------------------------------------------------------------

## 6. Execution and account contract

### 6.1 Actual Fill

A Nautilus Fill is the only Official execution event.

The project MUST preserve the Fill's:

``` text
order identity
fill identity
instrument
side
quantity
price
event time
commission data
```

The project MUST NOT change a Fill price, quantity, side, time, or identity after Nautilus emits it.

The project MUST NOT replace an inconvenient Fill with `NO_FILL`.

If Nautilus emits partial Fills, preserve them exactly. Strategy and research logic MUST use the resulting Nautilus position state; project code MUST NOT silently resize a partial Fill into a full Fill or cancel the observed portion after the fact.

### 6.2 No same-bar economic execution

A signal that uses completed bar `N` may create an order only after bar `N` is available and only when every material contemporaneous signal bar for that order satisfies the scoring-eligible rule in Section 5.4.

That order MUST NOT fill against bar `N` market state.

This invariant is more important than a preferred next-bar price.

The project accepts the later Fill price emitted by the pinned Nautilus execution path. It does not force `next_open`, `next_close`, `worst_price`, or another synthetic price.

### 6.3 Spot profile

For `BINANCE_SPOT_CASH_LONG_ONLY`:

- `account_type` MUST be `CASH`;
- `oms_type` MUST be `NETTING`;
- cash borrowing MUST be disabled;
- the strategy MUST NOT create an order that intentionally opens short inventory;
- a sell quantity MUST NOT exceed the available long holding under the frozen sizing rule;
- funding is forbidden.

If Nautilus produces a negative Spot inventory or an unauthorized borrowed balance, the Run is `BLOCKED`. Preserve the engine evidence.

### 6.4 Perpetual profile

For `BINANCE_USDM_LINEAR_PERPETUAL_ONE_WAY_NETTING`:

- `account_type` MUST be `MARGIN`;
- `oms_type` MUST be `NETTING`;
- one Instrument has one signed net position;
- `default_leverage` MUST be `1` unless this SSOT is revised;
- Hedge Mode is forbidden;
- the configured instrument MUST be linear and USDT-settled;
- funding uses the Nautilus funding settlement path;
- liquidation simulation remains disabled in V1.

The global single-live-order rule in Section 1.5 applies to Perpetual orders.

V1 MUST NOT submit an order that can cross an existing non-flat net position through zero. If the current signed position is positive, a candidate SELL quantity MUST be `<=` the current long quantity; if the current signed position is negative, a candidate BUY quantity MUST be `<=` the absolute short quantity. This bound applies to the opposing side only; a same-side increase remains subject to the normal frozen sizing, margin, and leverage rules. An exact opposing-side close to `FLAT` is allowed.

A reversal MUST be two separate Nautilus submissions. First submit an exact close-to-flat order. No opposite-side reopen may be submitted until that close order is terminal, every resulting Fill has been observed, Nautilus reports the position `FLAT`, and there is no other non-terminal strategy order. The new opposite-side order then receives the normal causal latency as a separate submission.

If a strategy emits an order whose own quantity would cross zero, preserve the intent, submit no order to Nautilus, and mark the Run `BLOCKED` with `CROSS_ZERO_ORDER_REJECTED`. If a strategy attempts any second order while another strategy order is non-terminal, preserve the second intent, submit it to neither venue nor engine, and mark the Run `BLOCKED` with `CONCURRENT_STRATEGY_ORDER_REJECTED`.

This restriction exists because the pinned v1.231.0 Python/Cython position path retains the old-side entry price on a direct cross-zero fill. The project MUST NOT repair that behavior with a second position or PnL engine.

### 6.5 Fees

The exchange fee used by the Official Run MUST be represented once through Nautilus.

The only V1 exchange-fee channel is the pinned runtime's standard `MakerTakerFeeModel`, using the Instrument `maker_fee` and `taker_fee` populated from the frozen `fee_assumption`. A second Nautilus fee model and any project-side exchange-fee debit are forbidden.

A missing fee assumption is not zero. If the Owner intends zero fees for a diagnostic Run, the Run Configuration MUST say `explicit_zero_fee=true` and state the reason.

A V1 fee claim is `ESTIMATED_FEE` unless the Dataset Release proves the exact historical account fee tier for the tested period.

The project MUST NOT reconstruct an assumed BNB discount or third-asset commission unless a future SSOT version defines that data contract.

### 6.6 Slippage and spread

Nautilus owns any Fill-price slippage model used by the Official execution path.

The project MUST NOT modify a Fill to add spread or slippage.

For the V1 bar profile, Nautilus FillModel slippage is fixed at `prob_slippage=1.0`. This is a deterministic one-tick adverse execution assumption, not a reconstruction of historical spread or market impact.

A cost-stress scenario MUST be a separate Nautilus Run with a new frozen `LabRunConfig`. Change only the declared Nautilus fee or FillModel assumptions required by the stress scenario. Label the result `ESTIMATED_COST_STRESS`. Do not alter a completed baseline Run after the fact.

A strategy whose edge materially depends on historical spread, queue position, or market impact is not supported by the V1 bar profile. Its claim is `INELIGIBLE` until a future higher-granularity profile exists.

### 6.7 Funding

Perpetual funding is owned by Nautilus.

The project supplies the frozen funding data required by the pinned Nautilus path and preserves the resulting settlement evidence.

The project MUST NOT post a second funding cashflow.

If the pinned runtime cannot perform the required native funding settlement from the frozen funding data, the Perpetual profile is `BLOCKED`. The project MUST NOT add a replacement funding ledger.

For a positive funding rate, the expected direction is that long exposure pays and short exposure receives. The golden suite MUST contain a known-result case that proves the sign and timing.

A funding settlement that occurs twice, uses an unresolved mark, or cannot be tied to the required source event makes the Run `BLOCKED`.

### 6.8 Valuation

Use Nautilus portfolio and account valuation as the Official financial state.

For Perpetual runs, `PortfolioConfig.use_mark_prices` MUST be `true`. The configured valuation path MUST use the required `markPriceKlines` role. The Dataset Release completeness check MUST ensure the required mark is present before the Run can proceed, and the checker MUST fail with `MARK_ROLE_INVALID` if Official Perpetual valuation uses last price, contract-price bars, index price, or another fallback instead of the required mark.

For Spot runs, `PortfolioConfig.use_mark_prices` MUST be `false`; derivative mark-price data are `NOT_APPLICABLE`.

If the pinned runtime cannot bind the required mark role to the Official Perpetual valuation path, the Perpetual profile is `BLOCKED` with `MARK_ROLE_INVALID`.

A mark price and a funding rate are different data roles. The project MUST NOT substitute one for the other.

### 6.9 Terminal boundary

The V1 terminal policy is:

``` text
MARK_OPEN_POSITION_NO_SYNTHETIC_CLOSE
```

At `scoring_end_exclusive`, do not create a synthetic closing order merely to finish the report.

A strategy order is eligible for Nautilus submission only when both conditions hold:

``` text
every material contemporaneous signal bar satisfies Section 5.4
and
t_submit + effective_insert_latency_nanos < scoring_end_exclusive
```

where `effective_insert_latency_nanos` is the exact frozen value from Section 3.4. If either condition is false, the laboratory MUST NOT submit that order to Nautilus. Preserve the suppressed intent and boundary reason in Run evidence. This is the declared scoring-window policy, not a favorable retry or a synthetic `NO_FILL`.

The runner MUST begin the scoring window with zero non-terminal strategy orders and MUST finish with no strategy order whose effective insert time reaches or crosses `scoring_end_exclusive`. An Official Run MUST contain zero Fills with `ts_event >= scoring_end_exclusive`. If such a Fill occurs, preserve it and mark the Run `BLOCKED` with `CAUSAL_EXECUTION_UNRESOLVED`; do not delete or ignore the Fill.

Funding events with normalized event time `>= scoring_end_exclusive` are outside the scored half-open interval and MUST NOT be loaded as scored funding events. Events strictly before the boundary remain subject to the normal funding contract.

Value any open position using the latest required valuation observation whose normalized `available_at <= scoring_end_exclusive` and whose `interval_end_exclusive <= scoring_end_exclusive`. For Perpetual this MUST be the required mark role from Section 6.8.

The report MUST disclose that the terminal position remained open.

------------------------------------------------------------------------

## 7. Run contract

### 7.1 `LabRunConfig`

Before Nautilus starts, resolve one immutable `LabRunConfig`.

It MUST contain:

``` text
run_id
run_purpose
runtime_lock_sha256
market_profile
instrument_id
dataset_release_id
strategy_spec_id
initial_capital
warmup_start
scoring_start
scoring_end_exclusive
execution_bar_type
signal_bar_types
nautilus_engine_config
nautilus_venue_config
nautilus_data_config
fee_assumption
funding_binding or NOT_APPLICABLE
mark_binding or NOT_APPLICABLE
random_seeds
research_protocol_id or NOT_APPLICABLE
```

`run_purpose` is one of:

``` text
QUALIFICATION
RESEARCH
OFFICIAL
```

Unknown fields and duplicate fields are invalid.

A material field MUST NOT be null unless this SSOT explicitly permits `NOT_APPLICABLE` or explicitly requires the pinned v1.231.0 `fee_model=None` sentinel in Section 3.3.

### 7.2 Run identity

Serialize the resolved material configuration as canonical JSON:

- UTF-8;
- sorted object keys;
- arrays preserve semantic order;
- Decimal values are strings;
- timestamps are UTC strings with explicit `Z`;
- NaN and Infinity are forbidden.

`config_sha256 = SHA256(exact_canonical_json_bytes)` over the material Run Configuration excluding `run_id`.

The Run directory name MUST include the run ID and `config_sha256` prefix.

Changing a material field creates a new Run identity.

### 7.3 Preflight

Before creating the Nautilus backtest node or engine, preflight MUST verify:

1.  Runtime Lock matches the current execution process and locked dependencies. Runtime Lock matching does not compare a field inside `runtime.lock.json` with Git `HEAD`.
2.  An Official Run has a clean Git worktree, then captures and freezes the Source Revision fields from Section 10.5, including `HEAD` and `HEAD^{tree}`. If either identity changes after freeze, preflight blocks the Run.
3.  Dataset Release resolves and passes completeness checks.
4.  Market Profile and Instrument agree.
5.  StrategySpec agrees with the Instrument and Market Profile.
6.  Initial Capital is finite, positive, and in the required currency.
7.  `warmup_start <= scoring_start < scoring_end_exclusive`; equality between `warmup_start` and `scoring_start` means the Run has zero warmup duration.
8.  The full `[warmup_start, scoring_end_exclusive)` interval required by the Run is inside the Dataset Release and satisfies the applicable completeness contract.
9.  Required fee, mark, and funding inputs exist.
10. All material Nautilus settings are explicit, including the resolved latency-model class and effective insert latency.
11. The configuration contains no excluded order type or profile.
12. The Official Run network policy is enforceable.
13. The configuration hash is frozen.

A failed preflight produces no Official backtest result.

### 7.4 Run states

Every attempted Run has exactly one terminal state:

``` text
COMPLETED
FAILED
BLOCKED
ABORTED
```

`COMPLETED` means Nautilus finished and every required laboratory check passed.

`FAILED` means execution completed but a deterministic expected behavior or test failed.

`BLOCKED` means required evidence or semantics were missing, ambiguous, stale, or incompatible.

`ABORTED` means execution started but did not finish because of an external interruption or explicit stop.

A started Run MUST NOT disappear from the trial history.

### 7.5 No favorable retry selection

A retry is a new attempt unless it is a process resume that produces no independent result.

If several attempts exist, retain all attempts.

Do not choose the best attempt and delete the rest.

------------------------------------------------------------------------

## 8. Verification contract

### 8.1 Verification has two layers

V1 uses only:

1.  golden tests with independently written expected results;
2.  a read-only invariant checker over Nautilus output and Run evidence.

Do not build a second backtest engine or a second Official ledger for verification.

### 8.2 Golden tests come first

For a material behavior, write the failing or expected golden fixture before the production implementation that depends on it.

Expected values MUST come from the SSOT rule, a hand calculation, or a fixed synthetic event sequence. They MUST NOT be copied from the production output that the test is supposed to verify.

### 8.3 Minimum golden suite

The build MUST include at least these tests:

| ID    | Required proof                                                                                                                                         |
|-------|--------------------------------------------------------------------------------------------------------------------------------------------------------|
| `G01` | A bar is not visible before its completion boundary.                                                                                                   |
| `G02` | A signal from bar `N` cannot Fill against bar `N`.                                                                                                     |
| `G03` | Removing the causal latency guard makes `G02` fail.                                                                                                    |
| `G04` | A missing required one-minute execution bar produces `BLOCKED`, not a jump to a later bar.                                                             |
| `G05` | A published Nautilus Fill remains byte-for-byte unchanged in project evidence.                                                                         |
| `G06` | Two fresh runs with the same frozen inputs produce the same semantic order and Fill sequence.                                                          |
| `G07` | Spot CASH cannot create short inventory or borrowing.                                                                                                  |
| `G08` | Perpetual NETTING supports long, reduce, exact close-to-flat, then opposite-side reopen; direct cross-zero quantity and a second concurrent strategy order are both blocked before submission. |
| `G09` | A positive funding rate debits a long and credits a short exactly once at the required boundary.                                                       |
| `G10` | Exchange fee is applied exactly once through the standard Maker/Taker instrument-rate path.                                                            |
| `G11` | Perpetual valuation requires mark price and cannot fall back to last, contract-bar, or index price.                                                    |
| `G12` | A malformed, duplicate-conflicting, or missing raw row is not silently repaired.                                                                       |
| `G13` | A last warmup bar whose `available_at=scoring_start` cannot submit an order; scoring starts flat, at frozen Initial Capital, with zero non-terminal strategy orders. |
| `G14` | An excluded V1 order type is rejected before submission.                                                                                               |
| `G15` | A failed or blocked trial remains in `trials.jsonl`.                                                                                                   |
| `G16` | A previously exposed overlapping market interval cannot later be relabeled as a fresh Final Holdout; first material Holdout exposure also consumes it. |
| `G17` | A report that omits a started failed trial is ineligible.                                                                                              |
| `G18` | Changing a frozen material config field changes `config_sha256`.                                                                                       |
| `G19` | An Official Run attempts no network access.                                                                                                            |
| `G20` | A runtime wheel or dependency-lock mismatch blocks before data loading.                                                                                |
| `G21` | An eligible scored intent whose effective insert latency reaches or crosses `scoring_end_exclusive` is not submitted, and no Fill occurs at or after the boundary. |

### 8.4 Invariant checker

The checker reads persisted evidence after the Nautilus run. It MUST NOT mutate the engine, strategy, account, position, Fill, or result.

The checker MUST verify at least:

- Runtime Lock matches the Run evidence;
- the frozen Source Revision is present, reports a clean preflight worktree, and matches the Run evidence inventory;
- config hash matches the exact config bytes;
- Dataset Release hashes resolve;
- no required data gap was ignored;
- every Fill belongs to the configured Instrument;
- every Fill time is causally valid relative to the signal that created its order;
- every submitted strategy order was triggered by a signal bar eligible under Section 5.4;
- scoring began with frozen Initial Capital, a `FLAT` position, and zero non-terminal strategy orders;
- no two strategy-created orders were simultaneously non-terminal for the Run Instrument;
- Spot never ends or passes through unauthorized short inventory or borrowing;
- Perpetual uses one-way NETTING only;
- no submitted Perpetual order crossed an existing non-flat position through zero;
- required fees use the single standard Maker/Taker instrument-rate path and are present once;
- required funding is present once;
- no project code changed a Nautilus Fill;
- required mark data were present for the configured valuation path and no prohibited valuation fallback was accepted;
- terminal policy was followed and no Fill has `ts_event >= scoring_end_exclusive`;
- the Run evidence inventory is complete;
- the trial journal contains the attempt.

The checker MAY independently calculate small known-value invariants. It MUST NOT become a second Official accounting engine.

### 8.5 Checker outcome

``` text
CHECK_PASS
CHECK_FAIL
CHECK_BLOCKED
```

An Official Result requires `CHECK_PASS`.

`CHECK_FAIL` or `CHECK_BLOCKED` cannot be overridden by profitability.

### 8.6 Deterministic replay

For the same Runtime Lock, frozen Source Revision, Dataset Release, StrategySpec, and LabRunConfig, two clean runs MUST match on the semantic sequence of:

``` text
orders
fills
positions
account events
funding settlements
terminal portfolio state
```

Ignore only fields proven to be non-semantic run-instance metadata.

Any unexplained semantic divergence makes the Run `BLOCKED`.

------------------------------------------------------------------------

## 9. Research integrity contract

### 9.1 Research is separate from backtest mechanics

A mechanically valid backtest can still support a bad research claim.

The laboratory therefore separates:

``` text
MechanicalIntegrity = PASS | FAIL | BLOCKED
ResearchEligibility = ELIGIBLE | EXPLORATORY_ONLY | INELIGIBLE | BLOCKED
```

Profitability cannot change `MechanicalIntegrity`.

Every ResearchProtocol declares `ResearchIntent = EXPLORATORY | CONFIRMATORY`. A `confirmatory claim` is a claim from a `CONFIRMATORY` protocol proposed with `ResearchEligibility=ELIGIBLE`. An `EXPLORATORY` protocol cannot produce an `ELIGIBLE` confirmatory claim; its result is labeled `EXPLORATORY_ONLY`.

### 9.2 `ResearchProtocol`

Before the first result-bearing trial in a study, freeze one `ResearchProtocol`.

It MUST contain:

``` text
research_family_id
hypothesis_id
research_intent
market_profile
instrument_scope
instrument_selection_basis
universe_selection_rule or NOT_APPLICABLE
universe_as_of_rule or NOT_APPLICABLE
universe_membership_sha256 or NOT_APPLICABLE
dataset_release_ids
strategy family
parameter domain or ordered candidate set
search budget
candidate ordering or deterministic generator
random seeds
primary metric
required benchmark
selection rule
tie-break rule
development interval
validation interval
OOS interval when used
final Holdout interval
purge and embargo rule or NOT_APPLICABLE
multiple-testing treatment, `NOT_APPLICABLE` for exactly one predeclared candidate, or `EXPLORATORY_ONLY`
sample_adequacy_rule
monte_carlo_spec or NOT_APPLICABLE
intended_claim_scope
kill criteria
terminal policy
```

`protocol_id` is the content identity of the frozen material `ResearchProtocol` payload. A material research-intent, search, partition, metric, benchmark, cost, universe, sample-adequacy, Monte Carlo, claim-scope, or selection change creates a new identity.

A confirmatory claim MAY use only supporting trials whose `protocol_id` exactly matches the claim's frozen `ResearchProtocol` and whose protocol was frozen before those trials produced result-bearing output. A later protocol MAY analyze earlier trials, but that re-analysis is `EXPLORATORY_ONLY`; it MUST NOT relabel earlier evidence as fresh confirmatory evidence under the later metric, adequacy threshold, selection rule, universe, or claim scope.

A missing material field makes result-bearing research `BLOCKED`.

### 9.3 Trial journal

`trials.jsonl` is append-only at the application level.

Write a trial record before execution starts.

Each record MUST contain:

``` text
trial_id
research_family_id
hypothesis_id
protocol_id
run_id
config_sha256
strategy_spec_id
dataset_release_id
partition_role
seed
state
started_at_utc
finished_at_utc when terminal
result_ref when available
failure_or_block_reason when applicable
```

The allowed states are:

``` text
PLANNED
STARTED
COMPLETED
FAILED
BLOCKED
ABORTED
```

Never delete a failed, blocked, aborted, or losing trial to improve the apparent research record.

### 9.4 Search budget and candidate order

The frozen protocol MUST determine which candidate is tested next.

An AI agent, human, or script MUST NOT inspect a result and then silently expand the parameter domain, symbol set, time window, cost assumption, or candidate budget under the same protocol identity.

A material change after result exposure creates a new protocol version. The new version MUST retain the prior trial and exposure history.

### 9.5 Partitions

Use chronological partitions only.

A partition is one of:

``` text
DEVELOPMENT
VALIDATION
OOS
FINAL_HOLDOUT
```

A scored partition starts with its own frozen Initial Capital and no financial state carried from another scored partition.

Strategy warmup MAY use earlier causal context. Warmup MUST NOT import later-partition information or carry prior scored PnL, positions, or pending orders.

### 9.6 Purge and embargo

If a feature, label, target, or training sample depends on observations that cross a partition boundary, the protocol MUST define enough purge or embargo to remove that overlap.

If the study has no such forward dependency, the protocol MAY record `NOT_APPLICABLE` with the reason.

Do not use random train/test shuffling for financial time-series claims.

### 9.7 Holdout lock

The Final Holdout is consumed on the first material result exposure.

Material exposure includes:

- viewing a performance metric;
- viewing a trade list or Equity curve;
- using the result to accept, reject, tune, or compare a strategy;
- partial output from an aborted Run;
- sharing the result with an AI agent for strategy improvement.

Before an interval can be designated `FINAL_HOLDOUT`, the checker MUST scan `trials.jsonl` and resolve each prior result-bearing trial through its `run_id`. If any prior trial exposed results for the same Market Profile and Instrument over an overlapping UTC interval, that overlap is already consumed and cannot become a fresh Final Holdout, regardless of the earlier `partition_role`, `protocol_id`, `research_family_id`, `hypothesis_id`, Dataset Release version, seed, branch, or strategy name. An attempted fresh Holdout designation over such overlap is `BLOCKED` with `HOLDOUT_ALREADY_CONSUMED`.

`holdout_lock.json` MUST record the consumed interval, Research Family, Hypothesis lineage, Dataset Release, first exposure time, and evidence reference.

A descendant strategy or renamed protocol inherits the consumed Holdout state.

Rename, retry, new seed, new Git branch, new Research Family, new Hypothesis ID, new Dataset Release version over the same exposed market interval, or new protocol version MUST NOT restore an already consumed Holdout.

### 9.8 Multiple testing

If the research compares more than one candidate and makes a confirmatory statistical claim, the ResearchProtocol MUST predeclare the multiple-testing treatment.

If no treatment is declared, the output MAY remain useful for exploration, but `ResearchEligibility` is `EXPLORATORY_ONLY` for confirmatory significance claims.

Do not hide the number of tried candidates.

### 9.9 Benchmarks

A Research claim MUST compare against the benchmark frozen in the ResearchProtocol.

The benchmark MUST use the same scored interval and a compatible cost basis.

A missing benchmark does not invalidate the mechanical backtest. It makes the benchmark-dependent Research claim `INELIGIBLE`.

### 9.10 External result exposure

Record any material result exposure that can influence later strategy choices.

Unknown exposure is not proof of untouched evidence.

If the Owner or an AI agent has already seen the Final Holdout result, do not describe that Holdout as untouched.

### 9.11 Point-in-time universe and survivorship protection

`instrument_scope` MUST be exactly one of:

``` text
SINGLE_INSTRUMENT
FROZEN_INSTRUMENT_SET
POINT_IN_TIME_UNIVERSE
```

`SINGLE_INSTRUMENT` binds one Instrument. `FROZEN_INSTRUMENT_SET` binds an explicit list before result exposure. `instrument_selection_basis` MUST state why that Instrument or set was chosen. Neither scope supports a claim about instruments outside the bound set. If a fixed set was selected using performance from the scored interval, that exposure MUST be recorded and the same evidence MUST NOT be presented as untouched confirmatory selection evidence.

`POINT_IN_TIME_UNIVERSE` is required only when the research claim is about a historical universe rather than a predeclared fixed set. The protocol MUST freeze the selection rule, selection timestamps, required source fields, and `universe_as_of_rule` before result exposure. Each membership decision MUST use only information available at its selection time.

A study MUST NOT select historical instruments from a current survivor list, future listing status, future liquidity, future performance, or another field unavailable at the selection time. The resolved membership evidence MUST be created before result exposure, stored with the frozen ResearchProtocol as `universe_membership.json`, identify the source and as-of time used for each membership decision, and match `universe_membership_sha256` in the protocol. If point-in-time membership cannot be reconstructed with evidence, a universe-wide claim is `INELIGIBLE`; do not invent membership. An individual Instrument Run may remain mechanically valid.

### 9.12 Sample adequacy

Sample size is a research-confidence rule, not a mechanical backtest rule. V1 has no universal minimum trade count.

For any result proposed for a confirmatory profitability claim, `sample_adequacy_rule` MUST be frozen before result exposure and MUST define:

``` text
counted observation = completed trade
minimum_completed_trades = positive integer
rationale
```

The result is:

``` text
SampleAdequacy = ADEQUATE | LOW_CONFIDENCE | NOT_APPLICABLE
```

`ADEQUATE` means the frozen criterion is met. `LOW_CONFIDENCE` means it is not met. `NOT_APPLICABLE` is allowed when `research_intent=EXPLORATORY`, or when the proposed claim is explicitly non-trade-based. In either case `sample_adequacy_rule` MUST be `NOT_APPLICABLE` with the reason instead of a numeric threshold. Promoting that research to a confirmatory claim requires a new protocol version with a frozen adequacy rule before new result exposure. A completed trade means the pinned Nautilus native trade-statistics unit. The project MUST NOT invent a second trade-pairing convention only to increase the sample count. If a stable native completed-trade sequence is unavailable, `SampleAdequacy` cannot be `ADEQUATE`.

Sample adequacy is evaluated per Instrument Official Run. Do not pool trades from different Instruments to make a weak sample appear adequate. For a cross-instrument confirmatory claim, every contributing Instrument Run MUST satisfy the frozen threshold independently.

A low sample does not change `MechanicalIntegrity`. It prevents a confirmatory claim from becoming `ELIGIBLE`; the result remains `EXPLORATORY_ONLY` unless a new protocol obtains sufficient evidence. Do not change the threshold after seeing the result.

### 9.13 Monte Carlo path-risk diagnostic

Monte Carlo is a research diagnostic over completed net-after-cost trade outcomes. It is not a backtest engine, a forecast, an optimizer, or an automatic PASS/FAIL gate.

``` text
MonteCarloStatus = COMPLETED | MC_LOW_CONFIDENCE | NOT_APPLICABLE
```

For a candidate proposed for `ResearchEligibility=ELIGIBLE`:

- if `SampleAdequacy=ADEQUATE`, a Monte Carlo path-risk diagnostic is required before the claim is eligible;
- if `SampleAdequacy=LOW_CONFIDENCE`, record `MC_LOW_CONFIDENCE` and do not present Monte Carlo output as reliable confirmatory evidence;
- Monte Carlo never changes Nautilus orders, Fills, positions, account state, or the original Run result.

`COMPLETED` means the frozen diagnostic ran successfully. `MC_LOW_CONFIDENCE` means the sample or native net-trade evidence is insufficient for reliable interpretation. `NOT_APPLICABLE` is allowed when `research_intent=EXPLORATORY` or the claim is non-trade-based and does not seek an eligible confirmatory profitability claim.

When applicable, `monte_carlo_spec` MUST freeze before the diagnostic is inspected:

``` text
resampling_method = IID_BOOTSTRAP | MOVING_BLOCK_BOOTSTRAP
simulation_count = positive integer
random_seed = integer
block_length = positive integer when `MOVING_BLOCK_BOOTSTRAP`, otherwise NOT_APPLICABLE
```

The implementation MUST use the persisted Nautilus completed-trade outcomes after the Official Run's fees and funding. It MUST NOT reconstruct a more favorable trade sequence from Fills. If a stable net completed-trade sequence is unavailable, record `MC_LOW_CONFIDENCE` and do not run a substitute Monte Carlo. If a cost-stress Run is being analyzed, use that Run's own net outcomes; do not mix cost assumptions across Runs.

For Monte Carlo, one trade outcome is that completed trade's persisted net PnL in the Run's settlement currency after every fee and funding effect that Nautilus unambiguously attributes to that completed trade. If the pinned runtime does not expose an unambiguous net completed-trade outcome, record `MC_LOW_CONFIDENCE`; the project MUST NOT invent a funding or fee allocation only to make Monte Carlo run. `IID_BOOTSTRAP` samples `n` outcomes with replacement for each simulation, where `n` is the original completed-trade count. `MOVING_BLOCK_BOOTSTRAP` samples contiguous blocks with replacement, concatenates them in sampled order, and truncates the final block to exactly `n` outcomes. No other V1 resampling method is allowed. Each simulated path starts at the Run's frozen Initial Capital and applies the sampled net PnL amounts cumulatively; it does not rerun Nautilus, rescale trades, or pretend to model a new execution path.

The diagnostic MUST report at least:

``` text
final_equity_distribution with p05, p50, p95
max_drawdown_distribution with p05, p50, p95
positive_simulation_rate
worst_simulated_drawdown
consecutive_loss_streak_distribution
original_result_location_in_distribution
top_winner_dependency
outlier_dependency
```

`positive_simulation_rate` is the fraction of simulated paths whose final Equity exceeds the frozen Initial Capital. `original_result_location_in_distribution` is the percentile rank of the original final Equity within the simulated final-Equity distribution. `top_winner_dependency` is the largest positive net trade PnL divided by total positive net trade PnL; if there is no positive trade PnL, record it as `NOT_APPLICABLE`. `outlier_dependency` MUST report the original net PnL and the net PnL after removing the single largest winning trade; it is diagnostic only and MUST NOT replace the Official result.

The report MUST identify the resampling method, simulation count, seed, and block length when applicable. It MUST NOT describe a favorable Monte Carlo distribution as proof of future profitability.

### 9.14 Claim scope and cross-instrument generalization

Every research claim MUST declare one scope, and `intended_claim_scope` MUST NOT be broader than the frozen `instrument_scope`:

``` text
INSTRUMENT_ONLY
FROZEN_SET_ONLY
POINT_IN_TIME_UNIVERSE
```

A single-Instrument Official Run can support only `INSTRUMENT_ONLY` claims.

A `FROZEN_SET_ONLY` claim requires valid independent Official Runs for the exact pre-frozen set under one ResearchProtocol. It supports only that set; it MUST NOT be worded as a market-wide or universe-wide result.

A `POINT_IN_TIME_UNIVERSE` claim additionally requires the point-in-time membership evidence from Section 9.11 and valid independent Official Runs for every Instrument that the frozen protocol says contributes to the claim. For `FROZEN_SET_ONLY` and `POINT_IN_TIME_UNIVERSE` confirmatory claims, the Sample Adequacy and Monte Carlo requirements apply independently to every contributing Instrument Run; do not pool trades across Instruments. The protocol MUST define any sampling rule before result exposure; missing or failed Instruments MUST remain visible and MUST NOT be silently dropped. Cross-instrument aggregation MAY summarize accepted independent Runs; it MUST NOT create shared cash, positions, or a portfolio that V1 does not define.

Evidence from one Instrument MUST NOT be generalized to another Instrument or to the crypto market as a whole. A claim broader than its evidence is `INELIGIBLE` even when every underlying Run is mechanically valid.

------------------------------------------------------------------------

## 10. Metrics, claims, and evidence

### 10.1 Metrics

Use Nautilus native reports and statistics when the pinned runtime provides the required metric.

Do not reimplement a Nautilus metric only to obtain the same value under another name.

A project metric MAY be added only when Nautilus does not provide the required research measure.

Every added metric MUST define:

- exact inputs;
- formula;
- units;
- valid domain;
- undefined behavior;
- one known-result fixture.

Undefined is not zero.

A report MUST NOT replace an undefined, missing, or invalid metric with `0`, `NaN`, infinity, or a favorable fallback.

### 10.2 Mechanical Integrity

`MechanicalIntegrity=PASS` requires all of these:

- Runtime Lock match;
- frozen Source Revision evidence from a clean preflight worktree;
- Dataset Release completeness for the Run;
- causal data visibility;
- causal execution timing;
- valid Market Profile;
- valid StrategySpec and LabRunConfig;
- required golden and profile tests passed for the current runtime and code;
- invariant checker `CHECK_PASS`;
- deterministic replay requirement passed for the qualified path;
- complete Run evidence;
- no unresolved material ambiguity.

A determinate violation gives `FAIL`.

Missing or ambiguous material evidence gives `BLOCKED`.

### 10.3 Claim classes

A report MAY label a value as:

``` text
CALCULATED
ESTIMATED
UNKNOWN
NOT_APPLICABLE
INELIGIBLE
```

`CALCULATED` means the value follows from accepted Run evidence. It does not mean the simulation reconstructed the historical Binance account exactly.

`ESTIMATED` means a frozen model or assumption materially affects the value.

`UNKNOWN` means the evidence does not support a numerical or directional claim.

`NOT_APPLICABLE` means the concept does not apply to the selected profile.

`INELIGIBLE` means the value exists but the research rules prohibit the proposed claim.

Bar-based execution in V1 is always disclosed as estimated execution.

Historical fee-tier claims are estimated unless exact account-tier evidence exists.

Queue position, market impact, exact historical spread, and liquidation behavior are `UNKNOWN` in V1.

### 10.4 Profitability claim

A profitability claim is eligible only when:

``` text
MechanicalIntegrity = PASS
and checker = CHECK_PASS
and required Dataset Release evidence is complete
and trial history is complete
and ResearchEligibility = ELIGIBLE
and SampleAdequacy = ADEQUATE for every supporting Instrument Run
and MonteCarloStatus = COMPLETED for every supporting Instrument Run
and claim_scope is supported by the frozen Instrument/universe evidence
and required performance diagnostics are complete for every supporting Run
and every supporting trial has the same protocol_id as the confirmatory claim
and that protocol was frozen before those trials produced result-bearing output
and the claim uses the frozen scored interval and metric
```

A profitable result with failed mechanical integrity, inadequate sample evidence, missing required Monte Carlo diagnostics, unsupported claim scope, or incomplete required diagnostics is not an eligible confirmatory profitability claim. These research conditions do not rewrite the underlying Official Run.

### 10.5 Run evidence directory

Every started Run gets one directory:

``` text
runs/<run_id>/
  lab_run_config.json
  lab_run_config.sha256
  runtime.lock.json
  source_revision.json
  dataset_release.json
  strategy_spec.json
  orders.csv
  fills.csv
  positions.csv
  account.csv
  funding.csv              # Perpetual only
  nautilus_result.json
  checker.json
  status.json
```

`source_revision.json` MUST contain exactly the required Source Revision evidence fields:

``` text
repository
branch_ref
git_commit
git_tree
clean_worktree
captured_at_utc
```

`git_commit` and `git_tree` are the full Git object IDs captured from `git rev-parse HEAD` and `git rev-parse HEAD^{tree}`. `clean_worktree` MUST be `true` at the Official Run preflight boundary. The exact `source_revision.json` bytes MUST be frozen with and bound to the Run evidence inventory. If the captured Git commit or tree changes after freeze, the Run is `BLOCKED`; do not update the frozen evidence in place. The final Run report MUST identify the frozen repository, branch/ref, Git commit, and Git tree. Do not substitute a custom project source-tree SHA-256.

If the pinned Nautilus runtime exposes a more faithful native event export than one of the CSV files, preserve that native export too. Do not remove the required human-readable projection.

Evidence files MUST be written from the exact Run state. A report MAY read them. A report MUST NOT mutate them.

### 10.6 Research evidence

The repository or research workspace also keeps:

``` text
research/
  protocols/                # includes universe_membership.json when applicable
  diagnostics/
  trials.jsonl
  holdout_lock.json
  defects.jsonl
  CHANGELOG.md
```

A report that cannot reconcile its selected result to `trials.jsonl` is `INELIGIBLE`.

### 10.7 Failure retention

When a Run fails after Nautilus emitted orders or Fills, preserve those events.

Do not roll back the evidence to make the attempt look as if it never ran.

The terminal status records the failure or block reason.

### 10.8 Required performance diagnostics

`research/diagnostics/<run_id>.json` is a read-only analysis artifact derived from immutable completed Run evidence. It is not part of the Official Run's execution evidence and MUST NOT feed values back into Nautilus or alter the Run directory.

Use Nautilus native reports and statistics first. If a required diagnostic is absent from the pinned runtime, implement only that diagnostic from persisted evidence under Section 10.1; do not create a second accounting engine.

Every completed research Run MUST report, when mathematically applicable:

``` text
total_return
CAGR
calendar_year_returns
max_drawdown
max_drawdown_duration
average_drawdown_duration
time_under_water
completed_trade_count
win_rate
max_consecutive_losses
equity_curve
drawdown_curve
benchmark_comparison
SampleAdequacy
Monte Carlo section or NOT_APPLICABLE / MC_LOW_CONFIDENCE
claim_scope
```

Required fallback semantics, only when Nautilus does not provide the measure:

- Fallback Equity-path diagnostics MUST use the finest persisted Nautilus Equity series available for the scored interval. The artifact MUST record that observation basis. Do not resample to a smoother frequency to improve the result.
- `total_return = ending_equity / starting_equity - 1` when `starting_equity > 0`; otherwise record it as undefined with the reason.
- `CAGR = (ending_equity / starting_equity)^(365.2425 / scored_days) - 1` when `starting_equity > 0`, `ending_equity > 0`, and `scored_days > 0`; otherwise record it as undefined with the reason.
- A drawdown starts when Equity falls below its previous high-water mark and ends at the first later observation with Equity greater than or equal to that high-water mark. An open terminal drawdown runs through `scoring_end_exclusive`. `max_drawdown_duration` is the longest episode; `average_drawdown_duration` is the arithmetic mean duration across all episodes, including an open terminal episode.
- `time_under_water` is the fraction of the scored elapsed time for which Equity is below its prior high-water mark.
- Trade-based fields use the pinned Nautilus native completed-trade sequence. If that sequence is unavailable, record `completed_trade_count`, `win_rate`, and `max_consecutive_losses` as undefined rather than inventing a project trade-pairing rule.
- When a native completed-trade sequence exists, `max_consecutive_losses` counts consecutive trades with net trade PnL `< 0`; a trade with net PnL `>= 0` ends the loss streak.
- `calendar_year_returns` use the first and last valid Equity observations inside each UTC calendar year intersecting the scored interval. A partial first or last year MUST be labeled partial.

These diagnostics are not universal performance thresholds. A large drawdown, low win rate, low Sharpe ratio, or weak calendar year does not automatically change `MechanicalIntegrity`. Any numeric research acceptance threshold must be frozen in the ResearchProtocol before result exposure.

The report MUST make performance concentration visible through `calendar_year_returns` and the Equity/drawdown curves. It MUST NOT hide a year, drawdown episode, losing streak, or failed trial because it makes the strategy look worse.

------------------------------------------------------------------------

## 11. Project shape

### 11.1 Architecture

Build one modular Python application. Keep the call graph shallow.

The intended direction is:

``` text
config ─┐
        ├─> data ─> nautilus_runner ─> evidence ─> reporting
strategy┘                 │
                          └─> checker
research ─────────────────────^ and reads completed evidence only
```

Rules:

- `data` does not import strategy code.
- strategy code does not import research or reporting code.
- `checker` does not mutate Nautilus state.
- `reporting` reads completed evidence only.
- `research` schedules and records Runs. It does not change a Run after freeze.
- no project module implements a second matching, position, account, margin, fee, funding, or portfolio engine.

### 11.2 Required repository layout

Use this layout unless a tool requires a mechanically equivalent path:

``` text
strict-crypto-backtesting-lab/
  SSOT.md
  README.md
  AGENTS.md
  pyproject.toml
  runtime.lock.json
  src/crypto_lab/
    config.py
    data.py
    runner.py
    checker.py
    research.py
    reporting.py
    strategies/
  configs/
    profiles/
      spot_cash_v1.json
      usdm_perpetual_v1.json
  tests/
    unit/
    integration/
    golden/
    qualification/
  data/
    raw/
    releases/
    catalog/
  runs/
  research/
    protocols/
    diagnostics/
    trials.jsonl
    holdout_lock.json
    defects.jsonl
    CHANGELOG.md
```

Do not add a service, database, queue, web server, plugin system, registry, or abstraction layer unless a concrete V1 requirement needs it.

### 11.3 Boundary validation

Validate external data and user configuration at the boundary.

After a value passes boundary validation, use typed internal values. Do not scatter duplicate validation across the codebase.

Keep one source of truth for each material decision.

### 11.4 Illegal states

Prefer types and closed enums that make these states impossible:

- unknown Market Profile;
- unsupported order type;
- missing required fee assumption;
- Spot funding;
- Perpetual Hedge Mode;
- unseeded probabilistic Fill Model;
- non-UTC study window;
- Run without Dataset Release;
- Run without config hash;
- claim without Mechanical Integrity state.

### 11.5 No speculative infrastructure

Do not build for hypothetical future live trading, multi-venue routing, distributed workers, shared portfolios, or another engine.

A future requirement may add those features through a new SSOT version.

------------------------------------------------------------------------

## 12. Build sequence

Build in five verifiable phases. Finish each phase before starting the next.

A phase is complete only when its acceptance tests pass on the real artifact.

### M0 — Contract and runtime foundation

Build:

- repository skeleton;
- `pyproject.toml`;
- user-local `.venv`;
- pinned Nautilus wheel installation;
- `runtime.lock.json`;
- canonical JSON and hashing helpers;
- typed Market Profile and Run Configuration models;
- golden fixtures that do not need market data.

Verify:

- wheel SHA-256 matches the pinned value;
- runtime imports `nautilus_trader==1.231.0`;
- Python is CPython 3.12;
- config round-trip preserves Decimal strings and timestamps;
- unknown or missing material config fields fail;
- `price_protection_points=0`, the single Maker/Taker fee path, and the profile-specific `PortfolioConfig.use_mark_prices` value are explicit in resolved configuration;
- the exact v1.231.0 latency-model import path/class is resolved from the pinned wheel, and its effective insert latency is exactly `60_000_000_000` ns;
- Runtime Lock mismatch blocks before data loading;
- the separate Source Revision evidence contract exists for later Run execution and contains `repository`, `branch_ref`, `git_commit`, `git_tree`, `clean_worktree`, and `captured_at_utc` without putting project Git identity in Runtime Lock.

Do not acquire market data or run a research strategy in M0.

M0 MUST NOT implement the M1 runner or execute an Official Run. It publishes only the Source Revision contract shape required by the later Run evidence path.

M0 exports stable `LabRunConfig`, Runtime Lock, Source Revision, status enums, and hashing rules to all later phases.

### M1 — Causal Nautilus harness

Build:

- the minimal Nautilus runner;
- the two explicit venue-profile configs;
- the strategy base restrictions;
- synthetic one-minute execution bars plus synthetic Perpetual mark and funding data;
- Run evidence capture;
- the invariant checker skeleton.

Verify on synthetic data:

- completed-bar visibility;
- later-than-signal Fill timing;
- negative same-bar control;
- deterministic replay;
- Fill immutability;
- excluded order rejection;
- Spot CASH profile rejection of borrowing and short inventory;
- Perpetual NETTING profile rejection of Hedge semantics;
- exact close-to-flat followed by a separate opposite-side reopen, rejection of a direct cross-zero order, and rejection of a second strategy order while the first remains non-terminal;
- a warmup-boundary fixture where the last warmup bar becomes available exactly at `scoring_start` and produces zero order submission and zero scored financial state;
- native funding sign, timing, and exactly-once settlement on the pinned runtime;
- Perpetual mark-price valuation with `use_mark_prices=true`, including a negative missing-mark/fallback fixture;
- exactly-once Maker/Taker fee application;
- terminal-boundary behavior: an intent from the last eligible signal cannot produce a Fill at or after `scoring_end_exclusive`;
- market-order partial-fill or remainder behavior cannot leave an order able to violate the causal or terminal contract.

M1 MUST settle causal execution, scoring-window boundaries, single-live-order behavior, NETTING reversal, native funding, mark valuation, fee, and terminal-boundary behavior before real research data enters the system. If the pinned runtime cannot satisfy any required Perpetual behavior, the Perpetual profile remains `BLOCKED`; Spot MAY continue when the failure is provably isolated.

M1 exports `run_lab(config) -> RunResult` and the stable Run evidence shape.

### M2 — Frozen Binance data

Build:

- official-source acquisition;
- raw-byte storage;
- checksum verification;
- timestamp normalization;
- one-minute completeness scanning;
- instrument metadata records;
- Dataset Release manifests;
- Nautilus catalog conversion;
- Perpetual mark and funding ingestion.

Verify:

- raw hashes are stable;
- a synthetic missing minute blocks;
- a conflicting duplicate blocks;
- Spot timestamp-unit handling across the `2025-01-01T00:00:00Z` boundary is explicit, and USDⓈ-M roles do not inherit the Spot rule;
- no silent repair occurs;
- catalog rebuild from the same raw objects is semantically stable;
- Spot and Perpetual roles cannot collide;
- Perpetual mark data satisfy the required one-minute grid;
- the expected funding schedule is proven from official evidence, a removed required funding event produces `FUNDING_MISSING`, and an unprovable schedule produces `FUNDING_AMBIGUOUS`;
- required mark and funding roles cannot be substituted.

M2 exports only `DatasetRelease` objects to M3 and M4.

### M3 — Qualified Spot and Perpetual paths

Build and verify the complete real-data path for each profile.

Spot acceptance:

- `CASH` account;
- no borrowing;
- no short inventory;
- fee applied once;
- causal Fill timing;
- terminal open-position policy;
- checker pass;
- deterministic replay pass.

Perpetual acceptance:

- linear USDT instrument;
- `NETTING` position lifecycle;
- leverage `1`;
- long, reduce, exact close-to-flat, short-from-flat, and cross-zero rejection fixtures;
- funding sign and timing;
- funding applied once;
- required mark-price path;
- no liquidation model;
- checker pass;
- deterministic replay pass.

A profile that fails its acceptance remains unavailable for Official Runs. The other profile MAY remain available if the failure cannot affect it.

M3 MUST NOT change the M1 execution contract to make a real-data test pass. Fix the root cause or stop.

### M4 — Research governance and reporting

Build:

- `ResearchProtocol` validation;
- append-only trial journal behavior;
- partition enforcement;
- Holdout lock;
- multiple-testing claim gate;
- benchmark binding;
- sample-adequacy evaluation;
- Monte Carlo path-risk diagnostics when applicable;
- point-in-time universe and claim-scope enforcement;
- required performance diagnostics;
- claim eligibility;
- report generation from persisted evidence.

Verify:

- a failed trial cannot disappear;
- a winner-only report is ineligible;
- a Holdout exposure consumes the Holdout;
- an interval already exposed by any prior result-bearing Trial for the same Market Profile and Instrument cannot later become a fresh Final Holdout under another label, protocol, family, hypothesis, Dataset Release version, seed, branch, or strategy name;
- a descendant cannot reset Holdout consumption;
- a post-result material protocol change creates a new version;
- a later protocol may re-analyze old trials only as `EXPLORATORY_ONLY` and cannot use them as fresh confirmatory evidence;
- missing multiplicity treatment limits confirmatory claims;
- a low sample cannot produce an eligible confirmatory claim;
- Monte Carlo is required for an otherwise eligible candidate when the frozen sample rule is adequate, but its numerical outcome is diagnostic rather than an automatic gate;
- the same Monte Carlo input and frozen seed reproduce the same diagnostic output;
- a single-Instrument result cannot produce a broader cross-instrument claim;
- a current-survivor universe cannot masquerade as a point-in-time historical universe;
- required diagnostics are present without replacing undefined values with favorable defaults;
- a profitable Run with failed Mechanical Integrity cannot produce an eligible profitability claim.

M4 completes V1.

------------------------------------------------------------------------

## 13. Cross-phase compatibility

### 13.1 Freeze the interfaces early

The purpose of phases is to expose mistakes before later work depends on them.

Freeze these interfaces at the end of the named phase:

| Producer | Interface                                                            | First consumers |
|----------|----------------------------------------------------------------------|-----------------|
| M0       | `RuntimeLock`, `SourceRevision`, `LabRunConfig`, status enums        | M1-M4           |
| M1       | `run_lab`, `RunResult`, Run evidence shape                           | M2-M4           |
| M2       | `DatasetRelease`                                                     | M3-M4           |
| M3       | qualified profile IDs and evidence                                   | M4              |
| M4       | `ResearchProtocol`, research diagnostics, claim result, report input | final output    |

A later phase MUST consume the published interface. It MUST NOT reach around the interface to depend on an internal implementation detail.

### 13.2 Downstream contract tests

Before a phase is accepted, run at least one small fixture that exercises the interface expected by the next phase.

Required checks:

- M0 creates a config and separate Source Revision evidence shape that M1 can parse without defaults.
- M1 emits a `RunResult` and evidence bundle that M4 can read without a real strategy edge.
- M2 emits a tiny Dataset Release that M3 can run without conversion changes.
- M3 emits one completed Spot and one completed Perpetual fixture that M4 can journal and report.

Future runtime behavior does not need to exist early. Its data shape and failure behavior do.

### 13.3 Material interface change

A change is material when it changes:

- field meaning;
- type or nullability;
- unit;
- timestamp semantics;
- financial meaning;
- Fill or order semantics;
- failure behavior;
- research exposure meaning;
- claim eligibility.

Formatting, comments, and internal refactoring are not material when tests prove behavior unchanged.

If a material producer interface changes, rerun every downstream phase that depends on that meaning.

Do not preserve a broken interface only to avoid rerunning later work.

### 13.4 Late defect rule

When a later phase exposes an earlier defect:

1.  Stop the current phase.
2.  Record the defect in `research/defects.jsonl` or the build defect log.
3.  Identify the earliest component whose behavior is wrong.
4.  Fix the root cause there.
5.  Rerun that phase's acceptance tests.
6.  Rerun every downstream acceptance test that depends on the changed behavior.
7.  Preserve prior failed evidence.

Do not edit a historical Official Result in place.

### 13.5 Repair scope

Use this default repair scope:

| Root defect                                       | Minimum rerun                                                          |
|---------------------------------------------------|------------------------------------------------------------------------|
| Runtime or execution semantics                    | M0 or M1 through all affected Official Runs                            |
| Data acquisition, timestamp, or release semantics | M2 through all affected Runs                                           |
| Spot profile only                                 | affected Spot path and downstream research                             |
| Perpetual profile only                            | affected Perpetual path and downstream research                        |
| Research protocol only                            | affected research lineage; engine and Dataset Releases remain reusable |
| Reporting only                                    | regenerate reports from unchanged accepted evidence                    |
| Unknown root                                      | stop until the root is identified                                      |

If the fix changes this SSOT's material semantics, stop. The Owner must adopt a new SSOT before implementation continues.

### 13.6 Three failed fixes

After three failed attempts to fix the same root defect, stop and ask the Owner.

Do not reset the count by renaming the defect, moving code, changing a branch, or creating a new Run ID.

------------------------------------------------------------------------

## 14. Implementation discipline

### 14.1 Build the proof with the feature

Every material feature ships with the test that proves it.

Do not leave causality, data integrity, account restrictions, funding, or Holdout protection for a final audit.

### 14.2 Fix root causes

When a test fails, reproduce the failure and fix the earliest wrong behavior.

Do not add a downstream guard that hides an upstream semantic error.

Do not loosen a golden test to make production code pass unless the Owner first changes this SSOT.

### 14.3 Prefer deletion

Before adding a wrapper, validator, adapter, or state object, check whether Nautilus or an existing project boundary already owns the behavior.

Delete redundant paths instead of synchronizing them.

One material decision MUST have one source of truth.

### 14.4 Keep the call graph shallow

A maintainer MUST be able to answer these without tracing through more than a few layers:

- Which data entered this Run?
- Which Nautilus configuration produced this Fill?
- Which strategy parameters were frozen?
- Why is this Run blocked?
- Has this Holdout already been consumed?

If the architecture makes those answers hard, simplify it.

### 14.5 Verify the real artifact

A phase does not pass because code compiles or unit tests alone are green.

Run the actual phase fixture. Inspect the produced config, data manifest, Nautilus output, evidence, and failure mutation.

### 14.6 No hidden recovery

A crash or partial write MUST NOT become a silent success.

If the application cannot prove that a Run completed and its evidence is complete, record the attempt as `ABORTED` or `BLOCKED`.

A retry creates visible history.

### 14.7 No root or sudo by default

Build and run under the normal project user.

If a required step needs `root`, `sudo`, a system package, or a host-level change, stop and explain the exact need before changing the host.

------------------------------------------------------------------------

## 15. Required failure codes

Use these codes for material stop conditions. Add a new code only when no existing code describes the failure without losing meaning.

``` text
RUNTIME_LOCK_MISMATCH
RUNTIME_WHEEL_HASH_MISMATCH
UNSUPPORTED_RUNTIME
UNSUPPORTED_MARKET_PROFILE
UNSUPPORTED_V1_ORDER_TYPE
CONFIG_INVALID
CONFIG_HASH_MISMATCH
NETWORK_DURING_OFFICIAL_RUN
DATA_SOURCE_INVALID
DATA_HASH_MISMATCH
DATA_TIMESTAMP_INVALID
DATA_GAP
DATA_DUPLICATE_CONFLICT
DATA_ROLE_MISMATCH
DATASET_RELEASE_STALE
INSTRUMENT_METADATA_INVALID
TIMEFRAME_AGGREGATION_UNRESOLVED
CAUSAL_EXECUTION_UNRESOLVED
LOOKAHEAD_DETECTED
SAME_BAR_EXECUTION_DETECTED
FILL_MUTATION_DETECTED
SPOT_SHORT_OR_BORROW_DETECTED
PERP_PROFILE_INVALID
CROSS_ZERO_ORDER_REJECTED
CONCURRENT_STRATEGY_ORDER_REJECTED
FEE_MISSING
FEE_DOUBLE_COUNT
FUNDING_MISSING
FUNDING_AMBIGUOUS
FUNDING_DOUBLE_COUNT
MARK_ROLE_INVALID
DETERMINISM_FAILURE
CHECKER_FAILURE
CHECKER_BLOCKED
TRIAL_HISTORY_INCOMPLETE
RESEARCH_PROTOCOL_INVALID
PARTITION_LEAKAGE
HOLDOUT_ALREADY_CONSUMED
HOLDOUT_HISTORY_VIOLATION
MULTIPLE_TESTING_UNDECLARED
CLAIM_INELIGIBLE
DOWNSTREAM_CONTRACT_FAILURE
DEFECT_ROOT_UNRESOLVED
RETRY_LIMIT_REACHED
EVIDENCE_INCOMPLETE
```

A material failure MUST include the code, the affected Run or phase, and the evidence that caused it.

------------------------------------------------------------------------

## 16. Definition of done

### 16.1 A phase is done when

A phase is done only when:

- its required artifact exists;
- its positive fixture passes;
- its required negative mutation fails for the intended reason;
- its downstream contract fixture passes;
- no unresolved material blocker remains;
- the repository tests for completed phases remain green.

### 16.2 An Official Run is valid when

An Official Run is valid only when:

- the runtime matches `runtime.lock.json`;
- a frozen Source Revision is present in Run evidence and binds the Run to its repository, branch/ref, Git commit, and Git tree;
- the Git worktree was clean at the Source Revision preflight boundary;
- the Dataset Release is immutable and complete for the Run;
- the Run Configuration is frozen and hashed;
- the strategy uses only causal inputs;
- every scored order originates from a scoring-eligible signal bar and warmup submitted no order;
- the Nautilus execution path satisfies the no-same-bar invariant;
- the selected Market Profile is valid;
- required fee, funding, and mark inputs are present exactly once where applicable;
- no two strategy-created orders were simultaneously non-terminal for the Run Instrument;
- no prohibited cross-zero Perpetual order was submitted;
- no Fill occurred at or after `scoring_end_exclusive`;
- Nautilus completes the Run;
- the invariant checker returns `CHECK_PASS`;
- required replay evidence passes;
- the Run evidence is complete;
- the trial journal contains the attempt.

### 16.3 A research claim is valid when

A research claim is valid only when:

- the underlying Official Runs are valid;
- the ResearchProtocol was frozen before result-bearing exposure;
- every trial supporting a confirmatory claim has the same `protocol_id` as that claim and was run after that protocol was frozen;
- the complete trial history is present;
- the relevant partition rules were respected;
- the Holdout state permits the claim;
- required benchmark and multiple-testing rules for that claim are satisfied;
- for a confirmatory trade-based claim, `SampleAdequacy=ADEQUATE` for every supporting Instrument Run; `NOT_APPLICABLE` is accepted only under the explicit exception in Section 9.12;
- for every supporting Instrument Run with `SampleAdequacy=ADEQUATE`, `MonteCarloStatus=COMPLETED`;
- the declared claim scope is no broader than the supporting Instrument and universe evidence;
- required performance diagnostics are present or explicitly undefined / not applicable with a reason;
- the report discloses V1 execution and fee limitations;
- `MechanicalIntegrity=PASS`;
- `ResearchEligibility=ELIGIBLE`.

### 16.4 V1 laboratory completion

The laboratory is complete when all M0-M4 acceptance criteria pass and these end-to-end fixtures pass from a clean process:

1.  one Spot CASH synthetic fixture;
2.  one Perpetual NETTING synthetic fixture with funding;
3.  one small real-data Spot Run;
4.  one small real-data Perpetual Run;
5.  one research lifecycle that contains a failed trial, a completed trial, a Holdout exposure, and an eligible or explicitly ineligible claim.

The final acceptance run MUST use the pinned Runtime Lock and MUST execute without network access.

------------------------------------------------------------------------

## Appendix A. Golden numeric fixtures

These values are independent expectations. Production code MUST NOT generate the expected side of the test from its own output.

### A.1 Spot fee fixture

Inputs:

``` text
initial USDT = 1000
BUY quantity = 2 BTC
Fill price = 100 USDT/BTC
taker fee rate = 0.001
```

Expected Nautilus fee amount under the configured quote-denominated fee assumption:

``` text
notional = 2 * 100 = 200 USDT
fee = 200 * 0.001 = 0.20 USDT
```

The fixture fails if the fee is charged twice or if project code changes the Fill price to represent the fee.

### A.2 Perpetual close-and-reverse fixture

The pinned v1.231.0 Python/Cython path MUST NOT be tested with a direct cross-zero Fill as an accepted V1 reversal.

#### A.2a Exact close to flat

Inputs:

``` text
linear multiplier = 1
position before = +2
average entry = 100
Fill = SELL 2 @ 90
```

Expected:

``` text
closed quantity = 2
realized PnL before fees and funding = 2 * (90 - 100) = -20 USDT
position after = FLAT
```

#### A.2b Prohibited direct cross-zero reversal

State:

``` text
position before = +2
candidate order = SELL 3
```

Expected:

``` text
order submission = REJECTED
run status = BLOCKED
failure_code = CROSS_ZERO_ORDER_REJECTED
Nautilus Fill = none
```

The project MUST preserve the rejected intent as evidence. It MUST NOT submit the order and then repair Nautilus position state afterward.

#### A.2c Legal opposite-side reopen

After Nautilus has reported the A.2a position `FLAT`, submit a separate MARKET SELL order under the normal causal latency. The synthetic eligible market state for this fixture MUST be constructed so the pinned Nautilus path emits:

``` text
SELL 1 Fill price = 90
```

Expected:

``` text
position after = -1
short entry = 90
```

The fixture fails if a single order crosses zero, if the reverse order is submitted before the `FLAT` state is observed, or if project code creates a second position/PnL engine to rewrite Nautilus state.

#### A.2d Concurrent-order guard

State:

``` text
position before = +2
intent 1 = SELL 2 and is submitted
before intent 1 becomes terminal, strategy attempts intent 2 = SELL 1
```

Expected:

``` text
non-terminal submitted strategy orders before intent 2 = 1
intent 2 submission = REJECTED
failure_code = CONCURRENT_STRATEGY_ORDER_REJECTED
no Nautilus Fill crosses zero
```

The first submitted order remains owned by Nautilus and follows its normal lifecycle. The project MUST NOT submit the second intent until the first order is terminal and the resulting position state is observable. The fixture MUST also be repeated from `FLAT`, and the strategy-conflict unit test MUST separately prove that multiple same-callback internal signals are reduced by the frozen `conflict_rule` to at most one order intent before submission.

### A.3 Funding sign fixture

Inputs:

``` text
position before funding = +2
multiplier = 1
funding mark = 100
funding rate = +0.01
```

Expected direction:

``` text
long funding cash effect = -2 USDT
```

With position `-2`, the expected direction is `+2 USDT`.

The fixture fails if the payment is applied twice or to the position opened after the funding boundary.

### A.4 Same-bar fixture

Create synthetic one-minute bars with non-overlapping price ranges:

``` text
bar N:   O=100 H=101 L=99 C=100
bar N+1: O=200 H=201 L=199 C=200
bar N+2: O=300 H=301 L=299 C=300
```

The strategy signals from completed bar `N`.

A legal Fill MUST occur after bar `N` becomes available and MUST NOT use a price from `[99,101]`.

The exact legal later Fill price is whatever the pinned Nautilus execution path emits under the frozen causal latency configuration.

The negative control removes the causal delay. The test suite MUST demonstrate that the negative control violates the invariant.

#### A.4b Warmup-to-scoring boundary fixture

Let `T0 = scoring_start`. Supply a final warmup bar with:

``` text
interval = [T0 - 60s, T0)
available_at = T0
strategy condition = would emit BUY if the bar were scoring-eligible
```

Expected:

``` text
order submission from that bar = none
Nautilus Fill from that bar = none
position at T0 = FLAT
account at T0 = frozen Initial Capital
non-terminal strategy orders at T0 = 0
```

Every contemporaneous bar that materially triggers the first scored order must satisfy the Section 5.4 scoring-eligible interval rule. Warmup indicator state may carry forward; a warmup bar may not be reused as the contemporaneous scored trigger.

### A.5 Missing-minute fixture

Remove the one-minute bar that would be required next in the execution window.

Expected result:

``` text
status = BLOCKED
failure_code = DATA_GAP
```

The engine MUST NOT jump to a later minute and pretend the missing minute never existed.

------------------------------------------------------------------------

## Appendix B. Owner inputs for a research study

These values are not hard-coded by V1. They are supplied before a study starts and then frozen.

``` text
research intent: `EXPLORATORY` or `CONFIRMATORY`
instrument or frozen instrument set required by `instrument_scope`
initial capital
study data range
warmup range
Development range
Validation range
OOS range when used
Final Holdout range
strategy rules and parameters
parameter-search domain or candidate list
search budget
random seeds
primary metric
benchmark
fee assumption
optional cost-stress assumptions
instrument scope and instrument-selection basis
when applicable, point-in-time universe selection rule and as-of rule
intended claim scope
sample-adequacy rule for confirmatory trade-based claims
Monte Carlo specification when the study is intended to support an eligible confirmatory claim
multiple-testing treatment, `NOT_APPLICABLE` for exactly one predeclared candidate, or `EXPLORATORY_ONLY`
kill criteria
```

The implementation agent MUST NOT invent a missing study value that can change the research result.

------------------------------------------------------------------------

## Appendix C. Source anchors

The implementation MUST use the pinned Nautilus source commit and package artifact as the runtime authority.

Current official source anchors used to write this SSOT:

- Git's official data-model, `git commit-tree`, and `git rev-parse` documentation for commit and tree object identity.
- SLSA v1.2 source requirements and provenance specification for separating source revision/provenance from runtime and build inputs.
- NautilusTrader package `1.231.0`.
- Source commit `27a8e54e7ac3c57d6cbf8891f0283dfbaee97317`.
- PyPI CPython 3.12 Linux x86-64 wheel SHA-256 `8c438e95c275a13df0c0ddb7012c462708b5e99ff3612e36a1b7bd49ab39c216`.
- Binance Public Data archives for Spot and USDⓈ-M historical market data.
- NautilusTrader v1/v2 migration notes only where they explicitly document a v1.231.0 behavior difference, including direct cross-zero position entry-price behavior and the v1 `PortfolioConfig.use_mark_prices=false` default.
- Binance Public Data README for the Spot timestamp-unit change at `2025-01-01T00:00:00Z`.
- Nautilus backtest execution-flow documentation for in-flight latency and shutdown semantics; the pinned v1.231.0 artifact remains the final runtime authority.

Latest or nightly Nautilus documentation MAY help locate concepts. It MUST NOT override v1.231.0 source behavior or exact v1.231.0 APIs.

------------------------------------------------------------------------

## Final rule

Keep the laboratory strict where a mistake can falsify a backtest. Keep it small everywhere else.

Use NautilusTrader for the trading engine. Add only the controls that make its historical use causal, reproducible, auditable, and scientifically honest.

When this SSOT determines the answer, continue. When a material answer is not determined, stop and ask the Owner. Never guess.
