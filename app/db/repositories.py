from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
import json

from sqlalchemy import select, update, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    User,
    Checkout,
    Payment,
    Order,
    WhatsAppOrder,
    WhatsAppOrderStatus,
    SmsOrder,
    SmsOrderStatus,
    Coupon,
    CouponRedemption,
    CouponDiscountType,
    AdminAuditLog,
    CheckoutStatus,
    OrderStatus,
)
from app.utils.ids import generate_uuid_str


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        stmt = select(User).where(User.telegram_id == telegram_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def upsert_user(self, telegram_id: int, username: Optional[str], first_name: Optional[str]) -> User:
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.username = username
            user.first_name = first_name
            user.updated_at = datetime.now(timezone.utc)
        else:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
            )
            self.session.add(user)
        await self.session.flush()
        return user

    async def count_users(self) -> int:
        stmt = select(func.count(User.id))
        res = await self.session.execute(stmt)
        return res.scalar() or 0

    async def get_balance(self, telegram_id: int) -> Decimal:
        """Get user balance by telegram_id with fallback to 0.0."""
        user = await self.get_by_telegram_id(telegram_id)
        if user and user.balance is not None:
            return user.balance
        return Decimal("0.0")

    async def add_balance(self, user_id: int, amount: Decimal) -> Decimal:
        """Atomically credit user balance and return new balance.

        Uses SELECT FOR UPDATE to prevent race conditions under concurrent access.
        """
        stmt = select(User).where(User.id == user_id).with_for_update()
        res = await self.session.execute(stmt)
        user = res.scalar_one_or_none()
        if user:
            user.balance = (user.balance or Decimal("0.0")) + amount
            user.updated_at = datetime.now(timezone.utc)
            await self.session.flush()
            return user.balance
        return Decimal("0.0")

    async def deduct_balance(self, user_id: int, amount: Decimal) -> bool:
        """Atomically deduct balance if user has sufficient funds. Return True if deducted.

        Uses SELECT FOR UPDATE to prevent race conditions under concurrent access.
        """
        stmt = select(User).where(User.id == user_id).with_for_update()
        res = await self.session.execute(stmt)
        user = res.scalar_one_or_none()
        if user and (user.balance or Decimal("0.0")) >= amount:
            user.balance = user.balance - amount
            user.updated_at = datetime.now(timezone.utc)
            await self.session.flush()
            return True
        return False

    async def refund_balance(self, user_id: int, amount: Decimal) -> Decimal:
        """Atomically refund balance back to user on timeout or cancellation.

        Uses SELECT FOR UPDATE to prevent race conditions under concurrent access.
        """
        return await self.add_balance(user_id, amount)

    async def get_users_paginated(self, limit: int = 8, offset: int = 0) -> List[User]:
        """Fetch paginated list of users for CRM dashboard."""
        stmt = select(User).order_by(desc(User.created_at)).limit(limit).offset(offset)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_total_system_liability(self) -> Decimal:
        """Calculate total unspent wallet balance held by all users."""
        stmt = select(func.coalesce(func.sum(User.balance), 0))
        res = await self.session.execute(stmt)
        val = res.scalar()
        return Decimal(str(val or 0))

    async def toggle_block_user(self, user_id: int) -> bool:
        """Block or unblock a user by ID."""
        user = await self.session.get(User, user_id)
        if user:
            user.is_blocked = not user.is_blocked
            user.updated_at = datetime.now(timezone.utc)
            await self.session.flush()
            return user.is_blocked
        return False


class CheckoutRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: int,
        product_id: str,
        product_name: str,
        supplier_price: Decimal,
        commission: Decimal,
        customer_unit_price: Decimal,
        quantity: int,
        expected_total: Decimal,
        coin: str,
        network: str,
        payment_address: str,
        expires_at: datetime,
    ) -> Checkout:
        checkout = Checkout(
            id=generate_uuid_str(),
            user_id=user_id,
            product_id=product_id,
            product_name=product_name,
            supplier_price_at_quote=supplier_price,
            commission=commission,
            customer_unit_price=customer_unit_price,
            quantity=quantity,
            expected_total=expected_total,
            coin=coin,
            network=network,
            payment_address=payment_address,
            status=CheckoutStatus.AWAITING_PAYMENT.value,
            expires_at=expires_at,
        )
        self.session.add(checkout)
        await self.session.flush()
        return checkout

    async def get_by_id(self, checkout_id: str, for_update: bool = False) -> Optional[Checkout]:
        stmt = select(Checkout).where(Checkout.id == checkout_id)
        if for_update:
            stmt = stmt.with_for_update()
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_user_checkout(self, checkout_id: str, user_id: int) -> Optional[Checkout]:
        stmt = select(Checkout).where(Checkout.id == checkout_id, Checkout.user_id == user_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def update_status(self, checkout_id: str, status: CheckoutStatus, *, expected_status: Optional[CheckoutStatus] = None) -> bool:
        """Update checkout status with optional expected current status check.

        Uses SELECT FOR UPDATE + conditional UPDATE to prevent race conditions. Returns True if row was updated.
        """
        # First, lock the row with SELECT FOR UPDATE
        stmt_lock = select(Checkout).where(Checkout.id == checkout_id).with_for_update()
        res_lock = await self.session.execute(stmt_lock)
        checkout = res_lock.scalar_one_or_none()

        if not checkout:
            return False

        if expected_status is not None and checkout.status != expected_status.value:
            return False

        # Now perform the update
        stmt = (
            update(Checkout)
            .where(Checkout.id == checkout_id)
            .values(status=status.value, updated_at=datetime.now(timezone.utc))
        )

        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.rowcount > 0

    async def get_stuck_verifying(self, older_than: datetime) -> List[Checkout]:
        stmt = select(Checkout).where(
            Checkout.status == CheckoutStatus.VERIFYING.value,
            Checkout.updated_at <= older_than,
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())


class PaymentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_txid(self, txid: str) -> Optional[Payment]:
        stmt = select(Payment).where(Payment.txid == txid.strip())
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_checkout_id(self, checkout_id: str) -> Optional[Payment]:
        stmt = select(Payment).where(Payment.checkout_id == checkout_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def create(
        self,
        checkout_id: str,
        user_id: int,
        txid: str,
        coin: str,
        network: str,
        expected_amount: Decimal,
        received_amount: Optional[Decimal],
        status: str,
        binance_status: Optional[str] = None,
        confirmations: Optional[int] = None,
        verified_at: Optional[datetime] = None,
        raw_metadata: Optional[dict] = None,
    ) -> Payment:
        payment = Payment(
            id=generate_uuid_str(),
            checkout_id=checkout_id,
            user_id=user_id,
            txid=txid.strip(),
            coin=coin,
            network=network,
            expected_amount=expected_amount,
            received_amount=received_amount,
            status=status,
            binance_status=binance_status,
            confirmations=confirmations,
            verified_at=verified_at,
            raw_reference_metadata=json.dumps(raw_metadata) if raw_metadata else None,
        )
        self.session.add(payment)
        await self.session.flush()
        return payment

    async def count_pending_payments(self) -> int:
        stmt = select(func.count(Payment.id)).where(
            Payment.status.in_([CheckoutStatus.VERIFYING.value, CheckoutStatus.PAYMENT_REVIEW.value, CheckoutStatus.OVERPAID_REVIEW.value])
        )
        res = await self.session.execute(stmt)
        return res.scalar() or 0


class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        checkout_id: str,
        user_id: int,
        product_id: str,
        idempotency_key: str,
        supplier_amount: Decimal,
        customer_amount: Decimal,
        commission_amount: Decimal,
        quantity: int,
        status: OrderStatus = OrderStatus.PROCESSING,
        delivery_type: Optional[str] = None,
    ) -> Order:
        order = Order(
            id=generate_uuid_str(),
            checkout_id=checkout_id,
            user_id=user_id,
            product_id=product_id,
            idempotency_key=idempotency_key,
            supplier_amount=supplier_amount,
            customer_amount=customer_amount,
            commission_amount=commission_amount,
            quantity=quantity,
            status=status.value,
            delivery_type=delivery_type,
        )
        self.session.add(order)
        await self.session.flush()
        return order

    async def get_by_id(self, order_id: str) -> Optional[Order]:
        stmt = select(Order).options(selectinload(Order.checkout)).where(Order.id == order_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_checkout_id(self, checkout_id: str) -> Optional[Order]:
        stmt = select(Order).options(selectinload(Order.checkout)).where(Order.checkout_id == checkout_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_idempotency_key(self, idempotency_key: str) -> Optional[Order]:
        stmt = select(Order).where(Order.idempotency_key == idempotency_key)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_user_orders(self, user_id: int, limit: int = 10, offset: int = 0) -> List[Order]:
        stmt = (
            select(Order)
            .options(selectinload(Order.checkout))
            .where(Order.user_id == user_id)
            .order_by(desc(Order.created_at))
            .limit(limit)
            .offset(offset)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def count_user_orders(self, user_id: int) -> int:
        stmt = select(func.count(Order.id)).where(Order.user_id == user_id)
        res = await self.session.execute(stmt)
        return res.scalar() or 0

    async def get_stuck_processing(self) -> List[Order]:
        stmt = select(Order).options(selectinload(Order.checkout)).where(
            Order.status == OrderStatus.PROCESSING.value
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def update_delivery(
        self,
        order_id: str,
        supplier_order_id: Optional[str],
        status: OrderStatus,
        encrypted_credentials: Optional[bytes] = None,
    ) -> None:
        values = {
            "status": status.value,
        }
        if supplier_order_id is not None:
            values["supplier_order_id"] = supplier_order_id
        if encrypted_credentials is not None:
            values["delivered_data_encrypted"] = encrypted_credentials
        if status == OrderStatus.DELIVERED:
            values["delivered_at"] = datetime.now(timezone.utc)

        stmt = update(Order).where(Order.id == order_id).values(**values)
        await self.session.execute(stmt)
        await self.session.flush()

    async def get_statistics_today(self, start_of_day: datetime) -> dict:
        stmt_orders = select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.customer_amount), 0),
            func.coalesce(func.sum(Order.commission_amount), 0),
        ).where(Order.created_at >= start_of_day, Order.status == OrderStatus.DELIVERED.value)

        res = await self.session.execute(stmt_orders)
        order_count, revenue, commission = res.one()

        stmt_failed = select(func.count(Order.id)).where(
            Order.status.in_([
                OrderStatus.SUPPLIER_FAILED.value,
                OrderStatus.PRICE_CHANGED_REVIEW.value,
                OrderStatus.MANUAL_REVIEW.value,
                OrderStatus.OUT_OF_STOCK.value,
            ])
        )
        res_failed = await self.session.execute(stmt_failed)
        failed_count = res_failed.scalar() or 0

        return {
            "delivered_today": order_count or 0,
            "revenue_today": Decimal(str(revenue or 0)),
            "commission_today": Decimal(str(commission or 0)),
            "failed_review_total": failed_count,
        }

    async def get_top_selling_products(self, limit: int = 5) -> List[dict]:
        """Rank products and services by total successful sales and profit."""
        stmt = (
            select(
                Checkout.product_name,
                func.count(Order.id).label("sales_count"),
                func.coalesce(func.sum(Order.customer_amount), 0).label("total_revenue"),
                func.coalesce(func.sum(Order.commission_amount), 0).label("total_profit"),
            )
            .join(Checkout, Order.checkout_id == Checkout.id)
            .where(Order.status == OrderStatus.DELIVERED.value)
            .group_by(Checkout.product_name)
            .order_by(desc("sales_count"))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        items = []
        for row in res.all():
            items.append({
                "name": row[0],
                "sales_count": row[1],
                "revenue": Decimal(str(row[2] or 0)),
                "profit": Decimal(str(row[3] or 0)),
            })
        return items

    async def get_recent_orders(self, limit: int = 8) -> List[Order]:
        """Fetch most recent orders across the platform."""
        stmt = (
            select(Order)
            .options(selectinload(Order.checkout))
            .order_by(desc(Order.created_at))
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_comprehensive_financial_metrics(self) -> dict:
        """Calculate complete financial health across ProdSeller, WhatsApp, and SMS verification."""
        now = datetime.now(timezone.utc)
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # 1. Digital products (Orders)
        stmt_lifetime = select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.customer_amount), 0),
            func.coalesce(func.sum(Order.commission_amount), 0),
        ).where(Order.status == OrderStatus.DELIVERED.value)
        res_life = (await self.session.execute(stmt_lifetime)).one()
        orders_life_count, orders_life_rev, orders_life_profit = res_life

        stmt_month = select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.customer_amount), 0),
            func.coalesce(func.sum(Order.commission_amount), 0),
        ).where(Order.status == OrderStatus.DELIVERED.value, Order.created_at >= start_of_month)
        res_m = (await self.session.execute(stmt_month)).one()
        orders_m_count, orders_m_rev, orders_m_profit = res_m

        stmt_day = select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.customer_amount), 0),
            func.coalesce(func.sum(Order.commission_amount), 0),
        ).where(Order.status == OrderStatus.DELIVERED.value, Order.created_at >= start_of_day)
        res_d = (await self.session.execute(stmt_day)).one()
        orders_d_count, orders_d_rev, orders_d_profit = res_d

        # 2. WhatsApp Verifications
        stmt_wa = select(
            func.count(WhatsAppOrder.id),
            func.coalesce(func.sum(WhatsAppOrder.charged_price), 0),
            func.coalesce(func.sum(WhatsAppOrder.charged_price - WhatsAppOrder.supplier_price), 0),
        ).where(WhatsAppOrder.status == WhatsAppOrderStatus.COMPLETED.value)
        res_wa = (await self.session.execute(stmt_wa)).one()
        wa_count, wa_rev, wa_profit = res_wa

        # 3. SMSPool Verifications
        stmt_sms = select(
            func.count(SmsOrder.id),
            func.coalesce(func.sum(SmsOrder.charged_price), 0),
            func.coalesce(func.sum(SmsOrder.charged_price - SmsOrder.supplier_price), 0),
        ).where(SmsOrder.status == SmsOrderStatus.COMPLETED.value)
        res_sms = (await self.session.execute(stmt_sms)).one()
        sms_count, sms_rev, sms_profit = res_sms

        total_rev = Decimal(str(orders_life_rev)) + Decimal(str(wa_rev)) + Decimal(str(sms_rev))
        total_profit = Decimal(str(orders_life_profit)) + Decimal(str(wa_profit)) + Decimal(str(sms_profit))
        total_orders = orders_life_count + wa_count + sms_count

        return {
            "total_revenue": total_rev,
            "total_profit": total_profit,
            "total_orders": total_orders,
            "month_revenue": Decimal(str(orders_m_rev)),
            "month_profit": Decimal(str(orders_m_profit)),
            "today_revenue": Decimal(str(orders_d_rev)),
            "today_profit": Decimal(str(orders_d_profit)),
            "digital_orders_count": orders_life_count,
            "digital_revenue": Decimal(str(orders_life_rev)),
            "digital_profit": Decimal(str(orders_life_profit)),
            "wa_count": wa_count,
            "wa_revenue": Decimal(str(wa_rev)),
            "wa_profit": Decimal(str(wa_profit)),
            "sms_count": sms_count,
            "sms_revenue": Decimal(str(sms_rev)),
            "sms_profit": Decimal(str(sms_profit)),
        }


class AuditRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def log_action(self, admin_telegram_id: int, action: str, details: Optional[str] = None) -> None:
        log = AdminAuditLog(
            admin_telegram_id=admin_telegram_id,
            action=action,
            details=details,
        )
        self.session.add(log)
        await self.session.flush()


class CouponRepository:
    """
    Manages promotional coupons and enforces strict service scoping.
    Valid services: 'all', 'whatsapp', 'openai', 'nvidia', 'youtube', 'products'
    """
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_coupon(
        self,
        code: str,
        service: str,
        discount_type: str,
        discount_value: Decimal,
        max_uses: int = 100,
    ) -> Coupon:
        """Create a new service-scoped coupon."""
        clean_code = code.strip().upper()
        coupon = Coupon(
            code=clean_code,
            service=service.strip().lower(),
            discount_type=discount_type,
            discount_value=discount_value,
            max_uses=max_uses,
            times_used=0,
            is_active=True,
        )
        self.session.add(coupon)
        await self.session.flush()
        return coupon

    async def get_by_code(self, code: str) -> Optional[Coupon]:
        """Fetch coupon by case-insensitive code."""
        clean_code = code.strip().upper()
        stmt = select(Coupon).where(Coupon.code == clean_code)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_all(self, limit: int = 20) -> List[Coupon]:
        """List recent coupons for admin dashboard."""
        stmt = select(Coupon).order_by(desc(Coupon.created_at)).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def delete_coupon(self, coupon_id: str) -> bool:
        """Delete coupon by ID."""
        c = await self.session.get(Coupon, coupon_id)
        if c:
            await self.session.delete(c)
            await self.session.flush()
            return True
        return False

    async def validate_and_calculate_discount(
        self,
        code: str,
        service: str,
        user_id: int,
        original_price: Decimal,
    ) -> tuple[bool, Decimal, Decimal, Optional[Coupon], str]:
        """
        Validate coupon against strict service scoping, user redemption history, and usage limits.
        Returns: (is_valid, final_discounted_price, discount_amount, coupon_obj, error_message)
        """
        coupon = await self.get_by_code(code)
        if not coupon:
            return False, original_price, Decimal("0.0"), None, "❌ Invalid coupon code."

        if not coupon.is_active:
            return False, original_price, Decimal("0.0"), coupon, "❌ This coupon is currently inactive."

        if coupon.times_used >= coupon.max_uses:
            return False, original_price, Decimal("0.0"), coupon, "❌ This coupon has reached its maximum usage limit."

        # STRICT SERVICE-SCOPING CHECK:
        # e.g., if coupon.service is "whatsapp", it CANNOT be used on "openai" or "youtube".
        req_service = service.strip().lower()
        allowed_service = coupon.service.strip().lower()
        if allowed_service != "all" and allowed_service != req_service:
            service_names = {
                "whatsapp": "WhatsApp Verification",
                "openai": "OpenAI / Codex Verification",
                "nvidia": "Nvidia Verification",
                "youtube": "YouTube Verification",
                "products": "Digital Store Products",
            }
            target_name = service_names.get(allowed_service, allowed_service.capitalize())
            return (
                False,
                original_price,
                Decimal("0.0"),
                coupon,
                f"🚫 <b>Service Restricted:</b> This coupon code is strictly for <b>{target_name}</b> only!",
            )

        # Check if user already used this coupon
        stmt_redemp = select(CouponRedemption).where(
            CouponRedemption.coupon_id == coupon.id,
            CouponRedemption.user_id == user_id,
        )
        res_redemp = await self.session.execute(stmt_redemp)
        if res_redemp.scalar_one_or_none():
            return False, original_price, Decimal("0.0"), coupon, "⚠️ You have already redeemed this coupon code."

        # Calculate discount amount
        if coupon.discount_type == CouponDiscountType.PERCENTAGE.value:
            discount = (original_price * (coupon.discount_value / Decimal("100.0"))).quantize(Decimal("0.0001"))
        else:  # FIXED
            discount = coupon.discount_value.quantize(Decimal("0.0001"))

        # Ensure discount does not exceed original price, minimum final price $0.0001
        discount = min(discount, original_price - Decimal("0.0001")) if original_price > Decimal("0.0001") else Decimal("0.0")
        final_price = max(Decimal("0.0001"), original_price - discount).quantize(Decimal("0.0001"))

        return True, final_price, discount, coupon, ""

    async def redeem_coupon(
        self,
        coupon_id: str,
        user_id: int,
        telegram_id: int,
        discount_applied: Decimal,
        service: str,
    ) -> None:
        """Mark coupon as used by user and increment global usage count."""
        coupon = await self.session.get(Coupon, coupon_id)
        if coupon:
            coupon.times_used += 1

        redemption = CouponRedemption(
            coupon_id=coupon_id,
            user_id=user_id,
            telegram_id=telegram_id,
            discount_applied=discount_applied,
            service=service,
        )
        self.session.add(redemption)
        await self.session.flush()
