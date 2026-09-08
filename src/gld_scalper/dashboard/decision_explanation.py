from __future__ import annotations

from typing import Any


_RULES: tuple[tuple[str, str, str, str], ...] = (
    ("market is closed", "MARKET_CLOSED", "Market clock", "The broker reports that the US market is closed. No strategy evaluation or order is permitted."),
    ("market clock unavailable", "MARKET_CLOCK_UNAVAILABLE", "Market clock", "The broker market clock could not be verified, so trading remains blocked."),
    ("timestamps are not aligned", "DATA_MISALIGNED", "Data safety", "Quotes, trades and bars do not describe the same market moment."),
    ("quote/trade age", "DATA_STALE", "Data safety", "The latest GLD quote or trade is older than the permitted limit."),
    ("stale", "DATA_STALE", "Data safety", "Live market data is too old to trust."),
    ("disconnect", "STREAM_DISCONNECTED", "Data safety", "The live market stream is disconnected."),
    ("spread too wide", "SPREAD_WIDE", "Liquidity and costs", "The bid/ask spread makes the trade uneconomic."),
    ("spread regime wide", "SPREAD_WIDE", "Liquidity and costs", "The spread is in its wide regime."),
    ("liquidity", "LIQUIDITY_WEAK", "Liquidity and costs", "Available liquidity is below the entry standard."),
    ("volume too low", "VOLUME_LOW", "Liquidity and costs", "Trading volume is too low for this setup."),
    ("trade intensity", "TRADE_INTENSITY_LOW", "Liquidity and costs", "Recent trade activity is too weak."),
    ("chopping", "PRICE_CHOP", "Price action", "Price is moving sideways around VWAP."),
    ("compressed range", "COMPRESSION_UNCONFIRMED", "Price action", "Compression has not produced a confirmed break."),
    ("proper break", "BREAK_UNCONFIRMED", "Price action", "The required breakout confirmation is missing."),
    ("1m and 5m disagree", "TIMEFRAME_CONFLICT", "Price action", "The one-minute and five-minute trends disagree."),
    ("poor_liquidity", "REGIME_BLOCKED", "Market regime", "The current regime is classified as poor liquidity."),
    ("sideways_chop", "REGIME_BLOCKED", "Market regime", "The current regime is classified as sideways chop."),
    ("volatility burst", "VOLATILITY_BURST", "Market regime", "A volatility burst lacks enough confirmation."),
    ("no champion model", "MODEL_UNAVAILABLE", "Model evidence", "No approved champion model supports this setup."),
    ("confidence", "MODEL_CONFIDENCE_LOW", "Model evidence", "Model confidence is below its entry threshold."),
    ("event risk", "EVENT_RISK", "Risk controls", "Scheduled or headline event risk blocks entry."),
    ("risk block", "RISK_VETO", "Risk controls", "A mandatory risk control vetoed the trade."),
)


def explain_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """Convert machine-oriented reason text into stable, deduplicated UI evidence."""
    raw = str(decision.get("reason") or "").strip()
    fragments = [part.strip() for part in raw.split(";") if part.strip()]
    seen: set[str] = set()
    groups: dict[str, list[dict[str, str]]] = {}
    for fragment in fragments:
        lowered = fragment.lower()
        match = next((rule for rule in _RULES if rule[0] in lowered), None)
        if match:
            _, code, category, message = match
        else:
            code = "OTHER_EVIDENCE"
            category = "Other evidence"
            message = fragment[:1].upper() + fragment[1:]
        if code in seen:
            continue
        seen.add(code)
        groups.setdefault(category, []).append({"code": code, "message": message, "source": fragment})

    action = str(decision.get("decision") or "NO_TRADE").upper()
    confidence = _number(decision.get("confidence"))
    primary = next((item for items in groups.values() for item in items), None)
    summary = primary["message"] if primary else ("No detailed reason was recorded." if not raw else raw)
    if action == "NO_TRADE":
        headline = "Entry withheld because one or more mandatory checks failed."
    else:
        headline = f"{action.replace('_', ' ').title()} was approved by the decision and risk pipeline."
    return {
        "action": action,
        "confidence": confidence,
        "directional_rule_strength": _number(decision.get("directional_rule_strength")),
        "directional_rule_strength_label": str(decision.get("confidence_label") or "Directional rule strength"),
        "ml_inference_skipped": bool(decision.get("ml_inference_skipped")),
        "ml_inference_skip_reason": decision.get("ml_inference_skip_reason"),
        "headline": headline,
        "summary": summary,
        "primary_code": primary["code"] if primary else None,
        "groups": [{"title": title, "items": items} for title, items in groups.items()],
        "raw_reason": raw,
    }


def _number(value: Any) -> float | None:
    try:
        return round(float(value), 6) if value is not None else None
    except (TypeError, ValueError):
        return None
