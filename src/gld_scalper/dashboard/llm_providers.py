from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..llm_provider import LLMError, _post_json
from .settings_store import EnvFileStore


PROVIDER_DEFAULTS = {
    "ollama": {
        "label": "Ollama Local",
        "base_url": "http://127.0.0.1:11434",
        "model": "llama3.2:1b",
        "requires_key": False,
        "description": "Private local inference for journals, labels, coaching, and research.",
    },
    "kimi": {
        "label": "Kimi Hosted",
        "base_url": "https://api.moonshot.ai/v1",
        "model": "kimi-k2.6",
        "requires_key": True,
        "description": "Hosted financial research under the configured Tier0 request budget.",
    },
}


class LLMProviderService:
    """Manage offline research engines without giving an LLM broker authority."""

    def __init__(self, project_root: Path, env_store: EnvFileStore) -> None:
        self.project_root = project_root
        self.env_store = env_store
        self._runtime_lock = threading.Lock()
        self._runtime_tests: dict[str, dict[str, Any]] = {}

    def catalog(self) -> dict[str, Any]:
        values = self.env_store.read()
        active = values.get("LLM_PROVIDER", "none").lower()
        providers = []
        for key, profile in PROVIDER_DEFAULTS.items():
            configured = key == "ollama" or bool(values.get("MOONSHOT_API_KEY"))
            with self._runtime_lock:
                runtime = dict(self._runtime_tests.get(key) or {})
            providers.append(
                {
                    "id": key,
                    **profile,
                    "active": active == key,
                    "configured": configured,
                    "current_base_url": values.get("LLM_BASE_URL", profile["base_url"]) if active == key else profile["base_url"],
                    "current_model": values.get("LLM_MODEL", profile["model"]) if active == key else profile["model"],
                    "runtime": runtime or {
                        "state": "not_tested" if configured else "not_configured",
                        "message": "Run a generation test to verify this provider." if configured else "Configuration is incomplete.",
                    },
                }
            )
        return {
            "active_provider": active,
            "providers": providers,
            "fingpt": self._fingpt_status(values),
            "safety": {
                "offline_only": values.get("LLM_OFFLINE_ONLY", "true").lower() == "true",
                "live_broker_authority": False,
                "promotion_requires_validation": True,
            },
        }

    def activate(self, provider: str, *, base_url: str = "", model: str = "", api_key: str = "") -> dict[str, Any]:
        provider = provider.strip().lower()
        if provider not in PROVIDER_DEFAULTS:
            raise ValueError("Provider must be ollama or kimi")
        defaults = PROVIDER_DEFAULTS[provider]
        resolved_url = (base_url or str(defaults["base_url"])).rstrip("/")
        resolved_model = model.strip() or str(defaults["model"])
        if provider == "kimi" and resolved_url != "https://api.moonshot.ai/v1":
            raise ValueError("Kimi must use https://api.moonshot.ai/v1")
        if provider == "kimi" and not api_key.strip() and not self.env_store.read().get("MOONSHOT_API_KEY"):
            raise ValueError("A Kimi API key is required before Kimi can be activated")
        changes = {
            "LLM_PROVIDER": provider,
            "LLM_BASE_URL": resolved_url,
            "LLM_MODEL": resolved_model,
            "MOONSHOT_API_KEY": api_key,
            "ENABLE_LLM_ANALYSIS": "true",
            "ENABLE_LLM_MACRO_CONTEXT": "true",
            "ENABLE_LLM_REVIEW_COACH": "true",
            "ENABLE_LLM_TRAINING_ADVICE": "true",
            "ENABLE_LLM_TRAINING_LABELS": "true",
            "LLM_OFFLINE_ONLY": "true",
            "ENABLE_LLM_LIVE_TRADING": "false",
        }
        self.env_store.update(changes)
        with self._runtime_lock:
            self._runtime_tests[provider] = {
                "state": "not_tested",
                "message": "Settings saved. Run a generation test to verify the provider.",
                "checked_at": None,
            }
        return self.catalog()

    def test(self, provider: str | None = None) -> dict[str, Any]:
        values = self.env_store.read()
        selected = (provider or values.get("LLM_PROVIDER", "none")).strip().lower()
        if selected not in PROVIDER_DEFAULTS:
            raise ValueError("Select Ollama or Kimi before testing")
        defaults = PROVIDER_DEFAULTS[selected]
        active = values.get("LLM_PROVIDER", "none").lower() == selected
        base_url = (values.get("LLM_BASE_URL") if active else defaults["base_url"]) or defaults["base_url"]
        model = (values.get("LLM_MODEL") if active else defaults["model"]) or defaults["model"]
        started = time.perf_counter()
        self._record_runtime(selected, state="testing", message="A generation test is running.")
        try:
            result = self._test_provider(selected, str(base_url), str(model), values, started)
        except Exception as exc:
            self._record_runtime(selected, state="failed", message=str(exc)[:300])
            raise
        self._record_runtime(
            selected,
            state="healthy" if result["ok"] else "failed",
            message=str(result["message"]),
            latency_ms=result["latency_ms"],
            model=str(model),
        )
        return result

    def _test_provider(
        self,
        selected: str,
        base_url: str,
        model: str,
        values: dict[str, str],
        started: float,
    ) -> dict[str, Any]:
        if selected == "ollama":
            payload = self._get_json(f"{base_url.rstrip('/')}/api/tags", timeout=8)
            names = [str(item.get("name") or "") for item in payload.get("models", []) if isinstance(item, dict)]
            installed = any(name == model or name.startswith(f"{model}:") for name in names)
            if not installed:
                available = False
                message = "Ollama is reachable, but the selected model is not installed."
                detail = {"installed_models": names, "model_available": False, "generation_tested": False}
            else:
                response = _post_json(
                    f"{base_url.rstrip('/')}/api/chat",
                    {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "Return strict JSON only."},
                            {"role": "user", "content": 'Return {"status":"ok"}.'},
                        ],
                        "format": "json",
                        "stream": False,
                        "keep_alive": "15m",
                        "options": {"temperature": 0, "num_ctx": 1_024, "num_predict": 32},
                    },
                    timeout=90,
                    provider="Ollama health check",
                )
                content = str(response.get("message", {}).get("content") or "")
                available = bool(content)
                message = "Ollama generated a valid research response." if available else "Ollama answered but returned empty content."
                detail = {"installed_models": names, "model_available": True, "generation_tested": True}
        else:
            key = values.get("MOONSHOT_API_KEY", "")
            if not key:
                raise RuntimeError("Kimi API key is not configured")
            try:
                payload = _post_json(
                    f"{base_url.rstrip('/')}/chat/completions",
                    {
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "Return strict JSON only."},
                            {"role": "user", "content": 'Return {"status":"ok"}.'},
                        ],
                        "response_format": {"type": "json_object"},
                        "stream": False,
                        "temperature": 0,
                        "max_completion_tokens": 32,
                    },
                    timeout=30,
                    headers={"Authorization": f"Bearer {key}"},
                    provider="Kimi health check",
                )
            except LLMError as exc:
                raise RuntimeError(str(exc)) from exc
            choices = payload.get("choices")
            content = ""
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message_payload = choices[0].get("message")
                if isinstance(message_payload, dict):
                    content = str(message_payload.get("content") or "")
            available = bool(content)
            message = "Kimi authenticated and generated a research response." if available else "Kimi authenticated but returned no completion content."
            detail = {"model_available": available, "generation_tested": True}
        return {
            "provider": selected,
            "model": model,
            "ok": bool(available),
            "message": message,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            **detail,
        }

    def _record_runtime(self, provider: str, *, state: str, message: str, **details: Any) -> None:
        record = {
            "state": state,
            "message": message,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            **details,
        }
        with self._runtime_lock:
            self._runtime_tests[provider] = record

    def _fingpt_status(self, values: dict[str, str]) -> dict[str, Any]:
        configured = Path(values.get("FINGPT_SOURCE_DIR", "FINGPT/FinGPT-1.0.0/fingpt"))
        root = configured if configured.is_absolute() else self.project_root / configured
        markers = (
            root / "FinGPT_Forecaster",
            root / "FinGPT_RAG",
            root / "FinGPT_Benchmark",
        )
        found = [path.name for path in markers if path.exists()]
        return {
            "available": bool(found),
            "root": str(root),
            "modules": found,
            "role": "financial prompts, RAG, sentiment, and data-preparation framework",
            "reasoning_engine": values.get("LLM_PROVIDER", "none"),
        }

    @staticmethod
    def _get_json(url: str, *, timeout: int, headers: dict[str, str] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(url, headers=headers or {}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"Provider returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Provider is unreachable at {url}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Provider health response was not a JSON object")
        return payload
