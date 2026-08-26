from __future__ import annotations

import csv
import json
from datetime import datetime
from typing import Any

from .config import PROJECT_ROOT, Settings
from .database import Database
from .kimi_tier0 import KimiBudgetError
from .llm_provider import (
    LLMError,
    compact_json_text,
    make_llm_client,
    make_ollama_fallback_client,
    recoverable_provider_error,
)
from .macro_context import MacroContextBuilder, _resolve_path
from .utils.math_utils import clamp
from .utils.time_utils import utc_now

SYSTEM_PROMPT = """You are a cautious algorithmic trading research assistant for a paper-only GLD scalping bot.
You analyze data, explain setups, suggest features, and produce labels for ML review.
You never give direct order instructions, never bypass risk controls, and never claim certainty.
Return strict JSON only."""


class LLMAnalysisService:
    def __init__(self, settings: Settings, database: Database, client: Any | None = None) -> None:
        self.settings = settings
        self.database = database
        self.client = client or make_llm_client(settings)

    def run_data_analysis(self, *, query: str = "Analyze bot performance and suggest improvements.") -> dict[str, Any]:
        context = self._analysis_context()
        user = {
            "task": "Analyze SQLite data and CSV export summaries for this GLD scalping bot.",
            "query": query,
            "required_keys": [
                "summary",
                "bull_case",
                "bear_case",
                "risk_critique",
                "execution_critique",
                "journal_review",
                "missed_opportunity_explanations",
                "setup_quality_notes",
                "recommendations",
            ],
            "list_keys": ["missed_opportunity_explanations", "setup_quality_notes", "recommendations"],
            "context": context,
        }
        parsed = self._ask(user)
        record = {
            "timestamp": utc_now(),
            "review_type": f"{self.client.provider}_data_analysis",
            "summary": str(parsed.get("summary") or ""),
            "bull_case": str(parsed.get("bull_case") or ""),
            "bear_case": str(parsed.get("bear_case") or ""),
            "risk_critique": str(parsed.get("risk_critique") or ""),
            "execution_critique": str(parsed.get("execution_critique") or ""),
            "journal_review": str(parsed.get("journal_review") or ""),
            "recommendations": parsed.get("recommendations") or [],
            "evidence": {
                "provider": self.client.provider,
                "model": self.client.model,
                "missed_opportunity_explanations": parsed.get("missed_opportunity_explanations") or [],
                "setup_quality_notes": parsed.get("setup_quality_notes") or [],
                "context": context,
                "raw_response": parsed,
            },
        }
        self.database.insert_llm_review(record)
        return record

    def build_llm_macro_context(self, *, now: datetime | None = None, horizon: str = "daily_weekly") -> dict[str, Any]:
        now = now or utc_now()
        deterministic = MacroContextBuilder(self.settings, self.database).build(now=now)
        headlines = self._headline_rows(limit=40)
        linked_fraction = sum(bool(row.get("outcome_linked")) for row in headlines) / max(len(headlines), 1)
        user = {
            "task": "Produce a FinGPT-style macro/sentiment context for GLD scalping from local headlines and market proxies.",
            "required_keys": [
                "gold_news_sentiment",
                "usd_sentiment",
                "fed_rate_sentiment",
                "risk_off_sentiment",
                "headline_event_risk",
                "sentiment_alignment",
                "macro_bias",
                "macro_confidence",
                "positive_developments",
                "potential_concerns",
                "forecast_summary",
            ],
            "number_ranges": {
                "gold_news_sentiment": "-1 to 1",
                "usd_sentiment": "-1 to 1",
                "fed_rate_sentiment": "-1 to 1",
                "risk_off_sentiment": "-1 to 1",
                "headline_event_risk": "0 to 1",
                "sentiment_alignment": "-1 to 1",
                "macro_confidence": "0 to 1",
            },
            "macro_bias_values": [
                "bullish_gold_environment",
                "bearish_gold_environment",
                "event_risk_environment",
                "neutral_environment",
            ],
            "deterministic_baseline": deterministic,
            "horizon": horizon,
            "recent_headlines": headlines,
        }
        parsed = self._ask(user)
        macro_confidence = _clamped(parsed.get("macro_confidence"), 0.0, 1.0)
        if linked_fraction < self.settings.llm_news_min_linked_fraction:
            macro_confidence = min(macro_confidence, 0.35)
        record = {
            "timestamp": now,
            "horizon": horizon,
            "gold_news_sentiment": _clamped(parsed.get("gold_news_sentiment"), -1.0, 1.0),
            "usd_sentiment": _clamped(parsed.get("usd_sentiment"), -1.0, 1.0),
            "fed_rate_sentiment": _clamped(parsed.get("fed_rate_sentiment"), -1.0, 1.0),
            "risk_off_sentiment": _clamped(parsed.get("risk_off_sentiment"), -1.0, 1.0),
            "headline_event_risk": _clamped(parsed.get("headline_event_risk"), 0.0, 1.0),
            "sentiment_alignment": _clamped(parsed.get("sentiment_alignment"), -1.0, 1.0),
            "macro_bias": _macro_bias(parsed.get("macro_bias")),
            "macro_confidence": macro_confidence,
            "news_linked_fraction": linked_fraction,
            "advisory_only": True,
            "positive_developments": _list(parsed.get("positive_developments")),
            "potential_concerns": _list(parsed.get("potential_concerns")),
            "forecast_summary": str(parsed.get("forecast_summary") or ""),
            "source": f"{self.client.provider}:{self.client.model}",
            "raw_inputs": {
                "deterministic_baseline": deterministic,
                "headlines": headlines,
                "raw_response": parsed,
            },
        }
        self.database.insert_macro_context(record)
        return record

    def classify_recent_news(self, *, limit: int = 40) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self.database.conn.execute(
            """
            SELECT * FROM news_items
            ORDER BY julianday(timestamp) DESC, id DESC LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()]
        if not rows:
            return []
        user = {
            "task": "Classify GLD impact using FinGPT-style financial sentiment and event analysis.",
            "rules": [
                "Return one item per news_id.",
                "Gold impact must be bullish, bearish, or neutral.",
                "Separate USD, Fed/rates, geopolitical, inflation, labor, and systemic-risk effects.",
                "Do not mark a label trusted; trust is assigned deterministically after price/spread linkage.",
            ],
            "required_keys": ["items"],
            "item_keys": [
                "news_id", "sentiment_score", "confidence_score", "novelty_score",
                "event_risk", "gold_impact", "event_type", "summary",
            ],
            "news": [
                {
                    "news_id": row["id"],
                    "timestamp": row["timestamp"],
                    "headline": row["headline"],
                    "summary": row.get("summary"),
                    "source": row.get("source"),
                    "related_move_1m": row.get("related_move_1m"),
                    "related_move_5m": row.get("related_move_5m"),
                    "related_move_15m": row.get("related_move_15m"),
                    "spread_change_5m": row.get("related_spread_change_5m"),
                    "outcome_linked": bool(row.get("outcome_linked")),
                }
                for row in rows
            ],
        }
        parsed = self._ask(user)
        by_id = {int(row["id"]): row for row in rows}
        saved: list[dict[str, Any]] = []
        for item in parsed.get("items") or []:
            try:
                news_id = int(item.get("news_id"))
            except (TypeError, ValueError):
                continue
            row = by_id.get(news_id)
            if row is None:
                continue
            impact = str(item.get("gold_impact") or "neutral").lower()
            if impact not in {"bullish", "bearish", "neutral"}:
                impact = "neutral"
            confidence = _clamped(item.get("confidence_score"), 0.0, 1.0)
            spread_linked = row.get("related_spread_change_5m") is not None or row.get("related_spread_change_15m") is not None
            trusted = (
                _sentiment_matches_realized_move(impact, row.get("related_move_15m"))
                and bool(row.get("outcome_linked"))
                and spread_linked
                and confidence >= 0.65
            )
            with self.database.conn:
                self.database.conn.execute(
                    """
                    UPDATE news_items SET sentiment_score = ?, confidence_score = ?, novelty_score = ?,
                        event_risk = ?, gold_impact = ?, event_type = ?, summary = COALESCE(NULLIF(?, ''), summary),
                        sentiment_trusted = ? WHERE id = ?
                    """,
                    (
                        _clamped(item.get("sentiment_score"), -1.0, 1.0),
                        confidence,
                        _clamped(item.get("novelty_score"), 0.0, 1.0),
                        _clamped(item.get("event_risk"), 0.0, 1.0),
                        impact,
                        str(item.get("event_type") or "other"),
                        str(item.get("summary") or ""),
                        1 if trusted else 0,
                        news_id,
                    ),
                )
            saved.append({"news_id": news_id, "gold_impact": impact, "confidence": confidence, "trusted": trusted})
        return saved

    def generate_training_advice(self) -> dict[str, Any]:
        context = self._analysis_context()
        user = {
            "task": "Generate structured training advice for improving the ML candidate model of a GLD scalping bot.",
            "required_keys": [
                "summary",
                "feature_recommendations",
                "labeling_recommendations",
                "training_actions",
                "risk_warnings",
                "candidate_notes",
            ],
            "list_keys": [
                "feature_recommendations",
                "labeling_recommendations",
                "training_actions",
                "risk_warnings",
                "candidate_notes",
            ],
            "context": context,
        }
        parsed = self._ask(user)
        record = {
            "timestamp": utc_now(),
            "provider": self.client.provider,
            "model": self.client.model,
            "summary": str(parsed.get("summary") or ""),
            "feature_recommendations": _list(parsed.get("feature_recommendations")),
            "labeling_recommendations": _list(parsed.get("labeling_recommendations")),
            "training_actions": _list(parsed.get("training_actions")),
            "risk_warnings": _list(parsed.get("risk_warnings")),
            "candidate_notes": _list(parsed.get("candidate_notes")),
            "raw_response": parsed,
        }
        self.database.insert_llm_training_advice(record)
        return record

    def label_recent_signals(self, *, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.database.fetch_signals_for_llm_labeling(limit=limit)
        if not rows:
            return []
        payload = [
            {
                "signal_id": row["id"],
                "timestamp": row["timestamp"],
                "symbol": row["symbol"],
                "decision": row["decision"],
                "reason": row.get("reason"),
                "features": _safe_json(row.get("feature_snapshot_json")),
            }
            for row in rows
        ]
        user = {
            "task": "Suggest advisory ML labels for recent GLD bot signals. Use only labels long_good, short_good, or no_trade.",
            "rules": [
                "Prefer actual trade_outcomes or missed_opportunity labels when present in features.",
                "If setup quality is unclear, use no_trade.",
                "Return one label object per signal_id.",
            ],
            "required_keys": ["labels"],
            "label_object_keys": ["signal_id", "suggested_label", "confidence", "rationale"],
            "allowed_labels": ["long_good", "short_good", "no_trade"],
            "signals": payload,
        }
        parsed = self._ask(user)
        labels = parsed.get("labels") or []
        saved: list[dict[str, Any]] = []
        row_by_id = {int(row["id"]): row for row in rows}
        for item in labels:
            try:
                signal_id = int(item.get("signal_id"))
            except (TypeError, ValueError):
                continue
            row = row_by_id.get(signal_id)
            if row is None:
                continue
            label = str(item.get("suggested_label") or "no_trade")
            if label not in {"long_good", "short_good", "no_trade"}:
                label = "no_trade"
            record = {
                "signal_id": signal_id,
                "timestamp": row["timestamp"],
                "symbol": row["symbol"],
                "suggested_label": label,
                "confidence": _clamped(item.get("confidence"), 0.0, 1.0),
                "rationale": str(item.get("rationale") or ""),
                "provider": self.client.provider,
                "model": self.client.model,
                "raw_response": item,
            }
            self.database.insert_llm_signal_label(record)
            saved.append(record)
        return saved

    def _ask(self, user_payload: dict[str, Any]) -> dict[str, Any]:
        request = {
            "response_instruction": (
                "Return one JSON object with the expected keys. "
                "Use the provided data to create specific, non-placeholder content. "
                "Limit every string to 240 characters and every list to at most three short items. "
                "Do not copy key descriptions, do not return an API error object, markdown, comments, or a schema explanation."
            ),
            "request": user_payload,
        }
        serialized = json.dumps(request, sort_keys=True, default=str)
        limit = 60_000 if self.settings.llm_provider.lower() == "kimi" else 18_000
        serialized = compact_json_text(serialized, max_chars=limit)
        try:
            response = self.client.chat_json(system=SYSTEM_PROMPT, user=serialized)
        except (LLMError, KimiBudgetError) as exc:
            if self.settings.llm_provider.lower() != "kimi" or not recoverable_provider_error(exc):
                raise
            fallback = make_ollama_fallback_client(self.settings)
            response = fallback.chat_json(system=SYSTEM_PROMPT, user=serialized)
            response.raw["fallback"] = {
                "from": "kimi",
                "to": "ollama",
                "reason": str(exc)[:240],
            }
            self.client = fallback
        return response.json_content()

    def _analysis_context(self) -> dict[str, Any]:
        return {
            "recent_signals": self.database.fetch_recent_table_rows("signals", limit=self.settings.llm_max_context_rows),
            "recent_no_trade_logs": self.database.fetch_recent_table_rows("no_trade_logs", limit=self.settings.llm_max_context_rows),
            "recent_trading_journal": self.database.fetch_recent_table_rows("trading_journal", limit=self.settings.llm_max_context_rows),
            "recent_missed_opportunities": self.database.fetch_recent_table_rows("missed_opportunities", limit=self.settings.llm_max_context_rows),
            "recent_trade_outcomes": self.database.fetch_recent_table_rows("trade_outcomes", limit=self.settings.llm_max_context_rows),
            "latest_macro_context": self.database.fetch_recent_table_rows("macro_context", limit=3),
            "latest_csv_summaries": _latest_csv_summaries(limit_rows=8),
        }

    def _headline_rows(self, *, limit: int) -> list[dict[str, Any]]:
        rows = [
            dict(row)
            for row in self.database.conn.execute(
                """
                SELECT id, timestamp, source, headline, summary, event_type, sentiment_score,
                       confidence_score, novelty_score, gold_impact, event_risk,
                       related_move_1m, related_move_5m, related_move_15m,
                       related_spread_change_5m, outcome_linked, sentiment_trusted
                FROM news_items ORDER BY julianday(timestamp) DESC, id DESC LIMIT ?
                """,
                (max(1, limit),),
            ).fetchall()
        ]
        if rows:
            return rows
        path = _resolve_path(self.settings.macro_context_headlines_path)
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                rows.append(dict(row))
                if len(rows) >= limit:
                    break
        return rows


def _latest_csv_summaries(*, limit_rows: int) -> dict[str, list[dict[str, Any]]]:
    latest = PROJECT_ROOT / "exports" / "latest"
    if not latest.exists():
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for path in latest.glob("*.csv"):
        try:
            with path.open("r", newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                result[path.name] = [dict(row) for _, row in zip(range(limit_rows), reader)]
        except Exception:
            continue
    return result


def _safe_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _clamped(value: Any, low: float, high: float) -> float:
    try:
        return float(clamp(float(value), low, high))
    except (TypeError, ValueError):
        return 0.0


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _macro_bias(value: Any) -> str:
    raw = str(value or "neutral_environment")
    if raw in {"bullish_gold_environment", "bearish_gold_environment", "event_risk_environment", "neutral_environment"}:
        return raw
    return "neutral_environment"


def _sentiment_matches_realized_move(impact: str, move: Any) -> bool:
    try:
        value = float(move)
    except (TypeError, ValueError):
        return False
    if impact == "bullish":
        return value > 0.0
    if impact == "bearish":
        return value < 0.0
    return abs(value) < 0.001


def require_offline_llm_enabled(settings: Settings) -> None:
    if settings.llm_provider.lower() not in {"ollama", "kimi"}:
        raise LLMError("Set LLM_PROVIDER=ollama or kimi before running offline LLM commands.")
    if settings.enable_llm_live_trading:
        raise LLMError("ENABLE_LLM_LIVE_TRADING must remain false. LLM output is advisory only.")
    if not settings.llm_offline_only:
        raise LLMError("LLM_OFFLINE_ONLY must remain true. LLM output is advisory only.")


require_ollama_enabled = require_offline_llm_enabled
