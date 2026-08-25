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
        bounded_user = compact_json_text(user, max_chars=18_000)
        payload = self._payload(system=system, user=bounded_user, num_predict=640)
        try:
            raw = _post_json(
                f"{self.base_url}/api/chat",
                payload,
                timeout=self.settings.llm_timeout_seconds,
                provider="Ollama",
            )
        except LLMError as exc:
            if "timed out" not in str(exc).lower():
                raise
            # The 1B model runs on CPU on the target laptop. A smaller retry is
            # preferable to failing the whole offline research workflow.
            payload = self._payload(
                system=system,
                user=compact_json_text(bounded_user, max_chars=8_000),
                num_predict=320,
            )
            raw = _post_json(
                f"{self.base_url}/api/chat",
                payload,
                timeout=self.settings.llm_timeout_seconds,
                provider="Ollama retry",
            )
            raw["dashboard_retry"] = "reduced_context"
        content = str(raw.get("message", {}).get("content") or "")
        if not content:
            raise LLMError("Ollama returned an empty response.")
        return LLMResponse(provider=self.provider, model=self.model, content=content, raw=raw)

    def _payload(self, *, system: str, user: str, num_predict: int) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": "json",
            "stream": False,
            "keep_alive": "15m",
            "options": {
                "temperature": self.settings.llm_temperature,
                "num_ctx": 4_096,
                "num_predict": num_predict,
            },
        }


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


def make_ollama_fallback_client(settings: Settings) -> OllamaClient:
    """Build the local advisory fallback without changing persisted settings."""
    from dataclasses import replace

    return OllamaClient(
        replace(
            settings,
            llm_provider="ollama",
            llm_base_url="http://127.0.0.1:11434",
            llm_model="llama3.2:1b",
        )
    )


def recoverable_provider_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "http 429",
            "quota",
            "balance",
            "token budget",
            "timed out",
            "could not reach",
            "unreachable",
        )
    )


def compact_json_text(text: str, *, max_chars: int) -> str:
    """Bound an LLM JSON prompt while preserving a valid JSON document."""
    if len(text) <= max_chars:
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[: max(1, max_chars - 20)] + "...[truncated]"
    compacted = _compact_value(payload, string_limit=900, list_limit=10)
    encoded = json.dumps(compacted, sort_keys=True, default=str)
    while len(encoded) > max_chars:
        compacted = _compact_value(compacted, string_limit=400, list_limit=5)
        encoded = json.dumps(compacted, sort_keys=True, default=str)
        if len(encoded) <= max_chars:
            break
        compacted = _compact_value(compacted, string_limit=180, list_limit=2)
        encoded = json.dumps(compacted, sort_keys=True, default=str)
        break
    if len(encoded) > max_chars:
        # Re-encoding an arbitrary JSON prefix as a string can grow beyond the
        # requested bound because quotes and backslashes must be escaped.
        # Keep only a small, valid summary when the structured reductions above
        # are still too large.
        return json.dumps(
            {
                "_notice": "Evidence exceeded the local model context and was summarized.",
                "_original_characters": len(text),
            },
            sort_keys=True,
        )[:max_chars]
    return encoded


def _compact_value(value: Any, *, string_limit: int, list_limit: int) -> Any:
    if isinstance(value, dict):
        item_limit = max(8, list_limit * 4)
        items = list(value.items())
        result = {
            str(key): _compact_value(item, string_limit=string_limit, list_limit=list_limit)
            for key, item in items[:item_limit]
        }
        if len(items) > item_limit:
            result["_omitted_fields"] = len(items) - item_limit
        return result
    if isinstance(value, list):
        selected = value[:list_limit]
        result = [_compact_value(item, string_limit=string_limit, list_limit=list_limit) for item in selected]
        if len(value) > list_limit:
            result.append({"_omitted_items": len(value) - list_limit})
        return result
    if isinstance(value, str) and len(value) > string_limit:
        return value[:string_limit] + "...[truncated]"
    return value


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
        if exc.code == 429:
            detail = "quota or account balance unavailable; use local Ollama or restore provider quota"
        suffix = f": {detail}" if detail else ""
        raise LLMError(f"{provider} API returned HTTP {exc.code}{suffix}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        if isinstance(exc, TimeoutError):
            raise LLMError(f"{provider} request timed out after {timeout} seconds") from exc
        raise LLMError(f"Could not reach {provider} at {url}: {exc}") from exc
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise LLMError(f"{provider} returned invalid response JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMError(f"{provider} response must be a JSON object.")
    return parsed
