from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_MODES = {"paper", "live"}


def _normalize_data_mode(value: str | None, *, alpaca_paper: bool = True) -> str:
    mode = (value or ("paper" if alpaca_paper else "live")).strip().lower()
    if mode not in DATA_MODES:
        raise ValueError("BOT_DATA_MODE must be either 'paper' or 'live'.")
    return mode


def _resolve_project_path(path: str | Path) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved
    return resolved


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value in {None, ""} else float(value)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value in {None, ""} else int(value)


def _int_list_env(name: str, default: Iterable[int]) -> list[int]:
    value = os.getenv(name)
    if value in {None, ""}:
        return [int(item) for item in default]
    return [int(item) for item in value.replace(",", " ").split() if item.strip()]


def parse_symbols(symbols: str | Iterable[str] | None) -> list[str]:
    if symbols is None:
        return ["GLD"]
    if isinstance(symbols, str):
        chunks = symbols.replace(",", " ").split()
    else:
        chunks = list(symbols)
    return [symbol.strip().upper() for symbol in chunks if symbol.strip()]


@dataclass(slots=True)
class Settings:
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    alpaca_paper_trade: bool = True
    alpaca_endpoint: str = "https://paper-api.alpaca.markets/v2"
    alpaca_data_feed: str = "iex"
    data_mode: str = "paper"
    database_url: str = "sqlite:///data/paper/gld_scalper.db"
    database_busy_timeout_ms: int = 30_000
    bot_symbol: str = "GLD"
    paper_account_size: float = 1_000_000.0
    min_trade_notional: float = 5_000.0
    max_trade_notional: float = 25_000.0
    max_daily_loss_pct: float = 0.01
    max_trade_risk_pct: float = 0.0025
    max_trades_per_day: int = 20
    max_consecutive_losses: int = 3
    max_holding_minutes: int = 15
    trade_timeframe: str = "1Min"
    startup_recovery_days: int = 10
    confirm_timeframe_1: str = "5Min"
    confirm_timeframe_2: str = "15Min"
    enable_ema_cross_strategy: bool = True
    ema_cross_timeframes: list[int] = field(default_factory=lambda: [1, 5, 15, 30, 45, 60])
    ema_cross_history_minutes: int = 3_900
    ema_cross_fast_period: int = 10
    ema_cross_slow_period: int = 20
    ema_cross_use_adx_filter: bool = True
    ema_cross_adx_period: int = 14
    ema_cross_adx_threshold: float = 20.0
    ema_cross_use_cooldown: bool = True
    ema_cross_cooldown_bars: int = 10
    ema_cross_atr_period: int = 14
    ema_cross_stop_loss_atr_multiple: float = 1.5
    ema_cross_take_profit_atr_multiple: float = 3.0
    ema_cross_paper_signal_authority: bool = True
    enable_shorts: bool = True
    enable_extended_hours: bool = False
    enable_live_stream: bool = True
    stream_startup_grace_seconds: int = 20
    bar_stale_seconds: int = 180
    quote_stale_seconds: int = 30
    enable_fast_scalp: bool = True
    enable_fast_scalp_order_submission: bool = True
    fast_scalp_interval_ms: int = 250
    fast_scalp_event_queue_size: int = 5_000
    fast_scalp_min_quote_count: int = 3
    fast_scalp_min_trade_count: int = 1
    fast_scalp_lookback_seconds: int = 5
    fast_scalp_false_break_window_seconds: int = 3
    fast_scalp_order_cooldown_seconds: int = 30
    fast_scalp_no_trade_log_interval_seconds: int = 10
    fast_scalp_tight_spread_pct: float = 0.00035
    fast_scalp_breakout_min_move_pct: float = 0.00025
    fast_scalp_imbalance_threshold: float = 0.30
    fast_scalp_min_trade_intensity: float = 0.40
    fast_scalp_min_confidence: float = 0.62
    fast_scalp_volatility_burst_pct: float = 0.0010
    fast_scalp_max_quote_age_seconds: float = 2.0
    fast_scalp_max_trade_age_seconds: float = 3.0
    fast_scalp_min_spread_stability: float = 0.55
    minute_entry_max_quote_age_seconds: float = 15.0
    minute_entry_max_trade_age_seconds: float = 30.0
    minute_entry_min_trade_intensity: float = 0.05
    minute_entry_min_spread_stability: float = 0.35
    enable_order_blocks: bool = True
    order_block_timeframes: list[int] = field(default_factory=lambda: [1, 5, 15, 30, 45, 60])
    order_block_history_minutes: int = 3_900
    order_block_lookback_bars: int = 80
    order_block_displacement_atr: float = 1.20
    order_block_min_volume_ratio: float = 1.05
    order_block_max_age_bars: int = 120
    order_block_retest_tolerance_pct: float = 0.0005
    enable_options_intelligence: bool = True
    options_underlying: str = "GLD"
    options_feed: str = "indicative"
    options_poll_interval_seconds: int = 30
    options_expiration_days: int = 30
    options_strike_window_pct: float = 0.05
    options_max_contracts: int = 80
    options_max_quote_age_seconds: int = 180
    options_max_spread_pct: float = 0.30
    options_max_score_adjustment: float = 3.0
    enable_latency_aware_ml: bool = True
    ml_max_inference_latency_ms: float = 5.0
    ml_validation_fraction: float = 0.20
    ml_purge_minutes: int = 30
    ml_embargo_minutes: int = 30
    ml_min_confidence: float = 0.58
    ml_min_probability_margin: float = 0.08
    ml_max_missing_feature_fraction: float = 0.25
    ml_max_outlier_feature_fraction: float = 0.15
    enable_transformer_shadow: bool = True
    transformer_trading_mode: str = "shadow"
    transformer_queue_size: int = 64
    transformer_cache_max_age_seconds: float = 5.0
    transformer_model_refresh_seconds: int = 60
    transformer_torch_threads: int = 2
    transformer_bounded_max_score_adjustment: float = 3.0
    transformer_adviser_min_confidence: float = 0.65
    transformer_adviser_max_uncertainty: float = 0.60
    transformer_champion_min_expected_edge_pct: float = 0.00015
    transformer_auto_promotion: bool = True
    transformer_auto_demotion: bool = True
    transformer_demotion_min_paper_trades: int = 50
    transformer_demotion_profit_factor: float = 0.85
    transformer_training_patience_rounds: int = 3
    transformer_training_minimum_improvement: float = 0.001
    transformer_training_interval_minutes: int = 60
    continual_training_horizons: list[int] = field(default_factory=lambda: [1, 3, 5, 15])
    continual_training_playbooks: list[str] = field(
        default_factory=lambda: [
            "all",
            "fast_microstructure",
            "minute_setups",
            "proper_breakout",
            "false_break_reversal",
            "pullback_continuation",
            "compression_breakout",
            "spread_capture",
            "news_event",
            "trend_continuation",
            "ema_cross_filtered",
        ]
    )
    continual_training_interval_minutes: int = 60
    continual_training_min_new_labels: int = 500
    continual_training_min_playbook_samples: int = 750
    continual_training_paper_weight: int = 2
    continual_training_paper_lookback_days: int = 730
    continual_training_lock_stale_hours: int = 24
    continual_training_only_outside_regular_hours: bool = True
    enable_scheduled_retraining: bool = True
    retrain_interval_hours: int = 24
    retrain_lookback_days: int = 90
    retrain_min_samples: int = 50
    retrain_min_clean_episodes: int = 10
    retrain_only_outside_regular_hours: bool = True
    enable_missed_opportunity_learning: bool = True
    missed_opportunity_horizon_minutes: int = 15
    missed_opportunity_min_move_pct: float = 0.002
    missed_opportunity_max_adverse_pct: float = 0.0012
    enable_multi_horizon_outcome_labels: bool = True
    outcome_label_batch_size: int = 5_000
    outcome_label_min_edge_pct: float = 0.0002
    outcome_label_slippage_pct: float = 0.0001
    outcome_label_snapshot_tolerance_seconds: int = 5
    outcome_label_max_bar_gap_minutes: int = 2
    enable_paper_exploration: bool = True
    paper_exploration_max_trades_per_day: int = 2
    paper_exploration_cooldown_minutes: int = 60
    paper_exploration_max_notional: float = 1_000.0
    paper_exploration_min_score: float = 70.0
    paper_exploration_min_score_gap: float = 20.0
    paper_exploration_max_no_trade_score: float = 65.0
    paper_exploration_min_liquidity_score: float = 0.70
    paper_exploration_max_spread_pct: float = 0.0008
    paper_learning_mode: bool = False
    paper_learning_fast_min_score: float = 55.0
    paper_learning_min_score: float = 60.0
    paper_learning_min_score_gap: float = 5.0
    paper_learning_max_no_trade_score: float = 100.0
    paper_learning_min_playbook_score: float = 60.0
    paper_learning_min_pattern_quality: float = 0.45
    paper_learning_min_liquidity_score: float = 0.40
    paper_learning_max_spread_pct: float = 0.0015
    paper_learning_exploration_max_notional: float = 2_000.0
    paper_learning_fast_order_cooldown_seconds: int = 5
    paper_learning_stop_loss_pct: float = 0.0008
    paper_learning_take_profit_pct: float = 0.0010
    paper_learning_ignore_model_rejection: bool = True
    paper_learning_max_concurrent_trades: int = 5
    paper_learning_max_aggregate_notional: float = 5_000.0
    paper_learning_max_spread_to_stop_ratio: float = 0.65
    paper_learning_exploration_sample_rate: float = 0.25
    paper_learning_max_exploration_trades_per_day: int = 0
    paper_learning_exploration_cooldown_seconds: int = 5
    post_loss_cooldown_seconds: int = 120
    regime_loss_lookback_minutes: int = 60
    regime_loss_threshold: int = 3
    regime_loss_cooldown_minutes: int = 30
    performance_snapshot_interval_seconds: int = 60
    estimated_fee_per_share: float = 0.001
    estimated_minimum_order_fee: float = 0.0
    estimated_round_trip_slippage_pct: float = 0.00010
    economic_breakeven_safety_buffer_pct: float = 0.00010
    enable_dynamic_position_management: bool = True
    position_manager_interval_ms: int = 250
    position_manager_broker_refresh_seconds: int = 2
    price_snapshot_interval_seconds: int = 1
    position_emergency_stop_pct: float = 0.0030
    position_breakeven_trigger_pct: float = 0.00045
    position_breakeven_offset_pct: float = 0.00005
    position_trailing_trigger_pct: float = 0.00070
    position_trailing_distance_pct: float = 0.00035
    position_max_normal_stop_pct: float = 0.00200
    position_min_reward_risk: float = 1.15
    position_structure_buffer_atr: float = 0.20
    position_profit_giveback_min_mfe_pct: float = 0.00080
    position_max_profit_giveback_fraction: float = 0.50
    position_min_stop_improvement: float = 0.02
    position_stop_replace_cooldown_seconds: int = 2
    position_profitable_time_exit_buffer_pct: float = 0.00010
    position_invalidation_min_loss_pct: float = 0.00025
    position_invalidation_min_confidence: float = 0.80
    position_invalidation_required_votes: int = 2
    position_close_management_minutes_before_close: int = 30
    position_close_risk_reduction_minutes_before_close: int = 15
    position_force_flatten_minutes_before_close: int = 8
    exit_model_min_trustworthy_outcomes: int = 500
    execution_reconcile_interval_seconds: int = 3
    execution_bracket_grace_period_seconds: int = 15
    execution_residual_confirmation_delay_seconds: int = 2
    execution_entry_freeze_minutes_before_close: int = 15
    execution_session_flatten_minutes_before_close: int = 10
    execution_intent_timeout_seconds: int = 30
    execution_order_state_timeout_seconds: int = 30
    execution_cancel_wait_seconds: int = 15
    execution_shutdown_timeout_seconds: int = 90
    execution_direction_switch_cooldown_seconds: int = 3
    execution_enable_direction_switch: bool = True
    execution_flatten_residual_positions: bool = True
    execution_flatten_on_shutdown: bool = True
    execution_broker_rejection_threshold: int = 3
    execution_stream_failure_threshold: int = 3
    execution_reconciliation_failure_threshold: int = 3
    execution_order_state_failure_threshold: int = 3
    paper_max_session_loss_pct: float = 0.005
    paper_max_drawdown_pct: float = 0.0075
    paper_max_consecutive_losses: int = 6
    paper_max_trades_per_day: int = 250
    max_orders_per_minute: int = 6
    paper_max_orders_per_minute: int = 12
    risk_execution_error_limit: int = 3
    max_correlated_exposure_pct: float = 0.10
    experimental_profit_factor_threshold: float = 1.15
    experimental_size_multiplier: float = 0.25
    enable_partial_profit_tranches: bool = True
    partial_profit_fraction: float = 0.50
    partial_profit_runner_target_multiplier: float = 2.0
    paper_require_ml_model: bool = False
    paper_enable_shadow_model: bool = True
    paper_ml_min_advisory_confidence: float = 0.45
    paper_ml_max_score_adjustment: float = 5.0
    enable_macro_context: bool = True
    macro_context_interval_minutes: int = 60
    macro_context_max_age_minutes: int = 1440
    macro_context_headlines_path: str = "data/macro_headlines.csv"
    macro_context_max_live_score_adjustment: float = 3.0
    enable_news_collection: bool = True
    news_collection_interval_minutes: int = 60
    news_symbols: list[str] = field(default_factory=lambda: ["GLD", "IAU", "SLV", "GDX", "GDXJ", "UUP", "TLT", "IEF", "SHY", "SPY", "QQQ"])
    enable_macro_series_collection: bool = True
    macro_series_interval_hours: int = 24
    fred_api_key: str = ""
    macro_series_ids: list[str] = field(default_factory=lambda: ["DGS2", "DGS10", "DFII10", "T10YIE", "DFF", "FEDFUNDS", "CPIAUCSL", "PCEPI", "PAYEMS", "UNRATE", "DCOILWTICO"])
    enable_event_calendar: bool = True
    economic_calendar_path: str = "data/economic_calendar.csv"
    event_risk_lookahead_minutes: int = 45
    event_risk_cooldown_minutes: int = 15
    enable_market_calendar_collection: bool = True
    enable_feature_audit_tables: bool = True
    enable_strict_playbooks: bool = True
    minimum_pattern_quality: float = 0.58
    minimum_liquidity_score: float = 0.55
    minimum_playbook_score: float = 70.0
    require_proper_break_for_breakout: bool = True
    avoid_midday_chop: bool = True
    avoid_event_risk_trading: bool = True
    llm_provider: str = "none"
    llm_base_url: str = "http://localhost:11434"
    llm_model: str = "llama3.2:1b"
    llm_timeout_seconds: int = 60
    llm_temperature: float = 0.1
    llm_max_context_rows: int = 80
    enable_llm_analysis: bool = False
    enable_llm_macro_context: bool = False
    enable_llm_review_coach: bool = False
    enable_llm_training_advice: bool = False
    enable_llm_training_labels: bool = False
    llm_training_label_min_confidence: float = 0.70
    enable_llm_live_trading: bool = False
    llm_offline_only: bool = True
    fingpt_source_dir: str = "FINGPT/FinGPT-1.0.0/fingpt"
    llm_context_max_sizing_adjustment: float = 0.05
    llm_news_min_linked_fraction: float = 0.60
    enable_tradingagents_advisory: bool = True
    tradingagents_source_dir: str = "../TradingAgents"
    tradingagents_advisory_max_age_minutes: int = 120
    tradingagents_min_confidence: float = 0.60
    tradingagents_max_score_adjustment: float = 3.0
    tradingagents_max_sizing_adjustment: float = 0.05
    walk_forward_train_months: int = 12
    walk_forward_test_months: int = 3
    promotion_min_profit_factor: float = 1.20
    promotion_min_win_rate: float = 0.48
    promotion_max_drawdown: float = 0.015
    promotion_min_trade_count: int = 100
    promotion_min_profitable_fold_ratio: float = 0.60
    promotion_max_calibration_error: float = 0.15
    promotion_min_fold_count: int = 3
    promotion_require_paper_results: bool = True
    promotion_min_paper_trade_count: int = 30
    promotion_min_regime_count: int = 2
    drift_min_predictions: int = 100
    drift_min_paper_outcomes: int = 30
    drift_demotion_score: float = 0.35
    drift_profit_factor_floor_ratio: float = 0.70
    drift_drawdown_limit_multiplier: float = 1.50
    retrain_after_hour_et: int = 20
    retrain_before_hour_et: int = 8
    enable_hourly_csv_export: bool = True
    csv_export_interval_minutes: int = 60
    csv_export_dir: str = "exports/paper/hourly"
    log_level: str = "INFO"
    related_symbols: list[str] = field(default_factory=lambda: ["IAU", "SLV", "GDX", "GDXJ", "UUP", "TLT", "IEF", "SHY", "SPY", "QQQ", "VIXY"])
    bullish_threshold: float = 75.0
    bearish_threshold: float = 75.0
    opposing_score_max: float = 40.0
    no_trade_score_max: float = 60.0
    extreme_rule_score_without_model: float = 88.0
    max_spread_pct: float = 0.0015
    stale_data_seconds: int = 120
    order_fill_timeout_seconds: int = 30
    stop_loss_pct_floor: float = 0.0015
    take_profit_pct_min: float = 0.0020
    take_profit_pct_max: float = 0.0040
    strategy_version: str = "rules-v1"

    @property
    def data_root(self) -> Path:
        return PROJECT_ROOT / "data" / self.data_mode

    @property
    def export_root(self) -> Path:
        return PROJECT_ROOT / "exports" / self.data_mode

    @property
    def all_symbols(self) -> list[str]:
        symbols = [self.bot_symbol.upper(), *self.related_symbols]
        return list(dict.fromkeys(symbols))

    @property
    def database_path(self) -> Path:
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("Only sqlite:/// DATABASE_URL values are supported by this project.")
        raw_path = self.database_url.replace("sqlite:///", "", 1)
        path = Path(raw_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @property
    def alpaca_trading_base_url(self) -> str:
        endpoint = self.alpaca_endpoint.rstrip("/")
        if endpoint.endswith("/v2"):
            endpoint = endpoint[: -len("/v2")]
        return endpoint

    def validate_safety(self) -> None:
        if self.data_mode not in DATA_MODES:
            raise RuntimeError("BOT_DATA_MODE must be either paper or live.")
        expected_data_mode = "paper" if self.alpaca_paper else "live"
        if self.data_mode != expected_data_mode:
            raise RuntimeError(
                f"BOT_DATA_MODE={self.data_mode!r} does not match Alpaca mode {expected_data_mode!r}."
            )
        database_path = self.database_path
        if _is_under(database_path, PROJECT_ROOT) and not _is_under(database_path, self.data_root):
            raise RuntimeError(f"DATABASE_URL must point inside data/{self.data_mode}/ for clear paper/live separation.")
        csv_export_path = _resolve_project_path(self.csv_export_dir)
        if _is_under(csv_export_path, PROJECT_ROOT) and not _is_under(csv_export_path, self.export_root):
            raise RuntimeError(f"CSV_EXPORT_DIR must point inside exports/{self.data_mode}/ for clear paper/live separation.")
        if not self.alpaca_paper:
            raise RuntimeError("ALPACA_PAPER must be true. This bot is paper-trading only.")
        if not self.alpaca_paper_trade:
            raise RuntimeError("ALPACA_PAPER_TRADE must be true. This bot is paper-trading only.")
        if "paper-api.alpaca.markets" not in self.alpaca_trading_base_url:
            raise RuntimeError("ALPACA_ENDPOINT must point to Alpaca's paper API endpoint.")
        if self.bot_symbol.upper() != "GLD":
            raise RuntimeError("BOT_SYMBOL must be GLD for trading. Related symbols are collection-only.")
        if self.min_trade_notional <= 0 or self.max_trade_notional < self.min_trade_notional:
            raise RuntimeError("Trade notional settings are invalid.")
        if not 0.05 <= self.ml_validation_fraction <= 0.50:
            raise RuntimeError("ML_VALIDATION_FRACTION must be between 0.05 and 0.50.")
        if not 0.0 <= self.ml_min_probability_margin <= 1.0:
            raise RuntimeError("ML_MIN_PROBABILITY_MARGIN must be between 0 and 1.")
        if not 0.0 <= self.ml_max_missing_feature_fraction <= 1.0:
            raise RuntimeError("ML_MAX_MISSING_FEATURE_FRACTION must be between 0 and 1.")
        if not self.ema_cross_timeframes or any(value <= 0 for value in self.ema_cross_timeframes):
            raise RuntimeError("EMA_CROSS_TIMEFRAMES must contain positive minute values.")
        if self.ema_cross_history_minutes < max(self.ema_cross_timeframes) * max(
            self.ema_cross_slow_period,
            self.ema_cross_adx_period * 2,
            self.ema_cross_atr_period,
        ):
            raise RuntimeError("EMA_CROSS_HISTORY_MINUTES is too short for the configured timeframes and indicators.")
        if not 1 <= self.ema_cross_fast_period < self.ema_cross_slow_period:
            raise RuntimeError("EMA cross periods require 1 <= fast period < slow period.")
        if self.ema_cross_adx_period < 2 or self.ema_cross_adx_threshold < 0:
            raise RuntimeError("EMA cross ADX settings are invalid.")
        if self.ema_cross_cooldown_bars < 0 or self.ema_cross_atr_period < 2:
            raise RuntimeError("EMA cross cooldown and ATR settings are invalid.")
        if not (
            0 < self.ema_cross_stop_loss_atr_multiple
            < self.ema_cross_take_profit_atr_multiple
        ):
            raise RuntimeError("EMA cross ATR target multiple must exceed the stop multiple.")
        if self.ema_cross_paper_signal_authority and not self.alpaca_paper:
            raise RuntimeError("EMA cross paper authority cannot be enabled outside Alpaca paper mode.")
        if self.transformer_queue_size < 1:
            raise RuntimeError("TRANSFORMER_QUEUE_SIZE must be positive.")
        if self.transformer_trading_mode not in {"shadow", "bounded_adviser", "paper_champion"}:
            raise RuntimeError("TRANSFORMER_TRADING_MODE must be shadow, bounded_adviser, or paper_champion.")
        if self.transformer_trading_mode == "paper_champion" and not self.alpaca_paper:
            raise RuntimeError("paper_champion Transformer authority is forbidden outside Alpaca paper mode.")
        if self.transformer_cache_max_age_seconds <= 0:
            raise RuntimeError("TRANSFORMER_CACHE_MAX_AGE_SECONDS must be positive.")
        if self.transformer_model_refresh_seconds < 5:
            raise RuntimeError("TRANSFORMER_MODEL_REFRESH_SECONDS must be at least 5.")
        if not 1 <= self.transformer_torch_threads <= 4:
            raise RuntimeError("TRANSFORMER_TORCH_THREADS must be between 1 and 4 on the laptop profile.")
        if not 0.0 <= self.transformer_bounded_max_score_adjustment <= 5.0:
            raise RuntimeError("TRANSFORMER_BOUNDED_MAX_SCORE_ADJUSTMENT must be between zero and five.")
        if not 0.0 <= self.transformer_adviser_min_confidence <= 1.0:
            raise RuntimeError("TRANSFORMER_ADVISER_MIN_CONFIDENCE must be between zero and one.")
        if not 0.0 <= self.transformer_adviser_max_uncertainty <= 1.0:
            raise RuntimeError("TRANSFORMER_ADVISER_MAX_UNCERTAINTY must be between zero and one.")
        if not self.continual_training_horizons or any(value <= 0 for value in self.continual_training_horizons):
            raise RuntimeError("CONTINUAL_TRAINING_HORIZONS must contain positive minute values.")
        if self.continual_training_min_new_labels < 1 or self.continual_training_min_playbook_samples < 1:
            raise RuntimeError("Continual-training sample thresholds must be positive.")
        if self.continual_training_paper_weight < 1:
            raise RuntimeError("CONTINUAL_TRAINING_PAPER_WEIGHT must be at least 1.")
        if self.outcome_label_batch_size < 1:
            raise RuntimeError("OUTCOME_LABEL_BATCH_SIZE must be positive.")
        if not 0.0 <= self.outcome_label_min_edge_pct <= 0.02:
            raise RuntimeError("OUTCOME_LABEL_MIN_EDGE_PCT must be between 0 and 0.02.")
        if not 0.0 <= self.outcome_label_slippage_pct <= 0.02:
            raise RuntimeError("OUTCOME_LABEL_SLIPPAGE_PCT must be between 0 and 0.02.")
        if self.outcome_label_snapshot_tolerance_seconds < 0:
            raise RuntimeError("OUTCOME_LABEL_SNAPSHOT_TOLERANCE_SECONDS cannot be negative.")
        if self.outcome_label_max_bar_gap_minutes < 1:
            raise RuntimeError("OUTCOME_LABEL_MAX_BAR_GAP_MINUTES must be positive.")
        if self.paper_exploration_max_trades_per_day < 1 or self.paper_exploration_cooldown_minutes < 1:
            raise RuntimeError("Paper-exploration limits must be positive.")
        if self.paper_exploration_max_notional <= 0 or self.paper_exploration_max_notional > self.max_trade_notional:
            raise RuntimeError("PAPER_EXPLORATION_MAX_NOTIONAL must be positive and no larger than MAX_TRADE_NOTIONAL.")
        if not 0.0 <= self.paper_exploration_min_liquidity_score <= 1.0:
            raise RuntimeError("PAPER_EXPLORATION_MIN_LIQUIDITY_SCORE must be between 0 and 1.")
        if not 0.0 < self.paper_exploration_max_spread_pct <= self.max_spread_pct:
            raise RuntimeError("PAPER_EXPLORATION_MAX_SPREAD_PCT must be positive and no larger than MAX_SPREAD_PCT.")
        if self.paper_learning_mode and not (self.alpaca_paper and self.alpaca_paper_trade and self.data_mode == "paper"):
            raise RuntimeError("PAPER_LEARNING_MODE can only run with Alpaca paper trading and BOT_DATA_MODE=paper.")
        if not 0.0 <= self.paper_learning_fast_min_score <= 100.0:
            raise RuntimeError("PAPER_LEARNING_FAST_MIN_SCORE must be between 0 and 100.")
        if not 0.0 <= self.paper_learning_min_score <= 100.0 or not 0.0 <= self.paper_learning_min_score_gap <= 100.0:
            raise RuntimeError("Paper-learning score thresholds must be between 0 and 100.")
        if not 0.0 <= self.paper_learning_max_no_trade_score <= 100.0:
            raise RuntimeError("PAPER_LEARNING_MAX_NO_TRADE_SCORE must be between 0 and 100.")
        if not 0.0 <= self.paper_learning_min_playbook_score <= 100.0:
            raise RuntimeError("PAPER_LEARNING_MIN_PLAYBOOK_SCORE must be between 0 and 100.")
        if not 0.0 <= self.paper_learning_min_pattern_quality <= 1.0:
            raise RuntimeError("PAPER_LEARNING_MIN_PATTERN_QUALITY must be between 0 and 1.")
        if not 0.0 <= self.paper_learning_min_liquidity_score <= 1.0:
            raise RuntimeError("PAPER_LEARNING_MIN_LIQUIDITY_SCORE must be between 0 and 1.")
        if not 0.0 < self.paper_learning_max_spread_pct <= self.max_spread_pct:
            raise RuntimeError("PAPER_LEARNING_MAX_SPREAD_PCT must be positive and no larger than MAX_SPREAD_PCT.")
        if not 0.0 < self.paper_learning_exploration_max_notional <= self.max_trade_notional:
            raise RuntimeError(
                "PAPER_LEARNING_EXPLORATION_MAX_NOTIONAL must be positive and no larger than MAX_TRADE_NOTIONAL."
            )
        if self.paper_learning_fast_order_cooldown_seconds < 1:
            raise RuntimeError("PAPER_LEARNING_FAST_ORDER_COOLDOWN_SECONDS must be at least 1.")
        if not 0.0 < self.paper_learning_stop_loss_pct < self.paper_learning_take_profit_pct <= 0.01:
            raise RuntimeError("Paper-learning stop and target percentages must be positive, ordered, and no larger than 1%.")
        if self.paper_learning_max_concurrent_trades < 1:
            raise RuntimeError("PAPER_LEARNING_MAX_CONCURRENT_TRADES must be at least 1.")
        if self.paper_learning_max_aggregate_notional < max(
            self.paper_exploration_max_notional,
            self.paper_learning_exploration_max_notional,
        ):
            raise RuntimeError("PAPER_LEARNING_MAX_AGGREGATE_NOTIONAL must fund at least one learning probe.")
        if not 0.0 < self.paper_learning_max_spread_to_stop_ratio <= 1.0:
            raise RuntimeError("PAPER_LEARNING_MAX_SPREAD_TO_STOP_RATIO must be between 0 and 1.")
        if not 0.0 <= self.paper_learning_exploration_sample_rate <= 1.0:
            raise RuntimeError("PAPER_LEARNING_EXPLORATION_SAMPLE_RATE must be between 0 and 1.")
        if self.paper_learning_max_exploration_trades_per_day < 0:
            raise RuntimeError("PAPER_LEARNING_MAX_EXPLORATION_TRADES_PER_DAY cannot be negative; zero means unlimited.")
        if self.paper_learning_exploration_cooldown_seconds < 1:
            raise RuntimeError("PAPER_LEARNING_EXPLORATION_COOLDOWN_SECONDS must be positive.")
        if min(self.post_loss_cooldown_seconds, self.regime_loss_lookback_minutes, self.regime_loss_threshold, self.regime_loss_cooldown_minutes) < 1:
            raise RuntimeError("Entry cooldown settings must be positive.")
        if min(
            self.fast_scalp_max_quote_age_seconds,
            self.fast_scalp_max_trade_age_seconds,
            self.minute_entry_max_quote_age_seconds,
            self.minute_entry_max_trade_age_seconds,
            self.performance_snapshot_interval_seconds,
        ) <= 0:
            raise RuntimeError("Entry freshness and performance snapshot intervals must be positive.")
        if not 0.0 <= self.fast_scalp_min_spread_stability <= 1.0 or not 0.0 <= self.minute_entry_min_spread_stability <= 1.0:
            raise RuntimeError("Spread-stability thresholds must be between 0 and 1.")
        if min(
            self.estimated_fee_per_share,
            self.estimated_minimum_order_fee,
            self.estimated_round_trip_slippage_pct,
            self.economic_breakeven_safety_buffer_pct,
        ) < 0:
            raise RuntimeError("Estimated trading costs cannot be negative.")
        if self.position_manager_interval_ms < 50:
            raise RuntimeError("POSITION_MANAGER_INTERVAL_MS must be at least 50 milliseconds.")
        if self.position_manager_broker_refresh_seconds < 1 or self.price_snapshot_interval_seconds < 1:
            raise RuntimeError("Position-manager broker refresh and price snapshot intervals must be at least one second.")
        if not 0.0 < self.position_emergency_stop_pct <= 0.02:
            raise RuntimeError("POSITION_EMERGENCY_STOP_PCT must be positive and no larger than 2%.")
        if not 0.0 < self.position_breakeven_trigger_pct < self.position_emergency_stop_pct:
            raise RuntimeError("POSITION_BREAKEVEN_TRIGGER_PCT must be positive and below the emergency stop.")
        if not 0.0 <= self.position_breakeven_offset_pct < self.position_breakeven_trigger_pct:
            raise RuntimeError("POSITION_BREAKEVEN_OFFSET_PCT must be non-negative and below the trigger.")
        if not self.position_breakeven_trigger_pct <= self.position_trailing_trigger_pct < self.position_emergency_stop_pct:
            raise RuntimeError("POSITION_TRAILING_TRIGGER_PCT must be at least the breakeven trigger and below the emergency stop.")
        if not 0.0 < self.position_trailing_distance_pct < self.position_emergency_stop_pct:
            raise RuntimeError("POSITION_TRAILING_DISTANCE_PCT must be positive and below the emergency stop.")
        if not 0.0 < self.position_max_normal_stop_pct <= self.position_emergency_stop_pct:
            raise RuntimeError("POSITION_MAX_NORMAL_STOP_PCT must be positive and no larger than the emergency stop.")
        if self.position_min_reward_risk < 1.0 or not 0.0 <= self.position_structure_buffer_atr <= 2.0:
            raise RuntimeError("Position reward/risk and structure-buffer settings are invalid.")
        if not 0.0 < self.position_profit_giveback_min_mfe_pct < self.position_emergency_stop_pct:
            raise RuntimeError("POSITION_PROFIT_GIVEBACK_MIN_MFE_PCT must be below the emergency stop.")
        if not 0.0 < self.position_max_profit_giveback_fraction < 1.0:
            raise RuntimeError("POSITION_MAX_PROFIT_GIVEBACK_FRACTION must be between 0 and 1.")
        if self.position_min_stop_improvement < 0.01 or self.position_stop_replace_cooldown_seconds < 1:
            raise RuntimeError("Dynamic stop replacement must improve by at least $0.01 and wait at least one second.")
        if not 0.0 <= self.position_profitable_time_exit_buffer_pct < self.position_emergency_stop_pct:
            raise RuntimeError("POSITION_PROFITABLE_TIME_EXIT_BUFFER_PCT is outside the supported range.")
        if not 0.0 <= self.position_invalidation_min_loss_pct < self.position_emergency_stop_pct:
            raise RuntimeError("POSITION_INVALIDATION_MIN_LOSS_PCT must be below the emergency stop.")
        if not 0.0 <= self.position_invalidation_min_confidence <= 1.0:
            raise RuntimeError("POSITION_INVALIDATION_MIN_CONFIDENCE must be between 0 and 1.")
        if self.position_invalidation_required_votes < 1 or self.position_force_flatten_minutes_before_close < 1:
            raise RuntimeError("Position invalidation votes and force-flatten minutes must be positive.")
        if not (
            self.position_close_management_minutes_before_close
            > self.position_close_risk_reduction_minutes_before_close
            > self.position_force_flatten_minutes_before_close
        ):
            raise RuntimeError("Close management must stage protection before risk reduction and final flattening.")
        if self.exit_model_min_trustworthy_outcomes < 100:
            raise RuntimeError("EXIT_MODEL_MIN_TRUSTWORTHY_OUTCOMES must be at least 100.")
        if self.execution_reconcile_interval_seconds < 1:
            raise RuntimeError("EXECUTION_RECONCILE_INTERVAL_SECONDS must be at least one second.")
        if self.execution_bracket_grace_period_seconds < self.execution_reconcile_interval_seconds:
            raise RuntimeError(
                "EXECUTION_BRACKET_GRACE_PERIOD_SECONDS must be at least one reconciliation interval."
            )
        if self.execution_residual_confirmation_delay_seconds < 0:
            raise RuntimeError("EXECUTION_RESIDUAL_CONFIRMATION_DELAY_SECONDS cannot be negative.")
        if not 1 <= self.startup_recovery_days <= 90:
            raise RuntimeError("STARTUP_RECOVERY_DAYS must be between 1 and 90.")
        if not 10 <= self.execution_entry_freeze_minutes_before_close <= 15:
            raise RuntimeError("EXECUTION_ENTRY_FREEZE_MINUTES_BEFORE_CLOSE must be between 10 and 15.")
        if not 1 <= self.execution_session_flatten_minutes_before_close <= self.execution_entry_freeze_minutes_before_close:
            raise RuntimeError("EXECUTION_SESSION_FLATTEN_MINUTES_BEFORE_CLOSE must be positive and no later than the entry freeze.")
        if min(
            self.execution_intent_timeout_seconds,
            self.execution_order_state_timeout_seconds,
            self.execution_cancel_wait_seconds,
            self.execution_shutdown_timeout_seconds,
            self.execution_direction_switch_cooldown_seconds,
        ) < 1:
            raise RuntimeError("Execution timeout and cooldown settings must be positive.")
        if min(
            self.execution_broker_rejection_threshold,
            self.execution_stream_failure_threshold,
            self.execution_reconciliation_failure_threshold,
            self.execution_order_state_failure_threshold,
        ) < 1:
            raise RuntimeError("Execution circuit-breaker thresholds must be positive.")
        if not 0.0 < self.paper_max_session_loss_pct <= self.max_daily_loss_pct:
            raise RuntimeError("PAPER_MAX_SESSION_LOSS_PCT must be positive and no larger than MAX_DAILY_LOSS_PCT.")
        if not self.paper_max_session_loss_pct <= self.paper_max_drawdown_pct <= 0.05:
            raise RuntimeError("PAPER_MAX_DRAWDOWN_PCT must be at least the paper loss limit and no larger than 5%.")
        if min(
            self.paper_max_consecutive_losses,
            self.paper_max_trades_per_day,
            self.max_orders_per_minute,
            self.paper_max_orders_per_minute,
            self.risk_execution_error_limit,
        ) < 1:
            raise RuntimeError("Paper and live risk-count limits must be positive.")
        if self.paper_max_orders_per_minute < self.max_orders_per_minute:
            raise RuntimeError("PAPER_MAX_ORDERS_PER_MINUTE cannot be lower than MAX_ORDERS_PER_MINUTE.")
        if not 0.0 <= self.max_correlated_exposure_pct <= 1.0:
            raise RuntimeError("MAX_CORRELATED_EXPOSURE_PCT must be between 0 and 1.")
        if self.experimental_profit_factor_threshold <= 1.0 or not 0.0 < self.experimental_size_multiplier <= 1.0:
            raise RuntimeError("Experimental model sizing settings are invalid.")
        if not 0.0 < self.partial_profit_fraction < 1.0:
            raise RuntimeError("PARTIAL_PROFIT_FRACTION must be between 0 and 1.")
        if self.partial_profit_runner_target_multiplier <= 1.0:
            raise RuntimeError("PARTIAL_PROFIT_RUNNER_TARGET_MULTIPLIER must be greater than 1.")
        if self.paper_require_ml_model and not self.paper_learning_mode:
            raise RuntimeError("PAPER_REQUIRE_ML_MODEL requires PAPER_LEARNING_MODE=true.")
        if not 0.0 <= self.paper_ml_min_advisory_confidence <= 1.0:
            raise RuntimeError("PAPER_ML_MIN_ADVISORY_CONFIDENCE must be between 0 and 1.")
        if not 0.0 <= self.paper_ml_max_score_adjustment <= 10.0:
            raise RuntimeError("PAPER_ML_MAX_SCORE_ADJUSTMENT must be between 0 and 10.")
        if self.enable_llm_live_trading:
            raise RuntimeError("ENABLE_LLM_LIVE_TRADING must remain false; LLM processing is offline and advisory only.")
        if not 0.0 <= self.llm_context_max_sizing_adjustment <= 0.10:
            raise RuntimeError("LLM_CONTEXT_MAX_SIZING_ADJUSTMENT must be between 0 and 0.10.")
        if not 0.0 <= self.llm_news_min_linked_fraction <= 1.0:
            raise RuntimeError("LLM_NEWS_MIN_LINKED_FRACTION must be between 0 and 1.")
        if self.tradingagents_advisory_max_age_minutes < 1:
            raise RuntimeError("TRADINGAGENTS_ADVISORY_MAX_AGE_MINUTES must be positive.")
        if not 0.0 <= self.tradingagents_min_confidence <= 1.0:
            raise RuntimeError("TRADINGAGENTS_MIN_CONFIDENCE must be between 0 and 1.")
        if not 0.0 <= self.tradingagents_max_score_adjustment <= 5.0:
            raise RuntimeError("TRADINGAGENTS_MAX_SCORE_ADJUSTMENT must be between 0 and 5.")
        if not 0.0 <= self.tradingagents_max_sizing_adjustment <= 0.10:
            raise RuntimeError("TRADINGAGENTS_MAX_SIZING_ADJUSTMENT must be between 0 and 0.10.")
        if min(self.promotion_min_paper_trade_count, self.promotion_min_regime_count, self.drift_min_predictions, self.drift_min_paper_outcomes) < 1:
            raise RuntimeError("ML promotion and drift sample thresholds must be positive.")
        if not 0.0 < self.drift_demotion_score <= 1.0:
            raise RuntimeError("DRIFT_DEMOTION_SCORE must be between 0 and 1.")
        if not 0.0 < self.drift_profit_factor_floor_ratio <= 1.0 or self.drift_drawdown_limit_multiplier < 1.0:
            raise RuntimeError("ML drift performance limits are invalid.")
        if not 0 <= self.retrain_after_hour_et <= 23 or not 0 <= self.retrain_before_hour_et <= 23:
            raise RuntimeError("Retraining window hours must be valid New York clock hours.")
        if self.retrain_min_clean_episodes < 1:
            raise RuntimeError("RETRAIN_MIN_CLEAN_EPISODES must be positive.")
        if self.database_busy_timeout_ms < 1_000:
            raise RuntimeError("DATABASE_BUSY_TIMEOUT_MS must be at least 1000 milliseconds.")
        if not self.order_block_timeframes or any(value <= 0 for value in self.order_block_timeframes):
            raise RuntimeError("ORDER_BLOCK_TIMEFRAMES must contain positive minute values.")
        if self.order_block_history_minutes < max(self.order_block_timeframes):
            raise RuntimeError("ORDER_BLOCK_HISTORY_MINUTES must cover the largest order-block timeframe.")
        if self.order_block_displacement_atr <= 0 or self.order_block_min_volume_ratio <= 0:
            raise RuntimeError("Order-block displacement and volume thresholds must be positive.")
        if self.options_underlying.upper() != "GLD":
            raise RuntimeError("OPTIONS_UNDERLYING must remain GLD for this bot.")
        if self.options_feed not in {"indicative", "opra"}:
            raise RuntimeError("OPTIONS_FEED must be either indicative or opra.")
        if self.options_poll_interval_seconds < 10:
            raise RuntimeError("OPTIONS_POLL_INTERVAL_SECONDS must be at least 10 seconds.")
        if not 0.0 <= self.options_max_score_adjustment <= 5.0:
            raise RuntimeError("OPTIONS_MAX_SCORE_ADJUSTMENT must be between 0 and 5.")

    def require_credentials(self) -> None:
        if not self.alpaca_api_key or not self.alpaca_secret_key:
            raise RuntimeError("Missing Alpaca paper credentials. Set ALPACA_API_KEY and ALPACA_SECRET_KEY in .env.")


def load_settings(env_file: str | Path | None = None) -> Settings:
    if env_file is None:
        env_file = PROJECT_ROOT / ".env"
    _load_dotenv(Path(env_file))
    alpaca_paper = _bool_env("ALPACA_PAPER", True)
    data_mode = _normalize_data_mode(os.getenv("BOT_DATA_MODE"), alpaca_paper=alpaca_paper)
    default_database_url = f"sqlite:///data/{data_mode}/gld_scalper.db"
    default_csv_export_dir = f"exports/{data_mode}/hourly"
    settings = Settings(
        alpaca_api_key=os.getenv("ALPACA_API_KEY", ""),
        alpaca_secret_key=os.getenv("ALPACA_SECRET_KEY", ""),
        alpaca_paper=alpaca_paper,
        alpaca_paper_trade=_bool_env("ALPACA_PAPER_TRADE", True),
        alpaca_endpoint=os.getenv("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2"),
        alpaca_data_feed=os.getenv("ALPACA_DATA_FEED", "iex").lower(),
        data_mode=data_mode,
        database_url=os.getenv("DATABASE_URL", default_database_url),
        database_busy_timeout_ms=_int_env("DATABASE_BUSY_TIMEOUT_MS", 30_000),
        bot_symbol=os.getenv("BOT_SYMBOL", "GLD").upper(),
        paper_account_size=_float_env("PAPER_ACCOUNT_SIZE", 1_000_000.0),
        min_trade_notional=_float_env("MIN_TRADE_NOTIONAL", 5_000.0),
        max_trade_notional=_float_env("MAX_TRADE_NOTIONAL", 25_000.0),
        max_daily_loss_pct=_float_env("MAX_DAILY_LOSS_PCT", 0.01),
        max_trade_risk_pct=_float_env("MAX_TRADE_RISK_PCT", 0.0025),
        max_trades_per_day=_int_env("MAX_TRADES_PER_DAY", 20),
        max_consecutive_losses=_int_env("MAX_CONSECUTIVE_LOSSES", 3),
        max_holding_minutes=_int_env("MAX_HOLDING_MINUTES", 15),
        trade_timeframe=os.getenv("TRADE_TIMEFRAME", "1Min"),
        startup_recovery_days=_int_env("STARTUP_RECOVERY_DAYS", 10),
        confirm_timeframe_1=os.getenv("CONFIRM_TIMEFRAME_1", "5Min"),
        confirm_timeframe_2=os.getenv("CONFIRM_TIMEFRAME_2", "15Min"),
        enable_ema_cross_strategy=_bool_env("ENABLE_EMA_CROSS_STRATEGY", True),
        ema_cross_timeframes=_int_list_env("EMA_CROSS_TIMEFRAMES", [1, 5, 15, 30, 45, 60]),
        ema_cross_history_minutes=_int_env("EMA_CROSS_HISTORY_MINUTES", 3_900),
        ema_cross_fast_period=_int_env("EMA_CROSS_FAST_PERIOD", 10),
        ema_cross_slow_period=_int_env("EMA_CROSS_SLOW_PERIOD", 20),
        ema_cross_use_adx_filter=_bool_env("EMA_CROSS_USE_ADX_FILTER", True),
        ema_cross_adx_period=_int_env("EMA_CROSS_ADX_PERIOD", 14),
        ema_cross_adx_threshold=_float_env("EMA_CROSS_ADX_THRESHOLD", 20.0),
        ema_cross_use_cooldown=_bool_env("EMA_CROSS_USE_COOLDOWN", True),
        ema_cross_cooldown_bars=_int_env("EMA_CROSS_COOLDOWN_BARS", 10),
        ema_cross_atr_period=_int_env("EMA_CROSS_ATR_PERIOD", 14),
        ema_cross_stop_loss_atr_multiple=_float_env("EMA_CROSS_STOP_LOSS_ATR_MULTIPLE", 1.5),
        ema_cross_take_profit_atr_multiple=_float_env("EMA_CROSS_TAKE_PROFIT_ATR_MULTIPLE", 3.0),
        ema_cross_paper_signal_authority=_bool_env("EMA_CROSS_PAPER_SIGNAL_AUTHORITY", True),
        enable_shorts=_bool_env("ENABLE_SHORTS", True),
        enable_extended_hours=_bool_env("ENABLE_EXTENDED_HOURS", False),
        enable_live_stream=_bool_env("ENABLE_LIVE_STREAM", True),
        stream_startup_grace_seconds=_int_env("STREAM_STARTUP_GRACE_SECONDS", 20),
        bar_stale_seconds=_int_env("BAR_STALE_SECONDS", 180),
        quote_stale_seconds=_int_env("QUOTE_STALE_SECONDS", 30),
        enable_fast_scalp=_bool_env("ENABLE_FAST_SCALP", True),
        enable_fast_scalp_order_submission=_bool_env("ENABLE_FAST_SCALP_ORDER_SUBMISSION", True),
        fast_scalp_interval_ms=_int_env("FAST_SCALP_INTERVAL_MS", 250),
        fast_scalp_event_queue_size=_int_env("FAST_SCALP_EVENT_QUEUE_SIZE", 5_000),
        fast_scalp_min_quote_count=_int_env("FAST_SCALP_MIN_QUOTE_COUNT", 3),
        fast_scalp_min_trade_count=_int_env("FAST_SCALP_MIN_TRADE_COUNT", 1),
        fast_scalp_lookback_seconds=_int_env("FAST_SCALP_LOOKBACK_SECONDS", 5),
        fast_scalp_false_break_window_seconds=_int_env("FAST_SCALP_FALSE_BREAK_WINDOW_SECONDS", 3),
        fast_scalp_order_cooldown_seconds=_int_env("FAST_SCALP_ORDER_COOLDOWN_SECONDS", 30),
        fast_scalp_no_trade_log_interval_seconds=_int_env("FAST_SCALP_NO_TRADE_LOG_INTERVAL_SECONDS", 10),
        fast_scalp_tight_spread_pct=_float_env("FAST_SCALP_TIGHT_SPREAD_PCT", 0.00035),
        fast_scalp_breakout_min_move_pct=_float_env("FAST_SCALP_BREAKOUT_MIN_MOVE_PCT", 0.00025),
        fast_scalp_imbalance_threshold=_float_env("FAST_SCALP_IMBALANCE_THRESHOLD", 0.30),
        fast_scalp_min_trade_intensity=_float_env("FAST_SCALP_MIN_TRADE_INTENSITY", 0.40),
        fast_scalp_min_confidence=_float_env("FAST_SCALP_MIN_CONFIDENCE", 0.62),
        fast_scalp_volatility_burst_pct=_float_env("FAST_SCALP_VOLATILITY_BURST_PCT", 0.0010),
        fast_scalp_max_quote_age_seconds=_float_env("FAST_SCALP_MAX_QUOTE_AGE_SECONDS", 2.0),
        fast_scalp_max_trade_age_seconds=_float_env("FAST_SCALP_MAX_TRADE_AGE_SECONDS", 3.0),
        fast_scalp_min_spread_stability=_float_env("FAST_SCALP_MIN_SPREAD_STABILITY", 0.55),
        minute_entry_max_quote_age_seconds=_float_env("MINUTE_ENTRY_MAX_QUOTE_AGE_SECONDS", 15.0),
        minute_entry_max_trade_age_seconds=_float_env("MINUTE_ENTRY_MAX_TRADE_AGE_SECONDS", 30.0),
        minute_entry_min_trade_intensity=_float_env("MINUTE_ENTRY_MIN_TRADE_INTENSITY", 0.05),
        minute_entry_min_spread_stability=_float_env("MINUTE_ENTRY_MIN_SPREAD_STABILITY", 0.35),
        enable_order_blocks=_bool_env("ENABLE_ORDER_BLOCKS", True),
        order_block_timeframes=_int_list_env("ORDER_BLOCK_TIMEFRAMES", [1, 5, 15, 30, 45, 60]),
        order_block_history_minutes=_int_env("ORDER_BLOCK_HISTORY_MINUTES", 3_900),
        order_block_lookback_bars=_int_env("ORDER_BLOCK_LOOKBACK_BARS", 80),
        order_block_displacement_atr=_float_env("ORDER_BLOCK_DISPLACEMENT_ATR", 1.20),
        order_block_min_volume_ratio=_float_env("ORDER_BLOCK_MIN_VOLUME_RATIO", 1.05),
        order_block_max_age_bars=_int_env("ORDER_BLOCK_MAX_AGE_BARS", 120),
        order_block_retest_tolerance_pct=_float_env("ORDER_BLOCK_RETEST_TOLERANCE_PCT", 0.0005),
        enable_options_intelligence=_bool_env("ENABLE_OPTIONS_INTELLIGENCE", True),
        options_underlying=os.getenv("OPTIONS_UNDERLYING", "GLD").upper(),
        options_feed=os.getenv("OPTIONS_FEED", "indicative").lower(),
        options_poll_interval_seconds=_int_env("OPTIONS_POLL_INTERVAL_SECONDS", 30),
        options_expiration_days=_int_env("OPTIONS_EXPIRATION_DAYS", 30),
        options_strike_window_pct=_float_env("OPTIONS_STRIKE_WINDOW_PCT", 0.05),
        options_max_contracts=_int_env("OPTIONS_MAX_CONTRACTS", 80),
        options_max_quote_age_seconds=_int_env("OPTIONS_MAX_QUOTE_AGE_SECONDS", 180),
        options_max_spread_pct=_float_env("OPTIONS_MAX_SPREAD_PCT", 0.30),
        options_max_score_adjustment=_float_env("OPTIONS_MAX_SCORE_ADJUSTMENT", 3.0),
        enable_latency_aware_ml=_bool_env("ENABLE_LATENCY_AWARE_ML", True),
        ml_max_inference_latency_ms=_float_env("ML_MAX_INFERENCE_LATENCY_MS", 5.0),
        ml_validation_fraction=_float_env("ML_VALIDATION_FRACTION", 0.20),
        ml_purge_minutes=_int_env("ML_PURGE_MINUTES", 30),
        ml_embargo_minutes=_int_env("ML_EMBARGO_MINUTES", 30),
        ml_min_confidence=_float_env("ML_MIN_CONFIDENCE", 0.58),
        ml_min_probability_margin=_float_env("ML_MIN_PROBABILITY_MARGIN", 0.08),
        ml_max_missing_feature_fraction=_float_env("ML_MAX_MISSING_FEATURE_FRACTION", 0.25),
        ml_max_outlier_feature_fraction=_float_env("ML_MAX_OUTLIER_FEATURE_FRACTION", 0.15),
        enable_transformer_shadow=_bool_env("ENABLE_TRANSFORMER_SHADOW", True),
        transformer_trading_mode=os.getenv("TRANSFORMER_TRADING_MODE", "shadow").strip().lower(),
        transformer_queue_size=_int_env("TRANSFORMER_QUEUE_SIZE", 64),
        transformer_cache_max_age_seconds=_float_env("TRANSFORMER_CACHE_MAX_AGE_SECONDS", 5.0),
        transformer_model_refresh_seconds=_int_env("TRANSFORMER_MODEL_REFRESH_SECONDS", 60),
        transformer_torch_threads=_int_env("TRANSFORMER_TORCH_THREADS", 2),
        transformer_bounded_max_score_adjustment=_float_env("TRANSFORMER_BOUNDED_MAX_SCORE_ADJUSTMENT", 3.0),
        transformer_adviser_min_confidence=_float_env("TRANSFORMER_ADVISER_MIN_CONFIDENCE", 0.65),
        transformer_adviser_max_uncertainty=_float_env("TRANSFORMER_ADVISER_MAX_UNCERTAINTY", 0.60),
        transformer_champion_min_expected_edge_pct=_float_env("TRANSFORMER_CHAMPION_MIN_EXPECTED_EDGE_PCT", 0.00015),
        transformer_auto_promotion=_bool_env("TRANSFORMER_AUTO_PROMOTION", True),
        transformer_auto_demotion=_bool_env("TRANSFORMER_AUTO_DEMOTION", True),
        transformer_demotion_min_paper_trades=_int_env("TRANSFORMER_DEMOTION_MIN_PAPER_TRADES", 50),
        transformer_demotion_profit_factor=_float_env("TRANSFORMER_DEMOTION_PROFIT_FACTOR", 0.85),
        transformer_training_patience_rounds=_int_env("TRANSFORMER_TRAINING_PATIENCE_ROUNDS", 3),
        transformer_training_minimum_improvement=_float_env("TRANSFORMER_TRAINING_MINIMUM_IMPROVEMENT", 0.001),
        transformer_training_interval_minutes=_int_env("TRANSFORMER_TRAINING_INTERVAL_MINUTES", 60),
        continual_training_horizons=_int_list_env("CONTINUAL_TRAINING_HORIZONS", [1, 3, 5, 15]),
        continual_training_playbooks=[
            item.strip().lower()
            for item in os.getenv(
                "CONTINUAL_TRAINING_PLAYBOOKS",
                "all fast_microstructure minute_setups proper_breakout false_break_reversal pullback_continuation compression_breakout spread_capture news_event trend_continuation ema_cross_filtered",
            ).replace(",", " ").split()
            if item.strip()
        ],
        continual_training_interval_minutes=_int_env("CONTINUAL_TRAINING_INTERVAL_MINUTES", 60),
        continual_training_min_new_labels=_int_env("CONTINUAL_TRAINING_MIN_NEW_LABELS", 500),
        continual_training_min_playbook_samples=_int_env("CONTINUAL_TRAINING_MIN_PLAYBOOK_SAMPLES", 750),
        continual_training_paper_weight=_int_env("CONTINUAL_TRAINING_PAPER_WEIGHT", 2),
        continual_training_paper_lookback_days=_int_env("CONTINUAL_TRAINING_PAPER_LOOKBACK_DAYS", 730),
        continual_training_lock_stale_hours=_int_env("CONTINUAL_TRAINING_LOCK_STALE_HOURS", 24),
        continual_training_only_outside_regular_hours=_bool_env("CONTINUAL_TRAINING_ONLY_OUTSIDE_REGULAR_HOURS", True),
        enable_scheduled_retraining=_bool_env("ENABLE_SCHEDULED_RETRAINING", True),
        retrain_interval_hours=_int_env("RETRAIN_INTERVAL_HOURS", 24),
        retrain_lookback_days=_int_env("RETRAIN_LOOKBACK_DAYS", 90),
        retrain_min_samples=_int_env("RETRAIN_MIN_SAMPLES", 50),
        retrain_min_clean_episodes=_int_env("RETRAIN_MIN_CLEAN_EPISODES", 10),
        retrain_only_outside_regular_hours=_bool_env("RETRAIN_ONLY_OUTSIDE_REGULAR_HOURS", True),
        enable_missed_opportunity_learning=_bool_env("ENABLE_MISSED_OPPORTUNITY_LEARNING", True),
        missed_opportunity_horizon_minutes=_int_env("MISSED_OPPORTUNITY_HORIZON_MINUTES", 15),
        missed_opportunity_min_move_pct=_float_env("MISSED_OPPORTUNITY_MIN_MOVE_PCT", 0.002),
        missed_opportunity_max_adverse_pct=_float_env("MISSED_OPPORTUNITY_MAX_ADVERSE_PCT", 0.0012),
        enable_multi_horizon_outcome_labels=_bool_env("ENABLE_MULTI_HORIZON_OUTCOME_LABELS", True),
        outcome_label_batch_size=_int_env("OUTCOME_LABEL_BATCH_SIZE", 5_000),
        outcome_label_min_edge_pct=_float_env("OUTCOME_LABEL_MIN_EDGE_PCT", 0.0002),
        outcome_label_slippage_pct=_float_env("OUTCOME_LABEL_SLIPPAGE_PCT", 0.0001),
        outcome_label_snapshot_tolerance_seconds=_int_env("OUTCOME_LABEL_SNAPSHOT_TOLERANCE_SECONDS", 5),
        outcome_label_max_bar_gap_minutes=_int_env("OUTCOME_LABEL_MAX_BAR_GAP_MINUTES", 2),
        enable_paper_exploration=_bool_env("ENABLE_PAPER_EXPLORATION", True),
        paper_exploration_max_trades_per_day=_int_env("PAPER_EXPLORATION_MAX_TRADES_PER_DAY", 2),
        paper_exploration_cooldown_minutes=_int_env("PAPER_EXPLORATION_COOLDOWN_MINUTES", 60),
        paper_exploration_max_notional=_float_env("PAPER_EXPLORATION_MAX_NOTIONAL", 1_000.0),
        paper_exploration_min_score=_float_env("PAPER_EXPLORATION_MIN_SCORE", 70.0),
        paper_exploration_min_score_gap=_float_env("PAPER_EXPLORATION_MIN_SCORE_GAP", 20.0),
        paper_exploration_max_no_trade_score=_float_env("PAPER_EXPLORATION_MAX_NO_TRADE_SCORE", 65.0),
        paper_exploration_min_liquidity_score=_float_env("PAPER_EXPLORATION_MIN_LIQUIDITY_SCORE", 0.70),
        paper_exploration_max_spread_pct=_float_env("PAPER_EXPLORATION_MAX_SPREAD_PCT", 0.0008),
        paper_learning_mode=_bool_env("PAPER_LEARNING_MODE", False),
        paper_learning_fast_min_score=_float_env("PAPER_LEARNING_FAST_MIN_SCORE", 55.0),
        paper_learning_min_score=_float_env("PAPER_LEARNING_MIN_SCORE", 60.0),
        paper_learning_min_score_gap=_float_env("PAPER_LEARNING_MIN_SCORE_GAP", 5.0),
        paper_learning_max_no_trade_score=_float_env("PAPER_LEARNING_MAX_NO_TRADE_SCORE", 100.0),
        paper_learning_min_playbook_score=_float_env("PAPER_LEARNING_MIN_PLAYBOOK_SCORE", 60.0),
        paper_learning_min_pattern_quality=_float_env("PAPER_LEARNING_MIN_PATTERN_QUALITY", 0.45),
        paper_learning_min_liquidity_score=_float_env("PAPER_LEARNING_MIN_LIQUIDITY_SCORE", 0.40),
        paper_learning_max_spread_pct=_float_env("PAPER_LEARNING_MAX_SPREAD_PCT", 0.0015),
        paper_learning_exploration_max_notional=_float_env("PAPER_LEARNING_EXPLORATION_MAX_NOTIONAL", 2_000.0),
        paper_learning_fast_order_cooldown_seconds=_int_env("PAPER_LEARNING_FAST_ORDER_COOLDOWN_SECONDS", 5),
        paper_learning_stop_loss_pct=_float_env("PAPER_LEARNING_STOP_LOSS_PCT", 0.0008),
        paper_learning_take_profit_pct=_float_env("PAPER_LEARNING_TAKE_PROFIT_PCT", 0.0010),
        paper_learning_ignore_model_rejection=_bool_env("PAPER_LEARNING_IGNORE_MODEL_REJECTION", True),
        paper_learning_max_concurrent_trades=_int_env("PAPER_LEARNING_MAX_CONCURRENT_TRADES", 5),
        paper_learning_max_aggregate_notional=_float_env("PAPER_LEARNING_MAX_AGGREGATE_NOTIONAL", 5_000.0),
        paper_learning_max_spread_to_stop_ratio=_float_env("PAPER_LEARNING_MAX_SPREAD_TO_STOP_RATIO", 0.65),
        paper_learning_exploration_sample_rate=_float_env("PAPER_LEARNING_EXPLORATION_SAMPLE_RATE", 0.25),
        paper_learning_max_exploration_trades_per_day=_int_env("PAPER_LEARNING_MAX_EXPLORATION_TRADES_PER_DAY", 0),
        paper_learning_exploration_cooldown_seconds=_int_env("PAPER_LEARNING_EXPLORATION_COOLDOWN_SECONDS", 5),
        post_loss_cooldown_seconds=_int_env("POST_LOSS_COOLDOWN_SECONDS", 120),
        regime_loss_lookback_minutes=_int_env("REGIME_LOSS_LOOKBACK_MINUTES", 60),
        regime_loss_threshold=_int_env("REGIME_LOSS_THRESHOLD", 3),
        regime_loss_cooldown_minutes=_int_env("REGIME_LOSS_COOLDOWN_MINUTES", 30),
        performance_snapshot_interval_seconds=_int_env("PERFORMANCE_SNAPSHOT_INTERVAL_SECONDS", 60),
        estimated_fee_per_share=_float_env("ESTIMATED_FEE_PER_SHARE", 0.001),
        estimated_minimum_order_fee=_float_env("ESTIMATED_MINIMUM_ORDER_FEE", 0.0),
        estimated_round_trip_slippage_pct=_float_env("ESTIMATED_ROUND_TRIP_SLIPPAGE_PCT", 0.00010),
        economic_breakeven_safety_buffer_pct=_float_env("ECONOMIC_BREAKEVEN_SAFETY_BUFFER_PCT", 0.00010),
        enable_dynamic_position_management=_bool_env("ENABLE_DYNAMIC_POSITION_MANAGEMENT", True),
        position_manager_interval_ms=_int_env("POSITION_MANAGER_INTERVAL_MS", 250),
        position_manager_broker_refresh_seconds=_int_env("POSITION_MANAGER_BROKER_REFRESH_SECONDS", 2),
        price_snapshot_interval_seconds=_int_env("PRICE_SNAPSHOT_INTERVAL_SECONDS", 1),
        position_emergency_stop_pct=_float_env("POSITION_EMERGENCY_STOP_PCT", 0.0030),
        position_breakeven_trigger_pct=_float_env("POSITION_BREAKEVEN_TRIGGER_PCT", 0.00045),
        position_breakeven_offset_pct=_float_env("POSITION_BREAKEVEN_OFFSET_PCT", 0.00005),
        position_trailing_trigger_pct=_float_env("POSITION_TRAILING_TRIGGER_PCT", 0.00070),
        position_trailing_distance_pct=_float_env("POSITION_TRAILING_DISTANCE_PCT", 0.00035),
        position_max_normal_stop_pct=_float_env("POSITION_MAX_NORMAL_STOP_PCT", 0.00200),
        position_min_reward_risk=_float_env("POSITION_MIN_REWARD_RISK", 1.15),
        position_structure_buffer_atr=_float_env("POSITION_STRUCTURE_BUFFER_ATR", 0.20),
        position_profit_giveback_min_mfe_pct=_float_env("POSITION_PROFIT_GIVEBACK_MIN_MFE_PCT", 0.00080),
        position_max_profit_giveback_fraction=_float_env("POSITION_MAX_PROFIT_GIVEBACK_FRACTION", 0.50),
        position_min_stop_improvement=_float_env("POSITION_MIN_STOP_IMPROVEMENT", 0.02),
        position_stop_replace_cooldown_seconds=_int_env("POSITION_STOP_REPLACE_COOLDOWN_SECONDS", 2),
        position_profitable_time_exit_buffer_pct=_float_env("POSITION_PROFITABLE_TIME_EXIT_BUFFER_PCT", 0.00010),
        position_invalidation_min_loss_pct=_float_env("POSITION_INVALIDATION_MIN_LOSS_PCT", 0.00025),
        position_invalidation_min_confidence=_float_env("POSITION_INVALIDATION_MIN_CONFIDENCE", 0.80),
        position_invalidation_required_votes=_int_env("POSITION_INVALIDATION_REQUIRED_VOTES", 2),
        position_close_management_minutes_before_close=_int_env("POSITION_CLOSE_MANAGEMENT_MINUTES_BEFORE_CLOSE", 30),
        position_close_risk_reduction_minutes_before_close=_int_env("POSITION_CLOSE_RISK_REDUCTION_MINUTES_BEFORE_CLOSE", 15),
        position_force_flatten_minutes_before_close=_int_env("POSITION_FORCE_FLATTEN_MINUTES_BEFORE_CLOSE", 8),
        exit_model_min_trustworthy_outcomes=_int_env("EXIT_MODEL_MIN_TRUSTWORTHY_OUTCOMES", 500),
        execution_reconcile_interval_seconds=_int_env("EXECUTION_RECONCILE_INTERVAL_SECONDS", 3),
        execution_bracket_grace_period_seconds=_int_env("EXECUTION_BRACKET_GRACE_PERIOD_SECONDS", 15),
        execution_residual_confirmation_delay_seconds=_int_env("EXECUTION_RESIDUAL_CONFIRMATION_DELAY_SECONDS", 2),
        execution_entry_freeze_minutes_before_close=_int_env("EXECUTION_ENTRY_FREEZE_MINUTES_BEFORE_CLOSE", 15),
        execution_session_flatten_minutes_before_close=_int_env("EXECUTION_SESSION_FLATTEN_MINUTES_BEFORE_CLOSE", 10),
        execution_intent_timeout_seconds=_int_env("EXECUTION_INTENT_TIMEOUT_SECONDS", 30),
        execution_order_state_timeout_seconds=_int_env("EXECUTION_ORDER_STATE_TIMEOUT_SECONDS", 30),
        execution_cancel_wait_seconds=_int_env("EXECUTION_CANCEL_WAIT_SECONDS", 15),
        execution_shutdown_timeout_seconds=_int_env("EXECUTION_SHUTDOWN_TIMEOUT_SECONDS", 90),
        execution_direction_switch_cooldown_seconds=_int_env("EXECUTION_DIRECTION_SWITCH_COOLDOWN_SECONDS", 3),
        execution_enable_direction_switch=_bool_env("EXECUTION_ENABLE_DIRECTION_SWITCH", True),
        execution_flatten_residual_positions=_bool_env("EXECUTION_FLATTEN_RESIDUAL_POSITIONS", True),
        execution_flatten_on_shutdown=_bool_env("EXECUTION_FLATTEN_ON_SHUTDOWN", True),
        execution_broker_rejection_threshold=_int_env("EXECUTION_BROKER_REJECTION_THRESHOLD", 3),
        execution_stream_failure_threshold=_int_env("EXECUTION_STREAM_FAILURE_THRESHOLD", 3),
        execution_reconciliation_failure_threshold=_int_env("EXECUTION_RECONCILIATION_FAILURE_THRESHOLD", 3),
        execution_order_state_failure_threshold=_int_env("EXECUTION_ORDER_STATE_FAILURE_THRESHOLD", 3),
        paper_max_session_loss_pct=_float_env("PAPER_MAX_SESSION_LOSS_PCT", 0.005),
        paper_max_drawdown_pct=_float_env("PAPER_MAX_DRAWDOWN_PCT", 0.0075),
        paper_max_consecutive_losses=_int_env("PAPER_MAX_CONSECUTIVE_LOSSES", 6),
        paper_max_trades_per_day=_int_env("PAPER_MAX_TRADES_PER_DAY", 250),
        max_orders_per_minute=_int_env("MAX_ORDERS_PER_MINUTE", 6),
        paper_max_orders_per_minute=_int_env("PAPER_MAX_ORDERS_PER_MINUTE", 12),
        risk_execution_error_limit=_int_env("RISK_EXECUTION_ERROR_LIMIT", 3),
        max_correlated_exposure_pct=_float_env("MAX_CORRELATED_EXPOSURE_PCT", 0.10),
        experimental_profit_factor_threshold=_float_env("EXPERIMENTAL_PROFIT_FACTOR_THRESHOLD", 1.15),
        experimental_size_multiplier=_float_env("EXPERIMENTAL_SIZE_MULTIPLIER", 0.25),
        enable_partial_profit_tranches=_bool_env("ENABLE_PARTIAL_PROFIT_TRANCHES", True),
        partial_profit_fraction=_float_env("PARTIAL_PROFIT_FRACTION", 0.50),
        partial_profit_runner_target_multiplier=_float_env("PARTIAL_PROFIT_RUNNER_TARGET_MULTIPLIER", 2.0),
        paper_require_ml_model=_bool_env("PAPER_REQUIRE_ML_MODEL", False),
        paper_enable_shadow_model=_bool_env("PAPER_ENABLE_SHADOW_MODEL", True),
        paper_ml_min_advisory_confidence=_float_env("PAPER_ML_MIN_ADVISORY_CONFIDENCE", 0.45),
        paper_ml_max_score_adjustment=_float_env("PAPER_ML_MAX_SCORE_ADJUSTMENT", 5.0),
        enable_macro_context=_bool_env("ENABLE_MACRO_CONTEXT", True),
        macro_context_interval_minutes=_int_env("MACRO_CONTEXT_INTERVAL_MINUTES", 60),
        macro_context_max_age_minutes=_int_env("MACRO_CONTEXT_MAX_AGE_MINUTES", 1440),
        macro_context_headlines_path=os.getenv("MACRO_CONTEXT_HEADLINES_PATH", "data/macro_headlines.csv"),
        macro_context_max_live_score_adjustment=_float_env("MACRO_CONTEXT_MAX_LIVE_SCORE_ADJUSTMENT", 3.0),
        llm_provider=os.getenv("LLM_PROVIDER", "none").lower(),
        llm_base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434"),
        llm_model=os.getenv("LLM_MODEL", "llama3.2:1b"),
        llm_timeout_seconds=_int_env("LLM_TIMEOUT_SECONDS", 60),
        llm_temperature=_float_env("LLM_TEMPERATURE", 0.1),
        llm_max_context_rows=_int_env("LLM_MAX_CONTEXT_ROWS", 80),
        enable_llm_analysis=_bool_env("ENABLE_LLM_ANALYSIS", False),
        enable_llm_macro_context=_bool_env("ENABLE_LLM_MACRO_CONTEXT", False),
        enable_llm_review_coach=_bool_env("ENABLE_LLM_REVIEW_COACH", False),
        enable_llm_training_advice=_bool_env("ENABLE_LLM_TRAINING_ADVICE", False),
        enable_llm_training_labels=_bool_env("ENABLE_LLM_TRAINING_LABELS", False),
        llm_training_label_min_confidence=_float_env("LLM_TRAINING_LABEL_MIN_CONFIDENCE", 0.70),
        enable_llm_live_trading=_bool_env("ENABLE_LLM_LIVE_TRADING", False),
        llm_offline_only=_bool_env("LLM_OFFLINE_ONLY", True),
        fingpt_source_dir=os.getenv("FINGPT_SOURCE_DIR", "FINGPT/FinGPT-1.0.0/fingpt"),
        llm_context_max_sizing_adjustment=_float_env("LLM_CONTEXT_MAX_SIZING_ADJUSTMENT", 0.05),
        llm_news_min_linked_fraction=_float_env("LLM_NEWS_MIN_LINKED_FRACTION", 0.60),
        enable_tradingagents_advisory=_bool_env("ENABLE_TRADINGAGENTS_ADVISORY", True),
        tradingagents_source_dir=os.getenv("TRADINGAGENTS_SOURCE_DIR", "../TradingAgents"),
        tradingagents_advisory_max_age_minutes=_int_env("TRADINGAGENTS_ADVISORY_MAX_AGE_MINUTES", 120),
        tradingagents_min_confidence=_float_env("TRADINGAGENTS_MIN_CONFIDENCE", 0.60),
        tradingagents_max_score_adjustment=_float_env("TRADINGAGENTS_MAX_SCORE_ADJUSTMENT", 3.0),
        tradingagents_max_sizing_adjustment=_float_env("TRADINGAGENTS_MAX_SIZING_ADJUSTMENT", 0.05),
        enable_hourly_csv_export=_bool_env("ENABLE_HOURLY_CSV_EXPORT", True),
        csv_export_interval_minutes=_int_env("CSV_EXPORT_INTERVAL_MINUTES", 60),
        csv_export_dir=os.getenv("CSV_EXPORT_DIR", default_csv_export_dir),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        enable_news_collection=_bool_env("ENABLE_NEWS_COLLECTION", True),
        news_collection_interval_minutes=_int_env("NEWS_COLLECTION_INTERVAL_MINUTES", 60),
        news_symbols=parse_symbols(os.getenv("NEWS_SYMBOLS", "GLD IAU SLV GDX GDXJ UUP TLT IEF SHY SPY QQQ VIXY")),
        enable_macro_series_collection=_bool_env("ENABLE_MACRO_SERIES_COLLECTION", True),
        macro_series_interval_hours=_int_env("MACRO_SERIES_INTERVAL_HOURS", 24),
        fred_api_key=os.getenv("FRED_API_KEY", ""),
        macro_series_ids=parse_symbols(os.getenv("MACRO_SERIES_IDS", "DGS2 DGS10 DFII10 T10YIE DFF FEDFUNDS CPIAUCSL PCEPI PAYEMS UNRATE DCOILWTICO")),
        enable_event_calendar=_bool_env("ENABLE_EVENT_CALENDAR", True),
        economic_calendar_path=os.getenv("ECONOMIC_CALENDAR_PATH", "data/economic_calendar.csv"),
        event_risk_lookahead_minutes=_int_env("EVENT_RISK_LOOKAHEAD_MINUTES", 45),
        event_risk_cooldown_minutes=_int_env("EVENT_RISK_COOLDOWN_MINUTES", 15),
        enable_market_calendar_collection=_bool_env("ENABLE_MARKET_CALENDAR_COLLECTION", True),
        enable_feature_audit_tables=_bool_env("ENABLE_FEATURE_AUDIT_TABLES", True),
        enable_strict_playbooks=_bool_env("ENABLE_STRICT_PLAYBOOKS", True),
        minimum_pattern_quality=_float_env("MINIMUM_PATTERN_QUALITY", 0.58),
        minimum_liquidity_score=_float_env("MINIMUM_LIQUIDITY_SCORE", 0.55),
        minimum_playbook_score=_float_env("MINIMUM_PLAYBOOK_SCORE", 70.0),
        require_proper_break_for_breakout=_bool_env("REQUIRE_PROPER_BREAK_FOR_BREAKOUT", True),
        avoid_midday_chop=_bool_env("AVOID_MIDDAY_CHOP", True),
        avoid_event_risk_trading=_bool_env("AVOID_EVENT_RISK_TRADING", True),
        walk_forward_train_months=_int_env("WALK_FORWARD_TRAIN_MONTHS", 12),
        walk_forward_test_months=_int_env("WALK_FORWARD_TEST_MONTHS", 3),
        promotion_min_profit_factor=_float_env("PROMOTION_MIN_PROFIT_FACTOR", 1.20),
        promotion_min_win_rate=_float_env("PROMOTION_MIN_WIN_RATE", 0.48),
        promotion_max_drawdown=_float_env("PROMOTION_MAX_DRAWDOWN", 0.015),
        promotion_min_trade_count=_int_env("PROMOTION_MIN_TRADE_COUNT", 100),
        promotion_min_profitable_fold_ratio=_float_env("PROMOTION_MIN_PROFITABLE_FOLD_RATIO", 0.60),
        promotion_max_calibration_error=_float_env("PROMOTION_MAX_CALIBRATION_ERROR", 0.15),
        promotion_min_fold_count=_int_env("PROMOTION_MIN_FOLD_COUNT", 3),
        promotion_require_paper_results=_bool_env("PROMOTION_REQUIRE_PAPER_RESULTS", True),
        promotion_min_paper_trade_count=_int_env("PROMOTION_MIN_PAPER_TRADE_COUNT", 30),
        promotion_min_regime_count=_int_env("PROMOTION_MIN_REGIME_COUNT", 2),
        drift_min_predictions=_int_env("DRIFT_MIN_PREDICTIONS", 100),
        drift_min_paper_outcomes=_int_env("DRIFT_MIN_PAPER_OUTCOMES", 30),
        drift_demotion_score=_float_env("DRIFT_DEMOTION_SCORE", 0.35),
        drift_profit_factor_floor_ratio=_float_env("DRIFT_PROFIT_FACTOR_FLOOR_RATIO", 0.70),
        drift_drawdown_limit_multiplier=_float_env("DRIFT_DRAWDOWN_LIMIT_MULTIPLIER", 1.50),
        retrain_after_hour_et=_int_env("RETRAIN_AFTER_HOUR_ET", 20),
        retrain_before_hour_et=_int_env("RETRAIN_BEFORE_HOUR_ET", 8),
        related_symbols=parse_symbols(os.getenv("RELATED_SYMBOLS", "IAU SLV GDX GDXJ UUP TLT IEF SHY SPY QQQ VIXY")),
    )
    settings.validate_safety()
    return settings
