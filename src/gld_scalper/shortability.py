from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .alpaca_clients import get_trading_client
from .config import Settings, load_settings


@dataclass(slots=True)
class ShortabilityResult:
    allowed: bool
    reason: str


def check_asset_shortability(asset: Any, *, buying_power: float | None = None, required_notional: float = 0.0) -> ShortabilityResult:
    symbol = str(getattr(asset, "symbol", "")).upper()
    if symbol != "GLD":
        return ShortabilityResult(False, "only GLD may be shorted by this bot")
    if not bool(getattr(asset, "status", "active") == "active" or getattr(asset, "active", False)):
        return ShortabilityResult(False, "GLD asset is not active")
    if not bool(getattr(asset, "tradable", False)):
        return ShortabilityResult(False, "GLD is not tradable")
    if not bool(getattr(asset, "marginable", False)):
        return ShortabilityResult(False, "GLD is not marginable")
    if not bool(getattr(asset, "shortable", False)):
        return ShortabilityResult(False, "GLD is not shortable")
    if buying_power is not None and buying_power < required_notional:
        return ShortabilityResult(False, "insufficient buying power for short")
    return ShortabilityResult(True, "GLD shortability checks passed")


def can_short(symbol: str = "GLD", settings: Settings | None = None, required_notional: float = 0.0) -> ShortabilityResult:
    settings = settings or load_settings()
    if not settings.enable_shorts:
        return ShortabilityResult(False, "shorts disabled by configuration")
    if symbol.upper() != "GLD":
        return ShortabilityResult(False, "bot may only short GLD")
    client = get_trading_client(settings)
    asset = client.get_asset(symbol.upper())
    try:
        account = client.get_account()
        buying_power = float(getattr(account, "buying_power", 0.0))
    except Exception:
        buying_power = None
    return check_asset_shortability(asset, buying_power=buying_power, required_notional=required_notional)
