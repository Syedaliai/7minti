from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Union


def to_decimal(val: Union[str, int, float, Decimal, Any]) -> Decimal:
    """Safely convert any numeric input or string representation to Decimal."""
    if isinstance(val, Decimal):
        return val
    if val is None:
        return Decimal("0.00")
    return Decimal(str(val))


def format_currency(amount: Union[Decimal, str, int, float], currency: str = "USDT") -> str:
    """Format a monetary Decimal to two decimal places with the currency symbol."""
    dec = to_decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{dec:.2f} {currency}"


def format_decimal(amount: Union[Decimal, str, int, float], places: int = 2) -> str:
    """Format decimal to standard display without trailing floating artifacts."""
    fmt = "0." + "0" * places
    dec = to_decimal(amount).quantize(Decimal(fmt), rounding=ROUND_HALF_UP)
    return f"{dec:.{places}f}"
