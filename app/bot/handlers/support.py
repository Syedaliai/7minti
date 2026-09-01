from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.config import settings


async def support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show customer support contact info."""
    query = update.callback_query
    if query:
        await query.answer()

    support_text = (
        f"🆘 <b>Customer Support &amp; Assistance</b>\n\n"
        f"Having issues with a payment, order, or deposit? We're here to help!\n\n"
        f"👤 <b>Support / Admin:</b> @{settings.SUPPORT_USERNAME}\n"
        f"⏱ <b>Response Time:</b> Usually within minutes\n\n"
        f"📌 <b>When contacting support, please share:</b>\n"
        f"• Your <b>Order ID</b> or <b>Binance Pay Order ID</b>\n"
        f"• Screenshot of your payment if needed\n\n"
        f"Click the button below to open a direct chat 👇"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Open Support Chat", url=f"https://t.me/{settings.SUPPORT_USERNAME}")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
    ])

    if query:
        await query.edit_message_text(support_text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.message.reply_text(support_text, parse_mode="HTML", reply_markup=keyboard)
