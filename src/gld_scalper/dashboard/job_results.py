from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class JobResultRepository:
    """Read structured results emitted by allowlisted dashboard jobs."""

    def __init__(self, log_root: Path) -> None:
        self.log_root = log_root
        self._cache: dict[str, tuple[int, int, dict[str, Any] | None]] = {}
        self._lock = threading.Lock()

    def latest(self, name: str) -> dict[str, Any] | None:
        path = self.log_root / f"{name}.log"
        if not path.exists():
            return None
        stat = path.stat()
        with self._lock:
            cached = self._cache.get(name)
            if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
                return cached[2]
        maximum_bytes = 4 * 1024 * 1024
        with path.open("rb") as handle:
            if stat.st_size > maximum_bytes:
                handle.seek(-maximum_bytes, 2)
                handle.readline()
            text = handle.read().decode("utf-8", errors="replace")
        decoder = json.JSONDecoder()
        result: dict[str, Any] | None = None
        index = 0
        while index < len(text):
            index = text.find("{", index)
            if index < 0:
                break
            try:
                value, end = decoder.raw_decode(text, index)
            except json.JSONDecodeError:
                index += 1
                continue
            if isinstance(value, dict):
                result = value
            index = end
        with self._lock:
            self._cache[name] = (stat.st_mtime_ns, stat.st_size, result)
        return result
