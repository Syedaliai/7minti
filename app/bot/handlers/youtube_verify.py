"""
YouTube Channel Verification handler using SMSPool.
Flow:
  1. User clicks service → deposit check → prices shown (US / UK)
  2. User selects country → SMSPool number purchased → number shown
  3. Bot polls every 5 s for OTP (up to ~9 min) → OTP displayed
  4. User can cancel at any time → order cancelled on SMSPool
Security:
  - Only users with at least one approved payment can access
  - All sessions tracked in sms_orders table (unique smspool_order_id)
  - One active session per user enforced
  - No client-provided prices; all prices fetched server-side
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from app.config import settings
from app.db.models import SmsOrder, SmsOrderStatus, Payment
from app.db.session import AsyncSessionLocal
from app.services.smspool import (
    SMSPoolService, SMSPoolError,
    COUNTRY_US, COUNTRY_UK, STATUS_COMPLETED, STATUS_EXPIRED, STATUS_CANCELLED,
)
from sqlalchemy import select, update as sa_update

logger = logging.getLogger(__name__)

# Commission multiplier: 60% on top → price * 1.60
COMMISSION_MULTIPLIER = Decimal("1") + settings.SMS_COMMISSION_RATE

# How often to poll SMSPool for OTP (seconds)
POLL_INTERVAL = 5
# Max polling time (seconds) — SMSPool numbers expire in ~10 min
MAX_WAIT = 540  # 9 minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_smspool() -> SMSPoolService:
    return SMSPoolService(settings.SMSPOOL_API_KEY)


def _with_commission(raw_price: float) -> Decimal:
    """Apply 60% commission and round to 4 decimal places."""
    return (Decimal(str(raw_price)) * COMMISSION_MULTIPLIER).quantize(Decimal("0.0001"))


async def _has_deposit(telegram_id: int) -> bool:
    """Return True if the user has at least one approved payment in the DB."""
    from app.db.models import User
    async with AsyncSessionLocal() as session:
        # Step 1: get internal user id
        user_res = await session.execute(
            select(User.id).where(User.telegram_id == telegram_id)
        )
        user_id = user_res.scalar_one_or_none()
        if not user_id:
            return False
        # Step 2: check for any approved payment
        pay_res = await session.execute(
            select(Payment.id).where(
                Payment.user_id == user_id,
                Payment.status.in_(["PAID", "COMPLETED", "CONFIRMED"]),
            ).limit(1)
        )
        return pay_res.scalar_one_or_none() is not None


async def _has_active_session(telegram_id: int) -> bool:
    """Return True if user already has an ACTIVE sms_order."""
    async with AsyncSessionLocal() as session:
        stmt = select(SmsOrder).where(
            SmsOrder.telegram_id == telegram_id,
            SmsOrder.status == SmsOrderStatus.ACTIVE.value,
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none() is not None


async def _get_active_order(telegram_id: int):
    """Return the user's current ACTIVE SmsOrder or None."""
    async with AsyncSessionLocal() as session:
        stmt = select(SmsOrder).where(
            SmsOrder.telegram_id == telegram_id,
            SmsOrder.status == SmsOrderStatus.ACTIVE.value,
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Entry — /youtube or button press
# ---------------------------------------------------------------------------

async def youtube_verify_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show YouTube Verification service with live US/UK prices."""
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    if not user:
        return

    # 1. Deposit guard
    if not await _has_deposit(user.id):
        msg = (
            "🔒 <b>Access Restricted</b>\n\n"
            "YouTube Channel Verification is available only to users who have made a deposit.\n\n"
            "Please <b>deposit first</b> via 💳 Deposit, then try again."
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("💳 Deposit Now", callback_data="nav:deposit"),
            InlineKeyboardButton("🏠 Home", callback_data="nav:home"),
        ]])
        if query:
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)
        else:
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)
        return

    # 2. Block if already has active session
    if await _has_active_session(user.id):
        msg = (
            "⚠️ <b>Active Session Exists</b>\n\n"
            "You already have a number assigned. Please wait for your current OTP "
            "or cancel it before requesting a new one."
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📱 View My Number", callback_data="yt:view_active"),
            InlineKeyboardButton("🏠 Home", callback_data="nav:home"),
        ]])
        if query:
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)
        else:
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)
        return

    # 3. Fetch live prices
    loading_text = "⏳ <i>Fetching live prices from SMSPool...</i>"
    if query:
        await query.edit_message_text(loading_text, parse_mode="HTML")
    else:
        sent = await update.message.reply_text(loading_text, parse_mode="HTML")
        context.user_data["yt_loading_msg_id"] = sent.message_id

    sms = _get_smspool()
    try:
        prices = await sms.get_prices_both_countries()
    finally:
        await sms.close()

    us_raw = prices.get("us")
    uk_raw = prices.get("uk")

    def price_label(raw):
        if raw is None:
            return "Unavailable"
        charged = _with_commission(raw)
        return f"${charged:.4f} USDT"

    us_label = price_label(us_raw)
    uk_label = price_label(uk_raw)

    msg = (
        "📺 <b>YouTube Channel Verification</b>\n\n"
        "Get a real phone number to verify your YouTube channel via SMS OTP.\n\n"
        "📍 <b>Select your country:</b>\n\n"
        f"🇺🇸 <b>United States:</b> {us_label}\n"
        f"🇬🇧 <b>United Kingdom:</b> {uk_label}\n\n"
        "⚠️ <i>Numbers expire in ~10 minutes after purchase. "
        "Price is charged from your deposit balance.</i>"
    )

    buttons = []
    if us_raw is not None:
        buttons.append(InlineKeyboardButton(
            f"🇺🇸 US  {price_label(us_raw)}",
            callback_data=f"yt:buy:{COUNTRY_US}",
        ))
    if uk_raw is not None:
        buttons.append(InlineKeyboardButton(
            f"🇬🇧 UK  {price_label(uk_raw)}",
            callback_data=f"yt:buy:{COUNTRY_UK}",
        ))

    kb_rows = [buttons] if buttons else []
    kb_rows.append([InlineKeyboardButton("🏠 Home", callback_data="nav:home")])
    kb = InlineKeyboardMarkup(kb_rows)

    if query:
        await query.edit_message_text(msg, parse_mode="HTML", reply_markup=kb)
    else:
        chat_id = update.effective_chat.id
        mid = context.user_data.pop("yt_loading_msg_id", None)
        if mid:
            await context.bot.edit_message_text(
                loading_text.replace(loading_text, msg),
                chat_id=chat_id, message_id=mid,
                parse_mode="HTML", reply_markup=kb,
            )
        else:
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=kb)


# ---------------------------------------------------------------------------
# Country selected → purchase number
# ---------------------------------------------------------------------------

async def youtube_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User selected US or UK — purchase the number from SMSPool."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    # Parse country from callback_data: yt:buy:{country_id}
    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer("Invalid selection.", show_alert=True)
        return
    country_id = parts[2]
    country_label = "🇺🇸 United States" if country_id == COUNTRY_US else "🇬🇧 United Kingdom"

    # Re-check deposit guard (prevent callback replay)
    if not await _has_deposit(user.id):
        await query.answer("❌ Deposit required.", show_alert=True)
        return

    # Re-check no active session (race condition guard)
    if await _has_active_session(user.id):
        await query.answer("⚠️ You already have an active number!", show_alert=True)
        return

    await query.edit_message_text(
        f"⏳ <b>Purchasing {country_label} number...</b>\n\n"
        "<i>Please wait a moment.</i>",
        parse_mode="HTML",
    )

    # Purchase from SMSPool — server-side price, no client input accepted
    sms = _get_smspool()
    try:
        result = await sms.purchase_number(country_id=country_id)
    except SMSPoolError as e:
        await sms.close()
        await query.edit_message_text(
            f"❌ <b>Purchase Failed</b>\n\n{e}\n\nPlease try again later.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔁 Retry", callback_data="yt:menu"),
                InlineKeyboardButton("🏠 Home", callback_data="nav:home"),
            ]]),
        )
        return
    finally:
        await sms.close()

    supplier_price = result["price"]
    charged_price = _with_commission(supplier_price)
    expires_in = result["expires_in"]
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Persist to DB
    async with AsyncSessionLocal() as session:
        from app.db.models import User as UserModel
        user_stmt = select(UserModel).where(UserModel.telegram_id == user.id)
        user_res = await session.execute(user_stmt)
        db_user = user_res.scalar_one_or_none()
        if not db_user:
            await query.edit_message_text("❌ User not found in database.", parse_mode="HTML")
            return

        sms_order = SmsOrder(
            id=str(uuid.uuid4()),
            user_id=db_user.id,
            telegram_id=user.id,
            smspool_order_id=result["order_id"],
            phone_number=result["number"],
            country="us" if country_id == COUNTRY_US else "uk",
            service="YouTube",
            supplier_price=Decimal(str(supplier_price)),
            charged_price=charged_price,
            status=SmsOrderStatus.ACTIVE.value,
            expires_at=expires_at,
        )
        session.add(sms_order)
        await session.commit()

    # Store in user_data for polling
    context.user_data["yt_order_id"] = result["order_id"]
    context.user_data["yt_number"] = result["number"]
    context.user_data["yt_country"] = country_label
    context.user_data["yt_chat_id"] = query.message.chat_id
    context.user_data["yt_message_id"] = query.message.message_id
    context.user_data["yt_poll_count"] = 0

    # Show number to user
    flag = "🇺🇸" if country_id == COUNTRY_US else "🇬🇧"
    mins = expires_in // 60
    await query.edit_message_text(
        f"📱 <b>Your Verification Number</b>\n\n"
        f"{flag} <b>Country:</b> {country_label}\n"
        f"📞 <b>Number:</b> <code>+{result['number']}</code>\n"
        f"💰 <b>Charged:</b> ${charged_price:.4f} USDT\n"
        f"⏳ <b>Expires in:</b> ~{mins} minutes\n\n"
        "👆 <b>Send this number on YouTube to receive OTP.</b>\n"
        "⌛ <i>Waiting for OTP automatically...</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel Number", callback_data="yt:cancel"),
        ]]),
    )

    # Start async polling job
    asyncio.create_task(
        _poll_for_otp(
            context=context,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            order_id=result["order_id"],
            number=result["number"],
            country_label=country_label,
            telegram_id=user.id,
            charged_price=charged_price,
        )
    )


# ---------------------------------------------------------------------------
# OTP Polling (background task)
# ---------------------------------------------------------------------------

async def _poll_for_otp(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    message_id: int,
    order_id: str,
    number: str,
    country_label: str,
    telegram_id: int,
    charged_price: Decimal,
) -> None:
    """Poll SMSPool every POLL_INTERVAL seconds and update the message when OTP arrives."""
    sms = _get_smspool()
    elapsed = 0

    try:
        while elapsed < MAX_WAIT:
            await asyncio.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL

            try:
                result = await sms.check_sms(order_id)
            except Exception as exc:
                logger.error("SMSPool poll error for order %s: %s", order_id, exc)
                continue

            status = result["status"]

            if status == STATUS_COMPLETED and result.get("sms"):
                # ✅ OTP received
                otp = result["sms"]
                full_msg = result.get("full_sms", otp)

                # Mark DB as COMPLETED
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        sa_update(SmsOrder)
                        .where(SmsOrder.smspool_order_id == order_id)
                        .values(
                            status=SmsOrderStatus.COMPLETED.value,
                            otp_received=otp,
                            full_sms=full_msg,
                            completed_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.commit()

                try:
                    await context.bot.edit_message_text(
                        f"✅ <b>OTP Received!</b>\n\n"
                        f"📞 <b>Number:</b> <code>+{number}</code>\n"
                        f"🔑 <b>OTP Code:</b> <code>{otp}</code>\n\n"
                        f"📩 <b>Full SMS:</b>\n<i>{full_msg}</i>\n\n"
                        f"💰 <b>Charged:</b> ${charged_price:.4f} USDT",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔁 Get Another Number", callback_data="yt:menu"),
                            InlineKeyboardButton("🏠 Home", callback_data="nav:home"),
                        ]]),
                    )
                except Exception as e:
                    logger.error("Failed to edit OTP message: %s", e)
                return

            elif status in (STATUS_EXPIRED, STATUS_CANCELLED):
                # Mark DB
                new_status = SmsOrderStatus.EXPIRED if status == STATUS_EXPIRED else SmsOrderStatus.CANCELLED
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        sa_update(SmsOrder)
                        .where(SmsOrder.smspool_order_id == order_id)
                        .values(status=new_status.value)
                    )
                    await session.commit()

                reason = "expired" if status == STATUS_EXPIRED else "cancelled"
                try:
                    await context.bot.edit_message_text(
                        f"⏰ <b>Number {reason.title()}</b>\n\n"
                        f"The number <code>+{number}</code> has {reason}.\n\n"
                        "You can request a new number below.",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔁 Get New Number", callback_data="yt:menu"),
                            InlineKeyboardButton("🏠 Home", callback_data="nav:home"),
                        ]]),
                    )
                except Exception as e:
                    logger.error("Failed to edit expired message: %s", e)
                return

        # Timeout reached — mark expired
        async with AsyncSessionLocal() as session:
            await session.execute(
                sa_update(SmsOrder)
                .where(SmsOrder.smspool_order_id == order_id)
                .values(status=SmsOrderStatus.EXPIRED.value)
            )
            await session.commit()

        try:
            await context.bot.edit_message_text(
                f"⏰ <b>OTP Wait Timeout</b>\n\n"
                f"No OTP was received for <code>+{number}</code> within the time limit.\n\n"
                "Please request a new number.",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔁 Try Again", callback_data="yt:menu"),
                    InlineKeyboardButton("🏠 Home", callback_data="nav:home"),
                ]]),
            )
        except Exception as e:
            logger.error("Failed to edit timeout message: %s", e)

    finally:
        await sms.close()


# ---------------------------------------------------------------------------
# Cancel active session
# ---------------------------------------------------------------------------

async def youtube_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User cancels their active number."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    async with AsyncSessionLocal() as session:
        stmt = select(SmsOrder).where(
            SmsOrder.telegram_id == user.id,
            SmsOrder.status == SmsOrderStatus.ACTIVE.value,
        )
        res = await session.execute(stmt)
        order = res.scalar_one_or_none()

        if not order:
            await query.answer("No active number found.", show_alert=True)
            return

        smspool_oid = order.smspool_order_id
        await session.execute(
            sa_update(SmsOrder)
            .where(SmsOrder.id == order.id)
            .values(status=SmsOrderStatus.CANCELLED.value)
        )
        await session.commit()

    # Also cancel on SMSPool
    sms = _get_smspool()
    try:
        await sms.cancel_order(smspool_oid)
    finally:
        await sms.close()

    await query.edit_message_text(
        "❌ <b>Number Cancelled</b>\n\nYour verification number has been cancelled.\n"
        "You can request a new one anytime.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔁 Get New Number", callback_data="yt:menu"),
            InlineKeyboardButton("🏠 Home", callback_data="nav:home"),
        ]]),
    )


# ---------------------------------------------------------------------------
# View active session
# ---------------------------------------------------------------------------

async def youtube_view_active_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the user their currently active number."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    async with AsyncSessionLocal() as session:
        stmt = select(SmsOrder).where(
            SmsOrder.telegram_id == user.id,
            SmsOrder.status == SmsOrderStatus.ACTIVE.value,
        )
        res = await session.execute(stmt)
        order = res.scalar_one_or_none()

    if not order:
        await query.edit_message_text(
            "ℹ️ No active number session found.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📺 Get Number", callback_data="yt:menu"),
                InlineKeyboardButton("🏠 Home", callback_data="nav:home"),
            ]]),
        )
        return

    flag = "🇺🇸" if order.country == "us" else "🇬🇧"
    remaining = max(0, int((order.expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()))
    mins, secs = divmod(remaining, 60)

    await query.edit_message_text(
        f"📱 <b>Active Number</b>\n\n"
        f"{flag} <b>Country:</b> {'United States' if order.country == 'us' else 'United Kingdom'}\n"
        f"📞 <b>Number:</b> <code>+{order.phone_number}</code>\n"
        f"⏳ <b>Time Left:</b> {mins}m {secs}s\n\n"
        "⌛ <i>Still waiting for OTP automatically...</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel Number", callback_data="yt:cancel"),
        ]]),
    )
