# Automated Test Suite

Unit and integration-style regression tests for safety, strategy, persistence, and learning.

Return to the [project manual](../README.md).

## Folder Contract

- Keep runtime code deterministic and testable; Ollama and research jobs may not call broker-order methods.
- Never commit credentials, `.env`, SQLite databases, raw quotes/trades, CSV exports, or downloaded training data.
- Preserve paper, historical, and future live state separation.
- Add or update tests when behavior changes, and update this guide when file ownership changes.

## Files In This Folder

| File | Responsibility |
|---|---|
| [`test_backtester.py`](../tests/test_backtester.py) | Regression tests for backtester. |
| [`test_config.py`](../tests/test_config.py) | Regression tests for config. |
| [`test_continual_training.py`](../tests/test_continual_training.py) | Regression tests for continual training. |
| [`test_csv_exporter.py`](../tests/test_csv_exporter.py) | Regression tests for csv exporter. |
| [`test_database.py`](../tests/test_database.py) | Regression tests for database. |
| [`test_ema_cross_strategy.py`](../tests/test_ema_cross_strategy.py) | Regression tests for ema cross strategy. |
| [`test_execution_engine.py`](../tests/test_execution_engine.py) | Regression tests for execution engine. |
| [`test_execution_safety.py`](../tests/test_execution_safety.py) | Regression tests for execution safety. |
| [`test_fast_scalp.py`](../tests/test_fast_scalp.py) | Regression tests for fast scalp. |
| [`test_feature_compatibility.py`](../tests/test_feature_compatibility.py) | Regression tests for feature compatibility. |
| [`test_hybrid_agent_council.py`](../tests/test_hybrid_agent_council.py) | Regression tests for deterministic council authority, bounded advisory persistence, and expiry. |
| [`test_indicators.py`](../tests/test_indicators.py) | Regression tests for indicators. |
| [`test_kimi_provider.py`](../tests/test_kimi_provider.py) | Kimi JSON protocol, Tier0 budget, secret-redaction, and safety-validation tests. |
| [`test_llm_analysis.py`](../tests/test_llm_analysis.py) | Regression tests for llm analysis. |
| [`test_macro_context.py`](../tests/test_macro_context.py) | Regression tests for macro context. |
| [`test_market_quality_features.py`](../tests/test_market_quality_features.py) | Regression tests for market quality features. |
| [`test_ml_training_guards.py`](../tests/test_ml_training_guards.py) | Regression tests for ml training guards. |
| [`test_ml_upgrade.py`](../tests/test_ml_upgrade.py) | Regression tests for ml upgrade. |
| [`test_no_trade_learning.py`](../tests/test_no_trade_learning.py) | Regression tests for no trade learning. |
| [`test_offline_review.py`](../tests/test_offline_review.py) | Regression tests for offline review. |
| [`test_options_intelligence.py`](../tests/test_options_intelligence.py) | Regression tests for options intelligence. |
| [`test_order_blocks.py`](../tests/test_order_blocks.py) | Regression tests for order blocks. |
| [`test_order_reconciler.py`](../tests/test_order_reconciler.py) | Regression tests for order reconciler. |
| [`test_outcome_labeler.py`](../tests/test_outcome_labeler.py) | Regression tests for outcome labeler. |
| [`test_phase2_phase3.py`](../tests/test_phase2_phase3.py) | Regression tests for phase2 phase3. |
| [`test_phase4_phase7.py`](../tests/test_phase4_phase7.py) | Regression tests for phase4 phase7. |
| [`test_position_manager.py`](../tests/test_position_manager.py) | Regression tests for position manager. |
| [`test_price_action.py`](../tests/test_price_action.py) | Regression tests for price action. |
| [`test_retraining_scheduler.py`](../tests/test_retraining_scheduler.py) | Regression tests for retraining scheduler. |
| [`test_risk_engine.py`](../tests/test_risk_engine.py) | Regression tests for risk engine. |
| [`test_rl_and_target_exposure.py`](../tests/test_rl_and_target_exposure.py) | Regression tests for rl and target exposure. |
| [`test_shortability.py`](../tests/test_shortability.py) | Regression tests for shortability. |
| [`test_strategy_engine.py`](../tests/test_strategy_engine.py) | Regression tests for strategy engine. |
| [`test_stream_collector.py`](../tests/test_stream_collector.py) | Regression tests for stream collector. |
| [`test_trade_learning.py`](../tests/test_trade_learning.py) | Regression tests for trade learning. |
| [`test_transformer_continual_confluence.py`](../tests/test_transformer_continual_confluence.py) | Regression tests for transformer continual confluence. |
| [`test_transformer_upgrade.py`](../tests/test_transformer_upgrade.py) | Regression tests for transformer upgrade. |

## Python Interfaces, Variables, And Linkage

#### `test_backtester.py`
**Public interfaces:** `test_backtest_uses_next_bar_for_entry`, `test_backtest_resets_daily_trade_limit_each_session`.
**Internal dependencies:** `gld_scalper.backtester`, `gld_scalper.config`, `gld_scalper.database`.

#### `test_config.py`
**Public interfaces:** `test_default_settings_keep_paper_data_separate`, `test_load_settings_uses_mode_specific_defaults`, `test_load_settings_rejects_shared_database_path`.
**Internal dependencies:** `gld_scalper.config`.

#### `test_continual_training.py`
**Public interfaces:** `test_prepare_experiment_records_selects_horizon_playbook_and_weights_paper`, `test_threshold_optimizer_uses_after_cost_outcomes`, `test_training_lock_is_exclusive_and_releases`, `test_continual_runner_remembers_completed_fingerprint`, `test_historical_search_round_changes_experiment_identity`, `test_paper_checkpoint_ignores_unavailable_outcome_markers`, `test_continuous_historical_mode_runs_new_rounds_without_paper_labels`, `test_continuous_historical_mode_stops_after_patience`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.ml.continual_training`, `gld_scalper.ml.evaluator`.

#### `test_csv_exporter.py`
**Public interfaces:** `test_export_database_to_csv_creates_timestamped_and_latest_dirs`, `test_export_database_to_csv_can_filter_to_collection_window`, `test_hourly_scheduler_waits_one_interval_before_exporting`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.reports.csv_exporter`.

#### `test_database.py`
**Public interfaces:** `test_database_upsert_prevents_duplicate_bars`, `test_failed_execution_episode_records_terminal_close_reason`, `test_database_records_current_migration_and_skips_repeat_backfill`, `test_database_serializes_concurrent_thread_writers`, `test_sqlite_log_handler_formats_exception_traceback`, `test_existing_database_migrates_parent_order_column_before_index`, `test_existing_database_migrates_multi_horizon_outcome_columns_before_index`, `test_database_migration_repairs_close_legs_misclassified_as_active_episodes`, `test_fetch_latest_bars_returns_newest_in_ascending_order`, `test_clear_all_data_removes_collected_rows`, `test_trading_journal_is_reset_with_collected_data`, `test_order_block_and_options_intelligence_round_trip`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.utils.logging_utils`.

#### `test_ema_cross_strategy.py`
**Public interfaces:** `test_completed_bar_cross_matches_filtered_long_signal`, `test_adx_filter_records_cross_but_blocks_eligibility`, `test_higher_timeframe_uses_session_anchor_and_excludes_partial_bar`, `test_selection_merges_same_direction_and_defers_lower_timeframe_conflict`, `test_paper_authority_converts_confirmed_cross_to_controlled_probe`, `test_paper_authority_never_overrides_a_playbook_safety_block`, `test_database_deduplicates_cross_and_tracks_execution`, `test_ema_cross_event_receives_multi_horizon_learning_labels`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.ema_cross_strategy`, `gld_scalper.models`, `gld_scalper.outcome_labeler`.

#### `test_execution_engine.py`
**Public interfaces:** `test_submission_gate_prevents_minute_and_fast_paths_from_overlapping`, `test_paper_learning_gate_allows_same_direction_tranches_and_enforces_cap`, `test_paper_learning_gate_rejects_opposite_direction_tranche`, `test_partial_profit_submission_uses_two_independently_protected_tranches`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.execution_engine`, `gld_scalper.models`.

#### `test_execution_safety.py`
**Public interfaces:** `FakeSafetyClient`, `test_coordinator_deduplicates_entry_by_client_order_id`, `test_startup_flattens_unprotected_residual_position_before_entries`, `test_new_bracket_position_gets_grace_and_second_check_can_clear_residual`, `test_close_window_freezes_new_entries`, `test_direction_switch_cancels_orders_flattens_and_reconciles`, `test_stuck_root_order_is_requeried_and_canceled`, `test_circuit_breaker_latches_after_configured_failures`, `test_failed_cancel_can_retry_without_weakening_entry_idempotency`, `test_episode_accounting_closes_atomically_after_exit_fill`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.execution_safety`.

#### `test_fast_scalp.py`
**Public interfaces:** `test_fast_scalp_detects_clean_breakout_long`, `test_fast_scalp_blocks_spread_expansion`, `test_paper_shadow_ml_cannot_reverse_a_fast_setup`, `test_fast_runtime_treats_order_plan_rejection_as_no_trade`, `test_fast_scalp_paper_learning_mode_does_not_manufacture_neutral_probe`, `test_fast_paper_exploration_promotes_only_a_structured_candidate`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.fast_scalp`, `gld_scalper.models`, `gld_scalper.paper_exploration`, `gld_scalper.risk_engine`, `gld_scalper.utils.time_utils`.

#### `test_feature_compatibility.py`
**Public interfaces:** `test_live_archive_features_match_training_formulas`, `test_live_snapshot_includes_model_quote_depth_inputs`.
**Internal dependencies:** `gld_scalper.feature_engine`, `gld_scalper.ml.archive_dataset`.

#### `test_hybrid_agent_council.py`
**Public interfaces:** `ScriptedClient`, `test_decision_council_builds_auditable_long_consensus`, `test_decision_council_gives_stale_data_absolute_priority`, `test_tradingagents_advisory_is_bounded_persisted_and_expires`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.decision_council`, `gld_scalper.tradingagents_advisory`.

#### `test_indicators.py`
**Public interfaces:** `test_ema_calculation`, `test_rsi_calculation_on_uptrend`, `test_atr_calculation`.
**Internal dependencies:** `gld_scalper.indicator_engine`.

#### `test_kimi_provider.py`
**Public interfaces:** regression tests for OpenAI-compatible request construction, API usage reconciliation, rolling-budget refusal, official endpoint enforcement, secret redaction, provider selection, offline-only enforcement, and redacted status output.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.kimi_tier0`, `gld_scalper.llm_analysis`, `gld_scalper.llm_provider`.

#### `test_llm_analysis.py`
**Public interfaces:** `FakeLLMClient`, `test_llm_data_analysis_persists_review`, `test_llm_macro_context_persists_context`, `test_llm_training_advice_and_labels_feed_dataset_when_enabled`, `test_llm_training_advice_persists_advice`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.llm_analysis`, `gld_scalper.llm_provider`, `gld_scalper.ml.dataset_builder`.

#### `test_macro_context.py`
**Public interfaces:** `test_macro_context_builds_bullish_gold_bias_from_headlines_and_proxies`, `test_macro_context_to_features_defaults_when_missing`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.macro_context`.

#### `test_market_quality_features.py`
**Public interfaces:** `test_microstructure_scores_liquid_tight_market`, `test_gold_volatility_detects_opening_drive_segment`, `test_reasoning_agents_confirm_clean_long_setup`.
**Internal dependencies:** `gld_scalper.gold_volatility`, `gld_scalper.microstructure`, `gld_scalper.reasoning_agents`.

#### `test_ml_training_guards.py`
**Public interfaces:** `test_label_quality_rejects_no_trade_only`, `test_train_rejects_no_trade_only_dataset`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.ml.dataset_builder`, `gld_scalper.ml.trainer`, `gld_scalper.utils.time_utils`.

#### `test_ml_upgrade.py`
**Public interfaces:** `FixedProbabilityModel`, `test_trading_metrics_use_predicted_actions_and_realized_returns`, `test_legacy_validation_wrapper_does_not_invent_profit`, `test_policy_predictions_apply_live_confidence_and_margin_abstention`, `test_cost_aware_label_requires_move_to_clear_spread_and_slippage`, `test_holdout_split_purges_neighboring_training_rows`, `test_predictor_abstains_when_live_quote_is_stale`, `test_predictor_loads_eligible_candidate_as_paper_shadow_without_promoting`, `test_strict_promotion_requires_walk_forward_proof`, `test_archive_builder_reads_bars_and_creates_cost_aware_records`, `test_walk_forward_uses_requested_candidate_and_feature_columns`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.ml.archive_dataset`, `gld_scalper.ml.evaluator`, `gld_scalper.ml.model_registry`, `gld_scalper.ml.predictor`, `gld_scalper.ml.trainer`, `gld_scalper.ml.walk_forward`.

#### `test_no_trade_learning.py`
**Public interfaces:** `test_missed_opportunity_analyzer_labels_clean_skipped_long`, `test_database_migration_adds_journal_context_columns`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.no_trade_learning`.

#### `test_offline_review.py`
**Public interfaces:** `test_local_rag_coach_persists_review`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.offline_review`.

#### `test_options_intelligence.py`
**Public interfaces:** `test_occ_contract_parser_extracts_type_expiry_and_strike`, `test_options_intelligence_is_bounded_and_bullish_for_call_pressure`, `test_stale_options_context_becomes_neutral`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.options_intelligence`.

#### `test_order_blocks.py`
**Public interfaces:** `test_detects_confirmed_bullish_order_block_and_retest`, `test_live_order_block_retest_uses_current_midpoint`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.order_blocks`.

#### `test_order_reconciler.py`
**Public interfaces:** `FakeTradingClient`, `test_reconciler_records_fills_outcome_and_journal`, `test_reconciler_keeps_concurrent_brackets_as_separate_learning_episodes`, `test_standalone_closing_leg_never_becomes_active_direction_episode`, `test_closed_atomic_episode_materializes_one_root_outcome_with_metadata`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.order_reconciler`.

#### `test_outcome_labeler.py`
**Public interfaces:** `test_labels_signal_and_fast_decision_at_all_horizons_from_snapshots`, `test_uses_last_completed_one_minute_bar_without_lookahead`, `test_partial_label_is_not_retried_unless_data_repair_is_requested`, `test_unavailable_decision_is_audited_without_blocking_future_batches`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.ml.dataset_builder`, `gld_scalper.outcome_labeler`.

#### `test_phase2_phase3.py`
**Public interfaces:** `test_fast_entry_gate_blocks_poor_liquidity_regime`, `test_post_loss_cooldown_is_independent_per_strategy_path`, `test_fill_cost_context_uses_quote_and_reports_each_component`, `test_performance_report_aggregates_partial_tranches_into_root_episode`, `test_account_tracker_records_equity_unrealized_and_drawdown_baseline`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.entry_quality`, `gld_scalper.performance_tracking`, `gld_scalper.reports.performance_report`.

#### `test_phase4_phase7.py`
**Public interfaces:** `FixedProbabilityModel`, `FakeNewsClient`, `test_economic_breakeven_includes_spread_slippage_fees_and_buffer`, `test_playbook_exit_geometry_bounds_stop_and_requires_reward_for_risk`, `test_dynamic_exit_preserves_profit_after_large_mfe_giveback`, `test_session_close_management_tightens_then_reduces_then_flattens`, `test_experimental_paper_size_stays_small_until_model_and_market_are_validated`, `test_scopes_separate_fast_minute_news_and_playbook_models`, `test_tampered_fitted_model_state_is_not_loaded`, `test_drift_demotes_champion_and_keeps_history`, `test_champions_are_versioned_and_can_be_rolled_back`, `test_paper_drift_performance_uses_episode_returns_not_dollar_drawdown`, `test_exit_model_waits_for_trustworthy_completed_outcomes`, `test_news_sentiment_requires_both_price_and_spread_linkage`, `test_local_fingpt_profile_fingerprints_only_available_framework_files`, `test_llm_live_order_path_configuration_is_rejected`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.exit_policy`, `gld_scalper.fingpt_offline`, `gld_scalper.llm_analysis`, `gld_scalper.llm_provider`, `gld_scalper.ml.drift`, `gld_scalper.ml.exit_trainer`, `gld_scalper.ml.model_registry`, `gld_scalper.ml.predictor`, `gld_scalper.ml.scopes`, `gld_scalper.models`.

#### `test_position_manager.py`
**Public interfaces:** `test_broker_order_discovery_requires_open_protective_legs`, `test_broker_order_discovery_rejects_standalone_closing_leg`, `test_negative_trade_gets_recovery_room_without_invalidation`, `test_negative_trade_exits_only_after_required_invalidation_votes`, `test_profitable_trade_arms_breakeven_before_trailing`, `test_profitable_trade_after_max_holding_requests_protected_exit`, `test_price_snapshot_is_compact_one_row_per_second`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.position_manager`.

#### `test_price_action.py`
**Public interfaces:** `test_price_action_classifies_proper_break_up`, `test_price_action_classifies_false_break_up`.
**Internal dependencies:** `gld_scalper.price_action`.

#### `test_retraining_scheduler.py`
**Public interfaces:** `test_retraining_scheduler_disabled_does_not_start`, `test_retraining_scheduler_skips_regular_session_when_configured`, `test_retraining_scheduler_requires_clean_closed_episodes`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.ml.retraining_scheduler`.

#### `test_risk_engine.py`
**Public interfaces:** `test_risk_blocks_daily_loss`, `test_risk_blocks_wide_spread`, `test_risk_blocks_stale_data`, `test_risk_blocks_disconnected_websocket`, `test_risk_blocks_very_low_liquidity_score`, `test_order_sizing_from_notional`, `test_build_order_plan_uses_whole_shares`, `test_paper_learning_mode_keeps_session_safety_limits`, `test_paper_learning_mode_uses_tight_small_probe_bracket_without_dynamic_management`, `test_dynamic_position_management_uses_bounded_structural_stop_and_reward_target`, `test_paper_learning_risk_allows_same_direction_scaling_only_below_cap`, `test_paper_learning_rejects_spread_that_consumes_stop_distance`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.models`, `gld_scalper.risk_engine`, `gld_scalper.utils.time_utils`.

#### `test_rl_and_target_exposure.py`
**Public interfaces:** `test_rl_environment_rewards_long_in_rising_market`, `test_rl_preview_persists_experiment`, `test_target_exposure_reflects_direction_and_macro_alignment`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.models`, `gld_scalper.rl_environment`, `gld_scalper.target_exposure`.

#### `test_shortability.py`
**Public interfaces:** `Asset`, `test_short_blocked_when_asset_not_shortable`.
**Internal dependencies:** `gld_scalper.shortability`.

#### `test_strategy_engine.py`
**Public interfaces:** `test_bullish_signal_generation`, `test_paper_ml_advice_is_bounded_and_adds_to_matching_direction`, `test_required_paper_ml_profile_blocks_rule_signal_when_features_are_incompatible`, `test_bearish_signal_generation`, `test_no_trade_decision`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.strategy_engine`.

#### `test_stream_collector.py`
**Public interfaces:** `test_stream_health_reports_counts_ages_and_stale_reason`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.stream_collector`, `gld_scalper.utils.time_utils`.

#### `test_trade_learning.py`
**Public interfaces:** `test_completed_trade_becomes_review_and_supervised_label`, `test_paper_exploration_selects_near_valid_setup_and_uses_small_size`, `test_paper_exploration_never_bypasses_stale_stream`, `test_paper_exploration_requires_both_fresh_quote_and_trade`, `test_paper_learning_mode_keeps_event_risk_as_no_trade`, `test_paper_learning_zero_daily_limit_selects_and_records_propensity`, `test_paper_learning_false_break_does_not_require_proper_break`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.models`, `gld_scalper.paper_exploration`, `gld_scalper.risk_engine`, `gld_scalper.trade_learning`.

#### `test_transformer_continual_confluence.py`
**Public interfaces:** `test_auto_fibonacci_uses_confirmed_pivots_and_extended_levels`, `test_fair_value_gap_lifecycle_detects_partial_fill`, `test_rsi_divergence_is_causal_and_structured`, `test_grouped_families_cap_correlated_indicators_to_one_vote_family`, `test_event_direction_requires_post_release_tape_confirmation`, `test_bounded_transformer_cannot_originate_trade`, `test_paper_champion_can_recommend_but_only_with_confluence`, `test_transformer_tables_and_scope_parser`, `test_historical_replay_and_paper_artifacts_are_combined_chronologically`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.gold_event_impact`, `gld_scalper.ml.transformer_authority`, `gld_scalper.ml.transformer_continual`, `gld_scalper.ml.transformer_dataset`, `gld_scalper.ml.transformer_runtime`, `gld_scalper.models`, `gld_scalper.technical_confluence`.

#### `test_transformer_upgrade.py`
**Public interfaces:** `test_transformer_scope_defaults_are_independent`, `test_raw_minute_builder_creates_memmap_sequence_artifact`, `test_transformer_predictions_are_persisted_and_exportable`, `test_paper_evaluator_updates_only_matching_transformer_version`, `test_exact_random_forest_baseline_is_mandatory_for_transformer_promotion`, `test_shadow_runtime_never_blocks_when_not_started`, `test_compact_transformer_masks_padding_and_returns_all_heads`, `test_tiny_offline_training_exports_reloadable_torchscript`.
**Internal dependencies:** `gld_scalper.config`, `gld_scalper.database`, `gld_scalper.ml.model_registry`, `gld_scalper.ml.transformer_dataset`, `gld_scalper.ml.transformer_evaluation`, `gld_scalper.ml.transformer_runtime`.

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
