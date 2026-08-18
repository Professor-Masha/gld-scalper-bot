from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any

from .alpaca_clients import get_stock_data_stream
from .config import Settings, load_settings
from .database import Database
from .utils.time_utils import utc_now

logger = logging.getLogger(__name__)


class StockStreamCollector:
    def __init__(
        self,
        settings: Settings | None = None,
        database: Database | None = None,
        event_sink: Callable[[str, dict[str, Any], Any], None] | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.database = database or Database(settings=self.settings)
        self.event_sink = event_sink
        self.connected = False
        self.last_bar_received_at = None
        self.last_quote_received_at = None
        self.last_trade_received_at = None
        self.last_message_received_at = None
        self.last_error: str | None = None
        self.bar_count = 0
        self.quote_count = 0
        self.trade_count = 0
        self._stream: Any | None = None

    async def handle_bar(self, bar: Any) -> None:
        received_at = utc_now()
        record = {
            "symbol": _field(bar, "symbol"),
            "timeframe": "1Min",
            "timestamp": _field(bar, "timestamp"),
            "open": _field(bar, "open"),
            "high": _field(bar, "high"),
            "low": _field(bar, "low"),
            "close": _field(bar, "close"),
            "volume": _field(bar, "volume", 0),
            "trade_count": _field(bar, "trade_count", None),
            "vwap": _field(bar, "vwap", None),
            "source": "alpaca_stream",
        }
        self.database.upsert_bars(
            [
                record
            ]
        )
        self.connected = True
        self.last_bar_received_at = received_at
        self.last_message_received_at = received_at
        self.bar_count += 1
        self._emit("bar", record, received_at)

    async def handle_quote(self, quote: Any) -> None:
        received_at = utc_now()
        record = {
            "symbol": _field(quote, "symbol"),
            "timestamp": _field(quote, "timestamp"),
            "bid_price": _field(quote, "bid_price"),
            "bid_size": _field(quote, "bid_size", 0),
            "ask_price": _field(quote, "ask_price"),
            "ask_size": _field(quote, "ask_size", 0),
            "source": "alpaca_stream",
        }
        self._emit("quote", record, received_at)
        self.database.upsert_quotes(
            [
                record
            ]
        )
        self.connected = True
        self.last_quote_received_at = received_at
        self.last_message_received_at = received_at
        self.quote_count += 1

    async def handle_trade(self, trade: Any) -> None:
        received_at = utc_now()
        record = {
            "symbol": _field(trade, "symbol"),
            "timestamp": _field(trade, "timestamp"),
            "price": _field(trade, "price"),
            "size": _field(trade, "size", 0),
            "exchange": _field(trade, "exchange", None),
            "conditions": _field(trade, "conditions", None),
            "tape": _field(trade, "tape", None),
            "source": "alpaca_stream",
        }
        self._emit("trade", record, received_at)
        self.database.upsert_trades(
            [
                record
            ]
        )
        self.connected = True
        self.last_trade_received_at = received_at
        self.last_message_received_at = received_at
        self.trade_count += 1

    async def start(self, stop_event: threading.Event | None = None) -> None:
        while stop_event is None or not stop_event.is_set():
            stream = get_stock_data_stream(self.settings)
            self._stream = stream
            stream.subscribe_bars(self.handle_bar, self.settings.bot_symbol)
            stream.subscribe_quotes(self.handle_quote, self.settings.bot_symbol)
            stream.subscribe_trades(self.handle_trade, self.settings.bot_symbol)
            for symbol in self.settings.related_symbols:
                stream.subscribe_bars(self.handle_bar, symbol)
            try:
                self.connected = False
                self.last_error = None
                self.database.log_event("INFO", __name__, "stream_starting", "Alpaca data stream starting", {"symbols": self.settings.all_symbols})
                await stream._run_forever()
            except Exception as exc:  # pragma: no cover - network path
                self.connected = False
                self.last_error = str(exc)
                self.database.log_event("ERROR", __name__, "stream_disconnected", str(exc), {})
            finally:
                self.connected = False
                self._stream = None
            if stop_event is not None and stop_event.is_set():
                break
            await asyncio.sleep(5)

    def stop(self) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            stream.stop()
        except Exception as exc:  # pragma: no cover - shutdown path
            self.last_error = str(exc)

    def _emit(self, event_type: str, payload: dict[str, Any], received_at: Any) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(event_type, payload, received_at)
        except Exception as exc:  # pragma: no cover - callback guard
            self.last_error = str(exc)


class LiveDataStreamRuntime:
    def __init__(self, settings: Settings, event_sink: Callable[[str, dict[str, Any], Any], None] | None = None) -> None:
        self.settings = settings
        self.event_sink = event_sink
        self.collector: StockStreamCollector | None = None
        self.started_at = None
        self.last_error: str | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.started_at = utc_now()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="alpaca-data-stream", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self.collector is not None:
            self.collector.stop()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)

    def health(self, now=None) -> dict[str, Any]:
        now = now or utc_now()
        thread_alive = self._thread is not None and self._thread.is_alive()
        collector = self.collector
        connected = bool(thread_alive and collector is not None and collector.connected)
        last_message_at = collector.last_message_received_at if collector else None
        last_bar_at = collector.last_bar_received_at if collector else None
        last_quote_at = collector.last_quote_received_at if collector else None
        last_trade_at = collector.last_trade_received_at if collector else None
        message_age = max(0.0, (now - last_message_at).total_seconds()) if last_message_at else None
        bar_age = max(0.0, (now - last_bar_at).total_seconds()) if last_bar_at else None
        quote_age = max(0.0, (now - last_quote_at).total_seconds()) if last_quote_at else None
        trade_age = max(0.0, (now - last_trade_at).total_seconds()) if last_trade_at else None
        startup_age = max(0.0, (now - self.started_at).total_seconds()) if self.started_at else 0.0
        tick_ages = [age for age in [quote_age, trade_age] if age is not None]
        tick_age = min(tick_ages) if tick_ages else None
        message_stale = bool(message_age is not None and message_age > self.settings.stale_data_seconds)
        quote_stale = bool(quote_age is None or quote_age > self.settings.quote_stale_seconds)
        trade_stale = bool(trade_age is None or trade_age > self.settings.quote_stale_seconds)
        bar_stale = bool(bar_age is None or bar_age > self.settings.bar_stale_seconds)
        tick_stale = bool(tick_age is not None and tick_age > self.settings.quote_stale_seconds)
        if connected:
            no_ticks_after_grace = tick_age is None and startup_age > self.settings.quote_stale_seconds
            stream_stale = message_stale or tick_stale or no_ticks_after_grace
        else:
            stream_stale = startup_age > self.settings.stale_data_seconds
        stale_reason = _stale_reason(
            connected=connected,
            thread_alive=thread_alive,
            collector_ready=collector is not None,
            message_age=message_age,
            bar_age=bar_age,
            quote_age=quote_age,
            trade_age=trade_age,
            startup_age=startup_age,
            stale_data_seconds=self.settings.stale_data_seconds,
            bar_stale_seconds=self.settings.bar_stale_seconds,
            quote_stale_seconds=self.settings.quote_stale_seconds,
            stream_stale=stream_stale,
        )
        error = self.last_error or (collector.last_error if collector else None)
        return {
            "websocket_connected": connected,
            "stream_thread_alive": thread_alive,
            "stream_stale": stream_stale,
            "stream_connected_but_stale": bool(connected and stream_stale),
            "stream_message_stale": message_stale,
            "stream_quote_stale": quote_stale,
            "stream_trade_stale": trade_stale,
            "stream_bar_stale": bar_stale,
            "stream_last_message_at": last_message_at.isoformat() if last_message_at else None,
            "stream_message_age_seconds": message_age,
            "stream_last_bar_at": last_bar_at.isoformat() if last_bar_at else None,
            "stream_last_quote_at": last_quote_at.isoformat() if last_quote_at else None,
            "stream_last_trade_at": last_trade_at.isoformat() if last_trade_at else None,
            "stream_bar_age_seconds": bar_age,
            "stream_quote_age_seconds": quote_age,
            "stream_trade_age_seconds": trade_age,
            "stream_bar_count": collector.bar_count if collector else 0,
            "stream_quote_count": collector.quote_count if collector else 0,
            "stream_trade_count": collector.trade_count if collector else 0,
            "stream_stale_reason": stale_reason,
            "stream_last_error": error,
        }

    def _run(self) -> None:
        database = Database(settings=self.settings)
        database.init_db()
        self.collector = StockStreamCollector(self.settings, database, event_sink=self.event_sink)
        try:
            asyncio.run(self.collector.start(self._stop_event))
        except Exception as exc:  # pragma: no cover - network path
            self.last_error = str(exc)
            try:
                database.log_event("ERROR", __name__, "stream_runtime_failed", str(exc), {})
            except Exception:
                logger.exception("stream runtime failed")
        finally:
            database.close()


def _field(message: Any, name: str, default: Any = None) -> Any:
    if isinstance(message, dict):
        return message.get(name, default)
    return getattr(message, name, default)


def _stale_reason(
    *,
    connected: bool,
    thread_alive: bool,
    collector_ready: bool,
    message_age: float | None,
    bar_age: float | None,
    quote_age: float | None,
    trade_age: float | None,
    startup_age: float,
    stale_data_seconds: int,
    bar_stale_seconds: int,
    quote_stale_seconds: int,
    stream_stale: bool,
) -> str:
    if not thread_alive:
        return "stream thread not alive"
    if not collector_ready:
        return "stream collector not initialized"
    if not connected:
        return "websocket not connected"
    if message_age is None:
        return "connected but no live messages received"
    if message_age > stale_data_seconds:
        return f"last live message age {message_age:.1f}s exceeds {stale_data_seconds}s"
    if bar_age is None and startup_age > bar_stale_seconds:
        return "connected but no live bars received"
    if quote_age is None and trade_age is None and startup_age > quote_stale_seconds:
        return "connected but no live quotes or trades received"
    tick_ages = [age for age in [quote_age, trade_age] if age is not None]
    tick_age = min(tick_ages) if tick_ages else None
    if stream_stale and tick_age is not None and tick_age > quote_stale_seconds:
        return f"latest GLD quote/trade age {tick_age:.1f}s exceeds {quote_stale_seconds}s"
    if stream_stale and bar_age is not None and tick_age is not None:
        return f"live bars age {bar_age:.1f}s and latest tick age {tick_age:.1f}s exceed freshness limits"
    if stream_stale and bar_age is not None:
        return f"live bar age {bar_age:.1f}s exceeds {bar_stale_seconds}s"
    return "healthy"
