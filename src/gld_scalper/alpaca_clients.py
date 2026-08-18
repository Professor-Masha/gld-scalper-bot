from __future__ import annotations

from typing import Any

from .config import Settings, load_settings


def _require_paper(settings: Settings) -> None:
    settings.validate_safety()
    if settings.alpaca_paper is not True or settings.alpaca_paper_trade is not True:
        raise RuntimeError("Refusing to initialize Alpaca client unless paper trading flags are true.")


def _alpaca_import_error(exc: Exception) -> RuntimeError:
    error = RuntimeError(
        "alpaca-py is required for broker/data operations. Install the project dependencies and verify "
        "paper Alpaca credentials are configured."
    )
    error.__cause__ = exc
    return error


def get_trading_client(settings: Settings | None = None) -> Any:
    settings = settings or load_settings()
    _require_paper(settings)
    settings.require_credentials()
    try:
        from alpaca.trading.client import TradingClient
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise _alpaca_import_error(exc)
    return TradingClient(
        settings.alpaca_api_key,
        settings.alpaca_secret_key,
        paper=True,
        url_override=settings.alpaca_trading_base_url,
    )


def get_trading_stream(settings: Settings | None = None) -> Any:
    settings = settings or load_settings()
    _require_paper(settings)
    settings.require_credentials()
    try:
        from alpaca.trading.stream import TradingStream
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise _alpaca_import_error(exc)
    return TradingStream(
        settings.alpaca_api_key,
        settings.alpaca_secret_key,
        paper=True,
    )


def get_stock_historical_client(settings: Settings | None = None) -> Any:
    settings = settings or load_settings()
    _require_paper(settings)
    settings.require_credentials()
    try:
        from alpaca.data.historical import StockHistoricalDataClient
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise _alpaca_import_error(exc)
    return StockHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_secret_key)


def get_option_historical_client(settings: Settings | None = None) -> Any:
    settings = settings or load_settings()
    _require_paper(settings)
    settings.require_credentials()
    try:
        from alpaca.data.historical import OptionHistoricalDataClient
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise _alpaca_import_error(exc)
    return OptionHistoricalDataClient(settings.alpaca_api_key, settings.alpaca_secret_key)


def get_stock_data_stream(settings: Settings | None = None) -> Any:
    settings = settings or load_settings()
    _require_paper(settings)
    settings.require_credentials()
    try:
        from alpaca.data.enums import DataFeed
        from alpaca.data.live import StockDataStream
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise _alpaca_import_error(exc)
    feed = DataFeed.IEX if settings.alpaca_data_feed == "iex" else DataFeed.SIP
    return StockDataStream(settings.alpaca_api_key, settings.alpaca_secret_key, feed=feed)


def health_check(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or load_settings()
    client = get_trading_client(settings)
    try:
        account = client.get_account()
        return {
            "ok": True,
            "account_status": getattr(account, "status", None),
            "trading_blocked": getattr(account, "trading_blocked", None),
            "paper": True,
        }
    except Exception as exc:  # pragma: no cover - network path
        message = str(exc)
        if "401" in message or "unauthorized" in message.lower():
            raise RuntimeError("Alpaca authentication failed. Check paper API key and secret.") from exc
        if "subscription" in message.lower():
            raise RuntimeError("Alpaca data subscription rejected the requested feed.") from exc
        raise RuntimeError(f"Alpaca health check failed: {message}") from exc
