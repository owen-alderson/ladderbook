"""Run the Kalshi and Deribit feeds side by side into one raw tape."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ladderbook.kalshi import DEMO_REST, DEMO_WS, PROD_REST, PROD_WS
from ladderbook.kalshi.auth import KalshiSigner
from ladderbook.kalshi.rest import KalshiREST
from ladderbook.record.deribit_feed import DeribitFeed
from ladderbook.record.kalshi_feed import KalshiFeed
from ladderbook.record.writer import RawWriter

DEFAULT_SERIES = ["KXBTCD", "KXBTC", "KXETHD", "KXETH"]
log = logging.getLogger(__name__)


async def _flush_every(writer: RawWriter, seconds: float) -> None:
    while True:
        await asyncio.sleep(seconds)
        writer.flush()  # quiet periods still land on disk


async def record(out: Path, series: list[str], demo: bool = False, rotate_seconds: float = 300.0) -> None:
    signer = KalshiSigner.from_env()
    writer = RawWriter(out, rotate_seconds=rotate_seconds)
    rest = KalshiREST(DEMO_REST if demo else PROD_REST)
    kalshi = KalshiFeed(signer, writer, rest, series, url=DEMO_WS if demo else PROD_WS)
    deribit = DeribitFeed(writer)
    log.info("recording %s to %s", ", ".join(series), out)
    try:
        await asyncio.gather(kalshi.run(), deribit.run(), _flush_every(writer, rotate_seconds))
    finally:
        writer.flush()
        await rest.aclose()
