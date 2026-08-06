from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .kimi_tier0 import KimiTier0Ledger, estimate_kimi_tokens, response_usage_tokens


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
            provider="Ollama",
        )
        content = str(raw.get("message", {}).get("content") or "")
        if not content:
            raise LLMError("Ollama returned an empty response.")
        return LLMResponse(provider=self.provider, model=self.model, content=content, raw=raw)


class KimiClient:
    provider = "kimi"

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
            "response_format": {"type": "json_object"},
            "stream": False,
            "temperature": self.settings.llm_temperature,
            "max_completion_tokens": self.settings.kimi_max_completion_tokens,
        }
        estimate = estimate_kimi_tokens(system, user, self.settings.kimi_max_completion_tokens)
        with KimiTier0Ledger(self.settings) as ledger:
            reservation = ledger.reserve(estimate)
            raw = _post_json(
                f"{self.base_url}/chat/completions",
                payload,
                timeout=self.settings.llm_timeout_seconds,
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                provider="Kimi",
            )
            reservation.reconcile(response_usage_tokens(raw))
        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMError("Kimi returned no completion choices.")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = str(message.get("content") or "") if isinstance(message, dict) else ""
        if not content:
            raise LLMError("Kimi returned an empty response.")
        return LLMResponse(provider=self.provider, model=self.model, content=content, raw=raw)


class DisabledLLMClient:
    provider = "none"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = "none"

    def chat_json(self, *, system: str, user: str) -> LLMResponse:
        raise LLMError("LLM provider is disabled. Set LLM_PROVIDER=ollama or kimi for offline analysis.")


def make_llm_client(settings: Settings):
    provider = settings.llm_provider.lower()
    if provider == "ollama":
        return OllamaClient(settings)
    if provider == "kimi":
        return KimiClient(settings)
    return DisabledLLMClient(settings)


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: int,
    headers: dict[str, str] | None = None,
    provider: str = "LLM",
) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(
        url,
        data=body,
        headers=request_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8")[:500]
        except Exception:
            detail = ""
        suffix = f": {detail}" if detail else ""
        raise LLMError(f"{provider} API returned HTTP {exc.code}{suffix}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Could not reach {provider} at {url}: {exc}") from exc
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise LLMError(f"{provider} returned invalid response JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError(f"{provider} response must be a JSON object.")
    return parsed
