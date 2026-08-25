import json
import time

import pytest

from gld_scalper.config import Settings
from gld_scalper.kimi_tier0 import KimiBudgetError, KimiTier0Ledger, kimi_tier0_status
from gld_scalper.llm_analysis import require_offline_llm_enabled
from gld_scalper.llm_provider import (
    KimiClient,
    LLMError,
    OllamaClient,
    compact_json_text,
    make_llm_client,
)


def _settings(tmp_path, **overrides):
    values = {
        "llm_provider": "kimi",
        "llm_base_url": "https://api.moonshot.ai/v1",
        "llm_model": "kimi-k2.6",
        "llm_api_key": "test-secret-key",
        "llm_offline_only": True,
        "enable_llm_live_trading": False,
        "kimi_usage_state_path": str(tmp_path / "kimi_usage.json"),
        "kimi_max_completion_tokens": 128,
        "kimi_tier0_rpm_limit": 10,
        "kimi_tier0_tpm_limit": 10_000,
        "kimi_tier0_tpd_limit": 20_000,
    }
    values.update(overrides)
    return Settings(**values)


def test_kimi_client_uses_openai_compatible_json_request_and_reconciles_usage(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    captured = {}

    def fake_post(url, payload, *, timeout, headers, provider):
        captured.update(
            {
                "url": url,
                "payload": payload,
                "timeout": timeout,
                "headers": headers,
                "provider": provider,
            }
        )
        return {
            "choices": [{"message": {"content": '{"summary":"bounded"}'}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
        }

    monkeypatch.setattr("gld_scalper.llm_provider._post_json", fake_post)

    response = KimiClient(settings).chat_json(system="Return JSON.", user="Analyze this evidence.")

    assert response.json_content() == {"summary": "bounded"}
    assert captured["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["max_completion_tokens"] == 128
    assert captured["headers"] == {"Authorization": "Bearer test-secret-key"}
    state = json.loads((tmp_path / "kimi_usage.json").read_text(encoding="utf-8"))
    assert state["records"][0]["tokens"] == 12


def test_kimi_budget_refuses_rolling_daily_overage(tmp_path):
    settings = _settings(tmp_path, kimi_tier0_tpd_limit=100)
    state_path = tmp_path / "kimi_usage.json"
    state_path.write_text(
        json.dumps({"records": [{"id": "existing", "timestamp": time.time(), "tokens": 90}]}),
        encoding="utf-8",
    )

    with KimiTier0Ledger(settings) as ledger, pytest.raises(KimiBudgetError, match="daily token budget"):
        ledger.reserve(20)


def test_kimi_settings_enforce_official_endpoint_tier0_ceilings_and_secret_redaction(tmp_path):
    settings = _settings(tmp_path)

    settings.validate_safety()

    assert "test-secret-key" not in repr(settings)
    with pytest.raises(RuntimeError, match="Tier0 ceiling of 20"):
        _settings(tmp_path, kimi_tier0_rpm_limit=21).validate_safety()
    with pytest.raises(RuntimeError, match="official"):
        _settings(tmp_path, llm_base_url="https://example.com/v1").validate_safety()


def test_offline_llm_gate_accepts_kimi_and_factory_selects_client(tmp_path):
    settings = _settings(tmp_path)

    require_offline_llm_enabled(settings)

    assert isinstance(make_llm_client(settings), KimiClient)
    with pytest.raises(Exception, match="LLM_OFFLINE_ONLY"):
        require_offline_llm_enabled(_settings(tmp_path, llm_offline_only=False))


def test_kimi_status_is_redacted_and_reports_remaining_budget(tmp_path):
    settings = _settings(tmp_path)

    result = kimi_tier0_status(settings)

    assert result["api_key_configured"] is True
    assert "test-secret-key" not in json.dumps(result)
    assert result["configured_limits"]["concurrency"] == 1
    assert result["usage"]["remaining_tokens_last_24_hours"] == 20_000


def test_compact_json_text_remains_valid_and_bounded():
    source = json.dumps({"rows": [{"text": "x" * 4_000, "value": index} for index in range(80)]})

    compacted = compact_json_text(source, max_chars=2_000)

    assert len(compacted) <= 2_000
    assert isinstance(json.loads(compacted), dict)


def test_ollama_retries_with_smaller_response_after_timeout(tmp_path, monkeypatch):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ollama.db'}",
        llm_provider="ollama",
        llm_base_url="http://127.0.0.1:11434",
        llm_model="llama3.2:1b",
    )
    payloads = []

    def fake_post(url, payload, **kwargs):
        payloads.append(payload)
        if len(payloads) == 1:
            raise LLMError("Ollama request timed out after 240 seconds")
        return {"message": {"content": '{"summary":"recovered"}'}}

    monkeypatch.setattr("gld_scalper.llm_provider._post_json", fake_post)

    response = OllamaClient(settings).chat_json(system="Return JSON.", user=json.dumps({"evidence": "x" * 30_000}))

    assert response.json_content()["summary"] == "recovered"
    assert payloads[0]["keep_alive"] == "15m"
    assert payloads[0]["options"]["num_predict"] == 640
    assert payloads[1]["options"]["num_predict"] == 320
    assert payloads[1]["options"]["num_ctx"] == 4_096
