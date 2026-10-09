"""Build a Smile from Deribit option marks (REST book summary or the markprice channel)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from ladderbook.fair.density import SECONDS_PER_YEAR, Smile

MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def parse_instrument(name: str) -> tuple[datetime, float, str]:
    """'BTC-16OCT26-83000-P' -> (2026-10-16 08:00 UTC, 83000.0, 'P'). Deribit options expire at 08:00 UTC."""
    _, expiry, strike, kind = name.split("-")
    day, month, year = int(expiry[:-5]), MONTHS[expiry[-5:-2]], 2000 + int(expiry[-2:])
    return datetime(year, month, day, 8, tzinfo=timezone.utc), float(strike.replace("d", ".")), kind


def nearest_smile(marks: list[dict], forward: float, now: datetime, min_hours: float = 2.0) -> Smile:
    """Smile of the nearest expiry at least `min_hours` away, from out-of-the-money options.

    marks: dicts with 'instrument_name' and either 'iv' (fraction, markprice channel) or
    'mark_iv' (percent, REST). Out-of-the-money options are the liquid ones: puts below
    the forward, calls above.
    """
    by_expiry: dict[datetime, dict[float, float]] = defaultdict(dict)
    for mark in marks:
        expiry, strike, kind = parse_instrument(mark["instrument_name"])
        if (expiry - now).total_seconds() < min_hours * 3600:
            continue
        if (kind == "P") != (strike < forward):
            continue
        vol = mark["iv"] if "iv" in mark else mark["mark_iv"] / 100.0
        if vol > 0:
            by_expiry[expiry][strike] = vol
    if not by_expiry:
        raise ValueError("no option expiry far enough away")
    expiry = min(by_expiry)
    points = by_expiry[expiry]
    strikes = sorted(points)
    years = (expiry - now).total_seconds() / SECONDS_PER_YEAR
    return Smile.from_points(forward, years, strikes, [points[k] for k in strikes])
