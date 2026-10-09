from datetime import datetime, timezone

import pytest

from ladderbook.deribit.surface import nearest_smile, parse_instrument

NOW = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)


def test_parse_instrument():
    assert parse_instrument("BTC-16OCT26-83000-P") == (datetime(2026, 10, 16, 8, tzinfo=timezone.utc), 83000.0, "P")
    assert parse_instrument("BTC-9OCT26-81500-C")[0] == datetime(2026, 10, 9, 8, tzinfo=timezone.utc)
    assert parse_instrument("XRP_USDC-9OCT26-2d5-C")[1] == 2.5


def test_nearest_smile_uses_out_of_the_money_options_of_the_nearest_expiry():
    marks = []
    for strike in range(76_000, 90_000, 1_000):
        otm, itm = ("P", "C") if strike < 82_000 else ("C", "P")
        marks.append({"instrument_name": f"BTC-9OCT26-{strike}-{otm}", "mark_iv": 50.0})
        marks.append({"instrument_name": f"BTC-9OCT26-{strike}-{itm}", "mark_iv": 99.0})  # ignored
        marks.append({"instrument_name": f"BTC-16OCT26-{strike}-{otm}", "iv": 0.30})  # later expiry: ignored
    smile = nearest_smile(marks, forward=82_000.0, now=NOW)
    assert smile.atm_vol == pytest.approx(0.5, abs=1e-4)
    assert smile.expiry_years * 365 * 24 == pytest.approx(8.0)


def test_expiries_too_close_are_skipped():
    marks = [{"instrument_name": "BTC-9OCT26-80000-P", "mark_iv": 50.0}]
    with pytest.raises(ValueError):
        nearest_smile(marks, forward=82_000.0, now=datetime(2026, 10, 9, 7, tzinfo=timezone.utc))
