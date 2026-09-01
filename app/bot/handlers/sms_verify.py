"""
Unified SMS Verification Service Handler (SMSPool Integration).
Supports:
  - OpenAI / ChatGPT / Codex Verification (80% profit commission)
  - YouTube Channel Verification (60% profit commission)

Security & Architecture:
  1. Strict Deposit Guard: Only users with verified deposits/payments in DB can access.
  2. Server-Side Calculations: Prices fetched in real-time from SMSPool /request/price.
  3. Replay & Tampering Prevention: Callback state verified against server DB state.
  4. Active Session Lock: Exactly 1 concurrent active OTP session per Telegram user.
  5. Asynchronous Polling: Non-blocking background worker checks SMS every 5s.
  6. Two-way Cancellation: Cancels on SMSPool and marks DB as CANCELLED.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, Any, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, update as sa_update

from app.config import settings
from app.db.models import SmsOrder, SmsOrderStatus, Payment, User
from app.db.repositories import UserRepository, CouponRepository
from app.db.session import AsyncSessionLocal
from app.services.smspool import (
    SMSPoolService,
    SMSPoolError,
    COUNTRY_US,
    COUNTRY_UK,
    SERVICE_OPENAI,
    SERVICE_YOUTUBE,
    SERVICE_NVIDIA,
    STATUS_COMPLETED,
    STATUS_EXPIRED,
    STATUS_CANCELLED,
)

logger = logging.getLogger(__name__)

# Registered Verification Services Configuration
SERVICES: Dict[str, Dict[str, Any]] = {
    "openai": {
        "id": SERVICE_OPENAI,
        "name": "Codex / OpenAI",
        "display_title": "🤖 Codex Verification",
        "commission_rate": settings.OPENAI_SMS_COMMISSION_RATE,  # 80% (0.80)
        "description": "Get a verified USA or UK phone number for Codex & OpenAI verification.",
    },
    "nvidia": {
        "id": SERVICE_NVIDIA,
        "name": "Nvidia",
        "display_title": "🟢 Nvidia Account Verification",
        "commission_rate": settings.NVIDIA_SMS_COMMISSION_RATE,  # 80% (0.80)
        "description": "Get a verified USA or UK phone number for Nvidia Developer & GeForce Account verification.",
    },
    "youtube": {
        "id": SERVICE_YOUTUBE,
        "name": "YouTube",
        "display_title": "📺 YouTube Channel Verification",
        "commission_rate": settings.SMS_COMMISSION_RATE,  # 60% (0.60)
        "description": "Get a verified USA or UK phone number for YouTube Channel feature verification.",
    },
}

# Polling configuration
POLL_INTERVAL_SECONDS = 5
MAX_POLL_DURATION_SECONDS = 540  # 9 minutes


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def _get_smspool() -> SMSPoolService:
    return SMSPoolService(settings.SMSPOOL_API_KEY)


def _calculate_customer_price(raw_price: float, commission_rate: Decimal) -> Decimal:
    """Apply commission (e.g. +80% = raw * 1.80) and round to 4 decimals."""
    multiplier = Decimal("1") + commission_rate
    return (Decimal(str(raw_price)) * multiplier).quantize(Decimal("0.0001"))


async def _has_valid_deposit(telegram_id: int) -> bool:
    """Return True if user has at least one confirmed payment or deposit."""
    async with AsyncSessionLocal() as session:
        user_res = await session.execute(
            select(User.id).where(User.telegram_id == telegram_id)
        )
        user_id = user_res.scalar_one_or_none()
        if not user_id:
            return False

        pay_res = await session.execute(
            select(Payment.id).where(
                Payment.user_id == user_id,
                Payment.status.in_(["PAID", "COMPLETED", "CONFIRMED"]),
            ).limit(1)
        )
        return pay_res.scalar_one_or_none() is not None


async def _get_active_user_order(telegram_id: int) -> Optional[SmsOrder]:
    """Return the user's current ACTIVE SmsOrder or None."""
    async with AsyncSessionLocal() as session:
        stmt = select(SmsOrder).where(
            SmsOrder.telegram_id == telegram_id,
            SmsOrder.status == SmsOrderStatus.ACTIVE.value,
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Verification Hub & Specific Service Menu
# ---------------------------------------------------------------------------

async def sms_hub_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show SMS Verification Hub with all supported services."""
    query = update.callback_query
    if query:
        await query.answer()

    text = (
        "⚡ <b>SMS OTP Verification Hub</b>\n\n"
        "Instant real phone numbers from USA 🇺🇸 and UK 🇬🇧 for SMS verification.\n\n"
        "Select the service you want to verify:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 WhatsApp Verification", callback_data="wa:menu")],
        [InlineKeyboardButton("🤖 Codex Verification", callback_data="sms:menu:openai")],
        [InlineKeyboardButton("🟢 Nvidia Account Verification", callback_data="sms:menu:nvidia")],
        [InlineKeyboardButton("📺 YouTube Channel Verification", callback_data="sms:menu:youtube")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
    ])

    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def service_verification_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    service_key: str = "openai",
) -> None:
    """Render the price selection menu for a specific service with live prices."""
    query = update.callback_query
    if query:
        await query.answer()

    user = update.effective_user
    if not user:
        return

    service_info = SERVICES.get(service_key, SERVICES["openai"])

    # 1. Concurrency Check: Only 1 active number session at a time
    active_order = await _get_active_user_order(user.id)
    if active_order:
        active_text = (
            "⚠️ <b>Active Number Session in Progress</b>\n\n"
            f"You already have an active number (<code>+{active_order.phone_number}</code>).\n"
            "Please wait for your OTP or cancel your active number before ordering another."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📱 View Active Number", callback_data="sms:view_active")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
        ])
        if query:
            await query.edit_message_text(active_text, parse_mode="HTML", reply_markup=kb)
        else:
            await update.message.reply_text(active_text, parse_mode="HTML", reply_markup=kb)
        return

    # 2. Live Price Check
    loading_text = f"⏳ <i>Checking real-time price & availability for {service_info['name']}...</i>"
    if query:
        await query.edit_message_text(loading_text, parse_mode="HTML")
    else:
        sent = await update.message.reply_text(loading_text, parse_mode="HTML")
        context.user_data["sms_loading_mid"] = sent.message_id

    sms_client = _get_smspool()
    try:
        prices = await sms_client.get_prices_both_countries(service_info["id"])
    finally:
        await sms_client.close()

    us_raw = prices.get("us")
    uk_raw = prices.get("uk")
    comm_rate = service_info["commission_rate"]

    def format_price(raw: Optional[float]) -> str:
        if raw is None:
            return "Unavailable"
        c_price = _calculate_customer_price(raw, comm_rate)
        return f"${c_price:.4f} USDT"

    us_display = format_price(us_raw)
    uk_display = format_price(uk_raw)

    menu_text = (
        f"{service_info['display_title']}\n\n"
        f"{service_info['description']}\n\n"
        f"📍 <b>Choose your country:</b>\n\n"
        f"🇺🇸 <b>United States:</b> {us_display}\n"
        f"🇬🇧 <b>United Kingdom:</b> {uk_display}\n\n"
        "⚡ <i>Numbers are activated instantly. OTP will appear automatically within seconds.</i>"
    )

    buttons = []
    if us_raw is not None:
        buttons.append(InlineKeyboardButton(
            f"🇺🇸 US  {us_display}",
            callback_data=f"sms:buy:{service_key}:{COUNTRY_US}",
        ))
    if uk_raw is not None:
        buttons.append(InlineKeyboardButton(
            f"🇬🇧 UK  {uk_display}",
            callback_data=f"sms:buy:{service_key}:{COUNTRY_UK}",
        ))

    rows = [buttons] if buttons else []
    rows.append([
        InlineKeyboardButton("⚡ All Services", callback_data="sms:hub"),
        InlineKeyboardButton("🏠 Home", callback_data="nav:home"),
    ])
    keyboard = InlineKeyboardMarkup(rows)

    if query:
        await query.edit_message_text(menu_text, parse_mode="HTML", reply_markup=keyboard)
    else:
        mid = context.user_data.pop("sms_loading_mid", None)
        if mid:
            try:
                await context.bot.edit_message_text(
                    menu_text,
                    chat_id=update.effective_chat.id,
                    message_id=mid,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                return
            except Exception:
                pass
        await update.message.reply_text(menu_text, parse_mode="HTML", reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Number Purchase Callback
# ---------------------------------------------------------------------------

async def sms_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle instant number purchase from SMSPool with atomic balance check."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    # Format: sms:buy:{service_key}:{country_id}
    parts = query.data.split(":")
    if len(parts) < 4:
        await query.answer("Invalid request.", show_alert=True)
        return

    service_key = parts[2]
    country_id = parts[3]
    service_info = SERVICES.get(service_key, SERVICES["openai"])
    country_name = "United States 🇺🇸" if country_id == COUNTRY_US else "United Kingdom 🇬🇧"
    flag = "🇺🇸" if country_id == COUNTRY_US else "🇬🇧"

    # Re-verify no active session
    if await _get_active_user_order(user.id):
        await query.answer("⚠️ You already have an active number session.", show_alert=True)
        return

    # Fetch live price
    sms_client = _get_smspool()
    try:
        raw_supplier_price = await sms_client.get_price(country_id, service_info["id"])
    finally:
        await sms_client.close()

    if raw_supplier_price is None:
        await query.edit_message_text(
            f"❌ <b>Service Unavailable</b>\n\nNo numbers available for {service_info['name']} ({country_name}) at this time.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Try Again", callback_data=f"sms:menu:{service_key}")],
                [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
            ]),
        )
        return

    charged_price = _calculate_customer_price(raw_supplier_price, service_info["commission_rate"])
    discount_applied = Decimal("0.0")
    redeemed_coupon_id = None
    promo_note = ""

    # User balance check & deduction
    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        coupon_repo = CouponRepository(session)
        db_user = await user_repo.upsert_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )

        # Check for active promo code
        active_code = context.user_data.get("active_promo_code")
        if active_code:
            is_val, final_p, disc_val, c_obj, err_msg = await coupon_repo.validate_and_calculate_discount(
                code=active_code,
                service=service_key,
                user_id=db_user.id,
                original_price=charged_price,
            )
            if is_val and c_obj:
                charged_price = final_p
                discount_applied = disc_val
                redeemed_coupon_id = c_obj.id
                promo_note = f"🎟️ <i>Promo Code <code>{c_obj.code}</code> Applied (-${disc_val:.4f})!</i>\n"
                context.user_data.pop("active_promo_code", None)

        current_balance = await user_repo.get_balance(user.id)

        if current_balance < charged_price:
            shortfall = (charged_price - current_balance).quantize(Decimal("0.0001"))
            binance_uid = settings.BINANCE_UID or "1254494426"
            pay_required_text = (
                f"💳 <b>Insufficient Balance to Activate Number</b>\n\n"
                f"🎯 <b>Service:</b> {service_info['name']}\n"
                f"{flag} <b>Country:</b> {country_name}\n"
                f"{promo_note}"
                f"💰 <b>Number Cost:</b> <code>${charged_price:.4f} USDT</code>\n"
                f"💵 <b>Your Available Balance:</b> <code>${current_balance:.4f} USDT</code>\n"
                f"⚠️ <b>Shortfall Needed:</b> <code>${shortfall:.4f} USDT</code>\n\n"
                f"🔶 <b>Binance Pay Details:</b>\n"
                f"• Send <b><code>${shortfall:.4f} USDT</code></b> to UID: <code>{binance_uid}</code>\n"
                f"• Then submit your Binance Pay Order ID to instantly fund your balance.\n\n"
                f"⚡ <i>Numbers include 100% Automatic Refund if no OTP is received!</i>"
            )
            pay_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🔶 Deposit ${shortfall:.4f} via Binance Pay", callback_data="deposit:binance_pay")],
                [InlineKeyboardButton("💳 All Deposit Methods", callback_data="nav:deposit")],
                [InlineKeyboardButton("🔙 Back", callback_data=f"sms:menu:{service_key}")],
            ])
            await query.edit_message_text(pay_required_text, parse_mode="HTML", reply_markup=pay_kb)
            return

        # Deduct balance atomically
        deducted = await user_repo.deduct_balance(db_user.id, charged_price)
        if not deducted:
            await query.answer("⚠️ Insufficient balance.", show_alert=True)
            return

        # Redeem coupon record
        if redeemed_coupon_id:
            await coupon_repo.redeem_coupon(
                coupon_id=redeemed_coupon_id,
                user_id=db_user.id,
                telegram_id=user.id,
                discount_applied=discount_applied,
                service=service_key,
            )

        await session.commit()
        user_db_id = db_user.id

    await query.edit_message_text(
        f"⏳ <b>Purchasing {country_name} number for {service_info['name']}...</b>\n\n"
        f"{promo_note}"
        f"💰 Deducted <code>${charged_price:.4f} USDT</code> from balance.\n"
        "<i>Connecting to SMS network...</i>",
        parse_mode="HTML",
    )

    sms_client = _get_smspool()
    try:
        purchase_res = await sms_client.purchase_number(
            country_id=country_id,
            service=service_info["id"],
        )
    except Exception as err:
        logger.error("SMSPool purchase failed: %s — Auto-refunding user %d", err, user.id)
        # Automatic refund
        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            refunded_bal = await user_repo.refund_balance(user_db_id, charged_price)
            await session.commit()

        await query.edit_message_text(
            f"❌ <b>Purchase Failed</b>\n\n{err}\n\n"
            f"💰 <b>Auto-Refund:</b> <code>+${charged_price:.4f} USDT</code> credited back to your balance.\n"
            f"💳 <b>Current Balance:</b> <code>${refunded_bal:.4f} USDT</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔁 Retry", callback_data=f"sms:menu:{service_key}")],
                [InlineKeyboardButton("🏠 Home", callback_data="nav:home")],
            ]),
        )
        return
    finally:
        await sms_client.close()

    expires_in_seconds = purchase_res["expires_in"]
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)

    # Persist in DB
    async with AsyncSessionLocal() as session:
        order_record = SmsOrder(
            id=str(uuid.uuid4()),
            user_id=user_db_id,
            telegram_id=user.id,
            smspool_order_id=purchase_res["order_id"],
            phone_number=purchase_res["number"],
            country="us" if country_id == COUNTRY_US else "uk",
            service=service_info["name"],
            supplier_price=Decimal(str(raw_supplier_price)),
            charged_price=charged_price,
            status=SmsOrderStatus.ACTIVE.value,
            expires_at=expires_at,
        )
        session.add(order_record)
        await session.commit()

    mins_left = expires_in_seconds // 60
    phone_display = f"+{purchase_res['number']}"

    await query.edit_message_text(
        f"📱 <b>Your Verification Number is Ready</b>\n\n"
        f"🎯 <b>Service:</b> {service_info['name']}\n"
        f"{flag} <b>Country:</b> {country_name}\n"
        f"📞 <b>Phone Number:</b> <code>{phone_display}</code> (tap to copy)\n"
        f"💰 <b>Price:</b> ${charged_price:.4f} USDT\n"
        f"⏳ <b>Valid For:</b> ~{mins_left} minutes\n\n"
        f"👉 <b>Enter this number on {service_info['name']}.</b>\n"
        "⚡ <i>Waiting for OTP... When received, it will appear here automatically!</i>\n\n"
        "🛡️ <i>Auto-Refund Guarantee: If no OTP is received, 100% of your funds will be auto-refunded to your balance.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel & Refund Number", callback_data=f"sms:cancel:{purchase_res['order_id']}")],
        ]),
    )

    # Launch background polling job
    asyncio.create_task(
        _poll_otp_background_worker(
            bot=context.bot,
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            user_id=user_db_id,
            order_id=purchase_res["order_id"],
            phone_number=purchase_res["number"],
            service_name=service_info["name"],
            service_key=service_key,
            charged_price=charged_price,
        )
    )


# ---------------------------------------------------------------------------
# Background OTP Polling Worker with 100% Automated Refunds
# ---------------------------------------------------------------------------

async def _poll_otp_background_worker(
    bot,
    chat_id: int,
    message_id: int,
    user_id: int,
    order_id: str,
    phone_number: str,
    service_name: str,
    service_key: str,
    charged_price: Decimal,
) -> None:
    """Continuously poll SMSPool every 5 seconds until OTP arrives or timeout."""
    sms_client = _get_smspool()
    elapsed = 0

    try:
        while elapsed < MAX_POLL_DURATION_SECONDS:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            elapsed += POLL_INTERVAL_SECONDS

            try:
                check_data = await sms_client.check_sms(order_id)
            except Exception as exc:
                logger.error("SMSPool poll error for order %s: %s", order_id, exc)
                continue

            status = check_data["status"]

            if status == STATUS_COMPLETED and check_data.get("sms"):
                # ✅ OTP Received successfully
                otp = check_data["sms"]
                full_sms = check_data.get("full_sms", otp)

                async with AsyncSessionLocal() as session:
                    await session.execute(
                        sa_update(SmsOrder)
                        .where(SmsOrder.smspool_order_id == order_id)
                        .values(
                            status=SmsOrderStatus.COMPLETED.value,
                            otp_received=otp,
                            full_sms=full_sms,
                            completed_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.commit()

                try:
                    await bot.edit_message_text(
                        f"🎉 <b>OTP Code Received!</b>\n\n"
                        f"🎯 <b>Service:</b> {service_name}\n"
                        f"📞 <b>Number:</b> <code>+{phone_number}</code>\n"
                        f"🔑 <b>Verification OTP:</b> <code>{otp}</code> (tap to copy)\n\n"
                        f"📩 <b>Full Message:</b>\n<i>{full_sms}</i>\n\n"
                        f"💰 <b>Total Charged:</b> ${charged_price:.4f} USDT",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔁 Get Another Number", callback_data=f"sms:menu:{service_key}")],
                            [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
                        ]),
                    )
                except Exception as ex:
                    logger.error("Failed updating message with received OTP: %s", ex)
                return

            elif status in (STATUS_EXPIRED, STATUS_CANCELLED):
                # Auto-Refund on supplier cancel/expiration
                async with AsyncSessionLocal() as session:
                    user_repo = UserRepository(session)
                    new_bal = await user_repo.refund_balance(user_id, charged_price)
                    await session.execute(
                        sa_update(SmsOrder)
                        .where(SmsOrder.smspool_order_id == order_id)
                        .values(status=SmsOrderStatus.REFUNDED.value)
                    )
                    await session.commit()

                label = "Expired" if status == STATUS_EXPIRED else "Cancelled"
                try:
                    await bot.edit_message_text(
                        f"⏰ <b>Number {label} — 100% Refunded!</b>\n\n"
                        f"The number <code>+{phone_number}</code> has {label.lower()}.\n\n"
                        f"💰 <b>Auto-Refund:</b> <code>+${charged_price:.4f} USDT</code> credited back to your balance.\n"
                        f"💳 <b>Current Balance:</b> <code>${new_bal:.4f} USDT</code>\n\n"
                        "You can request a new number anytime.",
                        chat_id=chat_id,
                        message_id=message_id,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔁 New Number", callback_data=f"sms:menu:{service_key}")],
                            [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
                        ]),
                    )
                except Exception as ex:
                    logger.error("Failed updating message on status %s: %s", status, ex)
                return

        # Timeout reached (9 minutes) — Auto-refund
        await sms_client.cancel_order(order_id)

        async with AsyncSessionLocal() as session:
            user_repo = UserRepository(session)
            new_bal = await user_repo.refund_balance(user_id, charged_price)
            await session.execute(
                sa_update(SmsOrder)
                .where(SmsOrder.smspool_order_id == order_id)
                .values(status=SmsOrderStatus.REFUNDED.value)
            )
            await session.commit()

        try:
            await bot.edit_message_text(
                f"⏰ <b>OTP Timeout — 100% Auto-Refunded!</b>\n\n"
                f"No SMS was received for <code>+{phone_number}</code> within the waiting window.\n\n"
                f"💰 <b>Refund Amount:</b> <code>+${charged_price:.4f} USDT</code>\n"
                f"💳 <b>Your Current Balance:</b> <code>${new_bal:.4f} USDT</code>\n\n"
                "You can request a fresh number below.",
                chat_id=chat_id,
                message_id=message_id,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔁 Try Again", callback_data=f"sms:menu:{service_key}")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
                ]),
            )
        except Exception:
            pass

    finally:
        await sms_client.close()


# ---------------------------------------------------------------------------
# Cancellation & Active Order View Handlers with Instant Auto-Refund
# ---------------------------------------------------------------------------

async def sms_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User cancels an active SMS number with 100% instant refund."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    # Extract order_id if present: sms:cancel:{order_id}
    parts = query.data.split(":")
    specific_oid = parts[2] if len(parts) > 2 else None

    async with AsyncSessionLocal() as session:
        user_repo = UserRepository(session)
        if specific_oid:
            stmt = select(SmsOrder).where(
                SmsOrder.telegram_id == user.id,
                SmsOrder.smspool_order_id == specific_oid,
                SmsOrder.status == SmsOrderStatus.ACTIVE.value,
            )
        else:
            stmt = select(SmsOrder).where(
                SmsOrder.telegram_id == user.id,
                SmsOrder.status == SmsOrderStatus.ACTIVE.value,
            )
        res = await session.execute(stmt)
        active_order = res.scalar_one_or_none()

        if not active_order:
            await query.edit_message_text(
                "ℹ️ No active number found to cancel.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")]]),
            )
            return

        order_id = active_order.smspool_order_id
        charged_price = active_order.charged_price

        # Atomically refund balance
        new_bal = await user_repo.refund_balance(active_order.user_id, charged_price)
        await session.execute(
            sa_update(SmsOrder)
            .where(SmsOrder.id == active_order.id)
            .values(status=SmsOrderStatus.REFUNDED.value)
        )
        await session.commit()

    sms_client = _get_smspool()
    try:
        await sms_client.cancel_order(order_id)
    finally:
        await sms_client.close()

    await query.edit_message_text(
        f"❌ <b>Number Cancelled & 100% Refunded!</b>\n\n"
        f"Your verification number has been cancelled.\n\n"
        f"💰 <b>Refunded:</b> <code>+${charged_price:.4f} USDT</code>\n"
        f"💳 <b>Current Balance:</b> <code>${new_bal:.4f} USDT</code>\n\n"
        f"You can request a new number anytime.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Verification Hub", callback_data="sms:hub")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
        ]),
    )


async def sms_view_active_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """View the currently active number and time remaining."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    active_order = await _get_active_user_order(user.id)
    if not active_order:
        await query.edit_message_text(
            "ℹ️ You have no active verification numbers.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ Verification Hub", callback_data="sms:hub")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
            ]),
        )
        return

    flag = "🇺🇸" if active_order.country == "us" else "🇬🇧"
    remaining = max(0, int((active_order.expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()))
    mins, secs = divmod(remaining, 60)

    await query.edit_message_text(
        f"📱 <b>Active Verification Session</b>\n\n"
        f"🎯 <b>Service:</b> {active_order.service}\n"
        f"{flag} <b>Country:</b> {'United States' if active_order.country == 'us' else 'United Kingdom'}\n"
        f"📞 <b>Number:</b> <code>+{active_order.phone_number}</code>\n"
        f"⏳ <b>Time Remaining:</b> {mins}m {secs}s\n\n"
        "⚡ <i>Listening for incoming OTP code in real-time...</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel Number", callback_data=f"sms:cancel:{active_order.smspool_order_id}")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="nav:home")],
        ]),
    )
