from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, Settings
from .database import Database
from .llm_provider import make_llm_client
from .utils.math_utils import clamp
from .utils.time_utils import ensure_utc, market_session, utc_now


SYSTEM_PROMPT = """You are an offline GLD research council inspired by the TradingAgents workflow.
You have no broker access and must never issue executable order instructions.
Use only supplied evidence, identify conflicts, abstain when evidence is weak, and return strict JSON only.
Price freshness, liquidity, broker reconciliation, and deterministic risk controls always outrank your advice."""


class TradingAgentsAdvisoryService:
    """Slow multi-stage Ollama research with no imports from broker modules."""

    def __init__(self, settings: Settings, database: Database, client: Any | None = None) -> None:
        self.settings = settings
        self.database = database
        self.client = client or make_llm_client(settings)

    def run(
        self,
        *,
        horizon: str = "hourly",
        now: datetime | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        now = ensure_utc(now or utc_now())
        self._assert_offline(now, force=force)
        evidence = self._evidence_packet()
        analyst_reports = self._ask(
            {
                "stage": "specialist_analysts",
                "task": "Produce separate technical, macro/news, and journal/execution assessments.",
                "required_keys": [
                    "technical_context",
                    "macro_news_context",
                    "journal_execution_context",
                    "evidence_ids",
                ],
                "horizon": horizon,
                "evidence": evidence,
            }
        )
        debate = self._ask(
            {
                "stage": "bull_bear_debate",
                "task": "State the strongest bull case, strongest bear case, unresolved conflicts, and whether evidence supports abstention.",
                "required_keys": ["bull_case", "bear_case", "unresolved_conflicts", "abstain"],
                "analyst_reports": analyst_reports,
            }
        )
        manager = self._ask(
            {
                "stage": "risk_and_context_manager",
                "task": "Create a bounded intraday GLD context advisory. It cannot override live safety controls.",
                "allowed_biases": ["bullish", "bearish", "neutral", "event_risk"],
                "required_keys": [
                    "bias",
                    "confidence",
                    "abstain",
                    "event_risk",
                    "risk_flags",
                    "size_multiplier",
                    "summary",
                ],
                "constraints": {
                    "confidence": "0 to 1",
                    "event_risk": "0 to 1",
                    "size_multiplier": (
                        f"{1.0 - self.settings.tradingagents_max_sizing_adjustment:.2f} to "
                        f"{1.0 + self.settings.tradingagents_max_sizing_adjustment:.2f}"
                    ),
                    "abstention": "Use true when evidence conflicts, lacks outcomes, or is weak.",
                },
                "analyst_reports": analyst_reports,
                "debate": debate,
            }
        )
        bias = str(manager.get("bias") or "neutral").lower()
        if bias not in {"bullish", "bearish", "neutral", "event_risk"}:
            bias = "neutral"
        confidence = _bounded(manager.get("confidence"), 0.0, 1.0)
        event_risk = _bounded(manager.get("event_risk"), 0.0, 1.0)
        abstain = bool(manager.get("abstain", debate.get("abstain", True)))
        if confidence < self.settings.tradingagents_min_confidence:
            abstain = True
        adjustment = self.settings.tradingagents_max_sizing_adjustment
        size_multiplier = _bounded(manager.get("size_multiplier"), 1.0 - adjustment, 1.0 + adjustment, default=1.0)
        if abstain or bias in {"neutral", "event_risk"}:
            size_multiplier = min(size_multiplier, 1.0)
        expires_at = now + timedelta(minutes=self.settings.tradingagents_advisory_max_age_minutes)
        record = {
            "timestamp": now,
            "symbol": self.settings.bot_symbol,
            "horizon": horizon,
            "bias": bias,
            "confidence": confidence,
            "abstain": abstain,
            "event_risk": event_risk,
            "size_multiplier": size_multiplier,
            "summary": str(manager.get("summary") or ""),
            "bull_case": str(debate.get("bull_case") or ""),
            "bear_case": str(debate.get("bear_case") or ""),
            "risk_flags": _strings(manager.get("risk_flags")),
            "evidence_ids": _strings(analyst_reports.get("evidence_ids")),
            "provider": getattr(self.client, "provider", self.settings.llm_provider),
            "model": getattr(self.client, "model", self.settings.llm_model),
            "source_fingerprint": self._source_fingerprint(),
            "expires_at": expires_at,
            "advisory_only": True,
            "raw_response": {
                "analyst_reports": analyst_reports,
                "debate": debate,
                "manager": manager,
            },
        }
        record["id"] = self.database.insert_agent_advisory(record)
        return record

    def _assert_offline(self, now: datetime, *, force: bool) -> None:
        if self.settings.enable_llm_live_trading:
            raise RuntimeError("TradingAgents/Ollama broker access is prohibited")
        if self.database.fetch_active_execution_episodes(self.settings.bot_symbol):
            raise RuntimeError("TradingAgents advisory refused while execution episodes are active")
        if not force and market_session(now, extended_hours=False) == "regular":
            raise RuntimeError("TradingAgents advisory is restricted to after-market hours")

    def _ask(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.chat_json(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "response_instruction": "Return exactly one JSON object with the requested keys and no markdown.",
                    "request": payload,
                },
                sort_keys=True,
                default=str,
            ),
        )
        return response.json_content()

    def _evidence_packet(self) -> dict[str, Any]:
        limit = min(max(self.settings.llm_max_context_rows, 5), 40)
        tables = (
            "signals",
            "trade_outcomes",
            "trade_reviews",
            "missed_opportunities",
            "macro_context",
            "news_items",
            "model_drift_reports",
            "performance_consistency_audits",
        )
        evidence: dict[str, Any] = {}
        for table in tables:
            try:
                rows = self.database.fetch_recent_table_rows(table, limit=limit)
            except Exception:
                rows = []
            evidence[table] = [
                {"evidence_id": f"{table}:{row.get('id', index)}", **row}
                for index, row in enumerate(rows)
            ]
        return evidence

    def _source_fingerprint(self) -> str | None:
        root = Path(self.settings.tradingagents_source_dir)
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        head = root / ".git" / "HEAD"
        if not head.exists():
            return None
        value = head.read_text(encoding="utf-8", errors="ignore").strip()
        if value.startswith("ref:"):
            ref = root / ".git" / value.split(":", 1)[1].strip()
            if ref.exists():
                return ref.read_text(encoding="utf-8", errors="ignore").strip()
        return value or None


def agent_advisory_to_features(
    row: dict[str, Any] | None,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = ensure_utc(now or utc_now())
    neutral = {
        "tradingagents_advisory_available": False,
        "tradingagents_advisory_stale": True,
        "tradingagents_advisory_bias": "neutral",
        "tradingagents_advisory_confidence": 0.0,
        "tradingagents_advisory_abstain": True,
        "tradingagents_advisory_event_risk": 0.0,
        "tradingagents_advisory_score_adjustment": 0.0,
        "tradingagents_advisory_size_multiplier": 1.0,
    }
    if not row:
        return neutral
    expires_at = ensure_utc(row["expires_at"])
    stale = now > expires_at
    confidence = _bounded(row.get("confidence"), 0.0, 1.0)
    abstain = bool(row.get("abstain", 1))
    bias = str(row.get("bias") or "neutral")
    eligible = (
        not stale
        and not abstain
        and confidence >= settings.tradingagents_min_confidence
        and bias in {"bullish", "bearish"}
    )
    direction = 1.0 if bias == "bullish" else -1.0 if bias == "bearish" else 0.0
    score_adjustment = (
        direction * min(settings.tradingagents_max_score_adjustment, confidence * settings.tradingagents_max_score_adjustment)
        if eligible
        else 0.0
    )
    size_multiplier = _bounded(
        row.get("size_multiplier"),
        1.0 - settings.tradingagents_max_sizing_adjustment,
        1.0 + settings.tradingagents_max_sizing_adjustment,
        default=1.0,
    )
    if not eligible:
        size_multiplier = min(size_multiplier, 1.0)
    return {
        "tradingagents_advisory_available": True,
        "tradingagents_advisory_stale": stale,
        "tradingagents_advisory_id": row.get("id"),
        "tradingagents_advisory_timestamp": row.get("timestamp"),
        "tradingagents_advisory_expires_at": row.get("expires_at"),
        "tradingagents_advisory_bias": bias,
        "tradingagents_advisory_confidence": confidence,
        "tradingagents_advisory_abstain": abstain,
        "tradingagents_advisory_event_risk": _bounded(row.get("event_risk"), 0.0, 1.0),
        "tradingagents_advisory_score_adjustment": round(score_adjustment, 4),
        "tradingagents_advisory_size_multiplier": round(size_multiplier, 4),
        "tradingagents_advisory_summary": row.get("summary"),
        "tradingagents_advisory_only": True,
    }


def _bounded(value: Any, low: float, high: float, *, default: float = 0.0) -> float:
    try:
        return float(clamp(float(value), low, high))
    except (TypeError, ValueError):
        return default


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return [] if value is None else [str(value)]
    return [str(item) for item in value]
