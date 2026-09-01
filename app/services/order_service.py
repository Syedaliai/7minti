from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.db.models import Checkout, Order, OrderStatus, CheckoutStatus
from app.db.repositories import OrderRepository, CheckoutRepository
from app.services.encryption import EncryptionService
from app.services.pricing import PricingService
from app.services.prodseller import (
    ProdSellerService,
    ProdSellerAPIError,
    ProdSellerAuthError,
    ProdSellerBalanceError,
    ProdSellerOutOfStockError,
    ProdSellerRateLimitError,
)
from app.utils.ids import generate_idempotency_key
from app.utils.money import to_decimal

logger = logging.getLogger(__name__)


class OrderService:
    """Coordinates supplier fulfillment, idempotency protection, and credential delivery."""

    def __init__(
        self,
        order_repo: OrderRepository,
        checkout_repo: CheckoutRepository,
        prodseller_service: ProdSellerService,
        encryption_service: EncryptionService,
    ):
        self.order_repo = order_repo
        self.checkout_repo = checkout_repo
        self.prodseller_service = prodseller_service
        self.encryption_service = encryption_service

    async def fulfill_order(
        self,
        checkout: Checkout,
        telegram_user_id: int,
    ) -> Tuple[OrderStatus, str, Optional[List[str]]]:
        """Fulfill a paid checkout by ordering from ProdSeller with Idempotency-Key safety."""
        # 1. Pre-fulfillment safety check: Fetch current live price and stock from ProdSeller
        try:
            current_prod = await self.prodseller_service.get_product(checkout.product_id)
        except Exception as ex:
            logger.warning("Could not pre-validate product before fulfillment: %s", ex)
            current_prod = None

        if current_prod:
            current_supplier_price = to_decimal(current_prod.get("price", "0"))
            # Prevent fulfilling at a loss if price increased
            if not PricingService.is_payment_sufficient_for_cost(
                customer_paid=checkout.expected_total,
                current_supplier_cost=current_supplier_price,
                quantity=checkout.quantity,
                commission=Decimal("0.00"),
            ):
                logger.error(
                    "Price increased after checkout! Paid: %s, Current supplier cost: %s for qty %d",
                    checkout.expected_total,
                    current_supplier_price,
                    checkout.quantity,
                )
                # Update checkout status to PRICE_CHANGED_REVIEW
                await self.checkout_repo.update_status(checkout.id, CheckoutStatus.PRICE_CHANGED_REVIEW)
                # Create order in PRICE_CHANGED_REVIEW state
                idempotency_key = generate_idempotency_key(telegram_user_id, checkout.id)
                await self.order_repo.create(
                    checkout_id=checkout.id,
                    user_id=checkout.user_id,
                    product_id=checkout.product_id,
                    idempotency_key=idempotency_key,
                    supplier_amount=current_supplier_price * Decimal(checkout.quantity),
                    customer_amount=checkout.expected_total,
                    commission_amount=PricingService.calculate_commission_total(checkout.quantity),
                    quantity=checkout.quantity,
                    status=OrderStatus.PRICE_CHANGED_REVIEW,
                )
                return (
                    OrderStatus.PRICE_CHANGED_REVIEW,
                    "⚠️ <b>Price Update Required</b>\n\n"
                    "The supplier updated this product's price while your payment was processing. "
                    f"Your payment has been received safely. Please contact support @{settings.SUPPORT_USERNAME} "
                    "to approve the order or request a resolution.",
                    None,
                )

        # 2. Check if an order already exists for this checkout
        existing_order = await self.order_repo.get_by_checkout_id(checkout.id)
        if existing_order:
            if existing_order.status == OrderStatus.DELIVERED.value:
                # Decrypt already delivered credentials
                credentials = []
                if existing_order.delivered_data_encrypted:
                    decrypted = self.encryption_service.decrypt(existing_order.delivered_data_encrypted)
                    if isinstance(decrypted, list):
                        credentials = decrypted
                    elif isinstance(decrypted, str):
                        credentials = [decrypted]
                return OrderStatus.DELIVERED, "Order was already delivered successfully.", credentials
            idempotency_key = existing_order.idempotency_key
            order = existing_order
        else:
            # 3. Generate and persist Idempotency-Key BEFORE calling supplier API
            idempotency_key = generate_idempotency_key(telegram_user_id, checkout.id)
            supplier_amount = checkout.supplier_price_at_quote * Decimal(checkout.quantity)
            customer_amount = checkout.expected_total
            commission_amount = PricingService.calculate_commission_total(checkout.quantity)

            order = await self.order_repo.create(
                checkout_id=checkout.id,
                user_id=checkout.user_id,
                product_id=checkout.product_id,
                idempotency_key=idempotency_key,
                supplier_amount=supplier_amount,
                customer_amount=customer_amount,
                commission_amount=commission_amount,
                quantity=checkout.quantity,
                status=OrderStatus.PROCESSING,
            )

        # 4. Call ProdSeller POST /orders with Idempotency-Key
        try:
            logger.info(
                "Calling ProdSeller POST /orders for order %s (idempotency_key=%s)",
                order.id,
                idempotency_key,
            )
            response = await self.prodseller_service.create_order(
                product_id=checkout.product_id,
                quantity=checkout.quantity,
                idempotency_key=idempotency_key,
            )

            # Extract keys/accounts
            delivered_keys: List[str] = []
            if "deliveredKey" in response and response["deliveredKey"]:
                delivered_keys.append(str(response["deliveredKey"]))
            if "deliveredKeys" in response and isinstance(response["deliveredKeys"], list):
                delivered_keys.extend([str(k) for k in response["deliveredKeys"] if str(k) not in delivered_keys])

            supplier_order_id = response.get("orderId")
            delivery_type = response.get("delivery", {}).get("type") if isinstance(response.get("delivery"), dict) else "instant"

            # Encrypt credentials at rest
            encrypted_payload = self.encryption_service.encrypt(delivered_keys) if delivered_keys else None

            # Mark order as DELIVERED in database
            await self.order_repo.update_delivery(
                order_id=order.id,
                supplier_order_id=supplier_order_id,
                status=OrderStatus.DELIVERED,
                encrypted_credentials=encrypted_payload,
            )

            logger.info("Order %s fulfilled successfully (supplier_order_id=%s)", order.id, supplier_order_id)
            return OrderStatus.DELIVERED, "Order fulfilled successfully.", delivered_keys

        except ProdSellerOutOfStockError as err:
            logger.error("Supplier out of stock for order %s: %s", order.id, err)
            await self.order_repo.update_delivery(order.id, None, OrderStatus.OUT_OF_STOCK)
            return (
                OrderStatus.OUT_OF_STOCK,
                "⚠️ This product is temporarily out of stock on the supplier network. Your payment is secure. Our team has been notified.",
                None,
            )

        except ProdSellerBalanceError as err:
            logger.critical("Supplier balance insufficient for order %s: %s", order.id, err)
            await self.order_repo.update_delivery(order.id, None, OrderStatus.SUPPLIER_BALANCE_LOW)
            return (
                OrderStatus.SUPPLIER_BALANCE_LOW,
                "⚠️ Supplier inventory fulfillment delay. Our administration team is automatically topping up balance.",
                None,
            )

        except Exception as ex:
            logger.error("Error creating supplier order %s: %s", order.id, ex)
            await self.order_repo.update_delivery(order.id, None, OrderStatus.SUPPLIER_FAILED)
            return (
                OrderStatus.SUPPLIER_FAILED,
                f"⚠️ Error fulfilling order with supplier. Please contact support @{settings.SUPPORT_USERNAME} with your order ID <code>{order.id}</code>.",
                None,
            )
