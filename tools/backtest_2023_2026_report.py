from __future__ import annotations

# ruff: noqa: E402

import argparse
import csv
import json
import math
import shutil
import sys
import time
import zipfile
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from gld_scalper.alpaca_clients import get_stock_historical_client
from gld_scalper.backtester import Backtester, calculate_metrics
from gld_scalper.config import load_settings
from gld_scalper.data_collector import _alpaca_timeframe, _bars_response_to_records
from gld_scalper.database import Database
from gld_scalper.models import BacktestResult, BacktestTrade
from gld_scalper.reports.csv_exporter import export_database_to_csv
from gld_scalper.utils.time_utils import EASTERN, ensure_utc, utc_now

DATASET_NAME = "gld_2023_2026"
START = datetime(2023, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 1, 1, tzinfo=timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or reuse a historical GLD dataset, run backtest, and write DOCX report.")
    parser.add_argument("--dataset-name", default=DATASET_NAME, help="Backtest dataset/report folder name.")
    parser.add_argument("--start", default=START.date().isoformat(), help="Start date, inclusive, YYYY-MM-DD.")
    parser.add_argument("--end", default=END.date().isoformat(), help="End date, exclusive, YYYY-MM-DD.")
    parser.add_argument("--source-db", help="Existing SQLite historical database to copy into a separate backtest database.")
    parser.add_argument("--skip-download", action="store_true", help="Use existing SQLite bars and skip Alpaca download.")
    parser.add_argument("--skip-export", action="store_true", help="Skip the large full-table CSV export.")
    parser.add_argument("--reuse-existing-trades", action="store_true", help="Build the report from existing backtest trade_outcomes without rerunning simulation.")
    args = parser.parse_args()

    configure_run(args.dataset_name, args.start, args.end)

    paths = build_paths()
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

    if args.source_db and not args.reuse_existing_trades:
        copy_source_database(Path(args.source_db), paths)

    settings = build_settings(paths)
    db = Database(settings=settings)
    db.init_db()

    if args.reuse_existing_trades:
        print("Reusing existing backtest trade_outcomes; simulation will not rerun.", flush=True)
    elif args.source_db:
        print(f"Using copied historical source database: {args.source_db}", flush=True)
    elif not args.skip_download:
        download_historical_dataset(db, settings)
    else:
        print("Skipping historical download; using existing dataset.", flush=True)

    data_coverage = dataset_coverage(db)
    result = load_existing_backtest_result(db, settings) if args.reuse_existing_trades else run_backtest(db, settings)
    analysis = analyze_result(result)
    write_analysis_files(paths, data_coverage, analysis, result)

    export_result = None
    if not args.skip_export:
        export_result = export_database_to_csv(db, paths["exports_hourly"], run_at=utc_now())
        print(f"CSV export completed: {export_result.export_dir}", flush=True)

    report_path = paths["reports"] / f"GLD_Backtest_Report_{DATASET_NAME}.docx"
    write_docx_report(report_path, paths, data_coverage, analysis, result, export_result)
    print(json.dumps({"report": str(report_path), "metrics": result.metrics}, indent=2, sort_keys=True), flush=True)


def configure_run(dataset_name: str, start: str, end: str) -> None:
    global DATASET_NAME, START, END
    safe_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in dataset_name)
    DATASET_NAME = safe_name
    START = parse_utc_date(start)
    END = parse_utc_date(end)
    if END <= START:
        raise SystemExit("--end must be after --start.")


def parse_utc_date(value: str) -> datetime:
    return datetime.fromisoformat(f"{value}T00:00:00+00:00").astimezone(timezone.utc)


def build_paths() -> dict[str, Path]:
    return {
        "dataset": PROJECT_ROOT / "data" / "paper" / "backtests" / DATASET_NAME,
        "exports": PROJECT_ROOT / "exports" / "paper" / "backtests" / DATASET_NAME,
        "exports_hourly": PROJECT_ROOT / "exports" / "paper" / "backtests" / DATASET_NAME / "hourly",
        "reports": PROJECT_ROOT / "reports" / "backtests" / DATASET_NAME,
    }


def build_settings(paths: dict[str, Path]):
    base = load_settings()
    return replace(
        base,
        data_mode="paper",
        database_url=f"sqlite:///{paths['dataset'] / 'gld_scalper_backtest.db'}",
        csv_export_dir=str(paths["exports_hourly"]),
        enable_hourly_csv_export=False,
    )


def copy_source_database(source_db: Path, paths: dict[str, Path]) -> None:
    source_db = source_db.resolve()
    if not source_db.exists():
        raise SystemExit(f"Source database not found: {source_db}")
    target_db = paths["dataset"] / "gld_scalper_backtest.db"
    remove_sqlite_sidecars(target_db)
    if source_db != target_db.resolve():
        shutil.copy2(source_db, target_db)
    remove_sqlite_sidecars(target_db)
    source_manifest = source_db.parent / "dataset_manifest.json"
    manifest = {
        "dataset_name": DATASET_NAME,
        "start_utc": START.isoformat(),
        "end_utc_exclusive": END.isoformat(),
        "source_database": str(source_db),
        "backtest_database": str(target_db),
        "copied_at": utc_now().isoformat(),
    }
    if source_manifest.exists():
        try:
            manifest["source_manifest"] = json.loads(source_manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            manifest["source_manifest_path"] = str(source_manifest)
    (paths["dataset"] / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")


def remove_sqlite_sidecars(db_path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{db_path}{suffix}")
        if sidecar.exists():
            sidecar.unlink()


def download_historical_dataset(db: Database, settings) -> None:
    settings.validate_safety()
    client = get_stock_historical_client(settings)
    all_symbols = list(dict.fromkeys([settings.bot_symbol.upper(), *settings.related_symbols]))
    plan = [
        ("1Min", [settings.bot_symbol.upper()], 14),
        ("5Min", [settings.bot_symbol.upper()], 60),
        ("15Min", [settings.bot_symbol.upper()], 90),
        ("1Day", all_symbols, 365),
    ]
    for timeframe, symbols, chunk_days in plan:
        print(f"Downloading {timeframe} bars for {symbols} from {START.date()} to {END.date()}...", flush=True)
        inserted_total = 0
        chunk_start = START
        while chunk_start < END:
            chunk_end = min(chunk_start + timedelta(days=chunk_days), END)
            rows = download_chunk(client, settings, symbols, timeframe, chunk_start, chunk_end)
            rows = [row for row in rows if START <= ensure_utc(row["timestamp"]) < END]
            inserted = db.upsert_bars(rows)
            inserted_total += inserted
            print(
                f"  {timeframe} {chunk_start.date()} -> {chunk_end.date()}: fetched={len(rows)} upserted={inserted}",
                flush=True,
            )
            chunk_start = chunk_end
        print(f"Finished {timeframe}: upserted_or_seen={inserted_total}", flush=True)

    manifest = {
        "dataset_name": DATASET_NAME,
        "start_utc": START.isoformat(),
        "end_utc_exclusive": END.isoformat(),
        "source": "Alpaca historical stock bars",
        "feed": settings.alpaca_data_feed,
        "downloaded_at": utc_now().isoformat(),
        "download_plan": [
            {"timeframe": timeframe, "symbols": symbols, "chunk_days": chunk_days}
            for timeframe, symbols, chunk_days in plan
        ],
    }
    (PROJECT_ROOT / "data" / "paper" / "backtests" / DATASET_NAME / "dataset_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def download_chunk(client: Any, settings: Any, symbols: list[str], timeframe: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest

    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=_alpaca_timeframe(timeframe),
        start=start,
        end=end,
        feed=DataFeed.IEX if settings.alpaca_data_feed == "iex" else DataFeed.SIP,
    )
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = client.get_stock_bars(request)
            return _bars_response_to_records(response, timeframe)
        except Exception as exc:  # pragma: no cover - depends on network/API behavior
            last_error = exc
            wait_seconds = 5 * attempt
            print(f"  download retry {attempt}/3 after error: {exc}", flush=True)
            time.sleep(wait_seconds)
    raise RuntimeError(f"Alpaca bar download failed for {timeframe} {start} -> {end}: {last_error}")


def run_backtest(db: Database, settings):
    with db.conn:
        db.conn.execute("DELETE FROM trade_outcomes WHERE mode = 'backtest'")
    if (END - START).days > 370:
        return run_backtest_segmented(db, settings)
    return Backtester(db, settings).run(START, END)


def run_backtest_segmented(db: Database, settings) -> BacktestResult:
    trades = []
    no_trade_count = 0
    segment_start = START
    while segment_start < END:
        segment_end = min(datetime(segment_start.year + 1, 1, 1, tzinfo=timezone.utc), END)
        print(f"Running backtest segment {segment_start.date()} -> {segment_end.date()}...", flush=True)
        segment_result = Backtester(db, settings).run(segment_start, segment_end)
        trades.extend(segment_result.trades)
        no_trade_count += segment_result.no_trade_count
        print(
            "  segment complete "
            f"trades={len(segment_result.trades)} "
            f"net_pnl={segment_result.metrics.get('net_pnl', 0.0):.2f} "
            f"no_trade={segment_result.no_trade_count}",
            flush=True,
        )
        segment_start = segment_end
    metrics = calculate_metrics(trades, settings.paper_account_size)
    return BacktestResult(START, END, trades, no_trade_count, metrics)


def load_existing_backtest_result(db: Database, settings) -> BacktestResult:
    rows = db.conn.execute(
        """
        SELECT *
        FROM trade_outcomes
        WHERE mode = 'backtest'
          AND entry_time >= ?
          AND entry_time < ?
        ORDER BY entry_time ASC
        """,
        (START.isoformat(), END.isoformat()),
    ).fetchall()
    trades: list[BacktestTrade] = []
    for row in rows:
        item = dict(row)
        setup_type = str(item.get("setup_type") or "")
        decision_text = setup_type.removeprefix("decision_at:") if setup_type.startswith("decision_at:") else item.get("entry_time")
        trades.append(
            BacktestTrade(
                symbol=str(item.get("symbol") or "GLD"),
                direction=str(item.get("direction") or "LONG"),
                decision_time=ensure_utc(decision_text),
                entry_time=ensure_utc(item.get("entry_time")),
                exit_time=ensure_utc(item.get("exit_time")),
                entry_price=float(item.get("entry_price") or 0.0),
                exit_price=float(item.get("exit_price") or 0.0),
                qty=int(float(item.get("qty") or 0)),
                gross_pnl=float(item.get("gross_pnl") or 0.0),
                net_pnl_estimated=float(item.get("net_pnl_estimated") or 0.0),
                exit_reason=str(item.get("exit_reason") or "unknown"),
                regime="unknown",
            )
        )
    if not trades:
        raise SystemExit("No existing backtest trade_outcomes were found for the requested date range.")
    return BacktestResult(START, END, trades, -1, calculate_metrics(trades, settings.paper_account_size))


def dataset_coverage(db: Database) -> list[dict[str, Any]]:
    rows = db.conn.execute(
        """
        SELECT symbol, timeframe, COUNT(*) AS rows, MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts
        FROM bars
        GROUP BY symbol, timeframe
        ORDER BY symbol, timeframe
        """
    ).fetchall()
    return [dict(row) for row in rows]


def analyze_result(result) -> dict[str, Any]:
    trades = result.trades
    metrics = dict(result.metrics)
    starting_equity = 1_000_000.0
    ending_equity = starting_equity + metrics.get("net_pnl", 0.0)
    yearly = grouped_stats(trades, lambda trade: str(trade.entry_time.astimezone(EASTERN).year))
    monthly = grouped_stats(trades, lambda trade: trade.entry_time.astimezone(EASTERN).strftime("%Y-%m"))
    direction = grouped_stats(trades, lambda trade: trade.direction)
    exit_reason = grouped_stats(trades, lambda trade: trade.exit_reason)
    regime = grouped_stats(trades, lambda trade: trade.regime or "unknown")
    hour = grouped_stats(trades, lambda trade: f"{trade.entry_time.astimezone(EASTERN).hour:02d}:00 ET")
    best_months = sorted(monthly, key=lambda row: row["net_pnl"], reverse=True)[:8]
    worst_months = sorted(monthly, key=lambda row: row["net_pnl"])[:8]
    return {
        "period": {"start_utc": START.isoformat(), "end_utc_exclusive": END.isoformat()},
        "starting_equity": starting_equity,
        "ending_equity": ending_equity,
        "metrics": metrics,
        "no_trade_count": result.no_trade_count,
        "yearly": yearly,
        "monthly": monthly,
        "direction": direction,
        "exit_reason": exit_reason,
        "regime": regime,
        "hour": hour,
        "best_months": best_months,
        "worst_months": worst_months,
        "equity_curve": equity_curve_rows(trades, starting_equity),
        "trades": [trade_to_dict(trade) for trade in trades],
        "observations": build_observations(metrics, direction, regime, hour, result.no_trade_count),
    }


def grouped_stats(trades: list[Any], key_fn) -> list[dict[str, Any]]:
    buckets: dict[str, list[Any]] = defaultdict(list)
    for trade in trades:
        buckets[str(key_fn(trade))].append(trade)
    rows = []
    for key, group in buckets.items():
        pnls = [trade.net_pnl_estimated for trade in group]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl <= 0]
        rows.append(
            {
                "group": key,
                "trades": len(group),
                "net_pnl": sum(pnls),
                "win_rate": len(wins) / len(group) if group else 0.0,
                "profit_factor": sum(wins) / abs(sum(losses)) if losses and sum(losses) else (999.0 if wins else 0.0),
                "avg_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
            }
        )
    return sorted(rows, key=lambda row: row["group"])


def equity_curve_rows(trades: list[Any], starting_equity: float) -> list[dict[str, Any]]:
    equity = starting_equity
    peak = starting_equity
    rows = []
    for idx, trade in enumerate(trades, start=1):
        equity += trade.net_pnl_estimated
        peak = max(peak, equity)
        drawdown = (peak - equity) / peak if peak else 0.0
        rows.append({"trade": idx, "exit_time": trade.exit_time.isoformat(), "equity": equity, "drawdown": drawdown})
    return rows


def trade_to_dict(trade: Any) -> dict[str, Any]:
    return {
        "symbol": trade.symbol,
        "direction": trade.direction,
        "decision_time": trade.decision_time.isoformat(),
        "entry_time": trade.entry_time.isoformat(),
        "exit_time": trade.exit_time.isoformat(),
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "qty": trade.qty,
        "gross_pnl": trade.gross_pnl,
        "net_pnl_estimated": trade.net_pnl_estimated,
        "exit_reason": trade.exit_reason,
        "regime": trade.regime,
        "holding_seconds": (trade.exit_time - trade.entry_time).total_seconds(),
    }


def build_observations(metrics: dict[str, float], direction: list[dict[str, Any]], regime: list[dict[str, Any]], hour: list[dict[str, Any]], no_trade_count: int) -> list[str]:
    observations = []
    net_pnl = metrics.get("net_pnl", 0.0)
    profit_factor = metrics.get("profit_factor", 0.0)
    win_rate = metrics.get("win_rate", 0.0)
    trades = metrics.get("number_of_trades", 0.0)
    observations.append(
        "The rules produced positive estimated net P/L over this historical period."
        if net_pnl > 0
        else "The rules did not produce positive estimated net P/L over this historical period."
    )
    if trades < 50:
        observations.append("Trade count is low for a three-year period, so conclusions should be treated as early evidence rather than statistical proof.")
    if profit_factor < 1.15:
        observations.append("Profit factor is weak; the strategy needs better filtering, exits, or sizing before live use.")
    if win_rate < 0.45:
        observations.append("Win rate is low; the bot may be entering too late, taking poor reward/risk, or overtrading weak breaks.")
    if no_trade_count >= 0 and no_trade_count > trades * 25:
        observations.append("The bot skipped far more setups than it traded, so missed-opportunity labeling is important for improvement.")
    if no_trade_count < 0:
        observations.append("The report was reconstructed from existing trade records, so skipped/no-trade decision count is unavailable for this run.")
    best_direction = best_group(direction)
    if best_direction:
        observations.append(f"Best direction by net P/L was {best_direction['group']}.")
    best_regime = best_group(regime)
    if best_regime:
        observations.append(f"Best regime by net P/L was {best_regime['group']}.")
    best_hour = best_group(hour)
    if best_hour:
        observations.append(f"Best entry hour by net P/L was {best_hour['group']}.")
    return observations


def best_group(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: row["net_pnl"])


def write_analysis_files(paths: dict[str, Path], data_coverage: list[dict[str, Any]], analysis: dict[str, Any], result) -> None:
    reports = paths["reports"]
    (reports / "backtest_summary.json").write_text(json.dumps(analysis, indent=2, sort_keys=True, default=str), encoding="utf-8")
    write_csv(reports / "dataset_coverage.csv", data_coverage)
    write_csv(reports / "trades.csv", analysis["trades"])
    write_csv(reports / "yearly_stats.csv", analysis["yearly"])
    write_csv(reports / "monthly_stats.csv", analysis["monthly"])
    write_csv(reports / "direction_stats.csv", analysis["direction"])
    write_csv(reports / "regime_stats.csv", analysis["regime"])
    write_csv(reports / "hour_stats.csv", analysis["hour"])
    metrics_rows = [{"metric": key, "value": value} for key, value in result.metrics.items()]
    write_csv(reports / "metrics.csv", metrics_rows)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_docx_report(report_path: Path, paths: dict[str, Path], data_coverage: list[dict[str, Any]], analysis: dict[str, Any], result, export_result) -> None:
    metrics = result.metrics
    body: list[str] = []
    body.append(heading("GLD Scalper Bot Backtest Report", 1))
    body.append(paragraph("Copyright (c) @Mashcorp. Proprietary research report."))
    body.append(paragraph(f"Generated: {utc_now().isoformat()}"))
    body.append(paragraph(f"Period tested: {START.isoformat()} through {END.isoformat()}."))
    body.append(paragraph("Mode: historical backtest using a separate paper backtest database. No live orders were submitted during this report run."))

    body.append(heading("Executive Summary", 1))
    summary_rows = [
        ["Starting equity", money(analysis["starting_equity"])],
        ["Ending equity", money(analysis["ending_equity"])],
        ["Estimated net P/L", money(metrics.get("net_pnl", 0.0))],
        ["Total return", pct(metrics.get("total_return", 0.0))],
        ["Number of trades", whole(metrics.get("number_of_trades", 0.0))],
        ["Win rate", pct(metrics.get("win_rate", 0.0))],
        ["Profit factor", num(metrics.get("profit_factor", 0.0))],
        ["Max drawdown", pct(metrics.get("max_drawdown", 0.0))],
        ["Sharpe-like trade ratio", num(metrics.get("sharpe_ratio", 0.0))],
        ["No-trade decisions in simulation", whole(analysis["no_trade_count"]) if analysis["no_trade_count"] >= 0 else "unavailable from reconstructed trades"],
    ]
    body.append(table(["Metric", "Value"], summary_rows))
    for item in analysis["observations"]:
        body.append(bullet(item))

    body.append(heading("Dataset Location And Coverage", 1))
    body.append(paragraph(f"SQLite dataset folder: {paths['dataset']}"))
    body.append(paragraph(f"Report output folder: {paths['reports']}"))
    body.append(paragraph(f"CSV export folder: {paths['exports']}"))
    coverage_rows = [[row["symbol"], row["timeframe"], whole(row["rows"]), row["first_ts"], row["last_ts"]] for row in data_coverage]
    body.append(table(["Symbol", "Timeframe", "Rows", "First Timestamp", "Last Timestamp"], coverage_rows))
    if export_result:
        body.append(paragraph(f"Full SQLite CSV export created at: {export_result.export_dir}"))

    body.append(heading("Backtest Assumptions", 1))
    assumptions = [
        "The simulation uses Alpaca historical stock bars from the configured feed.",
        "The execution model uses next-bar entry, estimated 0.01% slippage, and an estimated 0.02% transaction cost.",
        "Bracket exits are simulated from minute-bar high/low values.",
        "Historical quote depth and historical trade prints were not replayed, so microstructure quality is approximated from bar-derived features and defaults.",
        "The current backtester evaluates GLD 1-minute bars and derives higher-timeframe confirmation from those bars.",
        "Large multi-year reports run in calendar-year segments to keep laptop memory use controlled.",
        "Results are research estimates. They are not a guarantee of Alpaca paper or live trading performance.",
    ]
    for item in assumptions:
        body.append(bullet(item))

    body.append(heading("Performance Tables", 1))
    for title, rows in [
        ("Yearly Performance", analysis["yearly"]),
        ("Direction Performance", analysis["direction"]),
        ("Exit Reason Performance", analysis["exit_reason"]),
        ("Regime Performance", analysis["regime"]),
        ("Entry Hour Performance", analysis["hour"]),
        ("Best Months", analysis["best_months"]),
        ("Worst Months", analysis["worst_months"]),
    ]:
        body.append(heading(title, 2))
        body.append(stats_table(rows))

    body.append(heading("Where The Bot Worked Best", 1))
    for item in scenario_notes(analysis, positive=True):
        body.append(bullet(item))

    body.append(heading("Flaws And Weak Points", 1))
    for item in flaw_notes(analysis):
        body.append(bullet(item))

    body.append(heading("Improvement Plan", 1))
    for item in improvement_notes(analysis):
        body.append(bullet(item))

    body.append(heading("Files Produced", 1))
    file_rows = [
        ["SQLite backtest database", str(paths["dataset"] / "gld_scalper_backtest.db")],
        ["Dataset manifest", str(paths["dataset"] / "dataset_manifest.json")],
        ["DOCX report", str(report_path)],
        ["Trade list CSV", str(paths["reports"] / "trades.csv")],
        ["Metrics CSV", str(paths["reports"] / "metrics.csv")],
        ["Yearly stats CSV", str(paths["reports"] / "yearly_stats.csv")],
        ["Monthly stats CSV", str(paths["reports"] / "monthly_stats.csv")],
    ]
    body.append(table(["File", "Path"], file_rows))

    write_docx_zip(report_path, "".join(body))


def write_docx_zip(report_path: Path, body_xml: str) -> None:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        + body_xml
        + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="720" w:right="720" w:bottom="720" w:left="720"/></w:sectPr>'
        + "</w:body></w:document>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:spacing w:after="160"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:spacing w:before="120" w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>'
        '<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/>'
        '<w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4"/><w:left w:val="single" w:sz="4"/>'
        '<w:bottom w:val="single" w:sz="4"/><w:right w:val="single" w:sz="4"/><w:insideH w:val="single" w:sz="4"/>'
        '<w:insideV w:val="single" w:sz="4"/></w:tblBorders></w:tblPr></w:style></w:styles>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>'
    )
    with zipfile.ZipFile(report_path, "w", compression=zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", content_types)
        docx.writestr("_rels/.rels", rels)
        docx.writestr("word/document.xml", document_xml)
        docx.writestr("word/styles.xml", styles_xml)
        docx.writestr("word/_rels/document.xml.rels", document_rels)


def stats_table(rows: list[dict[str, Any]]) -> str:
    table_rows = [
        [row["group"], whole(row["trades"]), money(row["net_pnl"]), pct(row["win_rate"]), num(row["profit_factor"]), money(row["avg_pnl"])]
        for row in rows
    ]
    return table(["Group", "Trades", "Net P/L", "Win Rate", "Profit Factor", "Avg P/L"], table_rows)


def scenario_notes(analysis: dict[str, Any], *, positive: bool) -> list[str]:
    rows = []
    for label, source in [("direction", analysis["direction"]), ("regime", analysis["regime"]), ("entry hour", analysis["hour"])]:
        if not source:
            continue
        chosen = max(source, key=lambda row: row["net_pnl"]) if positive else min(source, key=lambda row: row["net_pnl"])
        if chosen["trades"] > 0:
            rows.append(f"Best {label}: {chosen['group']} with {money(chosen['net_pnl'])}, {whole(chosen['trades'])} trades, and {pct(chosen['win_rate'])} win rate.")
    if not rows:
        rows.append("No winning setup cluster was strong enough to identify from the completed trade sample.")
    return rows


def flaw_notes(analysis: dict[str, Any]) -> list[str]:
    metrics = analysis["metrics"]
    notes = [
        "The historical simulation does not replay true historical quotes, bid/ask depth, or trade prints. This weakens microstructure, spread-regime, and liquidity-score conclusions.",
        "The backtest assumes fills when minute high/low touches bracket levels. Real fills can be worse, especially around fast GLD moves or wide spreads.",
        "The current backtest path does not use a trained champion ML model. It tests the deterministic rule stack with a neutral model placeholder.",
        "Macro/LLM context is not replayed historically, so the slow sentiment layer is not reflected in this result.",
    ]
    if metrics.get("profit_factor", 0.0) < 1.15:
        notes.append("Profit factor is not strong enough for live confidence; entries or exits need improvement.")
    if metrics.get("max_drawdown", 0.0) > 0.01:
        notes.append("Drawdown is meaningful relative to scalping target size; risk throttles should be reviewed before larger sizing.")
    if metrics.get("number_of_trades", 0.0) < 100:
        notes.append("The strategy was selective. More labeled paper data is needed before promoting ML candidates.")
    return notes


def improvement_notes(analysis: dict[str, Any]) -> list[str]:
    notes = [
        "Add a historical quote/trade replay dataset so the spread regime, quote imbalance, trade intensity, and liquidity score are tested with real microstructure data.",
        "Train and walk-forward-test ML candidates on the backtest/paper dataset, but promote only if they beat the deterministic rules after costs.",
        "Use missed-opportunity labels to discover whether high-quality skipped breakouts should have been traded.",
        "Separate performance by opening drive, late morning, midday, afternoon, and power hour to tune time-of-day sizing.",
        f"Add walk-forward validation: train on one period, validate on the next, then roll forward. Avoid tuning on the full {START.date()} to {END.date()} sample at once.",
        "Stress-test slippage and costs at 2x and 3x current assumptions before considering live trading.",
    ]
    best_regime = best_group(analysis["regime"])
    if best_regime:
        notes.append(f"Focus first on setups matching the best historical regime: {best_regime['group']}.")
    return notes


def paragraph(text: str) -> str:
    return f"<w:p><w:r><w:t>{escape(str(text))}</w:t></w:r></w:p>"


def bullet(text: str) -> str:
    return f'<w:p><w:pPr><w:ind w:left="360" w:hanging="180"/></w:pPr><w:r><w:t>- {escape(str(text))}</w:t></w:r></w:p>'


def heading(text: str, level: int) -> str:
    style = "Heading1" if level == 1 else "Heading2"
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{escape(str(text))}</w:t></w:r></w:p>'


def table(headers: list[str], rows: list[list[Any]]) -> str:
    cells = [table_row(headers, header=True)]
    cells.extend(table_row(row) for row in rows)
    return '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>' + "".join(cells) + "</w:tbl>"


def table_row(values: list[Any], *, header: bool = False) -> str:
    return "<w:tr>" + "".join(table_cell(value, header=header) for value in values) + "</w:tr>"


def table_cell(value: Any, *, header: bool = False) -> str:
    bold = "<w:b/>" if header else ""
    return (
        '<w:tc><w:tcPr><w:tcW w:w="2400" w:type="dxa"/></w:tcPr>'
        f"<w:p><w:r><w:rPr>{bold}</w:rPr><w:t>{escape(str(value))}</w:t></w:r></w:p></w:tc>"
    )


def money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return f"${number:,.2f}"


def pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number * 100:.2f}%"


def num(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if math.isinf(number):
        return "inf"
    return f"{number:,.3f}"


def whole(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return f"{int(round(number)):,}"


if __name__ == "__main__":
    main()
