import logging
import math
from telegram import Update
from telegram.ext import ContextTypes

from app.bot.keyboards.checkout import get_orders_page_keyboard, get_order_detail_keyboard
from app.db.models import OrderStatus
from app.db.repositories import OrderRepository, UserRepository
from app.db.session import AsyncSessionLocal
from app.services.encryption import encryption_service
from app.utils.money import format_currency
from app.utils.telegram import escape_html

logger = logging.getLogger(__name__)

ORDERS_PAGE_SIZE = 5


async def my_orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle displaying authenticated user's order history."""
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    if not user:
        return

    page = 1
    if query and query.data and ":" in query.data:
        try:
            page = int(query.data.split(":")[1])
        except (ValueError, IndexError):
            page = 1

    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        order_repo = OrderRepository(session)

        db_user = await user_repo.get_by_telegram_id(user.id)
        if not db_user:
            msg_text = "📦 You have no purchase history yet."
            if query:
                await query.edit_message_text(msg_text)
            else:
                await update.message.reply_text(msg_text)
            return

        total_orders = await order_repo.count_user_orders(db_user.id)
        if total_orders == 0:
            msg_text = "📦 <b>You have not placed any orders yet.</b>\n\nBrowse our catalog to buy digital tools!"
            if query:
                await query.edit_message_text(msg_text, parse_mode="HTML")
            else:
                await update.message.reply_text(msg_text, parse_mode="HTML")
            return

        total_pages = max(1, math.ceil(total_orders / ORDERS_PAGE_SIZE))
        page = max(1, min(page, total_pages))
        offset = (page - 1) * ORDERS_PAGE_SIZE

        # Secure: fetches orders ONLY matching db_user.id
        orders = await order_repo.get_user_orders(db_user.id, limit=ORDERS_PAGE_SIZE, offset=offset)

    orders_text = (
        f"📦 <b>My Orders</b> (Page {page}/{total_pages})\n\n"
        f"Select an order below to inspect details and view delivered license credentials:"
    )

    keyboard = get_orders_page_keyboard(orders, page, total_pages)

    if query:
        await query.edit_message_text(orders_text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.message.reply_text(orders_text, parse_mode="HTML", reply_markup=keyboard)


async def order_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inspecting a single order — strictly authorized by user ID."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    parts = query.data.split(":")
    if len(parts) < 2:
        return
    order_id = parts[1]

    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        order_repo = OrderRepository(session)

        db_user = await user_repo.get_by_telegram_id(user.id)
        order = await order_repo.get_by_id(order_id)

        # Strict Authorization check
        if not db_user or not order or order.user_id != db_user.id:
            await query.answer("Unauthorized: You do not have access to this order.", show_alert=True)
            return

    prod_name = escape_html(order.checkout.product_name if order.checkout else f"Product {order.product_id}")
    status_icon = "✅" if order.status == OrderStatus.DELIVERED.value else "⏳"
    date_str = order.created_at.strftime("%Y-%m-%d %H:%M UTC")

    text = (
        f"📦 <b>Order Details</b>\n\n"
        f"🆔 <b>Order ID:</b> <code>{order.id}</code>\n"
        f"🛍 <b>Product:</b> {prod_name}\n"
        f"🔢 <b>Quantity:</b> {order.quantity}\n"
        f"💵 <b>Paid:</b> <code>{format_currency(order.customer_amount, order.checkout.coin if order.checkout else 'USDT')}</code>\n"
        f"📊 <b>Status:</b> {status_icon} <code>{order.status}</code>\n"
        f"📅 <b>Date:</b> {date_str}\n"
    )

    is_delivered = (order.status == OrderStatus.DELIVERED.value and bool(order.delivered_data_encrypted))

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=get_order_detail_keyboard(order.id, is_delivered=is_delivered),
    )


async def order_keys_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Decrypt and display delivered credentials to authorized purchaser."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    parts = query.data.split(":")
    if len(parts) < 2:
        return
    order_id = parts[1]

    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        order_repo = OrderRepository(session)

        db_user = await user_repo.get_by_telegram_id(user.id)
        order = await order_repo.get_by_id(order_id)

        # Strict Authorization check
        if not db_user or not order or order.user_id != db_user.id:
            await query.answer("Unauthorized.", show_alert=True)
            return

        if not order.delivered_data_encrypted:
            await query.answer("No delivery credentials found for this order.", show_alert=True)
            return

        decrypted = encryption_service.decrypt(order.delivered_data_encrypted)

    keys = decrypted if isinstance(decrypted, list) else [decrypted]
    keys_formatted = ""
    for idx, k in enumerate(keys, 1):
        keys_formatted += f"<b>Key {idx}:</b>\n<code>{escape_html(str(k))}</code>\n\n"

    keys_text = (
        f"🔑 <b>Delivered License Details for Order #{order.id[:8]}</b>\n\n"
        f"{keys_formatted}"
        f"🔒 <i>Keep this information private and secure.</i>"
    )

    await query.message.reply_text(keys_text, parse_mode="HTML")
