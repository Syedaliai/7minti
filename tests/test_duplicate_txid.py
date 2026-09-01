from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock
import pytest

from app.db.repositories import CheckoutRepository, PaymentRepository, UserRepository
from app.services.binance import BinanceService
from app.services.payment_verifier import PaymentVerifier, VerificationResult


@pytest.mark.asyncio
async def test_duplicate_txid_across_checkouts_rejected(db_session):
    """Ensure that the same TxID cannot be used by two different checkouts/users (Replay attack protection)."""
    user_repo = UserRepository(db_session)
    checkout_repo = CheckoutRepository(db_session)
    payment_repo = PaymentRepository(db_session)

    user1 = await user_repo.upsert_user(telegram_id=201, username="user1", first_name="User1")
    user2 = await user_repo.upsert_user(telegram_id=202, username="user2", first_name="User2")

    checkout1 = await checkout_repo.create(
        user_id=user1.id,
        product_id="prod_1",
        product_name="Product 1",
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

    checkout2 = await checkout_repo.create(
        user_id=user2.id,
        product_id="prod_2",
        product_name="Product 2",
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

    txid = "0xsharedtxid123456789"

    mock_binance = BinanceService()
    mock_binance.get_deposit_history = AsyncMock(return_value=[
        {
            "txId": txid,
            "amount": "2.70000000",
            "coin": "USDT",
            "network": "TRC20",
            "address": "TTestBinanceDepositAddress123",
            "status": 1,
        }
    ])

    verifier = PaymentVerifier(mock_binance, payment_repo, checkout_repo)

    # 1. User 1 successfully pays with txid
    res1, msg1, meta1 = await verifier.verify_txid(checkout1, txid, user1.telegram_id)
    assert res1 == VerificationResult.PAID

    # 2. User 2 attempts to use the SAME txid on a different checkout
    res2, msg2, meta2 = await verifier.verify_txid(checkout2, txid, user2.telegram_id)
    assert res2 == VerificationResult.ALREADY_USED_TXID
    assert "already been used" in msg2
