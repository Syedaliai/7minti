import logging
from decimal import Decimal
from telegram import Update
from telegram.ext import ContextTypes

from app.config import settings
from app.db.repositories import UserRepository
from app.db.session import AsyncSessionLocal
from app.bot.handlers.channel_guard import is_user_channel_member, send_force_sub_prompt
from app.bot.keyboards.main import get_main_menu_keyboard, get_persistent_reply_keyboard
from app.utils.telegram import escape_html

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command — Register user and display welcome hub."""
    user = update.effective_user
    if not user:
        return

    # Check mandatory channel subscription
    if not await is_user_channel_member(context.bot, user.id):
        await send_force_sub_prompt(update, context)
        return

    # Upsert user record in database and fetch balance
    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        db_user = await user_repo.upsert_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
        user_balance = await user_repo.get_balance(user.id)
        await session.commit()

    is_admin = user.id in settings.admin_ids_set

    welcome_text = (
        f"👋 <b>Welcome, {escape_html(user.first_name or 'there')}!</b>\n\n"
        f"🤖 <b>Digital & AI Tools Store</b>\n"
        f"Browse premium AI subscriptions, developer tools, and digital licenses at wholesale prices with instant automated delivery.\n\n"
        f"💳 <b>Your Balance:</b> <code>${user_balance:.4f} USDT</code>\n"
        f"💰 <b>Settlement Currency:</b> <code>{settings.PAYMENT_COIN} ({settings.PAYMENT_NETWORK})</code>\n"
        f"⚡ <b>Delivery:</b> Instant & 100% Automated\n\n"
        f"Select an option below to get started:"
    )

    # Send welcome message with persistent keyboard & inline menu
    await update.message.reply_text(
        welcome_text,
        parse_mode="HTML",
        reply_markup=get_persistent_reply_keyboard(is_admin=is_admin),
    )
    await update.message.reply_text(
        f"👇 <b>Main Menu (Balance: ${user_balance:.4f} USDT):</b>",
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(is_admin=is_admin),
    )


async def home_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle navigation back to home menu."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if user and not await is_user_channel_member(context.bot, user.id):
        await send_force_sub_prompt(update, context)
        return

    is_admin = user.id in settings.admin_ids_set if user else False

    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        user_balance = await user_repo.get_balance(user.id) if user else Decimal("0.0")

    menu_text = (
        f"🏠 <b>Main Menu</b>\n\n"
        f"💳 <b>Your Balance:</b> <code>${user_balance:.4f} USDT</code>\n\n"
        f"Please choose what you'd like to do:"
    )
    await query.edit_message_text(
        menu_text,
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(is_admin=is_admin),
    )


async def payment_guide_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show payment instructions guide."""
    query = update.callback_query
    await query.answer()

    guide_text = (
        f"💳 <b>Payment & Verification Guide</b>\n\n"
        f"1️⃣ <b>Select a product</b> and choose your desired quantity.\n"
        f"2️⃣ <b>Review the order summary</b> showing the exact total amount in <code>{settings.PAYMENT_COIN}</code>.\n"
        f"3️⃣ <b>Send the payment</b> to our official deposit address on the <b>{settings.PAYMENT_NETWORK}</b> network.\n"
        f"4️⃣ Click <b>'✅ I Have Paid'</b> and enter your blockchain <b>Transaction Hash (TxID)</b>.\n"
        f"5️⃣ Our system automatically verifies your deposit on Binance and instantly delivers your license key/account.\n\n"
        f"⚠️ <b>Important Notes:</b>\n"
        f"• Send ONLY <code>{settings.PAYMENT_COIN}</code> on the <code>{settings.PAYMENT_NETWORK}</code> network.\n"
        f"• Ensure you pay the exact amount or slightly more. Underpayments cannot be fulfilled automatically.\n"
        f"• Each transaction hash can only be redeemed once."
    )
    from app.bot.keyboards.admin import get_admin_back_keyboard
    await query.edit_message_text(
        guide_text,
        parse_mode="HTML",
        reply_markup=get_main_menu_keyboard(is_admin=update.effective_user.id in settings.admin_ids_set),
    )
