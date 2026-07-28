from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from .alpaca_clients import get_stock_historical_client
from .config import Settings, load_settings
from .database import Database
from .utils.time_utils import utc_now

logger = logging.getLogger(__name__)


TIMEFRAMES = ["1Min", "5Min", "15Min", "1Hour", "1Day"]


class HistoricalDataCollector:
    def __init__(self, settings: Settings | None = None, database: Database | None = None) -> None:
        self.settings = settings or load_settings()
        self.database = database or Database(settings=self.settings)

    def startup_recovery(self) -> dict[str, int]:
        self.database.init_db()
        return self.backfill(
            symbols=self.settings.all_symbols,
            days=self.settings.startup_recovery_days,
        )

    def backfill(self, *, symbols: list[str], days: int = 90) -> dict[str, int]:
        self.settings.validate_safety()
        client = get_stock_historical_client(self.settings)
        end = utc_now()
        results: dict[str, int] = {}
        for timeframe in TIMEFRAMES:
            earliest_start = end - timedelta(days=days)
            start_by_symbol = {
                symbol.upper(): self.database.get_last_bar_timestamp(symbol.upper(), timeframe) or earliest_start
                for symbol in symbols
            }
            start = min(start_by_symbol.values())
            rows = self._download_bars(client, [symbol.upper() for symbol in symbols], timeframe, start, end)
            filtered = [
                row
                for row in rows
                if row["timestamp"] >= start_by_symbol[row["symbol"]]
            ]
            inserted = self.database.upsert_bars(filtered)
            results[timeframe] = inserted
            self.database.log_event(
                "INFO",
                __name__,
                "backfill_completed",
                f"Backfilled {inserted} {timeframe} bars",
                {"symbols": symbols, "start": start.isoformat(), "end": end.isoformat()},
            )
        return results

    def _download_bars(self, client: Any, symbols: list[str], timeframe: str, start, end) -> list[dict[str, Any]]:
        try:
            from alpaca.data.enums import DataFeed
            from alpaca.data.requests import StockBarsRequest
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("alpaca-py data request classes are unavailable.") from exc
        request = StockBarsRequest(
            symbol_or_symbols=symbols,
            timeframe=_alpaca_timeframe(timeframe),
            start=start,
            end=end,
            feed=DataFeed.IEX if self.settings.alpaca_data_feed == "iex" else DataFeed.SIP,
        )
        try:
            response = client.get_stock_bars(request)
        except Exception as exc:  # pragma: no cover - network path
            message = str(exc)
            if "subscription" in message.lower() or "403" in message:
                raise RuntimeError(f"Alpaca data feed rejected request for {self.settings.alpaca_data_feed}: {message}") from exc
            raise RuntimeError(f"Alpaca bar download failed: {message}") from exc
        return _bars_response_to_records(response, timeframe)


def _alpaca_timeframe(name: str) -> Any:
    try:
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("alpaca-py timeframe classes are unavailable.") from exc
    mapping = {
        "1Min": TimeFrame(1, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
        "1Day": TimeFrame(1, TimeFrameUnit.Day),
    }
    if name not in mapping:
        raise ValueError(f"Unsupported timeframe: {name}")
    return mapping[name]


def _bars_response_to_records(response: Any, timeframe: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if hasattr(response, "df"):
        df = response.df
        if getattr(df, "empty", True):
            return []
        df = df.reset_index()
        for row in df.to_dict(orient="records"):
            records.append(
                {
                    "symbol": str(row.get("symbol")).upper(),
                    "timeframe": timeframe,
                    "timestamp": row.get("timestamp"),
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": row.get("volume", 0),
                    "trade_count": row.get("trade_count"),
                    "vwap": row.get("vwap"),
                    "source": "alpaca",
                }
            )
        return records
    data = getattr(response, "data", {})
    for symbol, bars in data.items():
        for bar in bars:
            records.append(
                {
                    "symbol": symbol.upper(),
                    "timeframe": timeframe,
                    "timestamp": getattr(bar, "timestamp"),
                    "open": getattr(bar, "open"),
                    "high": getattr(bar, "high"),
                    "low": getattr(bar, "low"),
                    "close": getattr(bar, "close"),
                    "volume": getattr(bar, "volume", 0),
                    "trade_count": getattr(bar, "trade_count", None),
                    "vwap": getattr(bar, "vwap", None),
                    "source": "alpaca",
                }
            )
    return records
