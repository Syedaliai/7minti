import logging
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.keyboards.checkout import get_checkout_keyboard
from app.config import settings
from app.db.repositories import CheckoutRepository, UserRepository
from app.db.session import AsyncSessionLocal
from app.services.checkout_service import CheckoutService
from app.services.prodseller import ProdSellerService, ProdSellerAPIError
from app.utils.money import format_currency
from app.utils.telegram import escape_html

logger = logging.getLogger(__name__)


async def buy_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'Buy Now' button: generate authoritative checkout quote and show deposit instructions."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) < 3:
        return

    product_id = parts[1]
    quantity = int(parts[2])

    user = update.effective_user
    if not user:
        return

    prodseller_service: ProdSellerService = context.bot_data["prodseller_service"]

    try:
        # Create authoritative checkout quote with DB session
        async with AsyncSessionLocal() as session:
            checkout_repo = CheckoutRepository(session)
            user_repo = UserRepository(session)
            checkout_service = CheckoutService(checkout_repo, user_repo, prodseller_service)

            checkout, product_data = await checkout_service.create_checkout(
                telegram_user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                product_id=product_id,
                quantity=quantity,
            )
            await session.commit()

            # Generate QR code for the deposit address
            qr_io = CheckoutService.generate_qr_code_bytes(checkout.payment_address)

    except ProdSellerAPIError as err:
        logger.warning("Checkout creation supplier error: %s", err)
        await query.answer(f"⚠️ {err}", show_alert=True)
        return
    except Exception as ex:
        logger.error("Failed to create checkout: %s", ex)
        await query.answer("⚠️ Unable to create order checkout. Please try again.", show_alert=True)
        return

    # Format summary message
    uid_line = f"\n🆔 <b>Binance Pay UID:</b> <code>{escape_html(settings.BINANCE_UID)}</code>" if settings.BINANCE_UID else ""
    summary_text = (
        f"🧾 <b>Order Checkout Summary</b>\n\n"
        f"📦 <b>Product:</b> {escape_html(checkout.product_name)}\n"
        f"🔢 <b>Quantity:</b> {checkout.quantity}\n"
        f"💰 <b>Unit Price:</b> {format_currency(checkout.customer_unit_price, checkout.coin)}\n"
        f"💵 <b>Total Payable:</b> <code>{format_currency(checkout.expected_total, checkout.coin)}</code>\n\n"
        f"🌐 <b>Network:</b> <code>{escape_html(checkout.network)}</code>\n"
        f"🪙 <b>Coin:</b> <code>{escape_html(checkout.coin)}</code>\n"
        f"📍 <b>Deposit Address:</b>\n"
        f"<code>{escape_html(checkout.payment_address)}</code>{uid_line}\n\n"
        f"❗ <b>Payment Instructions:</b>\n"
        f"1. Send <b>exactly</b> <code>{format_currency(checkout.expected_total, checkout.coin)}</code> to the address above.\n"
        f"2. Use <b>only</b> the <b>{escape_html(checkout.network)}</b> network.\n"
        f"3. After sending, click the <b>'✅ I Have Paid'</b> button below and submit your Transaction ID (TxID).\n\n"
        f"⏳ <i>Quote valid for 30 minutes.</i>"
    )

    # Send QR code image with caption and checkout actions
    await query.message.reply_photo(
        photo=qr_io,
        caption=summary_text,
        parse_mode="HTML",
        reply_markup=get_checkout_keyboard(checkout.id),
    )
