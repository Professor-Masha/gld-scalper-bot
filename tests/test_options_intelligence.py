from datetime import datetime, timedelta, timezone

from gld_scalper.config import Settings
from gld_scalper.options_intelligence import (
    build_options_intelligence,
    options_intelligence_to_features,
    parse_occ_contract,
)


def _snapshot(option_type, strike, now, *, trade_at_ask, size, iv):
    bid = 2.00
    ask = 2.10
    return {
        "timestamp": now,
        "underlying_symbol": "GLD",
        "underlying_price": 200.0,
        "contract_symbol": f"test-{option_type}-{strike}",
        "option_type": option_type,
        "expiration_date": "2026-07-17",
        "strike_price": strike,
        "days_to_expiration": 16,
        "bid_price": bid,
        "ask_price": ask,
        "bid_size": 100,
        "ask_size": 120,
        "midpoint": 2.05,
        "spread_pct": (ask - bid) / 2.05,
        "last_trade_price": ask if trade_at_ask else bid,
        "last_trade_size": size,
        "last_trade_timestamp": now - timedelta(seconds=2),
        "implied_volatility": iv,
        "quote_age_seconds": 1.0,
        "feed": "indicative",
    }


def test_occ_contract_parser_extracts_type_expiry_and_strike():
    parsed = parse_occ_contract("GLD260717C00200000")

    assert parsed is not None
    assert parsed["option_type"] == "call"
    assert parsed["strike_price"] == 200.0
    assert parsed["expiration_date"].isoformat() == "2026-07-17"


def test_options_intelligence_is_bounded_and_bullish_for_call_pressure():
    now = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)
    snapshots = [
        _snapshot("call", 199.0, now, trade_at_ask=True, size=120, iv=0.25),
        _snapshot("call", 201.0, now, trade_at_ask=True, size=100, iv=0.26),
        _snapshot("put", 199.0, now, trade_at_ask=False, size=20, iv=0.27),
        _snapshot("put", 201.0, now, trade_at_ask=False, size=20, iv=0.28),
    ]
    settings = Settings(options_max_score_adjustment=3.0)

    result = build_options_intelligence(snapshots, underlying_price=200.0, now=now, settings=settings)
    features = result["features"]

    assert result["options_bias"] == "bullish"
    assert 0 < result["score_adjustment"] <= 3.0
    assert features["options_contracts_analyzed"] == 4
    assert not features["options_stale"]


def test_stale_options_context_becomes_neutral():
    now = datetime(2026, 7, 1, 15, 10, tzinfo=timezone.utc)
    record = {
        "timestamp": now - timedelta(minutes=10),
        "reason": "old snapshot",
        "features": {
            "options_bias": "bullish",
            "options_confidence": 0.9,
            "options_score_adjustment": 3.0,
            "options_stale": False,
        },
    }

    features = options_intelligence_to_features(record, now=now, max_age_seconds=180)

    assert features["options_bias"] == "neutral"
    assert features["options_confidence"] == 0.0
    assert features["options_score_adjustment"] == 0.0
    assert features["options_stale"]
