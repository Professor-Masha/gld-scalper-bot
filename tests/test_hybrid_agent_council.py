from datetime import datetime, timedelta, timezone

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.decision_council import run_decision_council
from gld_scalper.tradingagents_advisory import (
    TradingAgentsAdvisoryService,
    agent_advisory_to_features,
)


class _Response:
    def __init__(self, value):
        self.value = value

    def json_content(self):
        return self.value


class ScriptedClient:
    provider = "ollama"
    model = "test-model"

    def __init__(self):
        self.responses = [
            {
                "technical_context": "Trend and price action support the long case.",
                "macro_news_context": "No imminent event conflict.",
                "journal_execution_context": "Comparable episodes had acceptable costs.",
                "evidence_ids": ["signals:1", "trade_outcomes:2"],
            },
            {
                "bull_case": "Price and liquidity align.",
                "bear_case": "Dollar strength remains a risk.",
                "unresolved_conflicts": [],
                "abstain": False,
            },
            {
                "bias": "bullish",
                "confidence": 0.8,
                "abstain": False,
                "event_risk": 0.2,
                "risk_flags": ["watch USD"],
                "size_multiplier": 1.5,
                "summary": "Bounded bullish intraday context.",
            },
        ]

    def chat_json(self, *, system, user):
        assert "no broker access" in system
        assert "JSON" in user
        return _Response(self.responses.pop(0))


def _healthy_features():
    return {
        "strategy_path": "minute",
        "websocket_connected": True,
        "stream_stale": False,
        "quote_age_seconds": 1.0,
        "trade_age_seconds": 2.0,
        "liquidity_score": 0.8,
        "spread_pct": 0.0002,
        "spread_regime": "tight",
        "quote_imbalance": 0.4,
        "trade_intensity": 1.0,
        "indicator_agent_score": 0.8,
        "pattern_agent_score": 0.8,
        "trend_agent_score": 0.7,
        "order_block_agent_score": 0.6,
        "ml_probability_long": 0.75,
        "ml_probability_short": 0.10,
        "playbook_allowed": True,
        "playbook_direction": "LONG",
        "technical_direction": "LONG",
        "agent_consensus": "LONG",
    }


def test_decision_council_builds_auditable_long_consensus():
    state = run_decision_council(_healthy_features(), Settings())

    assert state.consensus == "LONG"
    assert not state.hard_block
    assert {vote.agent for vote in state.votes} == {
        "DataHealthAgent",
        "MicrostructureAgent",
        "BullCaseAgent",
        "BearCaseAgent",
        "RiskCouncil",
    }
    assert state.as_features()["decision_council_json"]


def test_decision_council_gives_stale_data_absolute_priority():
    features = _healthy_features()
    features.update({"stream_stale": True, "stream_stale_reason": "no quotes for 5 seconds"})

    state = run_decision_council(features, Settings())

    assert state.consensus == "NO_TRADE"
    assert state.hard_block
    assert "no quotes" in " ".join(state.block_reasons)


def test_tradingagents_advisory_is_bounded_persisted_and_expires(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'agent.db'}",
        tradingagents_max_sizing_adjustment=0.05,
        tradingagents_advisory_max_age_minutes=60,
    )
    db = Database(settings=settings)
    db.init_db()
    now = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)

    record = TradingAgentsAdvisoryService(settings, db, client=ScriptedClient()).run(now=now)

    assert record["advisory_only"] is True
    assert record["size_multiplier"] == 1.05
    assert db.count_rows("agent_advisories") == 1
    fresh = agent_advisory_to_features(record, settings, now=now + timedelta(minutes=30))
    assert fresh["tradingagents_advisory_score_adjustment"] > 0
    stale = agent_advisory_to_features(record, settings, now=now + timedelta(minutes=61))
    assert stale["tradingagents_advisory_score_adjustment"] == 0
    assert stale["tradingagents_advisory_size_multiplier"] == 1.0
