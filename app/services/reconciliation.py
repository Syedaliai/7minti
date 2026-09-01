import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from app.db.models import OrderStatus, CheckoutStatus
from app.db.repositories import OrderRepository, CheckoutRepository, AuditRepository
from app.services.encryption import EncryptionService
from app.services.prodseller import ProdSellerService

logger = logging.getLogger(__name__)


class ReconciliationService:
    """Crash recovery and reconciliation routines for orders stuck in PROCESSING or VERIFYING."""

    def __init__(
        self,
        order_repo: OrderRepository,
        checkout_repo: CheckoutRepository,
        audit_repo: AuditRepository,
        prodseller_service: ProdSellerService,
        encryption_service: EncryptionService,
    ):
        self.order_repo = order_repo
        self.checkout_repo = checkout_repo
        self.audit_repo = audit_repo
        self.prodseller_service = prodseller_service
        self.encryption_service = encryption_service

    async def reconcile_stuck_orders(self) -> None:
        """Scan and reconcile orders left in PROCESSING state upon startup or periodic check."""
        try:
            stuck_orders = await self.order_repo.get_stuck_processing()
            if not stuck_orders:
                return

            logger.info("Found %d orders stuck in PROCESSING state. Starting reconciliation...", len(stuck_orders))

            for order in stuck_orders:
                logger.info("Reconciling order %s (idempotency_key=%s)", order.id, order.idempotency_key)

                # If we have a supplier_order_id, query ProdSeller GET /orders/:id directly
                if order.supplier_order_id:
                    try:
                        supp_order = await self.prodseller_service.get_order(order.supplier_order_id)
                        if supp_order.get("status") == "delivered":
                            keys = []
                            if supp_order.get("deliveredKey"):
                                keys.append(supp_order["deliveredKey"])
                            if supp_order.get("deliveredKeys"):
                                keys.extend(supp_order["deliveredKeys"])

                            encrypted = self.encryption_service.encrypt(keys) if keys else None
                            await self.order_repo.update_delivery(
                                order.id,
                                order.supplier_order_id,
                                OrderStatus.DELIVERED,
                                encrypted,
                            )
                            logger.info("Reconciliation delivered order %s via supplier status check", order.id)
                            continue
                    except Exception as ex:
                        logger.warning("Error fetching supplier order %s: %s", order.supplier_order_id, ex)

                # If request result was uncertain or interrupted, retry with the EXACT SAME Idempotency-Key
                try:
                    res = await self.prodseller_service.create_order(
                        product_id=order.product_id,
                        quantity=order.quantity,
                        idempotency_key=order.idempotency_key,
                    )
                    keys = []
                    if res.get("deliveredKey"):
                        keys.append(res["deliveredKey"])
                    if res.get("deliveredKeys"):
                        keys.extend(res["deliveredKeys"])

                    encrypted = self.encryption_service.encrypt(keys) if keys else None
                    supp_id = res.get("orderId")
                    await self.order_repo.update_delivery(
                        order.id,
                        supp_id,
                        OrderStatus.DELIVERED,
                        encrypted,
                    )
                    logger.info("Reconciliation resolved order %s using same idempotency key", order.id)
                except Exception as ex:
                    logger.error("Failed to reconcile order %s: %s", order.id, ex)

        except Exception as ex:
            logger.error("Error during stuck orders reconciliation: %s", ex)

    async def reconcile_stuck_checkouts(self, timeout_minutes: int = 15) -> None:
        """Reset checkouts stuck in VERIFYING state for more than timeout_minutes back to AWAITING_PAYMENT."""
        try:
            threshold = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
            stuck_checkouts = await self.checkout_repo.get_stuck_verifying(older_than=threshold)
            for c in stuck_checkouts:
                logger.info("Resetting stuck checkout %s from VERIFYING back to AWAITING_PAYMENT", c.id)
                await self.checkout_repo.update_status(c.id, CheckoutStatus.AWAITING_PAYMENT)
        except Exception as ex:
            logger.error("Error during stuck checkouts reconciliation: %s", ex)

    async def reconcile_stuck_verifications(self, session) -> None:
        """Scan and auto-refund any orphaned or expired active verification sessions across WhatsApp and SMS."""
        from sqlalchemy import select, update as sa_update
        from app.db.models import WhatsAppOrder, WhatsAppOrderStatus, SmsOrder, SmsOrderStatus
        from app.db.repositories import UserRepository
        from app.services.grizzlysms import GrizzlySMSService
        from app.services.smspool import SMSPoolService

        now = datetime.now(timezone.utc)
        user_repo = UserRepository(session)

        # 1. Reconcile WhatsApp orders
        try:
            res = await session.execute(
                select(WhatsAppOrder).where(WhatsAppOrder.status == WhatsAppOrderStatus.ACTIVE.value)
            )
            active_wa = res.scalars().all()
            for wa in active_wa:
                exp = wa.expires_at.replace(tzinfo=timezone.utc) if wa.expires_at.tzinfo is None else wa.expires_at
                if exp < now:
                    logger.info("Startup reconciliation: Auto-refunding expired WhatsApp order %s ($%s USDT)", wa.id, wa.charged_price)
                    await user_repo.refund_balance(wa.user_id, wa.charged_price)
                    await session.execute(
                        sa_update(WhatsAppOrder)
                        .where(WhatsAppOrder.id == wa.id)
                        .values(status=WhatsAppOrderStatus.REFUNDED.value)
                    )
                    # Cancel on supplier
                    try:
                        grizzly = GrizzlySMSService()
                        await grizzly.cancel_number(wa.grizzly_activation_id)
                        await grizzly.close()
                    except Exception:
                        pass
        except Exception as ex:
            logger.error("Error during WhatsApp orders reconciliation: %s", ex)

        # 2. Reconcile SMSPool orders
        try:
            res = await session.execute(
                select(SmsOrder).where(SmsOrder.status == SmsOrderStatus.ACTIVE.value)
            )
            active_sms = res.scalars().all()
            for sms in active_sms:
                exp = sms.expires_at.replace(tzinfo=timezone.utc) if sms.expires_at.tzinfo is None else sms.expires_at
                if exp < now:
                    logger.info("Startup reconciliation: Auto-refunding expired SMS order %s ($%s USDT)", sms.id, sms.charged_price)
                    await user_repo.refund_balance(sms.user_id, sms.charged_price)
                    await session.execute(
                        sa_update(SmsOrder)
                        .where(SmsOrder.id == sms.id)
                        .values(status=SmsOrderStatus.REFUNDED.value)
                    )
                    # Cancel on supplier
                    try:
                        smspool = SMSPoolService()
                        await smspool.cancel_order(sms.smspool_order_id)
                        await smspool.close()
                    except Exception:
                        pass
        except Exception as ex:
            logger.error("Error during SMS orders reconciliation: %s", ex)
