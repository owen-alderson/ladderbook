"""Builders for Kalshi websocket messages, shaped like the AsyncAPI spec."""

import json


def subscribed(channel, sid):
    return json.dumps({"id": 1, "type": "subscribed", "msg": {"channel": channel, "sid": sid}})


def snapshot(ticker, seq, yes=(), no=(), sid=1):
    body = {"market_ticker": ticker, "market_id": "m-" + ticker}
    if yes:
        body["yes_dollars_fp"] = [list(level) for level in yes]
    if no:
        body["no_dollars_fp"] = [list(level) for level in no]
    return json.dumps({"type": "orderbook_snapshot", "sid": sid, "seq": seq, "msg": body})


def delta(ticker, seq, price, change, side, sid=1):
    body = {"market_ticker": ticker, "market_id": "m-" + ticker, "price_dollars": price, "delta_fp": change, "side": side}
    return json.dumps({"type": "orderbook_delta", "sid": sid, "seq": seq, "msg": body})


def note(kind, **fields):
    return json.dumps({"type": kind, **fields})


def subscribe_command(use_yes_price=True):
    params = {"channels": ["orderbook_delta", "trade", "ticker"], "market_tickers": ["X"], "use_yes_price": use_yes_price}
    return json.dumps({"type": "command", "command": {"id": 1, "cmd": "subscribe", "params": params}})
