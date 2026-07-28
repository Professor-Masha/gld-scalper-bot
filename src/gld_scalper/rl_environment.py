from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from .config import Settings
from .database import Database
from .utils.math_utils import clamp, safe_div
from .utils.time_utils import ensure_utc, utc_now

ActionName = Literal["NO_TRADE", "LONG", "SHORT"]


@dataclass(slots=True)
class RLAction:
    name: ActionName
    size_multiplier: float = 1.0


@dataclass(slots=True)
class RLStep:
    timestamp: datetime
    action: ActionName
    reward: float
    pnl_pct: float
    spread_penalty: float
    risk_penalty: float


class GLDScalpingEnvironment:
    """Small offline environment for testing GLD scalp policies from SQLite bars."""

    def __init__(self, database: Database, settings: Settings, *, start: datetime | str | None = None, end: datetime | str | None = None, horizon_minutes: int = 5) -> None:
        self.database = database
        self.settings = settings
        self.horizon_minutes = horizon_minutes
        self.rows = database.fetch_bars(settings.bot_symbol, settings.trade_timeframe, start=start, end=end)
        self.index = 0

    def reset(self) -> dict[str, Any] | None:
        self.index = 0
        return self._observation()

    def done(self) -> bool:
        return self.index >= max(0, len(self.rows) - self.horizon_minutes - 1)

    def step(self, action: RLAction | ActionName | int) -> tuple[dict[str, Any] | None, float, bool, dict[str, Any]]:
        if self.done():
            return None, 0.0, True, {}
        parsed = _parse_action(action)
        current = self.rows[self.index]
        future = self.rows[self.index + 1 : self.index + 1 + self.horizon_minutes]
        step = _reward(current, future, parsed, self.settings.max_spread_pct)
        self.index += 1
        return self._observation(), step.reward, self.done(), {"step": step}

    def _observation(self) -> dict[str, Any] | None:
        if not self.rows or self.done():
            return None
        row = self.rows[self.index]
        return {
            "timestamp": row["timestamp"],
            "close": row["close"],
            "volume": row.get("volume"),
            "trade_count": row.get("trade_count"),
        }


def run_offline_policy_preview(
    database: Database,
    settings: Settings,
    *,
    start: datetime | str | None = None,
    end: datetime | str | None = None,
    policy_name: str = "momentum_preview",
) -> dict[str, Any]:
    env = GLDScalpingEnvironment(database, settings, start=start, end=end)
    observation = env.reset()
    rewards: list[float] = []
    trades = 0
    wins = 0
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    while observation is not None and not env.done():
        action = _preview_policy(env)
        if action.name != "NO_TRADE":
            trades += 1
        observation, reward, _, info = env.step(action)
        rewards.append(reward)
        equity += reward
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        step = info.get("step")
        if step and step.reward > 0:
            wins += 1
    total_reward = sum(rewards)
    benchmark_reward = 0.0
    promoted = False
    promotion_reason = "preview only; RL policies are advisory until walk-forward proof beats rules"
    result = {
        "timestamp": utc_now(),
        "experiment_name": "offline_gld_scalping_environment_preview",
        "policy_name": policy_name,
        "train_start": ensure_utc(start) if start else None,
        "train_end": None,
        "test_start": ensure_utc(start) if start else None,
        "test_end": ensure_utc(end) if end else None,
        "total_reward": total_reward,
        "total_trades": trades,
        "win_rate": safe_div(wins, trades),
        "max_drawdown": max_drawdown,
        "benchmark_reward": benchmark_reward,
        "promoted": promoted,
        "promotion_reason": promotion_reason,
        "metrics": {
            "steps": len(rewards),
            "average_reward": safe_div(total_reward, len(rewards)),
            "action_space": ["NO_TRADE", "LONG", "SHORT"],
            "size_multiplier_supported": True,
            "promotion_gate": "walk-forward reward must exceed current rules after costs and risk penalties",
        },
    }
    database.insert_rl_experiment(result)
    return result


def _preview_policy(env: GLDScalpingEnvironment) -> RLAction:
    idx = env.index
    if idx < 5:
        return RLAction("NO_TRADE", 0.0)
    closes = [float(row["close"]) for row in env.rows[idx - 5 : idx + 1]]
    momentum = safe_div(closes[-1] - closes[0], closes[0])
    if momentum > 0.0012:
        return RLAction("LONG", 0.5)
    if momentum < -0.0012:
        return RLAction("SHORT", 0.5)
    return RLAction("NO_TRADE", 0.0)


def _reward(row: dict[str, Any], future: list[dict[str, Any]], action: RLAction, max_spread_pct: float) -> RLStep:
    timestamp = ensure_utc(row["timestamp"])
    if not future or action.name == "NO_TRADE":
        return RLStep(timestamp, action.name, 0.0, 0.0, 0.0, 0.0)
    entry = float(row["close"])
    exit_price = float(future[-1]["close"])
    raw_pct = safe_div(exit_price - entry, entry)
    if action.name == "SHORT":
        raw_pct *= -1.0
    size = clamp(action.size_multiplier, 0.0, 1.5)
    spread_penalty = max_spread_pct * 0.75
    volatility = _future_range_pct(future, entry)
    risk_penalty = max(0.0, volatility - 0.004) * 0.25
    reward = (raw_pct * size) - spread_penalty - risk_penalty
    return RLStep(timestamp, action.name, reward, raw_pct, spread_penalty, risk_penalty)


def _future_range_pct(future: list[dict[str, Any]], entry: float) -> float:
    high = max(float(row["high"]) for row in future)
    low = min(float(row["low"]) for row in future)
    return safe_div(high - low, entry)


def _parse_action(action: RLAction | ActionName | int) -> RLAction:
    if isinstance(action, RLAction):
        return action
    if isinstance(action, int):
        return [RLAction("NO_TRADE", 0.0), RLAction("LONG", 1.0), RLAction("SHORT", 1.0)][int(clamp(action, 0, 2))]
    return RLAction(action, 1.0 if action != "NO_TRADE" else 0.0)
