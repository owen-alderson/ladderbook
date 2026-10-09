"""Prove the recorded tape is complete: rebuild every book from deltas and check it
against each in-band snapshot Kalshi sends, level for level.

A snapshot is only compared when the book is "clean": no sequence gap, reconnect or
impossible delta since that market's previous snapshot. Dirty books are re-seeded
from the snapshot and counted as resyncs, not as matches.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ladderbook import _core
from ladderbook.record.writer import read_raw
from ladderbook.units import PRICE_ONE, parse_price, parse_qty


@dataclass
class Report:
    messages: Counter = field(default_factory=Counter)
    markets: set = field(default_factory=set)
    connections: int = 0
    gaps: int = 0
    snapshots_compared: int = 0
    snapshots_matched: int = 0
    resyncs: int = 0
    orphan_deltas: int = 0
    negative_levels: int = 0
    crossed_after_delta: int = 0
    mismatches: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.snapshots_compared > 0 and self.snapshots_matched == self.snapshots_compared

    def summary(self) -> str:
        lines = [
            f"messages: {sum(self.messages.values()):,} ({', '.join(f'{k} {v:,}' for k, v in self.messages.most_common())})",
            f"markets: {len(self.markets):,}   connections: {self.connections}   sequence gaps: {self.gaps}",
            f"snapshots compared: {self.snapshots_compared:,}   exact matches: {self.snapshots_matched:,}   "
            f"resyncs after gaps: {self.resyncs:,}",
            f"orphan deltas: {self.orphan_deltas}   negative levels: {self.negative_levels}   "
            f"crossed books: {self.crossed_after_delta}",
            "RESULT: " + ("every clean book matched Kalshi exactly" if self.ok else "MISMATCH or nothing to compare"),
        ]
        lines += [f"  mismatch: {m}" for m in self.mismatches[:5]]
        return "\n".join(lines)


def _levels(snapshot: dict, yes_pricing: bool) -> tuple[list, list]:
    bids = sorted(((parse_price(p), parse_qty(q)) for p, q in snapshot.get("yes_dollars_fp", [])), reverse=True)
    asks = []
    for p, q in snapshot.get("no_dollars_fp", []):
        price = parse_price(p)
        asks.append((price if yes_pricing else PRICE_ONE - price, parse_qty(q)))
    return bids, sorted(asks)


def verify_rows(rows) -> Report:
    """rows: iterable of (source, raw) in receive order."""
    report = Report()
    books: dict[str, _core.BinaryBook] = {}
    dirty: set[str] = set()
    book_sids: set[int] = set()
    last_seq: dict[int, int] = {}
    yes_pricing = False

    for source, raw in rows:
        msg = json.loads(raw)
        kind = msg.get("type")
        if source == "ladderbook":
            if kind == "connected":
                report.connections += 1
                dirty.update(books)
                book_sids.clear()
                last_seq.clear()
            elif kind == "command" and msg["command"].get("cmd") == "subscribe":
                params = msg["command"].get("params", {})
                if "orderbook_delta" in params.get("channels", []):
                    yes_pricing = bool(params.get("use_yes_price", False))
            continue
        if source != "kalshi":
            continue
        report.messages[kind] += 1
        if kind == "subscribed" and msg["msg"]["channel"] == "orderbook_delta":
            book_sids.add(msg["msg"]["sid"])
        sid, seq = msg.get("sid"), msg.get("seq")
        if sid is not None and seq is not None:
            if sid in last_seq and seq != last_seq[sid] + 1:
                report.gaps += 1
                if sid in book_sids:
                    dirty.update(books)
            last_seq[sid] = seq

        if kind == "orderbook_snapshot":
            body = msg["msg"]
            ticker = body["market_ticker"]
            report.markets.add(ticker)
            bids, asks = _levels(body, yes_pricing)
            book = books.get(ticker)
            if book is not None and ticker not in dirty:
                report.snapshots_compared += 1
                if book.levels(_core.Side.BID) == bids and book.levels(_core.Side.ASK) == asks:
                    report.snapshots_matched += 1
                else:
                    report.mismatches.append(ticker)
            elif book is not None:
                report.resyncs += 1
            book = books[ticker] = _core.BinaryBook()
            for price, qty in bids:
                book.set_level(_core.Side.BID, price, qty)
            for price, qty in asks:
                book.set_level(_core.Side.ASK, price, qty)
            dirty.discard(ticker)
        elif kind == "orderbook_delta":
            body = msg["msg"]
            ticker = body["market_ticker"]
            book = books.get(ticker)
            if book is None:
                report.orphan_deltas += 1
                continue
            price = parse_price(body["price_dollars"])
            side = _core.Side.BID
            if body["side"] == "no":
                side = _core.Side.ASK
                price = price if yes_pricing else PRICE_ONE - price
            try:
                book.apply_delta(side, price, parse_qty(body["delta_fp"]))
            except RuntimeError:
                report.negative_levels += 1
                dirty.add(ticker)
                continue
            if book.crossed():
                report.crossed_after_delta += 1
    return report


def verify(root: Path) -> Report:
    table = read_raw(root)
    return verify_rows(zip(table.column("source").to_pylist(), table.column("raw").to_pylist()))
