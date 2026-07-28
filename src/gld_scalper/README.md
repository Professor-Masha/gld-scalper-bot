# GLD Scalper Runtime Package

Live trading, persistence, strategy, execution, research, reporting, and orchestration modules.

Return to the [project manual](../../README.md).

## Folder Contract

- Keep runtime code deterministic and testable; Ollama and research jobs may not call broker-order methods.
- Never commit credentials, `.env`, SQLite databases, raw quotes/trades, CSV exports, or downloaded training data.
- Preserve paper, historical, and future live state separation.
- Add or update tests when behavior changes, and update this guide when file ownership changes.

## Files In This Folder

| File | Responsibility |
|---|---|
| [`__init__.py`](../../src/gld_scalper/__init__.py) | Paper-only GLD scalping bot package. |
| [`alpaca_clients.py`](../../src/gld_scalper/alpaca_clients.py) | Python module exposing `get_trading_client`, `get_stock_historical_client`, `get_option_historical_client`, `get_stock_data_stream`. |
| [`backtester.py`](../../src/gld_scalper/backtester.py) | Python module exposing `Backtester`, `calculate_metrics`. |
| [`concurrent_trading.py`](../../src/gld_scalper/concurrent_trading.py) | Python module exposing `populate_concurrent_risk_state`, `broker_position_direction`, `is_bot_managed_order`. |
| [`config.py`](../../src/gld_scalper/config.py) | Typed environment configuration, defaults, validation, data-mode separation, and credential guards. |
| [`data_collector.py`](../../src/gld_scalper/data_collector.py) | Python module exposing `HistoricalDataCollector`. |
| [`database.py`](../../src/gld_scalper/database.py) | SQLite connection owner, schema migrations, transactional repositories, and episode/order/outcome persistence. |
| [`ema_cross_strategy.py`](../../src/gld_scalper/ema_cross_strategy.py) | Python module exposing `EMACrossState`, `EMACrossEvent`, `EMACrossSelection`, `EMACrossEvaluation`. |
| [`entry_quality.py`](../../src/gld_scalper/entry_quality.py) | Python module exposing `EntryGateResult`, `time_of_day_profile`, `EntryQualityGate`, `EntryCooldownPolicy`. |
| [`event_calendar.py`](../../src/gld_scalper/event_calendar.py) | Python module exposing `event_risk_features`. |
| [`execution_engine.py`](../../src/gld_scalper/execution_engine.py) | Converts approved order plans into serialized, idempotent, protected broker intents. |
| [`execution_safety.py`](../../src/gld_scalper/execution_safety.py) | Broker/database reconciliation, bracket grace, residual confirmation, circuit breakers, and safety flattening. |
| [`exit_policy.py`](../../src/gld_scalper/exit_policy.py) | Python module exposing `ExitGeometry`, `economic_breakeven_pct`, `build_exit_geometry`. |
| [`fast_scalp.py`](../../src/gld_scalper/fast_scalp.py) | Python module exposing `FastScalpEvent`, `FastScalpDecision`, `FastScalpEngine`, `FastScalpRuntime`. |
| [`feature_engine.py`](../../src/gld_scalper/feature_engine.py) | Python module exposing `resample_bars`, `build_feature_snapshot`, `build_archive_compatible_features`. |
| [`fingpt_offline.py`](../../src/gld_scalper/fingpt_offline.py) | Python module exposing `FinGPTSourceProfile`, `OfflineResearchLock`, `FinGPTOfflineResearch`, `load_fingpt_source_profile`. |
| [`gold_event_impact.py`](../../src/gld_scalper/gold_event_impact.py) | Python module exposing `build_gold_event_impact`. |
| [`gold_volatility.py`](../../src/gld_scalper/gold_volatility.py) | Python module exposing `build_gold_volatility_features`. |
| [`indicator_engine.py`](../../src/gld_scalper/indicator_engine.py) | Python module exposing `sma`, `ema`, `rsi`, `true_range`. |
| [`llm_analysis.py`](../../src/gld_scalper/llm_analysis.py) | Python module exposing `LLMAnalysisService`, `require_ollama_enabled`. |
| [`llm_provider.py`](../../src/gld_scalper/llm_provider.py) | Python module exposing `LLMError`, `LLMResponse`, `OllamaClient`, `DisabledLLMClient`. |
| [`macro_context.py`](../../src/gld_scalper/macro_context.py) | Python module exposing `MacroContextScheduler`, `MacroContextBuilder`, `macro_context_to_features`, `pretty_macro_context`. |
| [`main.py`](../../src/gld_scalper/main.py) | Command-line composition root that wires settings, databases, clients, services, and commands. |
| [`microstructure.py`](../../src/gld_scalper/microstructure.py) | Python module exposing `build_microstructure_features`. |
| [`models.py`](../../src/gld_scalper/models.py) | Python module exposing `utc_now`, `MarketSignal`, `MLPrediction`, `RiskState`. |
| [`no_trade_learning.py`](../../src/gld_scalper/no_trade_learning.py) | Python module exposing `MissedOpportunityAnalyzer`. |
| [`offline_review.py`](../../src/gld_scalper/offline_review.py) | Python module exposing `EvidenceChunk`, `LocalRAGCoach`, `OfflineReviewerDebate`, `BullCaseAgent`. |
| [`options_intelligence.py`](../../src/gld_scalper/options_intelligence.py) | Python module exposing `GLDOptionsIntelligenceCollector`, `build_options_intelligence`, `options_intelligence_to_features`, `OptionsIntelligenceRuntime`. |
| [`order_blocks.py`](../../src/gld_scalper/order_blocks.py) | Python module exposing `OrderBlockZone`, `analyze_order_blocks`, `detect_order_blocks`, `summarize_order_blocks`. |
| [`order_reconciler.py`](../../src/gld_scalper/order_reconciler.py) | Imports broker order/fill truth and materializes atomic closed-episode outcomes. |
| [`outcome_labeler.py`](../../src/gld_scalper/outcome_labeler.py) | Python module exposing `OutcomeLabelingResult`, `PricePoint`, `MultiHorizonOutcomeLabeler`. |
| [`paper_exploration.py`](../../src/gld_scalper/paper_exploration.py) | Python module exposing `PaperExplorationPolicy`, `exploration_playbook_quality`, `model_rejection_blocks`. |
| [`performance_tracking.py`](../../src/gld_scalper/performance_tracking.py) | Python module exposing `FillCostContext`, `build_fill_cost_context`, `AccountPerformanceTracker`, `run_performance_consistency_audit`. |
| [`position_manager.py`](../../src/gld_scalper/position_manager.py) | Python module exposing `ExitDecision`, `ManagedTrade`, `PositionEvent`, `PositionManager`. |
| [`price_action.py`](../../src/gld_scalper/price_action.py) | Python module exposing `analyze_price_action`. |
| [`reasoning_agents.py`](../../src/gld_scalper/reasoning_agents.py) | Python module exposing `AgentAssessment`, `IndicatorAgent`, `PatternAgent`, `TrendAgent`. |
| [`regime_detector.py`](../../src/gld_scalper/regime_detector.py) | Python module exposing `detect_regime`. |
| [`research_data.py`](../../src/gld_scalper/research_data.py) | Python implementation module. |
| [`risk_engine.py`](../../src/gld_scalper/risk_engine.py) | Python module exposing `OrderPlanRejected`, `RiskEngine`, `order_size_from_notional`. |
| [`rl_environment.py`](../../src/gld_scalper/rl_environment.py) | Python module exposing `RLAction`, `RLStep`, `GLDScalpingEnvironment`, `run_offline_policy_preview`. |
| [`schema.sql`](../../src/gld_scalper/schema.sql) | Canonical SQLite schema for market data, decisions, execution, models, diagnostics, and research. |
| [`shortability.py`](../../src/gld_scalper/shortability.py) | Python module exposing `ShortabilityResult`, `check_asset_shortability`, `can_short`. |
| [`strategy_engine.py`](../../src/gld_scalper/strategy_engine.py) | Python module exposing `StrategyEngine`. |
| [`strategy_playbooks.py`](../../src/gld_scalper/strategy_playbooks.py) | Python module exposing `PlaybookDecision`, `evaluate_playbooks`. |
| [`stream_collector.py`](../../src/gld_scalper/stream_collector.py) | Python module exposing `StockStreamCollector`, `LiveDataStreamRuntime`. |
| [`target_exposure.py`](../../src/gld_scalper/target_exposure.py) | Python module exposing `TargetExposure`, `target_exposure_from_signal`. |
| [`technical_confluence.py`](../../src/gld_scalper/technical_confluence.py) | Python module exposing `FairValueGap`, `TechnicalSetup`, `analyze_technical_market`, `build_fibonacci_features`. |
| [`trade_learning.py`](../../src/gld_scalper/trade_learning.py) | Python module exposing `TradeLearningAnalyzer`. |

## Python Interfaces, Variables, And Linkage

#### `alpaca_clients.py`
**Public interfaces:** `get_trading_client`, `get_stock_historical_client`, `get_option_historical_client`, `get_stock_data_stream`, `health_check`.

#### `backtester.py`
**Public interfaces:** `Backtester`, `calculate_metrics`.

#### `concurrent_trading.py`
**Public interfaces:** `populate_concurrent_risk_state`, `broker_position_direction`, `is_bot_managed_order`.

#### `config.py`
**Public interfaces:** `parse_symbols`, `Settings`, `load_settings`.
**Module constants:** `PROJECT_ROOT`, `DATA_MODES`.

#### `data_collector.py`
**Public interfaces:** `HistoricalDataCollector`.
**Module constants:** `TIMEFRAMES`.

#### `database.py`
**Public interfaces:** `SerializedSQLiteConnection`, `sqlite_write_lock`, `database_path_from_url`, `Database`.
**Module constants:** `_SQLITE_WRITE_LOCK`, `CURRENT_SCHEMA_MIGRATION`, `DATA_TABLES`.

#### `ema_cross_strategy.py`
**Public interfaces:** `EMACrossState`, `EMACrossEvent`, `EMACrossSelection`, `EMACrossEvaluation`, `evaluate_ema_cross_strategy`, `select_ema_cross_events`, `apply_ema_cross_paper_authority`, `resample_completed_session_bars`, `pine_dmi_adx`.
**Module constants:** `NEW_YORK`.

#### `entry_quality.py`
**Public interfaces:** `EntryGateResult`, `time_of_day_profile`, `EntryQualityGate`, `EntryCooldownPolicy`.

#### `event_calendar.py`
**Public interfaces:** `event_risk_features`.
**Module constants:** `HIGH_IMPACT_CATEGORIES`.

#### `execution_engine.py`
**Public interfaces:** `SubmittedEntry`, `SubmissionGroup`, `ExecutionEngine`, `make_client_order_id`.
**Module constants:** `_ORDER_SUBMISSION_LOCK`.

#### `execution_safety.py`
**Public interfaces:** `EntryBlockedError`, `SafetySnapshot`, `ExecutionSafetyState`, `OrderIntentCoordinator`, `ExecutionSafetySupervisor`.
**Module constants:** `_ACTIVE_ORDER_STATUSES`.

#### `exit_policy.py`
**Public interfaces:** `ExitGeometry`, `economic_breakeven_pct`, `build_exit_geometry`.

#### `fast_scalp.py`
**Public interfaces:** `FastScalpEvent`, `FastScalpDecision`, `FastScalpEngine`, `FastScalpRuntime`, `AsyncFastPersistence`.

#### `feature_engine.py`
**Public interfaces:** `resample_bars`, `build_feature_snapshot`, `build_archive_compatible_features`.

#### `fingpt_offline.py`
**Public interfaces:** `FinGPTSourceProfile`, `OfflineResearchLock`, `FinGPTOfflineResearch`, `load_fingpt_source_profile`.

#### `gold_event_impact.py`
**Public interfaces:** `build_gold_event_impact`.
**Module constants:** `INFLATION_TERMS`, `JOBS_TERMS`, `FED_TERMS`, `GDP_TERMS`, `GEOPOLITICAL_TERMS`.

#### `gold_volatility.py`
**Public interfaces:** `build_gold_volatility_features`.

#### `indicator_engine.py`
**Public interfaces:** `sma`, `ema`, `rsi`, `true_range`, `atr`, `rolling_max`, `rolling_min`, `bollinger_bands`, `macd`, `obv`, `money_flow_index`, `accumulation_distribution`, `adx`, `supertrend`, `parabolic_sar`, `compute_indicators`.

#### `llm_analysis.py`
**Public interfaces:** `LLMAnalysisService`, `require_ollama_enabled`.
**Module constants:** `SYSTEM_PROMPT`.

#### `llm_provider.py`
**Public interfaces:** `LLMError`, `LLMResponse`, `OllamaClient`, `DisabledLLMClient`, `make_llm_client`.

#### `macro_context.py`
**Public interfaces:** `MacroContextScheduler`, `MacroContextBuilder`, `macro_context_to_features`, `pretty_macro_context`.
**Module constants:** `POSITIVE_GOLD_TERMS`, `NEGATIVE_GOLD_TERMS`, `USD_POSITIVE_TERMS`, `USD_NEGATIVE_TERMS`, `RATES_HAWKISH_TERMS`, `RATES_DOVISH_TERMS`, `RISK_OFF_TERMS`, `EVENT_RISK_TERMS`.

#### `main.py`
**Public interfaces:** `init_db_command`, `backfill_command`, `train_command`, `build_transformer_dataset_command`, `train_transformer_command`, `transformer_train_loop_command`, `stop_transformer_training_command`, `transformer_status_command`, `evaluate_transformer_paper_command`, `promote_transformer_command`, `build_ml_archive_command`, `train_archive_command`, `train_loop_command`, `training_loop_status_command`, `stop_training_loop_command`, `ml_drift_report_command`, `walk_forward_command`, `backtest_command`.

#### `microstructure.py`
**Public interfaces:** `build_microstructure_features`.

#### `models.py`
**Public interfaces:** `utc_now`, `MarketSignal`, `MLPrediction`, `RiskState`, `OrderPlan`, `BacktestTrade`, `BacktestResult`.

#### `no_trade_learning.py`
**Public interfaces:** `MissedOpportunityAnalyzer`.
**Module constants:** `MISSED_LONG`, `MISSED_SHORT`, `VALID_NO_TRADE`.

#### `offline_review.py`
**Public interfaces:** `EvidenceChunk`, `LocalRAGCoach`, `OfflineReviewerDebate`, `BullCaseAgent`, `BearCaseAgent`, `RiskCriticAgent`, `ExecutionCriticAgent`, `JournalReviewerAgent`.

#### `options_intelligence.py`
**Public interfaces:** `GLDOptionsIntelligenceCollector`, `build_options_intelligence`, `options_intelligence_to_features`, `OptionsIntelligenceRuntime`, `parse_occ_contract`.
**Module constants:** `_OCC_PATTERN`.

#### `order_blocks.py`
**Public interfaces:** `OrderBlockZone`, `analyze_order_blocks`, `detect_order_blocks`, `summarize_order_blocks`, `live_order_block_retest`.

#### `order_reconciler.py`
**Public interfaces:** `BrokerOrderNode`, `PaperOrderReconciler`.
**Module constants:** `FILLED_STATUS`.

#### `outcome_labeler.py`
**Public interfaces:** `OutcomeLabelingResult`, `PricePoint`, `MultiHorizonOutcomeLabeler`.
**Module constants:** `HORIZONS`, `VALID_SOURCES`.

#### `paper_exploration.py`
**Public interfaces:** `PaperExplorationPolicy`, `exploration_playbook_quality`, `model_rejection_blocks`.

#### `performance_tracking.py`
**Public interfaces:** `FillCostContext`, `build_fill_cost_context`, `AccountPerformanceTracker`, `run_performance_consistency_audit`.

#### `position_manager.py`
**Public interfaces:** `ExitDecision`, `ManagedTrade`, `PositionEvent`, `PositionManager`, `DynamicPositionRuntime`.
**Module constants:** `TERMINAL_ORDER_STATUSES`.

#### `price_action.py`
**Public interfaces:** `analyze_price_action`.

#### `reasoning_agents.py`
**Public interfaces:** `AgentAssessment`, `IndicatorAgent`, `PatternAgent`, `TrendAgent`, `OrderBlockAgent`, `OptionsAgent`, `RiskAgent`, `combine_reasoning`.

#### `regime_detector.py`
**Public interfaces:** `detect_regime`.
**Module constants:** `AVOID_REGIMES`.

#### `risk_engine.py`
**Public interfaces:** `OrderPlanRejected`, `RiskEngine`, `order_size_from_notional`.

#### `rl_environment.py`
**Public interfaces:** `RLAction`, `RLStep`, `GLDScalpingEnvironment`, `run_offline_policy_preview`.

#### `shortability.py`
**Public interfaces:** `ShortabilityResult`, `check_asset_shortability`, `can_short`.

#### `strategy_engine.py`
**Public interfaces:** `StrategyEngine`.

#### `strategy_playbooks.py`
**Public interfaces:** `PlaybookDecision`, `evaluate_playbooks`.

#### `stream_collector.py`
**Public interfaces:** `StockStreamCollector`, `LiveDataStreamRuntime`.

#### `target_exposure.py`
**Public interfaces:** `TargetExposure`, `target_exposure_from_signal`.

#### `technical_confluence.py`
**Public interfaces:** `FairValueGap`, `TechnicalSetup`, `analyze_technical_market`, `build_fibonacci_features`, `detect_fair_value_gaps`, `build_rsi_divergence_features`, `summarize_fair_value_gaps`, `grouped_indicator_families`, `select_technical_setup`.
**Module constants:** `FIB_RETRACEMENTS`, `FIB_EXTENSIONS`.

#### `trade_learning.py`
**Public interfaces:** `TradeLearningAnalyzer`.

The interface list is generated from public top-level classes/functions and uppercase module constants. Read type annotations and tests before changing semantics; private helpers are implementation details but can still participate in safety invariants.

## Linkage And Change Discipline

1. Start at the composition root in `src/gld_scalper/main.py` or the invoking tool/script.
2. Follow typed settings from `config.py`; environment values should not be read ad hoc elsewhere.
3. Follow persistence through `database.py` and `schema.sql`; multi-row execution state must remain transactional.
4. Follow behavioral evidence into the matching tests before changing a public interface.
5. Run focused tests first, then the complete suite. Paper execution is the final verification stage, not the first.

## Data And Security

Tracked code and promoted model memory may be committed. Raw market data, account data, exports, logs, API keys, and local Ollama model blobs stay outside Git. Model artifacts must retain their checksum, manifest, training range, exact feature profile, metrics, and rollback lineage.

---

Copyright (c) Mashcorp. GLD Scalper Bot is a Mashcorp project.
