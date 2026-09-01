from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
import pytest

from app.db.models import OrderStatus
from app.db.repositories import CheckoutRepository, OrderRepository, UserRepository
from app.services.encryption import EncryptionService
from app.services.order_service import OrderService
from app.services.prodseller import ProdSellerService


@pytest.mark.asyncio
async def test_order_authorization_isolation(db_session):
    """Ensure User A cannot retrieve or query User B's orders."""
    user_repo = UserRepository(db_session)
    checkout_repo = CheckoutRepository(db_session)
    order_repo = OrderRepository(db_session)
    enc = EncryptionService()

    user_a = await user_repo.upsert_user(telegram_id=401, username="usera", first_name="A")
    user_b = await user_repo.upsert_user(telegram_id=402, username="userb", first_name="B")

    checkout_a = await checkout_repo.create(
        user_id=user_a.id,
        product_id="prod_a",
        product_name="Product A",
        supplier_price=Decimal("1.00"),
        commission=Decimal("0.20"),
        customer_unit_price=Decimal("1.20"),
        quantity=1,
        expected_total=Decimal("1.20"),
        coin="USDT",
        network="TRC20",
        payment_address="TAddress",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )

    # Order belonging to User A
    order_a = await order_repo.create(
        checkout_id=checkout_a.id,
        user_id=user_a.id,
        product_id="prod_a",
        idempotency_key="tg_401_order1",
        supplier_amount=Decimal("1.00"),
        customer_amount=Decimal("1.20"),
        commission_amount=Decimal("0.20"),
        quantity=1,
        status=OrderStatus.DELIVERED,
    )

    # User B lists their orders
    user_b_orders = await order_repo.get_user_orders(user_b.id)
    assert len(user_b_orders) == 0

    # User A lists their orders
    user_a_orders = await order_repo.get_user_orders(user_a.id)
    assert len(user_a_orders) == 1
    assert user_a_orders[0].id == order_a.id


@pytest.mark.asyncio
async def test_delivered_multiple_keys_support(db_session):
    """Ensure both deliveredKey and deliveredKeys are captured, encrypted, and decrypted."""
    user_repo = UserRepository(db_session)
    checkout_repo = CheckoutRepository(db_session)
    order_repo = OrderRepository(db_session)
    enc = EncryptionService()

    user = await user_repo.upsert_user(telegram_id=403, username="user_multikey", first_name="Test")
    checkout = await checkout_repo.create(
        user_id=user.id,
        product_id="prod_bulk",
        product_name="Bulk Keys",
        supplier_price=Decimal("2.00"),
        commission=Decimal("0.20"),
        customer_unit_price=Decimal("2.20"),
        quantity=2,
        expected_total=Decimal("4.40"),
        coin="USDT",
        network="TRC20",
        payment_address="TAddress",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )

    mock_prodseller = ProdSellerService()
    mock_prodseller.get_product = AsyncMock(return_value={"id": "prod_bulk", "price": 2.00, "inStock": True})
    mock_prodseller.create_order = AsyncMock(return_value={
        "orderId": "bulk_order_123",
        "status": "delivered",
        "deliveredKeys": ["key_alpha_1", "key_alpha_2"],
    })

    service = OrderService(order_repo, checkout_repo, mock_prodseller, enc)
    status, msg, keys = await service.fulfill_order(checkout, user.telegram_id)

    assert status == OrderStatus.DELIVERED
    assert keys == ["key_alpha_1", "key_alpha_2"]

    # Verify encrypted at rest
    persisted_order = await order_repo.get_by_checkout_id(checkout.id)
    assert persisted_order.delivered_data_encrypted is not None
    decrypted = enc.decrypt(persisted_order.delivered_data_encrypted)
    assert decrypted == ["key_alpha_1", "key_alpha_2"]


@pytest.mark.asyncio
async def test_price_increase_after_payment_blocks_loss(db_session):
    """If supplier price increases between checkout and fulfillment, order is placed in PRICE_CHANGED_REVIEW."""
    user_repo = UserRepository(db_session)
    checkout_repo = CheckoutRepository(db_session)
    order_repo = OrderRepository(db_session)
    enc = EncryptionService()

    user = await user_repo.upsert_user(telegram_id=404, username="user_pricechange", first_name="Test")
    checkout = await checkout_repo.create(
        user_id=user.id,
        product_id="prod_volatile",
        product_name="Volatile AI",
        supplier_price=Decimal("2.00"),
        commission=Decimal("0.20"),
        customer_unit_price=Decimal("2.20"),
        quantity=1,
        expected_total=Decimal("2.20"),
        coin="USDT",
        network="TRC20",
        payment_address="TAddress",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )

    # Supplier price suddenly jumps to 5.00
    mock_prodseller = ProdSellerService()
    mock_prodseller.get_product = AsyncMock(return_value={"id": "prod_volatile", "price": 5.00, "inStock": True})

    service = OrderService(order_repo, checkout_repo, mock_prodseller, enc)
    status, msg, keys = await service.fulfill_order(checkout, user.telegram_id)

    assert status == OrderStatus.PRICE_CHANGED_REVIEW
    assert "Price Update Required" in msg
    assert keys is None

@pytest.mark.asyncio
async def test_admin_sales_queries_use_checkout_product_names(db_session):
    """Admin reports should not rely on a non-existent orders.product_name column."""
    user_repo = UserRepository(db_session)
    checkout_repo = CheckoutRepository(db_session)
    order_repo = OrderRepository(db_session)

    user = await user_repo.upsert_user(telegram_id=405, username="admin_report_user", first_name="Test")
    checkout = await checkout_repo.create(
        user_id=user.id,
        product_id="prod_report",
        product_name="Reportable Product",
        supplier_price=Decimal("1.00"),
        commission=Decimal("0.20"),
        customer_unit_price=Decimal("1.20"),
        quantity=2,
        expected_total=Decimal("2.40"),
        coin="USDT",
        network="TRC20",
        payment_address="TAddress",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    await order_repo.create(
        checkout_id=checkout.id,
        user_id=user.id,
        product_id="prod_report",
        idempotency_key="tg_405_report",
        supplier_amount=Decimal("2.00"),
        customer_amount=Decimal("2.40"),
        commission_amount=Decimal("0.40"),
        quantity=2,
        status=OrderStatus.DELIVERED,
    )

    top_items = await order_repo.get_top_selling_products()
    recent_orders = await order_repo.get_recent_orders()

    assert top_items[0]["name"] == "Reportable Product"
    assert top_items[0]["sales_count"] == 1
    assert recent_orders[0].checkout.product_name == "Reportable Product"
