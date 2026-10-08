import pytest

from ladderbook.units import format_price, parse_price, parse_qty


@pytest.mark.parametrize(
    "text, expected",
    [("0.0800", 800), ("0.08", 800), ("1.0000", 10000), ("0", 0), ("0.5", 5000), ("0.0001", 1), ("0.12340", 1234)],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize("text", ["0.00005", "1.0001", "-0.01", "abc", "", "0.1e-2", "."])
def test_parse_price_rejects(text):
    with pytest.raises(ValueError):
        parse_price(text)


def test_parse_qty():
    assert parse_qty("300.00") == 30000
    assert parse_qty("0.01") == 1
    assert parse_qty("-12.5") == -1250
    with pytest.raises(ValueError):
        parse_qty("0.001")


def test_format_round_trip():
    for p in (0, 1, 800, 5000, 9999, 10000):
        assert parse_price(format_price(p)) == p
