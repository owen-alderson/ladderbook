"""Kalshi websocket recorder for whole series (e.g. every open KXBTCD market).

Every message is written verbatim. The recorder itself only reads enough of each
message to keep the subscription healthy:

  * sequence gaps: each subscription (sid) numbers its messages 1, 2, 3, ... If one is
    missed, a "gap" marker is written and fresh book snapshots are requested. Nothing is
    ever filled in by guesswork.
  * market discovery: new hourly markets are added and settled ones dropped.
  * periodic snapshots: every few minutes the book is re-requested in-band, so the
    normaliser can prove the delta-rebuilt book matched Kalshi's exactly.

Commands the recorder sends are written to the tape too (source "ladderbook"), so the
data always records which conventions (e.g. use_yes_price) it was captured under.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import websockets

from ladderbook.kalshi import PROD_WS, WS_PATH
from ladderbook.kalshi.auth import KalshiSigner
from ladderbook.kalshi.rest import KalshiREST
from ladderbook.net import SSL_CONTEXT
from ladderbook.record.writer import RawWriter

log = logging.getLogger(__name__)

MARKET_CHANNELS = ["orderbook_delta", "trade", "ticker"]
INDEX_IDS = ["BRTI", "ETHUSD_RTI"]


class KalshiFeed:
    def __init__(
        self,
        signer: KalshiSigner,
        writer: RawWriter,
        rest: KalshiREST,
        series: list[str],
        url: str = PROD_WS,
        discover_every: float = 60.0,
        snapshot_every: float = 300.0,
        connect=websockets.connect,
    ):
        self.signer = signer
        self.writer = writer
        self.rest = rest
        self.series = series
        self.url = url
        self.discover_every = discover_every
        self.snapshot_every = snapshot_every
        self._connect = connect
        self._ws = None
        self._next_id = 0
        self.markets: set[str] = set()
        self.sids: dict[str, int] = {}  # channel -> sid
        self.last_seq: dict[int, int] = {}
        self.gaps = 0

    # -- plumbing ---------------------------------------------------------------------

    def _note(self, kind: str, **fields) -> None:
        self.writer.write("ladderbook", json.dumps({"type": kind, **fields}))

    async def _send(self, cmd: str, params: dict | None = None) -> None:
        self._next_id += 1
        payload = {"id": self._next_id, "cmd": cmd}
        if params is not None:
            payload["params"] = params
        text = json.dumps(payload)
        self.writer.write("ladderbook", json.dumps({"type": "command", "command": payload}))
        await self._ws.send(text)

    async def _open_markets(self) -> set[str]:
        found: set[str] = set()
        for series in self.series:
            found.update(m["ticker"] for m in await self.rest.markets(series, status="open"))
        return found

    # -- message handling -------------------------------------------------------------

    async def handle(self, raw: str) -> None:
        self.writer.write("kalshi", raw)
        msg = json.loads(raw)
        kind = msg.get("type")
        if kind == "subscribed":
            self.sids[msg["msg"]["channel"]] = msg["msg"]["sid"]
            return
        if kind == "error":
            log.warning("kalshi error: %s", msg.get("msg"))
            return
        sid, seq = msg.get("sid"), msg.get("seq")
        if sid is None or seq is None:
            return
        last = self.last_seq.get(sid)
        self.last_seq[sid] = seq
        if last is not None and seq != last + 1:
            self.gaps += 1
            self._note("gap", sid=sid, expected=last + 1, got=seq)
            log.warning("sequence gap on sid %s: expected %s, got %s", sid, last + 1, seq)
            if sid == self.sids.get("orderbook_delta"):
                await self.request_snapshots()

    async def request_snapshots(self) -> None:
        sid = self.sids.get("orderbook_delta")
        if sid is not None and self.markets:
            await self._send(
                "update_subscription",
                {"sid": sid, "market_tickers": sorted(self.markets), "action": "get_snapshot"},
            )

    async def refresh_markets(self) -> None:
        current = await self._open_markets()
        added, removed = sorted(current - self.markets), sorted(self.markets - current)
        for channel in MARKET_CHANNELS:
            sid = self.sids.get(channel)
            if sid is None:
                continue
            if added:
                await self._send("update_subscription", {"sid": sid, "market_tickers": added, "action": "add_markets"})
            if removed:
                await self._send(
                    "update_subscription", {"sid": sid, "market_tickers": removed, "action": "delete_markets"}
                )
        self.markets = current
        if added or removed:
            log.info("markets: +%d -%d (now %d)", len(added), len(removed), len(current))

    # -- session ----------------------------------------------------------------------

    async def _periodic(self, every: float, action) -> None:
        while True:
            await asyncio.sleep(every)
            try:
                await action()
            except (httpx.HTTPError, OSError) as exc:  # a failed refresh must not stop recording
                log.warning("%s failed: %s", action.__name__, exc)

    async def session(self) -> None:
        """One connection: subscribe, then record until the socket closes."""
        self.markets = await self._open_markets()
        headers = self.signer.headers("GET", WS_PATH)
        ssl = SSL_CONTEXT if self.url.startswith("wss://") else None
        async with self._connect(self.url, additional_headers=headers, ssl=ssl, max_size=2**24) as ws:
            self._ws = ws
            self.sids.clear()
            self.last_seq.clear()  # sids and sequences restart on every connection
            self._note("connected", url=self.url, markets=len(self.markets))
            await self._send(
                "subscribe",
                {"channels": MARKET_CHANNELS, "market_tickers": sorted(self.markets), "use_yes_price": True},
            )
            await self._send("subscribe", {"channels": ["cfbenchmarks_value"], "index_ids": INDEX_IDS})
            tasks = [
                asyncio.create_task(self._periodic(self.discover_every, self.refresh_markets)),
                asyncio.create_task(self._periodic(self.snapshot_every, self.request_snapshots)),
            ]
            try:
                async for raw in ws:
                    await self.handle(raw)
            finally:
                for task in tasks:
                    task.cancel()
                self._note("disconnected")

    async def run(self) -> None:
        """Record forever, reconnecting with capped exponential backoff."""
        backoff = 1.0
        while True:
            started = time.monotonic()
            try:
                await self.session()
            except (OSError, httpx.HTTPError, websockets.WebSocketException) as exc:
                log.warning("kalshi connection lost: %s", exc)
            if time.monotonic() - started > 60:
                backoff = 1.0
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
