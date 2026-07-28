from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gld_scalper.config import Settings
from gld_scalper.database import Database
from gld_scalper.ema_cross_strategy import (
    EMACrossEvent,
    apply_ema_cross_paper_authority,
    evaluate_ema_cross_strategy,
    resample_completed_session_bars,
    select_ema_cross_events,
)
from gld_scalper.models import MarketSignal
from gld_scalper.outcome_labeler import MultiHorizonOutcomeLabeler


def _bars(closes: list[float]) -> list[dict]:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    output = []
    previous = closes[0]
    for index, close in enumerate(closes):
        output.append(
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": start + timedelta(minutes=index),
                "open": previous,
                "high": max(previous, close) + 0.08,
                "low": min(previous, close) - 0.08,
                "close": close,
                "volume": 10_000 + index,
                "trade_count": 100 + index,
                "vwap": close,
                "source": "test",
            }
        )
        previous = close
    return output


def _crossing_bars() -> list[dict]:
    falling = [100.0 - index * 0.05 for index in range(55)]
    rising = [falling[-1] + index * 0.18 for index in range(1, 35)]
    return _bars(falling + rising)


def _first_cross(settings: Settings):
    bars = _crossing_bars()
    for end in range(35, len(bars) + 1):
        evaluation = evaluate_ema_cross_strategy(bars[:end], settings)
        if evaluation.events:
            return evaluation.events[-1]
    raise AssertionError("synthetic series did not produce an EMA cross")


def _event(
    *,
    timeframe: int,
    direction: str,
    score: float = 80.0,
) -> EMACrossEvent:
    timestamp = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    return EMACrossEvent(
        timeframe_minutes=timeframe,
        bar_timestamp=timestamp,
        bar_close_timestamp=timestamp + timedelta(minutes=timeframe),
        direction=direction,
        fast_ema=101.0,
        slow_ema=100.0,
        previous_fast_ema=99.0,
        previous_slow_ema=100.0,
        adx=25.0,
        atr=0.20,
        adx_passed=True,
        cooldown_passed=True,
        bars_since_last_execution=None,
        eligible=True,
        block_reason="",
        confidence=0.75,
        score=score,
    )


def test_completed_bar_cross_matches_filtered_long_signal():
    settings = Settings(
        ema_cross_timeframes=[1],
        ema_cross_history_minutes=100,
        ema_cross_adx_threshold=0.0,
    )

    event = _first_cross(settings)

    assert event.direction == "LONG"
    assert event.eligible is True
    assert event.fast_ema > event.slow_ema
    assert event.previous_fast_ema <= event.previous_slow_ema
    assert event.bar_close_timestamp == event.bar_timestamp + timedelta(minutes=1)


def test_adx_filter_records_cross_but_blocks_eligibility():
    settings = Settings(
        ema_cross_timeframes=[1],
        ema_cross_history_minutes=100,
        ema_cross_adx_threshold=101.0,
    )

    event = _first_cross(settings)

    assert event.direction == "LONG"
    assert event.adx_passed is False
    assert event.eligible is False
    assert "ADX" in event.block_reason


def test_higher_timeframe_uses_session_anchor_and_excludes_partial_bar():
    bars = _bars([100.0 + index * 0.01 for index in range(90)])

    completed = resample_completed_session_bars(bars, 60)

    assert len(completed) == 1
    assert completed[0]["timestamp"] == datetime(
        2026,
        1,
        5,
        14,
        30,
        tzinfo=timezone.utc,
    )
    assert completed[0]["bar_close_timestamp"] == datetime(
        2026,
        1,
        5,
        15,
        30,
        tzinfo=timezone.utc,
    )


def test_selection_merges_same_direction_and_defers_lower_timeframe_conflict():
    long_1m = _event(timeframe=1, direction="LONG", score=78.0)
    long_5m = _event(timeframe=5, direction="LONG", score=82.0)
    short_15m = _event(timeframe=15, direction="SHORT", score=84.0)

    selection = select_ema_cross_events([long_1m, long_5m, short_15m])

    assert selection.selected == short_15m
    assert selection.merged == (short_15m,)
    assert selection.conflicts == (long_1m, long_5m)


def test_paper_authority_converts_confirmed_cross_to_controlled_probe():
    settings = Settings(
        paper_learning_mode=True,
        ema_cross_paper_signal_authority=True,
    )
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    base = MarketSignal(
        timestamp=now,
        symbol="GLD",
        decision="NO_TRADE",
        bullish_score=45.0,
        bearish_score=35.0,
        no_trade_score=90.0,
        regime="bullish_trend",
        confidence=0.45,
        reason="base strategy abstained",
    )
    features = {
        "ema_cross_signal_active": True,
        "ema_cross_direction": "LONG",
        "ema_cross_timeframe": "5Min",
        "ema_cross_timeframe_minutes": 5,
        "ema_cross_adx": 28.0,
        "ema_cross_atr": 0.25,
        "ema_cross_score": 84.0,
        "ema_cross_confidence": 0.72,
        "playbook": "ema_cross_filtered",
        "playbook_allowed": True,
    }

    signal = apply_ema_cross_paper_authority(base, features, settings)

    assert signal.decision == "LONG"
    assert signal.features["controlled_exploration_selected"] is True
    assert signal.features["strategy_path"] == "ema_cross"
    assert signal.features["playbook_target_r_multiple"] == 2.0


def test_paper_authority_never_overrides_a_playbook_safety_block():
    settings = Settings(paper_learning_mode=True)
    now = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    base = MarketSignal(
        timestamp=now,
        symbol="GLD",
        decision="NO_TRADE",
        bullish_score=45.0,
        bearish_score=35.0,
        no_trade_score=90.0,
        regime="poor_liquidity",
        confidence=0.45,
        reason="liquidity block",
    )
    features = {
        "ema_cross_signal_active": True,
        "ema_cross_direction": "LONG",
        "playbook": "ema_cross_filtered",
        "playbook_allowed": False,
    }

    signal = apply_ema_cross_paper_authority(base, features, settings)

    assert signal.decision == "NO_TRADE"


def test_database_deduplicates_cross_and_tracks_execution(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ema.db'}",
        ema_cross_timeframes=[1],
        ema_cross_history_minutes=100,
    )
    database = Database(settings=settings)
    database.init_db()
    event = _event(timeframe=1, direction="LONG")
    record = event.to_record(symbol="GLD")

    first_id, first_inserted = database.upsert_ema_cross_signal(record)
    second_id, second_inserted = database.upsert_ema_cross_signal(record)
    database.update_ema_cross_signals(
        [first_id],
        execution_status="submitted",
        signal_id=7,
        client_order_id="gld-test",
    )

    assert first_id == second_id
    assert first_inserted is True
    assert second_inserted is False
    assert database.fetch_latest_executed_ema_cross_bars("GLD") == {
        1: event.bar_timestamp
    }


def test_ema_cross_event_receives_multi_horizon_learning_labels(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ema-labels.db'}",
        ema_cross_timeframes=[1],
        ema_cross_history_minutes=100,
    )
    database = Database(settings=settings)
    database.init_db()
    event = _event(timeframe=1, direction="LONG")
    event_id, _ = database.upsert_ema_cross_signal(event.to_record(symbol="GLD"))
    for minutes, price in ((0, 100.0), (1, 100.2), (3, 100.3), (5, 100.4), (15, 100.5)):
        database.upsert_price_snapshot(
            {
                "timestamp": event.bar_close_timestamp + timedelta(minutes=minutes),
                "symbol": "GLD",
                "midpoint": price,
                "spread_pct": 0.0001,
            }
        )

    result = MultiHorizonOutcomeLabeler(settings, database).label_matured(
        now=event.bar_close_timestamp + timedelta(minutes=20),
        sources=("ema_cross",),
    )

    label = database.conn.execute(
        """
        SELECT * FROM outcome_labels
        WHERE decision_source = 'ema_cross' AND decision_id = ?
        """,
        (event_id,),
    ).fetchone()
    assert result.labeled == 1
    assert result.by_source == {"ema_cross": 1}
    assert label["label_1m"] == "long_good"
    assert label["label_15m"] == "long_good"
