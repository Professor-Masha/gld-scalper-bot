from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JobResultRepository:
    """Read structured results emitted by allowlisted dashboard jobs."""

    def __init__(self, log_root: Path) -> None:
        self.log_root = log_root

    def latest(self, name: str) -> dict[str, Any] | None:
        path = self.log_root / f"{name}.log"
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
        decoder = json.JSONDecoder()
        result: dict[str, Any] | None = None
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                result = value
        return result
