import math

import numpy as np
import pytest

from ladderbook.research import data, study_a
from ladderbook.research.study_a import MinuteSeries, bootstrap_ratio, quote_at, taker_fee

HOUR = 3600
T0 = 1_790_000_000 - 1_790_000_000 % HOUR  # an exact hour
PRICE = 50_000.0
TICK = 0.0005  # Coinbase closes alternate PRICE * exp(+-TICK): every 1-min log return is +-2*TICK


def coinbase_close(t: int) -> float:
    return PRICE * math.exp(TICK if (t // 60) % 2 else -TICK)


def test_quote_carried_forward_from_last_candle():
    candles = {120: (100, 200), 300: (150, 250)}
    assert quote_at(candles, 60) is None
    assert quote_at(candles, 120) == (100, 200)
    assert quote_at(candles, 299) == (100, 200)
    assert quote_at(candles, 10_000) == (150, 250)


def test_minute_series_price_and_realized_vol():
    start, end = T0, T0 + 3 * HOUR
    minutes = {t: (0, 0, 0, coinbase_close(t)) for t in range(start, end, 60)}
    series = MinuteSeries(minutes, start, end)
    t = T0 + 2 * HOUR
    assert series.price_at(t) == coinbase_close(t - 60)  # the minute that ends at t
    assert series.realized_vol(t, 60) == pytest.approx(2 * TICK * math.sqrt(study_a.MINUTES_PER_YEAR))
    del minutes[t - 600]
    gappy = MinuteSeries(minutes, start, end)
    assert math.isnan(gappy.price_at(t - 540))
    assert gappy.realized_vol(t, 60) == pytest.approx(series.realized_vol(t, 60))  # gaps are skipped, not zeroed


def test_bootstrap_ratio_resamples_whole_hours():
    num = np.array([1.0, 1.0, 0.0, 0.0])
    est, lo, hi = bootstrap_ratio(num, np.ones(4), event=np.array([0, 0, 1, 1]))
    assert est == 0.5 and lo == 0.0 and hi == 1.0  # two hours: a resample is 0, 1/2 or 1
    same_hour = bootstrap_ratio(num, np.ones(4), event=np.zeros(4))
    assert same_hour == (0.5, 0.5, 0.5)  # one hour has no sampling spread at all


def test_taker_fee_is_kalshi_quadratic():
    price = np.array([0.01, 0.5, 0.9])
    expected = np.ceil(0.07 * price * (1 - price) * 1e6) / 1e6
    assert taker_fee(price, 1.0) == pytest.approx(expected, abs=1e-6)
    assert taker_fee(price, 0.5) == pytest.approx(np.ceil(0.035 * price * (1 - price) * 1e6) / 1e6, abs=1e-6)


class FakeCache:
    """Serves synthetic Kalshi / Coinbase / Deribit responses in the shapes the real APIs use.

    40 hourly settlements. Every one settles $10 above Coinbase except the last, which
    settles $1000 above: if the last hour's own settlement leaked into its spot, it would show.
    """

    def __init__(self, hours: int = 40):
        self.fetched = 0
        self.closes = [T0 + (i + 1) * HOUR for i in range(hours)]
        self.markets = []
        for close in self.closes:
            minute = [coinbase_close(close - 60)] * 4
            settle = sum(minute) / 4 + (1000.0 if close == self.closes[-1] else 10.0)
            for strike in np.arange(49_000, 51_001, 100) - 0.01:
                self.markets.append({
                    "ticker": f"EV{close}-T{strike:.2f}", "close_time": _iso(close), "strike_type": "greater",
                    "floor_strike": float(strike), "result": "yes" if settle > strike else "no", "expiration_value": f"{settle:.2f}",
                })

    def get(self, url: str, params: dict):
        if url.endswith("/markets"):
            lo, hi = params["min_close_ts"], params["max_close_ts"]
            return {"markets": [m for m in self.markets if lo <= study_a._close_ts(m) <= hi], "cursor": ""}
        if url.endswith("/markets/candlesticks"):
            out = []
            for ticker in params["market_tickers"].split(","):
                strike = float(ticker.split("-T")[1])
                bid = round(0.48 - (strike - PRICE) / 1000, 2) if abs(strike - PRICE) < 350 else 0.0
                side = lambda p: {"close_dollars": f"{p:.4f}"}  # noqa: E731
                out.append({"market_ticker": ticker, "candlesticks": [{"end_period_ts": params["start_ts"] + 60, "yes_bid": side(bid), "yes_ask": side(bid + 0.02)}]})
            return {"markets": out}
        if "coinbase" in url:
            return [[t, *[coinbase_close(t)] * 4, 1] for t in range(params["start"], params["end"] + 1, 60)]
        if "volatility_index" in url:
            a, b = params["start_timestamp"], params["end_timestamp"]
            return {"result": {"data": [[ms, 40, 40, 40, 40.0] for ms in range(a, b + 1, 60_000)]}}
        raise AssertionError(url)


def _iso(t: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def test_build_uses_only_settlements_known_at_the_look():
    cache = FakeCache()
    start = cache.closes[0] + 24 * HOUR  # 24h of settlements before it for the basis
    sample = study_a.build(cache, "KX", start, cache.closes[-1] + 1, log=lambda *_: None)
    assert set(sample.look) == set(study_a.LOOKS)
    last = sample.close == cache.closes[-1]
    assert last.any()
    # Spot = Coinbase + the $10 gap of earlier hours; the last hour's own $1000 gap is invisible to it.
    for look in study_a.LOOKS:
        rows = last & (sample.look == look)
        expected = coinbase_close(cache.closes[-1] - look * 60 - 60) + 10.0
        assert sample.spot[rows] == pytest.approx(expected)
    assert sample.basis.max() == pytest.approx(1000.0, abs=0.01)  # it is reported afterwards, as a settlement gap
    # Strikes come from the band around spot, never from where BTC settled.
    assert np.all(np.abs(sample.z) <= study_a.BAND)
    assert np.all(sample.dvol == 0.40)


def test_analyse_runs_on_the_synthetic_sample():
    cache = FakeCache()
    sample = study_a.build(cache, "KX", cache.closes[0] + 24 * HOUR, cache.closes[-1] + 1, log=lambda *_: None)
    results = study_a.analyse(sample, fee_multiplier=1.0)
    assert results["counts"]["two_sided"] == int((~np.isnan(sample.mid)).sum()) > 0
    assert results["longshot"] and results["calibration"]["kalshi"]
    text = study_a.render(results, {"series": "KX", "start": "a", "end": "b", "command": "c", "fee_multiplier": 1.0})
    assert "## Longshot bias" in text


def test_coinbase_windows_cover_range_without_overlap():
    seen = []

    class Recorder:
        def get(self, url, params):
            seen.append((params["start"], params["end"]))
            return []

    data.coinbase_minutes(Recorder(), T0, T0 + 12 * HOUR)
    covered = [t for a, b in seen for t in range(a, b + 1, 60)]
    assert covered == list(range(T0, T0 + 12 * HOUR, 60))
