from __future__ import annotations

from typing import Any

from .utils.math_utils import clamp


INFLATION_TERMS = {"cpi", "pce", "ppi", "inflation"}
JOBS_TERMS = {"nfp", "jobs", "employment", "unemployment", "payroll"}
FED_TERMS = {"fomc", "fed", "powell", "rate decision"}
GDP_TERMS = {"gdp", "growth"}
GEOPOLITICAL_TERMS = {"war", "conflict", "sanction", "geopolitical", "missile", "invasion"}


def build_gold_event_impact(features: dict[str, Any]) -> dict[str, Any]:
    """Create a conservative event prior, confirmed by post-release live tape.

    Macro relationships are conditional rather than guaranteed. This function
    therefore never emits a direction before release and abstains when the
    macro prior conflicts with quote/trade pressure.
    """

    text = " ".join(
        str(features.get(key) or "")
        for key in ("next_event_name", "next_event_category", "next_event_expected_impact", "macro_bias")
    ).lower()
    category = _category(text)
    surprise = _f(features.get("next_event_surprise_pct"))
    prior = 0.0
    rationale = "event has no deterministic gold prior"
    if category == "inflation" and surprise:
        prior = -_sign(surprise)
        rationale = "upside inflation surprise can lift rate/USD pressure; downside surprise reverses that prior"
    elif category == "jobs" and surprise:
        prior = -_sign(surprise)
        rationale = "stronger jobs can lift rate/USD pressure; weaker jobs reverse that prior"
    elif category == "gdp" and surprise:
        prior = -_sign(surprise)
        rationale = "growth surprise is treated as a weak rates-sensitive prior"
    elif category == "fed":
        if any(term in text for term in ("hawkish", "higher for longer", "rate hike")):
            prior = -1.0
        elif any(term in text for term in ("dovish", "rate cut", "easing")):
            prior = 1.0
        rationale = "Fed language prior requires post-release tape confirmation"
    elif category == "geopolitical":
        prior = 1.0
        rationale = "geopolitical risk creates a weak risk-off gold prior"
    elif category == "central_bank_gold":
        prior = 1.0
        rationale = "reported central-bank gold demand creates a weak bullish prior"
    elif category == "usd_yields":
        usd_pressure = _f(features.get("UUP_roc")) + _f(features.get("real_yield_change"))
        prior = -_sign(usd_pressure) if usd_pressure else 0.0
        rationale = "USD and real-yield direction creates a weak inverse gold prior"

    tape = _tape_direction(features)
    post_release = bool(features.get("event_post_release"))
    volatility = bool(features.get("volatility_burst"))
    liquid = _f(features.get("liquidity_score"), 0.0) >= 0.45 and str(features.get("spread_regime") or "") != "wide"
    aligned = prior == 0.0 or tape == prior
    confirmed = post_release and volatility and liquid and tape != 0.0 and aligned
    direction = "LONG" if confirmed and tape > 0 else "SHORT" if confirmed and tape < 0 else "NO_TRADE"
    confidence = clamp(
        (0.30 if post_release else 0.0)
        + (0.20 if volatility else 0.0)
        + (0.20 if liquid else 0.0)
        + (0.20 if aligned and tape else 0.0)
        + min(abs(surprise), 1.0) * 0.10,
        0.0,
        1.0,
    )
    return {
        "event_gold_category": category,
        "event_gold_prior": prior,
        "event_tape_direction": tape,
        "event_gold_direction": direction,
        "event_gold_confidence": round(confidence, 6),
        "event_gold_confirmed": confirmed,
        "event_gold_reason": rationale if aligned else f"{rationale}; live tape conflicts, so abstain",
    }


def _category(text: str) -> str:
    if any(term in text for term in INFLATION_TERMS):
        return "inflation"
    if any(term in text for term in JOBS_TERMS):
        return "jobs"
    if any(term in text for term in FED_TERMS):
        return "fed"
    if any(term in text for term in GDP_TERMS):
        return "gdp"
    if any(term in text for term in GEOPOLITICAL_TERMS):
        return "geopolitical"
    if any(term in text for term in ("dollar", "usd", "yield", "treasury")):
        return "usd_yields"
    if "central bank" in text and "gold" in text:
        return "central_bank_gold"
    return "other"


def _tape_direction(features: dict[str, Any]) -> float:
    votes = [
        _sign(_f(features.get("quote_imbalance"))),
        _sign(_f(features.get("signed_volume"))),
        _sign(_f(features.get("micro_price_pressure"))),
    ]
    score = sum(votes)
    return 1.0 if score >= 2 else -1.0 if score <= -2 else 0.0


def _sign(value: float) -> float:
    return 1.0 if value > 0 else -1.0 if value < 0 else 0.0


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default
