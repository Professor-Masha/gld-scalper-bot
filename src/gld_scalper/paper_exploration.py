from __future__ import annotations

import hashlib
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings, load_settings
from .database import Database
from .models import MarketSignal
from .utils.math_utils import clamp
from .utils.time_utils import ensure_utc, market_session, utc_now


class PaperExplorationPolicy:
    """Select a few near-valid paper setups without weakening hard risk blocks."""

    def __init__(self, settings: Settings | None = None, database: Database | None = None) -> None:
        self.settings = settings or load_settings()
        self.database = database or Database(settings=self.settings)
        self._coverage_cache_at: datetime | None = None
        self._coverage_cache: dict[str, dict[str, int]] = {
            "direction": {},
            "playbook": {},
            "regime": {},
        }

    def maybe_select(
        self,
        signal: MarketSignal,
        features: dict,
        *,
        now: datetime | None = None,
    ) -> MarketSignal:
        now = ensure_utc(now or utc_now())
        if not self._eligible_mode(signal, now):
            return signal
        learning_mode = self.settings.paper_learning_mode
        direction = _learning_direction(signal, features, now, self.settings) if learning_mode else ("LONG" if signal.bullish_score > signal.bearish_score else "SHORT")
        if direction not in {"LONG", "SHORT"}:
            return signal
        directional_score = max(signal.bullish_score, signal.bearish_score)
        score_gap = abs(signal.bullish_score - signal.bearish_score)
        minimum_score = self.settings.paper_learning_min_score if learning_mode else self.settings.paper_exploration_min_score
        minimum_gap = self.settings.paper_learning_min_score_gap if learning_mode else self.settings.paper_exploration_min_score_gap
        if directional_score < minimum_score:
            return signal
        if score_gap < minimum_gap:
            return signal
        maximum_no_trade = (
            self.settings.paper_learning_max_no_trade_score
            if learning_mode
            else self.settings.paper_exploration_max_no_trade_score
        )
        if signal.no_trade_score > maximum_no_trade:
            return signal
        if direction == "SHORT" and (not self.settings.enable_shorts or not bool(features.get("is_shortable", True))):
            return signal
        if self._hard_block(features, direction, learning_mode=learning_mode):
            return signal
        if learning_mode:
            quality_allowed, quality_reason = exploration_playbook_quality(
                features,
                direction,
                self.settings,
                strategy_path="minute",
            )
            if not quality_allowed:
                features.update(
                    {
                        "exploration_candidate": True,
                        "controlled_exploration_selected": False,
                        "exploration_rejection_reason": quality_reason,
                    }
                )
                return signal
        start, end = _new_york_session_day(now)
        daily_limit = (
            self.settings.paper_learning_max_exploration_trades_per_day
            if learning_mode
            else self.settings.paper_exploration_max_trades_per_day
        )
        if daily_limit > 0 and self.database.count_paper_exploration_trades(start, end) >= daily_limit:
            return signal
        last = self.database.latest_paper_exploration_time()
        cooldown = timedelta(
            seconds=self.settings.paper_learning_exploration_cooldown_seconds
            if learning_mode
            else self.settings.paper_exploration_cooldown_minutes * 60
        )
        if last is not None and now < last + cooldown:
            return signal
        selection_probability = 1.0
        selection_bucket = 0.0
        priority_reasons: list[str] = []
        if learning_mode:
            selection_probability, priority_reasons = self._selection_probability(
                signal,
                features,
                direction,
                strategy_path="minute",
                now=now,
            )
            selection_bucket = _sample_bucket(
                signal.symbol,
                features,
                now,
                strategy_path="minute",
                bullish_score=signal.bullish_score,
                bearish_score=signal.bearish_score,
            )
            features.update(
                _selection_metadata(
                    selected=selection_bucket < selection_probability,
                    probability=selection_probability,
                    bucket=selection_bucket,
                    priority_reasons=priority_reasons,
                )
            )
            if selection_bucket >= selection_probability:
                return signal

        original_reason = signal.reason
        maximum_notional = (
            self.settings.paper_learning_exploration_max_notional
            if learning_mode
            else self.settings.paper_exploration_max_notional
        )
        exploration_features = dict(features)
        exploration_features.update(
            {
                "paper_exploration": True,
                "paper_exploration_direction": direction,
                "paper_exploration_score": directional_score,
                "paper_exploration_score_gap": score_gap,
                "paper_exploration_original_decision": signal.decision,
                "paper_exploration_original_reason": original_reason,
                "paper_exploration_max_notional": maximum_notional,
                "paper_learning_mode": learning_mode,
                "paper_learning_probe": "controlled_directional_probe" if learning_mode else None,
                "controlled_exploration_selected": True,
                "exploration_sample_rate": selection_probability if learning_mode else None,
                "exploration_selection_probability": selection_probability,
                "exploration_selection_bucket": selection_bucket,
                "exploration_priority_reasons": priority_reasons,
                "exploration_propensity_weight": round(1.0 / max(selection_probability, 0.01), 6),
            }
        )
        return MarketSignal(
            timestamp=signal.timestamp,
            symbol=signal.symbol,
            decision=direction,  # type: ignore[arg-type]
            bullish_score=signal.bullish_score,
            bearish_score=signal.bearish_score,
            no_trade_score=signal.no_trade_score,
            regime=signal.regime,
            confidence=round(clamp(directional_score / 100, 0.0, 1.0), 4),
            reason=(
                f"{'paper learning probe' if learning_mode else 'paper exploration'}: {direction.lower()} setup; "
                f"score={directional_score:.1f} gap={score_gap:.1f}; original={original_reason}"
            ),
            features=exploration_features,
            model_version=signal.model_version,
        )

    def maybe_select_fast(self, decision: Any, *, now: datetime | None = None) -> Any:
        """Promote a near-valid fast candidate into the paper-only exploration lane."""
        now = ensure_utc(now or getattr(decision, "timestamp", None) or utc_now())
        features = decision.features
        if not (
            self.settings.paper_learning_mode
            and self.settings.enable_paper_exploration
            and self.settings.alpaca_paper
            and self.settings.alpaca_paper_trade
            and self.settings.data_mode == "paper"
            and decision.decision == "NO_TRADE"
            and market_session(now, extended_hours=False) == "regular"
        ):
            return decision
        direction = str(features.get("fast_candidate_direction") or "NO_TRADE")
        score = float(features.get("fast_candidate_score") or decision.score or 0.0)
        if direction not in {"LONG", "SHORT"} or score < self.settings.paper_learning_fast_min_score:
            return decision
        if direction == "SHORT" and (not self.settings.enable_shorts or not bool(features.get("is_shortable", True))):
            return decision
        if self._hard_block(features, direction, learning_mode=True):
            return decision
        quality_allowed, quality_reason = exploration_playbook_quality(
            features,
            direction,
            self.settings,
            strategy_path="fast",
        )
        if not quality_allowed:
            features.update(
                {
                    "exploration_candidate": True,
                    "controlled_exploration_selected": False,
                    "exploration_rejection_reason": quality_reason,
                }
            )
            return decision
        start, end = _new_york_session_day(now)
        daily_limit = self.settings.paper_learning_max_exploration_trades_per_day
        if daily_limit > 0 and self.database.count_paper_exploration_trades(start, end) >= daily_limit:
            return decision
        last = self.database.latest_paper_exploration_time()
        cooldown = timedelta(seconds=self.settings.paper_learning_exploration_cooldown_seconds)
        if last is not None and now < last + cooldown:
            return decision
        pseudo_signal = MarketSignal(
            timestamp=now,
            symbol=decision.symbol,
            decision="NO_TRADE",
            bullish_score=score if direction == "LONG" else 0.0,
            bearish_score=score if direction == "SHORT" else 0.0,
            no_trade_score=100.0,
            regime=str(features.get("regime") or features.get("gold_volatility_regime") or "fast"),
            confidence=decision.confidence,
            reason=decision.reason,
            features=features,
        )
        selection_probability, priority_reasons = self._selection_probability(
            pseudo_signal,
            features,
            direction,
            strategy_path="fast",
            now=now,
        )
        selection_bucket = _sample_bucket(
            decision.symbol,
            features,
            now,
            strategy_path="fast",
            bullish_score=pseudo_signal.bullish_score,
            bearish_score=pseudo_signal.bearish_score,
        )
        selected = selection_bucket < selection_probability
        features.update(
            _selection_metadata(
                selected=selected,
                probability=selection_probability,
                bucket=selection_bucket,
                priority_reasons=priority_reasons,
            )
        )
        if not selected:
            return decision
        original_reason = decision.reason
        original_trigger = str(features.get("fast_candidate_trigger") or decision.trigger_type)
        features.update(
            {
                "paper_exploration": True,
                "paper_exploration_direction": direction,
                "paper_exploration_score": score,
                "paper_exploration_score_gap": score,
                "paper_exploration_original_decision": "NO_TRADE",
                "paper_exploration_original_reason": original_reason,
                "paper_exploration_original_trigger": original_trigger,
                "paper_exploration_max_notional": self.settings.paper_learning_exploration_max_notional,
                "paper_learning_mode": True,
                "paper_learning_probe": "controlled_fast_probe",
                "controlled_exploration_selected": True,
                "exploration_sample_rate": selection_probability,
                "exploration_selection_probability": selection_probability,
                "exploration_selection_bucket": selection_bucket,
                "exploration_priority_reasons": priority_reasons,
                "exploration_propensity_weight": round(1.0 / max(selection_probability, 0.01), 6),
            }
        )
        decision.decision = direction
        decision.trigger_type = original_trigger
        decision.allowed = True
        decision.score = score
        decision.confidence = round(clamp(score / 100.0, 0.0, 1.0), 4)
        decision.reason = (
            f"paper learning fast probe: {direction.lower()} {original_trigger}; "
            f"score={score:.1f}; original={original_reason}"
        )
        return decision

    def _eligible_mode(self, signal: MarketSignal, now: datetime) -> bool:
        return bool(
            self.settings.enable_paper_exploration
            and self.settings.alpaca_paper
            and self.settings.alpaca_paper_trade
            and self.settings.data_mode == "paper"
            and signal.decision == "NO_TRADE"
            and market_session(now, extended_hours=False) == "regular"
        )

    def _hard_block(self, features: dict, direction: str, *, learning_mode: bool) -> bool:
        if features.get("decision_council_hard_block"):
            return True
        spread = float(features.get("spread_pct") or 0.0)
        liquidity = float(features.get("liquidity_score") or 0.0)
        quote_age = _number_or_large(features.get("quote_age_seconds"))
        trade_age = _number_or_large(features.get("trade_age_seconds"))
        if self.settings.paper_require_ml_model and not features.get("ml_input_compatible"):
            return True
        if features.get("websocket_connected") is False or features.get("stream_stale"):
            return True
        if max(quote_age, trade_age) > self.settings.quote_stale_seconds:
            return True
        maximum_spread = self.settings.paper_learning_max_spread_pct if learning_mode else self.settings.paper_exploration_max_spread_pct
        if spread <= 0 or spread > min(self.settings.max_spread_pct, maximum_spread):
            return True
        if str(features.get("spread_regime") or "") == "wide":
            return True
        minimum_liquidity = self.settings.paper_learning_min_liquidity_score if learning_mode else self.settings.paper_exploration_min_liquidity_score
        if liquidity < minimum_liquidity:
            return True
        confirmed_news = _confirmed_news_playbook(features, direction)
        if (
            features.get("event_risk_active")
            or float(features.get("headline_event_risk") or 0.0) > 0.65
            or float(features.get("options_event_risk") or 0.0) >= 0.80
        ) and not confirmed_news:
            return True
        if (
            features.get("volatility_burst")
            and str(features.get("gold_volatility_regime") or "") == "high"
            and not confirmed_news
        ):
            return True
        regime = str(features.get("regime") or "")
        if regime in {"poor_liquidity", "high_volatility"}:
            return True
        if not learning_mode and regime in {"low_volatility", "sideways_chop"}:
            return True
        playbook_direction = str(features.get("playbook_direction") or "NO_TRADE")
        if playbook_direction != direction:
            return True
        if not learning_mode and features.get("playbook_allowed") is False:
            return True
        if features.get("order_block_conflict") and float(features.get("order_block_strength") or 0.0) >= 0.60:
            return True
        return False

    def _selection_probability(
        self,
        signal: MarketSignal,
        features: dict[str, Any],
        direction: str,
        *,
        strategy_path: str,
        now: datetime,
    ) -> tuple[float, list[str]]:
        base = self.settings.paper_learning_exploration_sample_rate
        if base <= 0.0:
            return 0.0, ["sampling_disabled"]
        if base >= 1.0:
            return 1.0, ["forced_exploration_sample"]
        bonuses = 0.0
        reasons: list[str] = []
        directional_score = max(signal.bullish_score, signal.bearish_score)
        if 50.0 <= directional_score <= 75.0:
            bonuses += 0.30
            reasons.append("decision_boundary_score")
        probabilities = sorted(
            (
                float(features.get("ml_probability_long") or 0.0),
                float(features.get("ml_probability_short") or 0.0),
                float(features.get("ml_probability_no_trade") or 0.0),
            ),
            reverse=True,
        )
        if probabilities[0] > 0.0 and probabilities[0] - probabilities[1] <= 0.15:
            bonuses += 0.35
            reasons.append("model_uncertainty")
        predicted_action = {
            "long_good": "LONG",
            "short_good": "SHORT",
            "no_trade": "NO_TRADE",
        }.get(str(features.get("ml_predicted_direction") or ""))
        if features.get("ml_participating") and predicted_action not in {None, direction}:
            bonuses += 0.30
            reasons.append("model_rule_disagreement")
        technical_direction = str(features.get("technical_direction") or "NO_TRADE")
        if technical_direction in {"LONG", "SHORT"} and technical_direction != direction:
            bonuses += 0.20
            reasons.append("indicator_direction_disagreement")
        required = int(features.get("playbook_required_confirmations") or 0)
        observed = int(features.get("playbook_confirmation_count") or 0)
        if required > 0 and observed == required - 1:
            bonuses += 0.30
            reasons.append("one_missing_playbook_confirmation")
        coverage = self._coverage_snapshot(now)
        direction_counts = coverage["direction"]
        playbook_counts = coverage["playbook"]
        if _is_underrepresented(direction_counts, direction):
            bonuses += 0.25
            reasons.append("underrepresented_direction")
        playbook = str(features.get("playbook") or "unknown")
        if _is_underrepresented(playbook_counts, playbook):
            bonuses += 0.25
            reasons.append("underrepresented_playbook")
        regime = str(features.get("regime") or features.get("gold_volatility_regime") or "unknown")
        if _is_underrepresented(coverage["regime"], regime):
            bonuses += 0.20
            reasons.append("underrepresented_regime")
        liquidity = float(features.get("liquidity_score") or 0.0)
        if self.settings.paper_learning_min_liquidity_score <= liquidity < self.settings.minimum_liquidity_score:
            bonuses += 0.10
            reasons.append("liquidity_boundary_sample")
        session_profile = _session_profile(now)
        if session_profile in {"open", "afternoon"}:
            bonuses += 0.10
            reasons.append(f"{session_profile}_session_sample")
        probability = clamp(base * (1.0 + bonuses), 0.0, 0.75)
        return round(probability, 6), reasons or ["base_exploration_sample"]

    def _coverage_snapshot(self, now: datetime) -> dict[str, dict[str, int]]:
        if self._coverage_cache_at is not None and now < self._coverage_cache_at + timedelta(minutes=5):
            return self._coverage_cache
        since = (now - timedelta(days=30)).isoformat()
        try:
            rows = self.database.conn.execute(
                """
                SELECT direction, COALESCE(playbook, 'unknown') AS playbook,
                       COALESCE(regime, 'unknown') AS regime, COUNT(*) AS count
                FROM trade_outcomes
                WHERE exploration_trade = 1 AND entry_time >= ?
                GROUP BY direction, COALESCE(playbook, 'unknown'), COALESCE(regime, 'unknown')
                """,
                (since,),
            ).fetchall()
        except Exception:
            rows = []
        direction_counts: dict[str, int] = {}
        playbook_counts: dict[str, int] = {}
        regime_counts: dict[str, int] = {}
        for row in rows:
            count = int(row["count"] or 0)
            direction = str(row["direction"] or "unknown")
            playbook = str(row["playbook"] or "unknown")
            regime = str(row["regime"] or "unknown")
            direction_counts[direction] = direction_counts.get(direction, 0) + count
            playbook_counts[playbook] = playbook_counts.get(playbook, 0) + count
            regime_counts[regime] = regime_counts.get(regime, 0) + count
        self._coverage_cache_at = now
        self._coverage_cache = {
            "direction": direction_counts,
            "playbook": playbook_counts,
            "regime": regime_counts,
        }
        return self._coverage_cache


def exploration_playbook_quality(
    features: dict[str, Any],
    direction: str,
    settings: Settings,
    *,
    strategy_path: str,
) -> tuple[bool, str]:
    """Apply relaxed-but-structured playbook rules only to paper exploration."""
    playbook = str(features.get("playbook") or "")
    playbook_direction = str(features.get("playbook_direction") or "NO_TRADE")
    playbook_score = float(features.get("playbook_score") or 0.0)
    liquidity = float(features.get("liquidity_score") or 0.0)
    pattern_quality = float(features.get("pattern_quality") or 0.0)
    required = int(features.get("playbook_required_confirmations") or 0)
    observed = int(features.get("playbook_confirmation_count") or 0)
    blocks: list[str] = []
    known_playbooks = {
        "proper_breakout",
        "buildup_break",
        "pullback_continuation",
        "trend_continuation",
        "false_break_reversal",
        "compression_breakout",
        "spread_capture",
        "news_event",
        "ema_cross_filtered",
    }
    if playbook not in known_playbooks:
        blocks.append("exploration requires a recognized playbook")
    if playbook_direction != direction:
        blocks.append("exploration playbook direction disagrees")
    if playbook_score < settings.paper_learning_min_playbook_score:
        blocks.append(
            f"exploration playbook score {playbook_score:.1f} below "
            f"{settings.paper_learning_min_playbook_score:.1f}"
        )
    if liquidity < settings.paper_learning_min_liquidity_score:
        blocks.append("exploration liquidity below minimum")
    pattern_required = playbook in {
        "proper_breakout",
        "buildup_break",
        "pullback_continuation",
        "false_break_reversal",
        "compression_breakout",
    }
    if pattern_required and pattern_quality < settings.paper_learning_min_pattern_quality:
        blocks.append("exploration pattern quality below minimum")
    if required > 0 and observed < max(1, required - 1):
        blocks.append(f"exploration confirmations {observed}/{required}; at most one may be missing")
    if playbook in {"proper_breakout", "buildup_break", "compression_breakout"} and not features.get("proper_break"):
        blocks.append("exploration breakout requires a proper break")
    pattern = str(features.get("pattern_classification") or "")
    if playbook == "false_break_reversal" and not (
        pattern.startswith("false_break") or features.get("false_break")
    ):
        blocks.append("false-break exploration requires a returned-to-range false break")
    if playbook == "pullback_continuation" and not pattern.startswith("pullback"):
        blocks.append("pullback exploration requires a pullback pattern")
    if playbook == "spread_capture" and strategy_path != "fast":
        blocks.append("spread-capture exploration is restricted to the fast path")
    if (
        playbook == "spread_capture"
        and float(features.get("spread_pct") or 0.0) > settings.fast_scalp_tight_spread_pct
    ):
        blocks.append("spread-capture exploration requires the normal tight-spread contract")
    if playbook == "news_event" and not _confirmed_news_playbook(features, direction):
        blocks.append("news exploration requires a confirmed post-release news playbook")
    return not blocks, "; ".join(blocks) or "paper exploration quality confirmed"


def model_rejection_blocks(settings: Settings, features: dict, rejects_trade: bool) -> bool:
    if not rejects_trade:
        return False
    return not bool(
        settings.paper_learning_mode
        and settings.paper_learning_ignore_model_rejection
        and settings.alpaca_paper
        and settings.alpaca_paper_trade
        and settings.data_mode == "paper"
        and features.get("controlled_exploration_selected") is True
    )


def _learning_direction(signal: MarketSignal, features: dict, now: datetime, settings: Settings) -> str:
    confidence = float(features.get("ml_confidence") or 0.0)
    probability_long = float(features.get("ml_probability_long") or 0.0)
    probability_short = float(features.get("ml_probability_short") or 0.0)
    margin = abs(probability_long - probability_short)
    predicted = str(features.get("ml_predicted_direction") or "")
    if (
        features.get("ml_participating")
        and features.get("ml_advice_eligible")
        and confidence >= settings.paper_ml_min_advisory_confidence
        and margin >= settings.ml_min_probability_margin
    ):
        if predicted == "long_good":
            return "LONG"
        if predicted == "short_good":
            return "SHORT"
    if signal.bullish_score > signal.bearish_score:
        return "LONG"
    if signal.bearish_score > signal.bullish_score:
        return "SHORT"
    evidence = (
        float(features.get("quote_imbalance") or 0.0)
        + float(features.get("micro_price_pressure") or 0.0)
        + float(features.get("return_1m") or 0.0) * 100.0
    )
    if evidence > 0:
        return "LONG"
    if evidence < 0:
        return "SHORT"
    return "NO_TRADE"


def _sample_bucket(
    symbol: str,
    features: dict[str, Any],
    now: datetime,
    *,
    strategy_path: str,
    bullish_score: float,
    bearish_score: float,
) -> float:
    key = "|".join(
        (
            symbol,
            ensure_utc(now).replace(microsecond=0).isoformat(),
            strategy_path,
            str(features.get("playbook") or "unknown"),
            str(features.get("regime") or features.get("gold_volatility_regime") or "unknown"),
            f"{bullish_score:.2f}",
            f"{bearish_score:.2f}",
        )
    )
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF


def _selection_metadata(
    *,
    selected: bool,
    probability: float,
    bucket: float,
    priority_reasons: list[str],
) -> dict[str, Any]:
    return {
        "exploration_candidate": True,
        "controlled_exploration_selected": selected,
        "exploration_sample_rate": probability,
        "exploration_selection_probability": probability,
        "exploration_selection_bucket": round(bucket, 8),
        "exploration_priority_reasons": list(priority_reasons),
        "exploration_propensity_weight": round(1.0 / max(probability, 0.01), 6),
    }


def _confirmed_news_playbook(features: dict[str, Any], direction: str) -> bool:
    if str(features.get("playbook") or "") != "news_event":
        return False
    if str(features.get("playbook_direction") or "NO_TRADE") != direction:
        return False
    post_release = bool(features.get("event_post_release") or features.get("news_event_post_release"))
    required = int(features.get("playbook_required_confirmations") or 0)
    observed = int(features.get("playbook_confirmation_count") or 0)
    return bool(
        post_release
        and features.get("volatility_burst")
        and observed >= max(1, required - 1)
    )


def _is_underrepresented(counts: dict[str, int], key: str) -> bool:
    if not counts:
        return True
    maximum = max(counts.values(), default=0)
    return counts.get(key, 0) <= max(1, int(maximum * 0.60))


def _session_profile(now: datetime) -> str:
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


def _new_york_session_day(now: datetime) -> tuple[datetime, datetime]:
    eastern = ensure_utc(now).astimezone(ZoneInfo("America/New_York"))
    start_local = datetime.combine(eastern.date(), time.min, tzinfo=eastern.tzinfo)
    return start_local.astimezone(ZoneInfo("UTC")), (start_local + timedelta(days=1)).astimezone(ZoneInfo("UTC"))


def _number_or_large(value) -> float:
    try:
        return float(value) if value is not None else 1_000_000.0
    except (TypeError, ValueError):
        return 1_000_000.0
