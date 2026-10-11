"""Cached downloads for the historical studies. All public; no API key.

  * Kalshi: settled markets and 1-minute YES bid/ask candles.
  * Coinbase: 1-minute BTC-USD candles (the spot proxy; Kalshi settles on CF
    Benchmarks' BRTI, of which Coinbase is the largest constituent).
  * Deribit: DVOL, its 30-day implied-volatility index, per minute.

Every response is cached on disk under a key made from the URL and parameters, so a
rerun is offline and gives identical numbers. Only windows that are entirely in the
past are requested; anything still changing would poison the cache.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

from ladderbook.kalshi import PROD_REST
from ladderbook.net import SSL_CONTEXT
from ladderbook.units import parse_price

COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/{product}/candles"
DERIBIT_DVOL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
CANDLE_BATCH = 25  # Kalshi bills a batch per market; 25 fits a basic read bucket comfortably
COINBASE_MAX = 300  # candles per Coinbase request
DVOL_MAX = 1000  # points per Deribit request


class Cache:
    """GET with an on-disk JSON cache and backoff on 429 / 5xx."""

    def __init__(self, root: Path, client: httpx.Client | None = None, tries: int = 7):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.client = client or httpx.Client(timeout=30.0, verify=SSL_CONTEXT)
        self.tries = tries
        self.fetched = 0

    def get(self, url: str, params: dict) -> dict | list:
        key = hashlib.sha1(json.dumps([url, sorted(params.items())]).encode()).hexdigest()
        path = self.root / key[:2] / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text())
        for attempt in range(self.tries):
            response = self.client.get(url, params=params)
            if response.status_code != 429 and response.status_code < 500:
                break
            time.sleep(0.5 * 2**attempt)
        response.raise_for_status()
        body = response.json()
        path.parent.mkdir(exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(body))
        tmp.replace(path)
        self.fetched += 1
        return body


def settled_markets(cache: Cache, series: str, start: int, end: int) -> list[dict]:
    """Every settled "above K" market in the series that closed in [start, end) (Unix seconds).

    Uses the live tier only: markets that settled before Kalshi's historical cutoff
    have moved to /historical/markets, and the caller is expected to stay after it.
    """
    out, cursor = [], None
    while True:
        params = {"series_ticker": series, "min_close_ts": start, "max_close_ts": end - 1, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        page = cache.get(PROD_REST + "/markets", params)
        out.extend(page["markets"])
        cursor = page.get("cursor")
        if not cursor or not page["markets"]:
            break
    return [m for m in out if m.get("strike_type") == "greater" and m.get("result") in ("yes", "no")]


def historical_cutoff(cache: Cache) -> dict:
    return cache.client.get(PROD_REST + "/historical/cutoff").raise_for_status().json()


def series_info(cache: Cache, series: str) -> dict:
    return cache.client.get(f"{PROD_REST}/series/{series}").raise_for_status().json()["series"]


def quotes(cache: Cache, tickers: list[str], start: int, end: int) -> dict[str, dict[int, tuple[int, int]]]:
    """1-minute YES (bid, ask) at each candle close in (start, end], in 1e-4 dollar units.

    Keyed by ticker, then by the candle's end timestamp (the moment the quote held).
    Minutes where either side is missing are left out rather than filled in.
    """
    out: dict[str, dict[int, tuple[int, int]]] = {}
    for i in range(0, len(tickers), CANDLE_BATCH):
        chunk = tickers[i : i + CANDLE_BATCH]
        params = {"market_tickers": ",".join(chunk), "start_ts": start, "end_ts": end, "period_interval": 1}
        body = cache.get(PROD_REST + "/markets/candlesticks", params)
        for market in body["markets"]:
            book = out.setdefault(market["market_ticker"], {})
            for c in market["candlesticks"]:
                bid, ask = c["yes_bid"].get("close_dollars"), c["yes_ask"].get("close_dollars")
                if bid is not None and ask is not None:
                    book[c["end_period_ts"]] = (parse_price(bid), parse_price(ask))
    return out


def coinbase_minutes(cache: Cache, start: int, end: int, product: str = "BTC-USD") -> dict[int, tuple[float, float, float, float]]:
    """Minute start (Unix seconds) -> (open, high, low, close) for minutes starting in [start, end)."""
    out = {}
    for a in range(start - start % 60, end, COINBASE_MAX * 60):
        b = min(a + COINBASE_MAX * 60, end) - 60  # Coinbase's end is inclusive
        params = {"granularity": 60, "start": a, "end": b}
        for t, low, high, open_, close, _ in cache.get(COINBASE_CANDLES.format(product=product), params):
            if start <= t < end:
                out[t] = (open_, high, low, close)
    return out


def dvol_minutes(cache: Cache, start: int, end: int, currency: str = "BTC") -> dict[int, float]:
    """Unix seconds -> DVOL as a fraction (0.37 = 37%), one point per minute in [start, end).

    Requested in fixed 1000-minute windows so the cache keys don't depend on the range asked for.
    """
    out = {}
    span = DVOL_MAX * 60
    for a in range(start - start % span, end, span):
        params = {"currency": currency, "start_timestamp": a * 1000, "end_timestamp": (a + span - 60) * 1000, "resolution": 60}
        for ts, *_, close in cache.get(DERIBIT_DVOL, params)["result"]["data"]:
            t = ts // 1000
            if start <= t < end:
                out[t] = close / 100.0
    return out
