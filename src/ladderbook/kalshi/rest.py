"""Public (unauthenticated) market-data endpoints."""

from __future__ import annotations

import httpx

from ladderbook.kalshi import PROD_REST


class KalshiREST:
    def __init__(self, base_url: str = PROD_REST, client: httpx.AsyncClient | None = None):
        self._base = base_url
        self._client = client or httpx.AsyncClient(timeout=10.0)

    async def _get(self, path: str, **params) -> dict:
        response = await self._client.get(self._base + path, params=params)
        response.raise_for_status()
        return response.json()

    async def markets(self, series_ticker: str, status: str = "open") -> list[dict]:
        """Every market in a series, following the pagination cursor."""
        out: list[dict] = []
        cursor = None
        while True:
            params = {"series_ticker": series_ticker, "status": status, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            page = await self._get("/markets", **params)
            out.extend(page["markets"])
            cursor = page.get("cursor")
            if not cursor or not page["markets"]:
                return out

    async def orderbook(self, ticker: str) -> dict:
        """{'yes_dollars': [[price, qty], ...], 'no_dollars': [...]} in no-leg pricing."""
        return (await self._get(f"/markets/{ticker}/orderbook"))["orderbook_fp"]

    async def aclose(self) -> None:
        await self._client.aclose()
