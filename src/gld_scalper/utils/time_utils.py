from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
try:
    EASTERN = ZoneInfo("America/New_York")
except Exception:  # Windows without tzdata installed; pyproject includes tzdata for real installs.
    EASTERN = timezone(timedelta(hours=-5), name="America/New_York")


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    else:
        dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def utc_iso(value: datetime | str) -> str:
    return ensure_utc(value).isoformat()


def parse_date(value: str) -> date:
    if value == "today":
        return utc_now().date()
    return date.fromisoformat(value)


def floor_to_minute(value: datetime | None = None) -> datetime:
    dt = ensure_utc(value or utc_now())
    return dt.replace(second=0, microsecond=0)


def seconds_until_next_minute(value: datetime | None = None) -> float:
    dt = ensure_utc(value or utc_now())
    next_minute = floor_to_minute(dt) + timedelta(minutes=1)
    return max(0.0, (next_minute - dt).total_seconds())


def market_session(now: datetime | None = None, extended_hours: bool = False) -> str:
    eastern = ensure_utc(now or utc_now()).astimezone(EASTERN)
    if eastern.weekday() >= 5:
        return "closed"
    current = eastern.time()
    if time(9, 30) <= current < time(16, 0):
        return "regular"
    if extended_hours and time(4, 0) <= current < time(9, 30):
        return "premarket"
    if extended_hours and time(16, 0) <= current < time(20, 0):
        return "afterhours"
    return "closed"


def minutes_since_open(now: datetime | None = None) -> int:
    eastern = ensure_utc(now or utc_now()).astimezone(EASTERN)
    open_dt = eastern.replace(hour=9, minute=30, second=0, microsecond=0)
    return int((eastern - open_dt).total_seconds() // 60)


def minutes_before_close(now: datetime | None = None) -> int:
    eastern = ensure_utc(now or utc_now()).astimezone(EASTERN)
    close_dt = eastern.replace(hour=16, minute=0, second=0, microsecond=0)
    return int((close_dt - eastern).total_seconds() // 60)
