from __future__ import annotations

# ruff: noqa: E402

import argparse
import csv
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from gld_scalper.config import load_settings, parse_symbols
from gld_scalper.database import Database
from gld_scalper.research_data import ResearchDataCollector, ResearchDataResult
from gld_scalper.research_data import _news_category, _news_event_type, _score_news_text
from gld_scalper.utils.time_utils import ensure_utc, utc_now

ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
DEFAULT_SYMBOLS = ["GLD", "IAU", "SLV", "GDX", "GDXJ", "UUP", "TLT", "IEF", "SPY", "QQQ"]
DEFAULT_MACRO_TERMS = [
    "gold",
    "gld",
    "dollar",
    "usd",
    "dxy",
    "fed",
    "fomc",
    "powell",
    "cpi",
    "ppi",
    "pce",
    "yields",
    "treasury",
    "rates",
    "inflation",
    "jobs",
    "payroll",
    "nfp",
    "unemployment",
    "recession",
    "war",
    "conflict",
    "banking crisis",
    "safe haven",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Download a deep Alpaca news archive into a historical dataset.")
    parser.add_argument("--start", required=True, help="Start date, inclusive, YYYY-MM-DD.")
    parser.add_argument("--end", required=True, help="End date, exclusive, YYYY-MM-DD.")
    parser.add_argument("--dataset-name", required=True, help="Historical dataset folder name.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS, help="Symbols to query from Alpaca news.")
    parser.add_argument("--macro-terms", nargs="+", default=DEFAULT_MACRO_TERMS, help="Terms used for local macro/event tagging.")
    parser.add_argument("--window-days", type=int, default=7, help="Download window size in days. Use 7 for weekly, 30 for monthly.")
    parser.add_argument("--include-content", action="store_true", help="Ask Alpaca for article content when available.")
    parser.add_argument("--exclude-contentless", action="store_true", help="Skip Alpaca articles that do not include content.")
    parser.add_argument("--limit", type=int, default=50, help="Alpaca page size; API allows 1 to 50.")
    parser.add_argument("--max-pages-per-window", type=int, default=500, help="Safety cap for pagination inside each time window.")
    parser.add_argument("--sleep-seconds", type=float, default=0.25, help="Pause between Alpaca requests to reduce rate-limit pressure.")
    parser.add_argument("--max-retries", type=int, default=8, help="Retry attempts for each Alpaca news request.")
    parser.add_argument("--retry-base-seconds", type=float, default=5.0, help="Initial retry wait; later retries back off from this value.")
    parser.add_argument("--label-moves", action="store_true", help="Link imported news to GLD moves after 1m, 5m, 15m, 1h, and 1d.")
    parser.add_argument("--max-label-batches", type=int, default=1000, help="Maximum 500-row labeling batches to process.")
    parser.add_argument("--export-csv", action="store_true", help="Export news_items.csv after import/labeling.")
    parser.add_argument("--no-resume", action="store_true", help="Ignore completed news windows and request every window again.")
    args = parser.parse_args()

    if not 1 <= args.limit <= 50:
        raise SystemExit("--limit must be between 1 and 50 for Alpaca news.")
    if args.window_days < 1:
        raise SystemExit("--window-days must be at least 1.")

    start = parse_utc_date(args.start)
    end = parse_utc_date(args.end)
    if end <= start:
        raise SystemExit("--end must be after --start.")

    paths = build_paths(args.dataset_name)
    paths["dataset"].mkdir(parents=True, exist_ok=True)
    paths["exports_news"].mkdir(parents=True, exist_ok=True)

    settings = build_settings(paths)
    settings.validate_safety()
    settings.require_credentials()

    db = Database(settings=settings)
    db.init_db()

    progress = load_progress(paths["progress"]) if paths["progress"].exists() and not args.no_resume else {"completed_windows": []}
    symbols = parse_symbols(args.symbols)
    macro_terms = [term.strip().lower() for term in args.macro_terms if term.strip()]

    archive = download_news_archive(
        db=db,
        settings=settings,
        symbols=symbols,
        macro_terms=macro_terms,
        start=start,
        end=end,
        window_days=args.window_days,
        include_content=args.include_content,
        exclude_contentless=args.exclude_contentless,
        limit=args.limit,
        max_pages_per_window=args.max_pages_per_window,
        sleep_seconds=args.sleep_seconds,
        max_retries=args.max_retries,
        retry_base_seconds=args.retry_base_seconds,
        progress=progress,
        progress_path=paths["progress"],
    )

    collector = ResearchDataCollector(settings, db)
    collector._record_run(
        ResearchDataResult("alpaca", "news_archive", archive["inserted"], "completed", json.dumps(archive, sort_keys=True)),
        start=start,
        end=end,
    )

    label_rows = 0
    if args.label_moves:
        for _ in range(args.max_label_batches):
            result = collector.label_news_price_moves()
            label_rows += result.rows
            if result.rows == 0:
                break
        collector._record_run(ResearchDataResult("sqlite", "news_price_moves", label_rows), start=start, end=end)

    export_summary: dict[str, Any] | None = None
    if args.export_csv:
        export_summary = export_news_archive_csv(db, paths["exports_news"])

    total_news = db.conn.execute("SELECT COUNT(*) AS rows FROM news_items").fetchone()["rows"]
    labeled_news = db.conn.execute(
        "SELECT COUNT(*) AS rows FROM news_items WHERE related_move_15m IS NOT NULL"
    ).fetchone()["rows"]
    print(
        json.dumps(
            {
                "archive": archive,
                "database": str(db.path),
                "total_news_items": total_news,
                "news_items_with_15m_move": labeled_news,
                "label_rows_updated_this_run": label_rows,
                "export": export_summary,
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )


def build_paths(dataset_name: str) -> dict[str, Path]:
    safe_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in dataset_name)
    dataset = PROJECT_ROOT / "data" / "paper" / "historical" / safe_name
    return {
        "dataset": dataset,
        "exports_news": PROJECT_ROOT / "exports" / "paper" / "historical" / safe_name / "news_archive",
        "progress": dataset / "news_archive_progress.json",
    }


def build_settings(paths: dict[str, Path]):
    base = load_settings()
    return replace(
        base,
        data_mode="paper",
        database_url=f"sqlite:///{paths['dataset'] / 'gld_scalper_historical.db'}",
        csv_export_dir=str(paths["exports_news"]),
        enable_hourly_csv_export=False,
    )


def export_news_archive_csv(db: Database, export_root: Path) -> dict[str, Any]:
    run_at = utc_now()
    export_dir = export_root / f"export_{run_at.strftime('%Y%m%d_%H%M%S')}"
    export_dir.mkdir(parents=True, exist_ok=True)
    csv_path = export_dir / "news_items.csv"
    cursor = db.conn.execute("SELECT * FROM news_items ORDER BY timestamp ASC, id ASC")
    columns = [description[0] for description in cursor.description]
    count = 0
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        while True:
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            for row in rows:
                writer.writerow([row[column] for column in columns])
                count += 1
    manifest = {
        "created_at": run_at.isoformat(),
        "database": str(db.path),
        "files": {"news_items": count},
    }
    (export_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return {"export_dir": str(export_dir), "files": manifest["files"]}


def parse_utc_date(value: str) -> datetime:
    return datetime.fromisoformat(f"{value}T00:00:00+00:00").astimezone(timezone.utc)


def download_news_archive(
    *,
    db: Database,
    settings: Any,
    symbols: list[str],
    macro_terms: list[str],
    start: datetime,
    end: datetime,
    window_days: int,
    include_content: bool,
    exclude_contentless: bool,
    limit: int,
    max_pages_per_window: int,
    sleep_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
    progress: dict[str, Any],
    progress_path: Path,
) -> dict[str, Any]:
    import requests

    completed = set(progress.get("completed_windows", []))
    stats = {"inserted": 0, "duplicates": 0, "fetched": 0, "windows": 0, "pages": 0, "skipped_windows": 0}
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
    }
    with requests.Session() as session:
        for window_start, window_end in chunk_windows(start, end, window_days):
            key = window_key(window_start, window_end, symbols, include_content, exclude_contentless)
            if key in completed:
                stats["skipped_windows"] += 1
                print(f"news {window_start.date()} -> {window_end.date()}: skipped completed window", flush=True)
                continue
            window_stats = download_news_window(
                session=session,
                db=db,
                headers=headers,
                symbols=symbols,
                macro_terms=macro_terms,
                start=window_start,
                end=window_end,
                include_content=include_content,
                exclude_contentless=exclude_contentless,
                limit=limit,
                max_pages=max_pages_per_window,
                sleep_seconds=sleep_seconds,
                max_retries=max_retries,
                retry_base_seconds=retry_base_seconds,
            )
            for name in ("inserted", "duplicates", "fetched", "pages"):
                stats[name] += window_stats[name]
            stats["windows"] += 1
            completed.add(key)
            progress["completed_windows"] = sorted(completed)
            progress["updated_at"] = utc_now().isoformat()
            progress_path.write_text(json.dumps(progress, indent=2, sort_keys=True), encoding="utf-8")
            print(
                f"news {window_start.date()} -> {window_end.date()}: fetched={window_stats['fetched']} inserted={window_stats['inserted']} duplicates={window_stats['duplicates']} pages={window_stats['pages']}",
                flush=True,
            )
    stats["symbols"] = symbols
    stats["macro_terms"] = macro_terms
    return stats


def download_news_window(
    *,
    session: Any,
    db: Database,
    headers: dict[str, str],
    symbols: list[str],
    macro_terms: list[str],
    start: datetime,
    end: datetime,
    include_content: bool,
    exclude_contentless: bool,
    limit: int,
    max_pages: int,
    sleep_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
) -> dict[str, int]:
    page_token: str | None = None
    seen_tokens: set[str] = set()
    stats = {"inserted": 0, "duplicates": 0, "fetched": 0, "pages": 0}
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "start": ensure_utc(start).isoformat(),
            "end": ensure_utc(end).isoformat(),
            "sort": "asc",
            "symbols": ",".join(symbols),
            "limit": limit,
            "include_content": str(include_content).lower(),
            "exclude_contentless": str(exclude_contentless).lower(),
        }
        if page_token:
            params["page_token"] = page_token
        payload = retry_request(lambda: request_news_page(session, headers, params), max_retries, retry_base_seconds)
        items = payload.get("news", [])
        if not isinstance(items, list):
            raise RuntimeError(f"Unexpected Alpaca news payload shape: {type(items).__name__}")
        for item in items:
            row = news_item_to_record(item, macro_terms)
            before = db.conn.total_changes
            db.insert_news_item(row)
            if db.conn.total_changes > before:
                stats["inserted"] += 1
            else:
                stats["duplicates"] += 1
        stats["fetched"] += len(items)
        stats["pages"] += 1
        next_token = payload.get("next_page_token")
        if not next_token or next_token in seen_tokens:
            break
        seen_tokens.add(str(next_token))
        page_token = str(next_token)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
    else:
        raise RuntimeError(f"Exceeded --max-pages-per-window={max_pages} for {start.isoformat()} -> {end.isoformat()}")
    return stats


def request_news_page(session: Any, headers: dict[str, str], params: dict[str, Any]) -> dict[str, Any]:
    response = session.get(ALPACA_NEWS_URL, headers=headers, params=params, timeout=90)
    if response.status_code == 429:
        reset_after = response.headers.get("X-RateLimit-Reset") or response.headers.get("Retry-After")
        raise RateLimitError(reset_after or "rate limited")
    response.raise_for_status()
    return response.json()


def retry_request(fn, attempts: int, retry_base_seconds: float) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except RateLimitError as exc:
            last_error = exc
            wait_seconds = rate_limit_wait_seconds(exc, retry_base_seconds, attempt)
            print(f"  news retry {attempt}/{attempts} after rate limit; waiting {wait_seconds:.1f}s", flush=True)
            time.sleep(wait_seconds)
        except Exception as exc:  # pragma: no cover - network/API behavior
            last_error = exc
            wait_seconds = min(retry_base_seconds * (2 ** (attempt - 1)), 300.0)
            print(f"  news retry {attempt}/{attempts} after error: {exc}; waiting {wait_seconds:.1f}s", flush=True)
            time.sleep(wait_seconds)
    raise RuntimeError(f"news request failed after {attempts} attempts: {last_error}")


def rate_limit_wait_seconds(exc: "RateLimitError", retry_base_seconds: float, attempt: int) -> float:
    raw = str(exc).strip()
    try:
        value = float(raw)
    except ValueError:
        return min(retry_base_seconds * (2 ** (attempt - 1)), 300.0)
    if value > 1_000_000_000:
        return max(1.0, min(value - time.time(), 300.0))
    return max(1.0, min(value, 300.0))


class RateLimitError(RuntimeError):
    pass


def news_item_to_record(item: dict[str, Any], macro_terms: list[str]) -> dict[str, Any]:
    headline = str(item.get("headline") or item.get("title") or "")
    summary = item.get("summary") or ""
    content = item.get("content") or item.get("content_text")
    text = f"{headline} {summary} {content or ''}"
    matched_terms = [term for term in macro_terms if term in text.lower()]
    scores = _score_news_text(text)
    category = news_category(headline, summary, content, matched_terms)
    event_type = news_event_type(category)
    if matched_terms:
        scores["confidence_score"] = max(float(scores.get("confidence_score") or 0.0), min(0.9, 0.35 + len(matched_terms) * 0.06))
        scores["novelty_score"] = min(1.0, len(set(matched_terms)) / 8.0)
    raw = dict(item)
    raw["macro_terms_matched"] = matched_terms
    return {
        "timestamp": item.get("created_at") or item.get("updated_at") or item.get("timestamp") or utc_now(),
        "source": normalize_source(item.get("source")),
        "headline": headline,
        "url": item.get("url"),
        "author": item.get("author"),
        "summary": summary,
        "content_text": content,
        "symbols": item.get("symbols") or [],
        "category": category,
        "event_type": event_type,
        **scores,
        "raw_json": raw,
    }


def normalize_source(source: Any) -> str | None:
    if source is None:
        return None
    if isinstance(source, dict):
        return str(source.get("name") or source.get("source") or source)
    return str(source)


def news_category(headline: str, summary: Any, content: Any, matched_terms: list[str]) -> str:
    text = f"{headline} {summary or ''} {content or ''}".lower()
    if any(term in text for term in ["fed", "fomc", "powell", "rate", "rates", "yield", "yields", "treasury"]):
        return "fed_rates"
    if any(term in text for term in ["cpi", "ppi", "pce", "inflation", "consumer prices", "producer prices"]):
        return "inflation"
    if any(term in text for term in ["jobs", "payroll", "unemployment", "nfp", "wages"]):
        return "labor"
    if any(term in text for term in ["war", "conflict", "missile", "geopolitical", "safe haven", "banking crisis", "bank crisis"]):
        return "geopolitical"
    if any(term in text for term in ["dollar", "usd", "dxy"]):
        return "usd"
    if any(term in text for term in ["recession", "growth", "gdp", "retail sales", "pmi", "ism"]):
        return "growth_risk"
    if matched_terms:
        return _news_category(headline, summary)
    return _news_category(headline, summary)


def news_event_type(category: str) -> str:
    mapping = {
        "fed_rates": "fed_rate_sentiment",
        "inflation": "inflation_release_or_commentary",
        "labor": "labor_market_release_or_commentary",
        "geopolitical": "headline_event_risk",
        "usd": "usd_sentiment",
        "growth_risk": "growth_or_recession_risk",
    }
    return mapping.get(category, _news_event_type(category, ""))


def chunk_windows(start: datetime, end: datetime, days: int):
    current = start
    step = timedelta(days=days)
    while current < end:
        window_end = min(current + step, end)
        yield current, window_end
        current = window_end


def window_key(start: datetime, end: datetime, symbols: list[str], include_content: bool, exclude_contentless: bool) -> str:
    symbol_part = ",".join(sorted(symbol.upper() for symbol in symbols))
    return f"{start.isoformat()}|{end.isoformat()}|{symbol_part}|content={include_content}|exclude_contentless={exclude_contentless}"


def load_progress(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"completed_windows": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"completed_windows": []}
    if not isinstance(data.get("completed_windows"), list):
        data["completed_windows"] = []
    return data


if __name__ == "__main__":
    main()
