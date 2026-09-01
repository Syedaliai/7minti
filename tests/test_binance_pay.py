from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
import pytest

from app.config import settings
from app.db.models import Checkout, CheckoutStatus
from app.db.repositories import CheckoutRepository, PaymentRepository, UserRepository
from app.services.binance import BinanceService
from app.services.payment_verifier import PaymentVerifier, VerificationResult


@pytest.mark.asyncio
async def test_binance_pay_order_id_verification_success(db_session):
    """Verify customer payment using Binance Pay Order ID matches successfully."""
    user_repo = UserRepository(db_session)
    checkout_repo = CheckoutRepository(db_session)
    payment_repo = PaymentRepository(db_session)

    user = await user_repo.upsert_user(telegram_id=999, username="binance_payer", first_name="BinanceUser")
    checkout = await checkout_repo.create(
        user_id=user.id,
        product_id="prod_ai_1",
        product_name="Claude Pro Access",
        supplier_price=Decimal("10.00"),
        commission=Decimal("2.00"),
        customer_unit_price=Decimal("12.00"),
        quantity=1,
        expected_total=Decimal("12.00"),
        coin="USDT",
        network="TRC20",
        payment_address="TTestBinanceDepositAddress123",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )

    # Mock Binance Pay transaction API response
    mock_binance = BinanceService()
    mock_binance.get_pay_transactions = AsyncMock(return_value=[
        {
            "orderId": "28941829482910",
            "transactionId": "M_P_998877",
            "currency": "USDT",
            "amount": "12.00000000",
            "status": "SUCCESS",
            "receiverInfo": {
                "binanceId": str(settings.BINANCE_UID or "1254494426"),
            },
            "transactionTime": 1700000000000,
        }
    ])
    mock_binance.get_deposit_history = AsyncMock(return_value=[])

    verifier = PaymentVerifier(mock_binance, payment_repo, checkout_repo)
    result, msg, meta = await verifier.verify_txid(checkout, "28941829482910", user.telegram_id)

    assert result == VerificationResult.PAID
    assert meta is not None
    assert meta["type"] == "binance_pay"
    assert meta["amount"] == "12.00000000"

    # Payment record persisted
    payment = await payment_repo.get_by_txid("28941829482910")
    assert payment is not None
    assert payment.received_amount == Decimal("12.00000000")


@pytest.mark.asyncio
async def test_binance_pay_fake_or_tampered_order_id_rejected(db_session):
    """Verify fake/manipulated Binance Pay Order ID is completely rejected."""
    user_repo = UserRepository(db_session)
    checkout_repo = CheckoutRepository(db_session)
    payment_repo = PaymentRepository(db_session)

    user = await user_repo.upsert_user(telegram_id=998, username="tamper_attacker", first_name="Attacker")
    checkout = await checkout_repo.create(
        user_id=user.id,
        product_id="prod_ai_1",
        product_name="Claude Pro Access",
        supplier_price=Decimal("10.00"),
        commission=Decimal("2.00"),
        customer_unit_price=Decimal("12.00"),
        quantity=1,
        expected_total=Decimal("12.00"),
        coin="USDT",
        network="TRC20",
        payment_address="TTestBinanceDepositAddress123",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )

    mock_binance = BinanceService()
    # No matching order on Binance
    mock_binance.get_pay_transactions = AsyncMock(return_value=[])
    mock_binance.get_deposit_history = AsyncMock(return_value=[])

    verifier = PaymentVerifier(mock_binance, payment_repo, checkout_repo)
    result, msg, meta = await verifier.verify_txid(checkout, "fake_order_id_12345", user.telegram_id)

    assert result == VerificationResult.INVALID_TXID
    # No payment record created
    payment = await payment_repo.get_by_txid("fake_order_id_12345")
    assert payment is None


@pytest.mark.asyncio
async def test_binance_pay_duplicate_replay_attack_rejected(db_session):
    """Verify that an already redeemed Binance Pay Order ID cannot be re-used."""
    user_repo = UserRepository(db_session)
    checkout_repo = CheckoutRepository(db_session)
    payment_repo = PaymentRepository(db_session)

    user1 = await user_repo.upsert_user(telegram_id=991, username="user1", first_name="User1")
    user2 = await user_repo.upsert_user(telegram_id=992, username="user2", first_name="User2")

    checkout1 = await checkout_repo.create(
        user_id=user1.id,
        product_id="prod_1",
        product_name="Product 1",
        supplier_price=Decimal("5.00"),
        commission=Decimal("1.00"),
        customer_unit_price=Decimal("6.00"),
        quantity=1,
        expected_total=Decimal("6.00"),
        coin="USDT",
        network="TRC20",
        payment_address="TTestAddress",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )

    # First user pays and redeems Order ID 555666777
    await payment_repo.create(
        checkout_id=checkout1.id,
        user_id=user1.id,
        txid="555666777",
        coin="USDT",
        network="Binance Pay",
        expected_amount=Decimal("6.00"),
        received_amount=Decimal("6.00"),
        status=CheckoutStatus.PAID.value,
        verified_at=datetime.now(timezone.utc),
    )

    # Second user creates a new checkout and tries to submit the same Order ID
    checkout2 = await checkout_repo.create(
        user_id=user2.id,
        product_id="prod_2",
        product_name="Product 2",
        supplier_price=Decimal("5.00"),
        commission=Decimal("1.00"),
        customer_unit_price=Decimal("6.00"),
        quantity=1,
        expected_total=Decimal("6.00"),
        coin="USDT",
        network="TRC20",
        payment_address="TTestAddress",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )

    mock_binance = BinanceService()
    mock_binance.get_pay_transactions = AsyncMock(return_value=[
        {
            "orderId": "555666777",
            "currency": "USDT",
            "amount": "6.00000000",
            "status": "SUCCESS",
        }
    ])

    verifier = PaymentVerifier(mock_binance, payment_repo, checkout_repo)
    result, msg, meta = await verifier.verify_txid(checkout2, "555666777", user2.telegram_id)

    # Must be rejected as ALREADY_USED_TXID
    assert result == VerificationResult.ALREADY_USED_TXID
