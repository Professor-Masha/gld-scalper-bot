from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .config import Settings
from .database import Database
from .utils.time_utils import ensure_utc, utc_iso


HIGH_IMPACT_CATEGORIES = {
    "cpi",
    "pce",
    "ppi",
    "inflation",
    "fomc",
    "fed",
    "powell",
    "nfp",
    "jobs",
    "unemployment",
    "gdp",
    "ism",
    "pmi",
    "treasury_auction",
    "central_bank_gold",
    "geopolitical",
    "usd",
    "yields",
}


def event_risk_features(database: Database, settings: Settings, now: datetime) -> dict[str, Any]:
    if not settings.enable_event_calendar:
        return {"event_risk_active": False, "event_risk_reason": ""}
    now = ensure_utc(now)
    lookback = now - timedelta(minutes=settings.event_risk_cooldown_minutes)
    lookahead = now + timedelta(minutes=settings.event_risk_lookahead_minutes)
    rows = database.conn.execute(
        """
        SELECT *
        FROM economic_events
        WHERE scheduled_at >= ?
          AND scheduled_at <= ?
        ORDER BY scheduled_at ASC
        LIMIT 10
        """,
        (utc_iso(lookback), utc_iso(lookahead)),
    ).fetchall()
    if not rows:
        return {"event_risk_active": False, "event_risk_reason": "", "upcoming_event_count": 0}
    events = [dict(row) for row in rows]
    risky = [event for event in events if _is_high_impact(event)]
    active = bool(risky)
    chosen = risky[0] if risky else events[0]
    scheduled = ensure_utc(chosen["scheduled_at"])
    minutes_until = (scheduled - now).total_seconds() / 60
    post_release = active and -settings.event_risk_cooldown_minutes <= minutes_until <= 0
    reason = f"{chosen.get('event_name')} at {scheduled.isoformat()} ({minutes_until:.1f} minutes)"
    return {
        "event_risk_active": active,
        "event_risk_reason": reason if active else "",
        "upcoming_event_count": len(events),
        "next_event_name": chosen.get("event_name"),
        "next_event_category": chosen.get("category"),
        "next_event_importance": chosen.get("importance"),
        "next_event_minutes_until": minutes_until,
        "next_event_surprise_pct": chosen.get("surprise_pct"),
        "next_event_actual": chosen.get("actual_value"),
        "next_event_forecast": chosen.get("forecast"),
        "next_event_previous": chosen.get("previous_value"),
        "next_event_expected_impact": chosen.get("expected_impact"),
        "event_post_release": post_release,
        "event_release_age_seconds": max(0.0, -minutes_until * 60) if post_release else None,
    }


def _is_high_impact(event: dict[str, Any]) -> bool:
    if event.get("avoid_trading"):
        return True
    importance = str(event.get("importance") or "").lower()
    if importance in {"high", "red", "3", "major"}:
        return True
    text = f"{event.get('category') or ''} {event.get('event_name') or ''}".lower()
    return any(term in text for term in HIGH_IMPACT_CATEGORIES)
