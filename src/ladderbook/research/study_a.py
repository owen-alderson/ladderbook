"""Study A: were Kalshi's hourly BTC ladders well calibrated, and did they agree with options?

For each settled hour and each look time (45, 30, 15 and 5 minutes before the close)
three forecasts of "settles above K" are scored against what happened:

  kalshi    the YES mid, where both sides were quoted
  options   the 60s-average settlement model with Deribit's DVOL as the volatility
  realized  the same model with the trailing 24h realized volatility of Coinbase BTC-USD

Spot is Coinbase BTC-USD at the look time plus the median Coinbase-to-BRTI gap of the
settlements in the previous 24 hours (all known by then: no look-ahead). Strikes are
chosen with look-time information only, within 4 standard deviations of spot; choosing
them by where BTC settled would leak the outcome into the sample.

Every interval is a bootstrap over settlement hours, not over rows: the ~30 strikes
and 4 looks of one hour share one outcome path and are not independent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ladderbook import _core
from ladderbook.fair.density import SECONDS_PER_YEAR, WINDOW_TICKS, Settlement, averaged_variance_seconds, implied_from_ladder, prob_above_flat
from ladderbook.research import data
from ladderbook.units import PRICE_ONE

LOOKS = (45, 30, 15, 5)  # minutes before the close; hourly events open 60 minutes before
BAND = 4.0  # strikes studied: |ln(K/S)| within this many standard deviations
FETCH_BAND = 6.0  # strikes downloaded, judged at the first look; wide enough to cover BAND later on
BASIS_HOURS = 24
MIN_BASIS_EVENTS = 6
RV_MINUTES = 24 * 60
MINUTES_PER_YEAR = SECONDS_PER_YEAR / 60
EPS = 0.005  # log-loss clip: half a cent, the finest a Kalshi mid can be
BOOTSTRAP = 2000

COLUMNS = ("event", "close", "look", "strike", "z", "y", "bid", "ask", "spot", "dvol", "rv", "p_options", "p_realized")


@dataclass
class Sample:
    """One row per (settlement hour, look time, strike). bid/ask are dollars; mid is NaN when one-sided."""

    cols: dict[str, np.ndarray]
    counts: dict[str, int] = field(default_factory=dict)
    basis: np.ndarray = field(default_factory=lambda: np.empty(0))
    vols: list[dict] = field(default_factory=list)

    def __getattr__(self, name):
        if name == "cols":
            raise AttributeError(name)
        try:
            return self.cols[name]
        except KeyError:
            raise AttributeError(name) from None

    @property
    def mid(self) -> np.ndarray:
        two_sided = (self.bid > 0) & (self.ask < 1)
        return np.where(two_sided, (self.bid + self.ask) / 2, np.nan)


def horizon_years(look_minutes: float) -> float:
    """Brownian time the settlement average still carries, in years."""
    where = Settlement.from_seconds_to_close(look_minutes * 60)
    return (where.seconds_to_window + averaged_variance_seconds(WINDOW_TICKS)) / SECONDS_PER_YEAR


class MinuteSeries:
    """Coinbase closes on a regular minute grid (NaN where a minute had no trades)."""

    def __init__(self, minutes: dict[int, tuple], start: int, end: int):
        self.start = start - start % 60
        n = (end - self.start) // 60
        self.close = np.full(n, np.nan)
        for t, (_, _, _, c) in minutes.items():
            i = (t - self.start) // 60
            if 0 <= i < n:
                self.close[i] = c
        r = np.diff(np.log(self.close), prepend=np.nan)
        ok = ~np.isnan(r)
        self._sq = np.concatenate([[0.0], np.cumsum(np.where(ok, r * r, 0.0))])
        self._n = np.concatenate([[0], np.cumsum(ok)])

    def _i(self, t: int) -> int:
        return (t - self.start) // 60

    def price_at(self, t: int) -> float:
        """Close of the minute that ends at t (NaN if that minute is missing)."""
        i = self._i(t) - 1
        return self.close[i] if 0 <= i < len(self.close) else math.nan

    def realized_vol(self, t: int, minutes: int = RV_MINUTES) -> float:
        """Annualized volatility of the 1-minute returns in the `minutes` before t."""
        b = self._i(t)
        a = b - minutes
        if a < 1 or b > len(self.close):
            return math.nan
        n = self._n[b] - self._n[a]
        return math.sqrt((self._sq[b] - self._sq[a]) / n * MINUTES_PER_YEAR) if n >= minutes * 0.7 else math.nan


def quote_at(candles: dict[int, tuple[int, int]], t: int) -> tuple[int, int] | None:
    """The (bid, ask) in force at t: the last candle closing at or before t.

    Kalshi only emits a candle for a minute in which something changed, so a quiet
    strike's quote is carried forward (checked: after every gap in the data, the next
    candle opens exactly where the previous one closed). None before the first candle.
    """
    ends = [e for e in candles if e <= t]
    return candles[max(ends)] if ends else None


def _close_ts(market: dict) -> int:
    return int(datetime.fromisoformat(market["close_time"].replace("Z", "+00:00")).timestamp())


def build(cache: data.Cache, series: str, start: int, end: int, log=print) -> Sample:
    """Download (or read from cache) everything for hours closing in [start, end) and build the rows."""
    warm = start - BASIS_HOURS * 3600
    markets = data.settled_markets(cache, series, warm, end)
    events: dict[int, list[dict]] = {}
    for m in markets:
        events.setdefault(_close_ts(m), []).append(m)
    log(f"{len(markets)} settled markets in {len(events)} hours")
    coinbase = data.coinbase_minutes(cache, warm - RV_MINUTES * 60, end)
    prices = MinuteSeries(coinbase, warm - RV_MINUTES * 60, end)
    dvol = data.dvol_minutes(cache, start - 3600, end)

    # Coinbase -> BRTI gap at each settlement: the official 60s average minus the mean of
    # Coinbase's OHLC over the same minute (a 4-point proxy for its 60 one-second prints).
    basis = {}
    for close, ms in events.items():
        minute = coinbase.get(close - 60)
        if minute is not None and ms[0].get("expiration_value"):
            basis[close] = float(ms[0]["expiration_value"]) - sum(minute) / 4
    basis_times = np.array(sorted(basis))
    basis_values = np.array([basis[t] for t in basis_times])

    rows: dict[str, list] = {c: [] for c in COLUMNS}
    counts = dict(hours=0, no_spot=0, no_vol=0, no_basis=0, no_quote=0, outside_fetch=0)
    vols = []
    closes = sorted(t for t in events if start <= t < end)
    for k, close in enumerate(closes):
        if k % 100 == 0:
            log(f"hour {k + 1}/{len(closes)}")
        ladder = sorted(events[close], key=lambda m: m["floor_strike"])
        strikes = np.array([m["floor_strike"] for m in ladder])
        first = close - LOOKS[0] * 60
        s0, v0 = prices.price_at(first), dvol.get(first - 60)
        if math.isnan(s0) or v0 is None:
            counts["no_spot"] += 1
            continue
        fetch = np.abs(np.log(strikes / s0)) <= FETCH_BAND * v0 * math.sqrt(horizon_years(LOOKS[0]))
        tickers = [m["ticker"] for m, f in zip(ladder, fetch) if f]
        book = data.quotes(cache, tickers, close - 3600, close)
        counts["hours"] += 1
        for look in LOOKS:
            t = close - look * 60
            recent = (basis_times >= t - BASIS_HOURS * 3600) & (basis_times <= t - 300)
            if recent.sum() < MIN_BASIS_EVENTS:
                counts["no_basis"] += 1
                continue
            spot = prices.price_at(t) + float(np.median(basis_values[recent]))
            sigma_d, sigma_r = dvol.get(t - 60), prices.realized_vol(t)
            if math.isnan(spot):
                counts["no_spot"] += 1
                continue
            if sigma_d is None or math.isnan(sigma_r):
                counts["no_vol"] += 1
                continue
            where = Settlement.from_seconds_to_close(look * 60)
            z = np.log(strikes / spot) / (sigma_d * math.sqrt(horizon_years(look)))
            p_opt = prob_above_flat(spot, strikes, sigma_d, where)
            p_rv = prob_above_flat(spot, strikes, sigma_r, where)
            ladder_mids = []
            for i in np.flatnonzero(np.abs(z) <= BAND):
                market = ladder[i]
                if not fetch[i]:
                    counts["outside_fetch"] += 1
                    continue
                quote = quote_at(book.get(market["ticker"], {}), t)
                if quote is None:
                    counts["no_quote"] += 1
                    continue
                bid, ask = quote[0] / PRICE_ONE, quote[1] / PRICE_ONE
                for c, v in zip(COLUMNS, (k, close, look, strikes[i], z[i], market["result"] == "yes", bid, ask, spot, sigma_d, sigma_r, p_opt[i], p_rv[i])):
                    rows[c].append(v)
                if bid > 0 and ask < 1:
                    ladder_mids.append((strikes[i], (bid + ask) / 2))
            if len(ladder_mids) >= 3:
                try:
                    _, kalshi_vol = implied_from_ladder(*zip(*ladder_mids), where)
                    vols.append(dict(look=look, kalshi=kalshi_vol, dvol=sigma_d, rv=sigma_r, after=prices.realized_vol(close, look)))
                except (ValueError, OverflowError):
                    pass  # a ladder with no slope implies no volatility
    cols = {c: np.array(v) for c, v in rows.items()}
    cols["y"] = cols["y"].astype(float)
    window = (basis_times >= start) & (basis_times < end)
    return Sample(cols, counts, basis_values[window], vols)


# ---------------------------------------------------------------- statistics


def bootstrap_ratio(num, den, event, n: int = BOOTSTRAP, seed: int = 0) -> tuple[float, float, float]:
    """sum(num)/sum(den) with a 95% interval from resampling whole settlement hours."""
    num, den, event = np.asarray(num, float), np.asarray(den, float), np.asarray(event)
    ids, e = np.unique(event, return_inverse=True)
    num_e, den_e = np.bincount(e, num, len(ids)), np.bincount(e, den, len(ids))
    estimate = num_e.sum() / den_e.sum()
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(ids), size=(n, len(ids)))
    stats = num_e[picks].sum(1) / den_e[picks].sum(1)
    lo, hi = np.nanpercentile(stats, [2.5, 97.5])
    return float(estimate), float(lo), float(hi)


def brier(p, y) -> np.ndarray:
    return (np.asarray(p) - y) ** 2


def log_loss(p, y) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def taker_fee(price: np.ndarray, multiplier: float) -> np.ndarray:
    """Per-contract taker fee in dollars, from the exact exchange arithmetic in the C++ core."""
    mult = round(multiplier * 10_000)
    return np.array([_core.trade_fee_micros(_core.TAKER_RATE, mult, round(p * PRICE_ONE), 100) / 1e6 for p in price])


CAL_EDGES = (0, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9, 0.95, 0.98, 1.0001)
PRICE_EDGES = (0.01, 0.05, 0.1, 0.2, 0.35, 0.5, 0.65, 0.8, 0.9, 0.95, 0.9901)
Z_EDGES = (0, 0.5, 1, 2, BAND + 1e-9)


def _label(lo, hi, cents=True):
    return f"{lo * 100:.0f}–{min(hi, 1) * 100:.0f}¢" if cents else f"{lo:g}–{min(hi, BAND):g}"


def analyse(s: Sample, fee_multiplier: float) -> dict:
    mid = s.mid
    quoted = ~np.isnan(mid)
    forecasts = {"kalshi": mid, "options": s.p_options, "realized": s.p_realized}
    out: dict = {"counts": {**s.counts, "rows": int(len(s.y)), "two_sided": int(quoted.sum()), "hours_used": int(len(np.unique(s.event)))}}

    def scores(mask):
        row = {"n": int(mask.sum()), "hours": int(len(np.unique(s.event[mask])))}
        for name, p in forecasts.items():
            row[f"brier_{name}"] = bootstrap_ratio(brier(p[mask], s.y[mask]), np.ones(mask.sum()), s.event[mask])
            row[f"logloss_{name}"] = bootstrap_ratio(log_loss(p[mask], s.y[mask]), np.ones(mask.sum()), s.event[mask])
        for name in ("options", "realized"):  # > 0: Kalshi's mid was the better forecast
            diff = brier(forecasts[name][mask], s.y[mask]) - brier(mid[mask], s.y[mask])
            row[f"edge_vs_{name}"] = bootstrap_ratio(diff, np.ones(mask.sum()), s.event[mask])
        return row

    out["by_look"] = {str(look): scores(quoted & (s.look == look)) for look in LOOKS}
    out["by_look"]["all"] = scores(quoted)
    az = np.abs(s.z)
    out["by_z"] = {_label(a, b, cents=False): scores(quoted & (az >= a) & (az < b)) for a, b in zip(Z_EDGES, Z_EDGES[1:])}

    out["calibration"] = {}
    for name, p in forecasts.items():
        table = []
        for a, b in zip(CAL_EDGES, CAL_EDGES[1:]):
            m = quoted & (p >= a) & (p < b)
            if m.sum() == 0:
                continue
            table.append({"bucket": _label(a, b), "n": int(m.sum()), "forecast": float(p[m].mean()), "won": bootstrap_ratio(s.y[m], np.ones(m.sum()), s.event[m])})
        out["calibration"][name] = table

    # Longshot bias: buy YES at the ask, or NO at 1 - bid, and hold to settlement.
    yes = s.ask < 1
    no = s.bid > 0
    price = np.concatenate([s.ask[yes], 1 - s.bid[no]])
    won = np.concatenate([s.y[yes], 1 - s.y[no]])
    event = np.concatenate([s.event[yes], s.event[no]])
    fee = taker_fee(price, fee_multiplier)
    out["longshot"] = []
    for a, b in zip(PRICE_EDGES, PRICE_EDGES[1:]):
        m = (price >= a) & (price < b)
        if m.sum() == 0:
            continue
        out["longshot"].append({
            "bucket": _label(a, b),
            "n": int(m.sum()),
            "price": float(price[m].mean()),
            "won": float(won[m].mean()),
            "gross": bootstrap_ratio(won[m] - price[m], price[m], event[m]),
            "net": bootstrap_ratio(won[m] - price[m] - fee[m], price[m] + fee[m], event[m]),
        })

    if len(s.basis):
        out["basis"] = {"hours": int(len(s.basis)), "median": float(np.median(s.basis)), "p25": float(np.percentile(s.basis, 25)), "p75": float(np.percentile(s.basis, 75))}
    out["vol"] = {}
    for look in LOOKS:
        v = [r for r in s.vols if r["look"] == look and not math.isnan(r["after"])]
        if v:
            med = lambda key: float(np.median([r[key] for r in v]))  # noqa: E731
            out["vol"][str(look)] = {"n": len(v), "kalshi": med("kalshi"), "dvol": med("dvol"), "rv": med("rv"), "after": med("after")}
    return out


# ---------------------------------------------------------------- report


def _ci(t, fmt="{:.4f}", scale=1.0):
    est, lo, hi = (x * scale for x in t)
    return f"{fmt.format(est)} [{fmt.format(lo)}, {fmt.format(hi)}]"


def render(results: dict, meta: dict) -> str:
    c = results["counts"]
    lines = [
        f"# Study A tables: {meta['series']}, hours closing {meta['start']} to {meta['end']} (UTC)",
        "",
        f"Generated by `{meta['command']}`. Intervals are 95% bootstraps over settlement hours.",
        "",
        f"{c['hours_used']} settlement hours, {c['rows']} (hour, look, strike) rows, {c['two_sided']} with a two-sided quote.",
        f"Dropped: {c['no_basis']} looks without 24h of basis history, {c['no_spot']} without a Coinbase minute, "
        f"{c['no_vol']} without a volatility, {c['no_quote']} strikes without a candle, {c['outside_fetch']} outside the download band.",
        "",
        "## Scores (lower is better; edge > 0 means Kalshi's mid beat the model)",
        "",
        "| look | rows | hours | Brier Kalshi | Brier options | Brier realized | edge vs options | edge vs realized |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for look, r in results["by_look"].items():
        name = f"{look} min" if look != "all" else "all"
        lines.append(f"| {name} | {r['n']} | {r['hours']} | {_ci(r['brier_kalshi'])} | {_ci(r['brier_options'])} | {_ci(r['brier_realized'])} | {_ci(r['edge_vs_options'])} | {_ci(r['edge_vs_realized'])} |")
    lines += ["", "| look | log-loss Kalshi | log-loss options | log-loss realized |", "|---|---|---|---|"]
    for look, r in results["by_look"].items():
        lines.append(f"| {look} | {_ci(r['logloss_kalshi'])} | {_ci(r['logloss_options'])} | {_ci(r['logloss_realized'])} |")
    lines += ["", "## By distance from the money (|z|, in standard deviations; all looks)", "",
              "| abs(z) | rows | Brier Kalshi | Brier options | edge vs options | edge vs realized |", "|---|---|---|---|---|---|"]
    for z, r in results["by_z"].items():
        lines.append(f"| {z} | {r['n']} | {_ci(r['brier_kalshi'])} | {_ci(r['brier_options'])} | {_ci(r['edge_vs_options'])} | {_ci(r['edge_vs_realized'])} |")
    lines += ["", "## Calibration (all looks, two-sided rows)", ""]
    for name, table in results["calibration"].items():
        lines += [f"**{name}**", "", "| forecast bucket | rows | mean forecast | won |", "|---|---|---|---|"]
        for r in table:
            lines.append(f"| {r['bucket']} | {r['n']} | {r['forecast']:.3f} | {_ci(r['won'], '{:.3f}')} |")
        lines.append("")
    lines += ["## Longshot bias: buy at the touch and hold to settlement", "",
              f"YES bought at the ask and NO at 1 − bid, pooled by the price paid. Return per dollar staked. Net = after Kalshi's taker fee (fee multiplier {meta['fee_multiplier']}).", "",
              "| price paid | contracts | mean price | won | return gross | return net of fee |", "|---|---|---|---|---|---|"]
    for r in results["longshot"]:
        lines.append(f"| {r['bucket']} | {r['n']} | {r['price']:.3f} | {r['won']:.3f} | {_ci(r['gross'], '{:+.1%}')} | {_ci(r['net'], '{:+.1%}')} |")
    if "basis" in results:
        b = results["basis"]
        lines += ["", "## Coinbase vs BRTI at settlement", "",
                  f"BRTI 60s average minus Coinbase's mean OHLC over the same minute, {b['hours']} hours: median ${b['median']:+.2f}, interquartile ${b['p25']:+.2f} to ${b['p75']:+.2f}."]
    lines += ["", "## Volatility (medians, annualized)", "",
              "| look | ladders | Kalshi-implied | DVOL (30-day options) | trailing 24h realized | realized over the rest of the hour |", "|---|---|---|---|---|---|"]
    for look, v in results["vol"].items():
        lines.append(f"| {look} min | {v['n']} | {v['kalshi']:.1%} | {v['dvol']:.1%} | {v['rv']:.1%} | {v['after']:.1%} |")
    return "\n".join(lines) + "\n"


def day_start(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def main(series: str, start: str, end: str, cache_dir: Path, out: Path, command: str) -> None:
    import json

    cache = data.Cache(cache_dir)
    cutoff = data.historical_cutoff(cache)["market_settled_ts"]
    t0, t1 = day_start(start), day_start(end)
    warm = datetime.fromtimestamp(t0 - BASIS_HOURS * 3600, timezone.utc)
    if warm < datetime.fromisoformat(cutoff.replace("Z", "+00:00")):
        raise SystemExit(f"--start must be at least a day after Kalshi's historical cutoff ({cutoff}); older markets live under /historical, which this study doesn't read yet")
    if t1 > datetime.now(timezone.utc).timestamp() - 3600:
        raise SystemExit("--end must be at least an hour in the past, so every downloaded window is final")
    sample = build(cache, series, t0, t1, log=lambda msg: print(msg, flush=True))
    fee_multiplier = float(data.series_info(cache, series)["fee_multiplier"])
    results = analyse(sample, fee_multiplier)
    meta = {"series": series, "start": start, "end": end, "command": command, "fee_multiplier": fee_multiplier}
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"meta": meta, **results}, indent=1))
    (out / "tables.md").write_text(render(results, meta))
    from ladderbook.research import figures

    figures.calibration(results, out / "calibration.png")
    figures.longshot(results, out / "longshot.png")
    print(f"{cache.fetched} downloads; wrote {out}/results.json, tables.md, calibration.png, longshot.png")
