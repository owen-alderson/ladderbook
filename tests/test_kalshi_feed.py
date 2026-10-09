"""Run the Kalshi feed against a local fake exchange that drops a message."""

import asyncio
import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from kalshi_msgs import delta, snapshot
from websockets.asyncio.server import serve

from ladderbook.kalshi.auth import KalshiSigner
from ladderbook.kalshi.rest import KalshiREST
from ladderbook.record.kalshi_feed import KalshiFeed
from ladderbook.record.verify import verify_rows


class MemoryWriter:
    def __init__(self):
        self.rows = []

    def write(self, source, raw, recv_ns=None):
        self.rows.append((source, raw))


def _rest(tickers):
    def handler(request):
        series = request.url.params["series_ticker"]
        markets = [{"ticker": t} for t in tickers if t.startswith(series)]
        return httpx.Response(200, json={"markets": markets, "cursor": ""})

    return KalshiREST("http://kalshi.test/trade-api/v2", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.fixture
def signer():
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return KalshiSigner.from_pem("test-key", pem), key.public_key()


def test_records_everything_detects_gap_and_resnapshots(signer):
    signer, public_key = signer
    received = []

    async def exchange(ws):
        headers = ws.request.headers
        message = (headers["KALSHI-ACCESS-TIMESTAMP"] + "GET/trade-api/ws/v2").encode()
        public_key.verify(base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]), message)
        sub = json.loads(await ws.recv())
        received.append(sub)
        await ws.send(json.dumps({"id": 1, "type": "subscribed", "msg": {"channel": "orderbook_delta", "sid": 1}}))
        received.append(json.loads(await ws.recv()))  # cfbenchmarks subscribe
        await ws.send(snapshot("KXBTCD-A", 1, yes=[("0.4000", "10.00")]))
        await ws.send(delta("KXBTCD-A", 2, "0.4000", "-1.00", "yes"))
        await ws.send(delta("KXBTCD-A", 4, "0.4000", "-1.00", "yes"))  # seq 3 never arrives
        received.append(json.loads(await ws.recv()))  # the recorder must ask for a snapshot
        await ws.send(snapshot("KXBTCD-A", 5, yes=[("0.4000", "7.00")]))
        await ws.send(delta("KXBTCD-A", 6, "0.4000", "1.00", "yes"))
        await ws.send(snapshot("KXBTCD-A", 7, yes=[("0.4000", "8.00")]))

    async def scenario():
        async with serve(exchange, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            writer = MemoryWriter()
            feed = KalshiFeed(signer, writer, _rest(["KXBTCD-A"]), ["KXBTCD"], url=f"ws://127.0.0.1:{port}/trade-api/ws/v2")
            await asyncio.wait_for(feed.session(), timeout=5)
            return feed, writer

    feed, writer = asyncio.run(scenario())

    subscribe = received[0]["params"]
    assert subscribe["market_tickers"] == ["KXBTCD-A"] and subscribe["use_yes_price"] is True
    assert received[1]["params"]["channels"] == ["cfbenchmarks_value"]
    assert received[2]["params"] == {"sid": 1, "market_tickers": ["KXBTCD-A"], "action": "get_snapshot"}
    assert feed.gaps == 1

    kinds = [json.loads(raw)["type"] for source, raw in writer.rows if source == "ladderbook"]
    assert kinds[0] == "connected" and "gap" in kinds and kinds[-1] == "disconnected"

    report = verify_rows(writer.rows)
    assert report.resyncs == 1  # the snapshot after the gap re-seeded the book
    assert report.snapshots_compared == 1 and report.ok  # and the next one matched exactly


def test_refresh_adds_new_markets_and_drops_settled_ones(signer):
    signer, _ = signer
    sent = []

    class FakeWS:
        async def send(self, text):
            sent.append(json.loads(text))

    feed = KalshiFeed(signer, MemoryWriter(), _rest(["KXBTCD-B", "KXBTCD-C"]), ["KXBTCD"])
    feed._ws = FakeWS()
    feed.markets = {"KXBTCD-A", "KXBTCD-B"}
    feed.sids = {"orderbook_delta": 1, "trade": 2, "ticker": 3}
    asyncio.run(feed.refresh_markets())
    actions = {(m["params"]["sid"], m["params"]["action"], tuple(m["params"]["market_tickers"])) for m in sent}
    assert actions == {
        (sid, action, tickers)
        for sid in (1, 2, 3)
        for action, tickers in (("add_markets", ("KXBTCD-C",)), ("delete_markets", ("KXBTCD-A",)))
    }
    assert feed.markets == {"KXBTCD-B", "KXBTCD-C"}
