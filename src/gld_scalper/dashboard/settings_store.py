from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Mapping


ALLOWED_SETTINGS = {
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_ENDPOINT", "ALPACA_DATA_FEED",
    "LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "MOONSHOT_API_KEY",
    "ENABLE_LLM_ANALYSIS", "ENABLE_LLM_MACRO_CONTEXT", "ENABLE_LLM_REVIEW_COACH",
    "ENABLE_LLM_TRAINING_ADVICE", "ENABLE_LLM_TRAINING_LABELS",
    "LLM_OFFLINE_ONLY", "ENABLE_LLM_LIVE_TRADING",
}
SECRET_SETTINGS = {"ALPACA_API_KEY", "ALPACA_SECRET_KEY", "MOONSHOT_API_KEY"}
_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class EnvFileStore:
    """Update the local .env without ever returning stored secret values."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def public_settings(self) -> dict[str, str | bool]:
        values = self.read()
        return {
            "alpaca_api_key_configured": bool(values.get("ALPACA_API_KEY")),
            "alpaca_secret_key_configured": bool(values.get("ALPACA_SECRET_KEY")),
            "alpaca_api_key_hint": _mask(values.get("ALPACA_API_KEY", "")),
            "alpaca_endpoint": values.get("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets/v2"),
            "alpaca_data_feed": values.get("ALPACA_DATA_FEED", "iex"),
            "alpaca_paper": True,
            "llm_provider": values.get("LLM_PROVIDER", "ollama"),
            "llm_base_url": values.get("LLM_BASE_URL", "http://127.0.0.1:11434"),
            "llm_model": values.get("LLM_MODEL", "llama3.2:1b"),
            "kimi_api_key_configured": bool(values.get("MOONSHOT_API_KEY")),
            "fingpt_pipeline_enabled": values.get("ENABLE_LLM_ANALYSIS", "false").lower() == "true",
        }

    def read(self) -> dict[str, str]:
        values: dict[str, str] = {}
        if not self.path.exists():
            return values
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
        return values

    def child_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.update(self.read())
        environment.update({"ALPACA_PAPER": "true", "ALPACA_PAPER_TRADE": "true", "BOT_DATA_MODE": "paper"})
        return environment

    def update(self, changes: Mapping[str, str | None]) -> None:
        clean: dict[str, str] = {}
        for key, raw_value in changes.items():
            if key not in ALLOWED_SETTINGS or not _KEY_PATTERN.fullmatch(key):
                raise ValueError(f"Dashboard cannot change setting: {key}")
            value = str(raw_value or "").strip()
            if not value and key in SECRET_SETTINGS:
                continue
            if "\n" in value or "\r" in value:
                raise ValueError(f"Invalid newline in setting: {key}")
            clean[key] = value
        clean.update({"ALPACA_PAPER": "true", "ALPACA_PAPER_TRADE": "true", "BOT_DATA_MODE": "paper"})
        lines = self.path.read_text(encoding="utf-8").splitlines() if self.path.exists() else []
        remaining = dict(clean)
        output: list[str] = []
        for line in lines:
            stripped = line.strip()
            key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
            output.append(f"{key}={_encode(remaining.pop(key))}" if key in remaining else line)
        if remaining:
            if output and output[-1].strip():
                output.append("")
            output.append("# Dashboard-managed local settings")
            output.extend(f"{key}={_encode(value)}" for key, value in remaining.items())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("\n".join(output).rstrip() + "\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _mask(value: str) -> str:
    if not value:
        return ""
    return "configured" if len(value) <= 8 else f"{value[:4]}...{value[-4:]}"


def _encode(value: str) -> str:
    if not value or any(char.isspace() for char in value) or "#" in value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value
