CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bars (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL DEFAULT 0,
    trade_count INTEGER,
    vwap REAL,
    source TEXT DEFAULT 'alpaca',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timeframe, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_bars_symbol_timeframe_timestamp ON bars(symbol, timeframe, timestamp);
CREATE INDEX IF NOT EXISTS idx_bars_timestamp ON bars(timestamp);

CREATE TABLE IF NOT EXISTS bar_variants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL DEFAULT 0,
    trade_count INTEGER,
    vwap REAL,
    source TEXT DEFAULT 'alpaca',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timeframe, timestamp, adjustment)
);

CREATE INDEX IF NOT EXISTS idx_bar_variants_symbol_timeframe_timestamp ON bar_variants(symbol, timeframe, timestamp);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    bid_price REAL NOT NULL,
    bid_size REAL DEFAULT 0,
    ask_price REAL NOT NULL,
    ask_size REAL DEFAULT 0,
    spread REAL,
    spread_pct REAL,
    quote_imbalance REAL,
    source TEXT DEFAULT 'alpaca',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_quotes_symbol_timestamp ON quotes(symbol, timestamp);

CREATE TABLE IF NOT EXISTS market_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    price REAL NOT NULL,
    size REAL DEFAULT 0,
    exchange TEXT,
    conditions TEXT,
    tape TEXT,
    source TEXT DEFAULT 'alpaca',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timestamp, price, size, exchange)
);

CREATE INDEX IF NOT EXISTS idx_market_trades_symbol_timestamp ON market_trades(symbol, timestamp);

CREATE TABLE IF NOT EXISTS account_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equity REAL,
    cash REAL,
    buying_power REAL,
    daytrade_count INTEGER,
    portfolio_value REAL,
    multiplier REAL,
    long_market_value REAL,
    short_market_value REAL,
    timestamp TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'minute',
    realized_pl REAL DEFAULT 0,
    unrealized_pl REAL DEFAULT 0,
    session_start_equity REAL,
    session_peak_equity REAL,
    drawdown REAL DEFAULT 0,
    drawdown_pct REAL DEFAULT 0,
    open_position_count INTEGER DEFAULT 0,
    open_order_count INTEGER DEFAULT 0,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alpaca_order_id TEXT,
    client_order_id TEXT UNIQUE,
    parent_order_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    position_side TEXT,
    qty REAL,
    notional REAL,
    order_type TEXT,
    order_class TEXT,
    time_in_force TEXT,
    limit_price REAL,
    stop_price REAL,
    take_profit_price REAL,
    status TEXT,
    submitted_at TEXT,
    filled_at TEXT,
    filled_qty REAL,
    filled_avg_price REAL,
    cancel_reason TEXT,
    strategy_path TEXT,
    playbook TEXT,
    maximum_loss REAL,
    economic_breakeven_pct REAL,
    risk_details_json TEXT,
    mode TEXT DEFAULT 'paper',
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_symbol_status ON orders(symbol, status);

CREATE TABLE IF NOT EXISTS decision_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_source TEXT NOT NULL,
    decision_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy_path TEXT NOT NULL,
    playbook TEXT,
    original_action TEXT,
    executed_action TEXT NOT NULL,
    execution_status TEXT NOT NULL,
    client_order_id TEXT,
    root_episode_id TEXT,
    model_scope TEXT,
    model_version TEXT,
    spread_pct REAL,
    expected_slippage_pct REAL,
    fill_quality_score REAL,
    direction_available INTEGER DEFAULT 1,
    session_phase TEXT,
    execution_error TEXT,
    feature_snapshot_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(decision_source, decision_id)
);

CREATE INDEX IF NOT EXISTS idx_decision_executions_time ON decision_executions(timestamp, strategy_path);
CREATE INDEX IF NOT EXISTS idx_decision_executions_episode ON decision_executions(root_episode_id);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL,
    price REAL,
    timestamp TEXT NOT NULL,
    client_order_id TEXT,
    episode_id TEXT,
    strategy_path TEXT,
    playbook TEXT,
    expected_price REAL,
    submitted_price REAL,
    bid_price REAL,
    ask_price REAL,
    midpoint REAL,
    spread_at_entry REAL,
    spread_pct REAL,
    slippage REAL,
    slippage_per_share REAL,
    slippage_cost REAL DEFAULT 0,
    spread_cost REAL DEFAULT 0,
    estimated_fee REAL DEFAULT 0,
    estimated_live_cost REAL DEFAULT 0,
    mode TEXT DEFAULT 'paper',
    raw_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_fills_symbol_timestamp ON fills(symbol, timestamp);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fills_order_id_unique
ON fills(order_id)
WHERE order_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT,
    qty REAL,
    avg_entry_price REAL,
    market_value REAL,
    unrealized_pl REAL,
    unrealized_plpc REAL,
    opened_at TEXT,
    closed_at TEXT,
    status TEXT,
    UNIQUE(symbol, opened_at, status)
);

CREATE TABLE IF NOT EXISTS trade_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_time TEXT,
    exit_time TEXT,
    entry_price REAL,
    exit_price REAL,
    qty REAL,
    notional REAL,
    gross_pnl REAL,
    net_pnl_estimated REAL,
    pnl_pct REAL,
    max_favorable_excursion REAL,
    max_adverse_excursion REAL,
    holding_seconds REAL,
    exit_reason TEXT,
    win_loss TEXT,
    mistake_category TEXT,
    setup_type TEXT,
    model_version TEXT,
    strategy_version TEXT,
    exploration_trade INTEGER DEFAULT 0,
    root_episode_id TEXT,
    strategy_path TEXT DEFAULT 'minute',
    playbook TEXT,
    regime TEXT,
    ml_prediction TEXT,
    confidence REAL,
    spread_cost REAL DEFAULT 0,
    slippage_cost REAL DEFAULT 0,
    estimated_fees REAL DEFAULT 0,
    estimated_live_cost REAL DEFAULT 0,
    net_pnl_after_costs REAL,
    opportunity_cost REAL DEFAULT 0,
    profit_given_back REAL DEFAULT 0,
    mode TEXT DEFAULT 'paper'
);

CREATE INDEX IF NOT EXISTS idx_trade_outcomes_symbol_entry ON trade_outcomes(symbol, entry_time);

CREATE TABLE IF NOT EXISTS trading_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    signal_id INTEGER,
    strategy_path TEXT,
    playbook TEXT,
    decision TEXT,
    confidence REAL,
    bullish_score REAL,
    bearish_score REAL,
    no_trade_score REAL,
    regime TEXT,
    reason TEXT,
    risk_block_reason TEXT,
    model_version TEXT,
    model_prediction TEXT,
    probability_long REAL,
    probability_short REAL,
    probability_no_trade REAL,
    order_id TEXT,
    client_order_id TEXT,
    side TEXT,
    qty REAL,
    price REAL,
    notional REAL,
    status TEXT,
    pnl REAL,
    pattern_classification TEXT,
    pattern_quality REAL,
    liquidity_score REAL,
    volatility_regime TEXT,
    missed_opportunity_label TEXT,
    reasoning_agents_json TEXT,
    macro_bias TEXT,
    macro_confidence REAL,
    target_exposure_pct REAL,
    order_block_direction TEXT,
    order_block_timeframe TEXT,
    order_block_strength REAL,
    order_block_retest_active INTEGER DEFAULT 0,
    options_bias TEXT,
    options_confidence REAL,
    options_score_adjustment REAL,
    options_event_risk REAL,
    exploration_trade INTEGER DEFAULT 0,
    feature_snapshot_json TEXT,
    broker_snapshot_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trading_journal_symbol_timestamp ON trading_journal(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_trading_journal_event_type ON trading_journal(event_type);
CREATE INDEX IF NOT EXISTS idx_trading_journal_client_order_id ON trading_journal(client_order_id);

CREATE TABLE IF NOT EXISTS trade_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_outcome_id INTEGER NOT NULL UNIQUE,
    trade_id TEXT UNIQUE,
    signal_id INTEGER,
    journal_id INTEGER,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_time TEXT,
    exit_time TEXT,
    result_label TEXT NOT NULL,
    review_type TEXT NOT NULL,
    setup_quality REAL,
    learning_reward REAL,
    net_return REAL,
    max_favorable_excursion REAL,
    max_adverse_excursion REAL,
    exit_reason TEXT,
    mistake_category TEXT,
    exploration_trade INTEGER DEFAULT 0,
    entry_reason TEXT,
    lesson_summary TEXT,
    positive_factors_json TEXT,
    negative_factors_json TEXT,
    counterfactual_json TEXT,
    feature_snapshot_json TEXT,
    outcome_snapshot_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(trade_outcome_id) REFERENCES trade_outcomes(id) ON DELETE CASCADE,
    FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE SET NULL,
    FOREIGN KEY(journal_id) REFERENCES trading_journal(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_trade_reviews_symbol_exit ON trade_reviews(symbol, exit_time);
CREATE INDEX IF NOT EXISTS idx_trade_reviews_result_label ON trade_reviews(result_label);

CREATE TABLE IF NOT EXISTS ema_cross_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    bar_timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timeframe_minutes INTEGER NOT NULL,
    strategy_name TEXT NOT NULL DEFAULT 'ema_cross_filtered',
    decision TEXT NOT NULL,
    fast_ema REAL,
    slow_ema REAL,
    adx REAL,
    atr REAL,
    adx_passed INTEGER DEFAULT 0,
    cooldown_passed INTEGER DEFAULT 0,
    eligible INTEGER DEFAULT 0,
    confidence REAL,
    score REAL,
    execution_status TEXT NOT NULL,
    signal_id INTEGER,
    client_order_id TEXT,
    block_reason TEXT,
    feature_snapshot_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timeframe_minutes, bar_timestamp, decision)
);

CREATE INDEX IF NOT EXISTS idx_ema_cross_signals_symbol_time
ON ema_cross_signals(symbol, timestamp, timeframe_minutes);

CREATE INDEX IF NOT EXISTS idx_ema_cross_signals_execution
ON ema_cross_signals(execution_status, timestamp);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    bullish_score REAL,
    bearish_score REAL,
    no_trade_score REAL,
    regime TEXT,
    decision TEXT,
    confidence REAL,
    reason TEXT,
    feature_snapshot_json TEXT,
    model_version TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_signals_symbol_timestamp ON signals(symbol, timestamp);

CREATE TABLE IF NOT EXISTS no_trade_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    reason TEXT,
    bullish_score REAL,
    bearish_score REAL,
    regime TEXT,
    spread_pct REAL,
    volume_condition TEXT,
    volatility_condition TEXT,
    risk_block_reason TEXT,
    model_rejection_reason TEXT,
    feature_snapshot_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_no_trade_symbol_timestamp ON no_trade_logs(symbol, timestamp);

CREATE TABLE IF NOT EXISTS missed_opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER UNIQUE,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision TEXT,
    label TEXT NOT NULL,
    entry_price REAL,
    horizon_minutes INTEGER,
    max_up_move_pct REAL,
    max_down_move_pct REAL,
    max_favorable_move_pct REAL,
    max_adverse_move_pct REAL,
    review_reason TEXT,
    feature_snapshot_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_missed_opportunities_symbol_timestamp ON missed_opportunities(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_missed_opportunities_label ON missed_opportunities(label);

CREATE TABLE IF NOT EXISTS macro_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    horizon TEXT NOT NULL,
    gold_news_sentiment REAL,
    usd_sentiment REAL,
    fed_rate_sentiment REAL,
    risk_off_sentiment REAL,
    headline_event_risk REAL,
    sentiment_alignment REAL,
    macro_bias TEXT,
    macro_confidence REAL,
    news_linked_fraction REAL,
    advisory_only INTEGER DEFAULT 1,
    positive_developments_json TEXT,
    potential_concerns_json TEXT,
    forecast_summary TEXT,
    source TEXT DEFAULT 'local_macro_context',
    raw_inputs_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_macro_context_timestamp ON macro_context(timestamp);

CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT,
    headline TEXT NOT NULL,
    url TEXT,
    author TEXT,
    summary TEXT,
    content_text TEXT,
    symbols_json TEXT,
    category TEXT,
    event_type TEXT,
    sentiment_score REAL,
    confidence_score REAL,
    novelty_score REAL,
    gold_impact TEXT,
    gold_score REAL,
    usd_score REAL,
    rates_score REAL,
    risk_score REAL,
    event_risk REAL,
    related_move_1m REAL,
    related_move_5m REAL,
    related_move_15m REAL,
    related_move_1h REAL,
    related_move_1d REAL,
    related_spread_change_1m REAL,
    related_spread_change_5m REAL,
    related_spread_change_15m REAL,
    outcome_linked INTEGER DEFAULT 0,
    sentiment_trusted INTEGER DEFAULT 0,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(timestamp, source, headline)
);

CREATE INDEX IF NOT EXISTS idx_news_items_timestamp ON news_items(timestamp);

CREATE TABLE IF NOT EXISTS economic_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT UNIQUE,
    scheduled_at TEXT NOT NULL,
    country TEXT,
    event_name TEXT NOT NULL,
    category TEXT,
    importance TEXT,
    forecast REAL,
    previous_value REAL,
    actual_value REAL,
    revision REAL,
    surprise_value REAL,
    surprise_pct REAL,
    expected_impact TEXT,
    realized_gld_move_1m REAL,
    realized_gld_move_5m REAL,
    realized_gld_move_15m REAL,
    realized_gld_move_1h REAL,
    realized_gld_move_1d REAL,
    realized_spread_widening REAL,
    avoid_trading INTEGER DEFAULT 0,
    source TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_economic_events_scheduled_at ON economic_events(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_economic_events_category ON economic_events(category);

CREATE TABLE IF NOT EXISTS macro_series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id TEXT NOT NULL,
    observation_date TEXT NOT NULL,
    value REAL,
    realtime_start TEXT,
    realtime_end TEXT,
    units TEXT,
    title TEXT,
    source TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(series_id, observation_date, realtime_start, realtime_end)
);

CREATE INDEX IF NOT EXISTS idx_macro_series_id_date ON macro_series(series_id, observation_date);

CREATE TABLE IF NOT EXISTS market_calendar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    calendar_date TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'US_EQUITY',
    is_open INTEGER NOT NULL,
    open_time TEXT,
    close_time TEXT,
    session_type TEXT,
    source TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(calendar_date, market)
);

CREATE INDEX IF NOT EXISTS idx_market_calendar_date ON market_calendar(calendar_date);

CREATE TABLE IF NOT EXISTS microstructure_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    bid_price REAL,
    ask_price REAL,
    bid_size REAL,
    ask_size REAL,
    midpoint REAL,
    spread REAL,
    spread_pct REAL,
    spread_regime TEXT,
    quote_imbalance REAL,
    quote_age_seconds REAL,
    trade_intensity REAL,
    signed_volume REAL,
    aggressive_buy_volume REAL,
    aggressive_sell_volume REAL,
    liquidity_score REAL,
    volatility_burst INTEGER DEFAULT 0,
    stale_data INTEGER DEFAULT 0,
    source TEXT,
    feature_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_microstructure_symbol_timestamp ON microstructure_features(symbol, timestamp);

CREATE TABLE IF NOT EXISTS data_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    symbol TEXT,
    data_type TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    gap_seconds REAL,
    severity TEXT,
    reason TEXT,
    source TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_data_gaps_detected_at ON data_gaps(detected_at);

CREATE TABLE IF NOT EXISTS stream_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    websocket_connected INTEGER,
    stream_stale INTEGER,
    last_message_age_seconds REAL,
    last_bar_age_seconds REAL,
    last_quote_age_seconds REAL,
    last_trade_age_seconds REAL,
    bar_count INTEGER,
    quote_count INTEGER,
    trade_count INTEGER,
    stale_reason TEXT,
    last_error TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_stream_diagnostics_timestamp ON stream_diagnostics(timestamp);

CREATE TABLE IF NOT EXISTS price_action_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    pattern_classification TEXT,
    pattern_quality REAL,
    buildup_detected INTEGER DEFAULT 0,
    buildup_side TEXT,
    proper_break INTEGER DEFAULT 0,
    false_break INTEGER DEFAULT 0,
    tease_break INTEGER DEFAULT 0,
    pullback INTEGER DEFAULT 0,
    support_level REAL,
    resistance_level REAL,
    range_compression INTEGER DEFAULT 0,
    compression_duration INTEGER,
    breakout_volume_confirmation INTEGER DEFAULT 0,
    vwap_rejection INTEGER DEFAULT 0,
    trend_continuation INTEGER DEFAULT 0,
    trend_exhaustion INTEGER DEFAULT 0,
    candle_body_strength REAL,
    wick_rejection TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timeframe, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_price_action_labels_symbol_timestamp ON price_action_labels(symbol, timestamp);

CREATE TABLE IF NOT EXISTS order_block_zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    zone_low REAL NOT NULL,
    zone_high REAL NOT NULL,
    origin_timestamp TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    strength REAL,
    displacement_pct REAL,
    displacement_atr REAL,
    volume_ratio REAL,
    break_of_structure INTEGER DEFAULT 0,
    fair_value_gap INTEGER DEFAULT 0,
    retest_count INTEGER DEFAULT 0,
    mitigated INTEGER DEFAULT 0,
    invalidated INTEGER DEFAULT 0,
    age_bars INTEGER,
    source TEXT,
    features_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timeframe, direction, origin_timestamp)
);

CREATE INDEX IF NOT EXISTS idx_order_blocks_symbol_timeframe ON order_block_zones(symbol, timeframe, confirmed_at);
CREATE INDEX IF NOT EXISTS idx_order_blocks_active ON order_block_zones(symbol, invalidated, direction);

CREATE TABLE IF NOT EXISTS option_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    underlying_symbol TEXT NOT NULL,
    underlying_price REAL,
    contract_symbol TEXT NOT NULL,
    option_type TEXT NOT NULL,
    expiration_date TEXT NOT NULL,
    strike_price REAL NOT NULL,
    days_to_expiration INTEGER,
    bid_price REAL,
    ask_price REAL,
    bid_size REAL,
    ask_size REAL,
    midpoint REAL,
    spread_pct REAL,
    last_trade_price REAL,
    last_trade_size REAL,
    last_trade_timestamp TEXT,
    implied_volatility REAL,
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    quote_age_seconds REAL,
    feed TEXT,
    source TEXT,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(contract_symbol, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_option_snapshots_underlying_timestamp ON option_snapshots(underlying_symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_option_snapshots_contract_timestamp ON option_snapshots(contract_symbol, timestamp);

CREATE TABLE IF NOT EXISTS options_intelligence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    underlying_symbol TEXT NOT NULL,
    underlying_price REAL,
    contracts_analyzed INTEGER,
    options_bias TEXT,
    confidence REAL,
    call_put_activity_ratio REAL,
    call_aggressive_flow REAL,
    put_aggressive_flow REAL,
    atm_iv REAL,
    put_call_iv_skew REAL,
    expected_move_pct REAL,
    options_liquidity_score REAL,
    options_event_risk REAL,
    score_adjustment REAL,
    stale INTEGER DEFAULT 0,
    reason TEXT,
    feed TEXT,
    source TEXT,
    features_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_options_intelligence_timestamp ON options_intelligence(underlying_symbol, timestamp);

CREATE TABLE IF NOT EXISTS outcome_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    journal_id INTEGER,
    decision_source TEXT NOT NULL DEFAULT 'signal',
    decision_id INTEGER,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision TEXT,
    label TEXT,
    label_1m TEXT,
    label_3m TEXT,
    label_5m TEXT,
    label_15m TEXT,
    entry_price REAL,
    price_source TEXT,
    forward_return_1m REAL,
    forward_return_3m REAL,
    forward_return_5m REAL,
    forward_return_15m REAL,
    forward_return_30m REAL,
    forward_return_1h REAL,
    max_favorable_excursion REAL,
    max_adverse_excursion REAL,
    stop_would_hit INTEGER DEFAULT 0,
    target_would_hit INTEGER DEFAULT 0,
    false_break INTEGER DEFAULT 0,
    proper_break INTEGER DEFAULT 0,
    missed_opportunity INTEGER DEFAULT 0,
    best_exit_minutes INTEGER,
    worst_drawdown_before_profit REAL,
    realized_spread_cost REAL,
    net_outcome_after_costs REAL,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE SET NULL,
    FOREIGN KEY(journal_id) REFERENCES trading_journal(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_outcome_labels_symbol_timestamp ON outcome_labels(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_outcome_labels_label ON outcome_labels(label);
CREATE INDEX IF NOT EXISTS idx_outcome_labels_signal_id ON outcome_labels(signal_id);

CREATE TABLE IF NOT EXISTS playbook_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    playbook TEXT NOT NULL,
    direction TEXT,
    score REAL,
    allowed INTEGER,
    block_reason TEXT,
    expected_hold_minutes INTEGER,
    target_r_multiple REAL,
    feature_snapshot_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_playbook_evaluations_symbol_timestamp ON playbook_evaluations(symbol, timestamp);

CREATE TABLE IF NOT EXISTS model_promotion_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    model_version TEXT,
    candidate_metrics_json TEXT,
    benchmark_metrics_json TEXT,
    walk_forward_json TEXT,
    promoted INTEGER DEFAULT 0,
    promotion_reason TEXT,
    minimum_rules_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_model_promotion_audits_timestamp ON model_promotion_audits(timestamp);

CREATE TABLE IF NOT EXISTS knowledge_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    title TEXT,
    artifact_type TEXT,
    sha256 TEXT,
    summary TEXT,
    tags_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    indexed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_knowledge_artifacts_sha256 ON knowledge_artifacts(sha256);

CREATE TABLE IF NOT EXISTS data_source_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    strategy_path TEXT NOT NULL DEFAULT 'minute',
    playbook TEXT,
    data_type TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    status TEXT,
    rows_inserted INTEGER DEFAULT 0,
    message TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_data_source_runs_timestamp ON data_source_runs(timestamp);

CREATE TABLE IF NOT EXISTS llm_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    review_type TEXT NOT NULL,
    summary TEXT,
    bull_case TEXT,
    bear_case TEXT,
    risk_critique TEXT,
    execution_critique TEXT,
    journal_review TEXT,
    recommendations_json TEXT,
    evidence_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_llm_reviews_timestamp_type ON llm_reviews(timestamp, review_type);

CREATE TABLE IF NOT EXISTS agent_advisories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL DEFAULT 'GLD',
    horizon TEXT NOT NULL DEFAULT 'hourly',
    bias TEXT NOT NULL DEFAULT 'neutral',
    confidence REAL NOT NULL DEFAULT 0,
    abstain INTEGER NOT NULL DEFAULT 1,
    event_risk REAL NOT NULL DEFAULT 0,
    size_multiplier REAL NOT NULL DEFAULT 1,
    summary TEXT,
    bull_case TEXT,
    bear_case TEXT,
    risk_flags_json TEXT,
    evidence_ids_json TEXT,
    provider TEXT,
    model TEXT,
    source_fingerprint TEXT,
    expires_at TEXT NOT NULL,
    advisory_only INTEGER NOT NULL DEFAULT 1,
    raw_response_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (bias IN ('bullish', 'bearish', 'neutral', 'event_risk')),
    CHECK (confidence >= 0 AND confidence <= 1),
    CHECK (event_risk >= 0 AND event_risk <= 1),
    CHECK (advisory_only = 1)
);

CREATE INDEX IF NOT EXISTS idx_agent_advisories_symbol_timestamp
ON agent_advisories(symbol, timestamp DESC);

CREATE TABLE IF NOT EXISTS llm_training_advice (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    summary TEXT,
    feature_recommendations_json TEXT,
    labeling_recommendations_json TEXT,
    training_actions_json TEXT,
    risk_warnings_json TEXT,
    candidate_notes_json TEXT,
    raw_response_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_llm_training_advice_timestamp ON llm_training_advice(timestamp);

CREATE TABLE IF NOT EXISTS llm_signal_labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER UNIQUE,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    suggested_label TEXT NOT NULL,
    confidence REAL,
    rationale TEXT,
    provider TEXT,
    model TEXT,
    raw_response_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY(signal_id) REFERENCES signals(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_signal_labels_timestamp ON llm_signal_labels(timestamp);
CREATE INDEX IF NOT EXISTS idx_llm_signal_labels_label ON llm_signal_labels(suggested_label);

CREATE TABLE IF NOT EXISTS rl_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    experiment_name TEXT NOT NULL,
    policy_name TEXT,
    train_start TEXT,
    train_end TEXT,
    test_start TEXT,
    test_end TEXT,
    total_reward REAL,
    total_trades INTEGER,
    win_rate REAL,
    max_drawdown REAL,
    benchmark_reward REAL,
    promoted INTEGER DEFAULT 0,
    promotion_reason TEXT,
    metrics_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_rl_experiments_timestamp ON rl_experiments(timestamp);

CREATE TABLE IF NOT EXISTS model_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    model_version TEXT,
    model_scope TEXT,
    predicted_direction TEXT,
    probability_long REAL,
    probability_short REAL,
    probability_no_trade REAL,
    expected_return REAL,
    confidence REAL,
    feature_snapshot_json TEXT,
    actual_outcome_when_known TEXT
);

CREATE INDEX IF NOT EXISTS idx_model_predictions_symbol_timestamp ON model_predictions(symbol, timestamp);

CREATE TABLE IF NOT EXISTS transformer_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    model_version TEXT,
    model_scope TEXT NOT NULL,
    model_role TEXT NOT NULL DEFAULT 'paper_shadow',
    predicted_direction TEXT,
    probability_long REAL,
    probability_short REAL,
    probability_no_trade REAL,
    expected_return_1m REAL,
    expected_return_3m REAL,
    expected_return_5m REAL,
    expected_return_15m REAL,
    expected_cost REAL,
    uncertainty REAL,
    inference_latency_ms REAL,
    cache_age_seconds REAL,
    status TEXT NOT NULL,
    fallback_model_version TEXT,
    feature_snapshot_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_transformer_predictions_scope_timestamp
ON transformer_predictions(model_scope, timestamp);

CREATE TABLE IF NOT EXISTS transformer_training_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_key TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL,
    historical_fingerprint TEXT NOT NULL,
    paper_fingerprint TEXT,
    combined_fingerprint TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    status TEXT NOT NULL,
    candidate_version TEXT,
    parent_version TEXT,
    score REAL,
    meaningful_improvement INTEGER DEFAULT 0,
    metrics_json TEXT,
    error_message TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_transformer_training_experiments_scope
ON transformer_training_experiments(scope, status, completed_at);

CREATE TABLE IF NOT EXISTS fair_value_gaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gap_key TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    zone_low REAL NOT NULL,
    zone_high REAL NOT NULL,
    midpoint REAL NOT NULL,
    status TEXT NOT NULL,
    fill_fraction REAL DEFAULT 0,
    age_bars INTEGER DEFAULT 0,
    invalidated INTEGER DEFAULT 0,
    last_touched_at TEXT,
    feature_snapshot_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fair_value_gaps_symbol_time
ON fair_value_gaps(symbol, timeframe, detected_at);

CREATE TABLE IF NOT EXISTS model_drift_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    model_version TEXT,
    sample_count INTEGER,
    drift_score REAL,
    missing_feature_fraction REAL,
    outlier_feature_fraction REAL,
    status TEXT,
    paper_outcome_count INTEGER DEFAULT 0,
    paper_profit_factor REAL,
    paper_drawdown REAL,
    champion_demoted INTEGER DEFAULT 0,
    demotion_reason TEXT,
    feature_details_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_model_drift_reports_timestamp ON model_drift_reports(timestamp);

CREATE TABLE IF NOT EXISTS ml_training_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_key TEXT NOT NULL UNIQUE,
    dataset_fingerprint TEXT NOT NULL,
    artifact_path TEXT,
    playbook TEXT NOT NULL,
    horizon_minutes INTEGER NOT NULL,
    status TEXT NOT NULL,
    sample_count INTEGER DEFAULT 0,
    paper_sample_count INTEGER DEFAULT 0,
    candidate_version TEXT,
    candidate_path TEXT,
    metrics_json TEXT,
    manifest_path TEXT,
    started_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_ml_training_experiments_status ON ml_training_experiments(status, completed_at);
CREATE INDEX IF NOT EXISTS idx_ml_training_experiments_playbook ON ml_training_experiments(playbook, horizon_minutes);

CREATE TABLE IF NOT EXISTS fast_scalp_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision TEXT NOT NULL,
    trigger_type TEXT,
    allowed INTEGER DEFAULT 0,
    confidence REAL,
    score REAL,
    reason TEXT,
    latency_ms REAL,
    spread_pct REAL,
    quote_imbalance REAL,
    trade_intensity REAL,
    liquidity_score REAL,
    volatility_burst INTEGER DEFAULT 0,
    model_version TEXT,
    model_prediction TEXT,
    probability_long REAL,
    probability_short REAL,
    probability_no_trade REAL,
    client_order_id TEXT,
    status TEXT,
    feature_snapshot_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fast_scalp_symbol_timestamp ON fast_scalp_decisions(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_fast_scalp_trigger ON fast_scalp_decisions(trigger_type, decision);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    bid_price REAL,
    ask_price REAL,
    midpoint REAL,
    last_trade_price REAL,
    spread REAL,
    spread_pct REAL,
    quote_age_seconds REAL,
    trade_age_seconds REAL,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(symbol, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_price_snapshots_symbol_timestamp ON price_snapshots(symbol, timestamp);

CREATE TABLE IF NOT EXISTS position_management_state (
    trade_id TEXT PRIMARY KEY,
    parent_order_id TEXT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'single',
    qty REAL NOT NULL,
    entry_time TEXT,
    entry_price REAL NOT NULL,
    stop_order_id TEXT,
    take_profit_order_id TEXT,
    initial_stop_price REAL,
    current_stop_price REAL,
    take_profit_price REAL,
    high_water_price REAL,
    low_water_price REAL,
    max_favorable_excursion REAL DEFAULT 0,
    max_adverse_excursion REAL DEFAULT 0,
    breakeven_armed INTEGER DEFAULT 0,
    trailing_armed INTEGER DEFAULT 0,
    exit_requested INTEGER DEFAULT 0,
    last_action TEXT,
    last_action_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    details_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_position_management_state_status ON position_management_state(status, symbol);

CREATE TABLE IF NOT EXISTS position_management_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    trade_id TEXT,
    parent_order_id TEXT,
    symbol TEXT NOT NULL,
    direction TEXT,
    role TEXT,
    action TEXT NOT NULL,
    reason TEXT,
    qty REAL,
    entry_price REAL,
    mark_price REAL,
    pnl REAL,
    pnl_pct REAL,
    old_stop_price REAL,
    new_stop_price REAL,
    high_water_price REAL,
    low_water_price REAL,
    max_favorable_excursion REAL,
    max_adverse_excursion REAL,
    status TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_position_management_events_trade_time
ON position_management_events(trade_id, timestamp);

CREATE TABLE IF NOT EXISTS execution_episodes (
    episode_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    source TEXT NOT NULL,
    strategy_path TEXT NOT NULL DEFAULT 'minute',
    playbook TEXT,
    status TEXT NOT NULL,
    planned_qty REAL NOT NULL,
    submitted_qty REAL NOT NULL DEFAULT 0,
    filled_qty REAL NOT NULL DEFAULT 0,
    remaining_qty REAL NOT NULL DEFAULT 0,
    entry_avg_price REAL,
    exit_avg_price REAL,
    realized_pnl REAL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    close_reason TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    details_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_episodes_status
ON execution_episodes(symbol, status, direction);

CREATE TABLE IF NOT EXISTS execution_episode_orders (
    order_key TEXT PRIMARY KEY,
    episode_id TEXT NOT NULL,
    alpaca_order_id TEXT,
    client_order_id TEXT,
    parent_order_id TEXT,
    role TEXT NOT NULL,
    intent_type TEXT NOT NULL,
    strategy_path TEXT,
    playbook TEXT,
    close_reason TEXT,
    side TEXT,
    qty REAL,
    filled_qty REAL NOT NULL DEFAULT 0,
    filled_avg_price REAL,
    status TEXT NOT NULL,
    submitted_at TEXT,
    updated_at TEXT NOT NULL,
    raw_json TEXT,
    FOREIGN KEY(episode_id) REFERENCES execution_episodes(episode_id)
);

CREATE INDEX IF NOT EXISTS idx_execution_episode_orders_episode
ON execution_episode_orders(episode_id, status);

CREATE TABLE IF NOT EXISTS order_intents (
    intent_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    intent_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    episode_id TEXT,
    order_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error TEXT,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_order_intents_status ON order_intents(status, created_at);

CREATE TABLE IF NOT EXISTS execution_safety_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    symbol TEXT NOT NULL,
    entry_frozen INTEGER NOT NULL DEFAULT 0,
    circuit_open INTEGER NOT NULL DEFAULT 0,
    broker_position_qty REAL,
    broker_position_direction TEXT,
    broker_open_order_count INTEGER,
    database_episode_count INTEGER,
    internal_episode_count INTEGER,
    reason TEXT,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS performance_consistency_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    stage TEXT NOT NULL,
    consistent INTEGER NOT NULL,
    unmatched_fill_count INTEGER NOT NULL DEFAULT 0,
    orphan_order_count INTEGER NOT NULL DEFAULT 0,
    open_episode_count INTEGER NOT NULL DEFAULT 0,
    broker_position_qty REAL NOT NULL DEFAULT 0,
    broker_open_order_count INTEGER NOT NULL DEFAULT 0,
    database_position_qty REAL NOT NULL DEFAULT 0,
    reasons_json TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_performance_consistency_timestamp
ON performance_consistency_audits(timestamp, stage);

CREATE INDEX IF NOT EXISTS idx_execution_safety_events_time
ON execution_safety_events(timestamp, event_type);

CREATE TABLE IF NOT EXISTS execution_latency_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    elapsed_ms REAL,
    stage_latency_ms REAL,
    event_age_ms REAL,
    strategy_path TEXT,
    playbook TEXT,
    client_order_id TEXT,
    order_id TEXT,
    status TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_execution_latency_trace ON execution_latency_events(trace_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_execution_latency_stage ON execution_latency_events(stage, timestamp);

CREATE TABLE IF NOT EXISTS model_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version TEXT NOT NULL UNIQUE,
    model_type TEXT,
    model_scope TEXT NOT NULL DEFAULT 'entry:all',
    path TEXT,
    created_at TEXT NOT NULL,
    training_start TEXT,
    training_end TEXT,
    feature_columns_json TEXT,
    feature_profile TEXT,
    model_parameters_json TEXT,
    thresholds_json TEXT,
    artifact_fingerprint TEXT,
    training_data_start TEXT,
    training_data_end TEXT,
    parent_champion_version TEXT,
    metrics_json TEXT,
    status TEXT CHECK(status IN ('champion', 'candidate', 'rejected', 'archived')),
    promoted_at TEXT,
    rejection_reason TEXT,
    demoted_at TEXT,
    demotion_reason TEXT
);

CREATE TABLE IF NOT EXISTS model_champion_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    model_scope TEXT NOT NULL,
    model_version TEXT NOT NULL,
    action TEXT NOT NULL,
    previous_champion_version TEXT,
    reason TEXT,
    metrics_json TEXT,
    artifact_fingerprint TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_model_champion_history_scope ON model_champion_history(model_scope, timestamp);

CREATE TABLE IF NOT EXISTS llm_offline_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    cadence TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    fingpt_source_fingerprint TEXT,
    news_linked_fraction REAL,
    status TEXT NOT NULL,
    outputs_json TEXT,
    error_message TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_llm_offline_cycles_time ON llm_offline_cycles(timestamp, cadence);

CREATE TABLE IF NOT EXISTS system_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    level TEXT NOT NULL,
    module TEXT,
    event_type TEXT,
    message TEXT,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_system_logs_timestamp_level ON system_logs(timestamp, level);
