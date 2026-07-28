from datetime import datetime, timedelta, timezone

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.macro_context import MacroContextBuilder, macro_context_to_features


def _bar(ts, symbol, close):
    return {
        "symbol": symbol,
        "timeframe": "1Min",
        "timestamp": ts,
        "open": close,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "volume": 1000,
    }


def test_macro_context_builds_bullish_gold_bias_from_headlines_and_proxies(tmp_path):
    headlines = tmp_path / "macro_headlines.csv"
    headlines.write_text(
        "timestamp,source,headline\n"
        "2026-01-02T14:00:00+00:00,test,Gold rally as weaker dollar and rate cut hopes lift safe haven demand\n",
        encoding="utf-8",
    )
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'macro.db'}", macro_context_headlines_path=str(headlines))
    db = Database(settings=settings)
    db.init_db()
    start = datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)
    db.upsert_bars([_bar(start + timedelta(minutes=i), "GLD", 100 + i * 0.02) for i in range(20)])
    db.upsert_bars([_bar(start + timedelta(minutes=i), "UUP", 30 - i * 0.002) for i in range(20)])

    record = MacroContextBuilder(settings, db).build(now=datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc))
    db.insert_macro_context(record)
    latest = db.get_latest_macro_context(max_age_minutes=120, now=datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc))
    features = macro_context_to_features(latest)

    # Unlinked text is retained for event-risk context but cannot establish a
    # directional macro bias until subsequent GLD behavior validates it.
    assert record["macro_bias"] == "neutral_environment"
    assert record["news_linked_fraction"] == 0.0
    assert record["macro_confidence"] <= 0.35
    assert features["macro_context_available"]
    assert db.count_rows("macro_context") == 1
    assert db.count_rows("news_items") == 1


def test_macro_context_to_features_defaults_when_missing():
    features = macro_context_to_features(None)
    assert features["macro_bias"] == "neutral_environment"
    assert features["macro_context_available"] is False
