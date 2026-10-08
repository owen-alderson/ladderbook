import json

from ladderbook.record.deribit_feed import DeribitFeed


class FakeWriter:
    def __init__(self):
        self.rows = []

    def write(self, source, raw, recv_ns=None):
        self.rows.append((source, raw))


def _msg(channel):
    return json.dumps({"jsonrpc": "2.0", "method": "subscription", "params": {"channel": channel, "data": {}}})


def test_surface_is_sampled_but_index_ticks_are_all_kept():
    now = [0.0]
    writer = FakeWriter()
    feed = DeribitFeed(writer, surface_every=15, clock=lambda: now[0])
    assert feed.handle(_msg("markprice.options.btc_usd"))
    now[0] = 5
    assert not feed.handle(_msg("markprice.options.btc_usd"))
    assert feed.handle(_msg("markprice.options.eth_usd"))  # each index sampled separately
    assert feed.handle(_msg("deribit_price_index.btc_usd"))
    now[0] = 15
    assert feed.handle(_msg("markprice.options.btc_usd"))
    assert len(writer.rows) == 4


def test_subscribes_to_index_and_surface_for_btc_and_eth():
    channels = DeribitFeed(FakeWriter()).channels()
    assert "deribit_price_index.btc_usd" in channels and "markprice.options.eth_usd" in channels
