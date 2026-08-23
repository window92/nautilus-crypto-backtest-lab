CREATE TABLE schema_metadata (
    schema_identity VARCHAR PRIMARY KEY CHECK (length(schema_identity) = 64),
    schema_version VARCHAR NOT NULL UNIQUE,
    duckdb_version VARCHAR NOT NULL CHECK (duckdb_version = '1.4.5'),
    timestamp_contract VARCHAR NOT NULL CHECK (timestamp_contract = 'UTC_INTEGER_NANOSECONDS_CANONICAL;SOURCE_UNITS_EXPLICIT'),
    extensions_allowed BOOLEAN NOT NULL CHECK (extensions_allowed = false),
    network_allowed BOOLEAN NOT NULL CHECK (network_allowed = false)
);

CREATE TABLE raw_objects (
    raw_object_sha256 VARCHAR PRIMARY KEY CHECK (regexp_full_match(raw_object_sha256, '[0-9a-f]{64}')),
    byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
    local_path VARCHAR NOT NULL,
    authority VARCHAR NOT NULL CHECK (authority = 'IMMUTABLE_BINANCE_OFFICIAL_RAW_BYTES'),
    content_verified BOOLEAN NOT NULL CHECK (content_verified = true),
    UNIQUE (raw_object_sha256, byte_size)
);

CREATE TABLE source_observations (
    observation_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(observation_id, '[0-9a-f]{64}')),
    raw_object_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    exact_locator VARCHAR NOT NULL,
    exact_query_json VARCHAR NOT NULL,
    http_status INTEGER CHECK (http_status BETWEEN 100 AND 599),
    response_headers_json VARCHAR NOT NULL,
    captured_at_utc VARCHAR NOT NULL,
    source_role VARCHAR NOT NULL,
    instrument VARCHAR NOT NULL,
    requested_interval VARCHAR NOT NULL,
    requested_start_ms BIGINT,
    requested_end_ms BIGINT,
    pagination_identity VARCHAR NOT NULL,
    parsed_event_time_ms BIGINT,
    semantic_row_sha256 VARCHAR CHECK (semantic_row_sha256 IS NULL OR regexp_full_match(semantic_row_sha256, '[0-9a-f]{64}')),
    original_row_json VARCHAR,
    validation_status VARCHAR NOT NULL CHECK (validation_status IN ('RAW_PRESERVED', 'PASS', 'SUPERSEDED', 'UNAVAILABLE')),
    delivery_classification VARCHAR,
    CHECK (requested_end_ms IS NULL OR requested_start_ms IS NULL OR requested_end_ms > requested_start_ms),
    UNIQUE (raw_object_sha256, source_role, pagination_identity, parsed_event_time_ms, semantic_row_sha256)
);

CREATE TABLE publisher_checksums (
    checksum_identity VARCHAR PRIMARY KEY CHECK (regexp_full_match(checksum_identity, '[0-9a-f]{64}')),
    archive_raw_object_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    checksum_raw_object_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    exact_filename VARCHAR NOT NULL,
    publisher_sha256 VARCHAR NOT NULL CHECK (regexp_full_match(publisher_sha256, '[0-9a-f]{64}')),
    local_match BOOLEAN NOT NULL CHECK (local_match = true),
    UNIQUE (archive_raw_object_sha256, checksum_raw_object_sha256)
);

CREATE TABLE source_conflicts (
    conflict_identity VARCHAR PRIMARY KEY CHECK (regexp_full_match(conflict_identity, '[0-9a-f]{64}')),
    market_profile VARCHAR NOT NULL,
    instrument_id VARCHAR NOT NULL,
    open_time_ns BIGINT NOT NULL CHECK (open_time_ns % 60000000000 = 0),
    status VARCHAR NOT NULL CHECK (status IN ('RESOLVED_SUPERSEDED', 'UNRESOLVED_BLOCKING')),
    reason VARCHAR NOT NULL,
    source_observation_ids_json VARCHAR NOT NULL,
    resolution_identity VARCHAR CHECK (resolution_identity IS NULL OR regexp_full_match(resolution_identity, '[0-9a-f]{64}')),
    UNIQUE (market_profile, instrument_id, open_time_ns, conflict_identity)
);

CREATE TABLE spot_agg_trades (
    source_raw_object_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    aggregate_trade_id BIGINT NOT NULL CHECK (aggregate_trade_id >= 0),
    source_role VARCHAR NOT NULL CHECK (source_role LIKE 'SPOT%AGG%'),
    symbol VARCHAR NOT NULL CHECK (symbol = 'BTCUSDT'),
    row_number BIGINT NOT NULL CHECK (row_number > 0),
    price_text VARCHAR NOT NULL,
    price_value DECIMAL(38,18) NOT NULL CHECK (price_value > 0),
    quantity_text VARCHAR NOT NULL,
    quantity_value DECIMAL(38,18) NOT NULL CHECK (quantity_value > 0),
    first_trade_id BIGINT NOT NULL CHECK (first_trade_id >= 0),
    last_trade_id BIGINT NOT NULL CHECK (last_trade_id >= first_trade_id),
    event_time_ms BIGINT NOT NULL,
    buyer_is_maker BOOLEAN NOT NULL,
    best_price_match BOOLEAN NOT NULL,
    PRIMARY KEY (source_raw_object_sha256, aggregate_trade_id),
    UNIQUE (source_raw_object_sha256, row_number)
);

CREATE TABLE spot_execution_bars_1m (
    canonical_bar_identity VARCHAR PRIMARY KEY CHECK (regexp_full_match(canonical_bar_identity, '[0-9a-f]{64}')),
    instrument_id VARCHAR NOT NULL CHECK (instrument_id = 'BTCUSDT.BINANCE'),
    open_time_ns BIGINT NOT NULL CHECK (open_time_ns % 60000000000 = 0),
    end_exclusive_ns BIGINT NOT NULL CHECK (end_exclusive_ns = open_time_ns + 60000000000),
    available_at_ns BIGINT NOT NULL CHECK (available_at_ns = end_exclusive_ns),
    disposition VARCHAR NOT NULL CHECK (disposition IN ('REAL_OFFICIAL_BAR', 'DERIVED_FROM_OFFICIAL_TRADES')),
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
    primary_source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    source_sha256s_json VARCHAR NOT NULL,
    CHECK (high_value >= greatest(open_value, close_value, low_value)),
    CHECK (low_value <= least(open_value, close_value, high_value)),
    UNIQUE (instrument_id, open_time_ns)
);

CREATE TABLE perpetual_execution_bars_1m (
    canonical_bar_identity VARCHAR PRIMARY KEY CHECK (regexp_full_match(canonical_bar_identity, '[0-9a-f]{64}')),
    instrument_id VARCHAR NOT NULL CHECK (instrument_id = 'BTCUSDT-PERP.BINANCE'),
    open_time_ns BIGINT NOT NULL CHECK (open_time_ns % 60000000000 = 0),
    end_exclusive_ns BIGINT NOT NULL CHECK (end_exclusive_ns = open_time_ns + 60000000000),
    available_at_ns BIGINT NOT NULL CHECK (available_at_ns = end_exclusive_ns),
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
    source_sha256s_json VARCHAR NOT NULL,
    CHECK (high_value >= greatest(open_value, close_value, low_value)),
    CHECK (low_value <= least(open_value, close_value, high_value)),
    UNIQUE (instrument_id, open_time_ns)
);

CREATE TABLE perpetual_mark_bars_1m (
    canonical_bar_identity VARCHAR PRIMARY KEY CHECK (regexp_full_match(canonical_bar_identity, '[0-9a-f]{64}')),
    instrument_id VARCHAR NOT NULL CHECK (instrument_id = 'BTCUSDT-PERP.BINANCE'),
    open_time_ns BIGINT NOT NULL CHECK (open_time_ns % 60000000000 = 0),
    end_exclusive_ns BIGINT NOT NULL CHECK (end_exclusive_ns = open_time_ns + 60000000000),
    available_at_ns BIGINT NOT NULL CHECK (available_at_ns = end_exclusive_ns),
    open_text VARCHAR NOT NULL,
    open_value DECIMAL(38,18) NOT NULL CHECK (open_value > 0),
    high_text VARCHAR NOT NULL,
    high_value DECIMAL(38,18) NOT NULL,
    low_text VARCHAR NOT NULL,
    low_value DECIMAL(38,18) NOT NULL,
    close_text VARCHAR NOT NULL,
    close_value DECIMAL(38,18) NOT NULL CHECK (close_value > 0),
    primary_source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    source_sha256s_json VARCHAR NOT NULL,
    CHECK (high_value >= greatest(open_value, close_value, low_value)),
    CHECK (low_value <= least(open_value, close_value, high_value)),
    UNIQUE (instrument_id, open_time_ns)
);

CREATE TABLE perpetual_funding_events (
    event_identity VARCHAR PRIMARY KEY CHECK (regexp_full_match(event_identity, '[0-9a-f]{64}')),
    instrument_id VARCHAR NOT NULL CHECK (instrument_id = 'BTCUSDT-PERP.BINANCE'),
    funding_time_ns BIGINT NOT NULL,
    funding_interval_hours INTEGER NOT NULL CHECK (funding_interval_hours > 0),
    funding_rate_text VARCHAR NOT NULL,
    funding_rate_value DECIMAL(38,18) NOT NULL,
    primary_source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    source_sha256s_json VARCHAR NOT NULL,
    UNIQUE (instrument_id, funding_time_ns)
);

CREATE TABLE verified_no_trade_intervals (
    proof_identity VARCHAR PRIMARY KEY CHECK (regexp_full_match(proof_identity, '[0-9a-f]{64}')),
    instrument_id VARCHAR NOT NULL CHECK (instrument_id = 'BTCUSDT.BINANCE'),
    start_ns BIGINT NOT NULL CHECK (start_ns % 60000000000 = 0),
    end_exclusive_ns BIGINT NOT NULL CHECK (end_exclusive_ns > start_ns AND end_exclusive_ns % 60000000000 = 0),
    before_trade_id BIGINT NOT NULL,
    after_trade_id BIGINT NOT NULL CHECK (after_trade_id = before_trade_id + 1),
    before_aggregate_id BIGINT NOT NULL,
    after_aggregate_id BIGINT NOT NULL CHECK (after_aggregate_id = before_aggregate_id + 1),
    raw_trade_source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    aggtrade_source_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    proof_json VARCHAR NOT NULL,
    UNIQUE (instrument_id, start_ns, end_exclusive_ns)
);

CREATE TABLE minute_dispositions (
    market_profile VARCHAR NOT NULL,
    instrument_id VARCHAR NOT NULL,
    open_time_ns BIGINT NOT NULL CHECK (open_time_ns % 60000000000 = 0),
    disposition VARCHAR NOT NULL CHECK (disposition IN (
        'REAL_OFFICIAL_BAR', 'DERIVED_FROM_OFFICIAL_TRADES', 'VERIFIED_NO_TRADE_INTERVAL',
        'SOURCE_CONFLICT', 'SOURCE_INCOMPLETE', 'UNRESOLVED_GAP'
    )),
    canonical_bar_identity VARCHAR,
    proof_identity VARCHAR,
    source_reconciliation_identity VARCHAR NOT NULL CHECK (regexp_full_match(source_reconciliation_identity, '[0-9a-f]{64}')),
    reason VARCHAR NOT NULL,
    blocking BOOLEAN NOT NULL,
    PRIMARY KEY (market_profile, instrument_id, open_time_ns),
    CHECK (
        (disposition IN ('REAL_OFFICIAL_BAR', 'DERIVED_FROM_OFFICIAL_TRADES') AND canonical_bar_identity IS NOT NULL AND proof_identity IS NULL AND blocking = false)
        OR (disposition = 'VERIFIED_NO_TRADE_INTERVAL' AND canonical_bar_identity IS NULL AND proof_identity IS NOT NULL AND blocking = false)
        OR (disposition IN ('SOURCE_CONFLICT', 'SOURCE_INCOMPLETE', 'UNRESOLVED_GAP') AND canonical_bar_identity IS NULL AND blocking = true)
    )
);

CREATE TABLE instrument_metadata (
    instrument_metadata_identity VARCHAR PRIMARY KEY CHECK (regexp_full_match(instrument_metadata_identity, '[0-9a-f]{64}')),
    market_profile VARCHAR NOT NULL,
    instrument_id VARCHAR NOT NULL UNIQUE,
    source_raw_object_sha256 VARCHAR NOT NULL REFERENCES raw_objects(raw_object_sha256),
    observed_at_utc VARCHAR NOT NULL,
    historical_exact BOOLEAN NOT NULL CHECK (historical_exact = false),
    metadata_json VARCHAR NOT NULL
);

CREATE TABLE data_windows (
    data_window_identity VARCHAR PRIMARY KEY CHECK (regexp_full_match(data_window_identity, '[0-9a-f]{64}')),
    classification VARCHAR NOT NULL CHECK (classification IN ('EXPOSED_DATA_BLOCKED_NOT_FINAL_HOLDOUT', 'DATA_QUALITY_INSPECTED_NOT_FINAL_HOLDOUT')),
    shift_months INTEGER NOT NULL CHECK (shift_months >= 0),
    dataset_start_ns BIGINT NOT NULL,
    scoring_start_ns BIGINT NOT NULL,
    scoring_end_exclusive_ns BIGINT NOT NULL,
    dataset_end_exclusive_ns BIGINT NOT NULL,
    partition_geometry_identity VARCHAR NOT NULL CHECK (regexp_full_match(partition_geometry_identity, '[0-9a-f]{64}')),
    status VARCHAR NOT NULL CHECK (status IN ('PASS', 'FAIL')),
    reason VARCHAR NOT NULL,
    strategy_performance_inspected BOOLEAN NOT NULL CHECK (strategy_performance_inspected = false),
    CHECK (dataset_start_ns < scoring_start_ns AND scoring_start_ns < scoring_end_exclusive_ns AND scoring_end_exclusive_ns = dataset_end_exclusive_ns)
);

CREATE TABLE dataset_releases (
    dataset_release_id VARCHAR PRIMARY KEY CHECK (regexp_full_match(dataset_release_id, '[0-9a-f]{64}')),
    market_profile VARCHAR NOT NULL,
    instrument_id VARCHAR NOT NULL,
    data_window_identity VARCHAR NOT NULL REFERENCES data_windows(data_window_identity),
    partition_geometry_identity VARCHAR NOT NULL CHECK (regexp_full_match(partition_geometry_identity, '[0-9a-f]{64}')),
    minute_coverage_identity VARCHAR NOT NULL CHECK (regexp_full_match(minute_coverage_identity, '[0-9a-f]{64}')),
    source_reconciliation_identity VARCHAR NOT NULL CHECK (regexp_full_match(source_reconciliation_identity, '[0-9a-f]{64}')),
    catalog_identity VARCHAR NOT NULL CHECK (regexp_full_match(catalog_identity, '[0-9a-f]{64}')),
    semantic_release_json VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status = 'PASS'),
    created_at_utc VARCHAR NOT NULL,
    UNIQUE (market_profile, instrument_id, data_window_identity)
);

CREATE TABLE release_members (
    dataset_release_id VARCHAR NOT NULL REFERENCES dataset_releases(dataset_release_id),
    member_type VARCHAR NOT NULL CHECK (member_type IN ('RAW_OBJECT', 'EXECUTION_BAR', 'MARK_BAR', 'FUNDING_EVENT', 'MINUTE_DISPOSITION', 'INSTRUMENT_METADATA')),
    member_identity VARCHAR NOT NULL,
    source_raw_object_sha256 VARCHAR REFERENCES raw_objects(raw_object_sha256),
    PRIMARY KEY (dataset_release_id, member_type, member_identity)
);

CREATE TABLE validation_results (
    validation_identity VARCHAR PRIMARY KEY CHECK (regexp_full_match(validation_identity, '[0-9a-f]{64}')),
    validation_name VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('PASS', 'FAIL', 'BLOCKED')),
    material_json VARCHAR NOT NULL,
    checked_at_utc VARCHAR NOT NULL,
    UNIQUE (validation_name, validation_identity)
);

CREATE TABLE build_manifests (
    build_identity VARCHAR PRIMARY KEY CHECK (regexp_full_match(build_identity, '[0-9a-f]{64}')),
    build_role VARCHAR NOT NULL CHECK (build_role IN ('PRIMARY', 'INDEPENDENT_REBUILD')),
    schema_identity VARCHAR NOT NULL REFERENCES schema_metadata(schema_identity),
    source_inventory_identity VARCHAR NOT NULL CHECK (regexp_full_match(source_inventory_identity, '[0-9a-f]{64}')),
    semantic_database_identity VARCHAR NOT NULL CHECK (regexp_full_match(semantic_database_identity, '[0-9a-f]{64}')),
    semantic_export_contract VARCHAR NOT NULL CHECK (semantic_export_contract = 'DUCKDB_1_4_5_SORTED_CSV_HEADER_LF_EXACT_TYPES_V1'),
    table_hashes_json VARCHAR NOT NULL,
    row_counts_json VARCHAR NOT NULL,
    dataset_release_ids_json VARCHAR NOT NULL,
    catalog_identities_json VARCHAR NOT NULL,
    completed_at_utc VARCHAR NOT NULL,
    UNIQUE (build_role, build_identity)
);
