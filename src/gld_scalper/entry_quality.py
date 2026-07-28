from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings
from .database import Database
from .paper_exploration import exploration_playbook_quality
from .utils.time_utils import ensure_utc


@dataclass(frozen=True, slots=True)
class EntryGateResult:
    allowed: bool
    reason: str
    time_profile: str

    def as_features(self) -> dict[str, Any]:
        return {
            "entry_quality_allowed": self.allowed,
            "entry_quality_reason": self.reason,
            "time_of_day_profile": self.time_profile,
        }


def time_of_day_profile(now: datetime) -> str:
    local = ensure_utc(now).astimezone(ZoneInfo("America/New_York")).time()
    if local < time(9, 30) or local >= time(16):
        return "closed"
    if local < time(10, 30):
        return "open"
    if local < time(12):
        return "morning"
    if local < time(14):
        return "mid_session"
    if local < time(15, 30):
        return "afternoon"
    return "close"


class EntryQualityGate:
    """Apply one explicit data-quality contract to both live strategy paths."""

    FAST_BLOCKED_REGIMES = {"poor_liquidity", "low_volatility", "sideways_chop", "high_volatility"}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def evaluate(self, features: dict[str, Any], *, strategy_path: str, now: datetime) -> EntryGateResult:
        profile = time_of_day_profile(now)
        blocks: list[str] = []
        fast = strategy_path == "fast"
        exploration = bool(
            self.settings.paper_learning_mode
            and features.get("paper_exploration")
            and features.get("controlled_exploration_selected")
        )
        quote_age = _number(features.get("quote_age_seconds"), 1_000_000.0)
        trade_age = _number(features.get("trade_age_seconds"), 1_000_000.0)
        quote_limit = self.settings.fast_scalp_max_quote_age_seconds if fast else self.settings.minute_entry_max_quote_age_seconds
        trade_limit = self.settings.fast_scalp_max_trade_age_seconds if fast else self.settings.minute_entry_max_trade_age_seconds
        intensity_min = self.settings.fast_scalp_min_trade_intensity if fast else self.settings.minute_entry_min_trade_intensity
        stability_min = self.settings.fast_scalp_min_spread_stability if fast else self.settings.minute_entry_min_spread_stability
        spread = _number(features.get("spread_pct"))
        maximum_spread = min(
            self.settings.max_spread_pct,
            self.settings.paper_learning_max_spread_pct
            if exploration
            else self.settings.fast_scalp_tight_spread_pct
            if fast
            else self.settings.paper_exploration_max_spread_pct,
        )

        if features.get("websocket_connected") is False or features.get("stream_stale"):
            blocks.append("live stream is disconnected or stale")
        if quote_age > quote_limit:
            blocks.append(f"quote age {quote_age:.2f}s exceeds {quote_limit:.2f}s")
        if trade_age > trade_limit:
            blocks.append(f"trade age {trade_age:.2f}s exceeds {trade_limit:.2f}s")
        if spread <= 0 or spread > maximum_spread or str(features.get("spread_regime") or "") == "wide":
            blocks.append("spread is not stable and tradeable")
        trade_intensity = _number(features.get("trade_intensity"), _number(features.get("trade_intensity_60s")))
        if trade_intensity < intensity_min:
            blocks.append("trade intensity below entry minimum")
        if _spread_stability(features) < stability_min:
            blocks.append("spread stability below entry minimum")
        minimum_liquidity = (
            self.settings.paper_learning_min_liquidity_score
            if exploration
            else self.settings.minimum_liquidity_score
        )
        if _number(features.get("liquidity_score")) < minimum_liquidity:
            blocks.append("liquidity score below entry minimum")

        regime = str(features.get("regime") or features.get("gold_volatility_regime") or "")
        playbook = str(features.get("playbook") or "")
        if fast and regime in self.FAST_BLOCKED_REGIMES and not (
            exploration and regime in {"low_volatility", "sideways_chop"}
        ):
            blocks.append(f"fast strategy disabled in {regime}")
        if fast and features.get("volatility_burst") and playbook not in {"proper_breakout", "news_event"}:
            blocks.append("unconfirmed fast volatility burst")
        if exploration:
            allowed, reason = exploration_playbook_quality(
                features,
                str(features.get("paper_exploration_direction") or features.get("playbook_direction") or ""),
                self.settings,
                strategy_path=strategy_path,
            )
            if not allowed:
                blocks.append(reason)
        elif features.get("playbook_allowed") is False:
            blocks.append(str(features.get("playbook_block_reason") or "playbook not confirmed"))
        if str(features.get("playbook_direction") or "NO_TRADE") == "NO_TRADE":
            blocks.append("no confirmed playbook direction")
        if (
            profile == "mid_session"
            and not features.get("proper_break")
            and playbook not in {"false_break_reversal", "ema_cross_filtered"}
            and not (exploration and playbook == "pullback_continuation")
        ):
            blocks.append("mid-session entry lacks a proper break")
        if profile == "close" and (
            playbook not in {"proper_breakout", "trend_continuation", "ema_cross_filtered"}
            or _number(features.get("playbook_score")) < max(self.settings.minimum_playbook_score, 80.0)
        ):
            blocks.append("close profile requires a high-quality breakout or trend continuation")
        if profile == "closed":
            blocks.append("entries disabled while the regular session is closed")
        return EntryGateResult(not blocks, "; ".join(dict.fromkeys(blocks)) or "entry quality confirmed", profile)


class EntryCooldownPolicy:
    """Derive post-loss and repeated-regime cooldowns from durable outcomes."""

    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def evaluate(self, *, strategy_path: str, features: dict[str, Any], now: datetime) -> EntryGateResult:
        profile = time_of_day_profile(now)
        last = self.database.conn.execute(
            """
            SELECT exit_time, net_pnl_after_costs, net_pnl_estimated
            FROM trade_outcomes
            WHERE strategy_path = ? AND exit_time IS NOT NULL
            ORDER BY exit_time DESC, id DESC LIMIT 1
            """,
            (strategy_path,),
        ).fetchone()
        if last is not None:
            net = _number(last["net_pnl_after_costs"], _number(last["net_pnl_estimated"]))
            elapsed = (ensure_utc(now) - ensure_utc(last["exit_time"])).total_seconds()
            if net < 0 and elapsed < self.settings.post_loss_cooldown_seconds:
                remaining = self.settings.post_loss_cooldown_seconds - elapsed
                return EntryGateResult(False, f"post-loss cooldown active for {remaining:.0f}s", profile)

        regime = str(features.get("regime") or "unknown")
        playbook = str(features.get("playbook") or "unknown")
        since = ensure_utc(now) - timedelta(minutes=self.settings.regime_loss_lookback_minutes)
        rows = self.database.conn.execute(
            """
            SELECT exit_time, net_pnl_after_costs, net_pnl_estimated
            FROM trade_outcomes
            WHERE strategy_path = ? AND regime = ? AND playbook = ? AND exit_time >= ?
            ORDER BY exit_time DESC, id DESC
            """,
            (strategy_path, regime, playbook, since.isoformat()),
        ).fetchall()
        losses = [row for row in rows if _number(row["net_pnl_after_costs"], _number(row["net_pnl_estimated"])) < 0]
        if len(losses) >= self.settings.regime_loss_threshold:
            last_loss = ensure_utc(losses[0]["exit_time"])
            release = last_loss + timedelta(minutes=self.settings.regime_loss_cooldown_minutes)
            if ensure_utc(now) < release:
                return EntryGateResult(False, f"{strategy_path}/{playbook}/{regime} loss-cluster cooldown active", profile)
        return EntryGateResult(True, "no entry cooldown", profile)


def _spread_stability(features: dict[str, Any]) -> float:
    explicit = features.get("spread_stability_score")
    if explicit is not None:
        return max(0.0, min(1.0, _number(explicit)))
    expansion = max(_number(features.get("spread_expansion_ratio"), 1.0), 0.0)
    return max(0.0, min(1.0, 1.0 - abs(expansion - 1.0)))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default
