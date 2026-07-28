from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from .config import Settings, load_settings
from .database import Database
from .utils.math_utils import clamp, safe_div
from .utils.time_utils import ensure_utc, utc_now


logger = logging.getLogger(__name__)


class TradeLearningAnalyzer:
    """Turn each completed paper trade into one durable supervised lesson."""

    def __init__(self, settings: Settings | None = None, database: Database | None = None) -> None:
        self.settings = settings or load_settings()
        self.database = database or Database(settings=self.settings)

    def review(self, trade_outcome_id: int) -> dict[str, Any] | None:
        if self.database.trade_review_exists(trade_outcome_id):
            return None
        row = self.database.conn.execute(
            "SELECT * FROM trade_outcomes WHERE id = ?",
            (int(trade_outcome_id),),
        ).fetchone()
        if row is None:
            return None
        outcome = dict(row)
        decision = self.database.fetch_trade_decision(outcome.get("trade_id")) or {}
        features = _json_object(decision.get("feature_snapshot_json"))
        for key in ("bullish_score", "bearish_score", "no_trade_score", "confidence", "regime"):
            if decision.get(key) is not None:
                features.setdefault(key, decision[key])
        entry_time = ensure_utc(outcome["entry_time"])
        exit_time = ensure_utc(outcome["exit_time"])
        excursions = self._excursions(outcome, entry_time, exit_time)
        net_pnl = float(outcome.get("net_pnl_after_costs") or outcome.get("net_pnl_estimated") or 0.0)
        net_return = safe_div(net_pnl, float(outcome.get("notional") or 0.0))
        exploration = bool(decision.get("exploration_trade") or features.get("paper_exploration"))
        setup_quality = _setup_quality(features, outcome["direction"], self.settings.max_spread_pct)
        mistake = _mistake_category(outcome, features, exploration, self.settings)
        positive, negative = _review_factors(outcome, features, excursions, net_return, self.settings)
        label = f"{str(outcome['direction']).lower()}_good" if net_return > 0 else "no_trade"
        review_type = "GOOD_TRADE" if net_return > 0 else "MISTAKE"
        risk_penalty = max(0.0, excursions["max_adverse_excursion"] - self.settings.stop_loss_pct_floor) * 0.25
        reward = net_return - risk_penalty
        signal_id = int(decision["signal_id"]) if decision.get("signal_id") is not None else self._find_signal(outcome, decision)
        setup_type = str(features.get("playbook") or features.get("pattern_classification") or outcome.get("setup_type") or "unknown")
        lesson = _lesson_summary(review_type, outcome, setup_quality, positive, negative, exploration)
        counterfactual = _counterfactual(review_type, outcome, features, positive, negative)
        best_possible_pnl = float(outcome.get("notional") or 0.0) * excursions["max_favorable_excursion"]
        profit_given_back = max(0.0, best_possible_pnl - max(float(outcome.get("gross_pnl") or 0.0), 0.0))
        opportunity_cost = max(
            0.0,
            best_possible_pnl - float(outcome.get("estimated_live_cost") or 0.0) - net_pnl,
        )
        record = {
            "trade_outcome_id": trade_outcome_id,
            "trade_id": outcome.get("trade_id"),
            "signal_id": signal_id,
            "journal_id": decision.get("id"),
            "symbol": outcome["symbol"],
            "direction": outcome["direction"],
            "entry_time": entry_time,
            "exit_time": exit_time,
            "result_label": label,
            "review_type": review_type,
            "setup_quality": setup_quality,
            "learning_reward": reward,
            "net_return": net_return,
            **excursions,
            "exit_reason": outcome.get("exit_reason"),
            "mistake_category": mistake,
            "exploration_trade": exploration,
            "entry_reason": decision.get("reason"),
            "lesson_summary": lesson,
            "positive_factors": positive,
            "negative_factors": negative,
            "counterfactual": counterfactual,
            "feature_snapshot": features,
            "outcome_snapshot": outcome,
            "created_at": utc_now(),
        }
        self.database.update_trade_outcome_learning(
            trade_outcome_id,
            {
                **excursions,
                "mistake_category": mistake,
                "setup_type": setup_type,
                "model_version": decision.get("model_version"),
                "exploration_trade": exploration,
                "opportunity_cost": opportunity_cost,
                "profit_given_back": profit_given_back,
            },
        )
        self.database.insert_trade_review(record)
        journal_id = self.database.insert_trading_journal(
            {
                "timestamp": exit_time,
                "symbol": outcome["symbol"],
                "event_type": "TRADE_REVIEW",
                "signal_id": signal_id,
                "decision": outcome["direction"],
                "confidence": decision.get("confidence"),
                "bullish_score": decision.get("bullish_score"),
                "bearish_score": decision.get("bearish_score"),
                "no_trade_score": decision.get("no_trade_score"),
                "regime": decision.get("regime"),
                "reason": lesson,
                "model_version": decision.get("model_version"),
                "client_order_id": outcome.get("trade_id"),
                "qty": outcome.get("qty"),
                "price": outcome.get("exit_price"),
                "notional": outcome.get("notional"),
                "status": label,
                "pnl": net_pnl,
                "exploration_trade": exploration,
                "pattern_classification": features.get("pattern_classification"),
                "pattern_quality": features.get("pattern_quality"),
                "liquidity_score": features.get("liquidity_score"),
                "volatility_regime": features.get("gold_volatility_regime"),
                "reasoning_agents_json": features.get("agent_reasoning_json"),
                "macro_bias": features.get("macro_bias"),
                "macro_confidence": features.get("macro_confidence"),
                "feature_snapshot_json": features,
                "broker_snapshot_json": {
                    "trade_review": record,
                    "positive_factors": positive,
                    "negative_factors": negative,
                    "counterfactual": counterfactual,
                },
            }
        )
        if signal_id is not None:
            self.database.insert_outcome_label(
                {
                    "signal_id": signal_id,
                    "journal_id": journal_id,
                    "timestamp": entry_time,
                    "symbol": outcome["symbol"],
                    "decision": outcome["direction"],
                    "label": label,
                    "max_favorable_excursion": excursions["max_favorable_excursion"],
                    "max_adverse_excursion": excursions["max_adverse_excursion"],
                    "stop_would_hit": outcome.get("exit_reason") == "stop_loss",
                    "target_would_hit": outcome.get("exit_reason") == "take_profit",
                    "false_break": bool(features.get("false_break")),
                    "proper_break": bool(features.get("proper_break")),
                    "realized_spread_cost": float(features.get("spread_pct") or 0.0),
                    "net_outcome_after_costs": net_return,
                    "best_exit_minutes": excursions.get("best_exit_minutes"),
                    "worst_drawdown_before_profit": excursions["max_adverse_excursion"],
                    "raw_json": {"source": "completed_paper_trade", "trade_review": record},
                }
            )
        logger.info(
            "trade review completed trade_id=%s review=%s label=%s mistake=%s reward=%.6f exploration=%s",
            outcome.get("trade_id"),
            review_type,
            label,
            mistake,
            reward,
            exploration,
        )
        return record

    def review_pending(self, *, limit: int = 100) -> dict[str, int]:
        rows = self.database.conn.execute(
            """
            SELECT o.id
            FROM trade_outcomes o
            WHERE NOT EXISTS (
                SELECT 1 FROM trade_reviews r WHERE r.trade_outcome_id = o.id
            )
            ORDER BY o.exit_time ASC, o.id ASC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        reviewed = 0
        failed = 0
        for row in rows:
            try:
                if self.review(int(row["id"])) is not None:
                    reviewed += 1
            except Exception:
                failed += 1
                logger.exception("pending trade review failed trade_outcome_id=%s", row["id"])
        return {"reviewed": reviewed, "failed": failed}

    def _excursions(self, outcome: dict[str, Any], entry_time, exit_time) -> dict[str, Any]:
        entry = float(outcome.get("entry_price") or 0.0)
        bars = self.database.fetch_bars(outcome["symbol"], self.settings.trade_timeframe, entry_time, exit_time)
        if entry <= 0:
            return {"max_favorable_excursion": 0.0, "max_adverse_excursion": 0.0, "best_exit_minutes": None}
        highs = [float(row["high"]) for row in bars] or [entry, float(outcome.get("exit_price") or entry)]
        lows = [float(row["low"]) for row in bars] or [entry, float(outcome.get("exit_price") or entry)]
        if outcome["direction"] == "SHORT":
            favorable = max(0.0, safe_div(entry - min(lows), entry))
            adverse = max(0.0, safe_div(max(highs) - entry, entry))
            best_index = lows.index(min(lows)) if bars else None
        else:
            favorable = max(0.0, safe_div(max(highs) - entry, entry))
            adverse = max(0.0, safe_div(entry - min(lows), entry))
            best_index = highs.index(max(highs)) if bars else None
        best_minutes = None
        if best_index is not None and bars:
            best_minutes = max(0, round((ensure_utc(bars[best_index]["timestamp"]) - entry_time).total_seconds() / 60))
        return {
            "max_favorable_excursion": favorable,
            "max_adverse_excursion": adverse,
            "best_exit_minutes": best_minutes,
        }

    def _find_signal(self, outcome: dict[str, Any], decision: dict[str, Any]) -> int | None:
        anchor = ensure_utc(decision.get("timestamp") or outcome["entry_time"])
        row = self.database.conn.execute(
            """
            SELECT id FROM signals
            WHERE symbol = ? AND decision = ? AND timestamp BETWEEN ? AND ?
            ORDER BY ABS(julianday(timestamp) - julianday(?)), id DESC
            LIMIT 1
            """,
            (
                str(outcome["symbol"]).upper(),
                outcome["direction"],
                (anchor - timedelta(minutes=2)).isoformat(),
                (anchor + timedelta(minutes=2)).isoformat(),
                anchor.isoformat(),
            ),
        ).fetchone()
        return int(row["id"]) if row is not None else None


def _setup_quality(features: dict[str, Any], direction: str, max_spread: float) -> float:
    raw_score = features.get("bullish_score") if direction == "LONG" else features.get("bearish_score")
    score = float(raw_score or 0.0)
    pattern = clamp(float(features.get("pattern_quality") or 0.0), 0.0, 1.0)
    liquidity = clamp(float(features.get("liquidity_score") or 0.0), 0.0, 1.0)
    agent = clamp(float(features.get("agent_confidence") or 0.0), 0.0, 1.0)
    spread = float(features.get("spread_pct") or max_spread)
    spread_quality = 1.0 - clamp(safe_div(spread, max(max_spread, 1e-9)), 0.0, 1.0)
    return round(clamp(score / 100 * 0.30 + pattern * 0.25 + liquidity * 0.20 + agent * 0.15 + spread_quality * 0.10, 0.0, 1.0), 4)


def _mistake_category(outcome: dict[str, Any], features: dict[str, Any], exploration: bool, settings: Settings) -> str | None:
    if float(outcome.get("net_pnl_estimated") or 0.0) > 0:
        return None
    if features.get("paper_learning_mode"):
        return "paper_learning_probe_failed"
    if exploration:
        return "exploration_setup_failed"
    if features.get("stream_stale") or features.get("websocket_connected") is False:
        return "stale_data_entry"
    if float(features.get("spread_pct") or 0.0) > settings.max_spread_pct:
        return "wide_spread_entry"
    if float(features.get("liquidity_score") or 1.0) < settings.minimum_liquidity_score:
        return "poor_liquidity_entry"
    if features.get("event_risk_active") or float(features.get("headline_event_risk") or 0.0) > 0.75:
        return "event_risk_entry"
    if features.get("order_block_conflict"):
        return "order_block_conflict"
    if str(features.get("pattern_classification") or "").startswith("false_break"):
        return "false_break_entry"
    if outcome.get("exit_reason") == "stop_loss":
        return "setup_invalidated"
    return "negative_expectancy_setup"


def _review_factors(
    outcome: dict[str, Any],
    features: dict[str, Any],
    excursions: dict[str, Any],
    net_return: float,
    settings: Settings,
) -> tuple[list[str], list[str]]:
    positive: list[str] = []
    negative: list[str] = []
    direction = outcome["direction"]
    if net_return > 0:
        positive.append("trade produced positive after-fill return")
    else:
        negative.append("trade produced non-positive after-fill return")
    if outcome.get("exit_reason") == "take_profit":
        positive.append("planned take-profit was reached")
    elif outcome.get("exit_reason") == "stop_loss":
        negative.append("planned stop-loss was reached")
    pattern = str(features.get("pattern_classification") or "")
    if pattern in {"proper_break_up", "proper_break_down", "pullback_up", "pullback_down"}:
        positive.append(f"clean price-action pattern: {pattern}")
    elif pattern:
        negative.append(f"weak or conflicting pattern: {pattern}")
    liquidity = float(features.get("liquidity_score") or 0.0)
    if liquidity >= 0.70:
        positive.append("liquidity was healthy at entry")
    elif liquidity < settings.minimum_liquidity_score:
        negative.append("liquidity was weak at entry")
    spread = float(features.get("spread_pct") or 0.0)
    if spread and spread <= settings.max_spread_pct * 0.5:
        positive.append("entry spread was tight")
    elif spread > settings.max_spread_pct:
        negative.append("entry spread exceeded the configured maximum")
    expected_agent = direction
    if features.get("agent_consensus") == expected_agent:
        positive.append("reasoning agents aligned with the trade")
    elif features.get("agent_consensus") not in {None, "", "NO_TRADE", expected_agent}:
        negative.append("reasoning agents opposed the trade")
    if features.get("order_block_conflict"):
        negative.append("nearby order blocks conflicted")
    if features.get("event_risk_active"):
        negative.append("scheduled event risk was active")
    if excursions["max_favorable_excursion"] > excursions["max_adverse_excursion"] * 2:
        positive.append("favorable excursion dominated adverse excursion")
    elif excursions["max_adverse_excursion"] > excursions["max_favorable_excursion"]:
        negative.append("adverse excursion dominated favorable excursion")
    return list(dict.fromkeys(positive)), list(dict.fromkeys(negative))


def _counterfactual(
    review_type: str,
    outcome: dict[str, Any],
    features: dict[str, Any],
    positive: list[str],
    negative: list[str],
) -> dict[str, Any]:
    if review_type == "GOOD_TRADE":
        return {"preserve": positive, "avoid_changing": ["risk limits", "fresh-data checks", "protective bracket"]}
    return {
        "avoid_when": negative,
        "required_confirmation": [
            "fresh quote and trade stream",
            "tight spread and acceptable liquidity",
            "directional agent and playbook agreement",
        ],
        "observed_pattern": features.get("pattern_classification"),
        "exit_reason": outcome.get("exit_reason"),
    }


def _lesson_summary(
    review_type: str,
    outcome: dict[str, Any],
    setup_quality: float,
    positive: list[str],
    negative: list[str],
    exploration: bool,
) -> str:
    prefix = "Exploration trade" if exploration else "Trade"
    factors = positive[:3] if review_type == "GOOD_TRADE" else negative[:3]
    explanation = "; ".join(factors) or "insufficient contextual factors were recorded"
    return (
        f"{prefix} review={review_type} direction={outcome['direction']} exit={outcome.get('exit_reason')} "
        f"setup_quality={setup_quality:.3f}: {explanation}"
    )


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
