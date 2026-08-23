CREATE TABLE schema_metadata (
    schema_identity VARCHAR PRIMARY KEY CHECK (length(schema_identity) = 64),
    schema_version VARCHAR NOT NULL UNIQUE,
    created_at_utc VARCHAR NOT NULL,
    duckdb_version VARCHAR NOT NULL CHECK (duckdb_version = '1.4.5'),
    extensions_install_allowed BOOLEAN NOT NULL CHECK (extensions_install_allowed = false),
    extensions_load_allowed BOOLEAN NOT NULL CHECK (extensions_load_allowed = false),
    network_allowed BOOLEAN NOT NULL CHECK (network_allowed = false)
);

CREATE TABLE raw_objects (
    raw_object_sha256 VARCHAR PRIMARY KEY CHECK (length(raw_object_sha256) = 64),
    byte_length BIGINT NOT NULL CHECK (byte_length >= 0),
    local_path VARCHAR NOT NULL UNIQUE,
    content_verified BOOLEAN NOT NULL CHECK (content_verified = true)
);

CREATE TABLE http_observations (
    observation_id VARCHAR PRIMARY KEY CHECK (length(observation_id) = 64),
    raw_object_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    exact_url VARCHAR NOT NULL,
    exact_query VARCHAR NOT NULL,
    status_code INTEGER NOT NULL CHECK (status_code BETWEEN 100 AND 599),
    response_headers_json VARCHAR NOT NULL,
    capture_started_at_utc VARCHAR NOT NULL,
    capture_completed_at_utc VARCHAR NOT NULL,
    source_role VARCHAR NOT NULL,
    pagination_position VARCHAR NOT NULL,
    UNIQUE (exact_url, capture_started_at_utc, observation_id)
);

CREATE TABLE archive_observations (
    archive_observation_id VARCHAR PRIMARY KEY CHECK (length(archive_observation_id) = 64),
    http_observation_id VARCHAR NOT NULL REFERENCES http_observations(observation_id),
    raw_object_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    source_kind VARCHAR NOT NULL,
    cadence VARCHAR NOT NULL CHECK (cadence IN ('daily', 'monthly')),
    exact_filename VARCHAR NOT NULL,
    expected_member VARCHAR NOT NULL,
    range_start_ms BIGINT NOT NULL,
    range_end_ms BIGINT NOT NULL CHECK (range_end_ms > range_start_ms),
    archive_available BOOLEAN NOT NULL,
    official_absence_status VARCHAR,
    CHECK (
        (archive_available = true AND official_absence_status IS NULL)
        OR (archive_available = false AND official_absence_status IS NOT NULL)
    )
);

CREATE TABLE publisher_checksums (
    checksum_observation_id VARCHAR PRIMARY KEY REFERENCES http_observations(observation_id),
    archive_raw_object_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    checksum_raw_object_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    exact_filename VARCHAR NOT NULL,
    publisher_sha256 VARCHAR NOT NULL CHECK (length(publisher_sha256) = 64),
    local_match BOOLEAN NOT NULL CHECK (local_match = true),
    UNIQUE (archive_raw_object_sha256, checksum_raw_object_sha256)
);

CREATE TABLE spot_kline_observations (
    observation_row_id VARCHAR PRIMARY KEY CHECK (length(observation_row_id) = 64),
    source_kind VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    row_number BIGINT NOT NULL CHECK (row_number > 0),
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    interval_name VARCHAR NOT NULL CHECK (interval_name = '1m'),
    open_time_ms BIGINT NOT NULL CHECK (open_time_ms % 60000 = 0),
    open_text VARCHAR NOT NULL,
    open_value DECIMAL(38,18) NOT NULL CHECK (open_value > 0),
    high_text VARCHAR NOT NULL,
    high_value DECIMAL(38,18) NOT NULL,
    low_text VARCHAR NOT NULL,
    low_value DECIMAL(38,18) NOT NULL,
    close_text VARCHAR NOT NULL,
    close_value DECIMAL(38,18) NOT NULL CHECK (close_value > 0),
    base_volume_text VARCHAR NOT NULL,
    base_volume_value DECIMAL(38,18) NOT NULL CHECK (base_volume_value >= 0),
    close_time_ms BIGINT NOT NULL,
    quote_volume_text VARCHAR NOT NULL,
    quote_volume_value DECIMAL(38,18) NOT NULL CHECK (quote_volume_value >= 0),
    trade_count BIGINT NOT NULL CHECK (trade_count >= 0),
    taker_buy_base_text VARCHAR NOT NULL,
    taker_buy_base_value DECIMAL(38,18) NOT NULL CHECK (taker_buy_base_value >= 0),
    taker_buy_quote_text VARCHAR NOT NULL,
    taker_buy_quote_value DECIMAL(38,18) NOT NULL CHECK (taker_buy_quote_value >= 0),
    ignore_text VARCHAR NOT NULL,
    invalid_reasons VARCHAR NOT NULL,
    CHECK (high_value >= greatest(open_value, close_value, low_value)),
    CHECK (low_value <= least(open_value, close_value, high_value)),
    UNIQUE (source_sha256, row_number),
    UNIQUE (source_kind, source_sha256, open_time_ms)
);

CREATE TABLE spot_agg_trades (
    source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    source_kind VARCHAR NOT NULL,
    row_number BIGINT NOT NULL CHECK (row_number > 0),
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    aggregate_trade_id BIGINT NOT NULL CHECK (aggregate_trade_id >= 0),
    price_text VARCHAR NOT NULL,
    price_value DECIMAL(38,18) NOT NULL CHECK (price_value > 0),
    quantity_text VARCHAR NOT NULL,
    quantity_value DECIMAL(38,18) NOT NULL CHECK (quantity_value > 0),
    first_trade_id BIGINT NOT NULL CHECK (first_trade_id >= 0),
    last_trade_id BIGINT NOT NULL CHECK (last_trade_id >= first_trade_id),
    timestamp_ms BIGINT NOT NULL,
    buyer_is_maker BOOLEAN NOT NULL,
    best_price_match BOOLEAN NOT NULL,
    PRIMARY KEY (source_sha256, aggregate_trade_id),
    UNIQUE (source_sha256, row_number)
);

CREATE TABLE derived_spot_klines (
    derivation_identity VARCHAR PRIMARY KEY CHECK (length(derivation_identity) = 64),
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    open_time_ms BIGINT NOT NULL CHECK (open_time_ms % 60000 = 0),
    close_time_ms BIGINT NOT NULL CHECK (close_time_ms = open_time_ms + 59999),
    open_text VARCHAR NOT NULL,
    open_value DECIMAL(38,18) NOT NULL CHECK (open_value > 0),
    high_text VARCHAR NOT NULL,
    high_value DECIMAL(38,18) NOT NULL,
    low_text VARCHAR NOT NULL,
    low_value DECIMAL(38,18) NOT NULL,
    close_text VARCHAR NOT NULL,
    close_value DECIMAL(38,18) NOT NULL CHECK (close_value > 0),
    base_volume_text VARCHAR NOT NULL,
    base_volume_value DECIMAL(38,18) NOT NULL CHECK (base_volume_value > 0),
    quote_volume_text VARCHAR NOT NULL,
    quote_volume_value DECIMAL(38,18) NOT NULL CHECK (quote_volume_value > 0),
    trade_count BIGINT NOT NULL CHECK (trade_count > 0),
    taker_buy_base_text VARCHAR NOT NULL,
    taker_buy_base_value DECIMAL(38,18) NOT NULL CHECK (taker_buy_base_value >= 0),
    taker_buy_quote_text VARCHAR NOT NULL,
    taker_buy_quote_value DECIMAL(38,18) NOT NULL CHECK (taker_buy_quote_value >= 0),
    first_aggregate_trade_id BIGINT NOT NULL,
    last_aggregate_trade_id BIGINT NOT NULL CHECK (last_aggregate_trade_id >= first_aggregate_trade_id),
    first_underlying_trade_id BIGINT NOT NULL,
    last_underlying_trade_id BIGINT NOT NULL CHECK (last_underlying_trade_id >= first_underlying_trade_id),
    primary_source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    source_sha256s_json VARCHAR NOT NULL,
    comparison_json VARCHAR NOT NULL,
    UNIQUE (symbol, open_time_ms)
);

CREATE TABLE perpetual_execution_observations (
    observation_row_id VARCHAR PRIMARY KEY CHECK (length(observation_row_id) = 64),
    source_kind VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    row_number BIGINT NOT NULL CHECK (row_number > 0),
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    interval_name VARCHAR NOT NULL CHECK (interval_name = '1m'),
    open_time_ms BIGINT NOT NULL CHECK (open_time_ms % 60000 = 0),
    open_text VARCHAR NOT NULL,
    open_value DECIMAL(38,18) NOT NULL CHECK (open_value > 0),
    high_text VARCHAR NOT NULL,
    high_value DECIMAL(38,18) NOT NULL,
    low_text VARCHAR NOT NULL,
    low_value DECIMAL(38,18) NOT NULL,
    close_text VARCHAR NOT NULL,
    close_value DECIMAL(38,18) NOT NULL CHECK (close_value > 0),
    volume_text VARCHAR NOT NULL,
    volume_value DECIMAL(38,18) NOT NULL CHECK (volume_value >= 0),
    close_time_ms BIGINT NOT NULL CHECK (close_time_ms = open_time_ms + 59999),
    quote_volume_text VARCHAR NOT NULL,
    quote_volume_value DECIMAL(38,18) NOT NULL CHECK (quote_volume_value >= 0),
    trade_count BIGINT NOT NULL CHECK (trade_count >= 0),
    taker_buy_base_text VARCHAR NOT NULL,
    taker_buy_base_value DECIMAL(38,18) NOT NULL CHECK (taker_buy_base_value >= 0),
    taker_buy_quote_text VARCHAR NOT NULL,
    taker_buy_quote_value DECIMAL(38,18) NOT NULL CHECK (taker_buy_quote_value >= 0),
    invalid_reasons VARCHAR NOT NULL,
    CHECK (high_value >= greatest(open_value, close_value, low_value)),
    CHECK (low_value <= least(open_value, close_value, high_value)),
    UNIQUE (source_sha256, row_number),
    UNIQUE (source_kind, source_sha256, open_time_ms)
);

CREATE TABLE perpetual_mark_observations (
    observation_row_id VARCHAR PRIMARY KEY CHECK (length(observation_row_id) = 64),
    source_kind VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    row_number BIGINT NOT NULL CHECK (row_number > 0),
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    interval_name VARCHAR NOT NULL CHECK (interval_name = '1m'),
    open_time_ms BIGINT NOT NULL CHECK (open_time_ms % 60000 = 0),
    open_text VARCHAR NOT NULL,
    open_value DECIMAL(38,18) NOT NULL CHECK (open_value > 0),
    high_text VARCHAR NOT NULL,
    high_value DECIMAL(38,18) NOT NULL,
    low_text VARCHAR NOT NULL,
    low_value DECIMAL(38,18) NOT NULL,
    close_text VARCHAR NOT NULL,
    close_value DECIMAL(38,18) NOT NULL CHECK (close_value > 0),
    close_time_ms BIGINT NOT NULL CHECK (close_time_ms = open_time_ms + 59999),
    invalid_reasons VARCHAR NOT NULL,
    CHECK (high_value >= greatest(open_value, close_value, low_value)),
    CHECK (low_value <= least(open_value, close_value, high_value)),
    UNIQUE (source_sha256, row_number),
    UNIQUE (source_kind, source_sha256, open_time_ms)
);

CREATE TABLE funding_observations (
    observation_row_id VARCHAR PRIMARY KEY CHECK (length(observation_row_id) = 64),
    source_kind VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    row_number BIGINT NOT NULL CHECK (row_number > 0),
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    funding_time_ms BIGINT NOT NULL,
    funding_interval_hours INTEGER,
    funding_rate_text VARCHAR NOT NULL,
    funding_rate_value DECIMAL(38,18) NOT NULL,
    mark_price_text VARCHAR,
    mark_price_value DECIMAL(38,18),
    UNIQUE (source_sha256, row_number),
    UNIQUE (source_kind, source_sha256, funding_time_ms)
);

CREATE TABLE instrument_metadata (
    metadata_identity VARCHAR PRIMARY KEY CHECK (length(metadata_identity) = 64),
    market_profile VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    instrument_id VARCHAR NOT NULL,
    source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    observed_at_utc VARCHAR NOT NULL,
    historical_exact BOOLEAN NOT NULL CHECK (historical_exact = false),
    source_role VARCHAR NOT NULL,
    raw_definition_json VARCHAR NOT NULL,
    limitations_json VARCHAR NOT NULL,
    UNIQUE (market_profile, instrument_id, source_sha256)
);

CREATE TABLE minute_coverage (
    market_profile VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    open_time_ms BIGINT NOT NULL CHECK (open_time_ms % 60000 = 0),
    disposition VARCHAR NOT NULL CHECK (disposition IN (
        'REAL_OFFICIAL_BAR',
        'DERIVED_FROM_OFFICIAL_TRADES',
        'VERIFIED_NO_TRADE_INTERVAL',
        'SOURCE_CONFLICT',
        'SOURCE_INCOMPLETE',
        'UNRESOLVED_GAP'
    )),
    canonical_identity VARCHAR,
    proof_identity VARCHAR,
    reason VARCHAR NOT NULL,
    blocking BOOLEAN NOT NULL,
    PRIMARY KEY (market_profile, symbol, open_time_ms),
    CHECK (
        (disposition IN ('REAL_OFFICIAL_BAR', 'DERIVED_FROM_OFFICIAL_TRADES') AND canonical_identity IS NOT NULL)
        OR (disposition = 'VERIFIED_NO_TRADE_INTERVAL' AND canonical_identity IS NULL AND proof_identity IS NOT NULL)
        OR (disposition IN ('SOURCE_CONFLICT', 'SOURCE_INCOMPLETE', 'UNRESOLVED_GAP') AND blocking = true)
    )
);

CREATE TABLE source_conflicts (
    conflict_identity VARCHAR PRIMARY KEY CHECK (length(conflict_identity) = 64),
    market_profile VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    open_time_ms BIGINT NOT NULL CHECK (open_time_ms % 60000 = 0),
    status VARCHAR NOT NULL CHECK (status IN ('RESOLVED_SUPERSEDED', 'UNRESOLVED_BLOCKING')),
    reason VARCHAR NOT NULL,
    observation_identities_json VARCHAR NOT NULL,
    resolution_identity VARCHAR,
    UNIQUE (market_profile, symbol, open_time_ms, conflict_identity)
);

CREATE TABLE verified_no_trade_intervals (
    proof_identity VARCHAR PRIMARY KEY CHECK (length(proof_identity) = 64),
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    start_ms BIGINT NOT NULL CHECK (start_ms % 60000 = 0),
    end_ms BIGINT NOT NULL CHECK (end_ms > start_ms AND end_ms % 60000 = 0),
    before_aggregate_trade_id BIGINT NOT NULL,
    after_aggregate_trade_id BIGINT NOT NULL CHECK (after_aggregate_trade_id = before_aggregate_trade_id + 1),
    before_last_trade_id BIGINT NOT NULL,
    after_first_trade_id BIGINT NOT NULL CHECK (after_first_trade_id = before_last_trade_id + 1),
    primary_source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    source_sha256s_json VARCHAR NOT NULL,
    classification VARCHAR NOT NULL CHECK (classification IN (
        'NO_TRADE_OBSERVED', 'PROBABLE_VENUE_OUTAGE', 'OFFICIALLY_ANNOUNCED_MAINTENANCE'
    )),
    official_announcement_identity VARCHAR,
    CHECK (classification <> 'OFFICIALLY_ANNOUNCED_MAINTENANCE' OR official_announcement_identity IS NOT NULL)
);

CREATE TABLE canonical_execution_bars (
    market_profile VARCHAR NOT NULL,
    instrument_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    open_time_ms BIGINT NOT NULL CHECK (open_time_ms % 60000 = 0),
    close_time_ms BIGINT NOT NULL CHECK (close_time_ms = open_time_ms + 59999),
    disposition VARCHAR NOT NULL CHECK (disposition IN ('REAL_OFFICIAL_BAR', 'DERIVED_FROM_OFFICIAL_TRADES')),
    canonical_identity VARCHAR NOT NULL CHECK (length(canonical_identity) = 64),
    open_text VARCHAR NOT NULL,
    open_value DECIMAL(38,18) NOT NULL CHECK (open_value > 0),
    high_text VARCHAR NOT NULL,
    high_value DECIMAL(38,18) NOT NULL,
    low_text VARCHAR NOT NULL,
    low_value DECIMAL(38,18) NOT NULL,
    close_text VARCHAR NOT NULL,
    close_value DECIMAL(38,18) NOT NULL CHECK (close_value > 0),
    volume_text VARCHAR NOT NULL,
    volume_value DECIMAL(38,18) NOT NULL CHECK (volume_value >= 0),
    quote_volume_text VARCHAR NOT NULL,
    quote_volume_value DECIMAL(38,18) NOT NULL CHECK (quote_volume_value >= 0),
    trade_count BIGINT NOT NULL CHECK (trade_count >= 0),
    taker_buy_base_text VARCHAR NOT NULL,
    taker_buy_base_value DECIMAL(38,18) NOT NULL CHECK (taker_buy_base_value >= 0),
    taker_buy_quote_text VARCHAR NOT NULL,
    taker_buy_quote_value DECIMAL(38,18) NOT NULL CHECK (taker_buy_quote_value >= 0),
    primary_source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    source_bindings_json VARCHAR NOT NULL,
    PRIMARY KEY (market_profile, instrument_id, open_time_ms),
    CHECK (high_value >= greatest(open_value, close_value, low_value)),
    CHECK (low_value <= least(open_value, close_value, high_value))
);

CREATE TABLE canonical_mark_bars (
    market_profile VARCHAR NOT NULL,
    instrument_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    open_time_ms BIGINT NOT NULL CHECK (open_time_ms % 60000 = 0),
    close_time_ms BIGINT NOT NULL CHECK (close_time_ms = open_time_ms + 59999),
    canonical_identity VARCHAR NOT NULL CHECK (length(canonical_identity) = 64),
    open_text VARCHAR NOT NULL,
    open_value DECIMAL(38,18) NOT NULL CHECK (open_value > 0),
    high_text VARCHAR NOT NULL,
    high_value DECIMAL(38,18) NOT NULL,
    low_text VARCHAR NOT NULL,
    low_value DECIMAL(38,18) NOT NULL,
    close_text VARCHAR NOT NULL,
    close_value DECIMAL(38,18) NOT NULL CHECK (close_value > 0),
    primary_source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    source_bindings_json VARCHAR NOT NULL,
    PRIMARY KEY (market_profile, instrument_id, open_time_ms),
    CHECK (high_value >= greatest(open_value, close_value, low_value)),
    CHECK (low_value <= least(open_value, close_value, high_value))
);

CREATE TABLE canonical_funding_events (
    event_identity VARCHAR PRIMARY KEY CHECK (length(event_identity) = 64),
    market_profile VARCHAR NOT NULL,
    instrument_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    funding_time_ms BIGINT NOT NULL,
    funding_interval_hours INTEGER NOT NULL CHECK (funding_interval_hours > 0),
    funding_rate_text VARCHAR NOT NULL,
    funding_rate_value DECIMAL(38,18) NOT NULL,
    primary_source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    source_bindings_json VARCHAR NOT NULL,
    UNIQUE (market_profile, instrument_id, funding_time_ms)
);

CREATE TABLE validation_results (
    validation_identity VARCHAR PRIMARY KEY CHECK (length(validation_identity) = 64),
    validation_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('PASS', 'FAIL', 'BLOCKED')),
    material_json VARCHAR NOT NULL,
    checked_at_utc VARCHAR NOT NULL,
    UNIQUE (validation_name, validation_identity)
);

CREATE TABLE dataset_releases (
    dataset_release_id VARCHAR PRIMARY KEY CHECK (length(dataset_release_id) = 64),
    market_profile VARCHAR NOT NULL,
    instrument_id VARCHAR NOT NULL,
    start_ms BIGINT NOT NULL,
    end_ms BIGINT NOT NULL CHECK (end_ms > start_ms),
    status VARCHAR NOT NULL CHECK (status IN ('PASS', 'DATASET_RELEASE_BLOCKED')),
    minute_coverage_identity VARCHAR NOT NULL CHECK (length(minute_coverage_identity) = 64),
    source_reconciliation_identity VARCHAR NOT NULL CHECK (length(source_reconciliation_identity) = 64),
    instrument_metadata_identity VARCHAR NOT NULL CHECK (length(instrument_metadata_identity) = 64),
    funding_data_identity VARCHAR NOT NULL,
    mark_data_identity VARCHAR NOT NULL,
    normalizer_version VARCHAR NOT NULL,
    catalog_identity VARCHAR NOT NULL,
    derived_validation_identity VARCHAR NOT NULL CHECK (length(derived_validation_identity) = 64),
    data_tool_lock_identity VARCHAR NOT NULL CHECK (length(data_tool_lock_identity) = 64),
    material_manifest_json VARCHAR NOT NULL,
    created_at_utc VARCHAR NOT NULL,
    UNIQUE (market_profile, instrument_id, dataset_release_id)
);

CREATE TABLE rebuild_manifests (
    rebuild_identity VARCHAR PRIMARY KEY CHECK (length(rebuild_identity) = 64),
    database_role VARCHAR NOT NULL CHECK (database_role IN ('PRIMARY', 'INDEPENDENT_REBUILD')),
    schema_identity VARCHAR NOT NULL REFERENCES schema_metadata(schema_identity),
    semantic_identity VARCHAR NOT NULL CHECK (length(semantic_identity) = 64),
    source_inventory_identity VARCHAR NOT NULL CHECK (length(source_inventory_identity) = 64),
    row_counts_json VARCHAR NOT NULL,
    min_max_timestamps_json VARCHAR NOT NULL,
    completed_at_utc VARCHAR NOT NULL,
    UNIQUE (database_role, rebuild_identity)
);
