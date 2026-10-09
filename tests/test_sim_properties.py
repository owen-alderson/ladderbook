"""Properties of the simulated exchange over many random tapes."""

import numpy as np

from ladderbook import _core as c

C = 100  # one contract


def random_tape(rng, n=400):
    """A plausible tape around 40c bid / 45c ask: joins, cancels, and trades with their decrements."""
    events = [("level", c.Side.BID, 4000, int(rng.integers(50, 500)) * C), ("level", c.Side.ASK, 4500, 300 * C)]
    depth = events[0][3]
    for _ in range(n):
        r = rng.random()
        if r < 0.4:
            size = int(rng.integers(1, 100)) * C
            events.append(("delta", c.Side.BID, 4000, size))
            depth += size
        elif r < 0.7 and depth > 0:
            size = int(rng.integers(1, depth // C + 1)) * C
            events.append(("delta", c.Side.BID, 4000, -size))
            depth -= size
        elif depth > 0:
            size = int(rng.integers(1, depth // C + 1)) * C
            events.append(("trade", 4000, size))
            events.append(("delta", c.Side.BID, 4000, -size))
            depth -= size
    return events


def run(events, model, order_qty=50 * C):
    cfg = c.SimConfig()
    cfg.order_latency = cfg.cancel_latency = 0
    cfg.queue_model = model
    ex = c.SimExchange(cfg)
    ex.on_clear(0)
    ts = 0
    order = None
    fills = []
    for i, event in enumerate(events):
        ts = i
        if event[0] == "level":
            ex.on_level(ts, *event[1:])
        elif event[0] == "delta":
            ex.on_delta(ts, *event[1:])
        else:
            ex.on_trade(ts, event[1], event[2], c.Side.ASK)
        if i == 1:
            order = ex.place(ts, c.Side.BID, 4000, order_qty)
        fills += ex.take_fills()
    return ex, order, fills


def test_queue_models_bracket_each_other():
    rng = np.random.default_rng(0)
    for _ in range(300):
        tape = random_tape(rng)
        filled = [sum(f.qty for f in run(tape, m)[2]) for m in (c.QueueModel.CONSERVATIVE, c.QueueModel.PROPORTIONAL, c.QueueModel.OPTIMISTIC)]
        assert filled[0] <= filled[1] <= filled[2]


def test_never_filled_beyond_the_volume_that_traded_after_us():
    rng = np.random.default_rng(1)
    for _ in range(300):
        tape = random_tape(rng)
        traded_after = sum(e[2] for e in tape[2:] if e[0] == "trade")
        for model in (c.QueueModel.CONSERVATIVE, c.QueueModel.PROPORTIONAL, c.QueueModel.OPTIMISTIC):
            _, _, fills = run(tape, model)
            assert sum(f.qty for f in fills) <= min(traded_after, 50 * C)


def test_without_cancels_we_fill_exactly_what_trades_beyond_our_queue():
    rng = np.random.default_rng(2)
    for _ in range(300):
        # joins and trades only: no size ever leaves without trading, so every model agrees
        tape = [e for e in random_tape(rng) if not (e[0] == "delta" and e[3] < 0)]
        depth, cleaned = tape[0][3], tape[:2]
        for e in tape[2:]:
            if e[0] == "trade":
                size = min(e[2], depth)
                if size == 0:
                    continue
                cleaned += [("trade", 4000, size), ("delta", c.Side.BID, 4000, -size)]
                depth -= size
            else:
                cleaned.append(e)
                depth += e[3]
        ahead = cleaned[0][3]
        traded_after = sum(e[2] for e in cleaned[2:] if e[0] == "trade")
        expected = min(max(traded_after - ahead, 0), 50 * C)
        for model in (c.QueueModel.CONSERVATIVE, c.QueueModel.PROPORTIONAL, c.QueueModel.OPTIMISTIC):
            assert sum(f.qty for f in run(cleaned, model)[2]) == expected


def test_cash_and_position_reconcile_with_fills():
    rng = np.random.default_rng(3)
    for _ in range(100):
        ex, _, fills = run(random_tape(rng), c.QueueModel.PROPORTIONAL)
        assert ex.position == sum(f.qty if f.side == c.Side.BID else -f.qty for f in fills)
        assert ex.cash == sum(f.balance_change for f in fills)
        assert ex.fees_paid == sum(f.fee for f in fills)


def test_replay_is_deterministic():
    tape = random_tape(np.random.default_rng(4), n=2000)
    first = [(f.ts, f.qty, f.price, f.balance_change) for f in run(tape, c.QueueModel.PROPORTIONAL)[2]]
    second = [(f.ts, f.qty, f.price, f.balance_change) for f in run(tape, c.QueueModel.PROPORTIONAL)[2]]
    assert first == second and first
