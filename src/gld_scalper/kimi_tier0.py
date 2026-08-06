from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, Settings


class KimiBudgetError(RuntimeError):
    pass


@dataclass(slots=True)
class KimiBudgetReservation:
    ledger: "KimiTier0Ledger"
    record_id: str
    estimated_tokens: int
    reconciled: bool = False

    def reconcile(self, actual_tokens: int | None) -> None:
        if self.reconciled or actual_tokens is None or actual_tokens <= 0:
            return
        self.ledger._replace_tokens(self.record_id, actual_tokens)
        self.reconciled = True


class KimiTier0Ledger:
    """Serialize Kimi calls and reserve a conservative rolling Tier0 budget."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = _project_path(settings.kimi_usage_state_path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock_acquired = False

    def __enter__(self) -> "KimiTier0Ledger":
        self._acquire_lock()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self._release_lock()
        return False

    def reserve(self, estimated_tokens: int) -> KimiBudgetReservation:
        estimated_tokens = max(1, int(estimated_tokens))
        if estimated_tokens > self.settings.kimi_tier0_tpm_limit:
            raise KimiBudgetError(
                "Estimated request tokens exceed the configured Kimi Tier0 per-minute budget. "
                "Reduce LLM_MAX_CONTEXT_ROWS or KIMI_MAX_COMPLETION_TOKENS."
            )
        while True:
            now = time.time()
            state = self._load_state(now)
            records = state["records"]
            day_tokens = sum(int(item["tokens"]) for item in records)
            if day_tokens + estimated_tokens > self.settings.kimi_tier0_tpd_limit:
                raise KimiBudgetError(
                    "Configured Kimi Tier0 daily token budget is exhausted. "
                    "Wait for the rolling 24-hour window to clear or use Ollama."
                )
            minute_records = [item for item in records if now - float(item["timestamp"]) < 60.0]
            minute_tokens = sum(int(item["tokens"]) for item in minute_records)
            request_blocked = len(minute_records) >= self.settings.kimi_tier0_rpm_limit
            token_blocked = minute_tokens + estimated_tokens > self.settings.kimi_tier0_tpm_limit
            if not request_blocked and not token_blocked:
                record_id = uuid.uuid4().hex
                records.append({"id": record_id, "timestamp": now, "tokens": estimated_tokens})
                self._save_state(state)
                return KimiBudgetReservation(self, record_id, estimated_tokens)
            oldest = min(float(item["timestamp"]) for item in minute_records)
            time.sleep(max(0.05, min(60.0, 60.05 - (now - oldest))))

    def snapshot(self) -> dict[str, int]:
        now = time.time()
        records = self._load_state(now)["records"]
        minute_records = [item for item in records if now - float(item["timestamp"]) < 60.0]
        minute_tokens = sum(int(item["tokens"]) for item in minute_records)
        day_tokens = sum(int(item["tokens"]) for item in records)
        return {
            "requests_last_minute": len(minute_records),
            "tokens_last_minute": minute_tokens,
            "tokens_last_24_hours": day_tokens,
            "remaining_requests_this_minute": max(
                0,
                self.settings.kimi_tier0_rpm_limit - len(minute_records),
            ),
            "remaining_tokens_this_minute": max(
                0,
                self.settings.kimi_tier0_tpm_limit - minute_tokens,
            ),
            "remaining_tokens_last_24_hours": max(
                0,
                self.settings.kimi_tier0_tpd_limit - day_tokens,
            ),
        }

    def _replace_tokens(self, record_id: str, actual_tokens: int) -> None:
        state = self._load_state(time.time())
        for item in state["records"]:
            if item.get("id") == record_id:
                item["tokens"] = max(1, int(actual_tokens))
                break
        self._save_state(state)

    def _load_state(self, now: float) -> dict[str, list[dict[str, Any]]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            raw = {"records": []}
        records = raw.get("records") if isinstance(raw, dict) else []
        if not isinstance(records, list):
            records = []
        valid = []
        for item in records:
            try:
                timestamp = float(item["timestamp"])
                tokens = max(1, int(item["tokens"]))
                record_id = str(item["id"])
            except (KeyError, TypeError, ValueError):
                continue
            if now - timestamp < 86_400.0:
                valid.append({"id": record_id, "timestamp": timestamp, "tokens": tokens})
        return {"records": valid}

    def _save_state(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)

    def _acquire_lock(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.settings.llm_timeout_seconds
        stale_after = max(600.0, self.settings.llm_timeout_seconds * 2.0 + 60.0)
        while True:
            try:
                descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = time.time() - self.lock_path.stat().st_mtime
                    if age > stale_after:
                        self.lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise KimiBudgetError("Timed out waiting for the serialized Kimi Tier0 request lock.")
                time.sleep(0.25)
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "created_at": time.time()}, handle)
            self._lock_acquired = True
            return

    def _release_lock(self) -> None:
        if self._lock_acquired:
            self.lock_path.unlink(missing_ok=True)
            self._lock_acquired = False


def estimate_kimi_tokens(system: str, user: str, max_completion_tokens: int) -> int:
    # Three characters per token is deliberately conservative for mixed prose,
    # JSON, code, and financial symbols.
    input_tokens = max(1, (len(system) + len(user) + 2) // 3)
    return input_tokens + max(1, int(max_completion_tokens))


def response_usage_tokens(raw: dict[str, Any]) -> int | None:
    usage = raw.get("usage")
    if not isinstance(usage, dict):
        return None
    try:
        return int(usage.get("total_tokens") or 0) or None
    except (TypeError, ValueError):
        return None


def kimi_tier0_status(settings: Settings) -> dict[str, Any]:
    with KimiTier0Ledger(settings) as ledger:
        usage = ledger.snapshot()
    return {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "base_url": settings.llm_base_url,
        "api_key_configured": bool(settings.llm_api_key),
        "offline_only": settings.llm_offline_only,
        "live_trading_enabled": settings.enable_llm_live_trading,
        "configured_limits": {
            "concurrency": 1,
            "requests_per_minute": settings.kimi_tier0_rpm_limit,
            "tokens_per_minute": settings.kimi_tier0_tpm_limit,
            "tokens_per_24_hours": settings.kimi_tier0_tpd_limit,
            "max_completion_tokens": settings.kimi_max_completion_tokens,
        },
        "usage": usage,
        "state_path": str(_project_path(settings.kimi_usage_state_path)),
    }


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path
