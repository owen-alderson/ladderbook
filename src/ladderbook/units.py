"""Exact conversion between Kalshi's decimal strings and ladderbook's integer units.

Prices are integers in 1e-4 dollars ("0.0800" -> 800); sizes are integers in
hundredths of a contract ("300.00" -> 30000). Anything finer than that is an error,
never a silent rounding.
"""

PRICE_DECIMALS = 4
QTY_DECIMALS = 2
PRICE_ONE = 10**PRICE_DECIMALS


def parse_fixed(text: str, decimals: int) -> int:
    """Parse a plain decimal string into an integer scaled by 10**decimals, exactly."""
    s = text.strip()
    negative = s.startswith("-")
    if negative or s.startswith("+"):
        s = s[1:]
    whole, _, frac = s.partition(".")
    if not (whole or frac) or not (whole + frac).isdigit():
        raise ValueError(f"not a plain decimal: {text!r}")
    extra = frac[decimals:]
    if extra.strip("0"):
        raise ValueError(f"{text!r} has more than {decimals} decimal places")
    value = int(whole or "0") * 10**decimals + int(frac[:decimals].ljust(decimals, "0") or "0")
    return -value if negative else value


def parse_price(text: str) -> int:
    value = parse_fixed(text, PRICE_DECIMALS)
    if not 0 <= value <= PRICE_ONE:
        raise ValueError(f"price {text!r} outside [0, 1]")
    return value


def parse_qty(text: str) -> int:
    return parse_fixed(text, QTY_DECIMALS)


def format_price(price: int) -> str:
    return f"{price // PRICE_ONE}.{price % PRICE_ONE:04d}"
