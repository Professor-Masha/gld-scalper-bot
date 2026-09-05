from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Callable

from .alpaca_clients import get_trading_stream
from .config import Settings
from .database import Database
from .execution_safety import ExecutionSafetyState
from .order_reconciler import PaperOrderReconciler
from .utils.time_utils import utc_now


logger = logging.getLogger(__name__)


class BrokerOrderUpdateRuntime:
    """Consume Alpaca trade_updates while retaining REST reconciliation as fallback."""

    def __init__(
        self,
        settings: Settings,
        safety_state: ExecutionSafetyState,
        *,
        stream_factory: Callable[[Settings], Any] = get_trading_stream,
        latency_tracker: Any | None = None,
    ) -> None:
        self.settings = settings
        self.safety_state = safety_state
        self.stream_factory = stream_factory
        self.latency_tracker = latency_tracker
        self.last_event_at: datetime | None = None
        self.last_error: str | None = None
        self.event_count = 0
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._stream: Any | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="alpaca-trade-updates", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        stream = self._stream
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                logger.exception("failed to stop Alpaca trade-update stream")
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)

    def health(self) -> dict[str, Any]:
        return {
            "thread_alive": self._thread is not None and self._thread.is_alive(),
            "event_count": self.event_count,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
            "last_error": self.last_error,
        }

    def _run(self) -> None:
        database = Database(settings=self.settings)
        database.init_db()
        reconciler = PaperOrderReconciler(self.settings, database)

        async def on_trade_update(update: Any) -> None:
            try:
                order = _field(update, "order", {})
                client_order_id = _text(_field(order, "client_order_id")) or None
                order_id = _text(_field(order, "id")) or None
                event_name = _text(_field(update, "event")).lower()
                if self.latency_tracker is not None:
                    trace_id = self.latency_tracker.trace_for(client_order_id=client_order_id, order_id=order_id)
                    if trace_id:
                        self.latency_tracker.bind(trace_id, client_order_id=client_order_id, order_id=order_id)
                        stage = "fill_received" if event_name in {"fill", "partial_fill"} else "broker_trade_update"
                        self.latency_tracker.record(trace_id, stage, client_order_id=client_order_id, order_id=order_id, status=event_name)
                result = reconciler.process_trade_update(update, utc_now())
                self.last_event_at = utc_now()
                self.event_count += 1
                self.last_error = None
                self.safety_state.record_success("order_state")
                database.log_event(
                    "INFO",
                    __name__,
                    "broker_trade_update",
                    "Alpaca trade update persisted",
                    {"event": _text(_field(update, "event")), **result},
                )
            except Exception as exc:
                self.last_error = str(exc)
                self.safety_state.record_failure("order_state", f"trade update persistence failed: {exc}")
                logger.exception("Alpaca trade update persistence failed: %s", exc)

        try:
            while not self._stop_event.is_set():
                try:
                    self._stream = self.stream_factory(self.settings)
                    self._stream.subscribe_trade_updates(on_trade_update)
                    self._stream.run()
                    if not self._stop_event.is_set():
                        raise RuntimeError("Alpaca trade-update stream ended unexpectedly")
                except Exception as exc:
                    if self._stop_event.is_set():
                        break
                    self.last_error = str(exc)
                    self.safety_state.record_failure("order_state", f"trade update stream: {exc}")
                    database.log_event(
                        "ERROR",
                        __name__,
                        "broker_trade_update_stream_failed",
                        str(exc),
                        self.health(),
                    )
                    logger.exception("Alpaca trade-update stream failed; reconnecting: %s", exc)
                    self._stop_event.wait(3)
                finally:
                    self._stream = None
        finally:
            database.close()


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _text(value: Any) -> str:
    return str(getattr(value, "value", value or ""))
