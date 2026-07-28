from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from .config import PROJECT_ROOT, Settings
from .database import Database
from .utils.math_utils import clamp, safe_div
from .utils.time_utils import ensure_utc, utc_now

POSITIVE_GOLD_TERMS = {
    "gold rally",
    "safe haven",
    "inflation",
    "rate cut",
    "dovish",
    "geopolitical",
    "recession",
    "weaker dollar",
    "dollar falls",
    "risk off",
    "central bank buying",
}
NEGATIVE_GOLD_TERMS = {
    "gold falls",
    "strong dollar",
    "dollar rises",
    "hawkish",
    "rate hike",
    "higher yields",
    "risk on",
    "equities rally",
    "profit taking",
}
USD_POSITIVE_TERMS = {"strong dollar", "dollar rises", "usd gains", "higher yields", "hawkish"}
USD_NEGATIVE_TERMS = {"weaker dollar", "dollar falls", "usd slips", "rate cut", "dovish"}
RATES_HAWKISH_TERMS = {"rate hike", "higher for longer", "hawkish", "hot inflation", "higher yields"}
RATES_DOVISH_TERMS = {"rate cut", "dovish", "cooling inflation", "lower yields"}
RISK_OFF_TERMS = {"risk off", "recession", "geopolitical", "war", "bank stress", "safe haven", "selloff"}
EVENT_RISK_TERMS = {"fed", "fomc", "cpi", "jobs report", "nonfarm", "inflation", "powell", "war", "tariff"}


@dataclass(slots=True)
class MacroContextScheduler:
    settings: Settings
    last_update_at: datetime | None = None

    def maybe_update(self, database: Database, now: datetime | None = None, *, force: bool = False) -> dict[str, Any] | None:
        now = ensure_utc(now or utc_now())
        if not self.settings.enable_macro_context and not force:
            return None
        if not force and self.last_update_at is not None:
            due_at = self.last_update_at + timedelta(minutes=self.settings.macro_context_interval_minutes)
            if now < due_at:
                return None
        record = MacroContextBuilder(self.settings, database).build(now=now)
        database.insert_macro_context(record)
        self.last_update_at = now
        return record


class MacroContextBuilder:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database

    def build(self, now: datetime | None = None) -> dict[str, Any]:
        now = ensure_utc(now or utc_now())
        incoming_headlines = self._load_headlines(now)
        for item in incoming_headlines:
            self.database.insert_news_item(item)
        trusted_headlines = self._load_trusted_headlines(now)
        headline_scores = _aggregate_headline_scores(trusted_headlines)
        all_scores = _aggregate_headline_scores(incoming_headlines)
        headline_scores["event_risk"] = max(headline_scores["event_risk"], all_scores["event_risk"])
        proxy_scores = self._market_proxy_scores()

        gold_news = clamp(headline_scores["gold_score"] * 0.65 + proxy_scores["gold_proxy"] * 0.35, -1.0, 1.0)
        usd_sentiment = clamp(headline_scores["usd_score"] * 0.55 + proxy_scores["usd_proxy"] * 0.45, -1.0, 1.0)
        fed_rate = clamp(headline_scores["rates_score"] * 0.65 + proxy_scores["rates_proxy"] * 0.35, -1.0, 1.0)
        risk_off = clamp(headline_scores["risk_score"] * 0.55 + proxy_scores["risk_proxy"] * 0.45, -1.0, 1.0)
        event_risk = clamp(max(headline_scores["event_risk"], proxy_scores["event_proxy"]), 0.0, 1.0)
        alignment = _sentiment_alignment(gold_news, usd_sentiment, fed_rate, risk_off)
        macro_bias, confidence = _macro_bias(gold_news, usd_sentiment, fed_rate, risk_off, event_risk, alignment)
        linked_fraction = len(trusted_headlines) / max(len(incoming_headlines), 1)
        if linked_fraction < self.settings.llm_news_min_linked_fraction:
            confidence = min(confidence, 0.35)
        positives, concerns = _developments_and_concerns(
            gold_news=gold_news,
            usd_sentiment=usd_sentiment,
            fed_rate_sentiment=fed_rate,
            risk_off_sentiment=risk_off,
            headline_event_risk=event_risk,
            macro_bias=macro_bias,
        )
        summary = (
            f"{macro_bias.replace('_', ' ')} with confidence {confidence:.2f}; "
            f"gold={gold_news:.2f}, usd={usd_sentiment:.2f}, rates={fed_rate:.2f}, "
            f"risk_off={risk_off:.2f}, event_risk={event_risk:.2f}"
        )
        return {
            "timestamp": now,
            "horizon": "daily_weekly",
            "gold_news_sentiment": gold_news,
            "usd_sentiment": usd_sentiment,
            "fed_rate_sentiment": fed_rate,
            "risk_off_sentiment": risk_off,
            "headline_event_risk": event_risk,
            "sentiment_alignment": alignment,
            "macro_bias": macro_bias,
            "macro_confidence": confidence,
            "news_linked_fraction": linked_fraction,
            "advisory_only": True,
            "positive_developments": positives,
            "potential_concerns": concerns,
            "forecast_summary": summary,
            "source": "local_fingpt_style_macro_context",
            "raw_inputs": {
                "headline_count": len(incoming_headlines),
                "trusted_headline_count": len(trusted_headlines),
                "headline_scores": headline_scores,
                "market_proxy_scores": proxy_scores,
                "headlines_path": str(_resolve_path(self.settings.macro_context_headlines_path)),
            },
        }

    def _load_trusted_headlines(self, now: datetime) -> list[dict[str, Any]]:
        cutoff = (now - timedelta(days=10)).isoformat()
        return [
            {
                "timestamp": row["timestamp"],
                "headline": row["headline"],
                "gold_score": row["gold_score"],
                "usd_score": row["usd_score"],
                "rates_score": row["rates_score"],
                "risk_score": row["risk_score"],
                "event_risk": row["event_risk"],
            }
            for row in self.database.conn.execute(
                """
                SELECT timestamp, headline, gold_score, usd_score, rates_score, risk_score, event_risk
                FROM news_items
                WHERE sentiment_trusted = 1 AND timestamp >= ?
                ORDER BY julianday(timestamp) DESC, id DESC LIMIT 200
                """,
                (cutoff,),
            ).fetchall()
        ]

    def _load_headlines(self, now: datetime) -> list[dict[str, Any]]:
        path = _resolve_path(self.settings.macro_context_headlines_path)
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        with path.open("r", newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                headline = str(row.get("headline") or row.get("title") or "").strip()
                if not headline:
                    continue
                timestamp = _parse_timestamp(row.get("timestamp") or row.get("date"), now)
                if timestamp < now - timedelta(days=10):
                    continue
                scores = _score_headline(headline)
                records.append(
                    {
                        "timestamp": timestamp,
                        "source": row.get("source") or "local_csv",
                        "headline": headline,
                        "url": row.get("url"),
                        **scores,
                    }
                )
        return records

    def _market_proxy_scores(self) -> dict[str, float]:
        gld = _symbol_roc(self.database, self.settings.bot_symbol)
        uup = _symbol_roc(self.database, "UUP")
        spy = _symbol_roc(self.database, "SPY")
        tlt = _symbol_roc(self.database, "TLT")
        gold_proxy = clamp(gld * 25.0, -1.0, 1.0)
        usd_proxy = clamp(uup * 35.0, -1.0, 1.0)
        rates_proxy = clamp(-tlt * 20.0, -1.0, 1.0)
        risk_proxy = clamp((-spy * 18.0) + (tlt * 8.0), -1.0, 1.0)
        event_proxy = clamp(abs(spy) * 10.0 + abs(gld) * 6.0, 0.0, 1.0)
        return {
            "gold_proxy": gold_proxy,
            "usd_proxy": usd_proxy,
            "rates_proxy": rates_proxy,
            "risk_proxy": risk_proxy,
            "event_proxy": event_proxy,
            "gld_roc": gld,
            "uup_roc": uup,
            "spy_roc": spy,
            "tlt_roc": tlt,
        }


def macro_context_to_features(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "macro_context_available": False,
            "macro_bias": "neutral_environment",
            "macro_confidence": 0.0,
            "gold_news_sentiment": 0.0,
            "usd_sentiment": 0.0,
            "fed_rate_sentiment": 0.0,
            "risk_off_sentiment": 0.0,
            "headline_event_risk": 0.0,
            "sentiment_alignment": 0.0,
            "macro_news_linked_fraction": 0.0,
            "macro_advisory_only": True,
        }
    return {
        "macro_context_available": True,
        "macro_context_timestamp": row.get("timestamp"),
        "macro_bias": row.get("macro_bias") or "neutral_environment",
        "macro_confidence": _f(row.get("macro_confidence")),
        "gold_news_sentiment": _f(row.get("gold_news_sentiment")),
        "usd_sentiment": _f(row.get("usd_sentiment")),
        "fed_rate_sentiment": _f(row.get("fed_rate_sentiment")),
        "risk_off_sentiment": _f(row.get("risk_off_sentiment")),
        "headline_event_risk": _f(row.get("headline_event_risk")),
        "sentiment_alignment": _f(row.get("sentiment_alignment")),
        "macro_news_linked_fraction": _f(row.get("news_linked_fraction")),
        "macro_advisory_only": bool(row.get("advisory_only", 1)),
        "macro_forecast_summary": row.get("forecast_summary"),
        "macro_positive_developments": row.get("positive_developments_json"),
        "macro_potential_concerns": row.get("potential_concerns_json"),
    }


def _resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _parse_timestamp(value: Any, default: datetime) -> datetime:
    if not value:
        return default
    try:
        return ensure_utc(str(value))
    except Exception:
        return default


def _score_headline(headline: str) -> dict[str, float]:
    text = headline.lower()
    gold_score = _keyword_score(text, POSITIVE_GOLD_TERMS, NEGATIVE_GOLD_TERMS)
    usd_score = _keyword_score(text, USD_POSITIVE_TERMS, USD_NEGATIVE_TERMS)
    rates_score = _keyword_score(text, RATES_HAWKISH_TERMS, RATES_DOVISH_TERMS)
    risk_score = clamp(sum(1 for term in RISK_OFF_TERMS if term in text) / 3.0, 0.0, 1.0)
    event_risk = clamp(sum(1 for term in EVENT_RISK_TERMS if term in text) / 3.0, 0.0, 1.0)
    return {
        "gold_score": gold_score,
        "usd_score": usd_score,
        "rates_score": rates_score,
        "risk_score": risk_score,
        "event_risk": event_risk,
    }


def _keyword_score(text: str, positive: set[str], negative: set[str]) -> float:
    pos = sum(1 for term in positive if term in text)
    neg = sum(1 for term in negative if term in text)
    return clamp(safe_div(pos - neg, max(pos + neg, 1)), -1.0, 1.0)


def _aggregate_headline_scores(headlines: list[dict[str, Any]]) -> dict[str, float]:
    keys = ["gold_score", "usd_score", "rates_score", "risk_score", "event_risk"]
    if not headlines:
        return {key: 0.0 for key in keys}
    return {key: mean([_f(item.get(key)) for item in headlines]) for key in keys}


def _symbol_roc(database: Database, symbol: str, limit: int = 390) -> float:
    rows = database.fetch_latest_bars(symbol, "1Min", limit=limit)
    closes = [_f(row.get("close")) for row in rows if _f(row.get("close")) > 0]
    if len(closes) < 2:
        return 0.0
    return safe_div(closes[-1] - closes[0], closes[0])


def _sentiment_alignment(gold_news: float, usd_sentiment: float, fed_rate_sentiment: float, risk_off_sentiment: float) -> float:
    gold_support = gold_news - usd_sentiment * 0.35 - fed_rate_sentiment * 0.25 + risk_off_sentiment * 0.20
    return round(clamp(gold_support, -1.0, 1.0), 4)


def _macro_bias(
    gold_news: float,
    usd_sentiment: float,
    fed_rate_sentiment: float,
    risk_off_sentiment: float,
    headline_event_risk: float,
    sentiment_alignment: float,
) -> tuple[str, float]:
    if headline_event_risk >= 0.75:
        return "event_risk_environment", round(clamp(headline_event_risk, 0.0, 1.0), 4)
    score = gold_news - usd_sentiment * 0.35 - fed_rate_sentiment * 0.25 + risk_off_sentiment * 0.20 + sentiment_alignment * 0.30
    if score >= 0.22:
        return "bullish_gold_environment", round(clamp(abs(score), 0.0, 1.0), 4)
    if score <= -0.22:
        return "bearish_gold_environment", round(clamp(abs(score), 0.0, 1.0), 4)
    return "neutral_environment", round(clamp(abs(score), 0.0, 1.0), 4)


def _developments_and_concerns(
    *,
    gold_news: float,
    usd_sentiment: float,
    fed_rate_sentiment: float,
    risk_off_sentiment: float,
    headline_event_risk: float,
    macro_bias: str,
) -> tuple[list[str], list[str]]:
    positives: list[str] = []
    concerns: list[str] = []
    if gold_news > 0.15:
        positives.append("Gold-related headlines/proxies lean supportive.")
    if usd_sentiment < -0.15:
        positives.append("USD pressure is supportive for GLD.")
    if fed_rate_sentiment < -0.15:
        positives.append("Dovish rates/yields backdrop supports GLD.")
    if risk_off_sentiment > 0.20:
        positives.append("Risk-off tone can support safe-haven demand.")
    if usd_sentiment > 0.15:
        concerns.append("USD strength may cap GLD upside.")
    if fed_rate_sentiment > 0.15:
        concerns.append("Hawkish rates/yields pressure may weigh on GLD.")
    if headline_event_risk > 0.50:
        concerns.append("Headline/event risk is elevated; size should stay conservative.")
    if macro_bias == "neutral_environment":
        concerns.append("Macro backdrop is neutral, so price action must carry the setup.")
    return positives or ["No strong macro tailwind detected."], concerns or ["No major macro concern detected."]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str) and value.startswith("["):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def pretty_macro_context(record: dict[str, Any]) -> str:
    printable = dict(record)
    for key in {"positive_developments", "potential_concerns", "raw_inputs"}:
        if key in printable:
            printable[key] = json.dumps(printable[key], indent=2, sort_keys=True, default=str)
    return json.dumps(printable, indent=2, sort_keys=True, default=str)
