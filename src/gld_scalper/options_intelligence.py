from __future__ import annotations

import json
import logging
import math
import re
import threading
from datetime import date, datetime, timedelta
from statistics import mean
from typing import Any, Callable, Mapping

from .alpaca_clients import get_option_historical_client
from .config import Settings, load_settings
from .database import Database
from .utils.math_utils import clamp, safe_div
from .utils.time_utils import ensure_utc, utc_now

logger = logging.getLogger(__name__)

_OCC_PATTERN = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


class GLDOptionsIntelligenceCollector:
    """Collect and summarize GLD option-chain snapshots without placing option orders."""

    def __init__(
        self,
        settings: Settings | None = None,
        database: Database | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.database = database or Database(settings=self.settings)
        self.client = client or get_option_historical_client(self.settings)

    def collect(self, underlying_price: float, *, now: datetime | None = None) -> dict[str, Any]:
        now = ensure_utc(now or utc_now())
        if underlying_price <= 0:
            raise ValueError("A positive GLD price is required for option-chain collection.")
        chain = self.client.get_option_chain(self._request(underlying_price, now.date()))
        snapshots = [
            row
            for symbol, snapshot in dict(chain or {}).items()
            if (row := self._normalize_snapshot(str(symbol), snapshot, underlying_price, now)) is not None
        ]
        snapshots.sort(key=lambda row: (row["days_to_expiration"], abs(row["strike_price"] - underlying_price), row["option_type"]))
        snapshots = _balanced_limit(snapshots, self.settings.options_max_contracts)
        if snapshots:
            self.database.upsert_option_snapshots(snapshots)
        intelligence = build_options_intelligence(
            snapshots,
            underlying_price=underlying_price,
            now=now,
            settings=self.settings,
        )
        self.database.insert_options_intelligence(intelligence)
        return intelligence

    def _request(self, underlying_price: float, today: date) -> Any:
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.requests import OptionChainRequest

        feed = OptionsFeed.OPRA if self.settings.options_feed == "opra" else OptionsFeed.INDICATIVE
        strike_window = self.settings.options_strike_window_pct
        return OptionChainRequest(
            underlying_symbol=self.settings.options_underlying,
            feed=feed,
            strike_price_gte=round(underlying_price * (1 - strike_window), 2),
            strike_price_lte=round(underlying_price * (1 + strike_window), 2),
            expiration_date_gte=today,
            expiration_date_lte=today + timedelta(days=self.settings.options_expiration_days),
        )

    def _normalize_snapshot(
        self,
        contract_symbol: str,
        snapshot: Any,
        underlying_price: float,
        now: datetime,
    ) -> dict[str, Any] | None:
        contract = parse_occ_contract(contract_symbol)
        if contract is None:
            return None
        quote = _value(snapshot, "latest_quote")
        trade = _value(snapshot, "latest_trade")
        greeks = _value(snapshot, "greeks")
        quote_timestamp = _datetime_value(quote, "timestamp")
        trade_timestamp = _datetime_value(trade, "timestamp")
        timestamp = max((item for item in (quote_timestamp, trade_timestamp) if item is not None), default=now)
        bid = _number(quote, "bid_price")
        ask = _number(quote, "ask_price")
        midpoint = (bid + ask) / 2 if bid > 0 and ask >= bid else 0.0
        spread_pct = safe_div(ask - bid, midpoint) if midpoint > 0 else None
        expiration = contract["expiration_date"]
        return {
            "timestamp": timestamp,
            "underlying_symbol": self.settings.options_underlying,
            "underlying_price": underlying_price,
            "contract_symbol": contract_symbol,
            "option_type": contract["option_type"],
            "expiration_date": expiration.isoformat(),
            "strike_price": contract["strike_price"],
            "days_to_expiration": max((expiration - now.date()).days, 0),
            "bid_price": bid or None,
            "ask_price": ask or None,
            "bid_size": _number(quote, "bid_size"),
            "ask_size": _number(quote, "ask_size"),
            "midpoint": midpoint or None,
            "spread_pct": spread_pct,
            "last_trade_price": _number(trade, "price") or None,
            "last_trade_size": _number(trade, "size"),
            "last_trade_timestamp": trade_timestamp,
            "implied_volatility": _number(snapshot, "implied_volatility") or None,
            "delta": _number(greeks, "delta") if greeks is not None else None,
            "gamma": _number(greeks, "gamma") if greeks is not None else None,
            "theta": _number(greeks, "theta") if greeks is not None else None,
            "vega": _number(greeks, "vega") if greeks is not None else None,
            "quote_age_seconds": max(0.0, (now - quote_timestamp).total_seconds()) if quote_timestamp else None,
            "feed": self.settings.options_feed,
            "source": "alpaca_option_chain",
            "raw": _model_dump(snapshot),
        }


def build_options_intelligence(
    snapshots: list[dict[str, Any]],
    *,
    underlying_price: float,
    now: datetime,
    settings: Settings,
) -> dict[str, Any]:
    usable = [
        row
        for row in snapshots
        if _usable_quote(row, settings.options_max_quote_age_seconds, settings.options_max_spread_pct)
    ]
    calls = [row for row in usable if row["option_type"] == "call"]
    puts = [row for row in usable if row["option_type"] == "put"]
    call_activity = sum(_fresh_trade_size(row, now, settings.options_max_quote_age_seconds) for row in calls)
    put_activity = sum(_fresh_trade_size(row, now, settings.options_max_quote_age_seconds) for row in puts)
    activity_ratio = safe_div(call_activity + 1.0, put_activity + 1.0, default=1.0)

    call_aggressive, call_passive = _aggressive_flow(calls, now, settings.options_max_quote_age_seconds)
    put_aggressive, put_passive = _aggressive_flow(puts, now, settings.options_max_quote_age_seconds)
    directional_flow = (call_aggressive - call_passive) - (put_aggressive - put_passive)
    total_flow = call_aggressive + call_passive + put_aggressive + put_passive
    flow_score = safe_div(directional_flow, total_flow)
    activity_score = clamp(math.log(max(activity_ratio, 0.01)) / math.log(4), -1.0, 1.0)

    atm = sorted(usable, key=lambda row: abs(float(row["strike_price"]) - underlying_price))[:8]
    atm_ivs = [float(row["implied_volatility"]) for row in atm if _positive(row.get("implied_volatility"))]
    atm_iv = mean(atm_ivs) if atm_ivs else 0.0
    call_ivs = [float(row["implied_volatility"]) for row in calls if _near_money(row, underlying_price) and _positive(row.get("implied_volatility"))]
    put_ivs = [float(row["implied_volatility"]) for row in puts if _near_money(row, underlying_price) and _positive(row.get("implied_volatility"))]
    put_call_iv_skew = (mean(put_ivs) - mean(call_ivs)) if call_ivs and put_ivs else 0.0
    skew_score = -clamp(put_call_iv_skew / 0.10, -1.0, 1.0)

    nearest_dte = min((int(row["days_to_expiration"]) for row in atm), default=0)
    expected_move_pct = atm_iv * math.sqrt(max(nearest_dte, 1) / 365.0) if atm_iv > 0 else 0.0
    liquidity_scores = [_option_liquidity(row, settings) for row in usable]
    liquidity_score = mean(liquidity_scores) if liquidity_scores else 0.0
    freshness = safe_div(len(usable), len(snapshots)) if snapshots else 0.0
    confidence = clamp(freshness * 0.45 + liquidity_score * 0.35 + min(len(usable) / 20.0, 1.0) * 0.20, 0.0, 1.0)
    raw_score = clamp(flow_score * 0.55 + activity_score * 0.25 + skew_score * 0.20, -1.0, 1.0)
    options_bias = "bullish" if raw_score >= 0.15 else "bearish" if raw_score <= -0.15 else "neutral"
    event_risk = clamp(expected_move_pct / 0.08 + max(atm_iv - 0.35, 0.0), 0.0, 1.0)
    score_adjustment = clamp(
        raw_score * confidence * settings.options_max_score_adjustment,
        -settings.options_max_score_adjustment,
        settings.options_max_score_adjustment,
    )
    stale = not usable
    reason = (
        "no fresh, liquid option-chain quotes"
        if stale
        else (
            f"{options_bias} GLD options context; contracts={len(usable)}; "
            f"activity_ratio={activity_ratio:.2f}; atm_iv={atm_iv:.3f}; liquidity={liquidity_score:.2f}"
        )
    )
    features = {
        "options_bias": options_bias if not stale else "neutral",
        "options_raw_score": round(raw_score if not stale else 0.0, 6),
        "options_confidence": round(confidence if not stale else 0.0, 6),
        "options_call_put_activity_ratio": round(activity_ratio, 6),
        "options_call_aggressive_flow": round(call_aggressive, 6),
        "options_put_aggressive_flow": round(put_aggressive, 6),
        "options_atm_iv": round(atm_iv, 8),
        "options_put_call_iv_skew": round(put_call_iv_skew, 8),
        "options_expected_move_pct": round(expected_move_pct, 8),
        "options_liquidity_score": round(liquidity_score, 6),
        "options_event_risk": round(event_risk, 6),
        "options_score_adjustment": round(score_adjustment if not stale else 0.0, 6),
        "options_contracts_analyzed": len(usable),
        "options_snapshot_count": len(snapshots),
        "options_stale": stale,
        "options_feed": settings.options_feed,
        "options_reason": reason,
    }
    return {
        "timestamp": now,
        "underlying_symbol": settings.options_underlying,
        "underlying_price": underlying_price,
        "contracts_analyzed": len(usable),
        "options_bias": features["options_bias"],
        "confidence": features["options_confidence"],
        "call_put_activity_ratio": features["options_call_put_activity_ratio"],
        "call_aggressive_flow": features["options_call_aggressive_flow"],
        "put_aggressive_flow": features["options_put_aggressive_flow"],
        "atm_iv": features["options_atm_iv"],
        "put_call_iv_skew": features["options_put_call_iv_skew"],
        "expected_move_pct": features["options_expected_move_pct"],
        "options_liquidity_score": features["options_liquidity_score"],
        "options_event_risk": features["options_event_risk"],
        "score_adjustment": features["options_score_adjustment"],
        "stale": stale,
        "reason": reason,
        "feed": settings.options_feed,
        "source": "alpaca_option_chain_v1",
        "features": features,
    }


def options_intelligence_to_features(
    record: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 180,
) -> dict[str, Any]:
    if not record:
        return _empty_options_features("options intelligence unavailable")
    now = ensure_utc(now or utc_now())
    timestamp = ensure_utc(record["timestamp"])
    age = max(0.0, (now - timestamp).total_seconds())
    raw_features = record.get("features", record.get("features_json", {}))
    if isinstance(raw_features, str):
        try:
            raw_features = json.loads(raw_features)
        except json.JSONDecodeError:
            raw_features = {}
    features = _empty_options_features(str(record.get("reason") or "options intelligence available"))
    if isinstance(raw_features, Mapping):
        features.update(dict(raw_features))
    features["options_intelligence_age_seconds"] = round(age, 3)
    if age > max_age_seconds:
        features.update(
            {
                "options_bias": "neutral",
                "options_confidence": 0.0,
                "options_score_adjustment": 0.0,
                "options_stale": True,
                "options_reason": f"options intelligence stale ({age:.1f}s)",
            }
        )
    return features


class OptionsIntelligenceRuntime:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        feature_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.feature_sink = feature_sink
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._status: dict[str, Any] = {"thread_alive": False, "last_success_at": None, "last_error": None}

    def start(self) -> None:
        if not self.settings.enable_options_intelligence:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="gld-options-intelligence", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=10)

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {**self._status, "thread_alive": self._thread is not None and self._thread.is_alive()}

    def _run(self) -> None:
        database = Database(settings=self.settings)
        database.init_db()
        try:
            collector = GLDOptionsIntelligenceCollector(self.settings, database)
            while not self._stop_event.is_set():
                try:
                    underlying_price = _latest_underlying_price(database, self.settings.options_underlying)
                    if underlying_price > 0:
                        record = collector.collect(underlying_price)
                        features = options_intelligence_to_features(
                            record,
                            max_age_seconds=self.settings.options_max_quote_age_seconds,
                        )
                        if self.feature_sink is not None:
                            self.feature_sink(features)
                        with self._lock:
                            self._status.update(
                                {
                                    "last_success_at": ensure_utc(record["timestamp"]).isoformat(),
                                    "last_error": None,
                                    "contracts_analyzed": record["contracts_analyzed"],
                                    "options_bias": record["options_bias"],
                                }
                            )
                    else:
                        with self._lock:
                            self._status["last_error"] = "GLD price unavailable"
                except Exception as exc:  # pragma: no cover - network/runtime guard
                    logger.warning("GLD options intelligence collection failed: %s", exc)
                    with self._lock:
                        self._status["last_error"] = str(exc)
                    try:
                        database.log_event(
                            "WARNING",
                            __name__,
                            "options_intelligence_failed",
                            str(exc),
                            {"feed": self.settings.options_feed},
                        )
                    except Exception:
                        pass
                self._stop_event.wait(self.settings.options_poll_interval_seconds)
        finally:
            database.close()


def parse_occ_contract(symbol: str) -> dict[str, Any] | None:
    match = _OCC_PATTERN.fullmatch(symbol.upper())
    if match is None:
        return None
    _, expiry, kind, strike = match.groups()
    expiration = datetime.strptime(expiry, "%y%m%d").date()
    return {
        "expiration_date": expiration,
        "option_type": "call" if kind == "C" else "put",
        "strike_price": int(strike) / 1_000.0,
    }


def _balanced_limit(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or len(rows) <= limit:
        return rows
    calls = [row for row in rows if row["option_type"] == "call"]
    puts = [row for row in rows if row["option_type"] == "put"]
    half = max(limit // 2, 1)
    selected = [*calls[:half], *puts[:half]]
    if len(selected) < limit:
        selected_ids = {row["contract_symbol"] for row in selected}
        selected.extend(row for row in rows if row["contract_symbol"] not in selected_ids)
    return selected[:limit]


def _usable_quote(row: Mapping[str, Any], max_age: int, max_spread: float) -> bool:
    bid = _as_float(row.get("bid_price"))
    ask = _as_float(row.get("ask_price"))
    age = _as_float(row.get("quote_age_seconds"), default=1_000_000.0)
    spread = _as_float(row.get("spread_pct"), default=1_000_000.0)
    return bid > 0 and ask >= bid and age <= max_age and spread <= max_spread


def _fresh_trade_size(row: Mapping[str, Any], now: datetime, max_age: int) -> float:
    timestamp = row.get("last_trade_timestamp")
    if not timestamp or (now - ensure_utc(timestamp)).total_seconds() > max_age:
        return 0.0
    return max(_as_float(row.get("last_trade_size")), 0.0)


def _aggressive_flow(rows: list[dict[str, Any]], now: datetime, max_age: int) -> tuple[float, float]:
    aggressive = 0.0
    passive = 0.0
    for row in rows:
        size = _fresh_trade_size(row, now, max_age)
        price = _as_float(row.get("last_trade_price"))
        bid = _as_float(row.get("bid_price"))
        ask = _as_float(row.get("ask_price"))
        if size <= 0 or price <= 0 or ask < bid:
            continue
        threshold = max((ask - bid) * 0.20, 0.001)
        if price >= ask - threshold:
            aggressive += size
        elif price <= bid + threshold:
            passive += size
    return aggressive, passive


def _option_liquidity(row: Mapping[str, Any], settings: Settings) -> float:
    spread = _as_float(row.get("spread_pct"), default=settings.options_max_spread_pct)
    depth = _as_float(row.get("bid_size")) + _as_float(row.get("ask_size"))
    age = _as_float(row.get("quote_age_seconds"), default=settings.options_max_quote_age_seconds)
    return clamp(
        1.0
        - clamp(safe_div(spread, max(settings.options_max_spread_pct, 0.0001)), 0.0, 1.0) * 0.55
        - clamp(safe_div(age, max(settings.options_max_quote_age_seconds, 1)), 0.0, 1.0) * 0.25
        + clamp(depth / 500.0, 0.0, 0.20),
        0.0,
        1.0,
    )


def _near_money(row: Mapping[str, Any], underlying_price: float) -> bool:
    return abs(safe_div(_as_float(row.get("strike_price")) - underlying_price, underlying_price)) <= 0.03


def _latest_underlying_price(database: Database, symbol: str) -> float:
    quote = database.get_latest_quote(symbol)
    if quote:
        bid = _as_float(quote.get("bid_price"))
        ask = _as_float(quote.get("ask_price"))
        if bid > 0 and ask >= bid:
            return (bid + ask) / 2
    bar = database.get_latest_bar(symbol, "1Min")
    return _as_float(bar.get("close")) if bar else 0.0


def _value(value: Any, name: str) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _number(value: Any, name: str) -> float:
    return _as_float(_value(value, name))


def _datetime_value(value: Any, name: str) -> datetime | None:
    raw = _value(value, name)
    return ensure_utc(raw) if raw is not None else None


def _model_dump(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    return {"repr": repr(value)}


def _positive(value: Any) -> bool:
    return _as_float(value) > 0


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _empty_options_features(reason: str) -> dict[str, Any]:
    return {
        "options_bias": "neutral",
        "options_raw_score": 0.0,
        "options_confidence": 0.0,
        "options_call_put_activity_ratio": 1.0,
        "options_call_aggressive_flow": 0.0,
        "options_put_aggressive_flow": 0.0,
        "options_atm_iv": 0.0,
        "options_put_call_iv_skew": 0.0,
        "options_expected_move_pct": 0.0,
        "options_liquidity_score": 0.0,
        "options_event_risk": 0.0,
        "options_score_adjustment": 0.0,
        "options_contracts_analyzed": 0,
        "options_snapshot_count": 0,
        "options_stale": True,
        "options_feed": None,
        "options_intelligence_age_seconds": None,
        "options_reason": reason,
    }
