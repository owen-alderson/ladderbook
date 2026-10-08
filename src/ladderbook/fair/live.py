"""`ladderbook fair`: today's ladder, Kalshi's quotes next to the options-implied fair value.

Uses public endpoints only. Spot is Deribit's BTC index, a proxy: Kalshi settles on
CF Benchmarks' BRTI, which the recorder captures but which needs an API key to stream.
The two typically differ by a few dollars, which matters for strikes right at the money.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import numpy as np

from ladderbook.deribit.surface import nearest_smile
from ladderbook.fair.coherence import Contract, payoff_matrix, project
from ladderbook.fair.density import Settlement, implied_from_ladder, prob_above
from ladderbook.kalshi.rest import KalshiREST
from ladderbook.units import parse_price

DERIBIT_REST = "https://www.deribit.com/api/v2/public"


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


async def fetch(series: str = "KXBTCD", currency: str = "BTC"):
    async with httpx.AsyncClient(timeout=10.0) as client:
        kalshi = KalshiREST(client=client)
        markets, summary, index = await asyncio.gather(
            kalshi.markets(series),
            client.get(f"{DERIBIT_REST}/get_book_summary_by_currency", params={"currency": currency, "kind": "option"}),
            client.get(f"{DERIBIT_REST}/get_index_price", params={"index_name": f"{currency.lower()}_usd"}),
        )
    return markets, summary.json()["result"], index.json()["result"]["index_price"]


def ladder_rows(markets: list[dict], marks: list[dict], spot: float, now: datetime) -> tuple[str, float, list[dict], dict]:
    """Fair value for the next event to close. Returns (event, seconds to close, rows, summary)."""
    upcoming = [m for m in markets if m.get("strike_type") == "greater" and _ts(m["close_time"]) > now]
    if not upcoming:
        raise ValueError("no open above/below markets")
    event_close = min(_ts(m["close_time"]) for m in upcoming)
    event = sorted((m for m in upcoming if _ts(m["close_time"]) == event_close), key=lambda m: m["floor_strike"])
    seconds = (event_close - now).total_seconds()
    smile = nearest_smile(marks, forward=spot, now=now)
    strikes = np.array([m["floor_strike"] for m in event])
    where = Settlement.from_seconds_to_close(seconds)
    fair = prob_above(spot, strikes, smile, where)
    rows = []
    for m, f in zip(event, fair):
        bid, ask = parse_price(m["yes_bid_dollars"]), parse_price(m["yes_ask_dollars"])
        rows.append({"strike": m["floor_strike"], "bid": bid / 100, "ask": ask / 100, "fair": f * 100})
    # coherence of Kalshi's own mids, on the strikes with a real two-sided market
    quoted = [r for r in rows if 0 < r["bid"] and r["ask"] < 100]
    if len(quoted) >= 2:
        mids = np.array([(r["bid"] + r["ask"]) / 200 for r in quoted])
        result = project(payoff_matrix([Contract.above(r["strike"]) for r in quoted]), mids)
        for r, c in zip(quoted, result.prices):
            r["coherent_mid"] = c * 100
    summary = {"options_vol": smile.atm_vol}
    try:
        mids = [r.get("coherent_mid", (r["bid"] + r["ask"]) / 2) / 100 for r in rows]
        summary["kalshi_spot"], summary["kalshi_vol"] = implied_from_ladder(strikes, mids, where)
    except ValueError:
        pass  # inside the window, or no two-sided strikes: nothing honest to report
    return event[0]["event_ticker"], seconds, rows, summary


def render(event: str, seconds: float, spot: float, rows: list[dict], summary: dict) -> str:
    lines = [
        f"{event}   closes in {seconds / 60:.1f} min",
        "",
        f"{'strike':>12} {'bid':>6} {'ask':>6} {'mid':>7} {'options fair':>13}   (cents)",
    ]
    for r in rows:
        if r["fair"] < 0.5 and r["ask"] <= 1 or r["fair"] > 99.5 and r["bid"] >= 99:
            continue  # far from the money: nothing to see
        mid = f"{r['coherent_mid']:7.2f}" if "coherent_mid" in r else " " * 7
        lines.append(f"{r['strike']:>12,.2f} {r['bid']:6.1f} {r['ask']:6.1f} {mid} {r['fair']:13.2f}")
    lines += [
        "",
        f"{'':18}{'centre':>12} {'volatility':>11}",
        f"{'options imply':18}{spot:12,.2f} {summary['options_vol']:10.1%}   Deribit index; nearest-expiry ATM vol",
    ]
    if "kalshi_spot" in summary:
        lines.append(f"{'Kalshi implies':18}{summary['kalshi_spot']:12,.2f} {summary['kalshi_vol']:10.1%}   fitted to the mids above")
    lines += [
        "",
        "A different centre is mostly the gap between Deribit's index and BRTI, which",
        "Kalshi settles on. A different volatility is the open question this project",
        "measures. Neither alone is a mispricing: no fees, no basis correction here.",
    ]
    return "\n".join(lines)


def main(series: str = "KXBTCD") -> None:
    markets, marks, spot = asyncio.run(fetch(series))
    event, seconds, rows, summary = ladder_rows(markets, marks, spot, datetime.now(timezone.utc))
    print(render(event, seconds, spot, rows, summary))
