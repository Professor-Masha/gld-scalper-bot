from __future__ import annotations

# ruff: noqa: E402

import argparse
import gc
import json
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

ALPACA_DATA_BASE_URL = "https://data.alpaca.markets/v2"
MICROSTRUCTURE_PAGE_LIMIT = 5_000

from gld_scalper.alpaca_clients import get_stock_historical_client
from gld_scalper.config import load_settings, parse_symbols
from gld_scalper.data_collector import _alpaca_timeframe, _bars_response_to_records
from gld_scalper.database import Database
from gld_scalper.research_data import ResearchDataCollector
from gld_scalper.reports.csv_exporter import export_database_to_csv
from gld_scalper.utils.time_utils import ensure_utc, utc_now


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a standalone historical GLD dataset into SQLite.")
    parser.add_argument("--start", required=True, help="Start date, inclusive, YYYY-MM-DD.")
    parser.add_argument("--end", required=True, help="End date, exclusive, YYYY-MM-DD.")
    parser.add_argument("--dataset-name", required=True, help="Folder-safe dataset name, for example gld_2021_2026.")
    parser.add_argument("--symbols", nargs="+", default=["GLD"], help="Symbols for intraday bar downloads.")
    parser.add_argument("--related-symbols", nargs="+", default=["IAU", "SLV", "GDX", "GDXJ", "UUP", "TLT", "IEF", "SHY", "SPY", "QQQ", "VIXY"], help="Symbols for daily context bars.")
    parser.add_argument("--include-quotes", action="store_true", help="Also download historical GLD quotes. This can be very large.")
    parser.add_argument("--include-trades", action="store_true", help="Also download historical GLD trades. This can be very large.")
    parser.add_argument("--include-adjusted-bars", action="store_true", help="Also save raw and all-adjusted bars into bar_variants for research.")
    parser.add_argument("--include-news", action="store_true", help="Also download Alpaca historical news for the configured news symbols.")
    parser.add_argument("--include-macro-series", action="store_true", help="Also download configured FRED macro series when FRED_API_KEY is set.")
    parser.add_argument("--include-event-calendar", action="store_true", help="Also import local economic calendar CSV if configured.")
    parser.add_argument("--include-market-calendar", action="store_true", help="Also download Alpaca trading calendar.")
    parser.add_argument("--index-knowledge", action="store_true", help="Index local Knowledge folder artifacts into SQLite.")
    parser.add_argument("--label-derived", action="store_true", help="Build derived feature/outcome/news/event labels after data download.")
    parser.add_argument("--microstructure-symbol", default="GLD", help="Symbol used for optional historical quotes/trades.")
    parser.add_argument("--export-csv", action="store_true", help="Export the SQLite dataset to CSV after download.")
    parser.add_argument("--skip-coverage", action="store_true", help="Skip final coverage summary for very large datasets.")
    parser.add_argument("--full-coverage", action="store_true", help="Run full coverage scans, including huge quote/trade tables. Slow on large datasets.")
    parser.add_argument("--max-retries", type=int, default=8, help="Retry attempts for each Alpaca request.")
    parser.add_argument("--retry-base-seconds", type=float, default=10.0, help="Initial retry wait; later retries back off from this value.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore completed chunk progress and request every chunk again.")
    args = parser.parse_args()

    start = parse_utc_date(args.start)
    end = parse_utc_date(args.end)
    if end <= start:
        raise SystemExit("--end must be after --start.")

    paths = build_paths(args.dataset_name)
    for key in ("dataset", "exports", "exports_hourly"):
        paths[key].mkdir(parents=True, exist_ok=True)
    if paths["progress"].is_dir():
        raise SystemExit(
            f"{paths['progress']} is a directory, but it must be a progress file. "
            "Remove that directory and rerun this command."
        )

    settings = build_settings(paths)
    settings.validate_safety()
    db_path = paths["dataset"] / "gld_scalper_historical.db"
    can_resume = db_path.exists() and not args.no_resume
    progress = load_progress(paths["progress"]) if can_resume else {"completed_chunks": []}

    db = Database(settings=settings)
    db.init_db()
    client = get_stock_historical_client(settings)

    intraday_symbols = parse_symbols(args.symbols)
    related_symbols = parse_symbols(args.related_symbols)
    daily_symbols = list(dict.fromkeys([*intraday_symbols, *related_symbols]))

    bar_plan = [
        ("1Min", intraday_symbols, 7),
        ("5Min", intraday_symbols, 60),
        ("15Min", intraday_symbols, 90),
        ("1Hour", intraday_symbols, 180),
        ("1Day", daily_symbols, 365),
    ]
    manifest: dict[str, Any] = {
        "dataset_name": args.dataset_name,
        "start_utc": start.isoformat(),
        "end_utc_exclusive": end.isoformat(),
        "source": "Alpaca historical stock data",
        "feed": settings.alpaca_data_feed,
        "database": str(db.path),
        "created_at": utc_now().isoformat(),
        "bar_plan": [{"timeframe": timeframe, "symbols": symbols, "chunk_days": chunk_days} for timeframe, symbols, chunk_days in bar_plan],
        "include_quotes": args.include_quotes,
        "include_trades": args.include_trades,
        "include_adjusted_bars": args.include_adjusted_bars,
        "include_news": args.include_news,
        "include_macro_series": args.include_macro_series,
        "include_event_calendar": args.include_event_calendar,
        "include_market_calendar": args.include_market_calendar,
        "index_knowledge": args.index_knowledge,
        "label_derived": args.label_derived,
        "microstructure_symbol": args.microstructure_symbol.upper(),
    }

    for timeframe, symbols, chunk_days in bar_plan:
        download_bars(db, client, settings, symbols, timeframe, start, end, chunk_days, progress, paths["progress"], args.max_retries, args.retry_base_seconds)

    if args.include_adjusted_bars:
        for adjustment in ("raw", "all"):
            for timeframe, symbols, chunk_days in bar_plan:
                download_bar_variants(db, client, settings, symbols, timeframe, adjustment, start, end, chunk_days, progress, paths["progress"], args.max_retries, args.retry_base_seconds)

    if args.include_quotes:
        download_quotes(db, client, settings, args.microstructure_symbol.upper(), start, end, 3, progress, paths["progress"], args.max_retries, args.retry_base_seconds)

    if args.include_trades:
        download_trades(db, client, settings, args.microstructure_symbol.upper(), start, end, 3, progress, paths["progress"], args.max_retries, args.retry_base_seconds)

    research_results = collect_research_sidecars(db, settings, args, start, end)
    manifest["research_results"] = [asdict(item) for item in research_results]

    coverage = [] if args.skip_coverage else dataset_coverage(db, full_scan=args.full_coverage)
    manifest["coverage"] = coverage
    (paths["dataset"] / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"database": str(db.path), "coverage": coverage}, indent=2, sort_keys=True), flush=True)

    if args.export_csv:
        result = export_database_to_csv(db, paths["exports_hourly"], run_at=utc_now())
        print(json.dumps({"export_dir": str(result.export_dir), "files": result.files}, indent=2, sort_keys=True), flush=True)


def parse_utc_date(value: str) -> datetime:
    return datetime.fromisoformat(f"{value}T00:00:00+00:00").astimezone(timezone.utc)


def build_paths(dataset_name: str) -> dict[str, Path]:
    safe_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in dataset_name)
    return {
        "dataset": PROJECT_ROOT / "data" / "paper" / "historical" / safe_name,
        "exports": PROJECT_ROOT / "exports" / "paper" / "historical" / safe_name,
        "exports_hourly": PROJECT_ROOT / "exports" / "paper" / "historical" / safe_name / "hourly",
        "progress": PROJECT_ROOT / "data" / "paper" / "historical" / safe_name / "download_progress.json",
    }


def build_settings(paths: dict[str, Path]):
    base = load_settings()
    return replace(
        base,
        data_mode="paper",
        database_url=f"sqlite:///{paths['dataset'] / 'gld_scalper_historical.db'}",
        csv_export_dir=str(paths["exports_hourly"]),
        enable_hourly_csv_export=False,
    )


def download_bars(
    db: Database,
    client: Any,
    settings: Any,
    symbols: list[str],
    timeframe: str,
    start: datetime,
    end: datetime,
    chunk_days: int,
    progress: dict[str, Any],
    progress_path: Path,
    attempts: int,
    retry_base_seconds: float,
) -> None:
    print(f"Downloading {timeframe} bars for {symbols}: {start.date()} to {end.date()}", flush=True)
    total = 0
    for chunk_start, chunk_end in chunk_windows(start, end, chunk_days):
        progress_key = chunk_key("bars", timeframe, symbols, chunk_start, chunk_end)
        if progress_key in progress["completed_chunks"]:
            print(f"  {chunk_start.date()} -> {chunk_end.date()}: skipped completed chunk", flush=True)
            continue
        rows = retry_download(lambda: download_bar_chunk(client, settings, symbols, timeframe, chunk_start, chunk_end), attempts, retry_base_seconds)
        rows = [row for row in rows if start <= ensure_utc(row["timestamp"]) < end]
        inserted = db.upsert_bars(rows)
        total += inserted
        mark_progress(progress, progress_path, progress_key)
        print(f"  {chunk_start.date()} -> {chunk_end.date()}: fetched={len(rows)} upserted={inserted}", flush=True)
    print(f"Finished {timeframe} bars: upserted_or_seen={total}", flush=True)


def download_bar_chunk(client: Any, settings: Any, symbols: list[str], timeframe: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest

    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=_alpaca_timeframe(timeframe),
        start=start,
        end=end,
        feed=DataFeed.IEX if settings.alpaca_data_feed == "iex" else DataFeed.SIP,
    )
    response = client.get_stock_bars(request)
    return _bars_response_to_records(response, timeframe)


def download_bar_variants(
    db: Database,
    client: Any,
    settings: Any,
    symbols: list[str],
    timeframe: str,
    adjustment: str,
    start: datetime,
    end: datetime,
    chunk_days: int,
    progress: dict[str, Any],
    progress_path: Path,
    attempts: int,
    retry_base_seconds: float,
) -> None:
    print(f"Downloading {adjustment} {timeframe} bar variants for {symbols}: {start.date()} to {end.date()}", flush=True)
    total = 0
    for chunk_start, chunk_end in chunk_windows(start, end, chunk_days):
        progress_key = chunk_key("bar_variants", f"{timeframe}:{adjustment}", symbols, chunk_start, chunk_end)
        if progress_key in progress["completed_chunks"]:
            print(f"  {adjustment} {chunk_start.date()} -> {chunk_end.date()}: skipped completed chunk", flush=True)
            continue
        rows = retry_download(lambda: download_bar_variant_chunk(client, settings, symbols, timeframe, adjustment, chunk_start, chunk_end), attempts, retry_base_seconds)
        rows = [row for row in rows if start <= ensure_utc(row["timestamp"]) < end]
        inserted = db.upsert_bar_variants(rows, adjustment=adjustment)
        total += inserted
        mark_progress(progress, progress_path, progress_key)
        print(f"  {adjustment} {chunk_start.date()} -> {chunk_end.date()}: fetched={len(rows)} upserted={inserted}", flush=True)
    print(f"Finished {adjustment} {timeframe} bar variants: upserted_or_seen={total}", flush=True)


def download_bar_variant_chunk(client: Any, settings: Any, symbols: list[str], timeframe: str, adjustment: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest

    adjustment_enum = Adjustment.RAW if adjustment == "raw" else Adjustment.ALL
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=_alpaca_timeframe(timeframe),
        start=start,
        end=end,
        adjustment=adjustment_enum,
        feed=DataFeed.IEX if settings.alpaca_data_feed == "iex" else DataFeed.SIP,
    )
    response = client.get_stock_bars(request)
    records = _bars_response_to_records(response, timeframe)
    for record in records:
        record["source"] = f"alpaca_{adjustment}"
    return records


def download_quotes(
    db: Database,
    client: Any,
    settings: Any,
    symbol: str,
    start: datetime,
    end: datetime,
    chunk_days: int,
    progress: dict[str, Any],
    progress_path: Path,
    attempts: int,
    retry_base_seconds: float,
) -> None:
    print(f"Downloading historical quotes for {symbol}: {start.date()} to {end.date()}", flush=True)
    total = 0
    for chunk_start, chunk_end in chunk_windows(start, end, chunk_days):
        progress_key = chunk_key("quotes", "quote", [symbol], chunk_start, chunk_end)
        if progress_key in progress["completed_chunks"]:
            print(f"  quotes {chunk_start.date()} -> {chunk_end.date()}: skipped completed chunk", flush=True)
            continue
        fetched, inserted = download_quotes_adaptive(
            db,
            client,
            settings,
            symbol,
            chunk_start,
            chunk_end,
            start,
            end,
            progress,
            progress_path,
            attempts,
            retry_base_seconds,
        )
        total += inserted
        mark_progress(progress, progress_path, progress_key)
        print(f"  quotes {_window_label(chunk_start, chunk_end)}: fetched={fetched} upserted={inserted}", flush=True)
    print(f"Finished quotes: upserted_or_seen={total}", flush=True)


def download_quotes_adaptive(
    db: Database,
    client: Any,
    settings: Any,
    symbol: str,
    chunk_start: datetime,
    chunk_end: datetime,
    dataset_start: datetime,
    dataset_end: datetime,
    progress: dict[str, Any],
    progress_path: Path,
    attempts: int,
    retry_base_seconds: float,
) -> tuple[int, int]:
    sub_key = chunk_key("quotes_sub", "quote", [symbol], chunk_start, chunk_end)
    if sub_key in progress["completed_chunks"]:
        print(f"    quotes {_window_label(chunk_start, chunk_end)}: skipped completed subchunk", flush=True)
        return 0, 0
    try:
        fetched, inserted = retry_download(
            lambda: download_quote_chunk_to_db(db, settings, symbol, chunk_start, chunk_end, dataset_start, dataset_end),
            attempts,
            retry_base_seconds,
        )
        gc.collect()
        mark_progress(progress, progress_path, sub_key)
        return fetched, inserted
    except Exception as exc:
        if not _can_split_microstructure_window(chunk_start, chunk_end):
            raise
        midpoint = chunk_start + (chunk_end - chunk_start) / 2
        print(
            f"    quotes {_window_label(chunk_start, chunk_end)} too large or unstable ({_error_label(exc)}); splitting into smaller windows",
            flush=True,
        )
        left_fetched, left_inserted = download_quotes_adaptive(
            db, client, settings, symbol, chunk_start, midpoint, dataset_start, dataset_end, progress, progress_path, attempts, retry_base_seconds
        )
        right_fetched, right_inserted = download_quotes_adaptive(
            db, client, settings, symbol, midpoint, chunk_end, dataset_start, dataset_end, progress, progress_path, attempts, retry_base_seconds
        )
        mark_progress(progress, progress_path, sub_key)
        return left_fetched + right_fetched, left_inserted + right_inserted


def download_quote_chunk(client: Any, settings: Any, symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockQuotesRequest

    request = StockQuotesRequest(
        symbol_or_symbols=[symbol],
        start=start,
        end=end,
        feed=DataFeed.IEX if settings.alpaca_data_feed == "iex" else DataFeed.SIP,
    )
    response = client.get_stock_quotes(request)
    return quotes_response_to_records(response, symbol)


def download_trades(
    db: Database,
    client: Any,
    settings: Any,
    symbol: str,
    start: datetime,
    end: datetime,
    chunk_days: int,
    progress: dict[str, Any],
    progress_path: Path,
    attempts: int,
    retry_base_seconds: float,
) -> None:
    print(f"Downloading historical trades for {symbol}: {start.date()} to {end.date()}", flush=True)
    total = 0
    for chunk_start, chunk_end in chunk_windows(start, end, chunk_days):
        progress_key = chunk_key("trades", "trade", [symbol], chunk_start, chunk_end)
        if progress_key in progress["completed_chunks"]:
            print(f"  trades {chunk_start.date()} -> {chunk_end.date()}: skipped completed chunk", flush=True)
            continue
        fetched, inserted = download_trades_adaptive(
            db,
            client,
            settings,
            symbol,
            chunk_start,
            chunk_end,
            start,
            end,
            progress,
            progress_path,
            attempts,
            retry_base_seconds,
        )
        total += inserted
        mark_progress(progress, progress_path, progress_key)
        print(f"  trades {_window_label(chunk_start, chunk_end)}: fetched={fetched} upserted={inserted}", flush=True)
    print(f"Finished trades: upserted_or_seen={total}", flush=True)


def download_trades_adaptive(
    db: Database,
    client: Any,
    settings: Any,
    symbol: str,
    chunk_start: datetime,
    chunk_end: datetime,
    dataset_start: datetime,
    dataset_end: datetime,
    progress: dict[str, Any],
    progress_path: Path,
    attempts: int,
    retry_base_seconds: float,
) -> tuple[int, int]:
    sub_key = chunk_key("trades_sub", "trade", [symbol], chunk_start, chunk_end)
    if sub_key in progress["completed_chunks"]:
        print(f"    trades {_window_label(chunk_start, chunk_end)}: skipped completed subchunk", flush=True)
        return 0, 0
    try:
        fetched, inserted = retry_download(
            lambda: download_trade_chunk_to_db(db, settings, symbol, chunk_start, chunk_end, dataset_start, dataset_end),
            attempts,
            retry_base_seconds,
        )
        gc.collect()
        mark_progress(progress, progress_path, sub_key)
        return fetched, inserted
    except Exception as exc:
        if not _can_split_microstructure_window(chunk_start, chunk_end):
            raise
        midpoint = chunk_start + (chunk_end - chunk_start) / 2
        print(
            f"    trades {_window_label(chunk_start, chunk_end)} too large or unstable ({_error_label(exc)}); splitting into smaller windows",
            flush=True,
        )
        left_fetched, left_inserted = download_trades_adaptive(
            db, client, settings, symbol, chunk_start, midpoint, dataset_start, dataset_end, progress, progress_path, attempts, retry_base_seconds
        )
        right_fetched, right_inserted = download_trades_adaptive(
            db, client, settings, symbol, midpoint, chunk_end, dataset_start, dataset_end, progress, progress_path, attempts, retry_base_seconds
        )
        mark_progress(progress, progress_path, sub_key)
        return left_fetched + right_fetched, left_inserted + right_inserted


def download_trade_chunk(client: Any, settings: Any, symbol: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockTradesRequest

    request = StockTradesRequest(
        symbol_or_symbols=[symbol],
        start=start,
        end=end,
        feed=DataFeed.IEX if settings.alpaca_data_feed == "iex" else DataFeed.SIP,
    )
    response = client.get_stock_trades(request)
    return trades_response_to_records(response, symbol)


def download_quote_chunk_to_db(
    db: Database,
    settings: Any,
    symbol: str,
    start: datetime,
    end: datetime,
    dataset_start: datetime,
    dataset_end: datetime,
) -> tuple[int, int]:
    fetched = 0
    inserted = 0
    for page_symbol, raw_quotes in iter_alpaca_marketdata_pages(settings, "quotes", "quotes", symbol, start, end):
        rows = raw_quotes_to_records(page_symbol, raw_quotes, symbol)
        rows = [row for row in rows if dataset_start <= ensure_utc(row["timestamp"]) < dataset_end]
        inserted += db.upsert_quotes(rows)
        fetched += len(rows)
        del rows
        gc.collect()
    return fetched, inserted


def download_trade_chunk_to_db(
    db: Database,
    settings: Any,
    symbol: str,
    start: datetime,
    end: datetime,
    dataset_start: datetime,
    dataset_end: datetime,
) -> tuple[int, int]:
    fetched = 0
    inserted = 0
    for page_symbol, raw_trades in iter_alpaca_marketdata_pages(settings, "trades", "trades", symbol, start, end):
        rows = raw_trades_to_records(page_symbol, raw_trades, symbol)
        rows = [row for row in rows if dataset_start <= ensure_utc(row["timestamp"]) < dataset_end]
        inserted += db.upsert_trades(rows)
        fetched += len(rows)
        del rows
        gc.collect()
    return fetched, inserted


def iter_alpaca_marketdata_pages(
    settings: Any,
    endpoint: str,
    payload_key: str,
    symbol: str,
    start: datetime,
    end: datetime,
):
    import requests

    url = f"{ALPACA_DATA_BASE_URL}/stocks/{endpoint}"
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
    }
    params: dict[str, Any] = {
        "symbols": symbol,
        "start": ensure_utc(start).isoformat(),
        "end": ensure_utc(end).isoformat(),
        "feed": settings.alpaca_data_feed,
        "limit": MICROSTRUCTURE_PAGE_LIMIT,
        "sort": "asc",
    }
    page_token: str | None = None
    seen_tokens: set[str] = set()
    with requests.Session() as session:
        while True:
            if page_token:
                params["page_token"] = page_token
            else:
                params.pop("page_token", None)
            response = session.get(url, headers=headers, params=params, timeout=120)
            response.raise_for_status()
            payload = response.json()
            data = payload.get(payload_key, {})
            if isinstance(data, dict):
                for page_symbol, items in data.items():
                    yield page_symbol, items or []
            elif isinstance(data, list):
                yield symbol, data
            else:
                raise RuntimeError(f"Unexpected Alpaca {payload_key} payload shape: {type(data).__name__}")
            next_token = payload.get("next_page_token")
            if not next_token or next_token in seen_tokens:
                break
            seen_tokens.add(str(next_token))
            page_token = str(next_token)
            del payload


def raw_quotes_to_records(raw_symbol: str, raw_quotes: list[dict[str, Any]], fallback_symbol: str) -> list[dict[str, Any]]:
    symbol = str(raw_symbol or fallback_symbol).upper()
    records: list[dict[str, Any]] = []
    for quote in raw_quotes:
        timestamp = quote.get("t", quote.get("timestamp"))
        if not timestamp:
            continue
        records.append(
            {
                "symbol": symbol,
                "timestamp": timestamp,
                "bid_price": quote.get("bp", quote.get("bid_price", 0.0)),
                "bid_size": quote.get("bs", quote.get("bid_size", 0.0)),
                "ask_price": quote.get("ap", quote.get("ask_price", 0.0)),
                "ask_size": quote.get("as", quote.get("ask_size", 0.0)),
                "source": "alpaca",
            }
        )
    return records


def raw_trades_to_records(raw_symbol: str, raw_trades: list[dict[str, Any]], fallback_symbol: str) -> list[dict[str, Any]]:
    symbol = str(raw_symbol or fallback_symbol).upper()
    records: list[dict[str, Any]] = []
    for trade in raw_trades:
        timestamp = trade.get("t", trade.get("timestamp"))
        if not timestamp:
            continue
        records.append(
            {
                "symbol": symbol,
                "timestamp": timestamp,
                "price": trade.get("p", trade.get("price", 0.0)),
                "size": trade.get("s", trade.get("size", 0.0)),
                "exchange": trade.get("x", trade.get("exchange")),
                "conditions": trade.get("c", trade.get("conditions")),
                "tape": trade.get("z", trade.get("tape")),
                "source": "alpaca",
            }
        )
    return records


def quotes_response_to_records(response: Any, fallback_symbol: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if hasattr(response, "df"):
        df = response.df
        if getattr(df, "empty", True):
            return []
        for row in df.reset_index().to_dict(orient="records"):
            records.append(
                {
                    "symbol": str(row.get("symbol", fallback_symbol)).upper(),
                    "timestamp": row.get("timestamp"),
                    "bid_price": row.get("bid_price", row.get("bp", 0.0)),
                    "bid_size": row.get("bid_size", row.get("bs", 0.0)),
                    "ask_price": row.get("ask_price", row.get("ap", 0.0)),
                    "ask_size": row.get("ask_size", row.get("as", 0.0)),
                    "source": "alpaca",
                }
            )
        return records
    data = getattr(response, "data", {})
    for symbol, quotes in data.items():
        for quote in quotes:
            records.append(
                {
                    "symbol": symbol.upper(),
                    "timestamp": getattr(quote, "timestamp"),
                    "bid_price": getattr(quote, "bid_price", 0.0),
                    "bid_size": getattr(quote, "bid_size", 0.0),
                    "ask_price": getattr(quote, "ask_price", 0.0),
                    "ask_size": getattr(quote, "ask_size", 0.0),
                    "source": "alpaca",
                }
            )
    return records


def trades_response_to_records(response: Any, fallback_symbol: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if hasattr(response, "df"):
        df = response.df
        if getattr(df, "empty", True):
            return []
        for row in df.reset_index().to_dict(orient="records"):
            records.append(
                {
                    "symbol": str(row.get("symbol", fallback_symbol)).upper(),
                    "timestamp": row.get("timestamp"),
                    "price": row.get("price", 0.0),
                    "size": row.get("size", 0.0),
                    "exchange": row.get("exchange"),
                    "conditions": row.get("conditions"),
                    "tape": row.get("tape"),
                    "source": "alpaca",
                }
            )
        return records
    data = getattr(response, "data", {})
    for symbol, trades in data.items():
        for trade in trades:
            records.append(
                {
                    "symbol": symbol.upper(),
                    "timestamp": getattr(trade, "timestamp"),
                    "price": getattr(trade, "price", 0.0),
                    "size": getattr(trade, "size", 0.0),
                    "exchange": getattr(trade, "exchange", None),
                    "conditions": getattr(trade, "conditions", None),
                    "tape": getattr(trade, "tape", None),
                    "source": "alpaca",
                }
            )
    return records


def collect_research_sidecars(db: Database, settings: Any, args: argparse.Namespace, start: datetime, end: datetime):
    collector = ResearchDataCollector(settings, db)
    results = []
    if args.include_news:
        results.append(collector.collect_alpaca_news(start=start, end=end, include_content=True, limit=50))
    if args.include_event_calendar:
        results.append(collector.import_economic_calendar())
    if args.include_market_calendar:
        results.append(collector.collect_market_calendar(start=start.date(), end=end.date()))
    if args.include_macro_series:
        results.append(collector.collect_fred_macro_series(start=start.date(), end=end.date()))
    if args.index_knowledge:
        results.append(collector.index_knowledge_artifacts())
    if args.label_derived:
        results.append(collector.audit_recent_features())
        results.append(collector.label_signal_outcomes())
        results.append(collector.label_news_price_moves())
        results.append(collector.label_economic_event_price_moves())
    for result in results:
        collector._record_run(result, start=start, end=end)
        print(f"Research sidecar {result.data_type}: status={result.status} rows={result.rows} message={result.message}", flush=True)
    return results


def chunk_windows(start: datetime, end: datetime, chunk_days: int):
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=chunk_days), end)
        yield chunk_start, chunk_end
        chunk_start = chunk_end


def load_progress(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"completed_chunks": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("completed_chunks"), list):
        return {"completed_chunks": []}
    return data


def mark_progress(progress: dict[str, Any], path: Path, key: str) -> None:
    completed_chunks = set(progress["completed_chunks"])
    completed_chunks.add(key)
    progress["completed_chunks"] = sorted(completed_chunks)
    path.write_text(json.dumps(progress, indent=2, sort_keys=True), encoding="utf-8")


def chunk_key(kind: str, timeframe: str, symbols: list[str], start: datetime, end: datetime) -> str:
    symbol_part = ",".join(sorted(symbol.upper() for symbol in symbols))
    return f"{kind}|{timeframe}|{symbol_part}|{start.isoformat()}|{end.isoformat()}"


def _can_split_microstructure_window(start: datetime, end: datetime) -> bool:
    return (end - start) > timedelta(minutes=15)


def _window_label(start: datetime, end: datetime) -> str:
    return f"{start.isoformat()} -> {end.isoformat()}"


def _is_memory_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, MemoryError) or "unable to allocate" in text or "memoryerror" in text


def _error_label(exc: Exception) -> str:
    text = str(exc).strip()
    return text if text else exc.__class__.__name__


def retry_download(fn, attempts: int, retry_base_seconds: float):
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # pragma: no cover - depends on network/API behavior
            last_error = exc
            if _is_memory_error(exc):
                print(f"  retry aborted for memory-heavy chunk: {_error_label(exc)}", flush=True)
                raise
            wait_seconds = min(retry_base_seconds * (2 ** (attempt - 1)), 300.0)
            print(f"  retry {attempt}/{attempts} after error: {exc}", flush=True)
            time.sleep(wait_seconds)
    raise RuntimeError(f"download failed after {attempts} attempts: {last_error}")


def dataset_coverage(db: Database, *, full_scan: bool = False) -> list[dict[str, Any]]:
    small_tables = {
        "bars": "SELECT symbol, timeframe, COUNT(*) AS rows, MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts FROM bars GROUP BY symbol, timeframe",
        "bar_variants": "SELECT symbol, timeframe || ':' || adjustment AS timeframe, COUNT(*) AS rows, MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts FROM bar_variants GROUP BY symbol, timeframe, adjustment",
    }
    huge_tables = {
        "quotes": "SELECT symbol, 'quote' AS timeframe, COUNT(*) AS rows, MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts FROM quotes GROUP BY symbol",
        "market_trades": "SELECT symbol, 'trade' AS timeframe, COUNT(*) AS rows, MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts FROM market_trades GROUP BY symbol",
    }
    coverage: list[dict[str, Any]] = []
    for table, sql in small_tables.items():
        coverage.extend(_coverage_rows(db, table, sql))

    cached_items = _cached_coverage_by_table(db)
    for table, sql in huge_tables.items():
        if full_scan:
            coverage.extend(_coverage_rows(db, table, sql))
            continue
        cached = cached_items.get(table, [])
        if cached:
            for item in cached:
                item = dict(item)
                item["coverage_source"] = "cached_manifest"
                coverage.append(item)
        else:
            coverage.append(_skipped_huge_table_coverage(table))
    return coverage


def _coverage_rows(db: Database, table: str, sql: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in db.conn.execute(sql).fetchall():
        item = dict(row)
        item["table"] = table
        item["coverage_source"] = "sqlite_scan"
        rows.append(item)
    return rows


def _cached_coverage_by_table(db: Database) -> dict[str, list[dict[str, Any]]]:
    manifest_path = Path(db.path).parent / "dataset_manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cached: dict[str, list[dict[str, Any]]] = {}
    for item in payload.get("coverage", []):
        if not isinstance(item, dict):
            continue
        table = item.get("table")
        if isinstance(table, str):
            cached.setdefault(table, []).append(dict(item))
    return cached


def _skipped_huge_table_coverage(table: str) -> dict[str, Any]:
    timeframe = "quote" if table == "quotes" else "trade"
    return {
        "symbol": "GLD",
        "timeframe": timeframe,
        "rows": None,
        "first_ts": None,
        "last_ts": None,
        "table": table,
        "coverage_source": "skipped_expensive_scan",
    }


if __name__ == "__main__":
    main()
