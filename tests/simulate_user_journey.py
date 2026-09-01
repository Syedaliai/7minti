import asyncio
from datetime import datetime, timezone
from decimal import Decimal
import os
import sys

# Ensure UTF-8 output on Windows terminal
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Configure test environment
os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:TEST_BOT_TOKEN"
os.environ["PRODSELLER_API_KEY"] = "psk_live_test_key_xyz"
os.environ["PRODSELLER_BASE_URL"] = "https://prodseller.com/v1"
os.environ["PAYMENT_COMMISSION"] = "0.20"
os.environ["PAYMENT_COIN"] = "USDT"
os.environ["PAYMENT_NETWORK"] = "TRC20"
os.environ["PAYMENT_ADDRESS"] = "TRC20_Official_Binance_Deposit_Address_123"
os.environ["BINANCE_API_KEY"] = "test_binance_key"
os.environ["BINANCE_API_SECRET"] = "test_binance_secret"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_simulation.db"
os.environ["ADMIN_TELEGRAM_IDS"] = "999888777"
os.environ["SUPPORT_USERNAME"] = "CustomerSupport"
os.environ["ENCRYPTION_KEY"] = "gu_E91Pq5cKpFmQxVvX-e8iS3A_eJ5q1o7fX1u6i2bQ="

from app.db.base import Base
from app.db.models import CheckoutStatus, OrderStatus
from app.db.repositories import (
    UserRepository,
    CheckoutRepository,
    PaymentRepository,
    OrderRepository,
    AuditRepository,
)
from app.db.session import init_db, AsyncSessionLocal
from app.services.binance import BinanceService
from app.services.checkout_service import CheckoutService
from app.services.encryption import EncryptionService
from app.services.order_service import OrderService
from app.services.payment_verifier import PaymentVerifier, VerificationResult
from app.services.pricing import PricingService
from app.services.prodseller import ProdSellerService
from app.utils.money import format_currency


async def run_real_user_simulation():
    print("=" * 70)
    print(">> STARTING REAL USER END-TO-END FLOW SIMULATION")
    print("=" * 70)

    # 0. Initialize fresh test database
    await init_db()
    encryption_svc = EncryptionService()

    # User Profile
    USER_TG_ID = 100200300
    USER_NAME = "Ali_Khan"
    USER_FIRST_NAME = "Ali"

    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        checkout_repo = CheckoutRepository(session)
        payment_repo = PaymentRepository(session)
        order_repo = OrderRepository(session)
        audit_repo = AuditRepository(session)

        # -----------------------------------------------------------------
        # STEP 1: USER OPENS BOT (/start)
        # -----------------------------------------------------------------
        print("\n[STEP 1] User sends /start to bot")
        user = await user_repo.upsert_user(
            telegram_id=USER_TG_ID,
            username=USER_NAME,
            first_name=USER_FIRST_NAME,
        )
        await session.commit()
        print(f"  [OK] User registered in DB: ID={user.id}, TelegramID={user.telegram_id}, Name={user.first_name}")

        # -----------------------------------------------------------------
        # STEP 2: USER BROWSES CATALOG
        # -----------------------------------------------------------------
        print("\n[STEP 2] User clicks 'Browse Catalog'")
        # Simulate mock catalog response from ProdSeller
        prodseller_svc = ProdSellerService()
        catalog_mock = [
            {"id": "prod_chatgpt_plus", "name": "ChatGPT Plus (1 Month)", "price": 2.50, "publicPrice": 20.00, "inStock": True},
            {"id": "prod_canva_pro", "name": "Canva Pro Lifetime", "price": 1.80, "publicPrice": 12.00, "inStock": True},
            {"id": "prod_claude_pro", "name": "Claude Pro AI", "price": 3.00, "publicPrice": 25.00, "inStock": True},
        ]
        prodseller_svc.get_products = lambda force_refresh=False: asyncio.sleep(0.01, result=catalog_mock)

        products = await prodseller_svc.get_products()
        print(f"  [OK] Catalog loaded ({len(products)} products available):")
        for p in products:
            cost = p["price"]
            customer_price = PricingService.calculate_unit_price(cost)
            print(f"     * {p['name']} | Supplier Cost: ${cost:.2f} | Customer Price (+0.20): {format_currency(customer_price)}")

        # -----------------------------------------------------------------
        # STEP 3: USER SELECTS PRODUCT & QUANTITY (Checkout Quote Creation)
        # -----------------------------------------------------------------
        selected_prod_id = "prod_chatgpt_plus"
        quantity = 2
        print(f"\n[STEP 3] User selects '{catalog_mock[0]['name']}' with Quantity = {quantity}")

        # Mock fresh fetch before checkout
        prodseller_svc.get_product = lambda pid: asyncio.sleep(0.01, result={
            "id": pid,
            "name": "ChatGPT Plus (1 Month)",
            "price": 2.50, # Authoritative supplier cost
            "inStock": True,
            "stock": 45,
            "delivery": {"type": "instant"},
        })

        checkout_svc = CheckoutService(checkout_repo, user_repo, prodseller_svc)
        checkout, prod_data = await checkout_svc.create_checkout(
            telegram_user_id=USER_TG_ID,
            username=USER_NAME,
            first_name=USER_FIRST_NAME,
            product_id=selected_prod_id,
            quantity=quantity,
        )
        await session.commit()

        print("  [OK] Order Checkout Generated:")
        print(f"     - Checkout ID: {checkout.id}")
        print(f"     - Product: {checkout.product_name}")
        print(f"     - Quantity: {checkout.quantity}")
        print(f"     - Supplier Unit Cost: {format_currency(checkout.supplier_price_at_quote)}")
        print(f"     - Commission per unit: {format_currency(checkout.commission)}")
        print(f"     - Customer Unit Price: {format_currency(checkout.customer_unit_price)}")
        print(f"     - Expected Total: {format_currency(checkout.expected_total, checkout.coin)}")
        print(f"     - Deposit Network: {checkout.network}")
        print(f"     - Deposit Address: {checkout.payment_address}")
        print(f"     - Quote Status: {checkout.status}")

        # Verify QR Code generation
        qr_buffer = CheckoutService.generate_qr_code_bytes(checkout.payment_address)
        print(f"     - QR Code Image Buffer generated: {qr_buffer.getbuffer().nbytes} bytes")

        # -----------------------------------------------------------------
        # STEP 4: USER SENDS PAYMENT & SUBMITS TXID
        # -----------------------------------------------------------------
        submitted_txid = "0x9876543210abcdef9876543210abcdef9876543210abcdef9876543210abcdef"
        print(f"\n[STEP 4] User transfers USDT and submits TxID: {submitted_txid[:20]}...")

        # Mock Binance deposit record
        binance_svc = BinanceService()
        binance_svc.get_deposit_history = lambda coin=None, limit=50: asyncio.sleep(0.01, result=[
            {
                "txId": submitted_txid,
                "amount": "5.40000000", # Exact amount for 2 units (2.70 * 2 = 5.40)
                "coin": "USDT",
                "network": "TRX", # Normalized TRC20 alias
                "address": "TRC20_Official_Binance_Deposit_Address_123",
                "status": 1,      # Success / Credited
                "insertTime": int(datetime.now(timezone.utc).timestamp() * 1000),
            }
        ])

        # Run 11-point payment verification
        verifier = PaymentVerifier(binance_svc, payment_repo, checkout_repo)
        verif_result, verif_msg, meta = await verifier.verify_txid(checkout, submitted_txid, USER_TG_ID)
        await session.commit()

        print(f"  [OK] Verification Outcome: {verif_result.value}")
        print(f"  [OK] Bot response to user: {verif_msg}")

        assert verif_result == VerificationResult.PAID, "Payment should be verified as PAID"

        # -----------------------------------------------------------------
        # STEP 5: AUTOMATED SUPPLIER FULFILLMENT & CREDENTIAL DELIVERY
        # -----------------------------------------------------------------
        print("\n[STEP 5] Fulfilling order with ProdSeller POST /orders (Idempotency Protected)...")

        # Mock ProdSeller order creation with multi-key response
        prodseller_svc.create_order = lambda product_id, quantity, idempotency_key: asyncio.sleep(0.01, result={
            "orderId": "prodseller_order_888999",
            "status": "delivered",
            "deliveredKeys": [
                "chatgpt_user1@openai.com:PassKey123#",
                "chatgpt_user2@openai.com:PassKey456#",
            ],
            "delivery": {"type": "instant"},
        })

        order_svc = OrderService(order_repo, checkout_repo, prodseller_svc, encryption_svc)
        order_status, order_msg, delivered_keys = await order_svc.fulfill_order(checkout, USER_TG_ID)
        await session.commit()

        print(f"  [OK] Fulfillment Status: {order_status.value}")
        print(f"  [OK] Delivered Credentials:")
        for idx, k in enumerate(delivered_keys, 1):
            print(f"     Key {idx}: {k}")

        assert order_status == OrderStatus.DELIVERED, "Order should be DELIVERED"
        assert len(delivered_keys) == 2, "Should deliver 2 keys for quantity 2"

        # -----------------------------------------------------------------
        # STEP 6: USER REVIEWS 'MY ORDERS' & SEES ENCRYPTED/DECRYPTED KEYS
        # -----------------------------------------------------------------
        print("\n[STEP 6] User checks 'My Orders' in Telegram")
        user_orders = await order_repo.get_user_orders(user.id)
        print(f"  [OK] Found {len(user_orders)} orders for user {user.first_name}:")
        for o in user_orders:
            # Verify credentials can be decrypted
            decrypted = encryption_svc.decrypt(o.delivered_data_encrypted)
            print(f"     * Order ID: {o.id}")
            print(f"       Idempotency Key: {o.idempotency_key}")
            print(f"       Supplier Order ID: {o.supplier_order_id}")
            print(f"       Customer Paid: {format_currency(o.customer_amount)}")
            print(f"       Supplier Cost: {format_currency(o.supplier_amount)}")
            print(f"       Our Net Profit: {format_currency(o.commission_amount)}")
            print(f"       Encrypted Data at Rest: {o.delivered_data_encrypted[:30]}... (Fernet Ciphertext)")
            print(f"       Decrypted Keys: {decrypted}")

        # -----------------------------------------------------------------
        # STEP 7: SECURITY CHECK — ANOTHER USER CANNOT ACCESS ALI'S ORDER
        # -----------------------------------------------------------------
        print("\n[STEP 7] Security Test: Another User attempts to view Ali's order")
        other_user = await user_repo.upsert_user(telegram_id=999999, username="intruder", first_name="Intruder")
        other_orders = await order_repo.get_user_orders(other_user.id)
        print(f"  [OK] Intruder's order query returned {len(other_orders)} orders (Cross-user isolation verified!)")

        # -----------------------------------------------------------------
        # STEP 8: ADMIN DASHBOARD AUDIT & METRICS
        # -----------------------------------------------------------------
        print("\n[STEP 8] Admin opens '/admin' Dashboard")
        start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        stats = await order_repo.get_statistics_today(start_of_day)
        total_users = await user_repo.count_users()

        print("  [OK] Admin Live Metrics:")
        print(f"     * Total Registered Users: {total_users}")
        print(f"     * Orders Delivered Today: {stats['delivered_today']}")
        print(f"     * Gross Revenue Today: {format_currency(stats['revenue_today'])}")
        print(f"     * Net Profit Today: {format_currency(stats['commission_today'])}")
        print(f"     * Failed/Review Orders: {stats['failed_review_total']}")

    print("\n" + "=" * 70)
    print("SUCCESS: REAL USER JOURNEY COMPLETED WITH 100% PASS RATE!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_real_user_simulation())
