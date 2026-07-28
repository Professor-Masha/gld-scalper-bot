from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import mean

from .config import Settings, load_settings
from .database import Database
from .feature_engine import compute_indicators, resample_bars
from .gold_volatility import build_gold_volatility_features
from .gold_event_impact import build_gold_event_impact
from .microstructure import build_microstructure_features
from .models import BacktestResult, BacktestTrade, MLPrediction
from .price_action import analyze_price_action
from .reasoning_agents import combine_reasoning
from .risk_engine import RiskEngine
from .strategy_playbooks import evaluate_playbooks
from .technical_confluence import analyze_technical_market
from .strategy_engine import StrategyEngine
from .utils.math_utils import safe_div
from .utils.time_utils import EASTERN, ensure_utc, market_session


class Backtester:
    def __init__(self, database: Database, settings: Settings | None = None) -> None:
        self.database = database
        self.settings = settings or load_settings()
        self.strategy = StrategyEngine(self.settings)
        self.risk = RiskEngine(self.settings)

    def run(self, start: str | datetime, end: str | datetime) -> BacktestResult:
        start_dt = ensure_utc(start if isinstance(start, datetime) else f"{start}T00:00:00+00:00")
        end_dt = ensure_utc(end if isinstance(end, datetime) else f"{end}T23:59:59+00:00")
        bars = self.database.fetch_bars(self.settings.bot_symbol, self.settings.trade_timeframe, start_dt, end_dt)
        if len(bars) < 80:
            return BacktestResult(start_dt, end_dt, [], 0, _empty_metrics())

        trades: list[BacktestTrade] = []
        no_trade_count = 0
        open_position: dict | None = None
        equity = self.settings.paper_account_size
        daily_pnl = 0.0
        trades_today = 0
        current_trading_day = None
        consecutive_losses = 0
        cooldown_remaining = 0
        slippage_pct = 0.0001
        feature_rows = self._precompute_feature_rows(bars)

        for idx in range(60, len(bars) - 1):
            current_bar = bars[idx]
            current_time = ensure_utc(current_bar["timestamp"])
            trading_day = current_time.astimezone(EASTERN).date()
            if trading_day != current_trading_day:
                current_trading_day = trading_day
                daily_pnl = 0.0
                trades_today = 0

            if open_position:
                exit_trade = self._maybe_exit(open_position, current_bar, current_time, slippage_pct)
                if exit_trade:
                    trades.append(exit_trade)
                    equity += exit_trade.net_pnl_estimated
                    daily_pnl += exit_trade.net_pnl_estimated
                    consecutive_losses = consecutive_losses + 1 if exit_trade.net_pnl_estimated < 0 else 0
                    if consecutive_losses >= self.settings.max_consecutive_losses:
                        cooldown_remaining = 30
                    open_position = None
                continue

            if cooldown_remaining > 0:
                cooldown_remaining -= 1
                if cooldown_remaining == 0:
                    consecutive_losses = 0
                no_trade_count += 1
                continue

            features = feature_rows[idx]
            signal = self.strategy.evaluate(features, symbol=self.settings.bot_symbol, now=current_time, has_champion_model=True)
            state = self.risk.state_from_features(features)
            state.account_equity = equity
            state.day_start_equity = self.settings.paper_account_size
            state.daily_realized_pnl = daily_pnl
            state.trades_today = trades_today
            state.consecutive_losses = consecutive_losses
            state.market_open = market_session(current_time, extended_hours=self.settings.enable_extended_hours) != "closed"
            blocked, _ = self.risk.blocks_trading(state, signal.decision)
            if signal.decision == "NO_TRADE" or blocked:
                no_trade_count += 1
                continue

            next_bar = bars[idx + 1]
            entry_time = ensure_utc(next_bar["timestamp"])
            entry_open = float(next_bar["open"])
            entry_price = entry_open * (1 + slippage_pct if signal.decision == "LONG" else 1 - slippage_pct)
            state.latest_price = entry_price
            ml = MLPrediction(None, signal.decision.lower(), 0.5, 0.5, 0.0, confidence=0.5)
            try:
                plan = self.risk.build_order_plan(signal, ml, state)
            except ValueError:
                no_trade_count += 1
                continue
            open_position = {
                "symbol": signal.symbol,
                "direction": signal.decision,
                "decision_time": current_time,
                "entry_time": entry_time,
                "entry_price": entry_price,
                "qty": plan.qty,
                "take_profit_price": plan.take_profit_price,
                "stop_loss_price": plan.stop_loss_price,
                "regime": signal.regime,
            }
            trades_today += 1

        metrics = calculate_metrics(trades, self.settings.paper_account_size)
        for number, trade in enumerate(trades, start=1):
            self.database.insert_trade_outcome(
                {
                    "trade_id": f"backtest-{start_dt.date()}-{number}",
                    "symbol": trade.symbol,
                    "direction": trade.direction,
                    "entry_time": trade.entry_time,
                    "exit_time": trade.exit_time,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "qty": trade.qty,
                    "notional": trade.qty * trade.entry_price,
                    "gross_pnl": trade.gross_pnl,
                    "net_pnl_estimated": trade.net_pnl_estimated,
                    "pnl_pct": safe_div(trade.net_pnl_estimated, trade.qty * trade.entry_price),
                    "holding_seconds": (trade.exit_time - trade.entry_time).total_seconds(),
                    "exit_reason": trade.exit_reason,
                    "win_loss": "win" if trade.net_pnl_estimated > 0 else "loss",
                    "setup_type": f"decision_at:{trade.decision_time.isoformat()}",
                    "strategy_version": self.settings.strategy_version,
                    "mode": "backtest",
                }
            )
        return BacktestResult(start_dt, end_dt, trades, no_trade_count, metrics)

    def _precompute_feature_rows(self, bars: list[dict]) -> list[dict]:
        enriched_1m = compute_indicators(bars)
        enriched_5m = compute_indicators(resample_bars(bars, 5, "5Min"))
        enriched_15m = compute_indicators(resample_bars(bars, 15, "15Min"))
        tf5_idx = -1
        tf15_idx = -1
        session_day = None
        session_price_volume = 0.0
        session_volume = 0.0
        rows: list[dict] = []
        for idx, row in enumerate(enriched_1m):
            current_time = ensure_utc(row["timestamp"])
            trading_day = current_time.astimezone(EASTERN).date()
            if trading_day != session_day:
                session_day = trading_day
                session_price_volume = 0.0
                session_volume = 0.0
            volume = float(row.get("volume") or 0.0)
            typical = (float(row["high"]) + float(row["low"]) + float(row["close"])) / 3
            session_price_volume += typical * volume
            session_volume += volume

            features = dict(row)
            if session_volume:
                features["vwap"] = session_price_volume / session_volume
                features["vwap_deviation"] = safe_div(float(features["close"]) - float(features["vwap"]), float(features["vwap"]))

            tf5_idx = self._advance_completed_timeframe(enriched_5m, tf5_idx, current_time, 5)
            tf15_idx = self._advance_completed_timeframe(enriched_15m, tf15_idx, current_time, 15)
            if tf5_idx >= 0:
                self._merge_prefixed(features, enriched_5m[tf5_idx], "tf5")
            if tf15_idx >= 0:
                self._merge_prefixed(features, enriched_15m[tf15_idx], "tf15")

            features["latest_price"] = float(features["close"])
            features["data_age_seconds"] = 0.0
            features.setdefault("spread", float(features["close"]) * 0.0004)
            features.setdefault("spread_pct", 0.0004)
            features.setdefault("quote_imbalance", 0.0)
            window = enriched_1m[max(0, idx - 80) : idx + 1]
            features.update(analyze_price_action(window))
            features.update(build_microstructure_features(bars=window, quote=None, recent_trades=[], now=current_time))
            features.update(build_gold_volatility_features(window, now=current_time))
            features.update({"event_risk_active": False, "event_risk_reason": ""})
            features.update(build_gold_event_impact(features))
            technical_features, _ = analyze_technical_market(
                window,
                features,
                now=current_time,
                symbol=self.settings.bot_symbol,
                timeframe=self.settings.trade_timeframe,
            )
            features.update(technical_features)
            playbook = evaluate_playbooks(features, self.settings)
            features.update(playbook.to_features())
            features.update(combine_reasoning(features))
            rows.append(features)
        return rows

    @staticmethod
    def _advance_completed_timeframe(rows: list[dict], current_idx: int, current_time: datetime, minutes: int) -> int:
        next_idx = current_idx + 1
        while next_idx < len(rows):
            bucket_time = ensure_utc(rows[next_idx]["timestamp"])
            if bucket_time + timedelta(minutes=minutes) > current_time:
                break
            current_idx = next_idx
            next_idx += 1
        return current_idx

    @staticmethod
    def _merge_prefixed(target: dict, source: dict, prefix: str) -> None:
        for key, value in source.items():
            if key in {"symbol", "timeframe", "timestamp", "source", "created_at"}:
                continue
            target[f"{prefix}_{key}"] = value

    def _maybe_exit(self, position: dict, bar: dict, bar_time: datetime, slippage_pct: float) -> BacktestTrade | None:
        direction = position["direction"]
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        exit_price: float | None = None
        reason: str | None = None
        if direction == "LONG":
            if low <= position["stop_loss_price"]:
                exit_price = position["stop_loss_price"]
                reason = "stop_loss"
            elif high >= position["take_profit_price"]:
                exit_price = position["take_profit_price"]
                reason = "take_profit"
        else:
            if high >= position["stop_loss_price"]:
                exit_price = position["stop_loss_price"]
                reason = "stop_loss"
            elif low <= position["take_profit_price"]:
                exit_price = position["take_profit_price"]
                reason = "take_profit"
        holding_minutes = (bar_time - position["entry_time"]).total_seconds() / 60
        if exit_price is None and holding_minutes >= self.settings.max_holding_minutes:
            exit_price = close
            reason = "max_holding_time"
        if exit_price is None:
            return None
        if direction == "LONG":
            exit_price *= 1 - slippage_pct
            gross = (exit_price - position["entry_price"]) * position["qty"]
        else:
            exit_price *= 1 + slippage_pct
            gross = (position["entry_price"] - exit_price) * position["qty"]
        cost = position["entry_price"] * position["qty"] * 0.0002
        return BacktestTrade(
            symbol=position["symbol"],
            direction=direction,
            decision_time=position["decision_time"],
            entry_time=position["entry_time"],
            exit_time=bar_time,
            entry_price=position["entry_price"],
            exit_price=exit_price,
            qty=position["qty"],
            gross_pnl=gross,
            net_pnl_estimated=gross - cost,
            exit_reason=reason or "unknown",
            regime=position["regime"],
        )


def calculate_metrics(trades: list[BacktestTrade], starting_equity: float) -> dict[str, float]:
    if not trades:
        return _empty_metrics()
    pnls = [trade.net_pnl_estimated for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl <= 0]
    equity_curve: list[float] = []
    equity = starting_equity
    peak = starting_equity
    max_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        equity_curve.append(equity)
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, safe_div(peak - equity, peak))
    avg_pnl = mean(pnls)
    pnl_std = math.sqrt(mean([(pnl - avg_pnl) ** 2 for pnl in pnls])) if len(pnls) > 1 else 0.0
    long_pnls = [trade.net_pnl_estimated for trade in trades if trade.direction == "LONG"]
    short_pnls = [trade.net_pnl_estimated for trade in trades if trade.direction == "SHORT"]
    return {
        "total_return": safe_div(sum(pnls), starting_equity),
        "net_pnl": sum(pnls),
        "number_of_trades": float(len(trades)),
        "win_rate": safe_div(len(wins), len(trades)),
        "profit_factor": safe_div(sum(wins), abs(sum(losses)), 999.0 if wins else 0.0),
        "average_win": mean(wins) if wins else 0.0,
        "average_loss": mean(losses) if losses else 0.0,
        "average_pnl_per_trade": avg_pnl,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": safe_div(avg_pnl, pnl_std) * math.sqrt(252) if pnl_std else 0.0,
        "average_holding_seconds": mean([(trade.exit_time - trade.entry_time).total_seconds() for trade in trades]),
        "average_slippage": 0.0001,
        "long_net_pnl": sum(long_pnls),
        "short_net_pnl": sum(short_pnls),
        "consecutive_losses": float(_max_consecutive_losses(pnls)),
    }


def _max_consecutive_losses(pnls: list[float]) -> int:
    best = 0
    current = 0
    for pnl in pnls:
        if pnl <= 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _empty_metrics() -> dict[str, float]:
    return {
        "total_return": 0.0,
        "net_pnl": 0.0,
        "number_of_trades": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "average_win": 0.0,
        "average_loss": 0.0,
        "average_pnl_per_trade": 0.0,
        "max_drawdown": 0.0,
        "sharpe_ratio": 0.0,
        "average_holding_seconds": 0.0,
        "average_slippage": 0.0,
        "long_net_pnl": 0.0,
        "short_net_pnl": 0.0,
        "consecutive_losses": 0.0,
    }
