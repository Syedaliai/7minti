import logging
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.config import settings

logger = logging.getLogger(__name__)

VALID_STATUSES = {"creator", "administrator", "member", "restricted"}


def is_channel_guard_enabled() -> bool:
    """Check if mandatory channel subscription is configured in settings."""
    return bool(settings.REQUIRED_CHANNEL_ID and settings.REQUIRED_CHANNEL_ID.strip())


async def is_user_channel_member(bot, user_id: int) -> bool:
    """
    Check if a Telegram user is a member of the required channel.
    Returns True if joined or if guard is disabled.
    """
    if not is_channel_guard_enabled():
        return True

    # Admins always bypass guard
    if user_id in settings.admin_ids_set:
        return True

    channel = settings.REQUIRED_CHANNEL_ID.strip()
    try:
        member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
        if member.status in VALID_STATUSES:
            return True
        return False
    except Exception as exc:
        logger.warning(
            "Channel guard check failed for user %d in channel %s: %s (Ensure bot is added as Admin in the channel)",
            user_id,
            channel,
            exc,
        )
        # If bot cannot check (e.g. invalid channel or bot is not admin in channel), do not permanently lock out users
        return True


def get_force_sub_keyboard() -> InlineKeyboardMarkup:
    """Keyboard containing Channel Join Link and Verify Button."""
    join_url = (settings.REQUIRED_CHANNEL_LINK or "https://t.me/+YfygxZDBeDw4ZDk0").strip()

    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Join Official Channel", url=join_url)],
        [InlineKeyboardButton("✅ I Have Joined (Verify Now)", callback_data="force_sub:verify")],
    ])


async def send_force_sub_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send mandatory channel subscription prompt to the user."""
    channel_display = "our official channel"
    text = (
        "🔒 <b>Mandatory Channel Subscription</b>\n\n"
        "To protect the platform against abuse and receive critical service updates, "
        f"you must join <b>{channel_display}</b> before using the bot.\n\n"
        "<b>Simple 2-Step Activation:</b>\n"
        "1️⃣ Click <b>'📢 Join Official Channel'</b> below.\n"
        "2️⃣ Click <b>'✅ I Have Joined (Verify Now)'</b> to instantly unlock full access!\n\n"
        "⚡ <i>Access is automatically unlocked in 1 second upon joining.</i>"
    )
    kb = get_force_sub_keyboard()
    if update.callback_query:
        await update.callback_query.answer("⚠️ Subscription required to continue.", show_alert=True)
        try:
            await update.callback_query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="HTML", reply_markup=kb)
    elif update.message:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def force_sub_verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the '✅ I Have Joined (Verify Now)' button click."""
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    if not user:
        return

    is_member = await is_user_channel_member(context.bot, user.id)
    if is_member:
        await query.answer("🎉 Channel verified successfully! Welcome!", show_alert=True)
        from app.bot.handlers.start import start_command
        context.user_data["channel_verified"] = True
        try:
            await query.message.delete()
        except Exception:
            pass
        await start_command(update, context)
    else:
        await query.answer(
            "❌ You haven't joined the channel yet!\n\nPlease click '📢 Join Official Channel' first, then click Verify.",
            show_alert=True,
        )


async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-capture channel ID whenever a post is made in any channel where bot is admin."""
    chat = update.effective_chat
    if chat and chat.type in ("channel", "supergroup"):
        chan_id = str(chat.id)
        chan_title = chat.title or "Channel"
        settings.REQUIRED_CHANNEL_ID = chan_id
        if not settings.REQUIRED_CHANNEL_LINK:
            settings.REQUIRED_CHANNEL_LINK = "https://t.me/+YfygxZDBeDw4ZDk0"

        try:
            with open(".env", "r", encoding="utf-8") as f:
                env_content = f.read()
            if "REQUIRED_CHANNEL_ID=" in env_content:
                import re
                env_content = re.sub(r"REQUIRED_CHANNEL_ID=.*", f"REQUIRED_CHANNEL_ID={chan_id}", env_content)
            else:
                env_content += f"\nREQUIRED_CHANNEL_ID={chan_id}\n"
            with open(".env", "w", encoding="utf-8") as f:
                f.write(env_content)
        except Exception as e:
            logger.error("Could not write channel ID to .env: %s", e)

        logger.info("Automatically linked channel: %s (%s)", chan_title, chan_id)


async def forward_channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Auto-detect channel ID when admin forwards a message from the channel to the bot.
    Returns True if handled.
    """
    user = update.effective_user
    if not user or user.id not in settings.admin_ids_set:
        return False

    msg = update.message
    if not msg:
        return False

    chat = getattr(msg, "forward_from_chat", None)
    if not chat and hasattr(msg, "forward_origin") and msg.forward_origin:
        origin = msg.forward_origin
        chat = getattr(origin, "chat", None) or getattr(origin, "sender_chat", None)

    if chat and getattr(chat, "type", None) in ("channel", "supergroup"):
        chan_id = str(chat.id)
        chan_title = getattr(chat, "title", "Channel") or "Channel"
        settings.REQUIRED_CHANNEL_ID = chan_id
        if not settings.REQUIRED_CHANNEL_LINK:
            settings.REQUIRED_CHANNEL_LINK = "https://t.me/+YfygxZDBeDw4ZDk0"

        # Update .env
        try:
            with open(".env", "r", encoding="utf-8") as f:
                env_content = f.read()
            if "REQUIRED_CHANNEL_ID=" in env_content:
                import re
                env_content = re.sub(r"REQUIRED_CHANNEL_ID=.*", f"REQUIRED_CHANNEL_ID={chan_id}", env_content)
            else:
                env_content += f"\nREQUIRED_CHANNEL_ID={chan_id}\n"
            with open(".env", "w", encoding="utf-8") as f:
                f.write(env_content)
        except Exception as e:
            logger.error("Could not write channel ID to .env: %s", e)

        reply_text = (
            f"🎉 <b>Channel Successfully Connected!</b>\n\n"
            f"📢 <b>Title:</b> <code>{chan_title}</code>\n"
            f"🆔 <b>Channel ID:</b> <code>{chan_id}</code>\n"
            f"🔗 <b>Invite Link:</b> <code>{settings.REQUIRED_CHANNEL_LINK}</code>\n\n"
            f"🔒 <i>All users must now join this channel before using the bot. Auto-verification is live!</i>"
        )
        await msg.reply_text(reply_text, parse_mode="HTML")
        return True

    return False
