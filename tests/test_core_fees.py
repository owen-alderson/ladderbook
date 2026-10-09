"""The Python bindings expose the same exact fee arithmetic as the C++ tests."""

from ladderbook import _core


def test_documented_example():
    assert _core.trade_fee_micros(_core.TAKER_RATE, 10000, 550, 100) == 3639


def test_rounding_accumulator():
    acc = _core.OrderFeeAccumulator()
    cash = acc.apply(-55_000, 3_639)
    assert (cash.balance_change, cash.rounding_fee, cash.rebate) == (-60_000, 1_361, 0)
    assert acc.carried == 1_361
