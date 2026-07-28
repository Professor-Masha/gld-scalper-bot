from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta
from typing import Any

from .config import Settings
from .database import Database
from .utils.math_utils import safe_div
from .utils.time_utils import ensure_utc, utc_now

MISSED_LONG = "MISSED_LONG"
MISSED_SHORT = "MISSED_SHORT"
VALID_NO_TRADE = "VALID_NO_TRADE"


class MissedOpportunityAnalyzer:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def label_matured_no_trades(self, now=None, limit: int = 100) -> dict[str, int]:
        now = ensure_utc(now or utc_now())
        cutoff = now - timedelta(minutes=self.settings.missed_opportunity_horizon_minutes)
        rows = self.database.fetch_unlabeled_no_trade_signals(before=cutoff, limit=limit)
        counts: Counter[str] = Counter()
        for row in rows:
            record = self._review_signal(row)
            if record is None:
                continue
            self.database.insert_missed_opportunity(record)
            self._insert_journal_review(record)
            counts[record["label"]] += 1
        return dict(counts)

    def _review_signal(self, row: dict[str, Any]) -> dict[str, Any] | None:
        signal_time = ensure_utc(row["timestamp"])
        horizon = int(self.settings.missed_opportunity_horizon_minutes)
        end_time = signal_time + timedelta(minutes=horizon)
        bars = self.database.fetch_bars(row["symbol"], self.settings.trade_timeframe, start=signal_time, end=end_time)
        future_bars = [bar for bar in bars if ensure_utc(bar["timestamp"]) > signal_time]
        if not future_bars:
            return None
        features = json.loads(row.get("feature_snapshot_json") or "{}")
        entry_price = _f(features.get("latest_price"), _f(features.get("close")))
        if entry_price <= 0:
            entry_price = _f(future_bars[0].get("open"), _f(future_bars[0].get("close")))
        if entry_price <= 0:
            return None
        future_high = max(_f(bar.get("high")) for bar in future_bars)
        future_low = min(_f(bar.get("low")) for bar in future_bars)
        up_move_pct = safe_div(future_high - entry_price, entry_price)
        down_move_pct = safe_div(entry_price - future_low, entry_price)
        min_move = self.settings.missed_opportunity_min_move_pct
        max_adverse = self.settings.missed_opportunity_max_adverse_pct

        clean_long = up_move_pct >= min_move and down_move_pct <= max_adverse
        clean_short = down_move_pct >= min_move and up_move_pct <= max_adverse
        if clean_long and (up_move_pct >= down_move_pct):
            label = MISSED_LONG
            favorable = up_move_pct
            adverse = down_move_pct
            review_reason = f"skipped NO_TRADE but price advanced {up_move_pct:.4%} within {horizon} minutes"
        elif clean_short:
            label = MISSED_SHORT
            favorable = down_move_pct
            adverse = up_move_pct
            review_reason = f"skipped NO_TRADE but price declined {down_move_pct:.4%} within {horizon} minutes"
        else:
            label = VALID_NO_TRADE
            favorable = max(up_move_pct, down_move_pct)
            adverse = min(up_move_pct, down_move_pct)
            review_reason = "skip validated; no clean directional move after signal"

        return {
            "signal_id": row.get("id"),
            "timestamp": signal_time,
            "symbol": row["symbol"],
            "decision": row.get("decision"),
            "label": label,
            "entry_price": entry_price,
            "horizon_minutes": horizon,
            "max_up_move_pct": up_move_pct,
            "max_down_move_pct": down_move_pct,
            "max_favorable_move_pct": favorable,
            "max_adverse_move_pct": adverse,
            "review_reason": review_reason,
            "feature_snapshot_json": features,
        }

    def _insert_journal_review(self, record: dict[str, Any]) -> None:
        features = dict(record.get("feature_snapshot_json") or {})
        self.database.insert_trading_journal(
            {
                "timestamp": utc_now(),
                "symbol": record["symbol"],
                "event_type": "MISSED_OPPORTUNITY_REVIEW",
                "decision": record.get("decision"),
                "confidence": features.get("confidence"),
                "bullish_score": features.get("bullish_score"),
                "bearish_score": features.get("bearish_score"),
                "no_trade_score": features.get("no_trade_score"),
                "regime": features.get("regime"),
                "reason": record["review_reason"],
                "status": record["label"],
                "price": record.get("entry_price"),
                "pattern_classification": features.get("pattern_classification"),
                "pattern_quality": features.get("pattern_quality"),
                "liquidity_score": features.get("liquidity_score"),
                "volatility_regime": features.get("gold_volatility_regime"),
                "missed_opportunity_label": record["label"],
                "reasoning_agents_json": features.get("agent_reasoning_json"),
                "feature_snapshot_json": features,
                "broker_snapshot_json": {
                    "max_up_move_pct": record["max_up_move_pct"],
                    "max_down_move_pct": record["max_down_move_pct"],
                    "horizon_minutes": record["horizon_minutes"],
                },
            }
        )


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
