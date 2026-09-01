import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.bot.keyboards.checkout import get_payment_verifying_keyboard
from app.bot.middleware.rate_limit import verify_rate_limiter
from app.config import settings
from app.db.models import CheckoutStatus, OrderStatus
from app.db.repositories import CheckoutRepository, UserRepository, PaymentRepository, OrderRepository
from app.db.session import AsyncSessionLocal
from app.services.binance import BinanceService
from app.services.encryption import encryption_service
from app.services.order_service import OrderService
from app.services.payment_verifier import PaymentVerifier, VerificationResult
from app.services.prodseller import ProdSellerService
from app.utils.security import is_valid_txid
from app.utils.telegram import escape_html

logger = logging.getLogger(__name__)


async def paid_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'I Have Paid' button click — prompt customer for TxID."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 2:
        return
    checkout_id = parts[1]

    user = update.effective_user
    if not user:
        return

    # Check if checkout exists and belongs to user
    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        checkout_repo = CheckoutRepository(session)
        db_user = await user_repo.get_by_telegram_id(user.id)
        if not db_user:
            await query.answer("User session expired. Please restart /start.", show_alert=True)
            return

        checkout = await checkout_repo.get_user_checkout(checkout_id, db_user.id)
        if not checkout:
            await query.answer("Checkout record not found.", show_alert=True)
            return

    # Store checkout_id in user_data to route the next incoming text as TxID
    context.user_data["awaiting_txid_checkout_id"] = checkout_id

    prompt_text = (
        f"📝 <b>Submit Payment Verification</b>\n\n"
        f"Order: <code>{escape_html(checkout.product_name)}</code>\n"
        f"Amount: <code>{checkout.expected_total} {checkout.coin}</code>\n"
        f"Binance Pay UID: <code>{escape_html(settings.BINANCE_UID or '1254494426')}</code>\n\n"
        f"Please paste and send your <b>Binance Pay Order ID</b> or blockchain <b>TxID</b>:\n"
        f"<i>(Example: 28941829482910 or blockchain hash)</i>"
    )

    cancel_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Verification", callback_data="nav:home")]
    ])

    await query.message.reply_text(
        prompt_text,
        parse_mode="HTML",
        reply_markup=cancel_markup,
    )


async def txid_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process incoming TxID submission from user, verify against Binance, and trigger instant fulfillment."""
    checkout_id = context.user_data.get("awaiting_txid_checkout_id")
    if not checkout_id:
        return

    user = update.effective_user
    if not user:
        return

    txid = update.message.text.strip()

    # Rate limiting for verification attempts
    if not verify_rate_limiter.is_allowed(user.id):
        await update.message.reply_text(
            "⏳ <b>Verification rate limit reached.</b> Please wait a few seconds before trying again.",
            parse_mode="HTML",
        )
        return

    # Format sanity check
    if not is_valid_txid(txid):
        await update.message.reply_text(
            "❌ <b>Invalid TxID format.</b>\n"
            "Please provide a valid cryptocurrency transaction hash (alphanumeric, 16-128 characters).",
            parse_mode="HTML",
        )
        return

    # Clear awaiting state
    context.user_data.pop("awaiting_txid_checkout_id", None)

    verifying_msg = await update.message.reply_text(
        f"🔍 <b>Verifying Deposit on Binance...</b>\n"
        f"TxID: <code>{escape_html(txid)}</code>\n\n"
        f"<i>Please wait while we confirm your transaction...</i>",
        parse_mode="HTML",
    )

    binance_service: BinanceService = context.bot_data["binance_service"]
    prodseller_service: ProdSellerService = context.bot_data["prodseller_service"]

    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        checkout_repo = CheckoutRepository(session)
        payment_repo = PaymentRepository(session)
        order_repo = OrderRepository(session)

        db_user = await user_repo.get_by_telegram_id(user.id)
        if not db_user:
            await verifying_msg.edit_text("User session not found.")
            return

        # Fetch checkout with row lock to prevent race conditions on double button clicks
        checkout = await checkout_repo.get_by_id(checkout_id, for_update=True)
        if not checkout or checkout.user_id != db_user.id:
            await verifying_msg.edit_text("Order checkout not found.")
            return

        # Check if already paid
        if checkout.status in (CheckoutStatus.PAID.value, CheckoutStatus.OVERPAID_REVIEW.value):
            # Already paid — check if delivered
            order = await order_repo.get_by_checkout_id(checkout.id)
            if order and order.status == OrderStatus.DELIVERED.value:
                keys = encryption_service.decrypt(order.delivered_data_encrypted) if order.delivered_data_encrypted else []
                await send_delivery_success(update, checkout, order, keys)
                return

        # Mark state as VERIFYING (within same transaction)
        await checkout_repo.update_status(checkout.id, CheckoutStatus.VERIFYING)

        # Run 11-point payment verification
        verifier = PaymentVerifier(binance_service, payment_repo, checkout_repo)
        result, message, metadata = await verifier.verify_txid(checkout, txid, user.id)

        if result not in (VerificationResult.PAID, VerificationResult.OVERPAID_REVIEW):
            logger.info("Verification result for checkout %s: %s (%s)", checkout.id, result, message)
            if result in (VerificationResult.UNDERPAID, VerificationResult.EXPIRED_CHECKOUT):
                await session.commit()
            else:
                # Revert temporary VERIFYING status back to AWAITING_PAYMENT so customer can retry
                await checkout_repo.update_status(checkout.id, CheckoutStatus.AWAITING_PAYMENT)
                await session.commit()
            await verifying_msg.edit_text(message, parse_mode="HTML")
            return

        # Payment verified! Proceed to fulfillment
        await verifying_msg.edit_text(
            "✅ <b>Payment Confirmed!</b>\n"
            "⚡ <i>Fulfilling your digital license with supplier network...</i>",
            parse_mode="HTML",
        )

        order_service = OrderService(order_repo, checkout_repo, prodseller_service, encryption_service)
        try:
            order_status, order_msg, delivered_keys = await order_service.fulfill_order(checkout, user.id)
        except Exception as fulfill_err:
            logger.error("Unexpected error during order fulfillment for checkout %s: %s", checkout.id, fulfill_err)
            order_status = OrderStatus.SUPPLIER_FAILED
            order_msg = f"⚠️ Fulfillment delay on supplier network. Please contact support @{settings.SUPPORT_USERNAME}."
            delivered_keys = None

        # Single atomic commit point for entire flow (verification + fulfillment)
        await session.commit()

        if order_status == OrderStatus.DELIVERED:
            order = await order_repo.get_by_checkout_id(checkout.id)
            await send_delivery_success(update, checkout, order, delivered_keys)
        else:
            await update.message.reply_text(
                f"⚠️ <b>Order Status: {order_status.value}</b>\n\n{order_msg}",
                parse_mode="HTML",
            )


async def send_delivery_success(
    update: Update,
    checkout,
    order,
    keys: list,
) -> None:
    """Format and send secure delivery message with credentials to the customer."""
    keys_text = ""
    if keys:
        if len(keys) == 1:
            keys_text = f"🔑 <b>Delivered License / Account:</b>\n<code>{escape_html(keys[0])}</code>\n"
        else:
            keys_text = "🔑 <b>Delivered Licenses / Accounts:</b>\n"
            for idx, k in enumerate(keys, 1):
                keys_text += f"{idx}. <code>{escape_html(k)}</code>\n"
    else:
        keys_text = "🔑 <b>Product Details:</b> Instant digital license activated.\n"

    success_msg = (
        f"🎉 <b>Order Completed Successfully!</b>\n\n"
        f"📦 <b>Product:</b> {escape_html(checkout.product_name)}\n"
        f"🔢 <b>Quantity:</b> {checkout.quantity}\n"
        f"🆔 <b>Order ID:</b> <code>{order.id if order else checkout.id}</code>\n\n"
        f"{keys_text}\n"
        f"🔒 <i>Please save and keep your credentials private.</i>\n\n"
        f"Thank you for your purchase!"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 My Orders", callback_data="orders:1")],
        [InlineKeyboardButton("🛍 Browse More", callback_data="catalog:1")],
    ])

    if update.callback_query:
        await update.callback_query.message.reply_text(success_msg, parse_mode="HTML", reply_markup=keyboard)
    elif update.message:
        await update.message.reply_text(success_msg, parse_mode="HTML", reply_markup=keyboard)
