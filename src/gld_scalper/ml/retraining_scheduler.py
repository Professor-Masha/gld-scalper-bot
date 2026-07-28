from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings
from ..database import Database
from ..utils.time_utils import market_session, utc_now
from .dataset_builder import build_training_dataset, label_quality
from .trainer import train_candidate_model

logger = logging.getLogger(__name__)


class SafeRetrainingScheduler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_attempt_at: datetime | None = None
        self._last_completed_at: datetime | None = None
        self._last_result: dict[str, Any] | None = None
        self._pending_completion: dict[str, Any] | None = None
        self._last_error: str | None = None

    def maybe_start(self, now: datetime | None = None, *, live_orders_idle: bool = True) -> bool:
        now = now or utc_now()
        if not self.settings.enable_scheduled_retraining:
            return False
        if self.settings.retrain_only_outside_regular_hours and market_session(now, extended_hours=False) == "regular":
            return False
        if not live_orders_idle or not _inside_after_hours_window(now, self.settings):
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            if self._last_attempt_at is not None:
                next_allowed = self._last_attempt_at + timedelta(hours=self.settings.retrain_interval_hours)
                if now < next_allowed:
                    return False
            self._last_attempt_at = now
            self._thread = threading.Thread(target=self._run, name="safe-model-retraining", daemon=True)
            self._thread.start()
            return True

    def consume_completion(self) -> dict[str, Any] | None:
        with self._lock:
            result = self._pending_completion
            self._pending_completion = None
            return result

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "in_progress": self._thread is not None and self._thread.is_alive(),
                "last_attempt_at": self._last_attempt_at.isoformat() if self._last_attempt_at else None,
                "last_completed_at": self._last_completed_at.isoformat() if self._last_completed_at else None,
                "last_result": self._last_result,
                "last_error": self._last_error,
            }

    def _run(self) -> None:
        db = Database(settings=self.settings)
        try:
            db.init_db()
            if db.fetch_active_execution_episodes(self.settings.bot_symbol):
                self._record_result({"status": "skipped", "reason": "open execution episodes exist"})
                return
            clean_since = utc_now() - timedelta(days=self.settings.retrain_lookback_days)
            clean_row = db.conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM trade_outcomes o
                LEFT JOIN execution_episodes e ON e.episode_id = o.root_episode_id
                WHERE o.exit_time >= ?
                  AND COALESCE(o.exit_reason, '') NOT IN (
                    'unprotected_residual_position', 'startup_residual_position',
                    'safety_flatten', 'process_shutdown', 'session_close'
                  )
                  AND COALESCE(e.close_reason, '') NOT IN (
                    'unprotected_residual_position', 'startup_residual_position',
                    'safety_flatten', 'process_shutdown', 'session_close'
                  )
                """,
                (clean_since.isoformat(),),
            ).fetchone()
            clean_count = int(clean_row["count"] or 0)
            if clean_count < self.settings.retrain_min_clean_episodes:
                result = {
                    "status": "skipped",
                    "reason": "not enough clean completed execution episodes",
                    "clean_episodes": clean_count,
                    "minimum": self.settings.retrain_min_clean_episodes,
                }
                db.log_event("INFO", __name__, "model_retraining_skipped", result["reason"], result)
                self._record_result(result)
                return
            samples, labels, _ = build_training_dataset(db, self.settings.retrain_lookback_days)
            if len(samples) < self.settings.retrain_min_samples:
                result = {
                    "status": "skipped",
                    "reason": "not enough labeled samples",
                    "samples": len(samples),
                    "minimum": self.settings.retrain_min_samples,
                }
                db.log_event("INFO", __name__, "model_retraining_skipped", result["reason"], result)
                self._record_result(result)
                return
            labels_ok, label_reason = label_quality(labels)
            if not labels_ok:
                result = {
                    "status": "skipped",
                    "reason": label_reason,
                    "samples": len(samples),
                    "classes": sorted(set(labels)),
                }
                db.log_event("INFO", __name__, "model_retraining_skipped", result["reason"], result)
                self._record_result(result)
                return
            result = train_candidate_model(db, self.settings, lookback_days=self.settings.retrain_lookback_days)
            result["status"] = "completed"
            db.log_event("INFO", __name__, "model_retraining_completed", "Scheduled model retraining completed", result)
            self._record_result(result)
        except Exception as exc:
            result = {"status": "failed", "reason": str(exc)}
            try:
                db.log_event("ERROR", __name__, "model_retraining_failed", str(exc), {})
            except Exception:
                logger.exception("scheduled retraining failed")
            with self._lock:
                self._last_error = str(exc)
                self._last_result = result
                self._pending_completion = result
        finally:
            db.close()

    def _record_result(self, result: dict[str, Any]) -> None:
        with self._lock:
            self._last_completed_at = utc_now()
            self._last_error = None if result.get("status") != "failed" else result.get("reason")
            self._last_result = result
            self._pending_completion = result


def _inside_after_hours_window(now: datetime, settings: Settings) -> bool:
    hour = now.astimezone(ZoneInfo("America/New_York")).hour
    start = settings.retrain_after_hour_et
    end = settings.retrain_before_hour_et
    if start == end:
        return True
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end
