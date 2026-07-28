from datetime import datetime, timedelta, timezone

from gld_scalper.backtester import Backtester
from gld_scalper.config import Settings
from gld_scalper.database import Database


def test_backtest_uses_next_bar_for_entry(tmp_path):
    db_path = tmp_path / "backtest.db"
    settings = Settings(database_url=f"sqlite:///{db_path}", max_holding_minutes=3)
    db = Database(settings=settings)
    db.init_db()
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    price = 100.0
    bars = []
    for idx in range(130):
        open_price = price
        close_price = price + 0.05
        bars.append(
            {
                "symbol": "GLD",
                "timeframe": "1Min",
                "timestamp": start + timedelta(minutes=idx),
                "open": open_price,
                "high": close_price,
                "low": open_price - 0.15,
                "close": close_price,
                "volume": 10_000 + idx,
                "trade_count": 100,
                "vwap": open_price,
            }
        )
        price = close_price
    db.upsert_bars(bars)
    result = Backtester(db, settings).run("2026-01-05", "2026-01-05")
    assert result.trades
    assert all(trade.entry_time > trade.decision_time for trade in result.trades)


def test_backtest_resets_daily_trade_limit_each_session(tmp_path):
    db_path = tmp_path / "daily_reset.db"
    settings = Settings(database_url=f"sqlite:///{db_path}", max_holding_minutes=3, max_trades_per_day=1)
    db = Database(settings=settings)
    db.init_db()
    bars = []
    for day_offset in range(2):
        start = datetime(2026, 1, 5 + day_offset, 14, 30, tzinfo=timezone.utc)
        price = 100.0 + day_offset
        for idx in range(130):
            open_price = price
            close_price = price + 0.05
            bars.append(
                {
                    "symbol": "GLD",
                    "timeframe": "1Min",
                    "timestamp": start + timedelta(minutes=idx),
                    "open": open_price,
                    "high": close_price,
                    "low": open_price - 0.15,
                    "close": close_price,
                    "volume": 10_000 + idx,
                    "trade_count": 100,
                    "vwap": open_price,
                }
            )
            price = close_price
    db.upsert_bars(bars)

    result = Backtester(db, settings).run("2026-01-05", "2026-01-06")

    trade_days = {trade.entry_time.date() for trade in result.trades}
    assert len(trade_days) == 2
