import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
import qrcode

from app.config import settings
from app.db.models import Checkout, CheckoutStatus
from app.db.repositories import CheckoutRepository, UserRepository
from app.services.pricing import PricingService
from app.services.prodseller import ProdSellerService, ProdSellerAPIError
from app.utils.money import to_decimal

logger = logging.getLogger(__name__)


class CheckoutService:
    """Manages checkout creation, quote calculation, and lifecycle."""

    def __init__(
        self,
        checkout_repo: CheckoutRepository,
        user_repo: UserRepository,
        prodseller_service: ProdSellerService,
    ):
        self.checkout_repo = checkout_repo
        self.user_repo = user_repo
        self.prodseller_service = prodseller_service

    async def create_checkout(
        self,
        telegram_user_id: int,
        username: Optional[str],
        first_name: Optional[str],
        product_id: str,
        quantity: int = 1,
    ) -> Tuple[Checkout, dict]:
        """Fetch fresh supplier price, calculate authoritative customer quote, and create pending checkout."""
        if quantity <= 0:
            raise ValueError("Quantity must be at least 1")

        # 1. Fetch fresh authoritative product details directly from ProdSeller (never relying on cache)
        product_data = await self.prodseller_service.get_product(product_id)
        if not product_data:
            raise ProdSellerAPIError("Product could not be retrieved from supplier.")

        # Extract authoritative supplier price
        supplier_unit_price = to_decimal(product_data.get("price", "0"))
        if supplier_unit_price <= 0:
            raise ValueError("Invalid supplier price returned.")

        product_name = product_data.get("name", "Digital Product")

        # Check stock availability
        in_stock = product_data.get("inStock", True)
        stock_count = product_data.get("stock")
        if in_stock is False or (stock_count is not None and stock_count < quantity):
            raise ProdSellerAPIError(f"Insufficient stock for '{product_name}'. Available: {stock_count or 0}")

        # 2. Calculate customer pricing via single source of truth
        customer_unit_price = PricingService.calculate_unit_price(supplier_unit_price)
        expected_total = PricingService.calculate_total(supplier_unit_price, quantity)
        commission = PricingService.get_commission()

        # 3. Ensure user exists in local database
        user = await self.user_repo.upsert_user(
            telegram_id=telegram_user_id,
            username=username,
            first_name=first_name,
        )

        # 4. Set expiration time
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.CHECKOUT_EXPIRY_MINUTES)

        # 5. Persist checkout quote in DB
        checkout = await self.checkout_repo.create(
            user_id=user.id,
            product_id=product_id,
            product_name=product_name,
            supplier_price=supplier_unit_price,
            commission=commission,
            customer_unit_price=customer_unit_price,
            quantity=quantity,
            expected_total=expected_total,
            coin=settings.PAYMENT_COIN,
            network=settings.PAYMENT_NETWORK,
            payment_address=settings.PAYMENT_ADDRESS,
            expires_at=expires_at,
        )

        logger.info(
            "Created checkout %s for user %s: product=%s, qty=%d, supplier_unit=%s, customer_total=%s",
            checkout.id,
            telegram_user_id,
            product_name,
            quantity,
            supplier_unit_price,
            expected_total,
        )

        return checkout, product_data

    @staticmethod
    def generate_qr_code_bytes(address: str) -> io.BytesIO:
        """Generate a PNG QR code in-memory buffer for the cryptocurrency deposit address."""
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=8,
            border=2,
        )
        qr.add_data(address)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        bio = io.BytesIO()
        bio.name = "deposit_qr.png"
        img.save(bio, "PNG")
        bio.seek(0)
        return bio
