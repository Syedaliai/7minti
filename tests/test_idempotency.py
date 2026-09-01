from decimal import Decimal
from unittest.mock import AsyncMock, patch
import pytest

from app.db.models import OrderStatus
from app.db.repositories import CheckoutRepository, OrderRepository, UserRepository
from app.services.encryption import EncryptionService
from app.services.order_service import OrderService
from app.services.prodseller import ProdSellerService
from app.utils.ids import generate_idempotency_key


def test_idempotency_key_format_and_length():
    """Verify Idempotency-Key meets ProdSeller spec (max 100 characters, stable format)."""
    user_id = 123456789
    checkout_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"

    key = generate_idempotency_key(user_id, checkout_id)
    assert key == f"tg_{user_id}_{checkout_id}"
    assert len(key) <= 100


@pytest.mark.asyncio
async def test_order_fulfillment_uses_same_idempotency_key_on_retry(db_session):
    """Verify that retrying fulfillment of an order reuses the EXACT same persisted Idempotency-Key."""
    user_repo = UserRepository(db_session)
    checkout_repo = CheckoutRepository(db_session)
    order_repo = OrderRepository(db_session)
    enc_service = EncryptionService()

    from datetime import datetime, timedelta, timezone
    user = await user_repo.upsert_user(telegram_id=301, username="test_idemp", first_name="Test")
    checkout = await checkout_repo.create(
        user_id=user.id,
        product_id="prod_1",
        product_name="ChatGPT Plus",
        supplier_price=Decimal("2.50"),
        commission=Decimal("0.20"),
        customer_unit_price=Decimal("2.70"),
        quantity=1,
        expected_total=Decimal("2.70"),
        coin="USDT",
        network="TRC20",
        payment_address="TTestBinanceDepositAddress123",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )

    mock_prodseller = ProdSellerService()
    # Mock supplier response
    mock_prodseller.get_product = AsyncMock(return_value={"id": "prod_1", "price": 2.50, "inStock": True})
    mock_prodseller.create_order = AsyncMock(return_value={
        "orderId": "supp_order_999",
        "status": "delivered",
        "deliveredKey": "user@ai.com:secretpass123",
    })

    order_service = OrderService(order_repo, checkout_repo, mock_prodseller, enc_service)

    # 1. First fulfillment attempt
    status1, msg1, keys1 = await order_service.fulfill_order(checkout, user.telegram_id)
    assert status1 == OrderStatus.DELIVERED
    assert keys1 == ["user@ai.com:secretpass123"]

    persisted_order = await order_repo.get_by_checkout_id(checkout.id)
    first_idempotency_key = persisted_order.idempotency_key

    # 2. Second attempt (retry) on same checkout
    status2, msg2, keys2 = await order_service.fulfill_order(checkout, user.telegram_id)
    assert status2 == OrderStatus.DELIVERED
    assert keys2 == ["user@ai.com:secretpass123"]

    # Verify supplier create_order was called with the persisted idempotency key
    mock_prodseller.create_order.assert_called_with(
        product_id="prod_1",
        quantity=1,
        idempotency_key=first_idempotency_key,
    )
