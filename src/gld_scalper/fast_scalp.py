from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import TYPE_CHECKING, Any

from .alpaca_clients import get_trading_client
from .config import Settings, load_settings
from .decision_council import run_decision_council
from .concurrent_trading import populate_concurrent_risk_state
from .database import Database
from .entry_quality import EntryCooldownPolicy, EntryQualityGate, time_of_day_profile
from .execution_engine import ExecutionEngine, make_client_order_id
from .execution_safety import EntryBlockedError
from .models import MarketSignal, MLPrediction
from .ml.predictor import Predictor
from .ml.transformer_authority import apply_transformer_to_fast_decision
from .order_blocks import live_order_block_retest
from .paper_exploration import PaperExplorationPolicy, model_rejection_blocks
from .risk_engine import OrderPlanRejected, RiskEngine
from .utils.math_utils import clamp, safe_div
from .utils.time_utils import ensure_utc, market_session, utc_now

if TYPE_CHECKING:
    from .execution_safety import OrderIntentCoordinator
    from .ml.transformer_runtime import AsyncTransformerShadowRuntime

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FastScalpEvent:
    event_type: str
    payload: dict[str, Any]
    received_at: datetime
    enqueued_ns: int = field(default_factory=time.perf_counter_ns)


@dataclass(slots=True)
class FastScalpDecision:
    timestamp: datetime
    symbol: str
    decision: str
    trigger_type: str
    allowed: bool
    confidence: float
    score: float
    reason: str
    latency_ms: float
    features: dict[str, Any] = field(default_factory=dict)

    @property
    def is_trade(self) -> bool:
        return self.allowed and self.decision in {"LONG", "SHORT"}


class FastScalpEngine:
    """In-memory event engine for sub-minute GLD microstructure decisions.

    This class deliberately avoids database and broker calls. It only consumes
    quote/trade events and returns a decision when the minimum event interval is
    due. Broker/risk work belongs to FastScalpRuntime.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.quotes: deque[dict[str, Any]] = deque(maxlen=2_000)
        self.trades: deque[dict[str, Any]] = deque(maxlen=4_000)
        self.context_features: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._last_eval_monotonic = 0.0
        self._last_break: dict[str, Any] | None = None

    def update_context(self, features: dict[str, Any]) -> None:
        keep = {
            "atr_14",
            "atr_pct",
            "relative_volume",
            "macro_bias",
            "macro_confidence",
            "headline_event_risk",
            "event_risk_active",
            "event_risk_reason",
            "event_post_release",
            "news_event_post_release",
            "regime",
            "gold_volatility_regime",
            "stream_stale",
            "websocket_connected",
            "target_exposure_pct",
            "is_shortable",
            "order_block_bias",
            "order_block_strength",
            "order_block_direction",
            "order_block_zone_low",
            "order_block_zone_high",
            "order_block_conflict",
            "options_bias",
            "options_raw_score",
            "options_confidence",
            "options_score_adjustment",
            "options_event_risk",
            "options_stale",
            "options_reason",
            "paper_learning_mode",
            "breakout_20_high",
            "breakout_20_low",
            "compression_20",
            "log_trade_count",
            "minute_cos",
            "minute_sin",
            "quote_missing",
            "range_1m_pct",
            "realized_volatility_20",
            "return_1m",
            "volume_ratio_20",
            "technical_direction",
            "technical_quality",
            "technical_big3_aligned",
            "technical_route",
            "event_gold_confirmed",
            "event_gold_direction",
            "pattern_classification",
            "pattern_quality",
            "proper_break",
            "false_break",
            "playbook_allowed",
            "tradingagents_advisory_available",
            "tradingagents_advisory_stale",
            "tradingagents_advisory_bias",
            "tradingagents_advisory_confidence",
            "tradingagents_advisory_abstain",
            "tradingagents_advisory_event_risk",
            "tradingagents_advisory_score_adjustment",
            "tradingagents_advisory_size_multiplier",
        }
        with self._lock:
            self.context_features.update({key: features.get(key) for key in keep if key in features})

    def on_event(self, event_type: str, payload: dict[str, Any], received_at: datetime | None = None) -> FastScalpDecision | None:
        started = time.perf_counter()
        now = ensure_utc(received_at or utc_now())
        with self._lock:
            if event_type == "quote":
                quote = _normalize_quote(payload, now)
                if quote is not None:
                    self.quotes.append(quote)
            elif event_type == "trade":
                trade = _normalize_trade(payload, now)
                if trade is not None:
                    self.trades.append(trade)
            else:
                return None
            if not self._eval_due(started):
                return None
            self._last_eval_monotonic = started
            self._trim(now)
            features = self._features(now)
            if not features:
                return None
            decision = self._decide(now, features)
            decision.latency_ms = round((time.perf_counter() - started) * 1_000, 3)
            return decision

    def _eval_due(self, monotonic_now: float) -> bool:
        interval = max(self.settings.fast_scalp_interval_ms, 0) / 1_000
        return monotonic_now - self._last_eval_monotonic >= interval

    def _trim(self, now: datetime) -> None:
        keep_after = now - timedelta(seconds=max(self.settings.fast_scalp_lookback_seconds * 4, 20))
        while self.quotes and ensure_utc(self.quotes[0]["received_at"]) < keep_after:
            self.quotes.popleft()
        while self.trades and ensure_utc(self.trades[0]["received_at"]) < keep_after:
            self.trades.popleft()

    def _features(self, now: datetime) -> dict[str, Any]:
        if len(self.quotes) < self.settings.fast_scalp_min_quote_count:
            return {}
        quote = self.quotes[-1]
        bid = float(quote["bid_price"])
        ask = float(quote["ask_price"])
        if bid <= 0 or ask <= 0 or ask < bid:
            return {}
        midpoint = (bid + ask) / 2
        spread = ask - bid
        spread_pct = safe_div(spread, midpoint)
        bid_size = float(quote.get("bid_size") or 0.0)
        ask_size = float(quote.get("ask_size") or 0.0)
        quote_imbalance = safe_div(bid_size - ask_size, bid_size + ask_size)
        lookback = now - timedelta(seconds=self.settings.fast_scalp_lookback_seconds)
        recent_quotes = [item for item in self.quotes if ensure_utc(item["received_at"]) >= lookback]
        recent_trades = [item for item in self.trades if ensure_utc(item["received_at"]) >= lookback]
        if len(recent_trades) < self.settings.fast_scalp_min_trade_count:
            return {}
        mids = [float(item["midpoint"]) for item in recent_quotes if float(item.get("midpoint") or 0.0) > 0]
        spreads = [float(item["spread_pct"]) for item in recent_quotes if item.get("spread_pct") is not None]
        trade_intensity = len(recent_trades) / max(self.settings.fast_scalp_lookback_seconds, 1)
        aggressive_buy_volume = sum(float(item.get("size") or 0.0) for item in recent_trades if float(item.get("price") or 0.0) >= ask)
        aggressive_sell_volume = sum(float(item.get("size") or 0.0) for item in recent_trades if float(item.get("price") or 0.0) <= bid)
        signed_volume = aggressive_buy_volume - aggressive_sell_volume
        pressure = safe_div(signed_volume, aggressive_buy_volume + aggressive_sell_volume)
        recent_high = max(mids) if mids else midpoint
        recent_low = min(mids) if mids else midpoint
        prior_mids = mids[:-1] if len(mids) > 1 else mids
        prior_high = max(prior_mids) if prior_mids else midpoint
        prior_low = min(prior_mids) if prior_mids else midpoint
        midpoint_move_pct = safe_div(midpoint - mids[0], mids[0]) if mids else 0.0
        realized_range_pct = safe_div(recent_high - recent_low, midpoint)
        median_spread = median(spreads) if spreads else spread_pct
        spread_expansion_ratio = safe_div(spread_pct, median_spread, default=1.0)
        spread_stability_score = clamp(1.0 - abs(spread_expansion_ratio - 1.0), 0.0, 1.0)
        spread_regime = "tight" if spread_pct <= self.settings.fast_scalp_tight_spread_pct else "wide" if spread_pct > self.settings.max_spread_pct else "normal"
        liquidity_score = clamp(
            1.0
            - clamp(safe_div(spread_pct, max(self.settings.max_spread_pct, 0.000001)), 0.0, 1.0) * 0.55
            + clamp((bid_size + ask_size) / 5_000, 0.0, 0.25)
            - clamp(spread_expansion_ratio - 1.0, 0.0, 1.0) * 0.20,
            0.0,
            1.0,
        )
        volatility_burst = realized_range_pct >= self.settings.fast_scalp_volatility_burst_pct
        quote_age = (now - ensure_utc(quote["timestamp"])).total_seconds()
        last_trade = self.trades[-1] if self.trades else None
        trade_age = (now - ensure_utc(last_trade["timestamp"])).total_seconds() if last_trade else 1_000_000.0
        order_block_live_retest, order_block_live_direction = live_order_block_retest(
            self.context_features,
            midpoint,
            self.settings.order_block_retest_tolerance_pct,
        )
        features = {
            **self.context_features,
            "latest_price": midpoint,
            "bid_price": bid,
            "ask_price": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "midpoint": midpoint,
            "spread": spread,
            "spread_pct": spread_pct,
            "spread_regime": spread_regime,
            "spread_expansion_ratio": spread_expansion_ratio,
            "spread_stability_score": spread_stability_score,
            "quote_imbalance": quote_imbalance,
            "quote_age_seconds": max(0.0, quote_age),
            "trade_age_seconds": max(0.0, trade_age),
            "trade_intensity": trade_intensity,
            "aggressive_buy_volume": aggressive_buy_volume,
            "aggressive_sell_volume": aggressive_sell_volume,
            "signed_volume": signed_volume,
            "micro_price_pressure": pressure,
            "liquidity_score": liquidity_score,
            "volatility_burst": volatility_burst,
            "realized_range_pct": realized_range_pct,
            "midpoint_move_pct": midpoint_move_pct,
            "recent_high": recent_high,
            "recent_low": recent_low,
            "prior_high": prior_high,
            "prior_low": prior_low,
            "quote_count_fast_window": len(recent_quotes),
            "trade_count_fast_window": len(recent_trades),
            "websocket_connected": True,
            "stream_stale": False,
            "data_age_seconds": 0.0,
            "source": "fast_scalp_stream",
            "paper_learning_mode": self.settings.paper_learning_mode,
            "paper_exploration": False,
            "strategy_path": "fast",
            "time_of_day_profile": time_of_day_profile(now),
            "order_block_retest_active": order_block_live_retest,
            "order_block_retest_direction": order_block_live_direction,
        }
        return features

    def _decide(self, now: datetime, f: dict[str, Any]) -> FastScalpDecision:
        blocks = []
        maximum_spread = min(self.settings.max_spread_pct, self.settings.fast_scalp_tight_spread_pct)
        minimum_liquidity = self.settings.minimum_liquidity_score
        if f["spread_pct"] > maximum_spread:
            blocks.append("spread expansion")
        if f["liquidity_score"] < minimum_liquidity:
            blocks.append("liquidity collapse")
        if f.get("event_risk_active") and self.settings.avoid_event_risk_trading and not f.get("event_post_release"):
            blocks.append(str(f.get("event_risk_reason") or "scheduled event risk"))
        if max(float(f["quote_age_seconds"]), float(f["trade_age_seconds"])) > self.settings.quote_stale_seconds:
            blocks.append("live quote/trade stale")

        false_break = self._false_break(now, f)
        if false_break is not None:
            decision, reason, score = false_break
        else:
            decision, reason, score = self._directional_setup(now, f)

        if decision in {"LONG", "SHORT"}:
            context_adjustment, context_reason = self._context_adjustment(f, decision)
            score = clamp(score + context_adjustment, 0.0, 100.0)
            if context_reason:
                reason = f"{reason}; {context_reason}"

        trigger = _trigger_from_reason(reason)
        playbook = {
            "false_break": "false_break_reversal",
            "clean_breakout": "proper_breakout",
            "news_spike": "news_event",
            "spread_capture": "spread_capture",
        }.get(trigger, "spread_capture")
        if decision in {"LONG", "SHORT"}:
            pattern = {
                "false_break": "false_break_down" if decision == "LONG" else "false_break_up",
                "clean_breakout": "proper_break_up" if decision == "LONG" else "proper_break_down",
                "news_spike": "news_event_up" if decision == "LONG" else "news_event_down",
                "spread_capture": "microstructure_pressure_up" if decision == "LONG" else "microstructure_pressure_down",
            }.get(trigger, "microstructure_pressure")
            f.update(
                {
                    "fast_candidate_direction": decision,
                    "fast_candidate_score": score,
                    "fast_candidate_trigger": trigger,
                    "playbook": playbook,
                    "playbook_direction": decision,
                    "playbook_allowed": True,
                    "playbook_score": score,
                    "playbook_confirmations": ["fresh_quote", "fresh_trade", "stable_spread", "directional_tape"],
                    "playbook_confirmation_count": 4,
                    "playbook_required_confirmations": 4,
                    "pattern_classification": pattern,
                    "pattern_quality": max(float(f.get("pattern_quality") or 0.0), round(score / 100.0, 4)),
                    "proper_break": trigger == "clean_breakout",
                    "false_break": trigger == "false_break",
                }
            )
        confidence = clamp(score / 100, 0.0, 1.0)
        minimum_confidence = self.settings.fast_scalp_min_confidence
        if confidence < minimum_confidence:
            blocks.append(f"fast confidence {confidence:.2f} below {minimum_confidence:.2f}")
        f["fast_normal_blocks"] = list(blocks)
        if blocks:
            return FastScalpDecision(
                now,
                self.settings.bot_symbol,
                "NO_TRADE",
                "fast_block",
                False,
                confidence,
                score,
                "; ".join(blocks),
                0.0,
                f,
            )
        return FastScalpDecision(now, self.settings.bot_symbol, decision, trigger, True, confidence, score, reason, 0.0, f)

    def _false_break(self, now: datetime, f: dict[str, Any]) -> tuple[str, str, float] | None:
        move = self.settings.fast_scalp_breakout_min_move_pct
        midpoint = float(f["midpoint"])
        prior_high = float(f["prior_high"])
        prior_low = float(f["prior_low"])
        if midpoint > prior_high * (1 + move):
            self._last_break = {"direction": "up", "level": prior_high, "timestamp": now}
        elif midpoint < prior_low * (1 - move):
            self._last_break = {"direction": "down", "level": prior_low, "timestamp": now}
        last = self._last_break
        if last is None:
            return None
        age = (now - ensure_utc(last["timestamp"])).total_seconds()
        if age > self.settings.fast_scalp_false_break_window_seconds:
            return None
        level = float(last["level"])
        liquidity = float(f["liquidity_score"])
        if last["direction"] == "up" and midpoint < level and float(f["micro_price_pressure"]) <= -0.15:
            score = 72 + abs(float(f["micro_price_pressure"])) * 18 + liquidity * 10
            return "SHORT", "very fast false break up reversal", round(clamp(score, 0, 100), 2)
        if last["direction"] == "down" and midpoint > level and float(f["micro_price_pressure"]) >= 0.15:
            score = 72 + abs(float(f["micro_price_pressure"])) * 18 + liquidity * 10
            return "LONG", "very fast false break down reversal", round(clamp(score, 0, 100), 2)
        return None

    def _directional_setup(self, now: datetime, f: dict[str, Any]) -> tuple[str, str, float]:
        move = self.settings.fast_scalp_breakout_min_move_pct
        midpoint = float(f["midpoint"])
        pressure = float(f["micro_price_pressure"])
        imbalance = float(f["quote_imbalance"])
        intensity = float(f["trade_intensity"])
        tight_spread = float(f["spread_pct"]) <= self.settings.fast_scalp_tight_spread_pct
        volatility_burst = bool(f["volatility_burst"])
        news_spike = float(f.get("headline_event_risk") or 0.0) >= 0.60 and volatility_burst
        long_break = midpoint > float(f["prior_high"]) * (1 + move)
        short_break = midpoint < float(f["prior_low"]) * (1 - move)
        long_pressure = imbalance >= self.settings.fast_scalp_imbalance_threshold or pressure >= 0.25
        short_pressure = imbalance <= -self.settings.fast_scalp_imbalance_threshold or pressure <= -0.25
        active_tape = intensity >= self.settings.fast_scalp_min_trade_intensity
        base = 40 + clamp(abs(imbalance), 0, 1) * 18 + clamp(abs(pressure), 0, 1) * 18 + clamp(intensity / 10, 0, 1) * 14
        if long_break and long_pressure and active_tape:
            return "LONG", "clean fast breakout up with quote/trade confirmation", round(clamp(base + 18, 0, 100), 2)
        if short_break and short_pressure and active_tape:
            return "SHORT", "clean fast breakout down with quote/trade confirmation", round(clamp(base + 18, 0, 100), 2)
        if news_spike and pressure > 0.20 and active_tape:
            return "LONG", "news-spike volatility burst with aggressive buying", round(clamp(base + 12, 0, 100), 2)
        if news_spike and pressure < -0.20 and active_tape:
            return "SHORT", "news-spike volatility burst with aggressive selling", round(clamp(base + 12, 0, 100), 2)
        if tight_spread and long_pressure and active_tape:
            return "LONG", "tiny-spread bid/ask pressure scalp", round(clamp(base + 8, 0, 100), 2)
        if tight_spread and short_pressure and active_tape:
            return "SHORT", "tiny-spread bid/ask pressure scalp", round(clamp(base + 8, 0, 100), 2)
        return "NO_TRADE", "waiting for fast microstructure edge", round(clamp(base, 0, 100), 2)

    def _context_adjustment(self, features: dict[str, Any], decision: str) -> tuple[float, str]:
        adjustment = 0.0
        reasons: list[str] = []
        expected = "bullish" if decision == "LONG" else "bearish"
        if features.get("order_block_retest_active"):
            if features.get("order_block_retest_direction") == expected:
                adjustment += 6.0
                reasons.append("live order-block retest confirms")
            else:
                adjustment -= 6.0
                reasons.append("live order-block retest opposes")
        if features.get("order_block_conflict"):
            adjustment -= 4.0
            reasons.append("order-block conflict")
        if not features.get("options_stale") and float(features.get("options_confidence") or 0.0) >= 0.35:
            options_adjustment = clamp(
                float(features.get("options_score_adjustment") or 0.0),
                -self.settings.options_max_score_adjustment,
                self.settings.options_max_score_adjustment,
            )
            adjustment += options_adjustment if decision == "LONG" else -options_adjustment
            if abs(options_adjustment) >= 0.25:
                reasons.append("bounded GLD options context applied")
        return adjustment, "; ".join(reasons)


class FastScalpRuntime:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        trading_client: Any | None = None,
        coordinator: "OrderIntentCoordinator | None" = None,
        transformer_runtime: "AsyncTransformerShadowRuntime | None" = None,
        latency_tracker: Any | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.engine = FastScalpEngine(self.settings)
        self.trading_client = trading_client
        self.coordinator = coordinator
        self.transformer_runtime = transformer_runtime
        self.latency_tracker = latency_tracker
        self._queue: queue.Queue[FastScalpEvent] = queue.Queue(maxsize=self.settings.fast_scalp_event_queue_size)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_order_at: datetime | None = None
        self._last_order_attempt_at: datetime | None = None
        self._last_model_refresh_at: datetime | None = None
        self._last_no_trade_log_at: datetime | None = None
        self._dropped_events = 0
        self._model_version: str | None = None
        self._model_role = "none"

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="fast-scalp-runtime", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)

    def enqueue_event(self, event_type: str, payload: dict[str, Any], received_at: datetime | None = None) -> None:
        try:
            self._queue.put_nowait(FastScalpEvent(event_type, dict(payload), ensure_utc(received_at or utc_now())))
        except queue.Full:
            self._dropped_events += 1

    def update_context(self, features: dict[str, Any]) -> None:
        self.engine.update_context(features)

    def status(self) -> dict[str, Any]:
        return {
            "thread_alive": self._thread is not None and self._thread.is_alive(),
            "queue_size": self._queue.qsize(),
            "dropped_events": self._dropped_events,
            "last_order_at": self._last_order_at.isoformat() if self._last_order_at else None,
            "ml_model_version": self._model_version,
            "ml_model_role": self._model_role,
            "ml_participating": self._model_version is not None,
        }

    def _run(self) -> None:
        database = Database(settings=self.settings)
        database.init_db()
        persistence = AsyncFastPersistence(self.settings)
        persistence.start()
        trading_client = self.trading_client or get_trading_client(self.settings)
        predictor = Predictor(database)
        if self.settings.paper_require_ml_model and not predictor.has_model:
            raise RuntimeError("fast paper runtime requires an ML model, but no champion or eligible shadow loaded")
        self._model_version = predictor.model_version
        self._model_role = predictor.model_role
        logger.info(
            "fast ML runtime ready participating=%s role=%s model_version=%s",
            predictor.has_model,
            predictor.model_role,
            predictor.model_version,
            extra={"event_type": "fast_ml_runtime_ready"},
        )
        risk = RiskEngine(self.settings, database)
        paper_exploration = PaperExplorationPolicy(self.settings, database)
        execution = ExecutionEngine(
            self.settings,
            database,
            trading_client=trading_client,
            coordinator=self.coordinator,
        )
        try:
            while not self._stop_event.is_set():
                try:
                    event = self._queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                decision = self.engine.on_event(event.event_type, event.payload, event.received_at)
                if decision is None:
                    continue
                trace_id = None
                if self.latency_tracker is not None:
                    trace_id = self.latency_tracker.begin(strategy_path="fast", event_time=event.received_at, started_ns=event.enqueued_ns)
                    self.latency_tracker.record(trace_id, "decision_complete", details={"engine_latency_ms": decision.latency_ms, "trigger": decision.trigger_type})
                    decision.features["latency_trace_id"] = trace_id
                if self._last_model_refresh_at is None or decision.timestamp >= self._last_model_refresh_at + timedelta(seconds=60):
                    predictor.reload_champion()
                    self._model_version = predictor.model_version
                    self._model_role = predictor.model_role
                    self._last_model_refresh_at = decision.timestamp
                self._handle_decision(
                    database,
                    persistence,
                    trading_client,
                    predictor,
                    risk,
                    execution,
                    decision,
                    paper_exploration,
                )
        except Exception as exc:  # pragma: no cover - runtime guard
            logger.exception("fast scalp runtime failed: %s", exc)
            try:
                database.log_event("ERROR", __name__, "fast_scalp_runtime_failed", str(exc), self.status())
            except Exception:
                pass
        finally:
            persistence.stop()
            database.close()

    def _handle_decision(
        self,
        database: Database,
        persistence: "AsyncFastPersistence",
        trading_client: Any,
        predictor: Predictor,
        risk: RiskEngine,
        execution: ExecutionEngine,
        decision: FastScalpDecision,
        paper_exploration: PaperExplorationPolicy | None = None,
    ) -> None:
        ml_result = predictor.predict(decision.features)
        trace_id = decision.features.get("latency_trace_id")
        if self.latency_tracker is not None:
            self.latency_tracker.record(trace_id, "classical_model_complete", strategy_path="fast")
        decision.features.update(predictor.prediction_features(ml_result))
        transformer_prediction = None
        if self.transformer_runtime is not None:
            shadow = self.transformer_runtime.submit(
                "fast_microstructure",
                decision.timestamp,
                decision.features,
                fallback_model_version=ml_result.model_version,
            )
            decision.features.update(shadow.as_features())
            transformer_prediction = shadow
        if transformer_prediction is not None:
            apply_transformer_to_fast_decision(decision, transformer_prediction, self.settings)
        _apply_paper_ml_advice(decision, ml_result, predictor, self.settings)
        council = run_decision_council(decision.features, self.settings, now=decision.timestamp)
        if self.latency_tracker is not None:
            self.latency_tracker.record(trace_id, "decision_council_complete", strategy_path="fast")
        decision.features.update(council.as_features())
        if council.hard_block and decision.decision != "NO_TRADE":
            decision.decision = "NO_TRADE"
            decision.allowed = False
            decision.reason = "; ".join(council.block_reasons)
        decision = (paper_exploration or PaperExplorationPolicy(self.settings, database)).maybe_select_fast(
            decision,
            now=decision.timestamp,
        )
        if council.hard_block:
            decision.decision = "NO_TRADE"
            decision.allowed = False
            decision.reason = "; ".join(council.block_reasons)
        predicted_action = {"long_good": "LONG", "short_good": "SHORT"}.get(ml_result.predicted_direction)
        decision.features["ml_direction_aligned"] = predicted_action == decision.decision
        self._persist_fast_decision(persistence, decision, ml_result, None, None)
        if self.settings.paper_require_ml_model and not decision.features.get("ml_input_compatible"):
            self._maybe_log_fast_no_trade(persistence, decision, "required ML feature profile unavailable")
            return
        if not decision.is_trade:
            self._maybe_log_fast_no_trade(persistence, decision)
            return
        quality = EntryQualityGate(self.settings).evaluate(decision.features, strategy_path="fast", now=decision.timestamp)
        decision.features.update(quality.as_features())
        if not quality.allowed:
            self._maybe_log_fast_no_trade(persistence, decision, quality.reason)
            self._persist_fast_decision(persistence, decision, ml_result, None, quality.reason)
            return
        cooldown = EntryCooldownPolicy(self.settings, database).evaluate(
            strategy_path="fast",
            features=decision.features,
            now=decision.timestamp,
        )
        decision.features.update({"entry_cooldown_allowed": cooldown.allowed, "entry_cooldown_reason": cooldown.reason})
        if not cooldown.allowed:
            self._maybe_log_fast_no_trade(persistence, decision, cooldown.reason)
            self._persist_fast_decision(persistence, decision, ml_result, None, cooldown.reason)
            return
        if not self.settings.enable_fast_scalp_order_submission:
            self._maybe_log_fast_no_trade(persistence, decision, "fast order submission disabled")
            return
        cooldown_seconds = (
            self.settings.paper_learning_fast_order_cooldown_seconds
            if self.settings.paper_learning_mode
            else self.settings.fast_scalp_order_cooldown_seconds
        )
        if self._last_order_attempt_at is not None:
            age = (decision.timestamp - self._last_order_attempt_at).total_seconds()
            if age < cooldown_seconds:
                self._maybe_log_fast_no_trade(persistence, decision, "fast order cooldown active")
                return
        if model_rejection_blocks(self.settings, decision.features, ml_result.rejects_trade):
            self._maybe_log_fast_no_trade(persistence, decision, ml_result.rejection_reason or "ML rejected fast setup")
            return
        self._last_order_attempt_at = decision.timestamp
        signal = _decision_to_signal(decision, ml_result)
        risk_state = risk.state_from_features(decision.features)
        _refresh_fast_broker_state(risk_state, trading_client, self.settings.bot_symbol, database, self.settings)
        risk.enrich_runtime_state(risk_state, now=decision.timestamp)
        blocked, block_reason = risk.blocks_trading(risk_state, signal.decision)
        if blocked:
            self._maybe_log_fast_no_trade(persistence, decision, block_reason or "risk block")
            self._persist_fast_decision(persistence, decision, ml_result, None, block_reason)
            return
        try:
            plan = risk.build_order_plan(signal, ml_result, risk_state)
        except OrderPlanRejected as exc:
            block_reason = str(exc)
            decision.features["order_plan_block_reason"] = block_reason
            self._maybe_log_fast_no_trade(persistence, decision, block_reason)
            self._persist_fast_decision(persistence, decision, ml_result, None, block_reason)
            logger.info(
                "fast scalp order plan blocked reason=%s",
                block_reason,
                extra={"event_type": "fast_scalp_order_plan_blocked"},
            )
            return
        try:
            plan.client_order_id = plan.client_order_id or make_client_order_id(plan.symbol, f"FAST-{plan.direction}", decision.timestamp)
            plan.latency_trace_id = trace_id
            if self.latency_tracker is not None:
                self.latency_tracker.bind(trace_id, client_order_id=plan.client_order_id)
                self.latency_tracker.record(trace_id, "order_plan_complete", strategy_path="fast", playbook=plan.playbook, client_order_id=plan.client_order_id)
            submission = execution.submit_entry_with_protection(plan)
            primary = submission.primary
            order = primary.order
            self._last_order_at = decision.timestamp
            self._persist_fast_decision(
                persistence,
                decision,
                ml_result,
                primary.client_order_id,
                str(getattr(order, "status", "submitted")),
            )
            persistence.enqueue_signal(
                {
                    "timestamp": decision.timestamp,
                    "symbol": decision.symbol,
                    "decision": decision.decision,
                    "bullish_score": signal.bullish_score,
                    "bearish_score": signal.bearish_score,
                    "no_trade_score": signal.no_trade_score,
                    "regime": signal.regime,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "model_version": ml_result.model_version,
                    "feature_snapshot_json": {
                        **decision.features,
                        "bullish_score": signal.bullish_score,
                        "bearish_score": signal.bearish_score,
                        "no_trade_score": signal.no_trade_score,
                        "confidence": decision.confidence,
                        "regime": signal.regime,
                        "fast_scalp_trade": True,
                        "fast_scalp_trigger_type": decision.trigger_type,
                    },
                }
            )
            for submitted in submission.entries:
                tranche_order = submitted.order
                persistence.enqueue_journal(
                    {
                        "timestamp": decision.timestamp,
                        "symbol": decision.symbol,
                        "event_type": "FAST_ORDER_SUBMITTED",
                        "decision": decision.decision,
                        "confidence": decision.confidence,
                        "bullish_score": signal.bullish_score,
                        "bearish_score": signal.bearish_score,
                        "no_trade_score": signal.no_trade_score,
                        "regime": signal.regime,
                        "reason": decision.reason,
                        "model_version": ml_result.model_version,
                        "model_prediction": ml_result.predicted_direction,
                        "probability_long": ml_result.probability_long,
                        "probability_short": ml_result.probability_short,
                        "probability_no_trade": ml_result.probability_no_trade,
                        "order_id": str(getattr(tranche_order, "id", "")),
                        "client_order_id": submitted.client_order_id,
                        "side": submitted.plan.side,
                        "qty": submitted.qty,
                        "price": submitted.plan.entry_limit_price,
                        "notional": submitted.plan.estimated_notional,
                        "status": str(getattr(tranche_order, "status", "submitted")),
                        "pattern_classification": decision.features.get("pattern_classification"),
                        "pattern_quality": decision.features.get("pattern_quality"),
                        "liquidity_score": decision.features.get("liquidity_score"),
                        "volatility_regime": decision.features.get("gold_volatility_regime"),
                        "macro_bias": decision.features.get("macro_bias"),
                        "macro_confidence": decision.features.get("macro_confidence"),
                        "target_exposure_pct": decision.features.get("target_exposure_pct"),
                        "order_block_direction": decision.features.get("order_block_direction"),
                        "order_block_timeframe": decision.features.get("order_block_timeframe"),
                        "order_block_strength": decision.features.get("order_block_strength"),
                        "order_block_retest_active": decision.features.get("order_block_retest_active"),
                        "options_bias": decision.features.get("options_bias"),
                        "options_confidence": decision.features.get("options_confidence"),
                        "options_score_adjustment": decision.features.get("options_score_adjustment"),
                        "options_event_risk": decision.features.get("options_event_risk"),
                        "exploration_trade": decision.features.get("paper_exploration"),
                        "feature_snapshot_json": {
                            **decision.features,
                            "position_role": submitted.role,
                            "root_client_order_id": plan.client_order_id,
                        },
                        "broker_snapshot_json": tranche_order.model_dump(mode="json") if hasattr(tranche_order, "model_dump") else {"repr": repr(tranche_order)},
                    }
                )
            if submission.errors:
                logger.error(
                    "partial protected tranche submission errors=%s",
                    "; ".join(submission.errors),
                    extra={"event_type": "partial_tranche_submit_failed"},
                )
            logger.warning(
                "fast scalp order submitted decision=%s trigger=%s confidence=%.3f latency_ms=%.3f",
                decision.decision,
                decision.trigger_type,
                decision.confidence,
                decision.latency_ms,
                extra={"event_type": "fast_scalp_order_submitted"},
            )
        except EntryBlockedError as exc:
            self._persist_fast_decision(persistence, decision, ml_result, None, f"safety_block: {exc}")
            self._maybe_log_fast_no_trade(persistence, decision, str(exc))
            logger.info(
                "fast scalp entry blocked by execution safety: %s",
                exc,
                extra={"event_type": "fast_scalp_safety_block"},
            )
        except Exception as exc:  # pragma: no cover - broker/network path
            self._persist_fast_decision(persistence, decision, ml_result, None, f"submit_failed: {exc}")
            persistence.enqueue_journal(
                {
                    "timestamp": decision.timestamp,
                    "symbol": decision.symbol,
                    "event_type": "FAST_ORDER_SUBMIT_FAILED",
                    "decision": decision.decision,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "model_version": ml_result.model_version,
                    "model_prediction": ml_result.predicted_direction,
                    "feature_snapshot_json": decision.features,
                    "broker_snapshot_json": {"error": str(exc)},
                    "status": "submit_failed",
                }
            )
            logger.exception("fast scalp order submission failed: %s", exc, extra={"event_type": "fast_scalp_order_failed"})

    def _persist_fast_decision(
        self,
        persistence: "AsyncFastPersistence",
        decision: FastScalpDecision,
        ml_result: MLPrediction,
        client_order_id: str | None,
        status_or_block: str | None,
    ) -> None:
        persistence.enqueue_fast_decision(
            {
                "timestamp": decision.timestamp,
                "symbol": decision.symbol,
                "decision": decision.decision,
                "trigger_type": decision.trigger_type,
                "allowed": decision.allowed,
                "confidence": decision.confidence,
                "score": decision.score,
                "reason": decision.reason,
                "latency_ms": decision.latency_ms,
                "spread_pct": decision.features.get("spread_pct"),
                "quote_imbalance": decision.features.get("quote_imbalance"),
                "trade_intensity": decision.features.get("trade_intensity"),
                "liquidity_score": decision.features.get("liquidity_score"),
                "volatility_burst": decision.features.get("volatility_burst"),
                "model_version": ml_result.model_version,
                "model_prediction": ml_result.predicted_direction,
                "probability_long": ml_result.probability_long,
                "probability_short": ml_result.probability_short,
                "probability_no_trade": ml_result.probability_no_trade,
                "client_order_id": client_order_id,
                "status": status_or_block,
                "features": decision.features,
            }
        )

    def _maybe_log_fast_no_trade(self, persistence: "AsyncFastPersistence", decision: FastScalpDecision, reason: str | None = None) -> None:
        now = decision.timestamp
        if self._last_no_trade_log_at is not None:
            age = (now - self._last_no_trade_log_at).total_seconds()
            if age < self.settings.fast_scalp_no_trade_log_interval_seconds:
                return
        self._last_no_trade_log_at = now
        persistence.enqueue_no_trade(
            {
                "timestamp": now,
                "symbol": decision.symbol,
                "reason": reason or decision.reason,
                "spread_pct": decision.features.get("spread_pct"),
                "risk_block_reason": None if decision.allowed else decision.reason,
                "feature_snapshot_json": decision.features,
            }
        )


class AsyncFastPersistence:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=settings.fast_scalp_event_queue_size)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.dropped_records = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="fast-scalp-persistence", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)

    def enqueue_fast_decision(self, record: dict[str, Any]) -> None:
        self._enqueue("fast_decision", record)

    def enqueue_no_trade(self, record: dict[str, Any]) -> None:
        self._enqueue("no_trade", record)

    def enqueue_signal(self, record: dict[str, Any]) -> None:
        self._enqueue("signal", record)

    def enqueue_journal(self, record: dict[str, Any]) -> None:
        self._enqueue("journal", record)

    def _enqueue(self, operation: str, record: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait((operation, record))
        except queue.Full:
            self.dropped_records += 1
            logger.error("fast persistence queue full operation=%s", operation, extra={"event_type": "fast_persistence_drop"})

    def _run(self) -> None:
        database = Database(settings=self.settings)
        database.init_db()
        try:
            while not self._stop_event.is_set() or not self._queue.empty():
                try:
                    operation, record = self._queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                try:
                    if operation == "fast_decision":
                        decision_id = database.insert_fast_scalp_decision(record)
                        if record.get("status") is not None:
                            client_order_id = record.get("client_order_id")
                            submitted = bool(client_order_id)
                            database.upsert_decision_execution(
                                {
                                    "decision_source": "fast_scalp",
                                    "decision_id": decision_id,
                                    "timestamp": record["timestamp"],
                                    "symbol": record["symbol"],
                                    "strategy_path": "fast",
                                    "playbook": (record.get("features") or {}).get("playbook"),
                                    "original_action": record.get("decision"),
                                    "executed_action": record.get("decision") if submitted else "NO_TRADE",
                                    "execution_status": "submitted" if submitted else "blocked",
                                    "client_order_id": client_order_id,
                                    "root_episode_id": client_order_id,
                                    "model_scope": (record.get("features") or {}).get("ml_model_scope"),
                                    "model_version": record.get("model_version"),
                                    "spread_pct": record.get("spread_pct"),
                                    "expected_slippage_pct": self.settings.estimated_round_trip_slippage_pct,
                                    "direction_available": not bool((record.get("features") or {}).get("shortability_blocked")),
                                    "session_phase": (record.get("features") or {}).get("time_of_day_profile"),
                                    "execution_error": None if submitted else record.get("status"),
                                    "features": record.get("features") or {},
                                }
                            )
                    elif operation == "no_trade":
                        database.insert_no_trade(record)
                    elif operation == "signal":
                        database.insert_signal(record)
                    elif operation == "journal":
                        database.insert_trading_journal(record)
                except Exception as exc:  # pragma: no cover - disk failure path
                    logger.exception("asynchronous fast persistence failed: %s", exc)
                finally:
                    self._queue.task_done()
        finally:
            database.close()


def _decision_to_signal(decision: FastScalpDecision, ml_result: MLPrediction) -> MarketSignal:
    bullish = decision.score if decision.decision == "LONG" else 0.0
    bearish = decision.score if decision.decision == "SHORT" else 0.0
    return MarketSignal(
        timestamp=decision.timestamp,
        symbol=decision.symbol,
        decision=decision.decision,  # type: ignore[arg-type]
        bullish_score=bullish,
        bearish_score=bearish,
        no_trade_score=0.0,
        regime=f"fast_{decision.trigger_type}",
        confidence=decision.confidence,
        reason=decision.reason,
        features=decision.features,
        model_version=ml_result.model_version,
    )


def _apply_paper_ml_advice(
    decision: FastScalpDecision,
    prediction: MLPrediction,
    predictor: Predictor,
    settings: Settings,
) -> None:
    """Apply bounded ML support only when it agrees with an already valid fast setup."""
    if not (
        settings.paper_learning_mode
        and predictor.has_model
        and decision.allowed
        and decision.features.get("ml_advice_eligible")
        and prediction.confidence >= settings.paper_ml_min_advisory_confidence
    ):
        return
    probability_margin = abs(prediction.probability_long - prediction.probability_short)
    if probability_margin < settings.ml_min_probability_margin:
        return
    direction = {"long_good": "LONG", "short_good": "SHORT"}.get(prediction.predicted_direction)
    if direction is None or direction != decision.decision:
        return
    adjustment = clamp(
        prediction.confidence * settings.paper_ml_max_score_adjustment,
        0.0,
        settings.paper_ml_max_score_adjustment,
    )
    decision.score = round(clamp(decision.score + adjustment, 0.0, 100.0), 2)
    decision.confidence = round(clamp(decision.score / 100.0, 0.0, 1.0), 4)
    decision.reason = (
        f"{decision.reason}; paper ML adviser {prediction.predicted_direction} "
        f"confidence={prediction.confidence:.3f} aligned={direction}"
    )
    decision.features.update(
        {
            "paper_ml_advice_applied": True,
            "paper_ml_prior_direction": decision.decision,
            "paper_ml_score_adjustment": adjustment,
        }
    )


def _refresh_fast_broker_state(
    risk_state,
    trading_client: Any,
    symbol: str,
    database: Database,
    settings: Settings,
) -> None:
    try:
        account = trading_client.get_account()
        risk_state.account_equity = float(getattr(account, "equity", risk_state.account_equity) or risk_state.account_equity)
        risk_state.day_start_equity = risk_state.account_equity
        positions = trading_client.get_all_positions()
        related = {item.upper() for item in settings.related_symbols}
        risk_state.correlated_exposure_notional = sum(
            abs(float(getattr(position, "market_value", 0.0) or 0.0))
            for position in positions
            if str(getattr(position, "symbol", "")).upper() in related
        )
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = trading_client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol.upper()], nested=True)
        )
        populate_concurrent_risk_state(
            risk_state,
            symbol=symbol,
            positions=list(positions),
            open_orders=list(orders),
            database=database,
            settings=settings,
        )
        risk_state.position_reconciled = True
        risk_state.market_open = market_session(extended_hours=False) != "closed"
    except Exception:
        risk_state.position_reconciled = False


def _normalize_quote(payload: dict[str, Any], received_at: datetime) -> dict[str, Any] | None:
    bid = _float(payload.get("bid_price"))
    ask = _float(payload.get("ask_price"))
    if bid <= 0 or ask <= 0:
        return None
    midpoint = (bid + ask) / 2
    return {
        "symbol": str(payload.get("symbol") or "GLD").upper(),
        "timestamp": ensure_utc(payload.get("timestamp") or received_at),
        "received_at": received_at,
        "bid_price": bid,
        "ask_price": ask,
        "bid_size": _float(payload.get("bid_size")),
        "ask_size": _float(payload.get("ask_size")),
        "midpoint": midpoint,
        "spread_pct": safe_div(ask - bid, midpoint),
    }


def _normalize_trade(payload: dict[str, Any], received_at: datetime) -> dict[str, Any] | None:
    price = _float(payload.get("price"))
    if price <= 0:
        return None
    return {
        "symbol": str(payload.get("symbol") or "GLD").upper(),
        "timestamp": ensure_utc(payload.get("timestamp") or received_at),
        "received_at": received_at,
        "price": price,
        "size": _float(payload.get("size")),
    }


def _trigger_from_reason(reason: str) -> str:
    if "false break" in reason:
        return "false_break"
    if "news-spike" in reason:
        return "news_spike"
    if "tiny-spread" in reason:
        return "spread_capture"
    if "breakout" in reason:
        return "clean_breakout"
    return "microstructure"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
