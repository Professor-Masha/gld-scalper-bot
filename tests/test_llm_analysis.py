import json
from datetime import datetime, timezone

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.llm_analysis import LLMAnalysisService
from gld_scalper.llm_provider import LLMError, LLMResponse
from gld_scalper.ml.dataset_builder import build_training_dataset


class FakeLLMClient:
    provider = "ollama"
    model = "fake-model"

    def __init__(self, response):
        self.response = response

    def chat_json(self, *, system, user):
        return LLMResponse(provider=self.provider, model=self.model, content=json.dumps(self.response), raw={"fake": True})


class FailingKimiClient:
    provider = "kimi"
    model = "kimi-k2.6"

    def chat_json(self, *, system, user):
        raise LLMError("Kimi API returned HTTP 429: quota or account balance unavailable")


def test_llm_data_analysis_persists_review(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'llm.db'}")
    db = Database(settings=settings)
    db.init_db()
    client = FakeLLMClient(
        {
            "summary": "Bot is collecting data.",
            "bull_case": "Structured logs exist.",
            "bear_case": "No trades yet.",
            "risk_critique": "Avoid overfitting.",
            "execution_critique": "Check stale data.",
            "journal_review": "Journal is readable.",
            "missed_opportunity_explanations": ["No labels yet."],
            "setup_quality_notes": ["Wait for clean buildup."],
            "recommendations": ["Collect more paper data."],
        }
    )

    result = LLMAnalysisService(settings, db, client=client).run_data_analysis()

    assert result["summary"] == "Bot is collecting data."
    assert db.count_rows("llm_reviews") == 1


def test_llm_macro_context_persists_context(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'macro_llm.db'}")
    db = Database(settings=settings)
    db.init_db()
    client = FakeLLMClient(
        {
            "gold_news_sentiment": 0.4,
            "usd_sentiment": -0.2,
            "fed_rate_sentiment": -0.1,
            "risk_off_sentiment": 0.2,
            "headline_event_risk": 0.3,
            "sentiment_alignment": 0.5,
            "macro_bias": "bullish_gold_environment",
            "macro_confidence": 0.6,
            "positive_developments": ["Weak USD supports GLD."],
            "potential_concerns": ["Event risk moderate."],
            "forecast_summary": "Bullish macro context.",
        }
    )

    result = LLMAnalysisService(settings, db, client=client).build_llm_macro_context()

    assert result["macro_bias"] == "bullish_gold_environment"
    assert db.count_rows("macro_context") == 1


def test_llm_training_advice_and_labels_feed_dataset_when_enabled(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'labels.db'}", enable_llm_training_labels=True)
    db = Database(settings=settings)
    db.init_db()
    signal_id = db.insert_signal(
        {
            "timestamp": datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
            "symbol": "GLD",
            "decision": "LONG",
            "bullish_score": 90,
            "bearish_score": 10,
            "no_trade_score": 20,
            "regime": "breakout_up",
            "confidence": 0.9,
            "reason": "test setup",
            "feature_snapshot_json": {"close": 100.0, "pattern_quality": 0.9},
        }
    )
    db.upsert_decision_execution(
        {
            "decision_source": "signal",
            "decision_id": signal_id,
            "timestamp": datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc),
            "symbol": "GLD",
            "strategy_path": "minute",
            "original_action": "LONG",
            "executed_action": "LONG",
            "execution_status": "submitted",
            "direction_available": True,
        }
    )
    client = FakeLLMClient(
        {
            "labels": [
                {
                    "signal_id": signal_id,
                    "suggested_label": "long_good",
                    "confidence": 0.85,
                    "rationale": "Clean setup.",
                }
            ]
        }
    )

    labels = LLMAnalysisService(settings, db, client=client).label_recent_signals(limit=5)
    samples, dataset_labels, columns = build_training_dataset(db, lookback_days=1000, use_llm_labels=True)

    assert labels[0]["suggested_label"] == "long_good"
    assert dataset_labels == ["long_good"]
    assert samples
    assert "close" in columns


def test_llm_training_advice_persists_advice(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'advice.db'}")
    db = Database(settings=settings)
    db.init_db()
    client = FakeLLMClient(
        {
            "summary": "Improve labels and feature stability.",
            "feature_recommendations": ["Add event risk features."],
            "labeling_recommendations": ["Review missed opportunities."],
            "training_actions": ["Run walk-forward validation."],
            "risk_warnings": ["Avoid leakage."],
            "candidate_notes": ["Compare to rules baseline."],
        }
    )

    result = LLMAnalysisService(settings, db, client=client).generate_training_advice()

    assert "feature stability" in result["summary"]
    assert db.count_rows("llm_training_advice") == 1


def test_kimi_quota_failure_falls_back_to_local_ollama(tmp_path, monkeypatch):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'fallback.db'}",
        llm_provider="kimi",
        llm_base_url="https://api.moonshot.ai/v1",
        llm_model="kimi-k2.6",
        llm_api_key="test-key",
    )
    db = Database(settings=settings)
    db.init_db()
    fallback = FakeLLMClient(
        {
            "summary": "Local fallback completed the review.",
            "bull_case": "Evidence exists.",
            "bear_case": "No edge is proven.",
            "risk_critique": "Keep risk controls.",
            "execution_critique": "No broker authority.",
            "journal_review": "Review stored.",
            "missed_opportunity_explanations": [],
            "setup_quality_notes": [],
            "recommendations": ["Collect clean outcomes."],
        }
    )
    monkeypatch.setattr("gld_scalper.llm_analysis.make_ollama_fallback_client", lambda _settings: fallback)

    result = LLMAnalysisService(settings, db, client=FailingKimiClient()).run_data_analysis()

    assert result["summary"] == "Local fallback completed the review."
    assert result["review_type"] == "ollama_data_analysis"
    assert result["evidence"]["provider"] == "ollama"
