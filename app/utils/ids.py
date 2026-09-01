import uuid


def generate_uuid_str() -> str:
    """Generate standard UUID v4 string."""
    return str(uuid.uuid4())


def generate_idempotency_key(telegram_user_id: int, checkout_id: str) -> str:
    """Generate a stable, unique idempotency key for supplier orders.

    ProdSeller limit: Maximum 100 characters.
    Format: tg_{user_id}_{checkout_id}
    """
    key = f"tg_{telegram_user_id}_{checkout_id}"
    return key[:100]
