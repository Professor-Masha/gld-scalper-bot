from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import Settings, load_settings
from .models import MarketSignal
from .regime_detector import AVOID_REGIMES, detect_regime
from .utils.math_utils import clamp
from .utils.time_utils import utc_now


class StrategyEngine:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()

    def evaluate(
        self,
        features: dict[str, Any],
        *,
        symbol: str = "GLD",
        now: datetime | None = None,
        has_champion_model: bool = False,
    ) -> MarketSignal:
        now = now or utc_now()
        if not features:
            return MarketSignal(now, symbol, "NO_TRADE", 0.0, 0.0, 100.0, "unclear", 0.0, "missing features", {})

        regime = detect_regime(features)
        bullish_score, bullish_reasons = self._bullish_score(features)
        bearish_score, bearish_reasons = self._bearish_score(features)
        no_trade_score, no_trade_reasons = self._no_trade_score(features, bullish_score, bearish_score, regime, has_champion_model)

        decision = "NO_TRADE"
        reason = "; ".join(no_trade_reasons) or "setup not strong enough"
        confidence = clamp(max(bullish_score, bearish_score) / 100, 0.0, 1.0)

        if (
            bullish_score >= self.settings.bullish_threshold
            and bearish_score <= self.settings.opposing_score_max
            and no_trade_score < self.settings.no_trade_score_max
        ):
            decision = "LONG"
            reason = "; ".join(bullish_reasons)
        elif (
            bearish_score >= self.settings.bearish_threshold
            and bullish_score <= self.settings.opposing_score_max
            and no_trade_score < self.settings.no_trade_score_max
        ):
            if self.settings.enable_shorts and bool(features.get("is_shortable", True)):
                decision = "SHORT"
                reason = "; ".join(bearish_reasons)
            else:
                reason = "short setup present but GLD is not shortable or shorts are disabled"

        if regime in {"poor_liquidity", "high_volatility", "low_volatility"} and decision != "NO_TRADE":
            decision = "NO_TRADE"
            no_trade_score = max(no_trade_score, 80.0)
            reason = f"regime blocked: {regime}"
        if decision != "NO_TRADE" and not has_champion_model and max(bullish_score, bearish_score) < self.settings.extreme_rule_score_without_model:
            decision = "NO_TRADE"
            no_trade_score = max(no_trade_score, self.settings.no_trade_score_max)
            reason = "no champion model and rule score is not extreme"
        if decision != "NO_TRADE" and self.settings.enable_strict_playbooks:
            playbook_direction = str(features.get("playbook_direction") or "NO_TRADE")
            if features.get("playbook_allowed") is False:
                decision = "NO_TRADE"
                no_trade_score = max(no_trade_score, self.settings.no_trade_score_max)
                reason = f"playbook blocked: {features.get('playbook_block_reason') or 'not allowed'}"
            elif playbook_direction not in {decision, "NO_TRADE"}:
                decision = "NO_TRADE"
                no_trade_score = max(no_trade_score, self.settings.no_trade_score_max)
                reason = f"playbook direction {playbook_direction} disagrees with signal"

        return MarketSignal(
            timestamp=now,
            symbol=symbol.upper(),
            decision=decision,
            bullish_score=round(clamp(bullish_score, 0, 100), 2),
            bearish_score=round(clamp(bearish_score, 0, 100), 2),
            no_trade_score=round(clamp(no_trade_score, 0, 100), 2),
            regime=regime,
            confidence=round(confidence, 4),
            reason=reason,
            features=dict(features),
        )

    def _bullish_score(self, f: dict[str, Any]) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        score = self._add(score, reasons, _gt(f, "ema_9", "ema_21"), 14, "1m EMA 9 above EMA 21")
        score = self._add(score, reasons, _gt(f, "sma_20", "sma_50"), 8, "SMA 20 above SMA 50")
        score = self._add(score, reasons, _gt(f, "tf5_ema_9", "tf5_ema_21"), 10, "5m EMA confirms")
        score = self._add(score, reasons, _gt(f, "close", "vwap"), 10, "price above VWAP")
        score = self._add(score, reasons, _gt(f, "close", "ema_50"), 8, "price above EMA 50")
        score = self._add(score, reasons, _f(f.get("macd_histogram_slope")) > 0 and _f(f.get("macd_histogram")) >= 0, 8, "MACD histogram rising")
        score = self._add(score, reasons, 50 <= _f(f.get("rsi_14"), 50) <= 75, 8, "RSI bullish but not extreme")
        score = self._add(score, reasons, f.get("rsi_divergence") == "bullish" and _f(f.get("rsi_divergence_strength")) >= 0.25, 6, "confirmed bullish RSI divergence")
        score = self._add(score, reasons, _f(f.get("relative_volume"), 1) >= 1.0, 7, "relative volume healthy")
        score = self._add(score, reasons, _f(f.get("spread_pct")) <= self.settings.max_spread_pct, 7, "spread tight")
        score = self._add(score, reasons, 0.0004 <= _f(f.get("atr_pct")) <= 0.01, 7, "ATR healthy")
        score = self._add(score, reasons, bool(f.get("breakout_candle")) or _gt(f, "close", "recent_high_20", inclusive=True), 8, "recent high breakout")
        score = self._add(score, reasons, f.get("pattern_classification") == "proper_break_up", 12, "proper price-action break up")
        score = self._add(score, reasons, f.get("pattern_classification") == "pullback_up", 8, "bullish pullback setup")
        score = self._add(score, reasons, bool(f.get("buildup_detected")) and f.get("buildup_side") == "resistance", 6, "buildup below resistance")
        score = self._add(score, reasons, _f(f.get("liquidity_score"), 0.5) >= 0.65, 4, "liquidity score healthy")
        score = self._add(score, reasons, f.get("agent_consensus") == "LONG", 5, "reasoning agents confirm long")
        score = self._add(score, reasons, not (_lt(f, "tf15_ema_9", "tf15_ema_21") and _lt(f, "tf15_close", "tf15_vwap")), 5, "15m trend does not oppose")
        score = self._add(score, reasons, _f(f.get("UUP_roc")) <= 0.001, 3, "UUP not strongly rising")
        score = self._add(score, reasons, _f(f.get("IAU_roc")) >= -0.001 or _f(f.get("GDX_roc")) >= -0.001, 3, "gold context not negative")
        score = self._add(score, reasons, f.get("macro_bias") == "bullish_gold_environment", min(self.settings.macro_context_max_live_score_adjustment, 3.0), "macro context mildly supports long")
        score = self._add(score, reasons, f.get("order_block_retest_direction") == "bullish" and _f(f.get("order_block_strength")) >= 0.60, 6, "bullish order-block retest")
        score = self._add(score, reasons, f.get("order_block_bias") == "bullish" and _f(f.get("order_block_alignment")) >= 0.55, 3, "multi-timeframe order blocks support long")
        options_adjustment = clamp(_f(f.get("options_score_adjustment")), 0.0, self.settings.options_max_score_adjustment)
        score = self._add(score, reasons, not f.get("options_stale") and _f(f.get("options_confidence")) >= 0.35 and options_adjustment > 0, options_adjustment, "GLD options context mildly supports long")
        ml_adjustment = self._paper_ml_adjustment(f, "long_good")
        score = self._add(score, reasons, ml_adjustment > 0, ml_adjustment, "paper ML adviser supports long")
        score = self._add(score, reasons, _f(f.get("similar_setup_win_rate"), 0.5) >= 0.55, 2, "recent similar setups positive")
        score = self._add(
            score,
            reasons,
            f.get("technical_direction") == "LONG" and _f(f.get("technical_quality")) >= 0.52,
            min(6.0, _f(f.get("technical_quality")) * 6.0),
            "grouped technical-confluence route supports long",
        )
        return score, reasons

    def _bearish_score(self, f: dict[str, Any]) -> tuple[float, list[str]]:
        score = 0.0
        reasons: list[str] = []
        score = self._add(score, reasons, _lt(f, "ema_9", "ema_21"), 14, "1m EMA 9 below EMA 21")
        score = self._add(score, reasons, _lt(f, "sma_20", "sma_50"), 8, "SMA 20 below SMA 50")
        score = self._add(score, reasons, _lt(f, "tf5_ema_9", "tf5_ema_21"), 10, "5m EMA confirms")
        score = self._add(score, reasons, _lt(f, "close", "vwap"), 10, "price below VWAP")
        score = self._add(score, reasons, _lt(f, "close", "ema_50"), 8, "price below EMA 50")
        score = self._add(score, reasons, _f(f.get("macd_histogram_slope")) < 0 and _f(f.get("macd_histogram")) <= 0, 8, "MACD histogram falling")
        score = self._add(score, reasons, 25 <= _f(f.get("rsi_14"), 50) <= 50, 8, "RSI bearish but not extreme")
        score = self._add(score, reasons, f.get("rsi_divergence") == "bearish" and _f(f.get("rsi_divergence_strength")) >= 0.25, 6, "confirmed bearish RSI divergence")
        score = self._add(score, reasons, _f(f.get("relative_volume"), 1) >= 1.0, 7, "relative volume healthy")
        score = self._add(score, reasons, _f(f.get("spread_pct")) <= self.settings.max_spread_pct, 7, "spread tight")
        score = self._add(score, reasons, 0.0004 <= _f(f.get("atr_pct")) <= 0.01, 7, "ATR healthy")
        score = self._add(score, reasons, bool(f.get("breakdown_candle")) or _lt(f, "close", "recent_low_20", inclusive=True), 8, "recent low breakdown")
        score = self._add(score, reasons, f.get("pattern_classification") == "proper_break_down", 12, "proper price-action break down")
        score = self._add(score, reasons, f.get("pattern_classification") == "pullback_down", 8, "bearish pullback setup")
        score = self._add(score, reasons, bool(f.get("buildup_detected")) and f.get("buildup_side") == "support", 6, "buildup above support")
        score = self._add(score, reasons, _f(f.get("liquidity_score"), 0.5) >= 0.65, 4, "liquidity score healthy")
        score = self._add(score, reasons, f.get("agent_consensus") == "SHORT", 5, "reasoning agents confirm short")
        score = self._add(score, reasons, not (_gt(f, "tf15_ema_9", "tf15_ema_21") and _gt(f, "tf15_close", "tf15_vwap")), 5, "15m trend does not oppose")
        score = self._add(score, reasons, _f(f.get("UUP_roc")) >= -0.001, 3, "UUP confirms or neutral")
        score = self._add(score, reasons, bool(f.get("is_shortable", True)), 3, "GLD marked shortable")
        score = self._add(score, reasons, f.get("macro_bias") == "bearish_gold_environment", min(self.settings.macro_context_max_live_score_adjustment, 3.0), "macro context mildly supports short")
        score = self._add(score, reasons, f.get("order_block_retest_direction") == "bearish" and _f(f.get("order_block_strength")) >= 0.60, 6, "bearish order-block retest")
        score = self._add(score, reasons, f.get("order_block_bias") == "bearish" and _f(f.get("order_block_alignment")) >= 0.55, 3, "multi-timeframe order blocks support short")
        options_adjustment = clamp(-_f(f.get("options_score_adjustment")), 0.0, self.settings.options_max_score_adjustment)
        score = self._add(score, reasons, not f.get("options_stale") and _f(f.get("options_confidence")) >= 0.35 and options_adjustment > 0, options_adjustment, "GLD options context mildly supports short")
        ml_adjustment = self._paper_ml_adjustment(f, "short_good")
        score = self._add(score, reasons, ml_adjustment > 0, ml_adjustment, "paper ML adviser supports short")
        score = self._add(score, reasons, _f(f.get("similar_setup_win_rate"), 0.5) >= 0.55, 2, "recent similar setups positive")
        score = self._add(
            score,
            reasons,
            f.get("technical_direction") == "SHORT" and _f(f.get("technical_quality")) >= 0.52,
            min(6.0, _f(f.get("technical_quality")) * 6.0),
            "grouped technical-confluence route supports short",
        )
        return score, reasons

    def _no_trade_score(self, f: dict[str, Any], bullish: float, bearish: float, regime: str, has_champion_model: bool) -> tuple[float, list[str]]:
        score = 5.0
        reasons: list[str] = []
        if abs(bullish - bearish) < 15:
            score += 25
            reasons.append("bullish and bearish scores are close")
        if _f(f.get("spread_pct")) > self.settings.max_spread_pct:
            score += 35
            reasons.append("spread too wide")
        if f.get("spread_regime") == "wide":
            score += 30
            reasons.append("spread regime wide")
        liquidity_score = _f(f.get("liquidity_score"), 1.0)
        if liquidity_score < 0.40 and str(f.get("spread_regime") or "") != "unknown":
            score += 25
            reasons.append("liquidity score weak")
        if _f(f.get("relative_volume"), 1.0) < 0.70:
            score += 20
            reasons.append("volume too low")
        atr_pct = _f(f.get("atr_pct"))
        if atr_pct and atr_pct < 0.00035:
            score += 20
            reasons.append("ATR too low")
        if atr_pct > 0.012:
            score += 25
            reasons.append("ATR too high")
        if abs(_f(f.get("vwap_deviation"))) < 0.0005:
            score += 12
            reasons.append("price chopping around VWAP")
        rsi = _f(f.get("rsi_14"), 50)
        if rsi >= 78 or rsi <= 22:
            score += 15
            reasons.append("RSI extreme")
        if (_gt(f, "ema_9", "ema_21") and _lt(f, "tf5_ema_9", "tf5_ema_21")) or (_lt(f, "ema_9", "ema_21") and _gt(f, "tf5_ema_9", "tf5_ema_21")):
            score += 25
            reasons.append("1m and 5m disagree")
        if regime in AVOID_REGIMES:
            score += 20
            reasons.append(f"avoid regime: {regime}")
        pattern = str(f.get("pattern_classification") or "")
        if pattern.startswith("false_break"):
            score += 35
            reasons.append("false break detected")
        if pattern.startswith("tease_break"):
            score += 22
            reasons.append("tease break detected")
        if f.get("range_compression") and not f.get("proper_break"):
            score += 10
            reasons.append("compressed range needs a proper break")
        if f.get("volatility_burst"):
            score += 25
            reasons.append("volatility burst")
        gold_vol_regime = str(f.get("gold_volatility_regime") or "")
        if gold_vol_regime in {"closed", "low", "high"}:
            score += 15
            reasons.append(f"gold volatility regime: {gold_vol_regime}")
        if f.get("agent_consensus") == "NO_TRADE" and _f(f.get("agent_confidence")) >= 0.25:
            score += 15
            reasons.append("reasoning agents prefer no trade")
        if f.get("macro_bias") == "event_risk_environment":
            score += 8
            reasons.append("macro event-risk environment")
        if _f(f.get("headline_event_risk")) > 0.75:
            score += 8
            reasons.append("headline event risk elevated")
        if f.get("event_risk_active"):
            score += 35
            reasons.append(str(f.get("event_risk_reason") or "scheduled event risk active"))
        if f.get("order_block_conflict") and _f(f.get("order_block_strength")) >= 0.60:
            score += 10
            reasons.append("nearby order blocks conflict")
        if not f.get("options_stale") and _f(f.get("options_confidence")) >= 0.40 and _f(f.get("options_event_risk")) >= 0.80:
            score += 8
            reasons.append("options-implied event risk elevated")
        if self.settings.enable_strict_playbooks:
            if f.get("playbook_allowed") is False:
                score += 35
                reasons.append(str(f.get("playbook_block_reason") or "playbook blocked"))
            if _f(f.get("playbook_score")) < self.settings.minimum_playbook_score:
                score += 20
                reasons.append("playbook score too low")
            if (
                str(f.get("playbook") or "") in {"proper_breakout", "buildup_break", "pullback_continuation"}
                and _f(f.get("pattern_quality")) < self.settings.minimum_pattern_quality
            ):
                score += 15
                reasons.append("pattern quality too low")
        if self.settings.paper_require_ml_model and not f.get("ml_input_compatible"):
            score += 100
            reasons.append("required ML feature profile unavailable")
        if self._paper_ml_adjustment(f, "no_trade") > 0:
            score += self._paper_ml_adjustment(f, "no_trade")
            reasons.append("paper ML adviser prefers no trade")
        if not has_champion_model and max(bullish, bearish) < self.settings.extreme_rule_score_without_model:
            score += 20
            reasons.append("no champion model and rule score is not extreme")
        bar_age = _f(f.get("data_age_seconds"))
        quote_age = _f(f.get("quote_age_seconds"), 1_000_000.0)
        trade_age = _f(f.get("trade_age_seconds"), 1_000_000.0)
        if bar_age > self.settings.bar_stale_seconds and min(quote_age, trade_age) > self.settings.quote_stale_seconds:
            score += 35
            reasons.append("market data stale")
        if f.get("websocket_connected") is False:
            score += 35
            reasons.append("websocket disconnected")
        if f.get("stream_stale") is True:
            score += 35
            stale_reason = f.get("stream_stale_reason") or "live stream stale"
            reasons.append(str(stale_reason))
        if not reasons:
            reasons.append("wait")
        return score, reasons

    def _paper_ml_adjustment(self, features: dict[str, Any], predicted: str) -> float:
        if not (
            self.settings.paper_learning_mode
            and features.get("ml_participating")
            and features.get("ml_model_role") in {"paper_shadow", "champion"}
            and features.get("ml_advice_eligible")
            and features.get("ml_predicted_direction") == predicted
        ):
            return 0.0
        confidence = _f(features.get("ml_confidence"))
        if confidence < self.settings.paper_ml_min_advisory_confidence:
            return 0.0
        return clamp(
            confidence * self.settings.paper_ml_max_score_adjustment,
            0.0,
            self.settings.paper_ml_max_score_adjustment,
        )

    @staticmethod
    def _add(score: float, reasons: list[str], condition: bool, points: float, reason: str) -> float:
        if condition:
            reasons.append(reason)
            return score + points
        return score


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _gt(features: dict[str, Any], left: str, right: str, *, inclusive: bool = False) -> bool:
    l_value = _f(features.get(left))
    r_value = _f(features.get(right))
    return l_value >= r_value if inclusive else l_value > r_value


def _lt(features: dict[str, Any], left: str, right: str, *, inclusive: bool = False) -> bool:
    l_value = _f(features.get(left))
    r_value = _f(features.get(right))
    return l_value <= r_value if inclusive else l_value < r_value
