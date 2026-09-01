from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


GENESIS_HASH = "0" * 64
SECRET_MARKERS = ("secret", "password", "token", "api_key", "apikey", "credential")


class AuditLedger:
    """Append-only, hash-chained control-plane audit log.

    The ledger records dashboard commands and configuration changes. It is not a
    replacement for broker/order reconciliation or the trading journal.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def append(
        self,
        *,
        event_type: str,
        actor_id: str,
        status: str,
        correlation_id: str,
        command_id: str | None = None,
        action: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            previous = self._last_record()
            record = {
                "sequence": int(previous.get("sequence", 0)) + 1 if previous else 1,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                "actor_id": actor_id,
                "status": status,
                "correlation_id": correlation_id,
                "command_id": command_id,
                "action": action,
                "details": _redact(details or {}),
                "previous_hash": previous.get("record_hash", GENESIS_HASH) if previous else GENESIS_HASH,
            }
            record["record_hash"] = _hash_record(record)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":"), default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return record

    def tail(self, limit: int = 100) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 1000))
        with self._lock:
            records = self._records()
        return list(reversed(records[-bounded:]))

    def find_idempotency_result(self, key: str) -> dict[str, Any] | None:
        if not key:
            return None
        with self._lock:
            for record in reversed(self._records()):
                details = record.get("details") or {}
                if details.get("idempotency_key") == key and details.get("command_result"):
                    return dict(details["command_result"])
        return None

    def verify(self) -> dict[str, Any]:
        with self._lock:
            records = self._records()
        previous_hash = GENESIS_HASH
        for index, record in enumerate(records, start=1):
            stored_hash = str(record.get("record_hash") or "")
            candidate = dict(record)
            candidate.pop("record_hash", None)
            if record.get("sequence") != index:
                return {"valid": False, "records": len(records), "failure_sequence": index, "reason": "sequence gap"}
            if record.get("previous_hash") != previous_hash:
                return {"valid": False, "records": len(records), "failure_sequence": index, "reason": "broken hash link"}
            if _hash_record(candidate) != stored_hash:
                return {"valid": False, "records": len(records), "failure_sequence": index, "reason": "record hash mismatch"}
            previous_hash = stored_hash
        return {"valid": True, "records": len(records), "head_hash": previous_hash}

    def _last_record(self) -> dict[str, Any] | None:
        records = self._records()
        return records[-1] if records else None

    def _records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    records.append(value)
        return records


def _hash_record(record: dict[str, Any]) -> str:
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _redact(value: Any, key: str = "") -> Any:
    if any(marker in key.lower() for marker in SECRET_MARKERS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _redact(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value
