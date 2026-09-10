from __future__ import annotations

import logging
import json
import queue
import threading
import time
import uuid
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .concurrent_trading import broker_position_direction, is_bot_managed_order
from .config import Settings, load_settings
from .database import Database
from .utils.time_utils import ensure_utc, utc_now


logger = logging.getLogger(__name__)
_ACTIVE_ORDER_STATUSES = {
    "accepted",
    "accepted_for_bidding",
    "calculated",
    "held",
    "new",
    "partially_filled",
    "pending_cancel",
    "pending_new",
    "pending_replace",
    "replaced",
}


class EntryBlockedError(RuntimeError):
    """Raised when a safety gate intentionally refuses a new entry."""


@dataclass(frozen=True, slots=True)
class SafetySnapshot:
    timestamp: datetime
    broker_position_qty: float
    broker_position_direction: str
    broker_open_order_count: int
    database_episode_count: int
    database_episode_direction: str
    internal_episode_count: int | None
    protected_position: bool
    protection_grace_active: bool
    unknown_order_count: int
    consistent: bool
    reasons: tuple[str, ...]
    market_open: bool
    minutes_to_close: float | None


class ExecutionSafetyState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._freeze_reasons: set[str] = set()
        self._failures: dict[str, int] = {}
        self._circuit_open = False
        self._circuit_reason: str | None = None

    def freeze(self, reason: str) -> None:
        with self._lock:
            self._freeze_reasons.add(str(reason))

    def unfreeze(self, reason: str) -> None:
        with self._lock:
            self._freeze_reasons.discard(str(reason))

    def record_success(self, category: str) -> None:
        with self._lock:
            self._failures[category] = 0

    def record_failure(self, category: str, reason: str) -> bool:
        thresholds = {
            "broker_rejection": self.settings.execution_broker_rejection_threshold,
            "stream": self.settings.execution_stream_failure_threshold,
            "reconciliation": self.settings.execution_reconciliation_failure_threshold,
            "order_state": self.settings.execution_order_state_failure_threshold,
        }
        with self._lock:
            count = self._failures.get(category, 0) + 1
            self._failures[category] = count
            if count >= thresholds.get(category, 1):
                self._circuit_open = True
                self._circuit_reason = f"{category}: {reason}"
                self._freeze_reasons.add("circuit_breaker")
                return True
        return False

    def assert_entry_allowed(self) -> None:
        with self._lock:
            if self._circuit_open:
                raise EntryBlockedError(f"execution circuit breaker open: {self._circuit_reason}")
            if self._freeze_reasons:
                reasons = ", ".join(sorted(self._freeze_reasons))
                raise EntryBlockedError(f"new entries frozen: {reasons}")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entry_frozen": bool(self._freeze_reasons),
                "freeze_reasons": sorted(self._freeze_reasons),
                "circuit_open": self._circuit_open,
                "circuit_reason": self._circuit_reason,
                "failure_counts": dict(self._failures),
            }


@dataclass(slots=True)
class _OrderIntent:
    intent_id: str
    idempotency_key: str
    intent_type: str
    symbol: str
    operation: Callable[[], Any]
    future: Future[Any]
    episode_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    queued_ns: int = field(default_factory=time.perf_counter_ns)
    latency_trace_id: str | None = None


class OrderIntentCoordinator:
    """Serializes every broker write and deduplicates repeated requests."""

    def __init__(self, settings: Settings, trading_client: Any, state: ExecutionSafetyState | None = None, latency_tracker: Any | None = None) -> None:
        self.settings = settings
        self.trading_client = trading_client
        self.state = state or ExecutionSafetyState(settings)
        self._queue: queue.Queue[_OrderIntent | None] = queue.Queue()
        self._futures: dict[str, Future[Any]] = {}
        self._futures_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._entry_guard: Callable[[str], None] | None = None
        self.latency_tracker = latency_tracker

    def set_entry_guard(self, guard: Callable[[str], None]) -> None:
        self._entry_guard = guard

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="order-intent-coordinator", daemon=True)
        self._thread.start()

    def stop(self, *, drain: bool = True) -> None:
        if drain:
            deadline = time.monotonic() + self.settings.execution_intent_timeout_seconds
            while self._queue.unfinished_tasks and time.monotonic() < deadline:
                time.sleep(0.05)
        self._stop_event.set()
        self._queue.put(None)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=self.settings.execution_intent_timeout_seconds)

    def status(self) -> dict[str, Any]:
        return {
            **self.state.snapshot(),
            "thread_alive": self._thread is not None and self._thread.is_alive(),
            "queued_intents": self._queue.qsize(),
        }

    def prepare_entry(self, direction: str) -> None:
        self.state.assert_entry_allowed()
        if self._entry_guard is not None:
            self._entry_guard(str(direction).upper())
        self.state.assert_entry_allowed()

    def submit_entry(self, order_data: Any, *, client_order_id: str, direction: str, episode_id: str, latency_trace_id: str | None = None) -> Any:
        self.prepare_entry(direction)

        def operation() -> Any:
            getter = getattr(self.trading_client, "get_order_by_client_id", None)
            if getter is not None:
                try:
                    return getter(client_order_id)
                except Exception as exc:
                    if not _is_not_found(exc):
                        raise
            return self.trading_client.submit_order(order_data=order_data)

        return self._execute(
            intent_type="entry",
            idempotency_key=f"entry:{client_order_id}",
            symbol=self.settings.bot_symbol,
            episode_id=episode_id,
            details={"direction": direction, "client_order_id": client_order_id},
            operation=operation,
            latency_trace_id=latency_trace_id,
        )

    def replace_order(self, order_id: str, order_data: Any, *, idempotency_key: str, episode_id: str | None = None) -> Any:
        return self._execute(
            intent_type="replace",
            idempotency_key=f"replace:{idempotency_key}",
            symbol=self.settings.bot_symbol,
            episode_id=episode_id,
            details={"order_id": str(order_id)},
            operation=lambda: self.trading_client.replace_order_by_id(order_id, order_data=order_data),
        )

    def cancel_order(self, order_id: str, *, reason: str) -> Any:
        return self._execute(
            intent_type="cancel",
            idempotency_key=f"cancel:{order_id}",
            symbol=self.settings.bot_symbol,
            details={"order_id": str(order_id), "reason": reason},
            operation=lambda: self.trading_client.cancel_order_by_id(order_id),
            retry_failed=True,
        )

    def close_position(self, symbol: str, *, reason: str) -> Any:
        cycle = utc_now().strftime("%Y%m%dT%H%M")
        return self._execute(
            intent_type="close_position",
            idempotency_key=f"close:{symbol.upper()}:{reason}:{cycle}",
            symbol=symbol,
            details={"reason": reason},
            operation=lambda: self.trading_client.close_position(symbol),
            retry_failed=True,
        )

    def _execute(
        self,
        *,
        intent_type: str,
        idempotency_key: str,
        symbol: str,
        operation: Callable[[], Any],
        episode_id: str | None = None,
        details: dict[str, Any] | None = None,
        retry_failed: bool = False,
        latency_trace_id: str | None = None,
    ) -> Any:
        self.start()
        with self._futures_lock:
            future = self._futures.get(idempotency_key)
            retry_suffix = ""
            if future is not None and future.done() and future.exception() is not None and retry_failed:
                self._futures.pop(idempotency_key, None)
                future = None
                retry_suffix = f":retry:{uuid.uuid4().hex[:8]}"
            if future is None:
                future = Future()
                self._futures[idempotency_key] = future
                self._queue.put(
                    _OrderIntent(
                        intent_id=str(uuid.uuid4()),
                        idempotency_key=f"{idempotency_key}{retry_suffix}",
                        intent_type=intent_type,
                        symbol=symbol.upper(),
                        operation=operation,
                        future=future,
                        episode_id=episode_id,
                        details=details or {},
                        latency_trace_id=latency_trace_id,
                    )
                )
        try:
            return future.result(timeout=self.settings.execution_intent_timeout_seconds)
        except FutureTimeoutError as exc:
            self.state.record_failure("order_state", f"intent timed out: {idempotency_key}")
            raise TimeoutError(f"Order intent timed out without being resubmitted: {idempotency_key}") from exc

    def _run(self) -> None:
        database = Database(settings=self.settings)
        database.init_db()
        try:
            while not self._stop_event.is_set():
                intent = self._queue.get()
                if intent is None:
                    self._queue.task_done()
                    break
                database.insert_order_intent(
                    {
                        "intent_id": intent.intent_id,
                        "idempotency_key": intent.idempotency_key,
                        "intent_type": intent.intent_type,
                        "symbol": intent.symbol,
                        "episode_id": intent.episode_id,
                        "status": "queued",
                        "details": intent.details,
                    }
                )
                if self.latency_tracker is not None:
                    self.latency_tracker.record(
                        intent.latency_trace_id, "order_intent_dequeued",
                        stage_latency_ms=(time.perf_counter_ns() - intent.queued_ns) / 1_000_000.0,
                        client_order_id=intent.details.get("client_order_id"), status="running",
                    )
                database.update_order_intent(intent.intent_id, status="running")
                try:
                    result = intent.operation()
                    order_id = _text(_field(result, "id")) or None
                    database.update_order_intent(intent.intent_id, status="completed", order_id=order_id)
                    if self.latency_tracker is not None:
                        client_id = intent.details.get("client_order_id")
                        self.latency_tracker.bind(intent.latency_trace_id, client_order_id=client_id, order_id=order_id)
                        self.latency_tracker.record(intent.latency_trace_id, "broker_submission_acknowledged", client_order_id=client_id, order_id=order_id, status="accepted")
                    self.state.record_success("broker_rejection")
                    intent.future.set_result(result)
                except Exception as exc:
                    database.update_order_intent(intent.intent_id, status="failed", error=str(exc))
                    tripped = self.state.record_failure("broker_rejection", str(exc))
                    if tripped:
                        logger.critical("execution circuit opened after broker failures: %s", exc)
                    intent.future.set_exception(exc)
                    if self.latency_tracker is not None:
                        self.latency_tracker.record(intent.latency_trace_id, "broker_submission_failed", status="failed", details={"error": str(exc)})
                finally:
                    self._queue.task_done()
        finally:
            database.close()


class ExecutionSafetySupervisor:
    """Reconciles broker, database, runtime episodes, stream health, and session state."""

    def __init__(
        self,
        settings: Settings | None,
        trading_client: Any,
        coordinator: OrderIntentCoordinator,
    ) -> None:
        self.settings = settings or load_settings()
        self.trading_client = trading_client
        self.coordinator = coordinator
        self.state = coordinator.state
        self.coordinator.set_entry_guard(self.prepare_entry)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._operation_lock = threading.RLock()
        self._internal_episode_provider: Callable[[], set[str]] | None = None
        self._stream_health_provider: Callable[[datetime], dict[str, Any]] | None = None
        self._session_flatten_started = False
        self._last_snapshot: SafetySnapshot | None = None

    def set_internal_episode_provider(self, provider: Callable[[], set[str]]) -> None:
        self._internal_episode_provider = provider

    def set_stream_health_provider(self, provider: Callable[[datetime], dict[str, Any]]) -> None:
        self._stream_health_provider = provider

    def startup(self) -> SafetySnapshot:
        self.state.freeze("startup_reconciliation")
        self.coordinator.start()
        snapshot = self.reconcile(startup=True, record_failure=False)
        snapshot = self._close_orphan_database_episodes_after_flat_confirmation(snapshot)
        confirmed = self._confirm_residual_snapshot(snapshot, startup=True)
        if confirmed is not None:
            if not self.settings.execution_flatten_residual_positions:
                raise RuntimeError(f"Residual GLD exposure requires manual intervention: {confirmed.reasons}")
            self.flatten_symbol("startup_residual_position", release_freeze=False)
            snapshot = self.reconcile(startup=True)
        if snapshot.consistent:
            self.state.unfreeze("startup_reconciliation")
        else:
            raise RuntimeError(f"Startup broker reconciliation failed: {', '.join(snapshot.reasons)}")
        self._apply_session_clock(snapshot)
        self._start_thread()
        return snapshot

    def _close_orphan_database_episodes_after_flat_confirmation(
        self,
        snapshot: SafetySnapshot,
    ) -> SafetySnapshot:
        """Close stale local episodes only after the broker is confirmed flat twice."""
        if (
            snapshot.database_episode_count <= 0
            or snapshot.broker_position_qty != 0
            or snapshot.broker_open_order_count != 0
        ):
            return snapshot
        time.sleep(self.settings.execution_residual_confirmation_delay_seconds)
        position, open_orders, _clock = self._load_broker_state()
        if _float(_field(position, "qty")) != 0 or open_orders:
            return self.reconcile(startup=True, record_failure=False)
        database = Database(settings=self.settings)
        try:
            database.close_symbol_execution_state(
                self.settings.bot_symbol,
                "broker_flat_startup_reconciliation",
            )
            database.insert_execution_safety_event(
                {
                    **self.state.snapshot(),
                    "event_type": "ORPHAN_EPISODES_CLOSED",
                    "severity": "WARNING",
                    "reason": "broker flat on two startup checks",
                    "broker_position_qty": 0,
                    "broker_open_order_count": 0,
                    "database_episode_count": snapshot.database_episode_count,
                    "details": {"second_broker_check": True},
                }
            )
        finally:
            database.close()
        return self.reconcile(startup=True, record_failure=False)

    def _start_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="execution-safety-supervisor", daemon=True)
        self._thread.start()

    def stop(self, *, flatten: bool) -> bool:
        self.state.freeze("process_shutdown")
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=self.settings.execution_reconcile_interval_seconds + 2)
        return self.flatten_symbol("process_shutdown", release_freeze=False) if flatten else True

    def status(self) -> dict[str, Any]:
        snapshot = self._last_snapshot
        return {
            **self.coordinator.status(),
            "last_reconciliation_at": snapshot.timestamp.isoformat() if snapshot else None,
            "last_reconciliation_consistent": snapshot.consistent if snapshot else None,
            "last_reconciliation_reasons": list(snapshot.reasons) if snapshot else [],
        }

    def prepare_entry(self, direction: str) -> None:
        self.state.assert_entry_allowed()
        with self._operation_lock:
            position, open_orders, _clock = self._load_broker_state()
            current_direction = broker_position_direction(position)
            database = Database(settings=self.settings)
            try:
                summary = database.active_trade_episode_summary(self.settings.bot_symbol)
            finally:
                database.close()
            active_direction = current_direction or str(summary.get("direction") or "")
            if active_direction == "MIXED":
                self._trip("reconciliation", "mixed-direction episode state")
                raise EntryBlockedError("mixed-direction episode state requires reconciliation")
            if active_direction and active_direction != direction:
                if not self.settings.execution_enable_direction_switch:
                    raise EntryBlockedError(f"opposite {active_direction} exposure exists")
                self.state.freeze("direction_switch")
                if not self.flatten_symbol("direction_switch", release_freeze=False):
                    raise EntryBlockedError("direction switch flatten did not complete")
                time.sleep(self.settings.execution_direction_switch_cooldown_seconds)
                snapshot = self.reconcile()
                if not snapshot.consistent or snapshot.broker_position_qty != 0 or snapshot.broker_open_order_count:
                    raise EntryBlockedError("direction switch reconciliation did not reach flat state")
                self.state.unfreeze("direction_switch")
            elif position is None and open_orders and str(summary.get("direction") or "") not in {"", direction}:
                self.state.freeze("direction_switch")
                if not self.flatten_symbol("direction_switch_pending_orders", release_freeze=False):
                    raise EntryBlockedError("opposite pending orders could not be canceled")
                time.sleep(self.settings.execution_direction_switch_cooldown_seconds)
                self.state.unfreeze("direction_switch")
        self.state.assert_entry_allowed()

    def reconcile(self, *, startup: bool = False, record_failure: bool = True) -> SafetySnapshot:
        now = utc_now()
        position, open_orders, clock = self._load_broker_state()
        qty = _float(_field(position, "qty"))
        direction = broker_position_direction(position)
        database = Database(settings=self.settings)
        try:
            legacy = database.active_trade_episode_summary(self.settings.bot_symbol)
            atomic = database.fetch_active_execution_episodes(self.settings.bot_symbol)
            database_count = len(atomic) if atomic else int(legacy["count"])
            database_direction = _single_direction(atomic) if atomic else str(legacy["direction"])
            internal_ids = self._internal_episode_provider() if self._internal_episode_provider else None
            coverage = self._protection_coverage(database, atomic, position, open_orders, now)
            recent_episode_ids = set(coverage["grace_episodes"])
            protection_grace_active = coverage["grace"]
            unknown = [
                item
                for item in open_orders
                if not is_bot_managed_order(item, self.settings.bot_symbol, database)
                and _text(_field(item, "id")) not in coverage["known_ids"]
                and not (
                    protection_grace_active
                    and _order_matches_episode_ids(item, recent_episode_ids)
                )
            ]
            protected = (
                position is None
                or coverage["covered"]
            )
            reasons: list[str] = []
            if unknown:
                reasons.append("unknown GLD open orders")
            if database_direction == "MIXED":
                reasons.append("database contains mixed-direction episodes")
            if position is not None and database_count == 0:
                reasons.append("broker position has no database episode")
            if direction and database_direction and direction != database_direction:
                reasons.append("broker and database directions disagree")
            if position is not None and not protected:
                reasons.append("broker position lacks an active protective stop")
            if (
                position is not None
                and internal_ids is not None
                and not internal_ids
                and not protection_grace_active
            ):
                reasons.append("broker position is absent from the position runtime")
            market_open, minutes_to_close = _clock_state(clock, now)
            snapshot = SafetySnapshot(
                timestamp=now,
                broker_position_qty=qty,
                broker_position_direction=direction,
                broker_open_order_count=len(open_orders),
                database_episode_count=database_count,
                database_episode_direction=database_direction,
                internal_episode_count=len(internal_ids) if internal_ids is not None else None,
                protected_position=protected,
                protection_grace_active=protection_grace_active,
                unknown_order_count=len(unknown),
                consistent=not reasons,
                reasons=tuple(reasons),
                market_open=market_open,
                minutes_to_close=minutes_to_close,
            )
            self._last_snapshot = snapshot
            database.insert_execution_safety_event(
                {
                    **self.state.snapshot(),
                    "timestamp": now,
                    "event_type": "RECONCILIATION_GRACE" if protection_grace_active else "RECONCILIATION",
                    "severity": "INFO" if snapshot.consistent else "ERROR" if record_failure else "WARNING",
                    "broker_position_qty": qty,
                    "broker_position_direction": direction,
                    "broker_open_order_count": len(open_orders),
                    "database_episode_count": database_count,
                    "internal_episode_count": snapshot.internal_episode_count,
                    "reason": "; ".join(reasons) or "consistent",
                    "details": {
                        "startup": startup,
                        "protected_position": protected,
                        "protection_grace_active": protection_grace_active,
                        "recent_episode_ids": sorted(recent_episode_ids),
                        "confirmation_check": record_failure,
                        "protection": coverage,
                        "broker_position": _jsonable(position),
                        "broker_open_orders": [_jsonable(order) for order in open_orders],
                    },
                }
            )
        finally:
            database.close()
        if snapshot.consistent:
            self.state.record_success("reconciliation")
            self.state.unfreeze("reconciliation_mismatch")
        elif record_failure:
            self.state.freeze("reconciliation_mismatch")
            self._trip("reconciliation", "; ".join(snapshot.reasons))
        else:
            self.state.freeze("reconciliation_mismatch")
        return snapshot

    def _protection_coverage(self, database, episodes, position, orders, now):
        previous = database.conn.execute(
            "SELECT details_json FROM execution_safety_events WHERE event_type IN "
            "('RECONCILIATION', 'RECONCILIATION_GRACE') ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prior = json.loads(previous[0] or "{}") if previous else {}
        prior = {item["id"]: item for item in prior.get("protection", {}).get("tranches", [])}
        nodes = { _text(_field(o, "id")): o for o, _ in _flatten_order_nodes(orders) }
        result = {"tranches": [], "known_ids": [], "grace_episodes": [],
                  "broker_reads": [], "covered": position is None, "grace": False}
        total_covered = 0.0
        all_covered = True
        for episode in episodes:
            rows = database.conn.execute(
                "SELECT * FROM execution_episode_orders WHERE episode_id=? AND role='entry'",
                (episode["episode_id"],),
            ).fetchall()
            for row in rows:
                stored = dict(row)
                key = stored.get("alpaca_order_id") or stored["order_key"]
                parent = nodes.get(key)
                # Direct reads resolve child orders omitted from the open-order listing.
                if stored.get("alpaca_order_id"):
                    try:
                        from alpaca.trading.requests import GetOrderByIdRequest
                        parent = self.trading_client.get_order_by_id(
                            key, filter=GetOrderByIdRequest(nested=True))
                    except TypeError:
                        try:
                            parent = self.trading_client.get_order_by_id(key)
                        except Exception as exc:
                            result["broker_reads"].append({"id": key, "error": str(exc)})
                    except Exception as exc:
                        result["broker_reads"].append({"id": key, "error": str(exc)})
                if parent is not None:
                    result["broker_reads"].append({"id": key, "response": _jsonable(parent)})
                filled = max(_float(stored.get("filled_qty")), _float(_field(parent, "filled_qty")))
                planned = _float(_field(parent, "qty")) or _float(stored.get("qty"))
                candidates = []
                child_ids = {r[0] for r in database.conn.execute(
                    "SELECT alpaca_order_id FROM execution_episode_orders WHERE parent_order_id=? "
                    "UNION SELECT alpaca_order_id FROM orders WHERE parent_order_id=?",
                    (key, key)) if r[0]}
                for child in list(_field(parent, "legs", []) or []):
                    child_ids.add(_text(_field(child, "id")))
                    candidates.append(child)
                for child_id in child_ids:
                    try:
                        child = self.trading_client.get_order_by_id(child_id)
                        candidates = [o for o in candidates if _text(_field(o, "id")) != child_id]
                        candidates.append(child)
                        result["broker_reads"].append({"id": child_id, "response": _jsonable(child)})
                    except Exception as exc:
                        result["broker_reads"].append({"id": child_id, "error": str(exc)})
                        if child_id in nodes:
                            candidates.append(nodes[child_id])
                result["known_ids"].extend([key, *child_ids])
                closing_side = "sell" if episode["direction"].upper() == "LONG" else "buy"
                stops = { _text(_field(o, "id")): o for o in candidates
                    if _text(_field(o, "symbol")).upper() == self.settings.bot_symbol.upper()
                    and _text(_field(o, "side")).lower() == closing_side
                    and _text(_field(o, "type", _field(o, "order_type"))).lower() in {"stop", "stop_limit", "trailing_stop"}
                    and _status(o) in {"new", "accepted", "partially_filled"} }
                stop_qty = sum(max(0, _float(_field(o, "qty")) - _float(_field(o, "filled_qty"))) for o in stops.values())
                exit_qty = sum(_float(_field(o, "filled_qty")) for o in
                    {_text(_field(o, "id")): o for o in candidates}.values())
                remaining = max(0, filled - exit_qty)
                old = prior.get(key, {})
                first_fill = old.get("first_fill")
                if filled and not first_fill:
                    recorded_fill = database.conn.execute(
                        "SELECT MIN(timestamp) FROM fills WHERE order_id=?", (key,)
                    ).fetchone()[0]
                    first_fill = recorded_fill or _field(parent, "filled_at") or stored.get("updated_at") or now.isoformat()
                    first_fill = ensure_utc(first_fill).isoformat()
                full_fill = old.get("full_fill")
                if filled and filled >= planned and not full_fill:
                    full_fill = ensure_utc(_field(parent, "filled_at") or stored.get("updated_at") or now).isoformat()
                state = "submitted" if not filled else "fully_filled" if filled >= planned else "partially_filled"
                protected = remaining > 0 and stop_qty + 1e-8 >= remaining
                if protected:
                    state = "protection_confirmed"
                anchor = full_fill or first_fill
                grace = bool(remaining and not protected and anchor
                    and 0 <= (now - ensure_utc(anchor)).total_seconds() <= self.settings.execution_bracket_grace_period_seconds)
                # A partial fill has its own fixed deadline; later partials cannot extend it.
                if grace and state == "partially_filled":
                    grace = (now - ensure_utc(first_fill)).total_seconds() <= self.settings.execution_bracket_grace_period_seconds
                result["tranches"].append({"id": key, "state": state, "filled_qty": filled,
                    "remaining_qty": remaining, "stop_qty": stop_qty, "first_fill": first_fill,
                    "full_fill": full_fill, "grace": grace})
                if grace:
                    result["grace_episodes"].append(episode["episode_id"])
                all_covered = all_covered and (not remaining or protected or grace)
                if protected or grace:
                    total_covered += remaining
        result["covered"] = position is None or (all_covered and total_covered + 1e-8 >= abs(_float(_field(position, "qty"))))
        result["grace"] = bool(position is not None and result["covered"] and result["grace_episodes"])
        return result

    def flatten_symbol(self, reason: str, *, release_freeze: bool = True) -> bool:
        freeze_reason = f"flatten:{reason}"
        self.state.freeze(freeze_reason)
        with self._operation_lock:
            try:
                position, open_orders, _clock = self._load_broker_state()
                database = Database(settings=self.settings)
                try:
                    active_episodes = database.fetch_active_execution_episodes(self.settings.bot_symbol)
                finally:
                    database.close()
                for order in open_orders:
                    order_id = _text(_field(order, "id"))
                    if not order_id:
                        continue
                    try:
                        self.coordinator.cancel_order(order_id, reason=reason)
                    except Exception as exc:
                        if not _is_terminal_cancel_error(exc):
                            raise
                if not self._wait_for_no_open_orders(self.settings.execution_cancel_wait_seconds):
                    raise TimeoutError("GLD orders remained open after cancellation deadline")
                position = self._find_position()
                close_order = None
                if position is not None:
                    close_order = self.coordinator.close_position(self.settings.bot_symbol, reason=reason)
                if not self._wait_for_flat(self.settings.execution_shutdown_timeout_seconds):
                    raise TimeoutError("GLD position or open orders remained after flatten deadline")
                if close_order is not None:
                    close_order_id = _text(_field(close_order, "id"))
                    if close_order_id and hasattr(self.trading_client, "get_order_by_id"):
                        try:
                            close_order = self.trading_client.get_order_by_id(close_order_id)
                        except Exception:
                            pass
                database = Database(settings=self.settings)
                try:
                    if close_order is not None and active_episodes:
                        self._record_flatten_exit_orders(
                            database,
                            active_episodes,
                            close_order,
                            position,
                            reason,
                        )
                    from .order_reconciler import PaperOrderReconciler

                    PaperOrderReconciler(self.settings, database, trading_client=self.trading_client).sync(utc_now())
                    database.close_symbol_execution_state(self.settings.bot_symbol, reason)
                    if reason == "session_close":
                        from .performance_tracking import AccountPerformanceTracker, run_performance_consistency_audit

                        AccountPerformanceTracker(self.settings, database, self.trading_client).capture(
                            "end_session",
                            force=True,
                        )
                        run_performance_consistency_audit(
                            database,
                            self.settings,
                            self.trading_client,
                            stage="end_session",
                        )
                    database.insert_execution_safety_event(
                        {
                            **self.state.snapshot(),
                            "event_type": "FLATTEN_CONFIRMED",
                            "severity": "WARNING",
                            "reason": reason,
                            "broker_position_qty": 0,
                            "broker_open_order_count": 0,
                        }
                    )
                finally:
                    database.close()
                self.state.unfreeze(freeze_reason)
                if release_freeze:
                    self.state.unfreeze("reconciliation_mismatch")
                return True
            except Exception as exc:
                self._trip("order_state", f"flatten failed: {exc}")
                logger.exception("execution safety flatten failed reason=%s error=%s", reason, exc)
                return False

    def _record_flatten_exit_orders(
        self,
        database: Database,
        episodes: list[dict[str, Any]],
        close_order: Any,
        position: Any,
        reason: str,
    ) -> None:
        close_order_id = _text(_field(close_order, "id")) or f"safety-close-{utc_now().timestamp()}"
        total_qty = abs(_float(_field(close_order, "filled_qty"))) or abs(_float(_field(position, "qty")))
        weights = [float(item.get("remaining_qty") or item.get("filled_qty") or item.get("planned_qty") or 0.0) for item in episodes]
        weight_total = sum(weights)
        if total_qty <= 0 or weight_total <= 0:
            return
        allocated = 0.0
        for index, (episode, weight) in enumerate(zip(episodes, weights, strict=True)):
            qty = total_qty - allocated if index == len(episodes) - 1 else total_qty * weight / weight_total
            qty = max(0.0, qty)
            allocated += qty
            database.record_execution_episode_order(
                str(episode["episode_id"]),
                {
                    "order_key": f"{close_order_id}:{episode['episode_id']}",
                    "alpaca_order_id": close_order_id,
                    "client_order_id": _text(_field(close_order, "client_order_id")) or None,
                        "role": "safety_flatten",
                        "intent_type": "exit",
                        "strategy_path": episode.get("strategy_path"),
                        "playbook": episode.get("playbook"),
                        "close_reason": reason,
                    "side": _text(_field(close_order, "side")) or None,
                    "qty": qty,
                    "filled_qty": qty,
                    "filled_avg_price": _float(_field(close_order, "filled_avg_price")) or None,
                    "status": _status(close_order) or "filled",
                    "submitted_at": _field(close_order, "submitted_at"),
                    "updated_at": _field(close_order, "filled_at") or utc_now(),
                    "raw_json": _jsonable(close_order),
                },
            )

    def _run(self) -> None:
        while not self._stop_event.wait(self.settings.execution_reconcile_interval_seconds):
            try:
                snapshot = self.reconcile(record_failure=False)
                confirmed = self._confirm_residual_snapshot(snapshot)
                if confirmed is not None and self.settings.execution_flatten_residual_positions:
                    self.flatten_symbol("unprotected_residual_position")
                    continue
                self._apply_session_clock(snapshot)
                self._check_stream(snapshot)
                self._recover_stuck_orders()
            except Exception as exc:
                self._trip("reconciliation", str(exc))
                logger.exception("execution safety supervisor cycle failed: %s", exc)

    def _apply_session_clock(self, snapshot: SafetySnapshot) -> None:
        remaining = snapshot.minutes_to_close
        if not snapshot.market_open:
            self.state.freeze("market_closed")
            return
        self.state.unfreeze("market_closed")
        if remaining is not None:
            if remaining <= self.settings.execution_entry_freeze_minutes_before_close:
                self.state.freeze("session_close_window")
            if remaining <= self.settings.execution_session_flatten_minutes_before_close and not self._session_flatten_started:
                self._session_flatten_started = self.flatten_symbol("session_close", release_freeze=False)
            elif remaining > self.settings.execution_entry_freeze_minutes_before_close:
                self.state.unfreeze("session_close_window")
        if remaining is not None and remaining > self.settings.execution_entry_freeze_minutes_before_close:
            self._session_flatten_started = False

    def _check_stream(self, snapshot: SafetySnapshot) -> None:
        if not snapshot.market_open or self._stream_health_provider is None:
            self.state.record_success("stream")
            return
        health = self._stream_health_provider(utc_now())
        failed = not bool(health.get("websocket_connected")) or bool(health.get("stream_stale"))
        if failed:
            reason = str(health.get("stream_stale_reason") or health.get("stream_last_error") or "stream unavailable")
            self._trip("stream", reason)
        else:
            self.state.record_success("stream")

    def _recover_stuck_orders(self) -> None:
        _position, open_orders, _clock = self._load_broker_state()
        now = utc_now()
        failures = 0
        database = Database(settings=self.settings)
        try:
            for order, parent_id in _flatten_order_nodes(open_orders):
                status = _status(order)
                if status not in {"accepted", "new", "pending_new", "held", "replaced", "pending_cancel"}:
                    continue
                if parent_id and status in {"accepted", "new", "pending_new", "held"}:
                    continue
                age = _order_age_seconds(order, now)
                stored = database.get_order(
                    alpaca_order_id=_text(_field(order, "id")) or None,
                    client_order_id=_text(_field(order, "client_order_id")) or None,
                ) or {}
                timeout = self._entry_timeout_seconds(stored) if not parent_id else self.settings.execution_order_state_timeout_seconds
                if age is None or age < timeout:
                    continue
                order_id = _text(_field(order, "id"))
                if not order_id:
                    failures += 1
                    continue
                try:
                    current = self.trading_client.get_order_by_id(order_id)
                    if _status(current) not in _ACTIVE_ORDER_STATUSES:
                        continue
                    if not parent_id and self._try_reprice_entry(database, current, stored, now):
                        continue
                    self.coordinator.cancel_order(order_id, reason=f"entry_timeout_{status}" if not parent_id else f"stuck_{status}")
                except Exception as exc:
                    if not _is_terminal_cancel_error(exc):
                        failures += 1
                        logger.warning("stuck order recovery failed order_id=%s status=%s error=%s", order_id, status, exc)
        finally:
            database.close()
        if failures:
            self._trip("order_state", f"{failures} stuck orders could not be recovered")
        else:
            self.state.record_success("order_state")

    def _entry_timeout_seconds(self, stored: dict[str, Any]) -> int:
        if str(stored.get("playbook") or "") == "news_event":
            return self.settings.execution_news_entry_timeout_seconds
        if str(stored.get("strategy_path") or "minute") == "fast":
            return self.settings.execution_fast_entry_timeout_seconds
        return self.settings.execution_minute_entry_timeout_seconds

    def _try_reprice_entry(
        self,
        database: Database,
        order: Any,
        stored: dict[str, Any],
        now: datetime,
    ) -> bool:
        if self.settings.execution_entry_max_reprices == 0:
            return False
        order_id = _text(_field(order, "id"))
        prior = database.conn.execute(
            """SELECT COUNT(*) AS count FROM order_intents
               WHERE intent_type='replace' AND details_json LIKE ?""",
            (f'%"order_id": "{order_id}"%',),
        ).fetchone()
        if int(prior["count"] or 0) >= self.settings.execution_entry_max_reprices:
            return False
        quote = database.get_latest_quote(self.settings.bot_symbol)
        if not quote or not quote.get("timestamp"):
            return False
        quote_age = max(0.0, (now - ensure_utc(quote["timestamp"])).total_seconds())
        strategy_path = str(stored.get("strategy_path") or "minute")
        quote_limit = (
            self.settings.fast_scalp_max_quote_age_seconds
            if strategy_path == "fast"
            else self.settings.minute_entry_max_quote_age_seconds
        )
        if quote_age > quote_limit:
            return False
        bid = _float(quote.get("bid_price"))
        ask = _float(quote.get("ask_price"))
        side = _text(_field(order, "side") or stored.get("side")).lower()
        current_limit = _float(_field(order, "limit_price") or stored.get("limit_price"))
        candidate = ask if side == "buy" else bid if side == "sell" else 0.0
        if candidate <= 0 or current_limit <= 0 or abs(candidate / current_limit - 1.0) > self.settings.execution_entry_max_reprice_pct:
            return False
        if side == "buy" and candidate <= current_limit or side == "sell" and candidate >= current_limit:
            return False
        stop = _float(stored.get("stop_price"))
        target = _float(stored.get("take_profit_price"))
        risk = candidate - stop if side == "buy" else stop - candidate
        reward = target - candidate if side == "buy" else candidate - target
        economic_move = candidate * _float(stored.get("economic_breakeven_pct"))
        if risk <= 0 or reward <= max(risk * self.settings.position_min_reward_risk, economic_move * 1.5):
            return False
        try:
            from alpaca.trading.requests import ReplaceOrderRequest
        except Exception:
            return False
        self.coordinator.replace_order(
            order_id,
            ReplaceOrderRequest(limit_price=round(candidate, 2)),
            idempotency_key=f"entry_reprice:{order_id}:{round(candidate, 2)}",
            episode_id=_root_client_order_id(_text(_field(order, "client_order_id"))),
        )
        logger.info(
            "repriced stale entry order_id=%s strategy_path=%s old_limit=%.2f new_limit=%.2f quote_age=%.2f",
            order_id,
            strategy_path,
            current_limit,
            candidate,
            quote_age,
            extra={"event_type": "entry_repriced"},
        )
        return True

    def _load_broker_state(self) -> tuple[Any | None, list[Any], Any | None]:
        positions = list(self.trading_client.get_all_positions() or [])
        position = next(
            (
                item for item in positions
                if _text(_field(item, "symbol")).upper() == self.settings.bot_symbol.upper()
                and abs(_float(_field(item, "qty"))) > 0
            ),
            None,
        )
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            request = GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[self.settings.bot_symbol], nested=True)
            orders = list(self.trading_client.get_orders(filter=request) or [])
        except (ImportError, TypeError):
            orders = list(self.trading_client.get_orders() or [])
        orders = [item for item in orders if _text(_field(item, "symbol", self.settings.bot_symbol)).upper() == self.settings.bot_symbol.upper()]
        clock = self.trading_client.get_clock() if hasattr(self.trading_client, "get_clock") else None
        return position, orders, clock

    def _find_position(self) -> Any | None:
        positions = list(self.trading_client.get_all_positions() or [])
        return next(
            (item for item in positions if _text(_field(item, "symbol")).upper() == self.settings.bot_symbol.upper() and abs(_float(_field(item, "qty"))) > 0),
            None,
        )

    def _wait_for_no_open_orders(self, timeout_seconds: int) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            _position, orders, _clock = self._load_broker_state()
            if not orders:
                return True
            time.sleep(0.5)
        return False

    def _wait_for_flat(self, timeout_seconds: int) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            position, orders, _clock = self._load_broker_state()
            if position is None and not orders:
                return True
            time.sleep(0.5)
        return False

    def _requires_residual_flatten(self, snapshot: SafetySnapshot) -> bool:
        if snapshot.protection_grace_active:
            return False
        return snapshot.unknown_order_count > 0 or (
            snapshot.broker_position_qty != 0
            and (not snapshot.protected_position or snapshot.database_episode_count == 0)
        )

    def _confirm_residual_snapshot(
        self,
        snapshot: SafetySnapshot,
        *,
        startup: bool = False,
    ) -> SafetySnapshot | None:
        if not self._requires_residual_flatten(snapshot):
            return None
        delay = self.settings.execution_residual_confirmation_delay_seconds
        if delay > 0:
            if self._stop_event.wait(delay) and not startup:
                return None
        confirmation = self.reconcile(startup=startup, record_failure=True)
        database = Database(settings=self.settings)
        try:
            confirmed = self._requires_residual_flatten(confirmation)
            database.insert_execution_safety_event(
                {
                    **self.state.snapshot(),
                    "event_type": "RESIDUAL_CONFIRMED" if confirmed else "RESIDUAL_CLEARED",
                    "severity": "ERROR" if confirmed else "INFO",
                    "broker_position_qty": confirmation.broker_position_qty,
                    "broker_position_direction": confirmation.broker_position_direction,
                    "broker_open_order_count": confirmation.broker_open_order_count,
                    "database_episode_count": confirmation.database_episode_count,
                    "internal_episode_count": confirmation.internal_episode_count,
                    "reason": "; ".join(confirmation.reasons)
                    if confirmed
                    else "second broker check cleared provisional residual state",
                    "details": {
                        "first_check_at": snapshot.timestamp.isoformat(),
                        "second_check_at": confirmation.timestamp.isoformat(),
                        "delay_seconds": delay,
                        "startup": startup,
                    },
                }
            )
        finally:
            database.close()
        return confirmation if confirmed else None

    def _trip(self, category: str, reason: str) -> None:
        tripped = self.state.record_failure(category, reason)
        if tripped:
            database = Database(settings=self.settings)
            try:
                database.insert_execution_safety_event(
                    {
                        **self.state.snapshot(),
                        "event_type": "CIRCUIT_OPENED",
                        "severity": "CRITICAL",
                        "reason": reason,
                        "details": {"category": category},
                    }
                )
            finally:
                database.close()


def _clock_state(clock: Any | None, now: datetime) -> tuple[bool, float | None]:
    if clock is None:
        return False, None
    market_open = bool(_field(clock, "is_open", False))
    next_close = _field(clock, "next_close")
    if next_close is None:
        return market_open, None
    return market_open, (ensure_utc(next_close) - now).total_seconds() / 60


def _recent_execution_episode_ids(
    episodes: list[dict[str, Any]],
    now: datetime,
    grace_seconds: int,
) -> set[str]:
    recent: set[str] = set()
    for episode in episodes:
        opened_at = episode.get("opened_at")
        if not opened_at:
            continue
        age = (now - ensure_utc(opened_at)).total_seconds()
        if 0 <= age <= grace_seconds:
            recent.add(str(episode["episode_id"]))
    return recent


def _order_matches_episode_ids(order: Any, episode_ids: set[str]) -> bool:
    client_order_id = _text(_field(order, "client_order_id")).upper()
    if not client_order_id:
        return False
    return any(
        client_order_id == episode_id.upper()
        or client_order_id.startswith(f"{episode_id.upper()}-")
        for episode_id in episode_ids
    )


def _has_active_protective_stop(orders: list[Any]) -> bool:
    for order, parent_id in _flatten_order_nodes(orders):
        order_type = _text(_field(order, "order_type", _field(order, "type"))).lower()
        if parent_id and order_type in {"stop", "stop_limit", "trailing_stop"} and _status(order) in _ACTIVE_ORDER_STATUSES:
            return True
        intent = _text(_field(order, "position_intent")).lower()
        if intent.endswith("_to_close") and order_type in {"stop", "stop_limit", "trailing_stop"} and _status(order) in _ACTIVE_ORDER_STATUSES:
            return True
    return False


def _flatten_order_nodes(orders: list[Any]) -> list[tuple[Any, str | None]]:
    result: list[tuple[Any, str | None]] = []
    for order in orders:
        result.append((order, None))
        parent_id = _text(_field(order, "id")) or None
        for leg in list(_field(order, "legs", []) or []):
            result.append((leg, parent_id))
    return result


def _single_direction(episodes: list[dict[str, Any]]) -> str:
    directions = {str(item.get("direction") or "").upper() for item in episodes if item.get("direction")}
    return next(iter(directions)) if len(directions) == 1 else "MIXED" if directions else ""


def _root_client_order_id(client_order_id: str) -> str | None:
    value = str(client_order_id or "")
    for suffix in ("-TAKE", "-RUN"):
        if value.upper().endswith(suffix):
            return value[: -len(suffix)]
    return value or None


def _order_age_seconds(order: Any, now: datetime) -> float | None:
    timestamp = _field(order, "updated_at") or _field(order, "submitted_at") or _field(order, "created_at")
    if timestamp is None:
        return None
    return max(0.0, (now - ensure_utc(timestamp)).total_seconds())


def _is_not_found(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    code = getattr(exc, "code", None)
    text = str(exc).lower()
    return status_code == 404 or code == 404 or "not found" in text or "order does not exist" in text


def _is_terminal_cancel_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(value in text for value in ("already canceled", "already filled", "order is not cancelable", "not found"))


def _status(value: Any) -> str:
    return _text(_field(value, "status")).lower()


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _text(value: Any) -> str:
    return str(getattr(value, "value", value or ""))


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    return {"repr": repr(value)}
