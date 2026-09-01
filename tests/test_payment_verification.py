from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
import pytest

from app.db.models import Checkout, CheckoutStatus
from app.db.repositories import CheckoutRepository, PaymentRepository, UserRepository
from app.services.binance import BinanceService
from app.services.payment_verifier import PaymentVerifier, VerificationResult


@pytest.mark.asyncio
async def test_payment_verification_valid(db_session):
    """Verify successful 11-point deposit match."""
    user_repo = UserRepository(db_session)
    checkout_repo = CheckoutRepository(db_session)
    payment_repo = PaymentRepository(db_session)

    user = await user_repo.upsert_user(telegram_id=111, username="testuser", first_name="Test")
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

    # Mock Binance response with exact matching transaction
    mock_binance = BinanceService()
    mock_binance.get_deposit_history = AsyncMock(return_value=[
        {
            "txId": "0xabc123validtxid",
            "amount": "2.70000000",
            "coin": "USDT",
            "network": "TRX", # Alias for TRC20
            "address": "TTestBinanceDepositAddress123",
            "status": 1,      # Success
            "insertTime": 1700000000000,
        }
    ])

    verifier = PaymentVerifier(mock_binance, payment_repo, checkout_repo)
    result, msg, meta = await verifier.verify_txid(checkout, "0xabc123validtxid", user.telegram_id)

    assert result == VerificationResult.PAID

    # Verify payment record was created
    payment = await payment_repo.get_by_txid("0xabc123validtxid")
    assert payment is not None
    assert payment.received_amount == Decimal("2.70000000")


@pytest.mark.asyncio
async def test_payment_underpaid_rejected(db_session):
    """Verify underpaid transaction is rejected and marked UNDERPAID without fulfilling."""
    user_repo = UserRepository(db_session)
    checkout_repo = CheckoutRepository(db_session)
    payment_repo = PaymentRepository(db_session)

    user = await user_repo.upsert_user(telegram_id=112, username="underpaid_user", first_name="Test")
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

    # Deposit was only 2.00 USDT instead of 2.70
    mock_binance = BinanceService()
    mock_binance.get_deposit_history = AsyncMock(return_value=[
        {
            "txId": "0xshortpayment",
            "amount": "2.00000000",
            "coin": "USDT",
            "network": "TRC20",
            "address": "TTestBinanceDepositAddress123",
            "status": 1,
        }
    ])

    verifier = PaymentVerifier(mock_binance, payment_repo, checkout_repo)
    result, msg, meta = await verifier.verify_txid(checkout, "0xshortpayment", user.telegram_id)

    assert result == VerificationResult.UNDERPAID
    assert "Underpayment" in msg

    # Checkout status must be updated to UNDERPAID
    updated_checkout = await checkout_repo.get_by_id(checkout.id)
    assert updated_checkout.status == CheckoutStatus.UNDERPAID.value


@pytest.mark.asyncio
async def test_payment_unconfirmed_pending(db_session):
    """Verify unconfirmed blockchain transaction (status 0) is held as pending."""
    user_repo = UserRepository(db_session)
    checkout_repo = CheckoutRepository(db_session)
    payment_repo = PaymentRepository(db_session)

    user = await user_repo.upsert_user(telegram_id=113, username="unconfirmed_user", first_name="Test")
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

    mock_binance = BinanceService()
    mock_binance.get_deposit_history = AsyncMock(return_value=[
        {
            "txId": "0xpendingtx",
            "amount": "2.70000000",
            "coin": "USDT",
            "network": "TRC20",
            "address": "TTestBinanceDepositAddress123",
            "status": 0, # Pending confirmation
        }
    ])

    verifier = PaymentVerifier(mock_binance, payment_repo, checkout_repo)
    result, msg, meta = await verifier.verify_txid(checkout, "0xpendingtx", user.telegram_id)

    assert result == VerificationResult.UNCONFIRMED_PENDING
