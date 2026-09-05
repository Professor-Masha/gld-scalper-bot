from __future__ import annotations

import argparse
import json
import logging
import shutil
import time
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Sequence

from .backtester import Backtester
from .alpaca_clients import get_trading_client
from .config import PROJECT_ROOT, load_settings, parse_symbols
from .concurrent_trading import populate_concurrent_risk_state
from .data_collector import HistoricalDataCollector
from .database import Database
from .decision_council import run_decision_council
from .ema_cross_strategy import (
    apply_ema_cross_paper_authority,
    evaluate_ema_cross_strategy,
    select_ema_cross_events,
)
from .execution_engine import ExecutionEngine, make_client_order_id
from .execution_latency import ExecutionLatencyTracker
from .entry_quality import EntryCooldownPolicy, EntryQualityGate, time_of_day_profile
from .execution_safety import (
    EntryBlockedError,
    ExecutionSafetyState,
    ExecutionSafetySupervisor,
    OrderIntentCoordinator,
)
from .event_calendar import event_risk_features
from .fast_scalp import FastScalpRuntime
from .feature_engine import build_archive_compatible_features, build_feature_snapshot
from .fingpt_offline import FinGPTOfflineResearch
from .gold_volatility import build_gold_volatility_features
from .gold_event_impact import build_gold_event_impact
from .llm_analysis import LLMAnalysisService, require_offline_llm_enabled
from .kimi_tier0 import kimi_tier0_status
from .macro_context import MacroContextBuilder, MacroContextScheduler, macro_context_to_features, pretty_macro_context
from .microstructure import build_microstructure_features
from .ml.archive_dataset import build_archive_training_records, save_archive_training_artifact
from .ml.continual_training import ContinualTrainingRunner
from .ml.drift import generate_drift_report
from .ml.exit_trainer import train_exit_candidate
from .ml.model_registry import ModelRegistry
from .ml.predictor import Predictor
from .ml.retraining_scheduler import SafeRetrainingScheduler
from .ml.trainer import train_candidate_model
from .ml.transformer_dataset import build_transformer_sequence_artifact
from .ml.transformer_evaluation import evaluate_transformer_paper_models
from .ml.transformer_runtime import AsyncTransformerShadowRuntime
from .ml.transformer_authority import apply_transformer_to_signal
from .ml.transformer_continual import TransformerContinualTrainingRunner, parse_scope_artifacts
from .ml.transformer_trainer import TransformerTrainingOptions, train_transformer_candidate
from .ml.walk_forward import run_walk_forward_validation, save_walk_forward_experiment
from .models import MLPrediction, MarketSignal, RiskState
from .no_trade_learning import MissedOpportunityAnalyzer
from .offline_review import LocalRAGCoach
from .options_intelligence import OptionsIntelligenceRuntime, options_intelligence_to_features
from .order_blocks import analyze_order_blocks
from .order_reconciler import PaperOrderReconciler
from .broker_order_stream import BrokerOrderUpdateRuntime
from .outcome_labeler import MultiHorizonOutcomeLabeler
from .paper_exploration import PaperExplorationPolicy, model_rejection_blocks
from .performance_tracking import AccountPerformanceTracker, run_performance_consistency_audit
from .position_manager import DynamicPositionRuntime
from .price_action import analyze_price_action
from .reasoning_agents import combine_reasoning
from .research_data import ResearchDataCollector, ResearchDataScheduler
from .reports.csv_exporter import HourlyCSVExportScheduler, export_database_to_csv
from .reports.daily_report import generate_daily_report
from .risk_engine import OrderPlanRejected, RiskEngine
from .rl_environment import run_offline_policy_preview
from .shortability import ShortabilityResult, check_asset_shortability
from .strategy_playbooks import evaluate_playbooks
from .strategy_engine import StrategyEngine
from .stream_collector import LiveDataStreamRuntime
from .target_exposure import target_exposure_from_signal
from .technical_confluence import analyze_technical_market
from .tradingagents_advisory import TradingAgentsAdvisoryService, agent_advisory_to_features
from .utils.logging_utils import configure_logging
from .utils.time_utils import ensure_utc, market_session, parse_date, seconds_until_next_minute, utc_now

logger = logging.getLogger(__name__)


def init_db_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    db.log_event("INFO", __name__, "db_initialized", "SQLite database initialized", {"path": str(db.path)})
    print(f"Initialized database at {db.path}")


def backfill_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    collector = HistoricalDataCollector(settings, db)
    symbols = parse_symbols(args.symbols)
    inserted = collector.backfill(symbols=symbols, days=args.days)
    print(json.dumps(inserted, indent=2, sort_keys=True))


def train_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    result = train_candidate_model(db, settings, lookback_days=args.lookback_days, use_llm_labels=args.use_llm_labels)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def build_transformer_dataset_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    database_path = Path(args.database).resolve() if args.database else settings.database_path
    if not database_path.exists():
        raise RuntimeError(f"Transformer source database not found: {database_path}")
    source_database = Database(
        database_url=f"sqlite:///{database_path}",
        settings=settings,
        read_only=True,
    )
    try:
        result = build_transformer_sequence_artifact(
            source_database,
            settings,
            scope=args.scope,
            start=_parse_cli_datetime(args.start),
            end=_parse_cli_datetime(args.end),
            output=args.output,
            source=args.source,
            sequence_length=args.sequence_length,
            window_seconds=args.window_seconds,
            stride=args.stride,
            max_samples=args.max_samples,
            max_features=args.max_features,
            overwrite=args.overwrite,
        )
    finally:
        source_database.close()
    print(json.dumps(asdict(result), indent=2, sort_keys=True, default=str))


def train_transformer_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    database = Database(settings=settings)
    database.init_db()
    configure_logging(settings.log_level, database=database)
    options = TransformerTrainingOptions(
        d_model=args.d_model,
        num_layers=args.layers,
        nhead=4,
        dim_feedforward=args.feedforward_dimension,
        dropout=args.dropout,
        batch_size=args.batch_size,
        epochs=args.epochs,
        learning_rate=args.learning_rate,
        weight_decay=args.weight_decay,
        patience=args.patience,
        walk_forward_folds=args.walk_forward_folds,
        walk_forward_epochs=args.walk_forward_epochs,
        minimum_confidence=args.minimum_confidence,
        minimum_margin=args.minimum_margin,
        export_onnx=args.export_onnx,
        random_seed=args.seed,
    )
    result = train_transformer_candidate(
        database,
        settings,
        artifact_path=Path(args.artifact).resolve(),
        options=options,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def transformer_train_loop_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    database = Database(settings=settings)
    database.init_db()
    configure_logging(settings.log_level, database=database)
    artifacts = parse_scope_artifacts(args.artifact)
    state_root = PROJECT_ROOT / "data" / settings.data_mode / "ml_training" / "transformer" / "continual"
    stop_path = state_root / "STOP_TRANSFORMER_TRAINING"
    if args.clear_stop:
        stop_path.unlink(missing_ok=True)
    runner = TransformerContinualTrainingRunner(
        database,
        settings,
        historical_artifacts=artifacts,
        state_root=state_root,
        epochs=args.epochs,
        walk_forward_epochs=args.walk_forward_epochs,
        batch_size=args.batch_size,
        maximum_paper_samples=args.maximum_paper_samples,
    )
    result = runner.run(
        watch=args.watch,
        interval_minutes=args.interval_minutes,
        maximum_cycles=args.maximum_cycles,
        no_improvement_patience=args.no_improvement_patience,
        minimum_improvement=args.minimum_improvement,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def stop_transformer_training_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    path = PROJECT_ROOT / "data" / settings.data_mode / "ml_training" / "transformer" / "continual" / "STOP_TRANSFORMER_TRAINING"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"stop requested {utc_now().isoformat()}\n", encoding="ascii")
    print(json.dumps({"status": "stop_requested", "path": str(path)}, indent=2))


def transformer_status_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    database = Database(settings=settings)
    if not database.path.exists():
        database.init_db()
    models = [
        dict(row)
        for row in database.conn.execute(
            """
            SELECT model_version, model_scope, status, created_at, promoted_at,
                   training_data_start, training_data_end, metrics_json, path
            FROM model_versions WHERE model_type LIKE 'causal_transformer_%'
            ORDER BY datetime(created_at) DESC, id DESC
            """
        ).fetchall()
    ]
    for model in models:
        metrics = json.loads(model.pop("metrics_json") or "{}")
        model["summary"] = {
            "holdout_net_return": (metrics.get("holdout") or {}).get("net_return"),
            "walk_forward_net_return": (metrics.get("walk_forward") or {}).get("aggregate", {}).get("net_return"),
            "baseline_comparison_passed": bool(metrics.get("baseline_comparison_passed")),
            "paper_trade_count": metrics.get("paper_trade_count", 0),
            "inference_latency_ms": metrics.get("inference_latency_ms"),
        }
    prediction_table_exists = database.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'transformer_predictions'"
    ).fetchone() is not None
    prediction_counts = (
        {
            row["model_scope"]: row["count"]
            for row in database.conn.execute(
                "SELECT model_scope, COUNT(*) AS count FROM transformer_predictions GROUP BY model_scope"
            ).fetchall()
        }
        if prediction_table_exists
        else {}
    )
    experiment_count = database.conn.execute(
        "SELECT COUNT(*) AS count FROM transformer_training_experiments"
    ).fetchone()["count"]
    print(
        json.dumps(
            {
                "authority_mode": settings.transformer_trading_mode,
                "models": models,
                "prediction_counts": prediction_counts,
                "training_experiment_count": experiment_count,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


def evaluate_transformer_paper_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    database = Database(settings=settings)
    database.init_db()
    reports = evaluate_transformer_paper_models(database, settings, model_version=args.model_version)
    print(json.dumps({"reports": reports}, indent=2, sort_keys=True, default=str))


def promote_transformer_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    database = Database(settings=settings)
    database.init_db()
    registry = ModelRegistry(database, settings)
    candidate = registry.get_model(args.model_version)
    if candidate is None or not str(candidate.get("model_type") or "").startswith("causal_transformer_"):
        raise RuntimeError(f"Transformer candidate not found: {args.model_version}")
    metrics = json.loads(candidate.get("metrics_json") or "{}")
    ready, readiness_reason = registry.promotion_readiness(metrics)
    if not ready:
        print(
            json.dumps(
                {
                    "promoted": False,
                    "reason": readiness_reason,
                    "model_version": args.model_version,
                    "status": "candidate retained for further paper-shadow evidence",
                },
                indent=2,
            )
        )
        return
    promoted, reason = registry.promote_if_better(
        args.model_version,
        metrics,
        model_scope=str(candidate["model_scope"]),
    )
    print(json.dumps({"promoted": promoted, "reason": reason, "model_version": args.model_version}, indent=2))


def build_ml_archive_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    archive_path = Path(args.database).resolve()
    if not archive_path.exists():
        raise RuntimeError(f"Historical database not found: {archive_path}")
    database = Database(database_url=f"sqlite:///{archive_path}", settings=settings, read_only=True)
    try:
        records = build_archive_training_records(
            database,
            settings,
            start=_parse_cli_datetime(args.start),
            end=_parse_cli_datetime(args.end),
            stride_minutes=args.stride_minutes,
            horizon_minutes=args.horizon_minutes,
            horizons=args.horizons,
            max_samples=args.max_samples,
            slippage_pct=args.slippage_pct,
            minimum_edge_pct=args.minimum_edge_pct,
        )
    finally:
        database.close()
    result = save_archive_training_artifact(
        records,
        settings,
        name=args.artifact_name,
        horizon_minutes=args.horizon_minutes,
        stride_minutes=args.stride_minutes,
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True, default=str))


def train_archive_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    database = Database(settings=settings)
    database.init_db()
    configure_logging(settings.log_level, database=database)
    result = train_candidate_model(
        database,
        settings,
        archive_artifact=Path(args.artifact).resolve(),
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def train_loop_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    database = Database(settings=settings)
    database.init_db()
    configure_logging(settings.log_level, database=database)
    runner = ContinualTrainingRunner(
        database,
        settings,
        artifact=Path(args.artifact).resolve(),
        horizons=args.horizons,
        playbooks=args.playbooks,
        minimum_samples=args.minimum_samples,
        paper_weight=args.paper_weight,
    )
    if args.clear_stop:
        runner.stop_path.unlink(missing_ok=True)
    result = runner.run(
        watch=args.watch,
        continuous_historical=args.continuous_historical,
        interval_minutes=args.interval_minutes,
        max_cycles=args.max_cycles,
        retry_failed=args.retry_failed,
        no_improvement_patience=args.patience_rounds,
        minimum_improvement=args.minimum_improvement,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def training_loop_status_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    database = Database(settings=settings)
    database.init_db()
    root = settings.data_root / "ml_training" / "continual"
    state_path = root / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    rows = database.conn.execute(
        "SELECT status, COUNT(*) AS count FROM ml_training_experiments GROUP BY status ORDER BY status"
    ).fetchall()
    print(
        json.dumps(
            {
                "state_path": str(state_path),
                "lock_exists": (root / "training.lock").exists(),
                "stop_requested": (root / "STOP_TRAINING").exists(),
                "experiment_counts": {row["status"]: row["count"] for row in rows},
                "state": state,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


def stop_training_loop_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    stop_path = settings.data_root / "ml_training" / "continual" / "STOP_TRAINING"
    stop_path.parent.mkdir(parents=True, exist_ok=True)
    stop_path.write_text(f"stop requested at {utc_now().isoformat()}\n", encoding="utf-8")
    print(f"Training loop stop requested at {stop_path}")


def ml_drift_report_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    database = Database(settings=settings)
    database.init_db()
    result = generate_drift_report(database, limit=args.limit)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def walk_forward_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    result = run_walk_forward_validation(db, settings, lookback_days=args.lookback_days)
    save_walk_forward_experiment(db, result, policy_name=args.policy_name)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def backtest_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    result = Backtester(db, settings).run(args.start, args.end)
    print(json.dumps(result.metrics, indent=2, sort_keys=True))


def report_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    report_date = parse_date(args.date)
    print(generate_daily_report(db, report_date))


def export_csv_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    result = export_database_to_csv(db, args.output_dir or settings.csv_export_dir)
    print(
        json.dumps(
            {
                "export_dir": str(result.export_dir),
                "created_at": result.created_at.isoformat(),
                "files": result.files,
            },
            indent=2,
            sort_keys=True,
        )
    )


def collect_research_data_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    start = _parse_cli_datetime(args.start) if args.start else utc_now() - timedelta(days=args.days)
    end = _parse_cli_datetime(args.end) if args.end else utc_now()
    result = ResearchDataCollector(settings, db).collect_all(start=start, end=end, include_news_content=not args.no_news_content)
    print(json.dumps([asdict(item) for item in result], indent=2, sort_keys=True, default=str))


def label_paper_outcomes_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    result = MultiHorizonOutcomeLabeler(settings, db).label_matured(
        start=_parse_cli_datetime(args.start) if args.start else None,
        end=_parse_cli_datetime(args.end) if args.end else None,
        limit=args.limit,
        sources=tuple(args.sources),
        retry_partial=args.retry_partial,
    )
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str))


def _parse_cli_datetime(value: str):
    if "T" in value:
        return ensure_utc(value if value.endswith("Z") or "+" in value else f"{value}+00:00")
    return ensure_utc(f"{value}T00:00:00+00:00")


def update_macro_context_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    record = MacroContextBuilder(settings, db).build(now=utc_now())
    db.insert_macro_context(record)
    print(pretty_macro_context(record))


def review_coach_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    record = LocalRAGCoach(settings, db, knowledge_dir=args.knowledge_dir).build_review(query=args.query, limit=args.limit)
    print(json.dumps(record, indent=2, sort_keys=True, default=str))


def rl_preview_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    result = run_offline_policy_preview(db, settings, start=args.start, end=args.end, policy_name=args.policy_name)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def llm_analyze_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    require_offline_llm_enabled(settings)
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    result = LLMAnalysisService(settings, db).run_data_analysis(query=args.query)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def llm_provider_status_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    result = {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "offline_only": settings.llm_offline_only,
        "live_trading_enabled": settings.enable_llm_live_trading,
    }
    if settings.llm_provider == "kimi":
        result = kimi_tier0_status(settings)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def llm_macro_context_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    require_offline_llm_enabled(settings)
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    result = LLMAnalysisService(settings, db).build_llm_macro_context(now=utc_now())
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def llm_offline_cycle_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    result = FinGPTOfflineResearch(settings, db).run(cadence=args.cadence, force=args.force)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def tradingagents_advisory_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    require_offline_llm_enabled(settings)
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    result = TradingAgentsAdvisoryService(settings, db).run(horizon=args.horizon, force=args.force)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def train_exit_model_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    result = train_exit_candidate(db, settings, strategy_path=args.strategy_path, playbook=args.playbook)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def rollback_model_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    changed, reason = ModelRegistry(db, settings).rollback(args.scope, args.model_version, reason=args.reason)
    print(json.dumps({"changed": changed, "reason": reason, "scope": args.scope, "model_version": args.model_version}, indent=2))


def llm_training_advice_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    require_offline_llm_enabled(settings)
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    result = LLMAnalysisService(settings, db).generate_training_advice()
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


def llm_label_signals_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    require_offline_llm_enabled(settings)
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    result = LLMAnalysisService(settings, db).label_recent_signals(limit=args.limit)
    print(json.dumps({"saved_labels": result, "count": len(result)}, indent=2, sort_keys=True, default=str))


def llm_train_candidate_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    require_offline_llm_enabled(settings)
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    service = LLMAnalysisService(settings, db)
    labels = service.label_recent_signals(limit=args.label_limit) if args.label_limit > 0 else []
    advice = service.generate_training_advice() if args.with_advice else None
    result = train_candidate_model(db, settings, lookback_days=args.lookback_days, use_llm_labels=True)
    print(
        json.dumps(
            {
                "llm_labels_created": len(labels),
                "training_advice_created": advice is not None,
                "training_result": result,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


def reset_data_command(args: argparse.Namespace) -> None:
    if not args.yes:
        raise SystemExit("Refusing to reset data without --yes.")
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    deleted = db.clear_all_data()
    cleared_paths: list[str] = []
    if args.clear_exports:
        export_root = Path(settings.csv_export_dir)
        export_root = export_root if export_root.is_absolute() else Path.cwd() / export_root
        latest_dir = export_root.parent / "latest"
        for path in [export_root, latest_dir]:
            resolved = path if path.is_absolute() else Path.cwd() / path
            if resolved.exists():
                shutil.rmtree(resolved)
                cleared_paths.append(str(resolved))
        export_root.mkdir(parents=True, exist_ok=True)
    if args.clear_logs:
        log_path = Path.cwd() / "logs" / "bot.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            log_path.write_text("", encoding="utf-8")
            cleared_paths.append(str(log_path))
    print(json.dumps({"deleted_rows": deleted, "cleared_paths": cleared_paths}, indent=2, sort_keys=True))


def status_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    db = Database(settings=settings)
    db.init_db()
    registry = ModelRegistry(db)
    champion = registry.get_champion()
    predictor = Predictor(db)
    latest = db.get_latest_database_timestamp(settings.bot_symbol)
    macro = db.get_latest_macro_context(max_age_minutes=settings.macro_context_max_age_minutes)
    agent_advisory = db.get_latest_agent_advisory(
        settings.bot_symbol,
        max_age_minutes=settings.tradingagents_advisory_max_age_minutes,
    )
    options = db.get_latest_options_intelligence()
    champions_by_scope = {
        str(row["model_scope"] or "entry:all"): str(row["model_version"])
        for row in db.conn.execute(
            "SELECT model_scope, model_version FROM model_versions WHERE status = 'champion' ORDER BY model_scope"
        ).fetchall()
    }
    status = {
        "symbol": settings.bot_symbol,
        "data_mode": settings.data_mode,
        "paper_trading_enforced": settings.alpaca_paper,
        "database": str(db.path),
        "csv_export_dir": str(Path(settings.csv_export_dir)),
        "latest_bar_timestamp": latest.isoformat() if latest else None,
        "active_model_version": predictor.model_version,
        "active_model_role": predictor.model_role,
        "ml_participating": predictor.has_model,
        "champion_model_version": champion.get("model_version") if champion else None,
        "champions_by_scope": champions_by_scope,
        "latest_macro_bias": macro.get("macro_bias") if macro else None,
        "latest_macro_context_timestamp": macro.get("timestamp") if macro else None,
        "tradingagents_advisory_enabled": settings.enable_tradingagents_advisory,
        "latest_tradingagents_advisory_bias": agent_advisory.get("bias") if agent_advisory else None,
        "latest_tradingagents_advisory_confidence": agent_advisory.get("confidence") if agent_advisory else None,
        "latest_tradingagents_advisory_timestamp": agent_advisory.get("timestamp") if agent_advisory else None,
        "latest_tradingagents_advisory_expires_at": agent_advisory.get("expires_at") if agent_advisory else None,
        "latest_options_bias": options.get("options_bias") if options else None,
        "latest_options_intelligence_timestamp": options.get("timestamp") if options else None,
        "options_intelligence_enabled": settings.enable_options_intelligence,
        "order_blocks_enabled": settings.enable_order_blocks,
        "ema_cross_strategy_enabled": settings.enable_ema_cross_strategy,
        "ema_cross_paper_signal_authority": settings.ema_cross_paper_signal_authority,
        "ema_cross_timeframes": settings.ema_cross_timeframes,
        "ema_cross_signals": db.count_rows("ema_cross_signals"),
        "order_block_zones": db.count_rows("order_block_zones"),
        "bars": db.count_rows("bars"),
        "signals_today_not_loaded_from_broker": db.count_rows("signals"),
    }
    try:
        from .alpaca_clients import get_trading_client

        account = get_trading_client(settings).get_account()
        status.update(
            {
                "account_status": getattr(account, "status", None),
                "equity": str(getattr(account, "equity", "")),
                "buying_power": str(getattr(account, "buying_power", "")),
            }
        )
    except Exception as exc:
        status["broker_status"] = f"unavailable: {exc}"
    print(json.dumps(status, indent=2, sort_keys=True))


def dashboard_command(args: argparse.Namespace) -> None:
    from .dashboard import run_dashboard

    run_dashboard(host=args.host, port=args.port, open_browser=not args.no_browser)


def run_paper_command(args: argparse.Namespace) -> None:
    settings = load_settings()
    settings.validate_safety()
    db = Database(settings=settings)
    db.init_db()
    configure_logging(settings.log_level, database=db)
    logger.info("starting paper bot", extra={"event_type": "startup"})

    collector = HistoricalDataCollector(settings, db)
    try:
        collector.startup_recovery()
    except Exception as exc:
        db.log_event("ERROR", __name__, "startup_recovery_failed", str(exc), {})
        raise

    strategy = StrategyEngine(settings)
    risk = RiskEngine(settings, db)
    predictor = Predictor(db)
    if settings.paper_require_ml_model and not predictor.has_model:
        message = (
            "PAPER_REQUIRE_ML_MODEL=true but no champion or eligible paper-shadow model could be loaded; "
            "paper trading refused"
        )
        db.log_event("ERROR", __name__, "paper_ml_required_missing", message, {})
        raise RuntimeError(message)
    logger.info(
        "ML runtime ready participating=%s role=%s model_version=%s",
        predictor.has_model,
        predictor.model_role,
        predictor.model_version,
        extra={"event_type": "ml_runtime_ready"},
    )
    if predictor.model_role == "paper_shadow":
        logger.warning(
            "paper-shadow ML adviser active model_version=%s; candidate is unapproved, bounded, and paper-only",
            predictor.model_version,
            extra={"event_type": "paper_shadow_model_active"},
        )
    trading_client = get_trading_client(settings)
    execution_safety_state = ExecutionSafetyState(settings)
    latency_tracker = ExecutionLatencyTracker(settings)
    latency_tracker.start()
    order_coordinator = OrderIntentCoordinator(settings, trading_client, execution_safety_state, latency_tracker)
    execution_safety = ExecutionSafetySupervisor(settings, trading_client, order_coordinator)
    try:
        startup_safety = execution_safety.startup()
    except Exception:
        order_coordinator.stop(drain=False)
        latency_tracker.stop()
        raise
    logger.info(
        "execution safety startup complete consistent=%s position_qty=%s open_orders=%s protected=%s",
        startup_safety.consistent,
        startup_safety.broker_position_qty,
        startup_safety.broker_open_order_count,
        startup_safety.protected_position,
        extra={"event_type": "execution_safety_startup"},
    )
    execution_engine = ExecutionEngine(
        settings,
        db,
        trading_client=trading_client,
        coordinator=order_coordinator,
    )
    order_reconciler = PaperOrderReconciler(settings, db, trading_client=trading_client)
    broker_order_stream = None
    if settings.enable_live_stream and not args.no_stream:
        broker_order_stream = BrokerOrderUpdateRuntime(settings, execution_safety_state, latency_tracker=latency_tracker)
        broker_order_stream.start()
    performance_tracker = AccountPerformanceTracker(settings, db, trading_client)
    entry_quality_gate = EntryQualityGate(settings)
    entry_cooldown_policy = EntryCooldownPolicy(settings, db)
    try:
        performance_tracker.capture("startup", force=True)
    except Exception as exc:
        logger.exception("startup account snapshot failed: %s", exc, extra={"event_type": "account_snapshot_failed"})
    paper_exploration = PaperExplorationPolicy(settings, db)
    if settings.enable_ema_cross_strategy:
        logger.warning(
            "filtered EMA-cross route active timeframes=%s fast=%s slow=%s adx=%s threshold=%.1f "
            "cooldown_bars=%s paper_authority=%s",
            settings.ema_cross_timeframes,
            settings.ema_cross_fast_period,
            settings.ema_cross_slow_period,
            settings.ema_cross_adx_period,
            settings.ema_cross_adx_threshold,
            settings.ema_cross_cooldown_bars,
            settings.ema_cross_paper_signal_authority,
            extra={"event_type": "ema_cross_strategy_active"},
        )
    if settings.paper_learning_mode:
        logger.warning(
            "paper learning mode active controlled_sample_rate=%.2f exploration_max_notional=%.2f "
            "fast_min_score=%.1f minute_min_score=%.1f playbook_min_score=%.1f "
            "pattern_min=%.2f liquidity_min=%.2f daily_exploration_limit=%s "
            "max_concurrent=%s aggregate_notional=%.2f fast_retry_seconds=%s live_data_required=true",
            settings.paper_learning_exploration_sample_rate,
            settings.paper_learning_exploration_max_notional,
            settings.paper_learning_fast_min_score,
            settings.paper_learning_min_score,
            settings.paper_learning_min_playbook_score,
            settings.paper_learning_min_pattern_quality,
            settings.paper_learning_min_liquidity_score,
            "unlimited"
            if settings.paper_learning_max_exploration_trades_per_day == 0
            else settings.paper_learning_max_exploration_trades_per_day,
            settings.paper_learning_max_concurrent_trades,
            settings.paper_learning_max_aggregate_notional,
            settings.paper_learning_fast_order_cooldown_seconds,
            extra={"event_type": "paper_learning_mode_active"},
        )
    csv_export_scheduler = HourlyCSVExportScheduler(settings)
    missed_opportunity_analyzer = MissedOpportunityAnalyzer(settings, db) if settings.enable_missed_opportunity_learning else None
    macro_context_scheduler = MacroContextScheduler(settings)
    research_data_scheduler = ResearchDataScheduler(settings)
    transformer_runtime = AsyncTransformerShadowRuntime(settings)
    transformer_runtime.start()
    position_runtime = None
    if settings.enable_dynamic_position_management and settings.enable_live_stream and not args.no_stream:
        position_runtime = DynamicPositionRuntime(
            settings,
            trading_client=trading_client,
            coordinator=order_coordinator,
            transformer_runtime=transformer_runtime,
        )
        position_runtime.start()
        execution_safety.set_internal_episode_provider(position_runtime.episode_snapshot)
        db.log_event(
            "INFO",
            __name__,
            "dynamic_position_runtime_started",
            "Dynamic protected position manager started",
            position_runtime.status(),
        )
    fast_scalp_runtime = None
    if settings.enable_fast_scalp and settings.enable_live_stream and not args.no_stream:
        fast_scalp_runtime = FastScalpRuntime(
            settings,
            trading_client=trading_client,
            coordinator=order_coordinator,
            transformer_runtime=transformer_runtime,
            latency_tracker=latency_tracker,
        )
        fast_scalp_runtime.start()
        db.log_event("INFO", __name__, "fast_scalp_runtime_started", "Fast scalp runtime started", fast_scalp_runtime.status())
    options_runtime = None
    if settings.enable_options_intelligence:
        options_runtime = OptionsIntelligenceRuntime(
            settings,
            feature_sink=fast_scalp_runtime.update_context if fast_scalp_runtime is not None else None,
        )
        options_runtime.start()
        db.log_event(
            "INFO",
            __name__,
            "options_intelligence_runtime_started",
            "GLD options intelligence runtime started",
            {"feed": settings.options_feed, "poll_interval_seconds": settings.options_poll_interval_seconds},
        )
    stream_runtime = None
    if settings.enable_live_stream and not args.no_stream:
        def route_live_event(event_type, payload, received_at):
            if fast_scalp_runtime is not None:
                fast_scalp_runtime.enqueue_event(event_type, payload, received_at)
            if position_runtime is not None:
                position_runtime.enqueue_event(event_type, payload, received_at)

        stream_runtime = LiveDataStreamRuntime(settings, event_sink=route_live_event)
        stream_runtime.start()
        execution_safety.set_stream_health_provider(stream_runtime.health)
        db.log_event("INFO", __name__, "stream_runtime_started", "Live Alpaca stream runtime started", {})
        logger.info(
            "waiting up to %s seconds for live stream warmup",
            settings.stream_startup_grace_seconds,
            extra={"event_type": "stream_warmup"},
        )
        warmup_deadline = time.monotonic() + settings.stream_startup_grace_seconds
        while time.monotonic() < warmup_deadline:
            health = stream_runtime.health(utc_now())
            if health["websocket_connected"]:
                logger.info(
                    "live stream warmup complete live_data_received=%s last_message=%s",
                    health["websocket_connected"],
                    health["stream_last_message_at"],
                    extra={"event_type": "stream_warmup_complete"},
                )
                break
            time.sleep(1)
    else:
        db.log_event("WARNING", __name__, "stream_runtime_disabled", "Live Alpaca stream runtime disabled", {})
    retraining_scheduler = None if args.no_retraining else SafeRetrainingScheduler(settings)
    last_drift_check_at = None
    loops = 1 if args.once else None
    completed = 0

    try:
        while True:
            now = utc_now()
            try:
                sync_result = order_reconciler.sync(now)
                if any(sync_result.values()):
                    logger.info(
                        "broker order sync orders=%s fills=%s outcomes=%s",
                        sync_result["orders"],
                        sync_result["fills"],
                        sync_result["trade_outcomes"],
                        extra={"event_type": "broker_order_sync"},
                    )
                    db.log_event("INFO", __name__, "broker_order_sync", "Broker order sync completed", sync_result)
            except Exception as exc:
                logger.exception("broker order sync failed: %s", exc, extra={"event_type": "broker_order_sync_failed"})
                db.log_event("ERROR", __name__, "broker_order_sync_failed", str(exc), {})

            try:
                performance_tracker.capture("minute", now=now)
            except Exception as exc:
                logger.exception("minute account snapshot failed: %s", exc, extra={"event_type": "account_snapshot_failed"})

            try:
                # The live loop consumes deterministic or previously saved context only.
                # Ollama/FinGPT analysis runs through the explicit offline research command.
                macro_record = macro_context_scheduler.maybe_update(db, now)
                if macro_record is not None:
                    logger.info(
                        "macro context updated bias=%s confidence=%.3f event_risk=%.3f",
                        macro_record.get("macro_bias"),
                        float(macro_record.get("macro_confidence") or 0.0),
                        float(macro_record.get("headline_event_risk") or 0.0),
                        extra={"event_type": "macro_context_updated"},
                    )
                    db.log_event("INFO", __name__, "macro_context_updated", "Slow macro context updated", macro_record)
            except Exception as exc:
                logger.exception("macro context update failed: %s", exc, extra={"event_type": "macro_context_failed"})
                db.log_event("ERROR", __name__, "macro_context_failed", str(exc), {})

            try:
                research_results = research_data_scheduler.poll(
                    now,
                    include_heavy=market_session(now, extended_hours=False) != "regular",
                )
                if research_results:
                    logger.info(
                        "research data collection results=%s",
                        [asdict(item) for item in research_results],
                        extra={"event_type": "research_data_collection"},
                    )
            except Exception as exc:
                logger.exception("research data collection failed: %s", exc, extra={"event_type": "research_data_failed"})
                db.log_event("ERROR", __name__, "research_data_failed", str(exc), {})

            if missed_opportunity_analyzer is not None:
                try:
                    missed_counts = missed_opportunity_analyzer.label_matured_no_trades(now=now, limit=50)
                    if missed_counts:
                        logger.info(
                            "missed-opportunity review completed counts=%s",
                            missed_counts,
                            extra={"event_type": "missed_opportunity_review"},
                        )
                        db.log_event(
                            "INFO",
                            __name__,
                            "missed_opportunity_review",
                            "Reviewed matured no-trade signals",
                            missed_counts,
                        )
                except Exception as exc:
                    logger.exception("missed-opportunity review failed: %s", exc, extra={"event_type": "missed_opportunity_review_failed"})
                    db.log_event("ERROR", __name__, "missed_opportunity_review_failed", str(exc), {})

            export_result = csv_export_scheduler.maybe_export(db, now)
            if export_result is not None:
                logger.info(
                    "csv export completed dir=%s rows=%s window=%s..%s",
                    export_result.export_dir,
                    sum(export_result.files.values()),
                    export_result.since.isoformat() if export_result.since else None,
                    export_result.until.isoformat() if export_result.until else None,
                    extra={"event_type": "csv_export_completed"},
                )
                db.log_event(
                    "INFO",
                    __name__,
                    "csv_export_completed",
                    "Hourly CSV export completed",
                    {
                        "export_dir": str(export_result.export_dir),
                        "files": export_result.files,
                        "since": export_result.since.isoformat() if export_result.since else None,
                        "until": export_result.until.isoformat() if export_result.until else None,
                    },
                )
            live_orders_idle = not db.fetch_active_execution_episodes(settings.bot_symbol)
            if retraining_scheduler is not None and retraining_scheduler.maybe_start(now, live_orders_idle=live_orders_idle):
                logger.info("scheduled retraining started", extra={"event_type": "scheduled_retraining_started"})
                db.log_event("INFO", __name__, "scheduled_retraining_started", "Scheduled retraining started", retraining_scheduler.status())
            if retraining_scheduler is not None:
                retraining_result = retraining_scheduler.consume_completion()
                if retraining_result is not None:
                    logger.info(
                        "scheduled retraining result status=%s promoted=%s reason=%s",
                        retraining_result.get("status"),
                        retraining_result.get("promoted"),
                        retraining_result.get("reason", retraining_result.get("registry_reason")),
                        extra={"event_type": "scheduled_retraining_result"},
                    )
                    db.log_event("INFO", __name__, "scheduled_retraining_result", "Scheduled retraining result available", retraining_result)
                    if retraining_result.get("status") == "completed" and retraining_result.get("promoted"):
                        if predictor.reload_champion():
                            logger.info(
                                "champion model reloaded model_version=%s",
                                predictor.model_version,
                                extra={"event_type": "champion_model_reloaded"},
                            )
                            db.log_event(
                                "INFO",
                                __name__,
                                "champion_model_reloaded",
                                "Reloaded newly promoted champion model",
                                {"model_version": predictor.model_version},
                            )
            if last_drift_check_at is None or now >= last_drift_check_at + timedelta(hours=1):
                last_drift_check_at = now
                scopes = [
                    str(row["model_scope"] or "entry:all")
                    for row in db.conn.execute("SELECT model_scope FROM model_versions WHERE status = 'champion'").fetchall()
                ]
                for model_scope in scopes:
                    drift_result = generate_drift_report(db, model_scope=model_scope, allow_demotion=True)
                    if drift_result.get("champion_demoted"):
                        predictor.reload_champion()
                        db.log_event(
                            "WARNING",
                            __name__,
                            "champion_model_demoted",
                            str(drift_result.get("demotion_reason") or "model drift"),
                            drift_result,
                        )
                if market_session(now, extended_hours=False) != "regular":
                    transformer_reports = evaluate_transformer_paper_models(db, settings)
                    if transformer_reports:
                        db.log_event(
                            "INFO",
                            __name__,
                            "transformer_paper_lifecycle",
                            "Transformer paper evidence evaluated outside regular hours",
                            {"reports": transformer_reports},
                        )

            # Maintenance and network collection can take several seconds. Decisions must use a fresh clock.
            now = utc_now()
            bars = db.fetch_latest_bars(settings.bot_symbol, settings.trade_timeframe, limit=200)
            if not bars:
                logger.warning("no trade: no market data available", extra={"event_type": "no_trade"})
                db.insert_no_trade(
                    {
                        "timestamp": now,
                        "symbol": settings.bot_symbol,
                        "reason": "no market data available",
                        "feature_snapshot_json": {},
                    }
                )
                time.sleep(seconds_until_next_minute())
                continue
            ema_cross_bars = (
                db.fetch_latest_bars(
                    settings.bot_symbol,
                    settings.trade_timeframe,
                    limit=settings.ema_cross_history_minutes,
                )
                if settings.enable_ema_cross_strategy
                else bars
            )

            quote = db.get_latest_quote(settings.bot_symbol)
            recent_quotes = db.fetch_recent_quotes(settings.bot_symbol, since=now - timedelta(seconds=60), limit=200)
            trade = db.get_latest_trade(settings.bot_symbol)
            recent_trades = db.fetch_recent_trades(settings.bot_symbol, since=now - timedelta(minutes=5), limit=500)
            features = build_feature_snapshot(bars_1m=bars, quote=quote, now=now)
            features.update(build_archive_compatible_features(bars))
            if trade and trade.get("timestamp") is not None:
                features["latest_trade_timestamp"] = trade["timestamp"]
                features["latest_trade_price"] = trade.get("price")
                features["trade_age_seconds"] = (ensure_utc(now) - ensure_utc(trade["timestamp"])).total_seconds()
            if stream_runtime is not None:
                stream_health = stream_runtime.health(now)
                features.update(stream_health)
                db.insert_stream_diagnostic({"timestamp": now, **stream_health})
            else:
                stream_health = (
                    {
                        "websocket_connected": False,
                        "stream_thread_alive": False,
                        "stream_stale": True,
                        "stream_last_message_at": None,
                        "stream_message_age_seconds": None,
                        "stream_last_bar_at": None,
                        "stream_last_quote_at": None,
                        "stream_last_trade_at": None,
                        "stream_bar_age_seconds": None,
                        "stream_quote_age_seconds": None,
                        "stream_trade_age_seconds": None,
                        "stream_bar_count": 0,
                        "stream_quote_count": 0,
                        "stream_trade_count": 0,
                        "stream_stale_reason": "live stream disabled",
                        "stream_last_error": "live stream disabled",
                    }
                )
                features.update(stream_health)
                db.insert_stream_diagnostic({"timestamp": now, **stream_health})
            features.update(analyze_price_action(bars))
            features.update(
                build_microstructure_features(
                    bars=bars,
                    quote=quote,
                    recent_quotes=recent_quotes,
                    recent_trades=recent_trades,
                    now=now,
                )
            )
            features.update(build_gold_volatility_features(bars, now=now))
            features.update({"strategy_path": "minute", "time_of_day_profile": time_of_day_profile(now)})
            if settings.enable_order_blocks:
                order_block_bars = db.fetch_latest_bars(
                    settings.bot_symbol,
                    settings.trade_timeframe,
                    limit=settings.order_block_history_minutes,
                )
                order_block_features, order_block_records = analyze_order_blocks(
                    order_block_bars,
                    settings,
                    now=now,
                    symbol=settings.bot_symbol,
                )
                features.update(order_block_features)
                db.upsert_order_block_zones(order_block_records)
            options_record = db.get_latest_options_intelligence(underlying_symbol=settings.options_underlying)
            features.update(
                options_intelligence_to_features(
                    options_record,
                    now=now,
                    max_age_seconds=max(
                        settings.options_max_quote_age_seconds,
                        settings.options_poll_interval_seconds * 3,
                    ),
                )
            )
            macro_context = db.get_latest_macro_context(max_age_minutes=settings.macro_context_max_age_minutes, now=now)
            features.update(macro_context_to_features(macro_context))
            agent_advisory = (
                db.get_latest_agent_advisory(
                    settings.bot_symbol,
                    max_age_minutes=settings.tradingagents_advisory_max_age_minutes,
                    now=now,
                )
                if settings.enable_tradingagents_advisory
                else None
            )
            features.update(agent_advisory_to_features(agent_advisory, settings, now=now))
            features.update(event_risk_features(db, settings, now))
            features.update(build_gold_event_impact(features))
            technical_features, fair_value_gaps = analyze_technical_market(
                bars,
                features,
                now=now,
                symbol=settings.bot_symbol,
                timeframe=settings.trade_timeframe,
            )
            features.update(technical_features)
            if fair_value_gaps:
                db.upsert_fair_value_gaps(
                    [{**record, "updated_at": now} for record in fair_value_gaps]
                )
            ema_evaluation = evaluate_ema_cross_strategy(
                ema_cross_bars,
                settings,
                last_executed_bars=db.fetch_latest_executed_ema_cross_bars(
                    settings.bot_symbol
                ),
            )
            new_ema_events: list[tuple[object, int]] = []
            for event in ema_evaluation.events:
                event_id, inserted = db.upsert_ema_cross_signal(
                    event.to_record(symbol=settings.bot_symbol)
                )
                if inserted and event.eligible:
                    new_ema_events.append((event, event_id))
            ema_selection = select_ema_cross_events(
                [event for event, _ in new_ema_events]
            )
            selected_ema_ids = [
                event_id
                for event, event_id in new_ema_events
                if event in ema_selection.merged
            ]
            conflicting_ema_ids = [
                event_id
                for event, event_id in new_ema_events
                if event in ema_selection.conflicts
            ]
            if conflicting_ema_ids:
                db.update_ema_cross_signals(
                    conflicting_ema_ids,
                    execution_status="timeframe_conflict",
                    block_reason=(
                        "opposite multi-timeframe cross deferred; highest timeframe selected"
                    ),
                )
            if selected_ema_ids:
                db.update_ema_cross_signals(
                    selected_ema_ids,
                    execution_status="selected_merged",
                )
            features.update(
                ema_evaluation.to_features(
                    ema_selection,
                    selected_ids=selected_ema_ids,
                )
            )
            if ema_selection.selected is not None:
                logger.info(
                    "EMA cross selected direction=%s timeframe=%sMin merged=%s conflicts=%s "
                    "adx=%s score=%.2f",
                    ema_selection.selected.direction,
                    ema_selection.selected.timeframe_minutes,
                    [event.timeframe_minutes for event in ema_selection.merged],
                    [event.timeframe_minutes for event in ema_selection.conflicts],
                    ema_selection.selected.adx,
                    ema_selection.selected.score,
                    extra={"event_type": "ema_cross_signal_selected"},
                )
            playbook = evaluate_playbooks(features, settings)
            features.update(playbook.to_features())
            if settings.enable_feature_audit_tables:
                db.upsert_microstructure_features(
                    [
                        {
                            "timestamp": now,
                            "symbol": settings.bot_symbol,
                            "bid_price": features.get("bid_price"),
                            "ask_price": features.get("ask_price"),
                            "bid_size": features.get("bid_size"),
                            "ask_size": features.get("ask_size"),
                            "midpoint": features.get("midpoint"),
                            "spread": features.get("spread"),
                            "spread_pct": features.get("spread_pct"),
                            "spread_regime": features.get("spread_regime"),
                            "quote_imbalance": features.get("quote_imbalance"),
                            "quote_age_seconds": features.get("quote_age_seconds"),
                            "trade_intensity": features.get("trade_intensity"),
                            "signed_volume": features.get("signed_volume"),
                            "aggressive_buy_volume": features.get("aggressive_buy_volume"),
                            "aggressive_sell_volume": features.get("aggressive_sell_volume"),
                            "liquidity_score": features.get("liquidity_score"),
                            "volatility_burst": features.get("volatility_burst"),
                            "stale_data": features.get("stream_stale"),
                            "source": "live_loop",
                            "features": features,
                        }
                    ]
                )
                db.upsert_price_action_labels(
                    [
                        {
                            "timestamp": now,
                            "symbol": settings.bot_symbol,
                            "timeframe": settings.trade_timeframe,
                            **features,
                        }
                    ]
                )
                db.insert_playbook_evaluation(
                    {
                        "timestamp": now,
                        "symbol": settings.bot_symbol,
                        "playbook": playbook.playbook,
                        "direction": playbook.direction,
                        "score": playbook.score,
                        "allowed": playbook.allowed,
                        "block_reason": playbook.block_reason,
                        "expected_hold_minutes": playbook.expected_hold_minutes,
                        "target_r_multiple": playbook.target_r_multiple,
                        "features": features,
                    }
                )
            ml_result = predictor.predict(features)
            features.update(predictor.prediction_features(ml_result))
            transformer_shadow = transformer_runtime.submit(
                "minute",
                now,
                features,
                fallback_model_version=ml_result.model_version,
            )
            features.update(transformer_shadow.as_features())
            if playbook.playbook == "news_event" or float(features.get("event_risk_score") or 0.0) > 0.0:
                news_shadow = transformer_runtime.submit(
                    "news_event",
                    now,
                    features,
                    fallback_model_version=ml_result.model_version,
                )
                features.update(news_shadow.as_features())
            logger.info(
                "model prediction role=%s model=%s predicted=%s p_long=%.3f p_short=%.3f p_no_trade=%.3f confidence=%.3f",
                predictor.model_role,
                ml_result.model_version,
                ml_result.predicted_direction,
                ml_result.probability_long,
                ml_result.probability_short,
                ml_result.probability_no_trade,
                ml_result.confidence,
                extra={"event_type": "model_prediction"},
            )
            db.insert_model_prediction(
                {
                    "timestamp": now,
                    "symbol": settings.bot_symbol,
                    "model_version": ml_result.model_version,
                    "model_scope": predictor.model_scope,
                    "predicted_direction": ml_result.predicted_direction,
                    "probability_long": ml_result.probability_long,
                    "probability_short": ml_result.probability_short,
                    "probability_no_trade": ml_result.probability_no_trade,
                    "expected_return": ml_result.expected_return,
                    "confidence": ml_result.confidence,
                    "feature_snapshot_json": features,
                }
            )
            features.update(combine_reasoning(features))
            features.update(run_decision_council(features, settings, now=now).as_features())
            signal = strategy.evaluate(features, symbol=settings.bot_symbol, now=now, has_champion_model=predictor.has_champion)
            signal.model_version = ml_result.model_version
            signal = apply_transformer_to_signal(
                signal,
                transformer_shadow,
                features,
                settings,
            )
            signal = apply_ema_cross_paper_authority(
                signal,
                features,
                settings,
            )
            predicted_action = {"long_good": "LONG", "short_good": "SHORT"}.get(ml_result.predicted_direction)
            features["ml_direction_aligned"] = predicted_action == signal.decision
            signal = paper_exploration.maybe_select(signal, features, now=now)
            if signal.features.get("paper_exploration"):
                features.update(signal.features)
            target_exposure = target_exposure_from_signal(signal, features)
            features.update(target_exposure.as_features())
            features.update(
                {
                    "decision": signal.decision,
                    "bullish_score": signal.bullish_score,
                    "bearish_score": signal.bearish_score,
                    "no_trade_score": signal.no_trade_score,
                    "regime": signal.regime,
                    "confidence": signal.confidence,
                    "signal_reason": signal.reason,
                }
            )
            signal.features.update(features)
            if fast_scalp_runtime is not None:
                fast_scalp_runtime.update_context(features)
            if position_runtime is not None:
                position_runtime.update_context(features)
            logger.info(
                "signal decision=%s bullish=%.2f bearish=%.2f no_trade=%.2f regime=%s confidence=%.3f data_age=%.1f stream_connected=%s reason=%s",
                signal.decision,
                signal.bullish_score,
                signal.bearish_score,
                signal.no_trade_score,
                signal.regime,
                signal.confidence,
                float(features.get("data_age_seconds") or 0.0),
                features.get("websocket_connected"),
                signal.reason,
                extra={"event_type": "signal_generated"},
            )
            signal_id = db.insert_signal(
                {
                    "timestamp": signal.timestamp,
                    "symbol": signal.symbol,
                    "bullish_score": signal.bullish_score,
                    "bearish_score": signal.bearish_score,
                    "no_trade_score": signal.no_trade_score,
                    "regime": signal.regime,
                    "decision": signal.decision,
                    "confidence": signal.confidence,
                    "reason": signal.reason,
                    "feature_snapshot_json": signal.features,
                    "model_version": signal.model_version,
                }
            )

            risk_state = risk.state_from_features(features)
            _refresh_broker_risk_state(risk_state, trading_client, settings.bot_symbol, db, settings)
            risk.enrich_runtime_state(risk_state, now=now)
            blocked, reason = risk.blocks_trading(risk_state, signal.decision)
            if signal.decision != "NO_TRADE":
                strategy_path = str(features.get("strategy_path") or "minute")
                quality = entry_quality_gate.evaluate(
                    features,
                    strategy_path=strategy_path,
                    now=now,
                )
                cooldown = entry_cooldown_policy.evaluate(
                    strategy_path=strategy_path,
                    features=features,
                    now=now,
                )
                features.update(quality.as_features())
                features.update(
                    {
                        "entry_cooldown_allowed": cooldown.allowed,
                        "entry_cooldown_reason": cooldown.reason,
                    }
                )
                signal.features.update(features)
                if not quality.allowed:
                    blocked, reason = True, quality.reason
                elif not cooldown.allowed:
                    blocked, reason = True, cooldown.reason
            if signal.decision == "NO_TRADE" or blocked:
                logger.info(
                    "no trade reason=%s risk_block=%s spread_pct=%s stream_stale=%s",
                    signal.reason if signal.decision == "NO_TRADE" else "risk block",
                    reason if blocked else None,
                    features.get("spread_pct"),
                    features.get("stream_stale"),
                    extra={"event_type": "no_trade"},
                )
                db.insert_no_trade(
                    {
                        "timestamp": now,
                        "symbol": settings.bot_symbol,
                        "reason": signal.reason if signal.decision == "NO_TRADE" else "risk block",
                        "bullish_score": signal.bullish_score,
                        "bearish_score": signal.bearish_score,
                        "regime": signal.regime,
                        "spread_pct": features.get("spread_pct"),
                        "risk_block_reason": reason if blocked else None,
                        "feature_snapshot_json": features,
                    }
                )
                _record_decision_execution(
                    db,
                    signal_id=signal_id,
                    signal=signal,
                    features=features,
                    ml_result=ml_result,
                    executed_action="NO_TRADE",
                    status="no_trade" if signal.decision == "NO_TRADE" else "risk_blocked",
                    error=reason if blocked else signal.reason,
                )
            else:
                if model_rejection_blocks(settings, features, ml_result.rejects_trade):
                    logger.info(
                        "no trade: ML rejected reason=%s",
                        ml_result.rejection_reason,
                        extra={"event_type": "no_trade"},
                    )
                    db.insert_no_trade(
                        {
                            "timestamp": now,
                            "symbol": settings.bot_symbol,
                            "reason": "ML rejected",
                            "model_rejection_reason": ml_result.rejection_reason,
                            "feature_snapshot_json": features,
                        }
                    )
                    _record_decision_execution(
                        db,
                        signal_id=signal_id,
                        signal=signal,
                        features=features,
                        ml_result=ml_result,
                        executed_action="NO_TRADE",
                        status="model_abstained",
                        error=ml_result.rejection_reason,
                    )
                else:
                    if ml_result.rejects_trade and settings.paper_learning_mode:
                        logger.info(
                            "paper learning mode records but overrides model abstention reason=%s",
                            ml_result.rejection_reason,
                            extra={"event_type": "paper_learning_model_override"},
                        )
                    try:
                        plan = risk.build_order_plan(signal, ml_result, risk_state)
                    except OrderPlanRejected as exc:
                        logger.info(
                            "no trade: order plan blocked reason=%s",
                            exc,
                            extra={"event_type": "order_plan_blocked"},
                        )
                        db.insert_no_trade(
                            {
                                "timestamp": now,
                                "symbol": settings.bot_symbol,
                                "reason": "order plan blocked",
                                "bullish_score": signal.bullish_score,
                                "bearish_score": signal.bearish_score,
                                "regime": signal.regime,
                                "spread_pct": features.get("spread_pct"),
                                "risk_block_reason": str(exc),
                                "model_rejection_reason": ml_result.rejection_reason,
                                "feature_snapshot_json": features,
                            }
                        )
                        _record_decision_execution(
                            db,
                            signal_id=signal_id,
                            signal=signal,
                            features=features,
                            ml_result=ml_result,
                            executed_action="NO_TRADE",
                            status="order_plan_blocked",
                            error=str(exc),
                        )
                        completed += 1
                        if loops and completed >= loops:
                            break
                        time.sleep(seconds_until_next_minute())
                        continue
                    if plan.direction == "SHORT":
                        shortability = _check_shortability_for_plan(trading_client, plan.symbol, plan.estimated_notional)
                        if not shortability.allowed:
                            logger.info(
                                "no trade: shortability blocked reason=%s",
                                shortability.reason,
                                extra={"event_type": "no_trade"},
                            )
                            db.insert_no_trade(
                                {
                                    "timestamp": now,
                                    "symbol": settings.bot_symbol,
                                    "reason": "shortability blocked",
                                    "bullish_score": signal.bullish_score,
                                    "bearish_score": signal.bearish_score,
                                    "regime": signal.regime,
                                    "spread_pct": features.get("spread_pct"),
                                    "risk_block_reason": shortability.reason,
                                    "feature_snapshot_json": features,
                                }
                            )
                            _record_decision_execution(
                                db,
                                signal_id=signal_id,
                                signal=signal,
                                features=features,
                                ml_result=ml_result,
                                executed_action="NO_TRADE",
                                status="direction_unavailable",
                                error=shortability.reason,
                                direction_available=False,
                            )
                            completed += 1
                            if loops and completed >= loops:
                                break
                            time.sleep(seconds_until_next_minute())
                            continue
                    logger.warning(
                        "submitting paper order direction=%s qty=%s limit=%.2f take_profit=%.2f stop_loss=%.2f",
                        plan.direction,
                        plan.qty,
                        plan.entry_limit_price,
                        plan.take_profit_price,
                        plan.stop_loss_price,
                        extra={"event_type": "order_submit"},
                    )
                    plan.client_order_id = plan.client_order_id or make_client_order_id(plan.symbol, plan.direction)
                    plan.latency_trace_id = latency_tracker.begin(
                        strategy_path=str(features.get("strategy_path") or "minute"),
                        event_time=signal.timestamp,
                        playbook=plan.playbook,
                    )
                    latency_tracker.bind(plan.latency_trace_id, client_order_id=plan.client_order_id)
                    latency_tracker.record(
                        plan.latency_trace_id,
                        "order_plan_complete",
                        strategy_path=str(features.get("strategy_path") or "minute"),
                        playbook=plan.playbook,
                        client_order_id=plan.client_order_id,
                    )
                    db.insert_trading_journal(
                        {
                            "timestamp": now,
                            "symbol": settings.bot_symbol,
                            "event_type": "TRADE_DECISION",
                            "signal_id": signal_id,
                            "decision": signal.decision,
                            "confidence": signal.confidence,
                            "bullish_score": signal.bullish_score,
                            "bearish_score": signal.bearish_score,
                            "no_trade_score": signal.no_trade_score,
                            "regime": signal.regime,
                            "reason": signal.reason,
                            "model_version": ml_result.model_version,
                            "model_prediction": ml_result.predicted_direction,
                            "probability_long": ml_result.probability_long,
                            "probability_short": ml_result.probability_short,
                            "probability_no_trade": ml_result.probability_no_trade,
                            "client_order_id": plan.client_order_id,
                            "side": plan.side,
                            "qty": plan.qty,
                            "price": plan.entry_limit_price,
                            "notional": plan.estimated_notional,
                            "status": "planned",
                            "exploration_trade": features.get("paper_exploration"),
                            **_journal_feature_fields(features),
                            "feature_snapshot_json": features,
                        }
                    )
                    try:
                        submission = execution_engine.submit_entry_with_protection(plan)
                        for submitted in submission.entries:
                            order = submitted.order
                            tranche_features = {
                                **features,
                                "position_role": submitted.role,
                                "root_client_order_id": plan.client_order_id,
                            }
                            db.insert_trading_journal(
                                {
                                    "timestamp": utc_now(),
                                    "symbol": settings.bot_symbol,
                                    "event_type": "ORDER_SUBMITTED",
                                    "signal_id": signal_id,
                                    "decision": signal.decision,
                                    "confidence": signal.confidence,
                                    "bullish_score": signal.bullish_score,
                                    "bearish_score": signal.bearish_score,
                                    "no_trade_score": signal.no_trade_score,
                                    "regime": signal.regime,
                                    "reason": signal.reason,
                                    "model_version": ml_result.model_version,
                                    "model_prediction": ml_result.predicted_direction,
                                    "probability_long": ml_result.probability_long,
                                    "probability_short": ml_result.probability_short,
                                    "probability_no_trade": ml_result.probability_no_trade,
                                    "order_id": str(getattr(order, "id", "")),
                                    "client_order_id": submitted.client_order_id,
                                    "side": submitted.plan.side,
                                    "qty": submitted.qty,
                                    "price": submitted.plan.entry_limit_price,
                                    "notional": submitted.plan.estimated_notional,
                                    "status": str(getattr(order, "status", "submitted")),
                                    "exploration_trade": features.get("paper_exploration"),
                                    **_journal_feature_fields(features),
                                    "feature_snapshot_json": tranche_features,
                                    "broker_snapshot_json": order.model_dump(mode="json") if hasattr(order, "model_dump") else {"repr": repr(order)},
                                }
                            )
                        if submission.errors:
                            db.log_event(
                                "ERROR",
                                __name__,
                                "partial_tranche_submit_failed",
                                "; ".join(submission.errors),
                                {"root_client_order_id": plan.client_order_id},
                            )
                        _record_decision_execution(
                            db,
                            signal_id=signal_id,
                            signal=signal,
                            features=features,
                            ml_result=ml_result,
                            executed_action=signal.decision,
                            status="submitted" if not submission.errors else "partially_submitted",
                            client_order_id=plan.client_order_id,
                            error="; ".join(submission.errors) if submission.errors else None,
                        )
                    except EntryBlockedError as exc:
                        logger.info(
                            "minute entry blocked by execution safety: %s",
                            exc,
                            extra={"event_type": "execution_safety_entry_block"},
                        )
                        db.insert_no_trade(
                            {
                                "timestamp": utc_now(),
                                "symbol": settings.bot_symbol,
                                "reason": "execution safety block",
                                "risk_block_reason": str(exc),
                                "feature_snapshot_json": features,
                            }
                        )
                        _record_decision_execution(
                            db,
                            signal_id=signal_id,
                            signal=signal,
                            features=features,
                            ml_result=ml_result,
                            executed_action="NO_TRADE",
                            status="execution_safety_blocked",
                            client_order_id=plan.client_order_id,
                            error=str(exc),
                        )
                    except Exception as exc:
                        logger.exception("paper order submission failed: %s", exc, extra={"event_type": "order_submit_failed"})
                        db.log_event("ERROR", __name__, "order_submit_failed", str(exc), {"client_order_id": plan.client_order_id})
                        db.insert_trading_journal(
                            {
                                "timestamp": utc_now(),
                                "symbol": settings.bot_symbol,
                                "event_type": "ORDER_SUBMIT_FAILED",
                                "signal_id": signal_id,
                                "decision": signal.decision,
                                "confidence": signal.confidence,
                                "bullish_score": signal.bullish_score,
                                "bearish_score": signal.bearish_score,
                                "no_trade_score": signal.no_trade_score,
                                "regime": signal.regime,
                                "reason": signal.reason,
                                "model_version": ml_result.model_version,
                                "model_prediction": ml_result.predicted_direction,
                                "probability_long": ml_result.probability_long,
                                "probability_short": ml_result.probability_short,
                                "probability_no_trade": ml_result.probability_no_trade,
                                "client_order_id": plan.client_order_id,
                                "side": plan.side,
                                "qty": plan.qty,
                                "price": plan.entry_limit_price,
                                "notional": plan.estimated_notional,
                                "status": "submit_failed",
                                "exploration_trade": features.get("paper_exploration"),
                                **_journal_feature_fields(features),
                                "feature_snapshot_json": features,
                                "broker_snapshot_json": {"error": str(exc)},
                            }
                        )
                        _record_decision_execution(
                            db,
                            signal_id=signal_id,
                            signal=signal,
                            features=features,
                            ml_result=ml_result,
                            executed_action="NO_TRADE",
                            status="submission_failed",
                            client_order_id=plan.client_order_id,
                            error=str(exc),
                        )

            completed += 1
            if loops and completed >= loops:
                break
            time.sleep(seconds_until_next_minute())
    except KeyboardInterrupt:
        db.log_event("INFO", __name__, "shutdown", "Paper bot interrupted by user", {})
        raise
    finally:
        execution_safety.state.freeze("process_shutdown")
        if broker_order_stream is not None:
            broker_order_stream.stop()
        if fast_scalp_runtime is not None:
            fast_scalp_runtime.stop()
        if position_runtime is not None:
            position_runtime.stop()
        transformer_runtime.stop()
        shutdown_flat = execution_safety.stop(flatten=settings.execution_flatten_on_shutdown)
        if not shutdown_flat:
            logger.critical(
                "paper bot shutdown could not confirm a flat GLD account",
                extra={"event_type": "execution_safety_shutdown_failed"},
            )
        if options_runtime is not None:
            options_runtime.stop()
        if stream_runtime is not None:
            stream_runtime.stop()
        if not research_data_scheduler.stop(timeout=5):
            logger.warning(
                "background research collector did not finish before shutdown timeout",
                extra={"event_type": "research_data_shutdown_timeout"},
            )
        try:
            order_reconciler.sync(utc_now())
            performance_tracker.capture("shutdown", force=True)
            audit = run_performance_consistency_audit(
                db,
                settings,
                trading_client,
                stage="shutdown",
            )
            if not audit["consistent"]:
                logger.error(
                    "shutdown performance consistency audit failed reasons=%s",
                    "; ".join(audit["reasons"]),
                    extra={"event_type": "performance_consistency_failed"},
                )
        except Exception as exc:
            logger.exception(
                "shutdown performance accounting failed: %s",
                exc,
                extra={"event_type": "performance_shutdown_failed"},
            )
        order_coordinator.stop()
        latency_tracker.stop()

    return


def _journal_feature_fields(features: dict) -> dict[str, object]:
    return {
        "pattern_classification": features.get("pattern_classification"),
        "pattern_quality": features.get("pattern_quality"),
        "liquidity_score": features.get("liquidity_score"),
        "volatility_regime": features.get("gold_volatility_regime"),
        "reasoning_agents_json": features.get("agent_reasoning_json"),
        "macro_bias": features.get("macro_bias"),
        "macro_confidence": features.get("macro_confidence"),
        "target_exposure_pct": features.get("target_exposure_pct"),
        "order_block_direction": features.get("order_block_direction"),
        "order_block_timeframe": features.get("order_block_timeframe"),
        "order_block_strength": features.get("order_block_strength"),
        "order_block_retest_active": features.get("order_block_retest_active"),
        "options_bias": features.get("options_bias"),
        "options_confidence": features.get("options_confidence"),
        "options_score_adjustment": features.get("options_score_adjustment"),
        "options_event_risk": features.get("options_event_risk"),
    }


def _record_decision_execution(
    database: Database,
    *,
    signal_id: int,
    signal: MarketSignal,
    features: dict,
    ml_result: MLPrediction,
    executed_action: str,
    status: str,
    client_order_id: str | None = None,
    error: str | None = None,
    direction_available: bool = True,
) -> None:
    database.upsert_decision_execution(
        {
            "decision_source": "signal",
            "decision_id": signal_id,
            "timestamp": signal.timestamp,
            "symbol": signal.symbol,
            "strategy_path": str(features.get("strategy_path") or "minute"),
            "playbook": features.get("playbook"),
            "original_action": signal.decision,
            "executed_action": executed_action,
            "execution_status": status,
            "client_order_id": client_order_id,
            "root_episode_id": client_order_id,
            "model_scope": features.get("ml_model_scope"),
            "model_version": ml_result.model_version,
            "spread_pct": features.get("spread_pct"),
            "expected_slippage_pct": database.settings.estimated_round_trip_slippage_pct,
            "fill_quality_score": features.get("liquidity_score"),
            "direction_available": direction_available,
            "session_phase": features.get("time_of_day_profile"),
            "execution_error": error,
            "features": features,
        }
    )
    ema_cross_ids = [
        int(item)
        for item in features.get("ema_cross_signal_ids", [])
        if int(item) > 0
    ]
    if not ema_cross_ids:
        return
    database.update_ema_cross_signals(
        ema_cross_ids,
        execution_status=status,
        signal_id=signal_id,
        client_order_id=client_order_id,
        block_reason=error,
    )
    for ema_cross_id in ema_cross_ids:
        database.upsert_decision_execution(
            {
                "decision_source": "ema_cross",
                "decision_id": ema_cross_id,
                "timestamp": features.get(
                    "ema_cross_bar_close_timestamp",
                    signal.timestamp,
                ),
                "symbol": signal.symbol,
                "strategy_path": "ema_cross",
                "playbook": "ema_cross_filtered",
                "original_action": features.get(
                    "ema_cross_direction",
                    signal.decision,
                ),
                "executed_action": executed_action,
                "execution_status": status,
                "client_order_id": client_order_id,
                "root_episode_id": client_order_id,
                "model_scope": features.get("ml_model_scope"),
                "model_version": ml_result.model_version,
                "spread_pct": features.get("spread_pct"),
                "expected_slippage_pct": (
                    database.settings.estimated_round_trip_slippage_pct
                ),
                "fill_quality_score": features.get("liquidity_score"),
                "direction_available": direction_available,
                "session_phase": features.get("time_of_day_profile"),
                "execution_error": error,
                "features": features,
            }
        )


def _refresh_broker_risk_state(
    risk_state: RiskState,
    trading_client,
    symbol: str,
    database: Database,
    settings,
) -> None:
    try:
        account = trading_client.get_account()
        risk_state.account_equity = float(getattr(account, "equity", risk_state.account_equity) or risk_state.account_equity)
        risk_state.day_start_equity = risk_state.account_equity
        positions = trading_client.get_all_positions()
        related = {item.upper() for item in settings.related_symbols}
        risk_state.correlated_exposure_notional = sum(
            abs(float(getattr(position, "market_value", 0.0) or 0.0))
            for position in positions
            if str(getattr(position, "symbol", "")).upper() in related
        )
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        open_orders = trading_client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol.upper()], nested=True)
        )
        populate_concurrent_risk_state(
            risk_state,
            symbol=symbol,
            positions=list(positions),
            open_orders=list(open_orders),
            database=database,
            settings=settings,
        )
        risk_state.position_reconciled = True
    except Exception:
        risk_state.position_reconciled = False


def _check_shortability_for_plan(trading_client, symbol: str, required_notional: float) -> ShortabilityResult:
    try:
        asset = trading_client.get_asset(symbol.upper())
        account = trading_client.get_account()
        buying_power = float(getattr(account, "buying_power", 0.0) or 0.0)
        return check_asset_shortability(asset, buying_power=buying_power, required_notional=required_notional)
    except Exception as exc:
        return ShortabilityResult(False, f"shortability check failed: {exc}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gld-scalper", description="Paper-only Alpaca GLD scalping bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db = subparsers.add_parser("init-db", help="Create SQLite database and tables")
    init_db.set_defaults(func=init_db_command)

    backfill = subparsers.add_parser("backfill", help="Download missing historical bars from Alpaca")
    backfill.add_argument("--symbols", nargs="+", default=["GLD", "IAU", "SLV", "GDX", "UUP", "TLT", "SPY", "QQQ"])
    backfill.add_argument("--days", type=int, default=90)
    backfill.set_defaults(func=backfill_command)

    run_paper = subparsers.add_parser("run-paper", help="Start the live paper trading bot")
    run_paper.add_argument("--once", action="store_true", help="Run one evaluation loop for smoke testing")
    run_paper.add_argument("--no-stream", action="store_true", help="Disable live stream startup for local smoke testing")
    run_paper.add_argument("--no-retraining", action="store_true", help="Disable scheduled retraining during this run")
    run_paper.set_defaults(func=run_paper_command)

    train = subparsers.add_parser("train", help="Train candidate ML model")
    train.add_argument("--lookback-days", type=int, default=90)
    train.add_argument("--use-llm-labels", action="store_true", help="Allow high-confidence LLM labels to fill unlabeled trade signals")
    train.set_defaults(func=train_command)

    transformer_data = subparsers.add_parser(
        "build-transformer-dataset",
        help="Build a memory-efficient causal sequence artifact for one independent Transformer scope",
    )
    transformer_data.add_argument("--database", help="Source SQLite path; defaults to the active paper database")
    transformer_data.add_argument(
        "--scope",
        required=True,
        choices=["fast_microstructure", "minute", "news_event", "exit"],
    )
    transformer_data.add_argument("--start", required=True)
    transformer_data.add_argument("--end", required=True)
    transformer_data.add_argument("--output", help="Artifact directory; defaults inside data/paper/ml_training/transformer")
    transformer_data.add_argument("--source", choices=["auto", "raw", "decisions"], default="auto")
    transformer_data.add_argument("--sequence-length", type=int)
    transformer_data.add_argument("--window-seconds", type=int)
    transformer_data.add_argument("--stride", type=int, default=1)
    transformer_data.add_argument("--max-samples", type=int, default=50_000)
    transformer_data.add_argument("--max-features", type=int, default=64)
    transformer_data.add_argument("--overwrite", action="store_true")
    transformer_data.set_defaults(func=build_transformer_dataset_command)

    transformer_train = subparsers.add_parser(
        "train-transformer",
        help="Train and register a compact causal Transformer as a paper-shadow candidate",
    )
    transformer_train.add_argument("--artifact", required=True)
    transformer_train.add_argument("--d-model", type=int, choices=[32, 48, 64], default=48)
    transformer_train.add_argument("--layers", type=int, choices=[2, 3], default=2)
    transformer_train.add_argument("--feedforward-dimension", type=int, default=128)
    transformer_train.add_argument("--dropout", type=float, default=0.10)
    transformer_train.add_argument("--batch-size", type=int, default=32)
    transformer_train.add_argument("--epochs", type=int, default=15)
    transformer_train.add_argument("--learning-rate", type=float, default=0.0003)
    transformer_train.add_argument("--weight-decay", type=float, default=0.0001)
    transformer_train.add_argument("--patience", type=int, default=4)
    transformer_train.add_argument("--walk-forward-folds", type=int, default=3)
    transformer_train.add_argument("--walk-forward-epochs", type=int, default=4)
    transformer_train.add_argument("--minimum-confidence", type=float, default=0.58)
    transformer_train.add_argument("--minimum-margin", type=float, default=0.08)
    transformer_train.add_argument("--seed", type=int, default=73)
    transformer_train.add_argument("--export-onnx", action="store_true")
    transformer_train.set_defaults(func=train_transformer_command)

    transformer_loop = subparsers.add_parser(
        "transformer-train-loop",
        help="Run resumable, separate, after-hours Transformer searches with historical replay and paper updates",
    )
    transformer_loop.add_argument(
        "--artifact",
        action="append",
        required=True,
        help="Repeat scope=path for fast_microstructure, minute, news_event, and exit artifacts",
    )
    transformer_loop.add_argument("--watch", action="store_true", help="Wait for new paper labels after convergence")
    transformer_loop.add_argument("--interval-minutes", type=int, default=60)
    transformer_loop.add_argument("--maximum-cycles", type=int, default=0)
    transformer_loop.add_argument("--epochs", type=int, default=8)
    transformer_loop.add_argument("--walk-forward-epochs", type=int, default=2)
    transformer_loop.add_argument("--batch-size", type=int, default=32)
    transformer_loop.add_argument("--maximum-paper-samples", type=int, default=20_000)
    transformer_loop.add_argument("--no-improvement-patience", type=int, default=3)
    transformer_loop.add_argument("--minimum-improvement", type=float, default=0.001)
    transformer_loop.add_argument("--clear-stop", action="store_true")
    transformer_loop.set_defaults(func=transformer_train_loop_command)

    transformer_stop = subparsers.add_parser(
        "stop-transformer-training",
        help="Request a clean stop at the next Transformer loop checkpoint",
    )
    transformer_stop.set_defaults(func=stop_transformer_training_command)

    transformer_status = subparsers.add_parser(
        "transformer-status",
        help="Show separate Transformer candidates, validation summaries, and shadow prediction counts",
    )
    transformer_status.set_defaults(func=transformer_status_command)

    transformer_paper = subparsers.add_parser(
        "evaluate-transformer-paper",
        help="Join shadow predictions to paper outcome labels and update after-cost registry evidence",
    )
    transformer_paper.add_argument("--model-version")
    transformer_paper.set_defaults(func=evaluate_transformer_paper_command)

    transformer_promote = subparsers.add_parser(
        "promote-transformer",
        help="Apply all strict baseline, walk-forward, cost, latency, regime, and paper promotion gates",
    )
    transformer_promote.add_argument("--model-version", required=True)
    transformer_promote.set_defaults(func=promote_transformer_command)

    build_ml_archive = subparsers.add_parser(
        "build-ml-archive",
        help="Build a leakage-safe, cost-aware supervised ML artifact from a historical SQLite archive",
    )
    build_ml_archive.add_argument("--database", required=True, help="Path to the historical SQLite database")
    build_ml_archive.add_argument("--start", required=True)
    build_ml_archive.add_argument("--end", required=True)
    build_ml_archive.add_argument("--artifact-name", default="gld_archive_training_v3")
    build_ml_archive.add_argument("--stride-minutes", type=int, default=5)
    build_ml_archive.add_argument("--horizon-minutes", type=int, default=5)
    build_ml_archive.add_argument("--horizons", nargs="+", type=int, default=[1, 3, 5, 15])
    build_ml_archive.add_argument("--max-samples", type=int, default=None)
    build_ml_archive.add_argument("--slippage-pct", type=float, default=0.0001)
    build_ml_archive.add_argument("--minimum-edge-pct", type=float, default=0.0002)
    build_ml_archive.set_defaults(func=build_ml_archive_command)

    train_archive = subparsers.add_parser(
        "train-archive",
        help="Train, validate, and safely register a candidate from a saved historical ML artifact",
    )
    train_archive.add_argument("--artifact", required=True)
    train_archive.set_defaults(func=train_archive_command)

    train_loop = subparsers.add_parser(
        "train-loop",
        help="Run resumable playbook/multi-horizon shadow training and skip completed fingerprints",
    )
    train_loop.add_argument("--artifact", required=True)
    train_loop.add_argument("--horizons", nargs="+", type=int, default=None)
    train_loop.add_argument("--playbooks", nargs="+", default=None)
    train_loop.add_argument("--minimum-samples", type=int, default=None)
    train_loop.add_argument("--paper-weight", type=int, default=None)
    train_loop.add_argument("--watch", action="store_true", help="Stay alive and start a new cycle only after enough new paper labels")
    train_loop.add_argument(
        "--continuous-historical",
        action="store_true",
        help="Continuously search new historical model configurations until a graceful stop is requested",
    )
    train_loop.add_argument("--interval-minutes", type=int, default=None)
    train_loop.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Zero means unlimited in watch or continuous-historical mode",
    )
    train_loop.add_argument(
        "--patience-rounds",
        type=int,
        default=3,
        help="Stop continuous historical search after this many rounds without meaningful walk-forward improvement",
    )
    train_loop.add_argument(
        "--minimum-improvement",
        type=float,
        default=0.001,
        help="Required absolute walk-forward net-return improvement to reset the patience counter",
    )
    train_loop.add_argument("--retry-failed", action="store_true")
    train_loop.add_argument("--clear-stop", action="store_true", help="Remove a previous graceful-stop request before starting")
    train_loop.set_defaults(func=train_loop_command)

    loop_status = subparsers.add_parser("training-loop-status", help="Show continual-training state and experiment counts")
    loop_status.set_defaults(func=training_loop_status_command)

    stop_loop = subparsers.add_parser("stop-training-loop", help="Request a graceful stop after the current model fit")
    stop_loop.set_defaults(func=stop_training_loop_command)

    drift_report = subparsers.add_parser("ml-drift-report", help="Measure recent live feature drift against the champion model")
    drift_report.add_argument("--limit", type=int, default=1000)
    drift_report.set_defaults(func=ml_drift_report_command)

    walk_forward = subparsers.add_parser("walk-forward", help="Run rolling walk-forward validation for ML labels")
    walk_forward.add_argument("--lookback-days", type=int, default=730)
    walk_forward.add_argument("--policy-name", default="random_forest_walk_forward")
    walk_forward.set_defaults(func=walk_forward_command)

    backtest = subparsers.add_parser("backtest", help="Run backtest")
    backtest.add_argument("--start", required=True)
    backtest.add_argument("--end", required=True)
    backtest.set_defaults(func=backtest_command)

    report = subparsers.add_parser("report", help="Generate daily trading report")
    report.add_argument("--date", default="today")
    report.set_defaults(func=report_command)

    export_csv = subparsers.add_parser("export-csv", help="Export SQLite tables to CSV files")
    export_csv.add_argument("--output-dir", default=None, help="Override CSV export directory")
    export_csv.set_defaults(func=export_csv_command)

    research_data = subparsers.add_parser("collect-research-data", help="Collect news, macro, event, calendar, knowledge, and label data for AI research")
    research_data.add_argument("--start", default=None, help="Start date/time, defaults to --days ago")
    research_data.add_argument("--end", default=None, help="End date/time, defaults to now")
    research_data.add_argument("--days", type=int, default=7)
    research_data.add_argument("--no-news-content", action="store_true", help="Do not request full Alpaca article content")
    research_data.set_defaults(func=collect_research_data_command)

    outcome_labels = subparsers.add_parser(
        "label-paper-outcomes",
        help="Create cost-aware 1, 3, 5, and 15-minute labels for paper decisions",
    )
    outcome_labels.add_argument("--start", default=None, help="Optional inclusive UTC start date/time")
    outcome_labels.add_argument("--end", default=None, help="Optional inclusive UTC end date/time")
    outcome_labels.add_argument("--limit", type=int, default=100_000, help="Maximum decisions to label in this run")
    outcome_labels.add_argument(
        "--retry-partial",
        action="store_true",
        help="Retry decisions previously labeled with only some horizons after repaired price data is available",
    )
    outcome_labels.add_argument(
        "--sources",
        nargs="+",
        choices=["signal", "fast_scalp", "ema_cross"],
        default=["signal", "fast_scalp", "ema_cross"],
    )
    outcome_labels.set_defaults(func=label_paper_outcomes_command)

    macro_context = subparsers.add_parser("update-macro-context", help="Build and save slow macro/sentiment context")
    macro_context.set_defaults(func=update_macro_context_command)

    review_coach = subparsers.add_parser("review-coach", help="Run local RAG-style bot coach over knowledge, SQLite, and exports")
    review_coach.add_argument("--knowledge-dir", default="Knowledge", help="Knowledge folder to retrieve from")
    review_coach.add_argument("--query", default="GLD scalping performance risk execution missed opportunities")
    review_coach.add_argument("--limit", type=int, default=8)
    review_coach.set_defaults(func=review_coach_command)

    rl_preview = subparsers.add_parser("rl-preview", help="Run offline FinRL-style policy preview from SQLite bars")
    rl_preview.add_argument("--start", default=None)
    rl_preview.add_argument("--end", default=None)
    rl_preview.add_argument("--policy-name", default="momentum_preview")
    rl_preview.set_defaults(func=rl_preview_command)

    llm_status = subparsers.add_parser(
        "llm-provider-status",
        help="Show redacted offline LLM provider configuration and local Kimi Tier0 usage",
    )
    llm_status.set_defaults(func=llm_provider_status_command)

    llm_analyze = subparsers.add_parser("llm-analyze", help="Use the configured offline LLM to analyze SQLite data and latest CSV exports")
    llm_analyze.add_argument("--query", default="Analyze bot performance, missed opportunities, journal quality, and ML improvements.")
    llm_analyze.set_defaults(func=llm_analyze_command)

    llm_macro = subparsers.add_parser("llm-macro-context", help="Use the configured offline LLM to build macro/sentiment context")
    llm_macro.set_defaults(func=llm_macro_context_command)

    llm_offline = subparsers.add_parser("llm-offline-cycle", help="Run offline FinGPT/LLM research with no broker-order access")
    llm_offline.add_argument("--cadence", choices=["hourly", "daily"], default="daily")
    llm_offline.add_argument("--force", action="store_true", help="Allow a maintenance run during market hours only when no episodes are open")
    llm_offline.set_defaults(func=llm_offline_cycle_command)

    tradingagents_advisory = subparsers.add_parser(
        "tradingagents-advisory",
        help="Run the offline TradingAgents-style GLD research council and save a bounded advisory",
    )
    tradingagents_advisory.add_argument("--horizon", choices=["hourly", "daily"], default="hourly")
    tradingagents_advisory.add_argument(
        "--force",
        action="store_true",
        help="Allow deliberate market-hours research only when no execution episode is active",
    )
    tradingagents_advisory.set_defaults(func=tradingagents_advisory_command)

    train_exit = subparsers.add_parser("train-exit-model", help="Train a separate advisory exit model after enough trustworthy outcomes exist")
    train_exit.add_argument("--strategy-path", choices=["all", "fast", "minute"], default="all")
    train_exit.add_argument("--playbook", default="all")
    train_exit.set_defaults(func=train_exit_model_command)

    rollback_model = subparsers.add_parser("rollback-model", help="Restore a preserved champion artifact for one model scope")
    rollback_model.add_argument("--scope", required=True)
    rollback_model.add_argument("--model-version", required=True)
    rollback_model.add_argument("--reason", default="operator rollback")
    rollback_model.set_defaults(func=rollback_model_command)

    llm_advice = subparsers.add_parser("llm-training-advice", help="Use the configured offline LLM to generate structured ML training advice")
    llm_advice.set_defaults(func=llm_training_advice_command)

    llm_labels = subparsers.add_parser("llm-label-signals", help="Use the configured offline LLM to suggest advisory ML labels for recent signals")
    llm_labels.add_argument("--limit", type=int, default=25)
    llm_labels.set_defaults(func=llm_label_signals_command)

    llm_train = subparsers.add_parser("llm-train-candidate", help="Label recent signals with the offline LLM, create advice, then train a candidate")
    llm_train.add_argument("--lookback-days", type=int, default=90)
    llm_train.add_argument("--label-limit", type=int, default=25)
    llm_train.add_argument("--with-advice", action="store_true")
    llm_train.set_defaults(func=llm_train_candidate_command)

    reset_data = subparsers.add_parser("reset-data", help="Delete collected SQLite data and optionally clear logs/exports")
    reset_data.add_argument("--yes", action="store_true", help="Required confirmation for deleting collected data")
    reset_data.add_argument("--clear-exports", action="store_true", help="Delete CSV export folders")
    reset_data.add_argument("--clear-logs", action="store_true", help="Clear logs/bot.log")
    reset_data.set_defaults(func=reset_data_command)

    status = subparsers.add_parser("status", help="Show bot status")
    status.set_defaults(func=status_command)

    dashboard = subparsers.add_parser("dashboard", help="Launch the local GLD operations dashboard")
    dashboard.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost", "::1"])
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    dashboard.set_defaults(func=dashboard_command)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
