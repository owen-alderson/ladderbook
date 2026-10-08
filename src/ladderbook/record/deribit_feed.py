"""Deribit public websocket: index prices on every tick, the options mark-IV surface sampled.

`markprice.options.<index>` carries every listed option (~1,000 for BTC) about once a
second. Fair value for hourly contracts doesn't need that rate, so one full surface is
kept every `surface_every` seconds. Kept messages are stored verbatim; sampling is the
only thing done to them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import websockets

from ladderbook.net import SSL_CONTEXT
from ladderbook.record.writer import RawWriter

log = logging.getLogger(__name__)

DERIBIT_WS = "wss://www.deribit.com/ws/api/v2"
INDEXES = ["btc_usd", "eth_usd"]


class DeribitFeed:
    def __init__(self, writer: RawWriter, url: str = DERIBIT_WS, surface_every: float = 15.0, clock=time.monotonic):
        self.writer = writer
        self.url = url
        self.surface_every = surface_every
        self._clock = clock
        self._last_surface: dict[str, float] = {}

    def channels(self) -> list[str]:
        return [f"deribit_price_index.{i}" for i in INDEXES] + [f"markprice.options.{i}" for i in INDEXES]

    def handle(self, raw: str) -> bool:
        """Record the message unless it is a surface update inside the sampling interval."""
        msg = json.loads(raw)
        channel = msg.get("params", {}).get("channel", "")
        if channel.startswith("markprice.options."):
            now = self._clock()
            last = self._last_surface.get(channel)
            if last is not None and now - last < self.surface_every:
                return False
            self._last_surface[channel] = now
        self.writer.write("deribit", raw)
        return True

    async def session(self) -> None:
        ssl = SSL_CONTEXT if self.url.startswith("wss://") else None
        async with websockets.connect(self.url, ssl=ssl, max_size=2**24) as ws:
            self._last_surface.clear()
            request = {"jsonrpc": "2.0", "id": 1, "method": "public/subscribe", "params": {"channels": self.channels()}}
            self.writer.write("ladderbook", json.dumps({"type": "command", "venue": "deribit", "command": request}))
            await ws.send(json.dumps(request))
            async for raw in ws:
                self.handle(raw)

    async def run(self) -> None:
        backoff = 1.0
        while True:
            started = time.monotonic()
            try:
                await self.session()
            except (OSError, websockets.WebSocketException) as exc:
                log.warning("deribit connection lost: %s", exc)
            if time.monotonic() - started > 60:
                backoff = 1.0
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
