import re
from typing import Optional


def is_valid_txid(txid: str) -> bool:
    """Basic format validation for cryptocurrency transaction hash/ID.

    Accepts standard hex or alphanumeric hashes between 16 and 128 chars.
    """
    if not txid or not isinstance(txid, str):
        return False
    cleaned = txid.strip()
    # Check length (supports 6-128 chars for Binance Pay Order IDs and blockchain TxIDs)
    if len(cleaned) < 6 or len(cleaned) > 128:
        return False
    # Check characters (hex or base58/alphanumeric)
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", cleaned))


def sanitize_callback_data(data: str) -> str:
    """Ensure callback data string is within Telegram's 64-byte payload limit."""
    return data[:64] if data else ""
