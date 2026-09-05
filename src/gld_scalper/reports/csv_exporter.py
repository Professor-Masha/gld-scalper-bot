from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from ..config import PROJECT_ROOT, Settings
from ..database import Database
from ..utils.time_utils import utc_iso, utc_now

EXPORT_TABLES = [
    "bars",
    "bar_variants",
    "quotes",
    "market_trades",
    "account_snapshots",
    "orders",
    "decision_executions",
    "fills",
    "positions",
    "trade_outcomes",
    "trade_reviews",
    "trading_journal",
    "missed_opportunities",
    "macro_context",
    "news_items",
    "economic_events",
    "fair_value_gaps",
    "macro_series",
    "market_calendar",
    "microstructure_features",
    "data_gaps",
    "stream_diagnostics",
    "price_action_labels",
    "order_block_zones",
    "option_snapshots",
    "options_intelligence",
    "outcome_labels",
    "playbook_evaluations",
    "model_promotion_audits",
    "knowledge_artifacts",
    "data_source_runs",
    "llm_reviews",
    "agent_advisories",
    "llm_training_advice",
    "llm_signal_labels",
    "rl_experiments",
    "ema_cross_signals",
    "signals",
    "no_trade_logs",
    "model_predictions",
    "transformer_predictions",
    "model_drift_reports",
    "ml_training_experiments",
    "fast_scalp_decisions",
    "price_snapshots",
    "position_management_state",
    "position_management_events",
    "execution_episodes",
    "execution_episode_orders",
    "order_intents",
    "execution_safety_events",
    "execution_latency_events",
    "performance_consistency_audits",
    "model_versions",
    "model_champion_history",
    "llm_offline_cycles",
    "system_logs",
]

EXPORT_FILTER_COLUMNS = {
    "bars": "created_at",
    "bar_variants": "created_at",
    "quotes": "created_at",
    "market_trades": "created_at",
    "account_snapshots": "timestamp",
    "orders": "submitted_at",
    "decision_executions": "timestamp",
    "fills": "timestamp",
    "positions": "opened_at",
    "trade_outcomes": "entry_time",
    "trade_reviews": "created_at",
    "trading_journal": "created_at",
    "missed_opportunities": "created_at",
    "macro_context": "created_at",
    "news_items": "created_at",
    "economic_events": "created_at",
    "fair_value_gaps": "updated_at",
    "macro_series": "created_at",
    "market_calendar": "created_at",
    "microstructure_features": "created_at",
    "data_gaps": "created_at",
    "stream_diagnostics": "created_at",
    "price_action_labels": "created_at",
    "order_block_zones": "created_at",
    "option_snapshots": "created_at",
    "options_intelligence": "created_at",
    "outcome_labels": "created_at",
    "playbook_evaluations": "created_at",
    "model_promotion_audits": "created_at",
    "knowledge_artifacts": "created_at",
    "data_source_runs": "created_at",
    "llm_reviews": "created_at",
    "agent_advisories": "created_at",
    "llm_training_advice": "created_at",
    "llm_signal_labels": "created_at",
    "rl_experiments": "created_at",
    "ema_cross_signals": "created_at",
    "signals": "created_at",
    "no_trade_logs": "timestamp",
    "model_predictions": "timestamp",
    "transformer_predictions": "timestamp",
    "model_drift_reports": "created_at",
    "ml_training_experiments": "created_at",
    "fast_scalp_decisions": "created_at",
    "price_snapshots": "created_at",
    "position_management_state": "updated_at",
    "position_management_events": "created_at",
    "execution_episodes": "updated_at",
    "execution_episode_orders": "updated_at",
    "order_intents": "created_at",
    "execution_safety_events": "timestamp",
    "execution_latency_events": "timestamp",
    "performance_consistency_audits": "timestamp",
    "model_versions": "created_at",
    "model_champion_history": "timestamp",
    "llm_offline_cycles": "timestamp",
    "system_logs": "timestamp",
}

EXPORT_ORDER_COLUMNS = {
    "position_management_state": "trade_id",
    "execution_episodes": "episode_id",
    "execution_episode_orders": "order_key",
    "order_intents": "intent_id",
}

EXPORT_TABLE_CATEGORIES = {
    "bars": "market_data",
    "bar_variants": "market_data",
    "quotes": "microstructure",
    "market_trades": "microstructure",
    "microstructure_features": "microstructure",
    "stream_diagnostics": "diagnostics",
    "data_gaps": "diagnostics",
    "market_calendar": "calendar",
    "economic_events": "events",
    "fair_value_gaps": "technical_structure",
    "news_items": "news",
    "macro_context": "macro",
    "macro_series": "macro",
    "account_snapshots": "broker",
    "orders": "broker",
    "decision_executions": "strategy",
    "fills": "broker",
    "positions": "broker",
    "trade_outcomes": "outcomes",
    "trade_reviews": "learning",
    "outcome_labels": "outcomes",
    "trading_journal": "journal",
    "missed_opportunities": "learning",
    "price_action_labels": "patterns",
    "order_block_zones": "patterns",
    "option_snapshots": "options",
    "options_intelligence": "options",
    "playbook_evaluations": "strategy",
    "ema_cross_signals": "strategy",
    "signals": "strategy",
    "no_trade_logs": "strategy",
    "model_predictions": "ml",
    "transformer_predictions": "ml",
    "model_drift_reports": "ml",
    "ml_training_experiments": "ml",
    "fast_scalp_decisions": "microstructure",
    "price_snapshots": "microstructure",
    "position_management_state": "position_management",
    "position_management_events": "position_management",
    "execution_episodes": "execution_safety",
    "execution_episode_orders": "execution_safety",
    "order_intents": "execution_safety",
    "execution_safety_events": "execution_safety",
    "execution_latency_events": "performance",
    "performance_consistency_audits": "performance",
    "model_versions": "ml",
    "model_champion_history": "ml",
    "model_promotion_audits": "ml",
    "llm_reviews": "llm",
    "agent_advisories": "llm",
    "llm_training_advice": "llm",
    "llm_signal_labels": "llm",
    "llm_offline_cycles": "llm",
    "rl_experiments": "rl",
    "knowledge_artifacts": "knowledge",
    "data_source_runs": "ingestion",
    "system_logs": "diagnostics",
}


@dataclass(slots=True)
class CSVExportResult:
    export_dir: Path
    files: dict[str, int]
    created_at: datetime
    since: datetime | None = None
    until: datetime | None = None


class HourlyCSVExportScheduler:
    def __init__(self, settings: Settings, start_at: datetime | None = None) -> None:
        self.settings = settings
        self._last_export_at: datetime | None = start_at or utc_now()

    def maybe_export(self, database: Database, now: datetime | None = None, *, force: bool = False) -> CSVExportResult | None:
        now = now or utc_now()
        if not self.settings.enable_hourly_csv_export and not force:
            return None
        since = self._last_export_at
        if not force and since is not None:
            next_export = since + timedelta(minutes=self.settings.csv_export_interval_minutes)
            if now < next_export:
                return None
        result = export_database_to_csv(database, self.settings.csv_export_dir, run_at=now, since=since, until=now)
        self._last_export_at = now
        return result


def export_database_to_csv(
    database: Database,
    export_dir: str | Path = "exports/paper/hourly",
    run_at: datetime | None = None,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> CSVExportResult:
    run_at = run_at or utc_now()
    root = _resolve_export_root(export_dir)
    root.mkdir(parents=True, exist_ok=True)
    stamp = run_at.strftime("%Y%m%d_%H%M%S")
    target_dir = root / f"export_{stamp}"
    target_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, int] = {}
    for table in EXPORT_TABLES:
        flat_path = target_dir / f"{table}.csv"
        files[table] = _export_table(database, table, flat_path, since=since, until=until)
        table_dir = target_dir / EXPORT_TABLE_CATEGORIES.get(table, "other") / table
        table_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(flat_path, table_dir / f"{table}.csv")
    _write_manifest(target_dir, run_at, files, since=since, until=until)
    _refresh_latest(root, target_dir)
    return CSVExportResult(export_dir=target_dir, files=files, created_at=run_at, since=since, until=until)


def _resolve_export_root(export_dir: str | Path) -> Path:
    path = Path(export_dir)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _export_table(database: Database, table: str, path: Path, *, since: datetime | None, until: datetime | None) -> int:
    if table not in EXPORT_TABLES:
        raise ValueError(f"Unsupported export table: {table}")
    params: list[str] = []
    clauses: list[str] = []
    filter_column = EXPORT_FILTER_COLUMNS[table]
    if since is not None:
        clauses.append(f"{filter_column} >= ?")
        params.append(utc_iso(since))
    if until is not None:
        clauses.append(f"{filter_column} <= ?")
        params.append(utc_iso(until))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    order_column = EXPORT_ORDER_COLUMNS.get(table, "id")
    cursor = database.conn.execute(f"SELECT * FROM {table}{where} ORDER BY {order_column} ASC", params)
    columns = [description[0] for description in cursor.description]
    count = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        while True:
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            for row in rows:
                writer.writerow([row[column] for column in columns])
                count += 1
    return count


def _write_manifest(target_dir: Path, run_at: datetime, files: dict[str, int], *, since: datetime | None, until: datetime | None) -> None:
    path = target_dir / "manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["created_at", "window_since", "window_until", "category", "table", "rows", "filename", "folder_filename"])
        for table, rows in files.items():
            category = EXPORT_TABLE_CATEGORIES.get(table, "other")
            writer.writerow(
                [
                    run_at.isoformat(),
                    since.isoformat() if since is not None else "",
                    until.isoformat() if until is not None else "",
                    category,
                    table,
                    rows,
                    f"{table}.csv",
                    f"{category}/{table}/{table}.csv",
                ]
            )


def _refresh_latest(root: Path, target_dir: Path) -> None:
    latest_dir = root.parent / "latest"
    if latest_dir.exists():
        shutil.rmtree(latest_dir)
    shutil.copytree(target_dir, latest_dir)
