from gld_scalper.indicator_engine import atr, ema, rsi


def test_ema_calculation():
    values = [10.0, 11.0, 12.0]
    result = ema(values, 3)
    assert abs(result[-1] - 11.25) < 1e-9


def test_rsi_calculation_on_uptrend():
    values = [float(value) for value in range(1, 31)]
    result = rsi(values, 14)
    assert result[-1] > 90


def test_atr_calculation():
    highs = [11.0, 12.0, 13.0, 14.0, 15.0]
    lows = [9.0, 10.0, 11.0, 12.0, 13.0]
    closes = [10.0, 11.0, 12.0, 13.0, 14.0]
    result = atr(highs, lows, closes, 3)
    assert abs(result[-1] - 2.0) < 1e-9
