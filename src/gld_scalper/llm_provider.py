from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import Settings


class LLMError(RuntimeError):
    pass


@dataclass(slots=True)
class LLMResponse:
    provider: str
    model: str
    content: str
    raw: dict[str, Any]

    def json_content(self) -> dict[str, Any]:
        try:
            parsed = json.loads(self.content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"LLM returned invalid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise LLMError("LLM JSON response must be an object.")
        return parsed


class OllamaClient:
    provider = "ollama"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model

    def chat_json(self, *, system: str, user: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": "json",
            "stream": False,
            "options": {"temperature": self.settings.llm_temperature},
        }
        raw = _post_json(
            f"{self.base_url}/api/chat",
            payload,
            timeout=self.settings.llm_timeout_seconds,
        )
        content = str(raw.get("message", {}).get("content") or "")
        if not content:
            raise LLMError("Ollama returned an empty response.")
        return LLMResponse(provider=self.provider, model=self.model, content=content, raw=raw)


class DisabledLLMClient:
    provider = "none"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = "none"

    def chat_json(self, *, system: str, user: str) -> LLMResponse:
        raise LLMError("LLM provider is disabled. Set LLM_PROVIDER=ollama to enable local Ollama.")


def make_llm_client(settings: Settings):
    provider = settings.llm_provider.lower()
    if provider == "ollama":
        return OllamaClient(settings)
    return DisabledLLMClient(settings)


def _post_json(url: str, payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise LLMError(f"Could not reach Ollama at {url}: {exc}") from exc
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Ollama returned invalid response JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError("Ollama response must be a JSON object.")
    return parsed
